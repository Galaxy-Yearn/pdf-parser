from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import json
import re

from PIL import Image

from cancel import check_cancel
from manifest import load_manifest, write_manifest
from model_gateway import ModelGateway


@dataclass
class OcrPageResult:
    page: int
    md_path: str
    json_path: str


@dataclass
class OcrBatchResult:
    doc_id: str
    output_dir: str
    pages: List[OcrPageResult]


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: List[List[List[str]]] = []
        self._in_table = False
        self._current_table: List[List[str]] = []
        self._current_row: List[str] = []
        self._current_cell: List[str] = []
        self._in_cell = False

    def handle_starttag(self, tag: str, attrs: List[tuple[str, Optional[str]]]) -> None:
        if tag.lower() == "table":
            self._in_table = True
            self._current_table = []
        elif self._in_table and tag.lower() == "tr":
            self._current_row = []
        elif self._in_table and tag.lower() in ("td", "th"):
            self._in_cell = True
            self._current_cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "table" and self._in_table:
            if self._current_table:
                self.tables.append(self._current_table)
            self._in_table = False
            self._current_table = []
        elif self._in_table and tag.lower() == "tr":
            if self._current_row:
                self._current_table.append(self._current_row)
            self._current_row = []
        elif self._in_table and tag.lower() in ("td", "th"):
            cell_text = "".join(self._current_cell).strip()
            cell_text = re.sub(r"\s+", " ", cell_text)
            self._current_row.append(cell_text)
            self._current_cell = []
            self._in_cell = False

    def handle_data(self, data: str) -> None:
        if self._in_table and self._in_cell:
            self._current_cell.append(data)


def _escape_cell(text: str) -> str:
    return text.replace("|", "\\|")


def _table_to_markdown(rows: List[List[str]]) -> str:
    if not rows:
        return ""
    col_count = max(len(row) for row in rows)
    if col_count == 0:
        return ""
    normalized = []
    for row in rows:
        padded = row + [""] * (col_count - len(row))
        normalized.append([_escape_cell(cell) for cell in padded])

    header = normalized[0]
    lines = ["| " + " | ".join(header) + " |"]
    lines.append("| " + " | ".join(["---"] * col_count) + " |")
    for row in normalized[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def normalize_markdown(raw: str) -> str:
    text = raw.replace("\r\n", "\n")
    table_pattern = re.compile(r"<table.*?>.*?</table>", re.IGNORECASE | re.DOTALL)

    def _replace(match: re.Match) -> str:
        fragment = match.group(0)
        parser = _TableParser()
        parser.feed(fragment)
        if not parser.tables:
            return fragment
        tables_md = []
        for rows in parser.tables:
            md = _table_to_markdown(rows)
            if md:
                tables_md.append(md)
        return "\n\n".join(tables_md) if tables_md else fragment

    text = table_pattern.sub(_replace, text)
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(lines).strip() + "\n"


def _extract_figure_blocks(raw: Dict[str, Any]) -> List[Dict[str, object]]:
    layout = raw.get("layout_details")
    if not isinstance(layout, list) or not layout:
        return []
    page_blocks = layout[0] if isinstance(layout[0], list) else layout
    if not isinstance(page_blocks, list):
        return []

    figures: List[Dict[str, object]] = []
    for block in page_blocks:
        if not isinstance(block, dict):
            continue
        label = str(block.get("label") or "").lower()
        native = str(block.get("native_label") or "").lower()
        if "image" not in label and "image" not in native and "figure" not in native:
            continue
        bbox = block.get("bbox_2d")
        if not (isinstance(bbox, list) and len(bbox) == 4):
            continue
        figures.append(
            {
                "label": block.get("label"),
                "native_label": block.get("native_label"),
                "bbox_2d": bbox,
            }
        )
    return figures


def _clamp(value: int, lower: int, upper: int) -> int:
    return max(lower, min(upper, value))


def _extract_figures(
    png_path: Path,
    raw: Dict[str, Any],
    figures_dir: Path,
    page_num: int,
    refresh: bool,
) -> List[Dict[str, object]]:
    figures_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"page_{page_num:04d}_fig_"
    if refresh:
        for existing in figures_dir.glob(f"{prefix}*.png"):
            try:
                existing.unlink()
            except OSError:
                pass

    blocks = _extract_figure_blocks(raw)
    if not blocks:
        return []

    with Image.open(png_path) as img:
        width, height = img.size
        results: List[Dict[str, object]] = []
        for idx, block in enumerate(blocks, start=1):
            bbox = block.get("bbox_2d") or []
            try:
                x1, y1, x2, y2 = [int(round(float(v))) for v in bbox]  # type: ignore[arg-type]
            except (ValueError, TypeError):
                continue
            x1 = _clamp(x1, 0, width)
            x2 = _clamp(x2, 0, width)
            y1 = _clamp(y1, 0, height)
            y2 = _clamp(y2, 0, height)
            if x2 <= x1 or y2 <= y1:
                continue

            crop = img.crop((x1, y1, x2, y2))
            target = figures_dir / f"{prefix}{idx:04d}.png"
            crop.save(target)

            results.append(
                {
                    "id": f"{prefix}{idx:04d}",
                    "path": str(target.relative_to(figures_dir.parent).as_posix()),
                    "bbox_2d": [x1, y1, x2, y2],
                    "label": block.get("label"),
                    "native_label": block.get("native_label"),
                    "width": x2 - x1,
                    "height": y2 - y1,
                }
            )

    return results


def ocr_document(
    doc_dir: Path,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    skip_existing: bool = True,
    extract_figures: bool = True,
    cancel: Optional[Callable[[], bool]] = None,
    cancel_file: Optional[str] = None,
) -> OcrBatchResult:
    manifest = load_manifest(doc_dir)
    pages = manifest.get("pages")
    if not isinstance(pages, list):
        raise ValueError("manifest pages missing or invalid")

    ocr_dir = doc_dir / "ocr"
    ocr_dir.mkdir(parents=True, exist_ok=True)
    figures_dir = doc_dir / "figures"

    legacy_blocks_dir = ocr_dir / "blocks"
    if legacy_blocks_dir.exists():
        try:
            for item in legacy_blocks_dir.rglob("*"):
                if item.is_file():
                    item.unlink()
            for item in sorted(legacy_blocks_dir.rglob("*"), reverse=True):
                if item.is_dir():
                    item.rmdir()
            legacy_blocks_dir.rmdir()
        except OSError:
            pass

    gateway = ModelGateway()
    results: List[OcrPageResult] = []

    for page in pages:
        check_cancel(cancel, cancel_file)
        if not isinstance(page, dict):
            continue
        page_num = int(page.get("page", 0))
        png_rel = page.get("png_path")
        if not png_rel:
            continue
        png_path = doc_dir / Path(str(png_rel))
        if not png_path.exists():
            continue

        md_path = ocr_dir / f"page_{page_num:04d}.md"
        json_path = ocr_dir / f"page_{page_num:04d}.ocr.json"
        legacy_raw_md = ocr_dir / f"page_{page_num:04d}.raw.md"

        if skip_existing and md_path.exists() and json_path.exists():
            raw = json.loads(json_path.read_text(encoding="utf-8"))
            if extract_figures:
                figures = page.get("figures")
                if not figures or not isinstance(figures, list):
                    figures = _extract_figures(
                        png_path, raw, figures_dir, page_num, refresh=False
                    )
                    page["figures"] = figures
            page.pop("raw_md_path", None)
            page.pop("block_count", None)
            results.append(
                OcrPageResult(
                    page=page_num,
                    md_path=str(md_path.relative_to(doc_dir).as_posix()),
                    json_path=str(json_path.relative_to(doc_dir).as_posix()),
                )
            )
            if legacy_raw_md.exists():
                try:
                    legacy_raw_md.unlink()
                except OSError:
                    pass
            continue

        check_cancel(cancel, cancel_file)
        ocr_result = gateway.ocr_image(str(png_path), provider=provider, model=model)
        md_path.write_text(normalize_markdown(ocr_result.markdown), encoding="utf-8")
        json_path.write_text(
            json.dumps(ocr_result.raw, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        page["md_path"] = str(md_path.relative_to(doc_dir).as_posix())
        page["ocr_json_path"] = str(json_path.relative_to(doc_dir).as_posix())
        if extract_figures:
            figures = _extract_figures(
                png_path, ocr_result.raw, figures_dir, page_num, refresh=True
            )
            page["figures"] = figures
        page.pop("raw_md_path", None)
        page.pop("block_count", None)
        if legacy_raw_md.exists():
            try:
                legacy_raw_md.unlink()
            except OSError:
                pass

        results.append(
            OcrPageResult(
                page=page_num,
                md_path=page["md_path"],
                json_path=page["ocr_json_path"],
            )
        )

    if extract_figures:
        total_figures = 0
        for entry in pages:
            if isinstance(entry, dict):
                fig_list = entry.get("figures")
                if isinstance(fig_list, list):
                    total_figures += len(fig_list)
        manifest["figure_count"] = total_figures
        manifest["figures_dir"] = str(figures_dir.relative_to(doc_dir).as_posix())
    manifest.pop("blocks_dir", None)

    write_manifest(doc_dir, manifest)

    doc_id = str(manifest.get("doc_id", doc_dir.name))
    return OcrBatchResult(doc_id=doc_id, output_dir=str(doc_dir), pages=results)

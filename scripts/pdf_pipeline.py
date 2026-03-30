from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from manifest import load_manifest
from pdf_figure_index import build_figure_index
from pdf_ingest import pdf_ingest
from pdf_ocr import ocr_document
from project_paths import repo_rel_path


@dataclass
class PdfPipelineResult:
    doc_id: str
    output_dir: str
    manifest_path: str
    doc_md_path: str
    figure_index_json: Optional[str]
    figure_index_md: Optional[str]
    page_count: int
    figure_count: int


def _rel_pointer(value: str) -> str:
    s = str(value or "").strip()
    if not s:
        return ""
    try:
        p = Path(s)
    except Exception:  # noqa: BLE001
        return s
    if not p.is_absolute():
        posix = p.as_posix()
        return posix[2:] if posix.startswith("./") else posix
    return repo_rel_path(p)


def _guess_title(md_text: str) -> Optional[str]:
    for line in md_text.lstrip("\ufeff").splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return None


def build_document_overview(doc_dir: Path, overwrite: bool = False) -> Path:
    manifest = load_manifest(doc_dir)
    doc_id = str(manifest.get("doc_id", doc_dir.name))
    source_path = _rel_pointer(str(manifest.get("source_path", "") or ""))
    created_at = manifest.get("created_at", "")
    page_count = manifest.get("page_count", 0)
    figure_count = manifest.get("figure_count", 0)
    figures_dir = manifest.get("figures_dir", "")

    title = None
    page1 = doc_dir / "ocr" / "page_0001.md"
    if page1.exists():
        title = _guess_title(page1.read_text(encoding="utf-8", errors="ignore"))

    doc_path = doc_dir / "doc.md"
    if doc_path.exists() and not overwrite:
        return doc_path

    lines = [
        "# Document",
        f"- doc_id: {doc_id}",
        f"- source_path: {source_path}",
        f"- created_at: {created_at}",
        f"- page_count: {page_count}",
        f"- figure_count: {figure_count}",
        f"- source_copy_path: {manifest.get('source_copy_path', '')}",
        f"- pages_dir: pages/",
        f"- ocr_dir: ocr/",
    ]
    if figures_dir:
        lines.append(f"- figures_dir: {figures_dir}")
    if title:
        lines.append(f"- title: {title}")
    lines.append("")

    if title:
        lines.extend(["## Title", title, ""])

    lines.extend(
        [
            "## Notes",
            "- (empty)",
            "",
        ]
    )

    doc_path.write_text("\n".join(lines), encoding="utf-8")
    return doc_path


def parse_pdf_document(
    pdf_path: Path | str,
    output_root: Optional[Path] = None,
    dpi: int = 150,
    extract_figures: bool = True,
    run_figure_index: bool = True,
    run_vision: bool = True,
    move_dropped: bool = True,
    figure_timeout_s: int = 300,
    review_workers: int = 2,
) -> PdfPipelineResult:
    ingest_result = pdf_ingest(
        pdf_path=pdf_path,
        output_root=output_root,
        dpi=dpi,
    )
    doc_dir = Path(ingest_result.output_dir)

    ocr_document(
        doc_dir=doc_dir,
        skip_existing=True,
        extract_figures=extract_figures,
    )

    figure_index_json: Optional[str] = None
    figure_index_md: Optional[str] = None
    if extract_figures and run_figure_index:
        figure_result = build_figure_index(
            doc_dir=doc_dir,
            run_vision=run_vision,
            move_dropped=move_dropped,
            timeout_s=figure_timeout_s,
            review_workers=review_workers,
        )
        figure_index_json = figure_result["figure_index_json"]
        figure_index_md = figure_result["figure_index_md"]

    doc_md = build_document_overview(doc_dir, overwrite=True)

    manifest_path = doc_dir / "manifest.json"
    manifest = load_manifest(doc_dir)

    return PdfPipelineResult(
        doc_id=str(manifest.get("doc_id", doc_dir.name)),
        output_dir=str(doc_dir),
        manifest_path=str(manifest_path),
        doc_md_path=str(doc_md),
        figure_index_json=figure_index_json,
        figure_index_md=figure_index_md,
        page_count=int(manifest.get("page_count", 0) or 0),
        figure_count=int(manifest.get("figure_count", 0) or 0),
    )

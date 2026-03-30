from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
import json
import os
import re
import shutil
import time

from cancel import check_cancel
from manifest import load_manifest, write_manifest
from model_gateway import ModelGateway
from prompt_templates import render_prompt


@dataclass
class FigureIndexEntry:
    id: str
    page: int
    rel_path: str
    bbox_2d: List[int]
    width: int
    height: int
    label: str
    native_label: str
    figure_tag: Optional[str]
    kind: str
    status: str
    reason: str
    title: Optional[str] = None
    summary: Optional[str] = None


@dataclass
class FigureCaptionGroup:
    figure_tag: str
    caption_path: Optional[str]
    caption_line: Optional[int]
    caption_text: Optional[str]
    entry_ids: List[str]


@dataclass
class VisionDecision:
    kind: str
    keep: Optional[bool]
    title: str
    summary: str


@dataclass
class PreparedFigure:
    fig: Dict[str, Any]
    id: str
    page: int
    rel_path: str
    bbox_2d: List[int]
    width: int
    height: int
    label: str
    native_label: str
    figure_tag: Optional[str]
    kind: str
    status: str
    reason: str
    caption_path: Optional[str]
    caption_line: Optional[int]
    caption_text: Optional[str]
    title: Optional[str] = None
    summary: Optional[str] = None
    vision_error: Optional[str] = None


def _extract_caption_blocks(page_md: Path) -> List[Tuple[int, int, str]]:
    """
    Returns [(line_no, fig_number, caption_text_line)].
    Keep it minimal: in this repo OCR output usually has captions on a single line.
    """
    if not page_md.exists():
        return []

    results: List[Tuple[int, int, str]] = []
    for idx, line in enumerate(
        page_md.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1
    ):
        # Example: "Fig. 1. (A) ... (B) ..."
        match = re.search(r"\bFig\.?\s*(\d+)\.", line)
        if not match:
            continue
        try:
            fig_no = int(match.group(1))
        except ValueError:
            continue
        caption = line.strip()
        if caption:
            results.append((idx, fig_no, caption))
    return results


def _caption_candidates_from_manifest(page: Dict[str, Any]) -> List[Dict[str, Any]]:
    # Use layout-native "figure_title" blocks as caption anchors (bbox); filter out tiny ones.
    candidates: List[Dict[str, Any]] = []
    for fig in page.get("figures") or []:
        if not isinstance(fig, dict):
            continue
        native = str(fig.get("native_label") or "").lower()
        if "figure_title" not in native:
            continue
        bbox = fig.get("bbox_2d")
        if not (isinstance(bbox, list) and len(bbox) == 4):
            continue
        w = int(fig.get("width") or 0)
        h = int(fig.get("height") or 0)
        # Exclude tiny panel labels like isolated "A".
        if w * h < 20000 or min(w, h) < 40:
            continue
        candidates.append(fig)
    candidates.sort(key=lambda f: (f.get("bbox_2d") or [0, 0, 0, 0])[1])
    return candidates


def _map_captions_to_blocks(
    captions: List[Tuple[int, int, str]],
    caption_blocks: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Produce a list of caption dicts with both text + bbox, matched by vertical order.
    """
    mapped: List[Dict[str, Any]] = []
    if not captions or not caption_blocks:
        return mapped
    # Order by line_no should correlate with top->bottom.
    captions_sorted = sorted(captions, key=lambda x: x[0])
    for idx, block in enumerate(caption_blocks):
        if idx >= len(captions_sorted):
            break
        line_no, fig_no, caption_text = captions_sorted[idx]
        mapped.append(
            {
                "fig_no": fig_no,
                "caption_line": line_no,
                "caption_text": caption_text,
                "bbox_2d": block.get("bbox_2d"),
            }
        )
    return mapped


def _assign_figure_tag(
    fig_bbox: List[int],
    mapped_captions: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not mapped_captions:
        return None
    _, _, _, y2 = fig_bbox
    best: Optional[Dict[str, Any]] = None
    best_delta: Optional[int] = None

    for cap in mapped_captions:
        bbox = cap.get("bbox_2d") or []
        if not (isinstance(bbox, list) and len(bbox) == 4):
            continue
        cap_y1 = int(bbox[1])
        delta = cap_y1 - int(y2)
        if delta < 0:
            continue
        if best_delta is None or delta < best_delta:
            best_delta = delta
            best = cap

    if best:
        return best

    # Fallback: nearest caption by absolute vertical distance.
    best_abs: Optional[Dict[str, Any]] = None
    best_abs_delta: Optional[int] = None
    for cap in mapped_captions:
        bbox = cap.get("bbox_2d") or []
        if not (isinstance(bbox, list) and len(bbox) == 4):
            continue
        cap_y = int(bbox[1])
        delta = abs(cap_y - int(y2))
        if best_abs_delta is None or delta < best_abs_delta:
            best_abs_delta = delta
            best_abs = cap
    return best_abs


def _default_kind_and_status(fig: Dict[str, Any]) -> Tuple[str, str, str]:
    native = str(fig.get("native_label") or "")
    label = str(fig.get("label") or "")
    w = int(fig.get("width") or 0)
    h = int(fig.get("height") or 0)
    native_lower = native.lower()
    label_lower = label.lower()

    if "header_image" in native_lower:
        return ("header_image", "dropped", "native_label=header_image")
    if "figure_title" in native_lower or label_lower == "text":
        return ("figure_title", "dropped", "caption/title block")
    if w <= 0 or h <= 0:
        return ("invalid", "dropped", "invalid dimensions")
    if w * h < 2000 or min(w, h) < 25:
        return ("panel_label", "dropped", "too small (likely panel label/icon)")
    if "table" in native_lower:
        return ("table", "kept", "native_label=table")
    if "chart" in native_lower:
        return ("chart_plot", "kept", "native_label=chart")
    if "figure" in native_lower or "image" in native_lower:
        return ("unknown", "kept", f"native_label={native_lower or label_lower or 'image'}")
    return ("unknown", "kept", "default keep")


def _vision_classify(
    gateway: ModelGateway,
    image_path: str,
    prompt_hint: str,
    crop_id: str,
    figure_tag: Optional[str] = None,
    caption_text: Optional[str] = None,
    timeout_s: int = 300,
) -> VisionDecision:
    """
    Review a heuristic-kept crop. The model can still mark it as junk.
    """
    prompt = render_prompt(
        "pdf_figure_classify_prompt.md",
        {
            "PROMPT_HINT": prompt_hint,
            "CROP_ID": crop_id,
            "FIGURE_TAG": figure_tag or "unknown",
            "CAPTION_TEXT": (caption_text or "").strip(),
        },
    ).strip()
    result = gateway.vision(image_path=image_path, prompt=prompt, timeout_s=timeout_s)
    text = (result.content or "").strip()
    if not text:
        return VisionDecision(kind="unknown", keep=None, title="", summary="")

    # Best-effort JSON parse.
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        blob = text[start : end + 1]
        try:
            obj = json.loads(blob)
            return VisionDecision(
                kind=str(obj.get("kind") or "unknown").strip() or "unknown",
                keep=obj.get("keep") if isinstance(obj.get("keep"), bool) else None,
                title=str(obj.get("title") or "").strip(),
                summary=str(obj.get("summary") or "").strip(),
            )
        except json.JSONDecodeError:
            pass

    # Fallback: treat entire text as summary.
    return VisionDecision(kind="unknown", keep=None, title="", summary=text[:400])


def _should_drop_cached_file(kind: str, keep_flag: Optional[bool] = None) -> bool:
    """
    Decide whether to remove the crop PNG from the main figures set.
    We still keep metadata + caption pointers in the figure index.
    """
    if keep_flag is False:
        return True
    drop_kinds = {
        "panel_label",
        "figure_title",
        "header_image",
        "decorative",
    }
    return kind in drop_kinds


def _default_review_workers() -> int:
    cpu = os.cpu_count() or 4
    return max(1, min(2, cpu))


def _is_retryable_review_error(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    markers = (
        "429",
        "too many requests",
        "engine_overloaded",
        "rate limit",
        "temporarily unavailable",
        "timed out",
        "timeout",
        "connection reset",
        "remote end closed",
        "service unavailable",
    )
    return any(marker in text for marker in markers)


def _review_backoff_seconds(figure_id: str, attempt: int) -> float:
    base = min(20.0, 2.0 * float(max(1, attempt)))
    jitter = (sum(ord(ch) for ch in figure_id) % 7) * 0.2
    return base + jitter


def _apply_review_decision(prepared: PreparedFigure, decision: VisionDecision) -> None:
    if decision.kind and decision.kind != "unknown":
        prepared.kind = decision.kind
    prepared.title = decision.title or None
    prepared.summary = decision.summary or None
    if _should_drop_cached_file(prepared.kind, keep_flag=decision.keep):
        prepared.status = "dropped"
        if decision.keep is False:
            prepared.reason = f"model_rejected(kind={prepared.kind})"
        else:
            prepared.reason = f"drop_policy(kind={prepared.kind})"


def _review_prepared_figure(
    doc_dir: Path,
    figure: PreparedFigure,
    timeout_s: int,
    max_attempts: int = 3,
) -> VisionDecision:
    image_path = doc_dir / Path(figure.rel_path)
    gateway = ModelGateway()
    last_exc: Optional[Exception] = None
    for attempt in range(1, max(1, int(max_attempts)) + 1):
        try:
            return _vision_classify(
                gateway,
                str(image_path),
                prompt_hint=f"page={figure.page} crop_id={figure.id}",
                crop_id=figure.id,
                figure_tag=figure.figure_tag,
                caption_text=figure.caption_text,
                timeout_s=timeout_s,
            )
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt >= max_attempts or not _is_retryable_review_error(exc):
                raise
            time.sleep(_review_backoff_seconds(figure.id, attempt))
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("review failed without exception")


def build_figure_index(
    doc_dir: Path,
    run_vision: bool = True,
    move_dropped: bool = True,
    timeout_s: int = 300,
    review_workers: int = 2,
    cancel: Optional[Callable[[], bool]] = None,
    cancel_file: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build a minimal figure<->caption index, and optionally clean up extracted crop assets.

    - Writes: <doc_dir>/figure_index.json and <doc_dir>/figure_index.md
    - Updates: manifest.json (adds status/kind/figure_tag/caption pointers per figure entry)
    - Cleanup: moves dropped crops to <doc_dir>/figures/junk/ (if enabled)
    """
    manifest = load_manifest(doc_dir)
    pages = manifest.get("pages") or []
    if not isinstance(pages, list):
        raise ValueError("manifest pages missing or invalid")

    entries: List[FigureIndexEntry] = []
    prepared_figures: List[PreparedFigure] = []
    review_queue: List[PreparedFigure] = []
    caption_groups: Dict[str, FigureCaptionGroup] = {}
    dropped = 0
    kept = 0
    review_candidates = 0
    model_reviewed = 0
    review_failed = 0
    retry_queue: List[PreparedFigure] = []

    for page in pages:
        check_cancel(cancel, cancel_file)
        if not isinstance(page, dict):
            continue
        page_num = int(page.get("page", 0) or 0)
        fig_list = page.get("figures") or []
        if not isinstance(fig_list, list) or not fig_list:
            continue

        page_md = doc_dir / "ocr" / f"page_{page_num:04d}.md"
        captions = _extract_caption_blocks(page_md)
        caption_blocks = _caption_candidates_from_manifest(page)
        mapped_caps = _map_captions_to_blocks(captions, caption_blocks)

        for fig in fig_list:
            check_cancel(cancel, cancel_file)
            if not isinstance(fig, dict):
                continue
            fig_id = str(fig.get("id") or "")
            rel_path = str(fig.get("path") or "")
            bbox = fig.get("bbox_2d") or []
            if not fig_id or not rel_path or not (isinstance(bbox, list) and len(bbox) == 4):
                continue

            kind, status, reason = _default_kind_and_status(fig)
            # Compute figure tag based on caption mapping (only for non-title images).
            fig_tag: Optional[str] = None
            cap_path: Optional[str] = None
            cap_line: Optional[int] = None
            cap_text: Optional[str] = None
            cap = _assign_figure_tag([int(v) for v in bbox], mapped_caps)
            if cap and kind not in {"figure_title", "panel_label", "header_image"}:
                fig_no = int(cap.get("fig_no") or 0)
                if fig_no > 0:
                    fig_tag = f"Fig. {fig_no}"
                cap_path = str(page_md.relative_to(doc_dir).as_posix()) if page_md.exists() else None
                cap_line = int(cap.get("caption_line") or 0) or None
                cap_text = str(cap.get("caption_text") or "").strip() or None
                group = caption_groups.get(fig_tag)
                if not group:
                    caption_groups[fig_tag] = FigureCaptionGroup(
                        figure_tag=fig_tag,
                        caption_path=cap_path,
                        caption_line=cap_line,
                        caption_text=cap_text,
                        entry_ids=[],
                    )

            prepared = PreparedFigure(
                fig=fig,
                id=fig_id,
                page=page_num,
                rel_path=rel_path,
                bbox_2d=[int(v) for v in bbox],
                width=int(fig.get("width") or 0),
                height=int(fig.get("height") or 0),
                label=str(fig.get("label") or ""),
                native_label=str(fig.get("native_label") or ""),
                figure_tag=fig_tag,
                kind=kind,
                status=status,
                reason=reason,
                caption_path=cap_path,
                caption_line=cap_line,
                caption_text=cap_text,
            )
            prepared_figures.append(prepared)

            abs_img = doc_dir / Path(rel_path)
            if run_vision and status == "kept" and abs_img.exists():
                review_queue.append(prepared)

    if run_vision and review_queue:
        review_candidates = len(review_queue)
        workers = max(1, int(review_workers or _default_review_workers()))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_map = {
                executor.submit(_review_prepared_figure, doc_dir, prepared, timeout_s): prepared
                for prepared in review_queue
            }
            for future in as_completed(future_map):
                check_cancel(cancel, cancel_file)
                prepared = future_map[future]
                try:
                    decision = future.result()
                    model_reviewed += 1
                    _apply_review_decision(prepared, decision)
                except Exception as exc:  # noqa: BLE001
                    if _is_retryable_review_error(exc):
                        retry_queue.append(prepared)
                        continue
                    review_failed += 1
                    prepared.vision_error = f"{type(exc).__name__}: {exc}"[:240]
                    prepared.reason = f"{prepared.reason}; vision_review_failed"

    for prepared in retry_queue:
        check_cancel(cancel, cancel_file)
        try:
            decision = _review_prepared_figure(
                doc_dir,
                prepared,
                timeout_s=timeout_s,
                max_attempts=2,
            )
            model_reviewed += 1
            _apply_review_decision(prepared, decision)
        except Exception as exc:  # noqa: BLE001
            review_failed += 1
            prepared.vision_error = f"{type(exc).__name__}: {exc}"[:240]
            prepared.reason = f"{prepared.reason}; vision_review_failed"

    for prepared in prepared_figures:
        fig = prepared.fig
        rel_path = prepared.rel_path
        abs_img = doc_dir / Path(rel_path)

        if move_dropped and prepared.status == "dropped" and abs_img.exists():
            junk_dir = doc_dir / "figures" / "junk"
            junk_dir.mkdir(parents=True, exist_ok=True)
            target = junk_dir / abs_img.name
            if target.resolve() != abs_img.resolve():
                try:
                    shutil.move(str(abs_img), str(target))
                    rel_path = str(target.relative_to(doc_dir).as_posix())
                    prepared.rel_path = rel_path
                except OSError:
                    pass

        fig["kind"] = prepared.kind
        fig["status"] = prepared.status
        fig["path"] = prepared.rel_path
        fig.pop("caption_path", None)
        fig.pop("caption_line", None)
        fig.pop("caption_text", None)
        fig.pop("vision_summary", None)
        if prepared.figure_tag:
            fig["figure_tag"] = prepared.figure_tag
        else:
            fig.pop("figure_tag", None)
        if prepared.vision_error:
            fig["vision_error"] = prepared.vision_error
        else:
            fig.pop("vision_error", None)
        if prepared.title:
            fig["title"] = prepared.title
        else:
            fig.pop("title", None)
        if prepared.summary:
            fig["summary"] = prepared.summary
        else:
            fig.pop("summary", None)

        if prepared.figure_tag and prepared.figure_tag in caption_groups:
            caption_groups[prepared.figure_tag].entry_ids.append(prepared.id)

        entry = FigureIndexEntry(
            id=prepared.id,
            page=prepared.page,
            rel_path=prepared.rel_path,
            bbox_2d=prepared.bbox_2d,
            width=prepared.width,
            height=prepared.height,
            label=prepared.label,
            native_label=prepared.native_label,
            figure_tag=prepared.figure_tag,
            kind=prepared.kind,
            status=prepared.status,
            reason=prepared.reason,
            title=prepared.title,
            summary=prepared.summary,
        )
        entries.append(entry)
        if prepared.status == "dropped":
            dropped += 1
        else:
            kept += 1

    # Write index files.
    out_json = doc_dir / "figure_index.json"
    out_md = doc_dir / "figure_index.md"
    out_json.write_text(
        json.dumps(
            {
                "captions": [g.__dict__ for g in sorted(caption_groups.values(), key=lambda x: x.figure_tag)],
                "entries": [e.__dict__ for e in entries],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # Minimal markdown: group by figure tag.
    by_tag: Dict[str, List[FigureIndexEntry]] = {}
    for e in entries:
        tag = e.figure_tag or "(unassigned)"
        by_tag.setdefault(tag, []).append(e)

    md_lines: List[str] = ["# Figure Index", ""]
    for tag in sorted(by_tag.keys(), key=lambda t: (t == "(unassigned)", t)):
        md_lines.append(f"## {tag}")
        group = caption_groups.get(tag)
        if group and group.caption_path and group.caption_line:
            md_lines.append(f"caption_ptr={group.caption_path}#L{group.caption_line}")
        if group and group.caption_text:
            md_lines.append(f"caption={group.caption_text}")
        for e in by_tag[tag]:
            parts = [
                f"- [{e.id}]",
                f"page={e.page}",
                f"status={e.status}",
                f"kind={e.kind}",
                f"path={e.rel_path}",
            ]
            if e.title:
                parts.append(f"title={e.title}")
            md_lines.append(" ".join(parts))
            if e.summary:
                md_lines.append(f"  summary={e.summary}")
        md_lines.append("")
    out_md.write_text("\n".join(md_lines).strip() + "\n", encoding="utf-8")

    write_manifest(doc_dir, manifest)
    return {
        "doc_id": str(manifest.get("doc_id", doc_dir.name)),
        "doc_dir": str(doc_dir),
        "figure_index_json": str(out_json),
        "figure_index_md": str(out_md),
        "kept": kept,
        "dropped": dropped,
        "review_candidates": review_candidates,
        "model_reviewed": model_reviewed,
        "review_failed": review_failed,
        "total": len(entries),
    }

# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "Pillow>=10.0.0",
# ]
# ///

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Optional, Sequence

from pdf_figure_index import build_figure_index
from pdf_get_page import pdf_get_page
from pdf_ingest import pdf_ingest
from pdf_ocr import ocr_document
from pdf_pipeline import parse_pdf_document
from project_paths import pdf_doc_dir, pdf_output_root, set_workspace_root


def _resolve_doc_dir(doc_id: Optional[str], explicit_dir: Optional[str]) -> Path:
    if explicit_dir:
        return Path(explicit_dir)
    if doc_id:
        return pdf_doc_dir(doc_id)
    raise SystemExit("Provide --doc-id or --dir")


def _add_common_review_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Review timeout in seconds for multimodal figure review",
    )
    parser.add_argument(
        "--review-workers",
        type=int,
        default=2,
        help="Parallel workers for multimodal figure review",
    )


def _add_workspace_root_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--workspace-root",
        default=None,
        help="Workspace root used for .env lookup and default artifacts output",
    )


def _resolve_output_root(value: Optional[str]) -> Path:
    return Path(value) if value else pdf_output_root()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PDF parsing workflow CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    parse_parser = subparsers.add_parser(
        "parse",
        help="Run the full PDF parsing workflow",
        description="Parse a PDF into a self-contained artifact folder",
    )
    _add_workspace_root_arg(parse_parser)
    parse_parser.add_argument("pdf", help="Path to the source PDF file")
    parse_parser.add_argument(
        "--output-root",
        default=None,
        help="Directory where parsed PDF folders will be written",
    )
    parse_parser.add_argument("--dpi", type=int, default=150, help="Render DPI")
    parse_parser.add_argument(
        "--no-figures", action="store_true", help="Skip figure crop extraction"
    )
    parse_parser.add_argument(
        "--no-figure-index",
        action="store_true",
        help="Skip figure-to-caption indexing",
    )
    parse_parser.add_argument(
        "--no-vision",
        action="store_true",
        help="Disable multimodal review for heuristic-kept figure crops",
    )
    parse_parser.add_argument(
        "--no-move-dropped",
        action="store_true",
        help="Keep dropped figure crops in the main figures folder",
    )
    _add_common_review_args(parse_parser)

    ingest_parser = subparsers.add_parser(
        "ingest",
        help="Render PDF pages to PNGs",
        description="PDF ingest: render pages to PNG",
    )
    _add_workspace_root_arg(ingest_parser)
    ingest_parser.add_argument("pdf", help="Path to PDF file")
    ingest_parser.add_argument(
        "--output-root",
        default=None,
        help="Directory where ingested PDF folders will be written",
    )
    ingest_parser.add_argument("--dpi", type=int, default=150, help="Render DPI")

    ocr_parser = subparsers.add_parser(
        "ocr",
        help="Run OCR on an ingested document",
        description="OCR pages from a parsed PDF document",
    )
    _add_workspace_root_arg(ocr_parser)
    ocr_parser.add_argument("--doc-id", default=None, help="Document id under artifacts/pdf")
    ocr_parser.add_argument("--dir", default=None, help="Document directory path")
    ocr_parser.add_argument("--provider", default=None, help="OCR provider override")
    ocr_parser.add_argument("--model", default=None, help="OCR model override")
    ocr_parser.add_argument("--no-skip", action="store_true", help="Do not skip existing pages")
    ocr_parser.add_argument(
        "--no-figures", action="store_true", help="Do not extract figure crops"
    )

    figure_parser = subparsers.add_parser(
        "figure-index",
        help="Build the figure to caption index",
        description="Build figure<->caption index for a parsed PDF doc",
    )
    _add_workspace_root_arg(figure_parser)
    figure_parser.add_argument("doc_id", help="Document id under artifacts/pdf/")
    figure_parser.add_argument(
        "--no-vision",
        action="store_true",
        help="Skip multimodal review (keep heuristics + caption mapping only)",
    )
    figure_parser.add_argument(
        "--no-move-dropped",
        action="store_true",
        help="Do not move dropped crops into figures/junk/",
    )
    _add_common_review_args(figure_parser)

    page_parser = subparsers.add_parser(
        "get-page",
        help="Get page asset paths from a parsed document",
        description="Get PDF page assets",
    )
    _add_workspace_root_arg(page_parser)
    page_parser.add_argument("doc_id", help="Document id under artifacts/pdf")
    page_parser.add_argument("page", type=int, help="Page number")

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    workspace = set_workspace_root(getattr(args, "workspace_root", None))
    os.environ["PDF_PARSER_WORKSPACE_ROOT"] = str(workspace)

    if args.command == "parse":
        result = parse_pdf_document(
            pdf_path=args.pdf,
            output_root=_resolve_output_root(args.output_root),
            dpi=args.dpi,
            extract_figures=not args.no_figures,
            run_figure_index=not args.no_figure_index,
            run_vision=not args.no_vision,
            move_dropped=not args.no_move_dropped,
            figure_timeout_s=args.timeout,
            review_workers=args.review_workers,
        )
        print(f"doc_id={result.doc_id}")
        print(f"output_dir={result.output_dir}")
        print(f"manifest_path={result.manifest_path}")
        print(f"doc_md_path={result.doc_md_path}")
        print(f"figure_index_json={result.figure_index_json}")
        print(f"figure_index_md={result.figure_index_md}")
        print(f"pages={result.page_count}")
        print(f"figures={result.figure_count}")
        return 0

    if args.command == "ingest":
        result = pdf_ingest(
            args.pdf,
            output_root=_resolve_output_root(args.output_root),
            dpi=args.dpi,
        )
        print(f"doc_id={result.doc_id}")
        print(f"output_dir={result.output_dir}")
        print(f"manifest_path={result.manifest_path}")
        print(f"pages={len(result.pages)}")
        return 0

    if args.command == "ocr":
        doc_dir = _resolve_doc_dir(args.doc_id, args.dir)
        result = ocr_document(
            doc_dir=doc_dir,
            provider=args.provider,
            model=args.model,
            skip_existing=not args.no_skip,
            extract_figures=not args.no_figures,
        )
        print(f"doc_id={result.doc_id}")
        print(f"output_dir={result.output_dir}")
        print(f"pages={len(result.pages)}")
        return 0

    if args.command == "figure-index":
        doc_dir = pdf_doc_dir(args.doc_id)
        if not doc_dir.exists():
            raise SystemExit(f"doc_dir not found: {doc_dir}")
        result = build_figure_index(
            doc_dir=doc_dir,
            run_vision=not args.no_vision,
            move_dropped=not args.no_move_dropped,
            timeout_s=args.timeout,
            review_workers=args.review_workers,
        )
        print(
            "doc_id={doc_id} total={total} kept={kept} dropped={dropped} review_candidates={candidates} "
            "model_reviewed={reviewed} review_failed={failed} figure_index_md={md}".format(
                doc_id=result["doc_id"],
                total=result["total"],
                kept=result["kept"],
                dropped=result["dropped"],
                candidates=result["review_candidates"],
                reviewed=result["model_reviewed"],
                failed=result["review_failed"],
                md=result["figure_index_md"],
            )
        )
        return 0

    if args.command == "get-page":
        ref = pdf_get_page(args.doc_id, args.page)
        print(f"page={ref.page}")
        print(f"page_png_path={ref.page_png_path}")
        print(f"page_md_path={ref.page_md_path}")
        print(f"page_ocr_json_path={ref.page_ocr_json_path}")
        return 0

    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())

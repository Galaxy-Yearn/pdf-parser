from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from manifest import load_manifest
from project_paths import pdf_doc_dir, repo_rel_path


@dataclass
class PdfPageRef:
    page: int
    page_png_path: str
    page_md_path: Optional[str]
    page_ocr_json_path: Optional[str]


def pdf_get_page(doc_id: str, page: int) -> PdfPageRef:
    doc_dir = pdf_doc_dir(doc_id)
    manifest = load_manifest(doc_dir)
    pages = manifest.get("pages", [])
    for entry in pages:
        if int(entry.get("page", 0)) == page:
            png_rel = entry.get("png_path")
            md_rel = entry.get("md_path")
            json_rel = entry.get("ocr_json_path")
            page_png = repo_rel_path(doc_dir / png_rel) if png_rel else ""
            page_md = repo_rel_path(doc_dir / md_rel) if md_rel else None
            page_json = repo_rel_path(doc_dir / json_rel) if json_rel else None
            return PdfPageRef(
                page=page,
                page_png_path=page_png,
                page_md_path=page_md,
                page_ocr_json_path=page_json,
            )

    raise ValueError(f"Page {page} not found in manifest for doc {doc_id}")

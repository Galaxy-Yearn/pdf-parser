from __future__ import annotations

from pathlib import Path
from typing import Any, Dict
import json


def manifest_path(doc_dir: Path | str) -> Path:
    return Path(doc_dir) / "manifest.json"


def load_manifest(doc_dir: Path | str) -> Dict[str, Any]:
    path = manifest_path(doc_dir)
    if not path.exists():
        raise FileNotFoundError(f"manifest.json not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def write_manifest(doc_dir: Path | str, manifest: Dict[str, Any]) -> Path:
    path = manifest_path(doc_dir)
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path

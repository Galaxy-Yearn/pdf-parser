from __future__ import annotations

from pathlib import Path
import os


WORKSPACE_ROOT_ENV = "PDF_PARSER_WORKSPACE_ROOT"


def skill_root() -> Path:
    return Path(__file__).resolve().parents[1]


def set_workspace_root(root: Path | str | None = None) -> Path:
    base = Path(root).expanduser() if root else Path.cwd()
    resolved = base.resolve()
    os.environ[WORKSPACE_ROOT_ENV] = str(resolved)
    return resolved


def workspace_root(root: Path | str | None = None) -> Path:
    if root:
        return Path(root).expanduser().resolve()
    env_root = os.getenv(WORKSPACE_ROOT_ENV, "").strip()
    if env_root:
        return Path(env_root).expanduser().resolve()
    return Path.cwd().resolve()


def repo_root(root: Path | str | None = None) -> Path:
    # Keep the legacy helper name for copied modules. In the skill bundle it
    # deliberately points at the caller workspace instead of the skill folder.
    return workspace_root(root)


def artifacts_root(root: Path | str | None = None) -> Path:
    return workspace_root(root) / "artifacts"


def pdf_output_root(root: Path | str | None = None) -> Path:
    return artifacts_root(root) / "pdf"


def pdf_doc_dir(doc_id: str, root: Path | str | None = None) -> Path:
    return pdf_output_root(root) / str(doc_id)


def prompts_root(root: Path | str | None = None) -> Path:
    base = Path(root).expanduser() if root else (skill_root() / "assets")
    if base.name.lower() == "prompts":
        return base.resolve()
    return (base / "prompts").resolve()


def repo_rel_path(path: Path | str, root: Path | str | None = None) -> str:
    target = Path(path)
    base = workspace_root(root)
    try:
        return target.resolve().relative_to(base.resolve()).as_posix()
    except Exception:  # noqa: BLE001
        return str(target)

from __future__ import annotations

"""Prompt template loader for repo-local prompt assets."""

from pathlib import Path
from typing import Any, Dict, Optional, Tuple
import re

from project_paths import prompts_root


_CACHE: Dict[str, Tuple[float, str]] = {}


def read_prompt(name: str, *, root: Optional[Path] = None, cache: bool = True) -> str:
    """
    Read a prompt template from prompts/<name>.

    We intentionally fail loudly if the prompt asset is missing to avoid silently
    falling back to hard-coded instructions.
    """
    prompt_dir = prompts_root(root)
    key = f"{str(prompt_dir.resolve()).lower()}::{name}"
    path = prompt_dir / str(name)
    if not path.exists():
        raise FileNotFoundError(f"Missing prompt template: {path}")

    if cache and key in _CACHE:
        try:
            mtime = float(path.stat().st_mtime)
        except OSError:
            mtime = -1.0
        cached_mtime, cached_text = _CACHE[key]
        if mtime > 0 and cached_mtime == mtime:
            return cached_text

    text = path.read_text(encoding="utf-8", errors="ignore").lstrip("\ufeff")
    if cache:
        try:
            mtime = float(path.stat().st_mtime)
        except OSError:
            mtime = -1.0
        _CACHE[key] = (mtime, text)
    return text


_IF_BLOCK_RE = re.compile(r"<<IF:(?P<key>[A-Z0-9_]+)>>(?P<body>.*?)<<ENDIF:(?P=key)>>", re.S)


def render_prompt(name: str, variables: Dict[str, Any], *, root: Optional[Path] = None) -> str:
    """
    Render a prompt template with variables.
    """
    vars_norm: Dict[str, Any] = {}
    for k, v in (variables or {}).items():
        kk = str(k or "").strip()
        if not kk:
            continue
        vars_norm[kk] = v

    text = read_prompt(name, root=root, cache=True)

    # 1) Apply conditional blocks.
    def _block_repl(m: re.Match[str]) -> str:
        key = str(m.group("key") or "").strip()
        val = vars_norm.get(key)
        if val:
            return str(m.group("body") or "")
        return ""

    text = _IF_BLOCK_RE.sub(_block_repl, text)

    # 2) Replace placeholders.
    for k, v in vars_norm.items():
        text = text.replace(f"<<{k}>>", "" if v is None else str(v))

    # 3) Clean up any unreplaced placeholders (best-effort).
    # We keep them as-is so issues are visible in artifacts/logs.
    return text.strip() + "\n"

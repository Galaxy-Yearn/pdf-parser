---
name: pdf-parser
description: Parse a single PDF into a self-contained artifact folder with page PNGs, OCR Markdown and JSON, extracted figure crops, a figure-to-caption index, and a `doc.md` summary. Use when coding agent needs to parse or inspect a PDF, regenerate OCR or figure artifacts, crop figures from scientific papers, fetch page-level assets, or run this workflow inside the current workspace without writing outputs into the installed skill folder.
---

# PDF Parser Skill

## Quick Start

- Resolve the absolute path of this skill directory first.
- Run the bundled CLI with `uv`; do not require the user to pre-create a Python environment for this skill.
- Ensure `uv` is installed on the machine before using this skill.
- Under the bundled default `.env`, provide two real API keys in the caller workspace: `KIMI_API_KEY` for chat/vision and `GLM_API_KEY` for OCR.
- Run the bundled CLI from the coding agent's workspace, not from the skill folder.
- Let the CLI default to the workspace-local artifact root unless the user asks for another output location.

```powershell
$skill_dir = "<absolute-path-to>/pdf-parser"
uv run "$skill_dir/scripts/pdf_parser.py" parse .\paper.pdf
```

If the current shell is not already at the target workspace root, pass `--workspace-root` explicitly:

```powershell
uv run "$skill_dir/scripts/pdf_parser.py" parse F:\project\paper.pdf --workspace-root F:\project
```

## Workflow

1. Run `parse` for the full pipeline.
2. Run `ingest` when only page PNGs are needed.
3. Run `ocr` on an existing `doc_id` or document directory when page OCR must be regenerated.
4. Run `figure-index` after OCR when figure crops already exist and only the index must be rebuilt.
5. Run `get-page` to locate the page PNG, OCR Markdown, and OCR JSON for a single page.

Common commands:

```powershell
uv run "$skill_dir/scripts/pdf_parser.py" parse .\paper.pdf
uv run "$skill_dir/scripts/pdf_parser.py" parse .\paper.pdf --no-vision
uv run "$skill_dir/scripts/pdf_parser.py" parse .\paper.pdf --timeout 300 --review-workers 2
uv run "$skill_dir/scripts/pdf_parser.py" parse .\paper.pdf --no-figures --no-figure-index
uv run "$skill_dir/scripts/pdf_parser.py" ingest .\paper.pdf
uv run "$skill_dir/scripts/pdf_parser.py" ocr --doc-id <doc_id>
uv run "$skill_dir/scripts/pdf_parser.py" figure-index <doc_id>
uv run "$skill_dir/scripts/pdf_parser.py" get-page <doc_id> 3
```

## Output Rules

- Write the generated PDF result folder to the coding agent's workspace, never to the installed skill directory.
- Keep generated artifacts inside the coding agent's workspace.
- Expect the default output root to be `<workspace>/artifacts/pdf/`.
- Expect the default `.env` lookup path to be `<workspace>/.env`.
- Avoid storing runtime outputs inside the installed skill directory.
- Use `--output-root` only when the user explicitly wants a different artifact location.

## Routing And Dependencies

- Ensure `pdftoppm` from Poppler is available on `PATH`.
- Keep all model routing on the workspace `.env` `DEFAULT_*` variables.
- Do not hard-code temporary models, providers, base URLs, or API keys.
- If the workspace does not have `.env`, copy [assets/default.env.example](assets/default.env.example) into the workspace as `.env`, then fill real credentials before running OCR or multimodal review.
- With the bundled default route settings, the minimum credential set is exactly two API keys: `KIMI_API_KEY` and `GLM_API_KEY`.

## Bundled Resources

- Use [scripts/pdf_parser.py](scripts/pdf_parser.py) as the stable CLI entry point.
- Use the peer Python modules in [scripts](scripts) as the flat runtime implementation.
- Use [assets/prompts](assets/prompts) as the bundled prompt source for vision defaults and figure classification.
- Use [assets/default.env.example](assets/default.env.example) as the workspace `.env` template when a new workspace needs the expected route keys.

## Notes

- `scripts/pdf_parser.py` declares its runtime dependencies with inline `uv` script metadata, so `uv run` will install what it needs automatically.
- Expect `--no-vision` to disable only multimodal figure review; heuristic crop filtering and caption mapping still run.
- Expect `parse` to generate `doc.md` and, unless disabled, `figure_index.json` and `figure_index.md`.
- Expect the default artifact layout under the caller workspace to be `artifacts/pdf/<doc_id>/` with `manifest.json`, `doc.md`, optional figure index files, plus `pages/`, `ocr/`, and `figures/`.
- Prefer running from the workspace shell so relative PDF paths and default output paths remain intuitive.

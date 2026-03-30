# pdf-parser

`pdf-parser` 是一个给 Codex 使用的 PDF 解析 skill，也可以作为一个独立发布的 skill 仓库维护。它会把单个 PDF 解析成一套自包含产物，包含页级 PNG、OCR Markdown 与 JSON、插图裁剪、图注索引，以及汇总用的 `doc.md`。

这个仓库本身就对应 skill `pdf-parser`。如果要安装到 Codex 技能目录，目标目录名也建议使用 `pdf-parser`。

## 仓库内容

- `SKILL.md`：skill 主说明，供 Codex 触发和执行时读取
- `agents/openai.yaml`：skill 的界面元数据
- `scripts/`：运行时脚本
- `assets/`：提示词和 `.env` 模板
- `README.md`：面向 GitHub 访客的人类说明

## 功能

- 把 PDF 渲染为页级 PNG
- 对每页执行 OCR，输出 Markdown 和原始 JSON
- 抽取论文中的插图并建立 figure index
- 生成 `doc.md` 文档摘要

默认产物目录位于调用工作区下的 `artifacts/pdf/<doc_id>/`。

## 依赖

- `uv`
- Python 3.11+
- Poppler 的 `pdftoppm`，并且已在 `PATH` 中
- 调用工作区中的 `.env`

## 安装

如果你准备把它作为 Codex skill 使用，推荐安装到：

```text
~/.codex/skills/pdf-parser
```

可以直接把仓库克隆到技能目录：

```powershell
git clone https://github.com/Galaxy-Yearn/pdf-parser.git $HOME/.codex/skills/pdf-parser
```

如果仓库先克隆到了别处，也可以把整个目录复制到技能目录，并命名为 `pdf-parser`。

## 使用

从调用工作区运行，不要在已安装的 skill 目录里写产物：

```powershell
$skill_dir = "<absolute-path-to>/pdf-parser"
uv run "$skill_dir/scripts/pdf_parser.py" parse .\paper.pdf
```

如果当前 shell 不在目标工作区根目录，显式传入 `--workspace-root`：

```powershell
uv run "$skill_dir/scripts/pdf_parser.py" parse F:\project\paper.pdf --workspace-root F:\project
```

常用命令：

```powershell
uv run "$skill_dir/scripts/pdf_parser.py" parse .\paper.pdf
uv run "$skill_dir/scripts/pdf_parser.py" parse .\paper.pdf --no-vision
uv run "$skill_dir/scripts/pdf_parser.py" ingest .\paper.pdf
uv run "$skill_dir/scripts/pdf_parser.py" ocr --doc-id <doc_id>
uv run "$skill_dir/scripts/pdf_parser.py" figure-index <doc_id>
uv run "$skill_dir/scripts/pdf_parser.py" get-page <doc_id> 3
```

## 产物结构

默认输出位于：

```text
artifacts/
  pdf/
    <doc_id>/
      <source>.pdf
      manifest.json
      doc.md
      figure_index.json
      figure_index.md
      pages/
      ocr/
      figures/
```

## `.env` 安全

- 不要把真实 `.env` 提交到 GitHub。
- 不要把任何真实 API key 写进 `SKILL.md`、脚本或示例命令。
- 真实密钥只放在调用工作区的 `.env` 中，不放在 skill 仓库目录里。
- 新工作区需要模板时，复制 `assets/default.env.example` 到工作区根目录并重命名为 `.env`，再手动填入真实密钥。

默认路由通过工作区 `.env` 中的 `DEFAULT_*` 变量读取，不需要在代码里硬编码模型、提供商或密钥。

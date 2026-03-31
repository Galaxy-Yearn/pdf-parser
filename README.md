# pdf-parser

`pdf-parser` 是一个面向 Codex 的 PDF 解析 skill，也可以单独作为一个 GitHub 仓库分发。它的目标不是只抽取纯文本，而是把单个 PDF 处理成一套可继续编排、检索、审阅和分析的结构化产物。

给定一个 PDF，默认会在调用工作区生成 `artifacts/pdf/<doc_id>/`，其中包含：

- 原始 PDF 副本
- 每页 PNG 图片
- 每页 OCR Markdown
- 每页 OCR 原始 JSON
- 插图裁剪结果
- figure 到 caption 的索引
- 汇总文档 `doc.md`

## 适用场景

- 把论文、报告或扫描件转换成可编程处理的产物目录
- 为后续摘要、问答、检索或标注准备稳定输入
- 单独抽取页图、OCR 结果或插图
- 对已有文档重新执行 OCR 或重建 figure index

## 默认推荐模型

仓库默认走工作区 `.env` 中的 `DEFAULT_*` 路由，不在代码里写死临时模型或密钥。推荐的默认配置就是仓库自带模板中的这一组：

```env
DEFAULT_CHAT_PROVIDER=kimi
DEFAULT_CHAT_MODEL=kimi-k2.5
DEFAULT_OCR_PROVIDER=glm
DEFAULT_OCR_MODEL=glm-ocr
```

对应的凭据最少需要两项：

- `KIMI_API_KEY`：用于聊天与多模态复核
- `GLM_API_KEY`：用于 OCR

默认模板见 [assets/default.env.example](assets/default.env.example)。

## 工作原理

主流程 `parse` 会按下面的阶段执行：

1. `pdf_ingest`
   把 PDF 渲染成页级 PNG，并把源 PDF 复制到结果目录。
2. `pdf_ocr`
   对每页图片执行 OCR，输出可读的 Markdown 和机器可复用的原始 JSON。
3. `pdf_figure_index`
   从页面中裁切候选插图，先做启发式过滤，再对保留候选图执行多模态复核，并尝试建立 figure 与 caption 的对应关系。
4. `build_document_overview`
   汇总全文结果，生成 `doc.md`，方便后续由人或代理快速浏览。

默认情况下，`--no-vision` 只会关闭候选图的多模态复核，不会关闭启发式筛选、caption 映射或 figure index 输出。

## 产物说明

默认输出目录结构如下：

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

各产物的作用：

- `<source>.pdf`
  保留输入副本，便于结果目录自包含。
- `manifest.json`
  记录文档元信息、页数、路径和各阶段状态，适合脚本消费。
- `doc.md`
  面向人和代理的总览文档，用于快速理解整份 PDF。
- `figure_index.json`
  结构化的 figure 索引，便于下游代码读取。
- `figure_index.md`
  面向人工检查的 figure 索引视图。
- `pages/`
  每页渲染后的 PNG，适合作为 OCR、多模态分析和人工核对的基础输入。
- `ocr/`
  每页 OCR 结果，其中 Markdown 适合阅读，JSON 适合程序继续处理。
- `figures/`
  从页面中裁出的插图候选与保留结果，供图表分析、复核和索引构建使用。

## 依赖

- `uv`
- Python 3.11+
- Poppler 的 `pdftoppm`，并且已在 `PATH` 中
- 调用工作区中的 `.env`

`scripts/pdf_parser.py` 带有 `uv` 脚本元数据，因此直接用 `uv run` 即可自动解析 Python 依赖。

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
uv run "$skill_dir/scripts/pdf_parser.py" parse .\paper.pdf --timeout 300 --review-workers 2
uv run "$skill_dir/scripts/pdf_parser.py" parse .\paper.pdf --no-figures --no-figure-index
uv run "$skill_dir/scripts/pdf_parser.py" ingest .\paper.pdf
uv run "$skill_dir/scripts/pdf_parser.py" ocr --doc-id <doc_id>
uv run "$skill_dir/scripts/pdf_parser.py" figure-index <doc_id>
uv run "$skill_dir/scripts/pdf_parser.py" get-page <doc_id> 3
```

## `.env` 与密钥安全

- 不要把真实 `.env` 提交到 GitHub。
- 不要把任何真实 API key 写进脚本、示例命令或文档。
- 真实密钥只放在调用工作区的 `.env` 中，不放在 skill 仓库目录里。
- 新工作区需要模板时，复制 [assets/default.env.example](assets/default.env.example) 到工作区根目录并重命名为 `.env`，再手动填入真实密钥。

默认路由通过工作区 `.env` 中的 `DEFAULT_*` 变量读取，不需要在代码里硬编码模型、提供商或密钥。

默认 `.env` 路由下，聊天和多模态复核使用 `kimi-k2.5`，OCR 使用 `glm-ocr`。

<div align="center">

# 🦴 Lazybones — 文献智能抽取工具
# 🦴 Lazybones — Intelligent Literature Extraction Tool

**一个面向科研人员的桌面工具，通过自定义 Schema（YAML 提示词），从任意学术领域的文献中批量抽取结构化信息，并存入可查询、可导出的本地数据库。​**

**A desktop tool for researchers that batch-extracts structured information from academic literature across any research domain, using customizable YAML-based schemas, and stores results in a queryable local database.​**

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python)
![License](https://img.shields.io/badge/License-MIT-green)
![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey?logo=windows)

</div>

---

## ✨ 功能特性 / Features

### 最新源码更新 / Latest source updates

- 导航调整为“数据库建立”和“AI 资料包”两个一级标签；文献抽取、内容审核、数据管理位于数据库建立下，偏好设置由右上角按钮进入。
- 统一经典、深浅色和极速皮肤的显示层级，优化工具栏、滚动与连续缩放时的重排。
- 新增独立的本地 [AI 资料包](docs/AI资料包.md)：PDF / DOCX 转 Markdown、表格和可选图像附件，提供保守分包、来源页码、OCR 风险报告及 ZIP 导出，不自动上传、不写入文献库。
- 文件变小不代表上下文无限；图文包可能更大，复杂公式、OCR 和阅读顺序仍需核对。旧版 DOC 请先另存为 DOCX。

以上更新已包含在当前源码中；现有 `v1.4.0` Release 便携包未包含这批更新。

The current source includes reorganized navigation, refreshed themes, resize/scroll improvements, and a separate local AI-bundle workspace. It exports PDF/DOCX text, tables, optional visuals, source-aware chunks, and quality reports without uploading documents. Smaller files do not bypass model context limits. These changes are not included in the existing v1.4.0 portable release.

- 📄 **批量导入文献** — 支持 PDF / TXT 格式，一次处理数十篇文献，自动识别并合并 SI 附件
  **Batch literature import** — Supports PDF / TXT formats; automatically detects and merges Supplementary Information files

- 🤖 **AI 智能抽取** — 调用大语言模型（DeepSeek），按自定义 Schema 精准提取结构化数据
  **AI-powered extraction** — Uses LLM (DeepSeek) to extract structured data based on user-defined schemas

- 📝 **Schema 自定义** — 通过 YAML 文件灵活定义抽取字段，一份工具适配任意综述方向
  **Custom schemas** — Define extraction fields via YAML files; one tool adapts to any review topic

- 💾 **本地数据库** — 抽取结果存入 SQLite，支持按版本筛选、查询与导出
  **Local database** — Results stored in SQLite with version filtering, query, and export support

- 📊 **数据导出** — 支持导出为 Excel / CSV，便于后续统计分析
  **Data export** — Export to Excel / CSV for downstream analysis

- 🔍 **人工审核** — 低置信度和 AI 自创标签自动进入待审核队列，保障数据质量
  **Manual review** — Low-confidence and AI-generated labels are automatically flagged for review

- ⚡ **智能缓存** — 对解析文本和抽取结果分层缓存，避免重复调用 API，节省费用
  **Smart caching** — Two-layer cache for parsed text and extraction results, reducing redundant API calls

---

## 🌐 适用场景 / Applicable Scenarios

Lazybones 不绑定任何特定领域，只要能写出对应的 YAML Schema，即可适配：

Lazybones is domain-agnostic. Any field with a well-written YAML schema is supported:

| 领域 / Domain | 示例综述方向 / Example Topics |
|---|---|
| 材料科学 | MOF 衍生碳、电催化剂、储能材料、涂层 |
| 化学 | 有机合成、催化机理、配位化学 |
| 生物医学 | 药物载体、纳米医学、蛋白质结构 |
| 环境工程 | 污染物去除、光催化降解 |
| 物理 | 薄膜制备、半导体器件 |
| 其他 | 任何需要从大量文献中批量提取结构化信息的综述场景 |

---

## 🚀 快速开始 / Quick Start

### 免安装便携版 / Portable Release

从 GitHub Releases 下载 `Lazybones-vX.Y.Z-portable-win64.zip`，解压到普通文件夹后双击 `Lazybones.exe`。不要直接在 ZIP 压缩包内运行。

便携版不要求单独安装 Python。数据库、配置、缓存与引用方案保存在 `%APPDATA%\Lazybones`，因此替换或升级程序目录不会覆盖用户数据。

软件启动后每天至多检查一次 GitHub 正式 Release。发现新版时会先征求确认，再下载完整便携包、校验 SHA-256、备份当前程序文件，并由独立更新器完成替换；失败时自动回滚。也可以在“设置 → 软件更新”中关闭自动检查或手动检查。

维护者发布新版本时，应同步修改 `src/version.py` 中的 `APP_VERSION`，并推送同名标签（如 `v1.4.0`）。GitHub Actions 会自动测试、构建并上传 ZIP 与校验文件。

### 环境要求 / Requirements

- Windows 10 / 11（64 位）
- Python 3.10 或以上 / Python 3.10+
- DeepSeek API Key（在 [platform.deepseek.com](https://platform.deepseek.com) 注册获取）

### 安装依赖 / Install Dependencies

```bash
git clone https://github.com/yuanxinxu190645/Lazybones.git
cd Lazybones
pip install -r requirements.txt
```

### 配置 API Key / Configure API Key

在项目根目录创建 `config/settings.json`（该文件不包含在仓库中，需自行创建）：

Create `config/settings.json` in the project root (excluded from repo, create manually):

```json
{
  "api_key": "your_deepseek_api_key_here",
  "model": "deepseek-chat",
  "base_url": "https://api.deepseek.com"
}
```

### 运行程序 / Run

```bash
python src/main.py
```

---

## 📁 项目结构 / Project Structure

```
Lazybones/
├── src/                    # 核心源码 / Core source code
│   ├── main.py             # 程序入口 / Entry point
│   ├── ui.py               # 图形界面 / GUI
│   ├── extractor.py        # AI 抽取逻辑 / AI extraction logic
│   ├── ai_client.py        # LLM API 客户端 / LLM API client
│   ├── database.py         # 数据库操作 / Database operations
│   ├── exporter.py         # 数据导出 / Data export
│   └── schema_loader.py    # Schema 加载器 / Schema loader
├── prompts/
│   └── v1.0_basic.yaml     # 基础抽取提示词示例 / Example schema
├── requirements.txt        # 依赖清单 / Dependencies
├── build.spec              # PyInstaller 打包配置 / Build config
├── setup.iss               # Inno Setup 安装包配置 / Installer config
└── README.md
```

---

## 📝 Schema 编写 / Writing a Schema

Lazybones 的核心是 YAML Schema 文件，存放在 `prompts/` 文件夹下。每个 Schema 对应一个综述方向，通过下拉框切换。

The core of Lazybones is the YAML schema file, stored in the `prompts/` folder. Each schema targets one review topic and can be switched via dropdown.

一个 Schema 包含四个顶层块：

A schema contains four top-level blocks:

```yaml
meta:           # 版本、主题等元数据 / Version, topic metadata
system_prompt_zh:  # 中文系统提示词 / Chinese system prompt
system_prompt_en:  # 英文系统提示词 / English system prompt
fields:         # 抽取字段列表 / Extraction field definitions
```

字段支持四种类型：`text`、`number`、`list`、`enum_multi`（带置信度和原文证据的枚举多选）。

Fields support four types: `text`, `number`, `list`, and `enum_multi` (multi-label with confidence scores and source evidence).

详细编写规范见仓库内 `Readme.yaml` 文件。

For the full schema specification, see `Readme.yaml` in the repository.

---

## 🛠️ 技术栈 / Tech Stack

| 组件 / Component | 技术 / Technology |
|---|---|
| 界面框架 / GUI | Tkinter |
| AI 模型 / LLM | DeepSeek API |
| 数据库 / Database | SQLite3 |
| 数据导出 / Export | openpyxl / pandas |
| 打包工具 / Packaging | PyInstaller + Inno Setup |

---

## ⚙️ 使用流程 / Workflow

1. 启动程序，在设置中填入 DeepSeek API Key
   Launch the app and enter your DeepSeek API Key in Settings
2. 在 `prompts/` 文件夹中选择或编写对应综述方向的 YAML Schema
   Select or write a YAML schema for your review topic in `prompts/`
3. 导入待处理的文献文件（PDF / TXT），程序自动识别 SI 附件
   Import literature files (PDF / TXT); SI files are auto-detected
4. 点击「开始抽取」，等待 AI 处理完成
   Click "Start Extraction" and wait for AI processing
5. 在审核界面核查低置信度结果，确认无误后导出为 Excel
   Review low-confidence results in the review panel, then export to Excel

---

## 📄 开源协议 / License

本项目基于 [MIT License](LICENSE) 开源，允许自由使用、修改和商业应用，保留署名即可。

This project is licensed under the [MIT License](LICENSE) — free to use, modify, and commercialize with attribution.

---

## 🙋 作者 / Author

**Xinyuan Xu** — 科研工作者 / Researcher

如有问题或建议，欢迎提交 [Issue](https://github.com/yuanxinxu190645/Lazybones/issues)。

For questions or suggestions, feel free to open an [Issue](https://github.com/yuanxinxu190645/Lazybones/issues).

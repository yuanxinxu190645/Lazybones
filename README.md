# Lazybones — 文献智能抽取工具

一个面向科研人员的桌面工具,专注于从 MOF(金属有机框架)衍生碳材料等
领域的文献中,批量抽取合成条件、表征数据、电化学性能等结构化参数,
并存入可检索的本地数据库。
首次运行会在 %APPDATA%\Lazybones\ 自动创建配置与数据目录。
请到「设置」页填写你的 API Key 后再使用。
提示词(Schema)编写
抽取字段由 prompts/ 下的 YAML 文件定义,编写规范见 Readme.yaml。
仓库内附带通用版 v1.0_basic.yaml 作为模板。

> 拖拽 PDF/Word → AI 抽取结构化数据 → 入库 → 可对话查询

## 功能特性

- **抽取**:拖拽 PDF/Word,自动匹配正文(MS)与补充材料(SI),并发调用 AI
- **审核**:查看 AI 抽取结果,收录入库 / 丢弃 / 重跑
- **数据管理**:数据库浏览、筛选、导出 Excel / Markdown、备份与导入
- **AI 对话**:整库综述、单篇深聊、自然语言字段检索

## 技术栈

Python 3.10+ · tkinter + tkinterdnd2 · SQLite · OpenAI 兼容接口
· pymupdf / pdfplumber · python-docx · openpyxl · PyYAML

## 安装与运行

```bash
git clone https://github.com/yuanxinxu190645/Lazybones.git
cd Lazybones
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple
python src/main.py

# F:\MOFCNM\src\schema_loader.py

import yaml
import json
import sys
import os
from pathlib import Path

# ── 强制注入的身份字段(硬编码,所有 schema 版本通用)──────────
# 规则:用户 schema 里已定义同名 key 的跳过,只补用户没有的。
# AI 被要求对所有字段尽量抽取,抽不到填 "N/A"。
# Keywords / Abstract 要求 AI 输出尽量简短版本。

_IDENTITY_FIELDS_ZH = [
    # 基础身份
    {"key": "Title",
     "label_zh": "论文完整题目", "label_en": "Title",
     "type": "text", "required": True,
     "description_zh": "【必填】论文完整题目,原文语言,不翻译",
     "description_en": "Full title of the paper, original language"},
    {"key": "Authors_Full",
     "label_zh": "完整作者列表", "label_en": "Authors (Full)",
     "type": "text", "required": True,
     "description_zh": "【必填】所有作者全名,分号分隔,如 Wang Lei; Li Xin",
     "description_en": "All authors, semicolon-separated"},
    {"key": "First_Author",
     "label_zh": "第一作者", "label_en": "First Author",
     "type": "text", "required": True,
     "description_zh": "【必填】第一作者全名",
     "description_en": "Full name of the first author"},
    {"key": "Corresponding_Author",
     "label_zh": "通讯作者", "label_en": "Corresponding Author",
     "type": "text", "required": False,
     "description_zh": "【选填】通讯作者全名,论文中有标注时填写,否则填 N/A",
     "description_en": "Corresponding author full name, N/A if not stated"},
    # 期刊信息
    {"key": "Journal_Full",
     "label_zh": "期刊全称", "label_en": "Journal (Full)",
     "type": "text", "required": True,
     "description_zh": "【必填】期刊完整名称,如 Advanced Materials",
     "description_en": "Full journal name, e.g. Advanced Materials"},
    {"key": "Journal_Abbr",
     "label_zh": "期刊缩写", "label_en": "Journal (Abbr.)",
     "type": "text", "required": True,
     "description_zh": "【必填】ACS 标准缩写,带点号,如 Adv. Mater.",
     "description_en": "ACS-style abbreviation with dots, e.g. Adv. Mater."},
    {"key": "ISSN",
     "label_zh": "ISSN", "label_en": "ISSN",
     "type": "text", "required": False,
     "description_zh": "【选填】期刊 ISSN 号,格式 XXXX-XXXX,抽不到填 N/A",
     "description_en": "Journal ISSN, format XXXX-XXXX, N/A if not found"},
    {"key": "Publisher",
     "label_zh": "出版商", "label_en": "Publisher",
     "type": "text", "required": False,
     "description_zh": "【选填】出版商名称,如 Wiley、ACS、Elsevier",
     "description_en": "Publisher name, e.g. Wiley, ACS, Elsevier"},
    # 时间与定位
    {"key": "Year",
     "label_zh": "发表年份", "label_en": "Year",
     "type": "text", "required": True,
     "description_zh": "【必填】发表年份,4位数字字符串,如 2023",
     "description_en": "Publication year, 4-digit string"},
    {"key": "Publication_Date",
     "label_zh": "发表日期", "label_en": "Publication Date",
     "type": "text", "required": False,
     "description_zh": "【选填】完整发表日期 YYYY-MM-DD,优先取在线发表日期",
     "description_en": "Full publication date YYYY-MM-DD, online date preferred"},
    {"key": "Volume",
     "label_zh": "卷号", "label_en": "Volume",
     "type": "text", "required": False,
     "description_zh": "【选填】期刊卷号,纯数字,如 35",
     "description_en": "Journal volume number"},
    {"key": "Issue",
     "label_zh": "期号", "label_en": "Issue",
     "type": "text", "required": False,
     "description_zh": "【选填】期刊期号,纯数字,如 12",
     "description_en": "Journal issue number"},
    {"key": "Pages",
     "label_zh": "页码", "label_en": "Pages",
     "type": "text", "required": False,
     "description_zh": "【选填】页码范围,如 1234-1245 或 e202401234",
     "description_en": "Page range, e.g. 1234-1245 or e202401234"},
    {"key": "Article_Number",
     "label_zh": "文章编号", "label_en": "Article Number",
     "type": "text", "required": False,
     "description_zh": "【选填】文章编号(部分期刊用编号代替页码),如 2200001",
     "description_en": "Article number if journal uses it instead of pages"},
    # 标识符
    {"key": "DOI",
     "label_zh": "DOI", "label_en": "DOI",
     "type": "text", "required": True,
     "description_zh": "【必填】DOI,不含 https://doi.org/ 前缀,如 10.1002/adma.202200001",
     "description_en": "DOI without https://doi.org/ prefix"},
    # 文章属性
    {"key": "Article_Type",
     "label_zh": "文章类型", "label_en": "Article Type",
     "type": "text", "required": False,
     "description_zh": "【选填】文章类型,如 Research Article/Review/Communication/Letter",
     "description_en": "Article type: Research Article/Review/Communication/Letter"},
    # 摘要与关键词(要求简短)
    {"key": "Abstract_Short",
     "label_zh": "摘要(简短)", "label_en": "Abstract (Short)",
     "type": "text", "required": False,
     "description_zh": "【选填】论文摘要,控制在 100 字以内的核心内容概括,不要逐句翻译",
     "description_en": "Short abstract, max 80 words, core content only"},
    {"key": "Keywords",
     "label_zh": "关键词", "label_en": "Keywords",
     "type": "text", "required": False,
     "description_zh": "【选填】论文关键词,分号分隔,最多 8 个,原文语言",
     "description_en": "Keywords, semicolon-separated, max 8, original language"},
]

# 快速查找集合,用于冲突检测
_IDENTITY_KEYS = {f["key"] for f in _IDENTITY_FIELDS_ZH}

def _get_resource_base() -> Path:
    """只读资源根目录（prompts）：程序安装目录"""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent
    else:
        return Path(__file__).parent.parent


def _get_writable_base() -> Path:
    """可写数据根目录（config）：AppData\Roaming\Lazybones"""
    if getattr(sys, 'frozen', False):
        appdata = Path(os.environ.get("APPDATA", Path.home()))
        writable = appdata / "Lazybones"
        writable.mkdir(parents=True, exist_ok=True)
        return writable
    else:
        return Path(__file__).parent.parent


def get_prompts_dir() -> Path:
    return _get_resource_base() / "prompts"


def get_config_path() -> Path:
    return _get_writable_base() / "config" / "settings.json"


def load_settings() -> dict:
    path = get_config_path()
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_settings(settings: dict):
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)


def list_prompt_versions() -> list:
    """返回所有可用的提示词版本列表"""
    d = get_prompts_dir()
    if not d.exists():
        return []
    files = sorted(d.glob("*.yaml"))
    return [f.stem for f in files]


def load_schema(version: str = None) -> dict:
    """加载指定版本的Schema，version=None时加载最新版"""
    d = get_prompts_dir()
    if version:
        path = d / f"{version}.yaml"
    else:
        files = sorted(d.glob("*.yaml"))
        if not files:
            raise FileNotFoundError(
                f"提示词文件未找到，请确保 {d} 下至少有一个 .yaml 文件。"
            )
        path = files[-1]
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_prompt(schema: dict, lang: str = "zh") -> str:
    """根据 Schema 动态构建发给 AI 的完整提示词。
    用户字段在前,身份字段强制追加在后(已存在同名 key 的跳过)。
    """
    system_key = "system_prompt_" + lang
    system = schema.get(system_key, schema.get("system_prompt_zh", ""))

    user_fields = schema.get("fields", [])
    desc_key = "description_" + lang
    label_key = "label_" + lang
    options_key = "options_" + lang

    # 已在用户 schema 里定义的 key 集合,用于冲突检测
    user_keys = {f["key"] for f in user_fields}

    # 合并字段列表:用户字段在前,身份字段补缺在后
    identity_to_append = [
        f for f in _IDENTITY_FIELDS_ZH
        if f["key"] not in user_keys
    ]
    all_fields = list(user_fields) + identity_to_append

    field_lines = []
    for field in all_fields:
        key = field["key"]

        # 身份字段直接用硬编码描述(已经是双语)
        if key in _IDENTITY_KEYS and key not in user_keys:
            desc = field.get("description_" + lang,
                             field.get("description_zh", ""))
            field_lines.append("  " + chr(34) + key + chr(34) + ": " + desc)
            continue

        # 用户字段走原有逻辑
        label = field.get(label_key, key)
        desc = field.get(desc_key, field.get("description_zh", ""))
        required = "【必填】" if field.get("required") else "【选填】"
        line = "  " + chr(34) + key + chr(34) + ": " + required + " " + desc

        if field.get("type") == "enum_multi" and options_key in field:
            opts = "、".join(field[options_key])
            line += chr(10) + "    预设选项：[" + opts + "]"
            if field.get("allow_custom"):
                if lang == "zh":
                    line += (chr(10) +
                             "    若预设选项不够用，可自创标签，"
                             "source标注为ai_generated")
                else:
                    line += (chr(10) +
                             "    Custom tags allowed, "
                             "mark source as ai_generated")

        field_lines.append(line)

    fields_str = chr(10).join(field_lines)
    total = len(all_fields)
    identity_count = len(identity_to_append)

    # 空值规范：强制 AI 对所有"无值"情况使用统一的字符串 "N/A"
    # 不允许出现 [], [""], ["N/A"], null, "None" 等任何其他形式
    empty_rule_zh = (
        "【空值规范，极其重要，违反此规则视为输出错误】" + chr(10) +
        "1. 任何字段抽不到、原文未提及、不适用时，统一填字符串 \"N/A\"。" + chr(10) +
        "2. 数组类型（list/array）的字段也一样：抽不到时整个字段填 \"N/A\"，" +
        "   不要填 []、[\"N/A\"]、[{\"value\":\"N/A\",...}] 等任何变体。" + chr(10) +
        "3. 只有真正抽到值时，数组字段才返回 list，例如 [\"value1\", \"value2\"] " +
        "或带 source/confidence 的对象数组。" + chr(10) +
        "4. 禁止使用 null、None、空字符串 \"\"、空数组 [] 来表示无值。"
    )
    empty_rule_en = (
        "[EMPTY-VALUE RULE, CRITICAL - violation is treated as output error]"
        + chr(10) +
        "1. For any field that is not found, not mentioned, or not applicable, "
        "fill the string \"N/A\"." + chr(10) +
        "2. This applies to array/list fields as well: when no value can be "
        "extracted, the entire field must be \"N/A\", NOT [], NOT [\"N/A\"], "
        "NOT [{\"value\":\"N/A\",...}] or any other variant." + chr(10) +
        "3. Only return a list when real values are extracted, e.g. "
        "[\"value1\", \"value2\"] or an array of objects with source/confidence."
        + chr(10) +
        "4. Do NOT use null, None, empty string \"\", or empty array [] "
        "to represent missing values."
    )

    if lang == "zh":
        identity_note = (
            "（其中后 " + str(identity_count) +
            " 个为文献身份字段,所有字段抽不到时填 \"N/A\"）"
        )
        prompt = (
            system + chr(10) + chr(10) +
            empty_rule_zh + chr(10) + chr(10) +
            "请从论文中抽取以下 " + str(total) +
            " 个字段" + identity_note + "，输出为JSON对象：" +
            chr(10) + chr(10) +
            fields_str + chr(10) + chr(10) +
            "重要：只输出JSON，不要有任何其他文字。" + chr(10) +
            "再次提醒：所有抽不到的字段（包括数组类型字段）必须填字符串 \"N/A\"，" +
            "不要使用 []、[\"N/A\"] 等任何变体。"
        )
    else:
        identity_note = (
            "(last " + str(identity_count) +
            " are identity fields; fill \"N/A\" if not found)"
        )
        prompt = (
            system + chr(10) + chr(10) +
            empty_rule_en + chr(10) + chr(10) +
            "Please extract the following " + str(total) +
            " fields " + identity_note +
            " from the paper, output as JSON object:" +
            chr(10) + chr(10) +
            fields_str + chr(10) + chr(10) +
            "Important: Output JSON only, no other text." + chr(10) +
            "Reminder: all missing fields (including list/array fields) "
            "must be the string \"N/A\", not [], not [\"N/A\"], not any variant."
        )

    return prompt

def get_field_labels(schema: dict, lang: str = "zh") -> dict:
    """返回 {key: label} 映射,用于 Excel 列名。
    包含用户字段和强制身份字段两部分。
    """
    label_key = "label_" + lang
    user_fields = schema.get("fields", [])
    user_keys = {f["key"] for f in user_fields}

    result = {
        f["key"]: f.get(label_key, f["key"])
        for f in user_fields
    }

    # 补充身份字段的标签(用户没定义的)
    for f in _IDENTITY_FIELDS_ZH:
        if f["key"] not in user_keys:
            result[f["key"]] = f.get(label_key, f["key"])

    return result

# 对外公开别名,供 ui.py 在审核页渲染身份字段使用
IDENTITY_FIELDS = _IDENTITY_FIELDS_ZH
IDENTITY_KEYS = _IDENTITY_KEYS


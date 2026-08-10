import json
import re
import uuid
from copy import deepcopy
from datetime import datetime
from pathlib import Path


DEFAULT_TEMPLATE = (
    "[[{Authors_Full}. ]][[{Title}. ]][[{Journal_Full}. ]]"
    "[[{Year}]]"
    "[[, {Volume}]][[({Issue})]][[, {Pages}]]"
    "[[. DOI: {DOI}]]"
)

DEFAULT_INLINE_TEMPLATE = "[{numbers}]"
DEFAULT_INLINE_ITEM_TEMPLATE = "{author}, {year}"

DEFAULT_SCHEME = {
    "id": "default_full",
    "name": "完整信息（默认）",
    "number_format": "[{n}]",
    "template": DEFAULT_TEMPLATE,
    "separator": "\n",
    "inline_style": "numeric",
    "inline_template": DEFAULT_INLINE_TEMPLATE,
    "inline_item_template": DEFAULT_INLINE_ITEM_TEMPLATE,
    "inline_separator": ",",
    "compress_ranges": True,
    "created_at": "builtin",
    "updated_at": "builtin",
    "builtin": True,
}

BUILTIN_SCHEMES = [
    DEFAULT_SCHEME,
    {
        "id": "builtin_acs",
        "name": "ACS（通用）",
        "number_format": "[{n}]",
        "template": (
            "[[{Authors_Full}. ]][[{Title}. ]]"
            "[[{Journal_Abbr}. ]][[{Year}]][[, {Volume}]]"
            "[[({Issue})]][[, {Pages}]][[. DOI: {DOI}]]"
        ),
        "separator": "\n",
        "inline_style": "numeric",
        "inline_template": DEFAULT_INLINE_TEMPLATE,
        "inline_item_template": DEFAULT_INLINE_ITEM_TEMPLATE,
        "inline_separator": ",",
        "compress_ranges": True,
        "created_at": "builtin",
        "updated_at": "builtin",
        "builtin": True,
    },
    {
        "id": "builtin_gbt7714",
        "name": "GB/T 7714（期刊）",
        "number_format": "[{n}]",
        "template": (
            "[[{Authors_Full}. ]][[{Title}[J]. ]]"
            "[[{Journal_Full}, ]][[{Year}]][[, {Volume}]]"
            "[[({Issue})]][[: {Pages}]][[. DOI: {DOI}]]"
        ),
        "separator": "\n",
        "inline_style": "numeric",
        "inline_template": DEFAULT_INLINE_TEMPLATE,
        "inline_item_template": DEFAULT_INLINE_ITEM_TEMPLATE,
        "inline_separator": ",",
        "compress_ranges": True,
        "created_at": "builtin",
        "updated_at": "builtin",
        "builtin": True,
    },
    {
        "id": "builtin_apa",
        "name": "APA（通用）",
        "number_format": "",
        "template": (
            "[[{Authors_Full}. ]][[({Year}). ]][[{Title}. ]]"
            "[[{Journal_Full}]][[, {Volume}]][[({Issue})]]"
            "[[, {Pages}]][[. https://doi.org/{DOI}]]"
        ),
        "separator": "\n",
        "inline_style": "author_year",
        "inline_template": "({items})",
        "inline_item_template": DEFAULT_INLINE_ITEM_TEMPLATE,
        "inline_separator": "; ",
        "compress_ranges": False,
        "created_at": "builtin",
        "updated_at": "builtin",
        "builtin": True,
    },
]

AVAILABLE_FIELDS = [
    "Authors_Full",
    "First_Author",
    "Corresponding_Author",
    "Title",
    "Journal_Full",
    "Journal_Abbr",
    "Year",
    "Publication_Date",
    "Volume",
    "Issue",
    "Pages",
    "Article_Number",
    "DOI",
    "ISSN",
    "Publisher",
    "Keywords",
]


def _schemes_path(project_dir: str) -> Path:
    return Path(project_dir) / "citation_schemes.json"


def normalize_citation_scheme(scheme: dict) -> dict:
    """Add new optional fields without invalidating legacy scheme files."""
    normalized = dict(scheme or {})
    style = str(normalized.get("inline_style", "numeric"))
    if style not in ("numeric", "author_year"):
        style = "numeric"
    normalized["inline_style"] = style
    normalized.setdefault(
        "inline_template",
        "({items})" if style == "author_year" else DEFAULT_INLINE_TEMPLATE)
    normalized.setdefault(
        "inline_item_template", DEFAULT_INLINE_ITEM_TEMPLATE)
    normalized.setdefault(
        "inline_separator", "; " if style == "author_year" else ",")
    normalized["compress_ranges"] = bool(
        normalized.get("compress_ranges", style == "numeric"))
    normalized.setdefault("separator", "\n")
    normalized.setdefault("number_format", "[{n}]")
    normalized.setdefault("template", DEFAULT_TEMPLATE)
    return normalized


def _valid_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                item = item.get("value", "")
            text = _valid_value(item)
            if text:
                parts.append(text)
        return "; ".join(parts)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    text = str(value).strip()
    if text.upper() in ("", "N/A", "NONE", "NULL", "NA"):
        return ""
    return text


def _normalize_citation_doi(value) -> str:
    text = _valid_value(value)
    text = re.sub(
        r'^\s*(?:https?://(?:dx\.)?doi\.org/|doi\s*:\s*)',
        '', text, flags=re.IGNORECASE)
    return text.rstrip(".,;:)]}")


def _render_conditionals(template: str, data: dict) -> str:
    pattern = re.compile(r'\[\[(.*?)\]\]', re.DOTALL)

    def replace(match):
        block = match.group(1)
        fields = re.findall(r'\{(\w+)\}', block)
        if fields and all(_valid_value(data.get(field)) for field in fields):
            return block
        return ""

    previous = None
    result = template
    while result != previous:
        previous = result
        result = pattern.sub(replace, result)
    return result


def _clean_punctuation(text: str) -> str:
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\s+([,.;:)\]])', r'\1', text)
    text = re.sub(r'([(\[])\s+', r'\1', text)
    text = re.sub(r'\(\s*\)|\[\s*\]', '', text)
    text = re.sub(r',\s*,+', ', ', text)
    text = re.sub(r'\.\s*\.+', '.', text)
    text = re.sub(r'\s+([。；，])', r'\1', text)
    return text.strip(" ,;\t\r\n")


def format_citation(data: dict, scheme: dict, index: int) -> str:
    data = dict(data)
    data["DOI"] = _normalize_citation_doi(data.get("DOI", ""))
    if not _valid_value(data.get("Authors_Full")):
        data["Authors_Full"] = data.get("Authors", "")
    if not _valid_value(data.get("Journal_Full")):
        data["Journal_Full"] = (
            data.get("Journal", "") or data.get("Journal_Abbr", ""))
    if not _valid_value(data.get("Journal_Abbr")):
        data["Journal_Abbr"] = data.get("Journal_Full", "")
    if not _valid_value(data.get("Pages")):
        data["Pages"] = data.get("Article_Number", "")
    template = scheme.get("template") or DEFAULT_TEMPLATE
    rendered = _render_conditionals(template, data)

    def replace_field(match):
        return _valid_value(data.get(match.group(1), ""))

    rendered = re.sub(r'\{(\w+)\}', replace_field, rendered)
    rendered = _clean_punctuation(rendered)

    number_format = scheme.get("number_format", "[{n}]")
    circled = (
        chr(0x2460 + index - 1)
        if 1 <= index <= 20 else "(" + str(index) + ")"
    )
    number = (
        number_format.replace("{n}", str(index))
        .replace("{circled}", circled)
    )
    if number:
        rendered = number.rstrip() + " " + rendered
    return rendered.strip()


def format_citation_list(papers: list, scheme: dict) -> list:
    return [
        format_citation(paper, scheme, index)
        for index, paper in enumerate(papers, 1)
    ]


def _compress_number_ranges(numbers: list[int]) -> str:
    unique = sorted(set(int(number) for number in numbers))
    if not unique:
        return ""
    groups = []
    start = previous = unique[0]
    for number in unique[1:] + [None]:
        if number is not None and number == previous + 1:
            previous = number
            continue
        groups.append(
            str(start) if start == previous else str(start) + "–" + str(previous))
        if number is not None:
            start = previous = number
    return ",".join(groups)


def _inline_author(record: dict) -> str:
    author = _valid_value(record.get("First_Author"))
    if not author:
        author = _valid_value(
            record.get("Authors_Full") or record.get("Authors"))
        author = re.split(r';|\band\b', author, maxsplit=1,
                          flags=re.IGNORECASE)[0].strip()
    return author or "Unknown"


def format_inline_citation(papers: list, scheme: dict,
                           number_map: dict[str, int] | None = None) -> str:
    """Render an in-text citation from the same persisted scheme."""
    normalized = normalize_citation_scheme(scheme)
    if not papers:
        return ""
    if normalized["inline_style"] == "author_year":
        item_template = normalized["inline_item_template"]
        items = []
        for record in papers:
            values = {
                "author": _inline_author(record),
                "year": _valid_value(record.get("Year")) or "n.d.",
                "title": _valid_value(record.get("Title")),
            }
            item = re.sub(
                r'\{(author|year|title)\}',
                lambda match: values.get(match.group(1), ""),
                item_template)
            items.append(_clean_punctuation(item))
        joined = normalized["inline_separator"].join(items)
        return normalized["inline_template"].replace("{items}", joined)

    number_map = number_map or {}
    numbers = []
    for index, record in enumerate(papers, 1):
        base_id = str(record.get("_citation_base_id", ""))
        numbers.append(int(number_map.get(base_id, index)))
    if normalized["compress_ranges"]:
        rendered_numbers = _compress_number_ranges(numbers)
    else:
        rendered_numbers = normalized["inline_separator"].join(
            str(number) for number in numbers)
    return normalized["inline_template"].replace(
        "{numbers}", rendered_numbers).replace(
        "{n}", rendered_numbers)


def select_citation_record(
        records: list,
        language_mode: str = "字段最完整（双语互补）") -> dict:
    """从同一基础文献的双语记录中选择或互补引用字段。"""
    choices = [dict(record) for record in records]
    if not choices:
        return {}

    def completeness(record):
        return sum(
            1 for key, value in record.items()
            if not key.startswith("_") and _valid_value(value))

    if language_mode == "中文记录优先":
        choices.sort(key=lambda record: (
            0 if record.get("_lang") == "zh" else 1,
            -completeness(record)))
    elif language_mode == "英文记录优先":
        choices.sort(key=lambda record: (
            0 if record.get("_lang") == "en" else 1,
            -completeness(record)))
    else:
        choices.sort(key=lambda record: -completeness(record))

    selected = dict(choices[0])
    if language_mode == "字段最完整（双语互补）":
        for alternative in choices[1:]:
            for key, value in alternative.items():
                if (not key.startswith("_")
                        and not _valid_value(selected.get(key))
                        and _valid_value(value)):
                    selected[key] = value
    return selected


def load_citation_schemes(project_dir: str) -> dict:
    path = _schemes_path(project_dir)
    payload = {
        "version": 2,
        "default_scheme_id": DEFAULT_SCHEME["id"],
        "schemes": [
            normalize_citation_scheme(scheme)
            for scheme in deepcopy(BUILTIN_SCHEMES)],
        "warnings": [],
    }
    if not path.exists():
        return payload
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            return payload
        builtin_ids = {builtin["id"] for builtin in BUILTIN_SCHEMES}
        known_names = {
            builtin["name"].strip().casefold()
            for builtin in BUILTIN_SCHEMES
        }
        for scheme in loaded.get("schemes", []):
            if (not isinstance(scheme, dict)
                    or scheme.get("id") in builtin_ids):
                continue
            normalized_name = str(
                scheme.get("name", "")).strip().casefold()
            if not normalized_name or normalized_name in known_names:
                payload["warnings"].append(
                    "已跳过重名引用方案：" +
                    str(scheme.get("name", "未命名")))
                continue
            known_names.add(normalized_name)
            payload["schemes"].append(normalize_citation_scheme(scheme))
        default_id = loaded.get("default_scheme_id")
        if any(scheme["id"] == default_id
               for scheme in payload["schemes"]):
            payload["default_scheme_id"] = default_id
    except Exception as error:
        corrupt_path = path.with_name(
            path.stem + ".corrupt_" +
            datetime.now().strftime("%Y%m%d_%H%M%S") + path.suffix)
        try:
            path.replace(corrupt_path)
            payload["warnings"].append(
                "引用方案文件损坏，已保留副本：" + str(corrupt_path))
        except Exception:
            payload["warnings"].append(
                "引用方案文件损坏：" + str(error))
    return payload


def save_citation_schemes(project_dir: str, payload: dict):
    project = Path(project_dir)
    project.mkdir(parents=True, exist_ok=True)
    path = _schemes_path(project_dir)
    clean_payload = {
        "version": 2,
        "default_scheme_id": payload.get(
            "default_scheme_id", DEFAULT_SCHEME["id"]),
        "schemes": [
            normalize_citation_scheme(scheme)
            for scheme in payload.get("schemes", [])
            if not scheme.get("builtin")
        ],
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(clean_payload, ensure_ascii=False, indent=2),
        encoding="utf-8")
    tmp.replace(path)


def upsert_citation_scheme(project_dir: str, payload: dict,
                           name: str, number_format: str,
                           template: str, separator: str = "\n",
                           scheme_id: str = None,
                           inline_style: str = "numeric",
                           inline_template: str = DEFAULT_INLINE_TEMPLATE,
                           inline_item_template: str = DEFAULT_INLINE_ITEM_TEMPLATE,
                           inline_separator: str = ",",
                           compress_ranges: bool = True) -> dict:
    now = datetime.now().isoformat()
    schemes = payload.setdefault("schemes", [])
    normalized_name = (name.strip() or "未命名方案").casefold()
    duplicate_name = next(
        (
            scheme for scheme in schemes
            if str(scheme.get("name", "")).strip().casefold()
            == normalized_name
            and scheme.get("id") != scheme_id
        ),
        None)
    if duplicate_name:
        raise ValueError("引用方案名称已存在：" + name.strip())
    existing = next(
        (scheme for scheme in schemes
         if scheme.get("id") == scheme_id and not scheme.get("builtin")),
        None)
    if existing is None:
        existing = {
            "id": scheme_id or uuid.uuid4().hex,
            "created_at": now,
            "builtin": False,
        }
        schemes.append(existing)
    existing.update({
        "name": name.strip() or "未命名方案",
        "number_format": number_format,
        "template": template,
        "separator": separator,
        "inline_style": inline_style,
        "inline_template": inline_template,
        "inline_item_template": inline_item_template,
        "inline_separator": inline_separator,
        "compress_ranges": bool(compress_ranges),
        "updated_at": now,
    })
    save_citation_schemes(project_dir, payload)
    return existing


def delete_citation_scheme(project_dir: str, payload: dict,
                           scheme_id: str) -> bool:
    before = len(payload.get("schemes", []))
    payload["schemes"] = [
        scheme for scheme in payload.get("schemes", [])
        if scheme.get("id") != scheme_id or scheme.get("builtin")
    ]
    changed = len(payload["schemes"]) != before
    if changed:
        if payload.get("default_scheme_id") == scheme_id:
            payload["default_scheme_id"] = DEFAULT_SCHEME["id"]
        save_citation_schemes(project_dir, payload)
    return changed

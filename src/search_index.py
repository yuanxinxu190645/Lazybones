import hashlib
import json
import re
import sqlite3
import unicodedata


def flatten_search_values(value):
    if isinstance(value, dict):
        for nested in value.values():
            yield from flatten_search_values(nested)
    elif isinstance(value, (list, tuple, set)):
        for nested in value:
            yield from flatten_search_values(nested)
    elif value is not None:
        yield str(value)


def normalize_search_text(value) -> str:
    text = " ".join(flatten_search_values(value))
    text = unicodedata.normalize("NFKC", text).casefold()
    text = re.sub(
        r'https?://(?:dx\.)?doi\.org/|doi\s*:\s*',
        ' ', text, flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', text).strip()


def build_paper_search_document(paper: dict) -> dict:
    def select_keys(*fragments):
        selected = {}
        for key, value in paper.items():
            lowered = key.casefold()
            if any(fragment in lowered for fragment in fragments):
                selected[key] = value
        return selected

    metadata_keys = {
        "_paper_id", "_schema_ver", "_lang", "_model_used", "_added_at",
    }
    return {
        "全部字段": normalize_search_text(paper),
        "文献ID": normalize_search_text(paper.get("_paper_id", "")),
        "DOI": normalize_search_text(select_keys("doi")),
        "标题": normalize_search_text(select_keys("title", "题名", "标题")),
        "作者": normalize_search_text(
            select_keys("author", "作者", "通讯", "correspond")),
        "期刊": normalize_search_text(
            select_keys("journal", "期刊", "publisher")),
        "年份": normalize_search_text(
            select_keys("year", "date", "年份", "日期")),
        "关键词": normalize_search_text(
            select_keys("keyword", "关键词")),
        "抽取内容": normalize_search_text({
            key: value for key, value in paper.items()
            if key not in metadata_keys
        }),
        "语言": normalize_search_text(paper.get("_lang", "")),
        "版本": normalize_search_text(paper.get("_schema_ver", "")),
        "模型": normalize_search_text(paper.get("_model_used", "")),
    }


def build_cached_search_documents(db_path: str, papers: list) -> dict:
    """复用 SQLite 中的规范化检索文档，仅重建发生变化的记录。"""
    connection = sqlite3.connect(db_path, timeout=30)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS paper_search_cache (
            paper_id       TEXT PRIMARY KEY,
            source_hash    TEXT NOT NULL,
            documents_json TEXT NOT NULL
        )
    """)
    cached = {
        row[0]: (row[1], row[2])
        for row in connection.execute(
            "SELECT paper_id, source_hash, documents_json "
            "FROM paper_search_cache").fetchall()
    }
    result = {}
    active_ids = set()
    for paper in papers:
        paper_id = str(paper.get("_paper_id", ""))
        if not paper_id:
            continue
        active_ids.add(paper_id)
        source_payload = {
            key: value for key, value in paper.items()
            if key != "_edit_log"
        }
        source_hash = hashlib.sha256(
            json.dumps(
                source_payload, ensure_ascii=False,
                sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        cached_item = cached.get(paper_id)
        if cached_item and cached_item[0] == source_hash:
            try:
                result[paper_id] = json.loads(cached_item[1])
                continue
            except Exception:
                pass
        documents = build_paper_search_document(paper)
        result[paper_id] = documents
        connection.execute(
            "INSERT INTO paper_search_cache "
            "(paper_id, source_hash, documents_json) VALUES (?, ?, ?) "
            "ON CONFLICT(paper_id) DO UPDATE SET "
            "source_hash=excluded.source_hash, "
            "documents_json=excluded.documents_json",
            (
                paper_id, source_hash,
                json.dumps(documents, ensure_ascii=False),
            ))
    stale_ids = set(cached) - active_ids
    if stale_ids:
        connection.executemany(
            "DELETE FROM paper_search_cache WHERE paper_id=?",
            [(paper_id,) for paper_id in stale_ids])
    connection.commit()
    connection.close()
    return result

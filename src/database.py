# F:\MOFCNM\src\database.py

import sqlite3
import json
import hashlib
import re
import shutil
import tempfile
import zipfile
import unicodedata
from pathlib import Path
from datetime import datetime

DATABASE_SCHEMA_VERSION = 2


def get_db_path(project_dir: str, db_name: str = "main.db") -> str:
    p = Path(project_dir) / "database"
    p.mkdir(parents=True, exist_ok=True)
    return str(p / db_name)


def init_db(db_path: str):
    """初始化数据库表结构"""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS papers (
            paper_id    TEXT PRIMARY KEY,
            added_at    TEXT,
            schema_ver  TEXT,
            model_used  TEXT,
            lang        TEXT,
            data_json   TEXT,
            edit_log    TEXT DEFAULT '[]'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rejected (
            paper_id      TEXT,
            rejected_at   TEXT,
            reason        TEXT,
            data_json     TEXT
        )
    """)
    # 只做增量补列，不改变既有 papers 列的含义，保证旧数据库原地继承。
    existing_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(papers)").fetchall()
    }
    additive_columns = {
        "added_at": "TEXT",
        "schema_ver": "TEXT",
        "model_used": "TEXT",
        "lang": "TEXT",
        "data_json": "TEXT DEFAULT '{}'",
        "edit_log": "TEXT DEFAULT '[]'",
    }
    for column_name, declaration in additive_columns.items():
        if column_name not in existing_columns:
            conn.execute(
                "ALTER TABLE papers ADD COLUMN " +
                column_name + " " + declaration)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version      INTEGER PRIMARY KEY,
            applied_at   TEXT,
            description  TEXT
        )
    """)
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations "
        "(version, applied_at, description) VALUES (?, ?, ?)",
        (1, datetime.now().isoformat(), "建立 papers/rejected 基础表"))
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations "
        "(version, applied_at, description) VALUES (?, ?, ?)",
        (2, datetime.now().isoformat(), "补齐兼容列并启用版本化迁移"))
    conn.execute("PRAGMA user_version=" + str(DATABASE_SCHEMA_VERSION))
    conn.commit()
    conn.close()


def paper_exists(db_path: str, paper_id: str) -> bool:
    conn = sqlite3.connect(db_path)
    cur = conn.execute("SELECT 1 FROM papers WHERE paper_id=?", (paper_id,))
    exists = cur.fetchone() is not None
    conn.close()
    return exists


def insert_paper(db_path: str, paper_id: str, data: dict,
                 schema_ver: str, model_used: str, lang: str):
    """插入一条文献记录"""
    conn = sqlite3.connect(db_path)
    conn.execute("""
        INSERT OR REPLACE INTO papers
        (paper_id, added_at, schema_ver, model_used, lang, data_json, edit_log)
        VALUES (?, ?, ?, ?, ?, ?, '[]')
    """, (
        paper_id,
        datetime.now().isoformat(),
        schema_ver,
        model_used,
        lang,
        json.dumps(data, ensure_ascii=False)
    ))
    conn.commit()
    conn.close()


def update_paper_field(db_path: str, paper_id: str,
                       field_key: str, new_value, old_value):
    """更新单个字段，记录编辑日志"""
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT data_json, edit_log FROM papers WHERE paper_id=?",
        (paper_id,)
    ).fetchone()

    if not row:
        conn.close()
        return

    data = json.loads(row[0])
    edit_log = json.loads(row[1])

    data[field_key] = new_value
    edit_log.append({
        "field": field_key,
        "old_value": old_value,
        "new_value": new_value,
        "edited_at": datetime.now().isoformat(),
        "edited_by": "human"
    })

    conn.execute(
        "UPDATE papers SET data_json=?, edit_log=? WHERE paper_id=?",
        (json.dumps(data, ensure_ascii=False),
         json.dumps(edit_log, ensure_ascii=False),
         paper_id)
    )
    conn.commit()
    conn.close()


def delete_paper(db_path: str, paper_id: str):
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM papers WHERE paper_id=?", (paper_id,))
    conn.commit()
    conn.close()


def _rename_identity_index_record(project_dir: str, old_id: str,
                                  new_id: str):
    index_db = Path(project_dir) / "dedup_index.db"
    if not index_db.exists():
        return
    conn = sqlite3.connect(str(index_db), timeout=30)
    try:
        old_exists = conn.execute(
            "SELECT 1 FROM identities WHERE paper_id=?",
            (old_id,)).fetchone()
        if not old_exists:
            return
        new_exists = conn.execute(
            "SELECT 1 FROM identities WHERE paper_id=?",
            (new_id,)).fetchone()
        if new_exists:
            conn.execute(
                "DELETE FROM identities WHERE paper_id=?", (old_id,))
        else:
            conn.execute(
                "UPDATE identities SET paper_id=? WHERE paper_id=?",
                (new_id, old_id))
        conn.commit()
    finally:
        conn.close()


def rename_paper_id(db_path: str,
                    old_base_id: str,
                    new_base_id: str,
                    project_dir: str) -> dict:
    """
    原子重命名一篇文献的 paper_id。
    同步更新:
      1. papers 表里所有 <old_base_id>__zh / <old_base_id>__en 的主键
      2. data_json 内的 _paper_id 字段
      3. extract_cache/<old_base_id>/ 目录改名为 extract_cache/<new_base_id>/
    返回 {"ok": True, "renamed": [...]} 或 {"ok": False, "error": "..."}
    """
    conn = sqlite3.connect(db_path)
    try:
        # 找出所有匹配 old_base_id 的行(可能有 __zh 和 __en 两条)
        rows = conn.execute(
            "SELECT paper_id, schema_ver, model_used, lang, data_json, edit_log, added_at "
            "FROM papers WHERE paper_id LIKE ?",
            (old_base_id + "__%",)
        ).fetchall()

        # 也尝试精确匹配(兼容没有 __ 分隔符的老 ID)
        exact = conn.execute(
            "SELECT paper_id, schema_ver, model_used, lang, data_json, edit_log, added_at "
            "FROM papers WHERE paper_id=?",
            (old_base_id,)
        ).fetchone()
        if exact:
            rows = list(rows) + [exact]

        if not rows:
            conn.close()
            return {"ok": False, "error": "未找到 paper_id: " + old_base_id}

        # 检查新 ID 是否已存在(任意一条)
        for row in rows:
            old_full_id = row[0]
            suffix = old_full_id[len(old_base_id):]   # "__zh" / "__en" / ""
            new_full_id = new_base_id + suffix
            conflict = conn.execute(
                "SELECT 1 FROM papers WHERE paper_id=?",
                (new_full_id,)
            ).fetchone()
            if conflict:
                conn.close()
                return {
                    "ok": False,
                    "error": "目标 ID 已存在: " + new_full_id + "，请换一个名称"
                }

        # 执行重命名:用事务保证原子性
        renamed = []
        conn.execute("BEGIN")
        for row in rows:
            old_full_id = row[0]
            suffix = old_full_id[len(old_base_id):]
            new_full_id = new_base_id + suffix

            data = json.loads(row[4])
            data["_paper_id"] = new_full_id

            edit_log = json.loads(row[5])
            edit_log.append({
                "field": "_paper_id",
                "old_value": old_full_id,
                "new_value": new_full_id,
                "edited_at": datetime.now().isoformat(),
                "edited_by": "rename_tool"
            })

            # 先插入新行,再删旧行(避免主键约束冲突)
            conn.execute(
                "INSERT INTO papers "
                "(paper_id, added_at, schema_ver, model_used, lang, data_json, edit_log) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (new_full_id,
                 row[6],
                 row[1],
                 row[2],
                 row[3],
                 json.dumps(data, ensure_ascii=False),
                 json.dumps(edit_log, ensure_ascii=False))
            )
            conn.execute("DELETE FROM papers WHERE paper_id=?", (old_full_id,))
            renamed.append((old_full_id, new_full_id))

        conn.commit()

    except Exception as e:
        conn.rollback()
        conn.close()
        return {"ok": False, "error": "数据库操作失败: " + str(e)}

    conn.close()

    # 同步 text_cache / extract_cache 目录及缓存内 paper_id。
    cache_messages = []
    for folder_name in ("text_cache", "extract_cache"):
        cache_old = Path(project_dir) / folder_name / old_base_id
        cache_new = Path(project_dir) / folder_name / new_base_id
        if not cache_old.exists():
            continue
        try:
            if cache_new.exists():
                cache_messages.append(
                    folder_name + " 目标目录已存在，跳过目录改名")
            else:
                cache_old.rename(cache_new)
                if folder_name == "text_cache":
                    meta_path = cache_new / "meta.json"
                    if meta_path.exists():
                        meta = json.loads(meta_path.read_text(
                            encoding="utf-8"))
                        meta["paper_id"] = new_base_id
                        meta_path.write_text(
                            json.dumps(meta, ensure_ascii=False, indent=2),
                            encoding="utf-8")
                else:
                    for cache_file in cache_new.glob("*.json"):
                        try:
                            data = json.loads(cache_file.read_text(
                                encoding="utf-8"))
                            data["_paper_id"] = new_base_id
                            cache_file.write_text(
                                json.dumps(
                                    data, ensure_ascii=False, indent=2),
                                encoding="utf-8")
                        except Exception:
                            pass
        except Exception as e:
            cache_messages.append(
                folder_name + " 目录改名失败: " + str(e))

    # 同步持久去重索引中的基础 ID。
    index_path = Path(project_dir) / "dedup_index.json"
    if index_path.exists():
        try:
            payload = json.loads(index_path.read_text(encoding="utf-8"))
            changed = False
            for record in payload.get("records", []) or []:
                if record.get("paper_id") == old_base_id:
                    record["paper_id"] = new_base_id
                    changed = True
            if changed:
                index_path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8")
        except Exception as e:
            cache_messages.append("去重索引更新失败: " + str(e))
    try:
        _rename_identity_index_record(
            project_dir, old_base_id, new_base_id)
    except Exception as e:
        cache_messages.append("SQLite 去重索引更新失败: " + str(e))

    return {
        "ok": True,
        "renamed": renamed,
        "cache_msg": ("; ".join(cache_messages)
                      if cache_messages else "")
    }

def reject_paper(db_path: str, paper_id: str, data: dict, reason: str):
    """移入拒绝库"""
    rej_path = str(Path(db_path).parent / "rejected.db")
    conn = sqlite3.connect(rej_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rejected (
            paper_id TEXT, rejected_at TEXT, reason TEXT, data_json TEXT
        )
    """)
    conn.execute(
        "INSERT INTO rejected VALUES (?, ?, ?, ?)",
        (paper_id, datetime.now().isoformat(), reason,
         json.dumps(data, ensure_ascii=False))
    )
    conn.commit()
    conn.close()

def get_all_papers(db_path: str) -> list:
    """读取所有已入库文献"""
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT paper_id, added_at, schema_ver, model_used, data_json, edit_log "
        "FROM papers ORDER BY added_at DESC"
    ).fetchall()
    conn.close()
    result = []
    for row in rows:
        data = json.loads(row[4])
        data["_paper_id"] = row[0]
        data["_added_at"] = row[1]
        data["_schema_ver"] = row[2]
        data["_model_used"] = row[3]
        data["_edit_log"] = json.loads(row[5])
        result.append(data)
    return result


def get_paper(db_path: str, paper_id: str) -> dict:
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT data_json, edit_log FROM papers WHERE paper_id=?",
        (paper_id,)
    ).fetchone()
    conn.close()
    if not row:
        return {}
    data = json.loads(row[0])
    data["_edit_log"] = json.loads(row[1])
    return data


def get_stats(db_path: str) -> dict:
    conn = sqlite3.connect(db_path)
    total = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    conn.close()
    rej_path = str(Path(db_path).parent / "rejected.db")
    rejected = 0
    if Path(rej_path).exists():
        c = sqlite3.connect(rej_path)
        try:
            rejected = c.execute("SELECT COUNT(*) FROM rejected").fetchone()[0]
        except Exception:
            pass
        c.close()
    return {"total": total, "rejected": rejected}


# ── 文献库身份去重与安全合并 ────────────────────────────

def _normalize_identity_value(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.upper() in ("N/A", "NONE", "NULL"):
        return ""
    text = unicodedata.normalize("NFKC", text).casefold()
    return "".join(re.findall(r'[a-z0-9]+|[\u4e00-\u9fff]', text))


def _normalize_db_doi(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    text = re.sub(
        r'^\s*(?:https?://(?:dx\.)?doi\.org/|doi\s*:\s*)',
        '',
        text,
        flags=re.IGNORECASE)
    match = re.search(
        r'\b10\.\d{4,9}/[-._;()/:A-Z0-9]+',
        text,
        flags=re.IGNORECASE)
    return (match.group(0).rstrip('.,;:)]}').lower()
            if match else "")


def _is_missing_value(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().upper() in ("", "N/A", "NONE", "NULL", "NA")
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def _data_completeness(data: dict) -> int:
    return sum(
        1 for key, value in data.items()
        if not key.startswith("_") and not _is_missing_value(value)
    )


def find_duplicate_groups(db_path: str) -> list:
    """
    扫描当前数据库中的重复基础文献。

    __zh / __en 属于同一基础文献，不互相算重复。跨基础 ID 优先按 DOI，
    DOI 缺失时按“规范化标题 + 年份或第一作者”连接为重复组。
    """
    papers = get_all_papers(db_path)
    bases = {}
    for paper in papers:
        full_id = paper.get("_paper_id", "")
        base_id = full_id.rsplit("__", 1)[0]
        if not base_id:
            continue
        entry = bases.setdefault(base_id, {
            "base_id": base_id,
            "papers": [],
            "dois": set(),
            "titles": set(),
            "years": set(),
            "authors": set(),
            "languages": set(),
        })
        entry["papers"].append(paper)
        doi = _normalize_db_doi(paper.get("DOI", ""))
        title = _normalize_identity_value(paper.get("Title", ""))
        year_match = re.search(
            r'\b(19\d{2}|20\d{2})\b',
            str(paper.get("Year", "") or ""))
        author = _normalize_identity_value(
            paper.get("First_Author", ""))
        if doi:
            entry["dois"].add(doi)
        if title:
            entry["titles"].add(title)
        if year_match:
            entry["years"].add(year_match.group(1))
        if author:
            entry["authors"].add(author)
        lang = paper.get("_lang", "")
        if lang:
            entry["languages"].add(lang)

    base_ids = sorted(bases)
    parent = {base_id: base_id for base_id in base_ids}
    reasons = {}

    def find(value):
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(a, b, reason):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra
        key = tuple(sorted((a, b)))
        reasons.setdefault(key, set()).add(reason)

    for i, first_id in enumerate(base_ids):
        first = bases[first_id]
        for second_id in base_ids[i + 1:]:
            second = bases[second_id]
            shared_doi = first["dois"] & second["dois"]
            if shared_doi:
                union(
                    first_id, second_id,
                    "DOI 相同：" + sorted(shared_doi)[0])
                continue

            shared_title = first["titles"] & second["titles"]
            if not shared_title:
                continue
            same_year = bool(first["years"] & second["years"])
            same_author = bool(first["authors"] & second["authors"])
            if same_year or same_author:
                union(
                    first_id, second_id,
                    "标题相同，且" +
                    ("年份一致" if same_year else "第一作者一致"))

    grouped = {}
    for base_id in base_ids:
        grouped.setdefault(find(base_id), []).append(base_id)

    result = []
    for members in grouped.values():
        if len(members) < 2:
            continue
        member_set = set(members)
        group_reasons = set()
        for pair, pair_reasons in reasons.items():
            if pair[0] in member_set and pair[1] in member_set:
                group_reasons.update(pair_reasons)

        records = []
        for base_id in members:
            entry = bases[base_id]
            representative = max(
                entry["papers"],
                key=_data_completeness)
            records.append({
                "base_id": base_id,
                "doi": next(iter(sorted(entry["dois"])), ""),
                "title": str(representative.get("Title", "") or ""),
                "languages": sorted(entry["languages"]),
                "record_count": len(entry["papers"]),
                "completeness": max(
                    _data_completeness(p) for p in entry["papers"]),
            })
        records.sort(
            key=lambda item: (
                -item["completeness"],
                -item["record_count"],
                item["base_id"],
            ))
        result.append({
            "reason": "; ".join(sorted(group_reasons)),
            "base_ids": sorted(members),
            "records": records,
            "recommended_keep": records[0]["base_id"],
        })

    result.sort(key=lambda group: group["recommended_keep"])
    return result


def _merge_missing_fields(target: dict, source: dict) -> tuple:
    merged = dict(target)
    filled = []
    for key, value in source.items():
        if key.startswith("_"):
            continue
        if _is_missing_value(merged.get(key)) and not _is_missing_value(value):
            merged[key] = value
            filled.append(key)
    return merged, filled


def _merge_cache_directories(project_dir: str, keep_base_id: str,
                             remove_base_ids: list) -> list:
    messages = []
    project = Path(project_dir)
    for folder_name in ("text_cache", "extract_cache"):
        keep_dir = project / folder_name / keep_base_id
        for remove_id in remove_base_ids:
            source_dir = project / folder_name / remove_id
            if not source_dir.exists():
                continue
            try:
                if not keep_dir.exists():
                    source_dir.rename(keep_dir)
                else:
                    for source_file in source_dir.rglob("*"):
                        if not source_file.is_file():
                            continue
                        relative = source_file.relative_to(source_dir)
                        target_file = keep_dir / relative
                        if not target_file.exists():
                            target_file.parent.mkdir(
                                parents=True, exist_ok=True)
                            shutil.copy2(source_file, target_file)
                    shutil.rmtree(source_dir)
            except Exception as e:
                messages.append(
                    folder_name + "/" + remove_id + ": " + str(e))

        if not keep_dir.exists():
            continue
        if folder_name == "text_cache":
            meta_path = keep_dir / "meta.json"
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    meta["paper_id"] = keep_base_id
                    meta_path.write_text(
                        json.dumps(meta, ensure_ascii=False, indent=2),
                        encoding="utf-8")
                except Exception:
                    pass
        else:
            for cache_file in keep_dir.glob("*.json"):
                try:
                    data = json.loads(cache_file.read_text(encoding="utf-8"))
                    data["_paper_id"] = keep_base_id
                    cache_file.write_text(
                        json.dumps(data, ensure_ascii=False, indent=2),
                        encoding="utf-8")
                except Exception:
                    pass
    return messages


def merge_duplicate_group(db_path: str, keep_base_id: str,
                          remove_base_ids: list,
                          project_dir: str) -> dict:
    """
    合并一组重复基础文献。

    保留 keep_base_id 的非空值；仅用重复记录填补空字段。缺失语言会整体
    移入保留 ID。数据库操作使用事务，成功后再同步缓存和去重索引。
    """
    remove_ids = [
        value for value in dict.fromkeys(remove_base_ids)
        if value and value != keep_base_id
    ]
    if not remove_ids:
        return {"ok": False, "error": "没有可合并的重复 ID"}

    conn = sqlite3.connect(db_path)
    merged_rows = 0
    filled_fields = 0
    try:
        conn.execute("BEGIN")
        for remove_id in remove_ids:
            rows = conn.execute(
                "SELECT paper_id, added_at, schema_ver, model_used, lang, "
                "data_json, edit_log FROM papers "
                "WHERE paper_id=? OR paper_id LIKE ?",
                (remove_id, remove_id + "__%")
            ).fetchall()
            for row in rows:
                old_full_id = row[0]
                suffix = old_full_id[len(remove_id):]
                new_full_id = keep_base_id + suffix
                source_data = json.loads(row[5])
                source_log = json.loads(row[6] or "[]")
                target = conn.execute(
                    "SELECT added_at, schema_ver, model_used, lang, "
                    "data_json, edit_log FROM papers WHERE paper_id=?",
                    (new_full_id,)
                ).fetchone()

                merge_event = {
                    "field": "_paper_id",
                    "old_value": old_full_id,
                    "new_value": new_full_id,
                    "edited_at": datetime.now().isoformat(),
                    "edited_by": "duplicate_merge",
                }

                if target:
                    target_data = json.loads(target[4])
                    merged_data, filled = _merge_missing_fields(
                        target_data, source_data)
                    merged_data["_paper_id"] = new_full_id
                    target_log = json.loads(target[5] or "[]")
                    target_log.extend(source_log)
                    target_log.append(merge_event)
                    conn.execute(
                        "UPDATE papers SET data_json=?, edit_log=? "
                        "WHERE paper_id=?",
                        (
                            json.dumps(merged_data, ensure_ascii=False),
                            json.dumps(target_log, ensure_ascii=False),
                            new_full_id,
                        ))
                    filled_fields += len(filled)
                else:
                    source_data["_paper_id"] = new_full_id
                    source_log.append(merge_event)
                    conn.execute(
                        "INSERT INTO papers "
                        "(paper_id, added_at, schema_ver, model_used, lang, "
                        "data_json, edit_log) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            new_full_id, row[1], row[2], row[3], row[4],
                            json.dumps(source_data, ensure_ascii=False),
                            json.dumps(source_log, ensure_ascii=False),
                        ))

                conn.execute(
                    "DELETE FROM papers WHERE paper_id=?", (old_full_id,))
                merged_rows += 1
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return {"ok": False, "error": "数据库合并失败: " + str(e)}
    conn.close()

    cache_messages = _merge_cache_directories(
        project_dir, keep_base_id, remove_ids)

    index_path = Path(project_dir) / "dedup_index.json"
    if index_path.exists():
        try:
            payload = json.loads(index_path.read_text(encoding="utf-8"))
            for record in payload.get("records", []) or []:
                if record.get("paper_id") in remove_ids:
                    record["paper_id"] = keep_base_id
            index_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except Exception as e:
            cache_messages.append("去重索引更新失败: " + str(e))
    for remove_id in remove_ids:
        try:
            _rename_identity_index_record(
                project_dir, remove_id, keep_base_id)
        except Exception as e:
            cache_messages.append(
                "SQLite 去重索引更新失败: " + str(e))

    return {
        "ok": True,
        "keep_base_id": keep_base_id,
        "removed_base_ids": remove_ids,
        "merged_rows": merged_rows,
        "filled_fields": filled_fields,
        "cache_messages": cache_messages,
    }


# ── 导入导出 ──────────────────────────────────────────

BACKUP_FORMAT_VERSION = 2
MAX_ZIP_MEMBERS = 200000
MAX_ZIP_UNCOMPRESSED_BYTES = 50 * 1024 * 1024 * 1024


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite_has_table(db_path: Path, table_name: str) -> bool:
    try:
        conn = sqlite3.connect(str(db_path))
        found = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,)).fetchone() is not None
        conn.close()
        return found
    except Exception:
        return False


def _snapshot_sqlite(source: Path, target: Path):
    target.parent.mkdir(parents=True, exist_ok=True)
    source_conn = sqlite3.connect(str(source))
    target_conn = sqlite3.connect(str(target))
    try:
        source_conn.backup(target_conn)
    finally:
        target_conn.close()
        source_conn.close()


def export_zip(project_dir: str, output_path: str) -> dict:
    """导出兼容旧版目录结构的完整、一致性 ZIP 备份。"""
    project = Path(project_dir)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    files = []
    databases = []

    with tempfile.TemporaryDirectory() as tmp_name:
        staging = Path(tmp_name) / "project"
        staging.mkdir(parents=True, exist_ok=True)

        database_dir = project / "database"
        if database_dir.exists():
            for source in sorted(database_dir.glob("*.db")):
                target = staging / "database" / source.name
                _snapshot_sqlite(source, target)
                if _sqlite_has_table(target, "papers"):
                    databases.append(source.stem)

        for folder_name in (
                "text_cache", "extract_cache", "citation_sessions"):
            source = project / folder_name
            if source.exists():
                shutil.copytree(
                    source, staging / folder_name, dirs_exist_ok=True)

        for file_name in ("dedup_index.json", "citation_schemes.json"):
            source = project / file_name
            if source.exists():
                shutil.copy2(source, staging / file_name)
        identity_db = project / "dedup_index.db"
        if identity_db.exists():
            _snapshot_sqlite(
                identity_db, staging / "dedup_index.db")

        for file in sorted(staging.rglob("*")):
            if file.is_file():
                files.append({
                    "path": file.relative_to(staging.parent).as_posix(),
                    "size": file.stat().st_size,
                    "sha256": _file_sha256(file),
                })

        manifest = {
            "app": "Lazybones",
            "format_version": BACKUP_FORMAT_VERSION,
            "exported_at": datetime.now().isoformat(),
            "databases": databases,
            "files": files,
        }
        (staging / "backup_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8")

        with zipfile.ZipFile(
                output, "w", zipfile.ZIP_DEFLATED) as archive:
            for file in sorted(staging.rglob("*")):
                if file.is_file():
                    archive.write(
                        file, file.relative_to(staging.parent).as_posix())

    return {
        "format_version": BACKUP_FORMAT_VERSION,
        "databases": databases,
        "file_count": len(files) + 1,
        "output_path": str(output),
    }


def _validate_zip_members(archive: zipfile.ZipFile):
    members = archive.infolist()
    if len(members) > MAX_ZIP_MEMBERS:
        raise ValueError(
            "ZIP 文件数量异常：" + str(len(members)) + " 个条目")
    total_size = sum(member.file_size for member in members)
    if total_size > MAX_ZIP_UNCOMPRESSED_BYTES:
        raise ValueError(
            "ZIP 解压后体积超过安全上限：" +
            str(round(total_size / 1024 / 1024 / 1024, 2)) + " GB")
    for member in members:
        if (member.file_size > 100 * 1024 * 1024
                and member.compress_size > 0
                and member.file_size / member.compress_size > 2000):
            raise ValueError(
                "ZIP 中存在异常高压缩比文件：" + member.filename)


def _safe_extract_zip(archive: zipfile.ZipFile, destination: Path):
    _validate_zip_members(archive)
    destination = destination.resolve()
    for member in archive.infolist():
        member_path = (destination / member.filename).resolve()
        try:
            member_path.relative_to(destination)
        except ValueError:
            raise ValueError("ZIP 包含不安全路径: " + member.filename)
    archive.extractall(destination)


def _verify_backup_manifest(extracted: Path, root: Path) -> int:
    manifest_path = root / "backup_manifest.json"
    if not manifest_path.exists():
        return 0
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("app") != "Lazybones":
        raise ValueError("备份清单不是 Lazybones 格式")
    checked = 0
    for item in manifest.get("files", []):
        relative = str(item.get("path", "")).replace("\\", "/")
        candidate = (extracted / relative).resolve()
        try:
            candidate.relative_to(extracted.resolve())
        except ValueError:
            raise ValueError("备份清单包含不安全路径：" + relative)
        if not candidate.is_file():
            raise ValueError("备份文件缺失：" + relative)
        expected_size = item.get("size")
        if expected_size is not None and candidate.stat().st_size != expected_size:
            raise ValueError("备份文件大小校验失败：" + relative)
        expected_hash = str(item.get("sha256", "") or "").lower()
        if expected_hash and _file_sha256(candidate) != expected_hash:
            raise ValueError("备份文件 SHA-256 校验失败：" + relative)
        checked += 1
    return checked


def _find_backup_root(extracted: Path) -> Path:
    project_root = extracted / "project"
    if project_root.exists():
        return project_root
    candidates = [
        item.parent.parent for item in extracted.rglob("*.db")
        if item.parent.name == "database"
    ]
    return candidates[0] if candidates else extracted


def inspect_backup(zip_path: str) -> dict:
    """读取新旧备份的元数据，不修改当前项目。"""
    with zipfile.ZipFile(zip_path, "r") as archive:
        names = set(archive.namelist())
        manifest_name = next(
            (name for name in names
             if name.replace("\\", "/").endswith(
                 "project/backup_manifest.json")),
            None)
        manifest = {}
        if manifest_name:
            try:
                manifest = json.loads(
                    archive.read(manifest_name).decode("utf-8"))
            except Exception:
                manifest = {}
        database_names = sorted({
            Path(name.replace("\\", "/")).stem
            for name in names
            if "/database/" in "/" + name.replace("\\", "/")
            and name.lower().endswith(".db")
            and Path(name).stem not in ("rejected", "chats")
        })
        return {
            "format_version": int(
                manifest.get("format_version", 1) or 1),
            "databases": manifest.get("databases") or database_names,
            "exported_at": manifest.get("exported_at", ""),
            "has_manifest": bool(manifest),
            "has_citation_schemes": any(
                name.replace("\\", "/").endswith(
                    "project/citation_schemes.json")
                for name in names),
            "has_citation_sessions": any(
                "/citation_sessions/" in "/" + name.replace("\\", "/")
                for name in names),
        }


def _merge_json_records(target_path: Path, source_path: Path):
    if not source_path.exists():
        return
    if not target_path.exists():
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)
        return
    try:
        target = json.loads(target_path.read_text(encoding="utf-8"))
        source = json.loads(source_path.read_text(encoding="utf-8"))
        if target_path.name == "citation_schemes.json":
            target_items = target.setdefault("schemes", [])
            known_ids = {item.get("id") for item in target_items}
            known_names = {item.get("name") for item in target_items}
            for item in source.get("schemes", []):
                if (item.get("id") not in known_ids
                        and item.get("name") not in known_names):
                    target_items.append(item)
                    known_ids.add(item.get("id"))
                    known_names.add(item.get("name"))
        else:
            target_items = target.setdefault("records", [])
            def identity_tuple(item):
                identity = item.get("identity") or {}
                return (
                    item.get("paper_id"),
                    identity.get("file_sha256"),
                    identity.get("doi"),
                    identity.get("text_sha256"),
                )
            seen = {
                identity_tuple(item)
                for item in target_items
            }
            for item in source.get("records", []):
                identity = identity_tuple(item)
                if identity not in seen:
                    target_items.append(item)
                    seen.add(identity)
        target_path.write_text(
            json.dumps(target, ensure_ascii=False, indent=2),
            encoding="utf-8")
    except Exception as error:
        raise ValueError(
            target_path.name + " 合并失败：" + str(error)) from error


def _copy_cache_tree(source: Path, target: Path):
    created = []
    if not source.exists():
        return created
    target.mkdir(parents=True, exist_ok=True)
    for item in source.rglob("*"):
        if item.is_file():
            destination = target / item.relative_to(source)
            if not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                created.append(destination)
                shutil.copy2(item, destination)
    return created


def _merge_identity_sqlite(target_path: Path, source_path: Path):
    if not source_path.exists():
        return
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target = sqlite3.connect(str(target_path), timeout=30)
    source = sqlite3.connect(str(source_path), timeout=30)
    columns = (
        "paper_id, main_file, recorded_at, file_sha256, doi, "
        "text_sha256, title_key, year, first_author_key, si_sha256_json"
    )
    try:
        target.execute("""
            CREATE TABLE IF NOT EXISTS identities (
                paper_id TEXT PRIMARY KEY,
                main_file TEXT,
                recorded_at TEXT,
                file_sha256 TEXT,
                doi TEXT,
                text_sha256 TEXT,
                title_key TEXT,
                year TEXT,
                first_author_key TEXT,
                si_sha256_json TEXT DEFAULT '[]'
            )
        """)
        has_source = source.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='identities'").fetchone()
        if not has_source:
            return
        existing_rows = target.execute(
            "SELECT paper_id, file_sha256, doi, text_sha256 "
            "FROM identities").fetchall()
        strong_keys = set()
        for paper_id, file_hash, doi, text_hash in existing_rows:
            for kind, value in (
                    ("file", file_hash), ("doi", doi), ("text", text_hash)):
                if value:
                    strong_keys.add((kind, value))
        target.execute("BEGIN")
        for row in source.execute("SELECT " + columns + " FROM identities"):
            source_keys = {
                (kind, value)
                for kind, value in (
                    ("file", row[3]), ("doi", row[4]), ("text", row[5]))
                if value
            }
            if source_keys & strong_keys:
                continue
            target.execute(
                "INSERT OR IGNORE INTO identities (" + columns + ") "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", row)
            strong_keys.update(source_keys)
        target.commit()
    except Exception:
        target.rollback()
        raise
    finally:
        source.close()
        target.close()


def _safe_database_name(name: str) -> str:
    safe = re.sub(r'[^\w\-]', '_', str(name or "")).strip("_")
    return safe or "imported"


def _unique_database_path(database_dir: Path, name: str) -> Path:
    safe = _safe_database_name(name)
    candidate = database_dir / (safe + ".db")
    index = 2
    while candidate.exists():
        candidate = database_dir / (safe + "_" + str(index) + ".db")
        index += 1
    return candidate


def import_zip(zip_path: str, project_dir: str, mode: str = "merge",
               target_db_name: str = "main",
               source_db_name: str = None,
               new_db_name: str = None) -> dict:
    """按合并、替换或新建文献库模式导入新旧 Lazybones ZIP。"""
    if mode not in ("merge", "replace", "new_library"):
        raise ValueError("未知导入模式: " + str(mode))
    project = Path(project_dir)
    database_dir = project / "database"
    database_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "mode": mode,
        "imported_databases": [],
        "inserted": 0,
        "merged": 0,
        "backups": [],
        "verified_files": 0,
        "rollback_performed": False,
    }

    with tempfile.TemporaryDirectory() as tmp_name:
        extracted = Path(tmp_name)
        with zipfile.ZipFile(zip_path, "r") as archive:
            _safe_extract_zip(archive, extracted)
        root = _find_backup_root(extracted)
        summary["verified_files"] = _verify_backup_manifest(
            extracted, root)
        source_db_dir = root / "database"
        paper_databases = [
            path for path in sorted(source_db_dir.glob("*.db"))
            if _sqlite_has_table(path, "papers")
        ]
        if source_db_name:
            requested = Path(source_db_name).stem
            paper_databases = [
                path for path in paper_databases
                if path.stem == requested]
        if not paper_databases:
            raise ValueError("备份中未找到可导入的文献数据库")
        # 临时解压目录内就地补齐旧库缺少的可选列，原 ZIP 不会被修改。
        for source_database in paper_databases:
            init_db(str(source_database))

        rollback_dir = extracted / "_rollback"
        rollback_dir.mkdir()
        snapshots = {}
        created_targets = set()
        created_cache_files = []

        def preserve(path: Path, sqlite_file=False):
            path = path.resolve()
            if path in snapshots or path in created_targets:
                return
            if not path.exists():
                created_targets.add(path)
                return
            backup_path = rollback_dir / (
                str(len(snapshots)) + "_" + path.name)
            if sqlite_file:
                _snapshot_sqlite(path, backup_path)
            else:
                shutil.copy2(path, backup_path)
            snapshots[path] = (backup_path, sqlite_file)

        try:
            if mode == "new_library":
                for source in paper_databases:
                    preferred = (
                        new_db_name if len(paper_databases) == 1
                        else ((new_db_name + "_" + source.stem)
                              if new_db_name else source.stem)
                    )
                    target = _unique_database_path(
                        database_dir, preferred or source.stem)
                    preserve(target, sqlite_file=True)
                    _snapshot_sqlite(source, target)
                    summary["imported_databases"].append(target.stem)
            else:
                source = paper_databases[0]
                target = database_dir / (
                    _safe_database_name(target_db_name) + ".db")
                preserve(target, sqlite_file=True)
                if mode == "replace":
                    if target.exists():
                        backup_dir = project / "backups"
                        backup_dir.mkdir(parents=True, exist_ok=True)
                        backup = backup_dir / (
                            target.stem + ".preimport_" +
                            datetime.now().strftime("%Y%m%d_%H%M%S") +
                            target.suffix)
                        _snapshot_sqlite(target, backup)
                        summary["backups"].append(str(backup))
                    _snapshot_sqlite(source, target)
                    summary["imported_databases"].append(target.stem)
                else:
                    merge_result = _merge_sqlite(
                        str(target), str(source))
                    summary.update(merge_result)
                    summary["imported_databases"].append(target.stem)

            for folder_name in (
                    "text_cache", "extract_cache", "citation_sessions"):
                created_cache_files.extend(_copy_cache_tree(
                    root / folder_name, project / folder_name))

            for file_name in ("dedup_index.json", "citation_schemes.json"):
                target_file = project / file_name
                preserve(target_file)
                _merge_json_records(target_file, root / file_name)

            identity_target = project / "dedup_index.db"
            preserve(identity_target, sqlite_file=True)
            _merge_identity_sqlite(
                identity_target, root / "dedup_index.db")
        except Exception as import_error:
            rollback_errors = []
            for cache_file in reversed(created_cache_files):
                try:
                    if cache_file.exists():
                        cache_file.unlink()
                except Exception as error:
                    rollback_errors.append(str(error))
            for target_path in created_targets:
                try:
                    if target_path.exists() and target_path.is_file():
                        target_path.unlink()
                except Exception as error:
                    rollback_errors.append(str(error))
            for target_path, (backup_path, sqlite_file) in snapshots.items():
                try:
                    if sqlite_file:
                        _snapshot_sqlite(backup_path, target_path)
                    else:
                        target_path.parent.mkdir(
                            parents=True, exist_ok=True)
                        shutil.copy2(backup_path, target_path)
                except Exception as error:
                    rollback_errors.append(str(error))
            summary["rollback_performed"] = True
            detail = "；回滚完成"
            if rollback_errors:
                detail = "；回滚有异常：" + " | ".join(rollback_errors[:3])
            raise RuntimeError(
                "导入失败，未保留不完整结果：" +
                str(import_error) + detail) from import_error

    return summary


def _base_and_suffix(paper_id: str) -> tuple:
    if "__" in paper_id:
        base, suffix = paper_id.rsplit("__", 1)
        return base, "__" + suffix
    return paper_id, ""


def _identity_key(data: dict) -> tuple:
    doi = _normalize_db_doi(data.get("DOI", ""))
    if doi:
        return ("doi", doi)
    title = _normalize_identity_value(data.get("Title", ""))
    if not title:
        return ()
    year_match = re.search(
        r'\b(19\d{2}|20\d{2})\b', str(data.get("Year", "") or ""))
    author = _normalize_identity_value(data.get("First_Author", ""))
    qualifier = year_match.group(1) if year_match else author
    return ("title", title, qualifier) if qualifier else ()


def _merge_sqlite(target_db: str, source_db: str) -> dict:
    """身份感知合并；只填补缺失字段，不覆盖目标库已有值。"""
    Path(target_db).parent.mkdir(parents=True, exist_ok=True)
    init_db(target_db)
    source_conn = sqlite3.connect(source_db)
    target_conn = sqlite3.connect(target_db)
    inserted = 0
    merged = 0
    try:
        source_rows = source_conn.execute(
            "SELECT paper_id, added_at, schema_ver, model_used, lang, "
            "data_json, edit_log FROM papers"
        ).fetchall()
        target_rows = target_conn.execute(
            "SELECT paper_id, data_json FROM papers"
        ).fetchall()
        target_identity = {}
        for existing_id, data_json in target_rows:
            try:
                key = _identity_key(json.loads(data_json or "{}"))
                if key:
                    target_identity.setdefault(
                        key, _base_and_suffix(existing_id)[0])
            except Exception:
                pass

        target_conn.execute("BEGIN")
        for row in source_rows:
            source_id = row[0]
            source_data = json.loads(row[5] or "{}")
            source_log = json.loads(row[6] or "[]")
            key = _identity_key(source_data)
            source_base, suffix = _base_and_suffix(source_id)
            target_base = target_identity.get(key, source_base)
            destination_id = target_base + suffix
            existing = target_conn.execute(
                "SELECT data_json, edit_log FROM papers WHERE paper_id=?",
                (destination_id,)).fetchone()
            if existing:
                existing_data = json.loads(existing[0] or "{}")
                merged_data, filled = _merge_missing_fields(
                    existing_data, source_data)
                existing_log = json.loads(existing[1] or "[]")
                if filled:
                    existing_log.append({
                        "field": "_import",
                        "old_value": "",
                        "new_value": "填补字段: " + ", ".join(filled),
                        "edited_at": datetime.now().isoformat(),
                        "edited_by": "zip_import",
                    })
                existing_log.extend(source_log)
                merged_data["_paper_id"] = destination_id
                target_conn.execute(
                    "UPDATE papers SET data_json=?, edit_log=? "
                    "WHERE paper_id=?",
                    (
                        json.dumps(merged_data, ensure_ascii=False),
                        json.dumps(existing_log, ensure_ascii=False),
                        destination_id,
                    ))
                merged += 1
            else:
                source_data["_paper_id"] = destination_id
                target_conn.execute(
                    "INSERT INTO papers "
                    "(paper_id, added_at, schema_ver, model_used, lang, "
                    "data_json, edit_log) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        destination_id, row[1], row[2], row[3], row[4],
                        json.dumps(source_data, ensure_ascii=False),
                        json.dumps(source_log, ensure_ascii=False),
                    ))
                inserted += 1
            if key:
                target_identity.setdefault(key, target_base)
        target_conn.commit()
    except Exception:
        target_conn.rollback()
        raise
    finally:
        source_conn.close()
        target_conn.close()
    return {"inserted": inserted, "merged": merged}

# ── 引用格式生成 ──────────────────────────────────────

def format_citation_acs(data: dict) -> str:
    """
    生成 ACS 格式引用。
    优先使用身份字段(Authors_Full / Journal_Abbr 等),
    兜底兼容老字段名(Authors / Journal)。
    格式:Authors. Title. J. Abbr. Year, Volume (Issue), Pages. DOI: xxx.
    """
    def _v(s):
        """过滤 N/A 和空值"""
        if s is None:
            return ""
        s = str(s).strip()
        return "" if s in ("N/A", "None", "") else s

    def _get(data, *keys):
        """按优先级依次尝试多个字段名,返回第一个有效值"""
        for k in keys:
            val = _v(data.get(k, ""))
            if val:
                return val
        return ""

    authors  = _get(data, "Authors_Full", "Authors")
    title    = _get(data, "Title")
    journal  = _get(data, "Journal_Abbr", "Journal_Full", "Journal")
    year     = _get(data, "Year")
    volume   = _get(data, "Volume")
    issue    = _get(data, "Issue")
    pages    = _get(data, "Pages", "Article_Number")
    doi      = _get(data, "DOI")

    parts = []

    if authors:
        parts.append(authors)

    if title:
        parts.append(title if title.endswith(".") else title + ".")

    # 期刊 + 年份 + 卷期页
    if journal:
        ref = journal
        if year:
            ref += " " + year
        if volume:
            ref += ", " + volume
            if issue:
                ref += " (" + issue + ")"
        if pages:
            ref += ", " + pages
        ref += "."
        parts.append(ref)

    if doi:
        parts.append("DOI: " + doi)

    return " ".join(parts) if parts else "[引用信息不完整]"

def export_citations(db_path: str, paper_ids: list = None) -> list:
    """
    导出引用列表
    paper_ids=None 时导出全部，否则只导出指定ID
    返回 [{"paper_id": ..., "citation": ..., "data": ...}, ...]
    """
    conn = sqlite3.connect(db_path)
    if paper_ids:
        placeholders = ",".join("?" * len(paper_ids))
        rows = conn.execute(
            "SELECT paper_id, data_json FROM papers "
            "WHERE paper_id IN (" + placeholders + ")",
            paper_ids
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT paper_id, data_json FROM papers"
        ).fetchall()
    conn.close()

    results = []
    for paper_id, data_json in rows:
        data = json.loads(data_json)
        citation = format_citation_acs(data)
        results.append({
            "paper_id": paper_id,
            "citation": citation,
            "data": data
        })
    return results

def list_databases(project_dir: str) -> list:
    """列出project_dir/database/下所有.db文件"""
    db_dir = Path(project_dir) / "database"
    db_dir.mkdir(parents=True, exist_ok=True)
    return [
        file.stem for file in sorted(db_dir.glob("*.db"))
        if file.stem != "rejected" and _sqlite_has_table(file, "papers")
    ]


def create_database(project_dir: str, db_name: str) -> str:
    """新建一个数据库，返回路径"""
    safe_name = re.sub(r'[^\w\-]', '_', db_name)
    db_path = str(Path(project_dir) / "database" / (safe_name + ".db"))
    init_db(db_path)
    return db_path


def get_db_path_by_name(project_dir: str, db_name: str) -> str:
    return str(Path(project_dir) / "database" / (db_name + ".db"))

def init_chat_db(project_dir: str) -> str:
    """初始化对话记录数据库"""
    db_path = str(Path(project_dir) / "database" / "chats.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id   TEXT PRIMARY KEY,
            title        TEXT,
            db_scope     TEXT,
            created_at   TEXT,
            updated_at   TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT,
            role        TEXT,
            content     TEXT,
            created_at  TEXT
        )
    """)
    conn.commit()
    conn.close()
    return db_path


def list_chat_sessions(chat_db: str) -> list:
    conn = sqlite3.connect(chat_db)
    rows = conn.execute(
        "SELECT session_id, title, db_scope, updated_at "
        "FROM sessions ORDER BY updated_at DESC"
    ).fetchall()
    conn.close()
    return [{"session_id": r[0], "title": r[1],
             "db_scope": r[2], "updated_at": r[3]} for r in rows]


def save_chat_session(chat_db: str, session_id: str,
                       title: str, db_scope: str):
    now = datetime.now().isoformat()
    conn = sqlite3.connect(chat_db)
    conn.execute("""
        INSERT OR REPLACE INTO sessions
        (session_id, title, db_scope, created_at, updated_at)
        VALUES (?, ?, ?, COALESCE(
            (SELECT created_at FROM sessions WHERE session_id=?), ?
        ), ?)
    """, (session_id, title, db_scope, session_id, now, now))
    conn.commit()
    conn.close()


def save_chat_message(chat_db: str, session_id: str,
                       role: str, content: str):
    conn = sqlite3.connect(chat_db)
    conn.execute(
        "INSERT INTO messages (session_id, role, content, created_at) "
        "VALUES (?, ?, ?, ?)",
        (session_id, role, content, datetime.now().isoformat())
    )
    conn.execute(
        "UPDATE sessions SET updated_at=? WHERE session_id=?",
        (datetime.now().isoformat(), session_id)
    )
    conn.commit()
    conn.close()


def load_chat_messages(chat_db: str, session_id: str) -> list:
    conn = sqlite3.connect(chat_db)
    rows = conn.execute(
        "SELECT role, content FROM messages "
        "WHERE session_id=? ORDER BY id",
        (session_id,)
    ).fetchall()
    conn.close()
    return [{"role": r[0], "content": r[1]} for r in rows]


def delete_chat_session(chat_db: str, session_id: str):
    conn = sqlite3.connect(chat_db)
    conn.execute("DELETE FROM sessions WHERE session_id=?", (session_id,))
    conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
    conn.commit()
    conn.close()

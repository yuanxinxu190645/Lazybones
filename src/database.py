# F:\MOFCNM\src\database.py

import sqlite3
import json
import re
import shutil
import zipfile
from pathlib import Path
from datetime import datetime


def get_db_path(project_dir: str, db_name: str = "main.db") -> str:
    p = Path(project_dir) / "database"
    p.mkdir(parents=True, exist_ok=True)
    return str(p / db_name)


def init_db(db_path: str):
    """初始化数据库表结构"""
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

    # 重命名 extract_cache 目录(数据库已提交,目录改名失败不回滚但会报告)
    cache_old = Path(project_dir) / "extract_cache" / old_base_id
    cache_new = Path(project_dir) / "extract_cache" / new_base_id
    cache_msg = ""
    if cache_old.exists():
        try:
            if cache_new.exists():
                cache_msg = "(⚠ 目标缓存目录已存在,跳过目录改名)"
            else:
                cache_old.rename(cache_new)
        except Exception as e:
            cache_msg = "(⚠ 目录改名失败: " + str(e) + ")"

    return {
        "ok": True,
        "renamed": renamed,
        "cache_msg": cache_msg
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


# ── 导入导出 ──────────────────────────────────────────

def export_zip(project_dir: str, output_path: str):
    """打包整个project目录为zip"""
    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for folder in ["database", "text_cache", "extract_cache"]:
            folder_path = Path(project_dir) / folder
            if folder_path.exists():
                for file in folder_path.rglob("*"):
                    if file.is_file():
                        zf.write(file, file.relative_to(Path(project_dir).parent))


def import_zip(zip_path: str, project_dir: str):
    """
    导入zip，合并到现有数据库
    已存在的paper_id不覆盖
    """
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(tmp)

        # 合并database
        tmp_db = Path(tmp) / "project" / "database" / "main.db"
        if tmp_db.exists():
            _merge_sqlite(
                str(Path(project_dir) / "database" / "main.db"),
                str(tmp_db)
            )

        # 复制text_cache和extract_cache（不覆盖已有）
        for folder in ["text_cache", "extract_cache"]:
            src = Path(tmp) / "project" / folder
            dst = Path(project_dir) / folder
            if src.exists():
                for item in src.iterdir():
                    dst_item = dst / item.name
                    if not dst_item.exists():
                        shutil.copytree(str(item), str(dst_item))


def _merge_sqlite(target_db: str, source_db: str):
    """合并两个SQLite数据库，按paper_id去重"""
    Path(target_db).parent.mkdir(parents=True, exist_ok=True)
    init_db(target_db)

    src_conn = sqlite3.connect(source_db)
    tgt_conn = sqlite3.connect(target_db)

    rows = src_conn.execute(
        "SELECT paper_id, added_at, schema_ver, model_used, lang, data_json, edit_log "
        "FROM papers"
    ).fetchall()

    for row in rows:
        existing = tgt_conn.execute(
            "SELECT 1 FROM papers WHERE paper_id=?", (row[0],)
        ).fetchone()
        if not existing:
            tgt_conn.execute(
                "INSERT INTO papers VALUES (?, ?, ?, ?, ?, ?, ?)", row
            )

    tgt_conn.commit()
    src_conn.close()
    tgt_conn.close()

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
    return [f.stem for f in sorted(db_dir.glob("*.db"))
            if f.stem != "rejected"]


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

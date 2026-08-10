# F:\MOFCNM\src\extractor.py

import re
import json
import hashlib
import sqlite3
import threading
import unicodedata
from pathlib import Path
from datetime import datetime

NL = chr(10)
IDENTITY_INDEX_VERSION = 2
_IDENTITY_WRITE_LOCK = threading.RLock()


# ── SI 文件名识别规则（保留，供 ui.py 的配对弹窗显示用）──────
SI_PATTERN = re.compile(
    r'[_\-\s]?(SI|supplementary|supporting|ESI|S\d+|supp)'
    r'(?=[\._\-\s]|$)',
    re.IGNORECASE
)

LEADING_NUM_PATTERN = re.compile(r'^\s*\d+\s*[.\-_)\s]\s*')


# ── 基础工具 ────────────────────────────────────────────

def get_file_md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def get_file_sha256(path: str) -> str:
    """计算文件内容 SHA-256，用于识别改名后的完全相同文件。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _strip_leading_number(stem: str) -> str:
    """剥掉文件名开头的序号（用户的排序前缀）"""
    return LEADING_NUM_PATTERN.sub('', stem)


def _edit_distance_ratio(a: str, b: str) -> float:
    """编辑距离相似度，返回 0.0 ~ 1.0"""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    la, lb = len(a), len(b)
    if min(la, lb) / max(la, lb) < 0.3:
        return 0.0
    dp = list(range(lb + 1))
    for i in range(1, la + 1):
        prev = dp[:]
        dp[0] = i
        for j in range(1, lb + 1):
            if a[i - 1] == b[j - 1]:
                dp[j] = prev[j - 1]
            else:
                dp[j] = 1 + min(prev[j], dp[j - 1], prev[j - 1])
    return 1.0 - dp[lb] / max(la, lb)


def _normalize_stem(stem: str) -> str:
    """去掉开头序号、SI 关键词、标点、空格，全部小写。"""
    s = _strip_leading_number(stem)
    s = re.sub(
        r'[\s_\-\.]?(?:supporting[\s_\-]*info(?:rmation)?'
        r'|supplementary[\s_\-]*(?:material|information|data|file|note)s?'
        r'|supplemental[\s_\-]*(?:material|information|data|file|note)s?'
        r'|supplementary|supporting|supplemental'
        r'|esi|supp|si(?=[\s_\-\.]|$)'
        r'|s\d{1,2}(?=[\s_\-\.]|$))',
        '',
        s,
        flags=re.IGNORECASE
    )
    s = re.sub(r'[\s\-_\.]+', '', s)
    return s.lower().strip()


# ── 文献身份与去重 ─────────────────────────────────────

_DOI_PATTERN = re.compile(
    r'\b10\.\d{4,9}/[-._;()/:A-Z0-9]+',
    re.IGNORECASE
)


def _normalize_doi(value) -> str:
    """统一 DOI 格式，去掉 URL/doi: 前缀和句末标点。"""
    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.upper() == "N/A":
        return ""
    text = re.sub(
        r'^\s*(?:https?://(?:dx\.)?doi\.org/|doi\s*:\s*)',
        '',
        text,
        flags=re.IGNORECASE
    )
    match = _DOI_PATTERN.search(text)
    if not match:
        return ""
    return match.group(0).rstrip('.,;:)]}').lower()


def _normalize_identity_text(value) -> str:
    """将标题、作者等字段压缩为可稳定比较的形式。"""
    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.upper() == "N/A":
        return ""
    text = unicodedata.normalize("NFKC", text).casefold()
    return "".join(re.findall(r'[a-z0-9]+|[\u4e00-\u9fff]', text))


def _text_fingerprint(text: str) -> str:
    """
    对正文开头生成稳定指纹。

    去掉分页标记、空白和标点后取前 6000 个字符，可让“原文件”和
    text_cache/main.txt 使用同一算法，同时避免整篇解析带来的额外耗时。
    """
    if not text:
        return ""
    text = re.sub(r'\[\s*page\s+\d+\s*\]', '', text,
                  flags=re.IGNORECASE)
    normalized = _normalize_identity_text(text)
    if len(normalized) < 1200:
        return ""
    return hashlib.sha256(normalized[:6000].encode("utf-8")).hexdigest()


def _extract_year(text: str) -> str:
    match = re.search(r'\b(19\d{2}|20\d{2})\b', text or "")
    return match.group(1) if match else ""


def build_document_identity(main_path: str) -> dict:
    """
    从正文文件构建多信号身份。

    file_sha256 识别完全相同但改名/换路径的文件；
    doi 识别同一论文的不同下载版本；
    text_sha256 识别文本内容一致但二进制元数据不同的文件；
    title/year/first_author 作为无 DOI 时的谨慎补充判据。
    """
    path = Path(main_path)
    identity = {
        "file_sha256": get_file_sha256(str(path)),
        "doi": "",
        "text_sha256": "",
        "title_key": "",
        "year": "",
        "first_author_key": "",
    }

    quick_text = _quick_read_text(
        str(path), max_pages=8, max_chars=50000)
    identity["text_sha256"] = _text_fingerprint(quick_text)
    identity["year"] = _extract_year(quick_text)

    meta_title = ""
    meta_author = ""
    meta_extra = ""
    try:
        if path.suffix.lower() == ".pdf":
            import fitz
            doc = fitz.open(str(path))
            meta = doc.metadata or {}
            doc.close()
            meta_title = meta.get("title", "") or ""
            meta_author = meta.get("author", "") or ""
            meta_extra = " ".join([
                meta.get("subject", "") or "",
                meta.get("keywords", "") or "",
            ])
        elif path.suffix.lower() in (".docx", ".doc"):
            from docx import Document
            doc = Document(str(path))
            props = doc.core_properties
            meta_title = props.title or ""
            meta_author = props.author or ""
            meta_extra = props.subject or ""
    except Exception:
        pass

    doi_match = _DOI_PATTERN.search(
        (quick_text or "") + "\n" + meta_extra)
    if doi_match:
        identity["doi"] = _normalize_doi(doi_match.group(0))

    title_key = _normalize_identity_text(meta_title)
    if title_key not in ("untitled", "document", "microsoftword"):
        identity["title_key"] = title_key

    if meta_author:
        first_author = re.split(r'[;,]', meta_author)[0].strip()
        identity["first_author_key"] = _normalize_identity_text(first_author)

    return identity


def _identity_match_reason(incoming: dict, known: dict) -> str:
    """返回强判重理由；空字符串表示证据不足，不自动剔除。"""
    main_reason = ""
    if (incoming.get("file_sha256")
            and incoming["file_sha256"] == known.get("file_sha256")):
        main_reason = "文件内容完全相同（SHA-256）"
    elif (incoming.get("doi")
          and incoming["doi"] == known.get("doi")):
        main_reason = "DOI 相同：" + incoming["doi"]
    elif (incoming.get("text_sha256")
          and incoming["text_sha256"] == known.get("text_sha256")):
        main_reason = "正文内容指纹相同"
    else:
        title = incoming.get("title_key", "")
        if (title and len(title) >= 20 and
                title == known.get("title_key")):
            same_year = (incoming.get("year") and
                         incoming["year"] == known.get("year"))
            same_author = (incoming.get("first_author_key") and
                           incoming["first_author_key"] ==
                           known.get("first_author_key"))
            if same_year or same_author:
                main_reason = "标题相同，且年份或第一作者一致"

    if not main_reason:
        return ""

    # 两边都有新版 SI 内容指纹时，SI 集合不同代表用户补充/替换了 SI。
    # 这种情况必须允许重跑，不能被旧数据库里较弱的 DOI 记录再次拦截。
    if ("si_sha256" in incoming and "si_sha256" in known and
            incoming.get("si_sha256") != known.get("si_sha256")):
        return "__SI_CHANGED__"

    return main_reason


def _identity_from_database_data(data: dict) -> dict:
    """从已入库的 AI 结果恢复可用于去重的身份字段。"""
    year = str(data.get("Year", "") or "")
    year = _extract_year(year)
    return {
        "file_sha256": "",
        "doi": _normalize_doi(data.get("DOI", "")),
        "text_sha256": "",
        "title_key": _normalize_identity_text(data.get("Title", "")),
        "year": year,
        "first_author_key": _normalize_identity_text(
            data.get("First_Author", "")),
    }


def _identity_db_path(project_dir: str) -> Path:
    return Path(project_dir) / "dedup_index.db"


def _init_identity_db(project_dir: str) -> Path:
    """初始化线程/进程安全的文献身份索引。"""
    path = _identity_db_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS identities (
            paper_id          TEXT PRIMARY KEY,
            main_file         TEXT,
            recorded_at       TEXT,
            file_sha256       TEXT,
            doi               TEXT,
            text_sha256       TEXT,
            title_key         TEXT,
            year              TEXT,
            first_author_key  TEXT,
            si_sha256_json    TEXT DEFAULT '[]'
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_identities_file_sha256 "
        "ON identities(file_sha256)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_identities_doi "
        "ON identities(doi)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_identities_text_sha256 "
        "ON identities(text_sha256)")
    conn.execute("PRAGMA user_version=" + str(IDENTITY_INDEX_VERSION))
    conn.commit()
    conn.close()
    return path


def _upsert_identity_row(conn, record: dict):
    identity = record.get("identity") or {}
    conn.execute("""
        INSERT INTO identities (
            paper_id, main_file, recorded_at, file_sha256, doi,
            text_sha256, title_key, year, first_author_key, si_sha256_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(paper_id) DO UPDATE SET
            main_file=excluded.main_file,
            recorded_at=excluded.recorded_at,
            file_sha256=excluded.file_sha256,
            doi=excluded.doi,
            text_sha256=excluded.text_sha256,
            title_key=excluded.title_key,
            year=excluded.year,
            first_author_key=excluded.first_author_key,
            si_sha256_json=excluded.si_sha256_json
        WHERE identities.recorded_at IS NULL
           OR identities.recorded_at = ''
           OR excluded.recorded_at >= identities.recorded_at
    """, (
        record.get("paper_id", ""),
        record.get("main_file", ""),
        record.get("recorded_at", ""),
        identity.get("file_sha256", ""),
        identity.get("doi", ""),
        identity.get("text_sha256", ""),
        identity.get("title_key", ""),
        identity.get("year", ""),
        identity.get("first_author_key", ""),
        json.dumps(identity.get("si_sha256", []) or [],
                   ensure_ascii=False),
    ))


def _identity_rows_to_records(conn) -> list:
    rows = conn.execute(
        "SELECT paper_id, main_file, recorded_at, file_sha256, doi, "
        "text_sha256, title_key, year, first_author_key, si_sha256_json "
        "FROM identities ORDER BY paper_id"
    ).fetchall()
    records = []
    for row in rows:
        try:
            si_hashes = json.loads(row[9] or "[]")
        except Exception:
            si_hashes = []
        records.append({
            "paper_id": row[0],
            "main_file": row[1] or "",
            "recorded_at": row[2] or "",
            "identity": {
                "file_sha256": row[3] or "",
                "doi": row[4] or "",
                "text_sha256": row[5] or "",
                "title_key": row[6] or "",
                "year": row[7] or "",
                "first_author_key": row[8] or "",
                "si_sha256": si_hashes,
            },
        })
    return records


def _sync_legacy_identity_json(project_dir: str, records: list):
    """保留旧版可读 JSON 镜像；SQLite 始终是新版权威索引。"""
    index_path = Path(project_dir) / "dedup_index.json"
    payload = {
        "version": IDENTITY_INDEX_VERSION,
        "storage": "sqlite-authoritative",
        "records": records,
    }
    tmp_path = index_path.with_name(
        index_path.name + "." + str(threading.get_ident()) + ".tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8")
    tmp_path.replace(index_path)


def _load_identity_index_records(project_dir: str) -> list:
    """
    读取 SQLite 权威索引，并自动吸收旧 dedup_index.json。

    JSON 继续作为旧版本兼容镜像，但不再承担并发写入职责。
    """
    with _IDENTITY_WRITE_LOCK:
        db_path = _init_identity_db(project_dir)
        conn = sqlite3.connect(str(db_path), timeout=30)
        legacy_path = Path(project_dir) / "dedup_index.json"
        if legacy_path.exists():
            try:
                payload = json.loads(
                    legacy_path.read_text(encoding="utf-8"))
                for record in payload.get("records", []) or []:
                    if record.get("paper_id"):
                        _upsert_identity_row(conn, record)
            except Exception:
                pass
        conn.commit()
        records = _identity_rows_to_records(conn)
        conn.close()
        _sync_legacy_identity_json(project_dir, records)
        return records


def _load_known_identities(project_dir: str,
                           current_db_path: str = None,
                           current_db_name: str = None) -> list:
    """优先汇总当前文献库，再加载历史索引和已完成文本缓存。"""
    project = Path(project_dir)
    records = []

    def _append_database_records(db_path: Path, source: str,
                                 current_library: bool):
        try:
            conn = sqlite3.connect(str(db_path))
            has_papers = conn.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='papers'"
            ).fetchone()
            if not has_papers:
                conn.close()
                return
            rows = conn.execute(
                "SELECT paper_id, data_json FROM papers"
            ).fetchall()
            conn.close()
            identities_by_base = {}
            for paper_id, data_json in rows:
                base_id = paper_id.rsplit("__", 1)[0]
                try:
                    data = json.loads(data_json)
                except Exception:
                    continue
                identity = _identity_from_database_data(data)
                merged = identities_by_base.setdefault(base_id, {
                    "file_sha256": "",
                    "doi": "",
                    "text_sha256": "",
                    "title_key": "",
                    "year": "",
                    "first_author_key": "",
                })
                for key, value in identity.items():
                    if value and not merged.get(key):
                        merged[key] = value
            for base_id, identity in identities_by_base.items():
                records.append({
                    "paper_id": base_id,
                    "main_file": "",
                    "source": source,
                    "current_library": current_library,
                    "identity": identity,
                })
        except Exception:
            try:
                conn.close()
            except Exception:
                pass

    current_path = None
    if current_db_path:
        current_path = Path(current_db_path)
        if current_path.exists():
            source_name = (
                "当前文献库：" + (current_db_name or current_path.stem))
            _append_database_records(
                current_path, source_name, current_library=True)

    indexed_records = _load_identity_index_records(project_dir)
    records_by_paper_id = {
        record.get("paper_id"): record for record in records
        if record.get("paper_id")
    }
    for indexed in indexed_records:
        paper_id = indexed.get("paper_id")
        current_record = records_by_paper_id.get(paper_id)
        if current_record:
            # 数据库身份缺少文件/SI 指纹时，用权威索引补齐，保留“当前库”来源。
            current_identity = current_record.setdefault("identity", {})
            for key, value in (indexed.get("identity") or {}).items():
                if value and not current_identity.get(key):
                    current_identity[key] = value
            continue
        indexed = dict(indexed)
        indexed.setdefault("source", "历史处理索引")
        indexed.setdefault("current_library", False)
        records.append(indexed)
        records_by_paper_id[paper_id] = indexed

    text_cache = project / "text_cache"
    if text_cache.exists():
        for paper_dir in text_cache.iterdir():
            if not paper_dir.is_dir():
                continue
            ai_cache_dir = project / "extract_cache" / paper_dir.name
            if (not ai_cache_dir.exists() or
                    not any(ai_cache_dir.glob("*.json"))):
                # 只有文本缓存、尚未成功产生 AI 结果，不算“已处理”。
                continue
            meta = {}
            meta_path = paper_dir / "meta.json"
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception:
                    meta = {}
            identity = meta.get("identity") or {}
            if not identity:
                main_text_path = paper_dir / "main.txt"
                if main_text_path.exists():
                    try:
                        identity = {
                            "file_sha256": "",
                            "doi": "",
                            "text_sha256": _text_fingerprint(
                                main_text_path.read_text(
                                    encoding="utf-8", errors="ignore")),
                            "title_key": "",
                            "year": "",
                            "first_author_key": "",
                        }
                    except Exception:
                        identity = {}
            if identity:
                records.append({
                    "paper_id": meta.get("paper_id", paper_dir.name),
                    "main_file": meta.get("main_file", ""),
                    "source": "text_cache",
                    "current_library": False,
                    "identity": identity,
                })

    # 未明确传入当前库时保留旧行为，供独立调用和兼容测试使用。
    database_dir = project / "database"
    if not current_path and database_dir.exists():
        for db_path in database_dir.glob("*.db"):
            _append_database_records(
                db_path, db_path.name, current_library=False)

    return records


def deduplicate_pairs(pairs: dict, project_dir: str,
                      log_fn=None, current_db_path: str = None,
                      current_db_name: str = None) -> tuple:
    """
    在正文/SI 配对完成后、文本提取和 AI 调用前去重。

    返回 (unique_pairs, duplicates, identities)：
      unique_pairs : 保留的正文/SI 配对
      duplicates   : 被剔除的配对及判重原因
      identities   : {main_path: identity}，供后续生成稳定 ID 和持久化
    """
    def _log(msg):
        if log_fn:
            log_fn(msg)

    known_records = _load_known_identities(
        project_dir,
        current_db_path=current_db_path,
        current_db_name=current_db_name)
    unique_pairs = {}
    duplicates = []
    identities = {}

    _log("========= 文献身份去重开始 =========")
    _log("   已加载历史身份记录 " + str(len(known_records)) + " 条")

    for main_path, si_paths in pairs.items():
        name = Path(main_path).name
        try:
            identity = build_document_identity(main_path)
            identity["si_sha256"] = sorted(
                get_file_sha256(str(path)) for path in si_paths)
        except Exception as e:
            _log("   ⚠ 无法生成身份，保留处理：" + name +
                 " (" + type(e).__name__ + ")")
            unique_pairs[main_path] = list(si_paths)
            continue

        identities[main_path] = identity
        matched = None
        changed_match = None
        reason = ""
        for record in known_records:
            candidate = record.get("identity") or {}
            reason = _identity_match_reason(identity, candidate)
            if reason == "__SI_CHANGED__":
                changed_match = changed_match or record
                reason = ""
                # 继续扫描：后面可能还有正文和 SI 都完全一致的记录。
                continue
            if reason:
                matched = record
                break

        if matched:
            duplicate = {
                "main_path": main_path,
                "si_paths": list(si_paths),
                "matched_paper_id": matched.get("paper_id", "历史文献"),
                "reason": reason,
                "source": matched.get("source", "历史身份记录"),
                "current_library": bool(
                    matched.get("current_library", False)),
            }
            duplicates.append(duplicate)
            _log("   ⏭ 剔除重复：" + name)
            _log("      已存在：" + duplicate["matched_paper_id"])
            _log("      来源：" + duplicate["source"])
            _log("      依据：" + reason)
            continue

        unique_pairs[main_path] = list(si_paths)
        if changed_match:
            identity["_dedup_state"] = "si_changed"
            identity["_matched_paper_id"] = changed_match.get(
                "paper_id", "历史文献")
            identity["_matched_current_library"] = bool(
                changed_match.get("current_library", False))
            _log(
                "   ↻ 正文已存在，但 SI 内容有变化，转为更新处理：" +
                name)
        else:
            identity["_dedup_state"] = "new"
            _log("   ✓ 新文献：" + name)
        known_records.append({
            "paper_id": "本批次：" + name,
            "main_file": name,
            "source": "current_batch",
            "identity": identity,
        })

    _log("========= 去重完成：保留 " + str(len(unique_pairs)) +
         " 篇，剔除 " + str(len(duplicates)) + " 篇 =========")
    return unique_pairs, duplicates, identities


def record_document_identity(project_dir: str, paper_id: str,
                             main_path: str, identity: dict):
    """将身份事务写入 SQLite，并刷新旧版兼容 JSON 镜像。"""
    if not identity:
        return
    new_record = {
        "paper_id": paper_id,
        "main_file": Path(main_path).name,
        "recorded_at": datetime.now().isoformat(),
        "identity": identity,
    }
    with _IDENTITY_WRITE_LOCK:
        db_path = _init_identity_db(project_dir)
        conn = sqlite3.connect(str(db_path), timeout=30)
        try:
            conn.execute("BEGIN IMMEDIATE")
            _upsert_identity_row(conn, new_record)
            conn.commit()
            records = _identity_rows_to_records(conn)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        _sync_legacy_identity_json(project_dir, records)

# ── 内容判断：SI 特征检测 ────────────────────────────────

# 强信号 SI 特征（出现在文档开头才算）
_SI_STRONG_PATTERNS = [
    # 文档标题级 SI 声明
    re.compile(
        r'(?:^|\n)\s*'
        r'(?:supporting\s+information'
        r'|supplementary\s+(?:material|information|data|note|file)s?'
        r'|supplemental\s+(?:material|information|data)s?'
        r'|electronic\s+supplementary\s+(?:information|material)s?)'
        r'\s*(?:\n|$)',
        re.IGNORECASE
    ),
    # 文档开头的 SI 声明语句
    re.compile(
        r'(?:this\s+(?:document|file|pdf)\s+(?:contains|provides|includes)'
        r'\s+(?:the\s+)?(?:supporting|supplementary)'
        r'|the\s+following\s+(?:supplementary|supporting)'
        r'|for\s+(?:the\s+)?(?:supporting|supplementary)\s+information'
        r'\s+(?:see|refer))',
        re.IGNORECASE
    ),
]

# 弱信号 SI 特征（仅在缺乏 MS 特征时才作为依据）
_SI_WEAK_PATTERNS = [
    # SI 专属图表/节编号（正文里也常引用，所以是弱信号）
    re.compile(
        r'(?:figure|fig\.|table|tbl\.|scheme|section|note|equation|eq\.)'
        r'\s+s\d{1,3}',
        re.IGNORECASE
    ),
    # 页眉/页脚 SI 标记（S1 of 12 等）
    re.compile(
        r'(?:^|\n)\s*s[-\s]?\d{1,3}\s+(?:of\s+\d+|page|\|)',
        re.IGNORECASE
    ),
]

_MS_CONTENT_PATTERNS = [
    # 摘要段落
    re.compile(r'(?:^|\n)\s*abstract\s*\n', re.IGNORECASE),
    # 引言
    re.compile(r'(?:^|\n)\s*(?:1[\.\s]+)?introduction\s*\n', re.IGNORECASE),
    # DOI 行
    re.compile(r'\bdoi\s*:\s*10\.\d{4,}/', re.IGNORECASE),
    # 收稿/接收日期
    re.compile(
        r'received\s*:\s*\w+\s+\d{1,2},?\s+\d{4}'
        r'|accepted\s*:\s*\w+\s+\d{1,2},?\s+\d{4}',
        re.IGNORECASE
    ),
    # 作者机构关键词（正文特有）
    re.compile(
        r'(?:correspondence\s+to|corresponding\s+author|e-?mail\s*:)',
        re.IGNORECASE
    ),
    # 关键词字段
    re.compile(r'(?:^|\n)\s*keywords\s*[:：]', re.IGNORECASE),
]

# SI 强信号只在前 1500 字符内查找（标题区域）
_SI_HEAD_WINDOW = 1500


def _score_si_from_content(filepath: str) -> tuple:
    """
    返回 (si_strong, si_weak, ms_hits, text_ok)
      si_strong : 文档开头出现的强 SI 信号数（标题、声明）
      si_weak   : 全文出现的弱 SI 信号数（FigS1 引用等）
      ms_hits   : MS 特征命中数
      text_ok   : 文本是否可读
    """
    text = _quick_read_text(filepath)
    if not text or len(text.strip()) < 100:
        return 0, 0, 0, False

    head = text[:_SI_HEAD_WINDOW]
    si_strong = sum(1 for p in _SI_STRONG_PATTERNS if p.search(head))
    si_weak = sum(1 for p in _SI_WEAK_PATTERNS if p.search(text))
    ms_hits = sum(1 for p in _MS_CONTENT_PATTERNS if p.search(text))
    return si_strong, si_weak, ms_hits, True


def _detect_si_from_content(filepath: str) -> str:
    """
    基于强弱信号的三态判断。

    判定规则（优先级从高到低）：
      1. 强 SI 信号 ≥ 1 → 直接判为 SI（标题就写着 Supporting Information）
      2. MS 信号 ≥ 2 → 判为 MS（Abstract+Introduction+DOI 等多重确认）
      3. 弱 SI 信号 ≥ 3 且 MS 信号 == 0 → 判为 SI（大量 FigS1 引用且无正文特征）
      4. MS 信号 ≥ 1 且弱 SI 信号 == 0 → 判为 MS
      5. 其他 → unknown，交由文件名兜底
    """
    si_strong, si_weak, ms_hits, text_ok = _score_si_from_content(filepath)
    if not text_ok:
        return "unknown"
    if si_strong >= 1:
        return "si"
    if ms_hits >= 2:
        return "ms"
    if si_weak >= 3 and ms_hits == 0:
        return "si"
    if ms_hits >= 1 and si_weak == 0:
        return "ms"
    return "unknown"


def _is_si_by_filename(stem: str) -> bool:
    """
    文件名兜底判断（仅在内容判断失败时使用）。
    """
    s = _strip_leading_number(stem).lower()
    s_norm = re.sub(r'[\s_\-\.]+', ' ', s).strip()

    if re.fullmatch(
        r'(si|esi|s\d{1,2}|supp'
        r'|supplementary|supporting'
        r'|supporting info(?:rmation)?'
        r'|supplementary (?:material|information|data|file|note)s?'
        r'|supplemental (?:material|information|data|file|note)s?)',
        s_norm
    ):
        return True
    if re.search(r'(?:^|[\s_\-\.])(?:si|esi)(?:[\s_\-\.]|$)', s):
        return True
    if re.search(r'(?:^|[\s_\-\.])s\d{1,2}(?:[\s_\-\.]|$)', s):
        return True
    if re.search(r'support(?:ing)?|supplement(?:ary|al)?', s):
        return True
    return False

def _quick_read_text(filepath: str, max_pages: int = 4,
                     max_chars: int = 6000) -> str:
    """
    快速读取文件前几页文本，仅用于 SI/MS 分类判断。
    不提取表格，不做 OCR，失败静默返回空字符串。
    """
    ext = Path(filepath).suffix.lower()
    try:
        if ext == ".pdf":
            import fitz
            doc = fitz.open(filepath)
            pages = []
            for i in range(min(max_pages, doc.page_count)):
                t = doc[i].get_text()
                if t.strip():
                    pages.append(t)
            doc.close()
            return NL.join(pages)[:max_chars]
        elif ext in (".docx", ".doc"):
            from docx import Document
            doc = Document(filepath)
            paras = [p.text for p in doc.paragraphs[:40] if p.text.strip()]
            return NL.join(paras)[:max_chars]
    except Exception:
        pass
    return ""

# ── 文本提取 ────────────────────────────────────────────

def extract_pdf_text(pdf_path: str, performance_options: dict = None) -> tuple:
    """提取 PDF 文本和表格，返回 (text, tables_list)"""
    import fitz
    import pdfplumber

    doc = fitz.open(pdf_path)
    pages_text = []
    for i, page in enumerate(doc):
        t = page.get_text()
        if t.strip():
            pages_text.append("[PAGE " + str(i + 1) + "]" + NL + t)
    doc.close()
    full_text = NL.join(pages_text)

    if len(full_text.strip()) < 300:
        full_text = ocr_pdf(pdf_path, performance_options)

    tables = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages):
                for j, table in enumerate(page.extract_tables() or []):
                    if table:
                        tables.append({
                            "page": i + 1,
                            "index": j,
                            "data": table
                        })
    except Exception:
        pass

    return full_text, tables


def extract_word_text(docx_path: str) -> str:
    """提取 Word 文档文本"""
    from docx import Document
    doc = Document(docx_path)
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return NL.join(paragraphs)


def document_requires_ocr(filepaths: list, sample_pages: int = 3) -> bool:
    """Quickly identify a pair that contains a scanned/image-only PDF."""
    import fitz

    for filepath in filepaths:
        if Path(filepath).suffix.lower() != ".pdf":
            continue
        try:
            doc = fitz.open(filepath)
            try:
                text = "".join(
                    doc[index].get_text()
                    for index in range(min(sample_pages, len(doc))))
            finally:
                doc.close()
            if len(text.strip()) < 120:
                return True
        except Exception:
            # Let the real extraction surface the error; do not misroute it.
            continue
    return False


def ocr_pdf(pdf_path: str, performance_options: dict = None) -> str:
    """OCR scanned PDFs, preferring DirectML and falling back to CPU."""
    options = performance_options or {}
    prefer_gpu = bool(options.get("gpu_ocr_ready", False))
    dpi = max(120, min(300, int(options.get("ocr_dpi", 190))))
    try:
        import fitz
        from gpu_acceleration import run_ocr

        doc = fitz.open(pdf_path)
        results = []
        providers = set()
        try:
            for page_number, page in enumerate(doc, 1):
                pix = page.get_pixmap(dpi=dpi, alpha=False)
                lines, provider = run_ocr(
                    pix.tobytes("png"), prefer_gpu=prefer_gpu)
                providers.add(provider)
                if lines:
                    results.append("[PAGE " + str(page_number) + "]" +
                                   NL + " ".join(lines))
        finally:
            doc.close()
        provider_label = ("GPU_DIRECTML" if "gpu_directml" in providers
                          else "CPU")
        return "[OCR:" + provider_label + "]" + NL + NL.join(results)
    except ModuleNotFoundError:
        return "[OCR 不可用：请安装 rapidocr 和 onnxruntime-directml]"
    except Exception as exc:
        return "[OCR 处理失败：" + str(exc) + "]"


def extract_text_from_file(filepath: str,
                           performance_options: dict = None) -> tuple:
    """根据文件类型自动选择提取方法，统一返回 (text, tables)"""
    ext = Path(filepath).suffix.lower()
    if ext == ".pdf":
        return extract_pdf_text(filepath, performance_options)
    elif ext in (".docx", ".doc"):
        return extract_word_text(filepath), []
    else:
        return "[不支持的文件格式: " + ext + "]", []


# ── 文本合并 ────────────────────────────────────────────

def build_merged_text(main_text: str, si_texts: list,
                      max_chars: int = 80000) -> str:
    """合并正文和 SI 文本，加结构标记，超长时按比例截断。"""
    parts = ["[SECTION: MAIN_TEXT]" + NL + main_text]
    for i, si in enumerate(si_texts):
        parts.append("[SECTION: SUPPLEMENTARY_INFO_" + str(i + 1) +
                     "]" + NL + si)

    sep = NL + NL + ("=" * 60) + NL + NL
    merged = sep.join(parts)

    if len(merged) > max_chars:
        main_budget = int(max_chars * 0.8)
        si_budget = int(max_chars * 0.2)
        truncated_main = main_text[:main_budget]
        truncated_si = si_texts[0][:si_budget] if si_texts else ""

        merged = "[SECTION: MAIN_TEXT]" + NL + truncated_main
        if truncated_si:
            merged += (sep +
                       "[SECTION: SUPPLEMENTARY_INFO_1]" + NL +
                       truncated_si)
        merged += NL + NL + "[注：文本因超长已截断]"

    return merged

def match_si_files(file_list: list, log_fn=None) -> tuple:
    """
    内容优先的 SI 匹配。

    返回值：(pairs, orphan_sis)
      pairs      : dict {main_path: [si_path, ...]}，只含正文文件作为 key
      orphan_sis : list，L4 三层都没匹配上的孤立 SI 路径列表

    Step 1：_score_si_from_content() 读取前 4 页文本检测 SI/MS 特征
    Step 2：内容判断不确定时，回退到 _is_si_by_filename()
    Step 3：有 SI 但无 MS 时，把 SI 特征最弱的文件强制降级为 MS（兜底）

    配对：L1 精确 → L2 包含 → L3 模糊 → L4 孤立（返回给调用方处理）
    """
    if not file_list:
        return {}, []

    def _log(msg):
        if log_fn:
            log_fn(msg)

    total = len(file_list)
    _log("========= SI/MS 分类开始，共 " + str(total) + " 个文件 =========")

    # 记录每个文件的分类结果和原始分数
    # 结构：[(filepath, label, si_hits, ms_hits), ...]
    classified = []

    for idx, f in enumerate(file_list, 1):
        name = Path(f).name
        _log("[" + str(idx) + "/" + str(total) + "] 分析: " + name)

        si_strong, si_weak, ms_hits, text_ok = _score_si_from_content(f)

        if not text_ok:
            label = "unknown_content"
        elif si_strong >= 1:
            label = "si"
        elif ms_hits >= 2:
            label = "ms"
        elif si_weak >= 3 and ms_hits == 0:
            label = "si"
        elif ms_hits >= 1 and si_weak == 0:
            label = "ms"
        else:
            label = "unknown_content"

        if label == "unknown_content":
            if _is_si_by_filename(Path(f).stem):
                label = "si_filename"
            else:
                label = "ms_filename"

        classified.append((f, label, si_strong, si_weak, ms_hits))

        if label == "si":
            _log("   → 内容判断: SI ✓  (strong=" + str(si_strong)
                 + " weak=" + str(si_weak) + " ms=" + str(ms_hits) + ")")
        elif label == "ms":
            _log("   → 内容判断: 正文 ✓  (strong=" + str(si_strong)
                 + " weak=" + str(si_weak) + " ms=" + str(ms_hits) + ")")
        elif label == "si_filename":
            _log("   → 内容不确定，文件名判断: SI")
        else:
            _log("   → 内容不确定，文件名判断: 正文")

    mains = [f for f, lb, *_ in classified
             if lb in ("ms", "ms_filename")]
    sis = [f for f, lb, *_ in classified
           if lb in ("si", "si_filename")]

    # ── 兜底：有 SI 但没有 MS ──────────────────────────────
    # ── 兜底：有 SI 但没有 MS ──────────────────────────────
    if sis and not mains:
        _log("   ⚠ 未检测到正文文件，启动兜底裁决...")
        si_entries = [(f, lb, ss, sw, mh)
                      for f, lb, ss, sw, mh in classified
                      if lb in ("si", "si_filename")]
        # SI 总分 = strong*3 + weak，MS 分 = mh*2
        # 兜底降级时优先选 (si_total - ms_total) 最小的，即最不像 SI 的
        si_entries.sort(key=lambda x: (x[2] * 3 + x[3]) - x[4] * 2)
        demoted = si_entries[0][0]
        mains.append(demoted)
        sis.remove(demoted)
        _log("   → 强制降级为正文: " + Path(demoted).name
             + " (该文件 SI 特征最弱)")

    _log("--- 分类结果: 正文 " + str(len(mains))
         + " 篇 / SI " + str(len(sis)) + " 个 ---")

    if not sis:
        _log("   无 SI 文件，跳过配对")
        return {m: [] for m in mains}, []

    if len(mains) == 1:
        _log("   单篇正文，所有 SI 直接归入: " + Path(mains[0]).name)
        return {mains[0]: sis}, []

    pairs = {m: [] for m in mains}
    unmatched_sis = list(sis)

    # --- L1: 归一化后完全一致 ---
    still_unmatched = []
    for si in unmatched_sis:
        si_norm = _normalize_stem(Path(si).stem)
        matched = None
        for main in mains:
            if si_norm == _normalize_stem(Path(main).stem):
                matched = main
                break
        if matched:
            pairs[matched].append(si)
            _log("   L1 精确匹配: " + Path(si).name
                 + " → " + Path(matched).name)
        else:
            still_unmatched.append(si)
    unmatched_sis = still_unmatched

    # --- L2: 归一化后一方包含另一方 ---
    still_unmatched = []
    for si in unmatched_sis:
        si_norm = _normalize_stem(Path(si).stem)
        best_main = None
        best_overlap = 0
        for main in mains:
            main_norm = _normalize_stem(Path(main).stem)
            if si_norm in main_norm or main_norm in si_norm:
                overlap = (len(si_norm) if si_norm in main_norm
                           else len(main_norm))
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_main = main
        if best_main and best_overlap >= 4:
            pairs[best_main].append(si)
            _log("   L2 包含匹配: " + Path(si).name
                 + " → " + Path(best_main).name)
        else:
            still_unmatched.append(si)
    unmatched_sis = still_unmatched

    # --- L3: 编辑距离模糊匹配 ---
    still_unmatched = []
    for si in unmatched_sis:
        si_norm = _normalize_stem(Path(si).stem)
        scores = sorted(
            [(_edit_distance_ratio(si_norm,
                                   _normalize_stem(Path(m).stem)), m)
             for m in mains],
            reverse=True
        )
        best_score = scores[0][0] if scores else 0.0
        second_score = scores[1][0] if len(scores) > 1 else 0.0

        if best_score > 0.72 and (best_score - second_score) > 0.10:
            best_main = scores[0][1]
            pairs[best_main].append(si)
            _log("   L3 模糊匹配: " + Path(si).name
                 + " → " + Path(best_main).name
                 + " (相似度 " + str(round(best_score, 2)) + ")")
        else:
            still_unmatched.append(si)
    unmatched_sis = still_unmatched

    # --- L4: 三层都没匹配上 → 收集为孤立 SI，返回给调用方 ---
    orphan_sis = list(unmatched_sis)
    for si in orphan_sis:
        _log("   ⚠ L4 未匹配: " + Path(si).name
             + " → 将在配对弹窗单独列出")

    # 最终配对汇总
    _log("========= 配对完成 =========")
    for main, si_list in pairs.items():
        if si_list:
            _log("   ✓ " + Path(main).name
                 + " + [" + ", ".join(Path(s).name for s in si_list) + "]")
        else:
            _log("   — " + Path(main).name + " (无 SI)")
    if orphan_sis:
        _log("   孤立 SI（需手动配对）: "
             + ", ".join(Path(s).name for s in orphan_sis))

    return pairs, orphan_sis


# ── 文献 ID 生成 ────────────────────────────────────────

def _guess_journal_from_filename(path: str) -> str:
    """从文件名猜测期刊缩写"""
    stem = Path(path).stem.lower()
    stem = _strip_leading_number(stem)
    normalized = stem.replace(" ", "").replace("-", "").replace("_", "")
    journal_map = {
        "acsnano": "ACSNano",
        "advancedmaterials": "AdvMater",
        "advmater": "AdvMater",
        "advmat": "AdvMater",
        "advancedenergy": "AdvEnergyMater",
        "advancedfunctional": "AdvFunctMater",
        "advancedscience": "AdvSci",
        "angewchem": "AngewChem",
        "angew": "AngewChem",
        "jacs": "JACS",
        "natcommun": "NatCommun",
        "naturecommun": "NatCommun",
        "natenergy": "NatEnergy",
        "nature": "Nature",
        "science": "Science",
        "carbon": "Carbon",
        "small": "Small",
        "chemsci": "ChemSci",
        "chemmater": "ChemMater",
        "aem": "AdvEnergyMater",
        "joule": "Joule",
        "energystorage": "EnergyStorageMater",
        "nanoletters": "NanoLett",
        "nanoenergy": "NanoEnergy",
        "appliedcatalysis": "ApplCatal",
        "thermal": "ThermochimActa",
    }
    for key in sorted(journal_map.keys(), key=len, reverse=True):
        if key in normalized:
            return journal_map[key]
    return ""


def generate_paper_id(main_path: str, doi: str = None,
                      identity: dict = None) -> str:
    """
    生成规范的文献 ID。
    优先级：DOI → PDF 元数据 → 文件名解析 → MD5 兜底。
    无 DOI 时附加正文指纹短码，避免“同一作者同一年”发生 ID 撞车。
    """
    import fitz

    normalized_doi = _normalize_doi(doi)
    if normalized_doi:
        safe_doi = re.sub(r'[^a-zA-Z0-9._-]+', '_', normalized_doi)
        return ("doi_" + safe_doi).strip('_')[:90]

    fingerprint_suffix = ""
    if identity and identity.get("text_sha256"):
        fingerprint_suffix = "_" + identity["text_sha256"][:8]

    if str(main_path).lower().endswith(".pdf"):
        try:
            doc = fitz.open(main_path)
            meta = doc.metadata or {}
            first_page = doc[0].get_text() if doc.page_count > 0 else ""
            doc.close()

            author = meta.get("author", "") or ""
            first_author = ""
            if author:
                first_author = author.split(";")[0].split(",")[0].strip()
                if first_author:
                    first_author = first_author.split()[-1]
                    first_author = re.sub(r'[^\w]', '', first_author)[:15]

            year_match = re.search(r'\b(20\d{2}|19\d{2})\b', first_page)
            year = year_match.group() if year_match else ""

            if first_author and year:
                journal = _guess_journal_from_filename(str(main_path))
                parts = [first_author, year]
                if journal:
                    parts.append(journal)
                return "_".join(parts) + fingerprint_suffix
        except Exception:
            pass

    stem = Path(main_path).stem
    stem = _strip_leading_number(stem)
    stem = re.sub(r'[_\-\s]?(SI|supplementary|supporting|ESI)',
                  '', stem, flags=re.IGNORECASE)
    year_match = re.search(r'(20\d{2}|19\d{2})', stem)
    year = year_match.group() if year_match else ""

    clean = re.sub(r'[<>:"/\\|?*\s]', '_', stem)
    clean = re.sub(r'_+', '_', clean).strip('_')

    if year and len(clean) < 50:
        return clean[:50] + fingerprint_suffix

    md5 = get_file_md5(main_path)[:8]
    return (clean[:30] + "_" + md5) if clean else md5


# ── 第一层缓存处理 ──────────────────────────────────────

def process_to_text_cache(main_path: str, si_paths: list,
                          cache_dir: str, paper_id: str,
                          max_chars: int = 80000,
                          identity: dict = None,
                          performance_options: dict = None) -> dict:
    """
    处理文件到第一层文本缓存，返回 meta 信息。
    生成：main.txt / SI_N.txt / tables.json / merged.txt / meta.json
    """
    dest = Path(cache_dir) / paper_id
    dest.mkdir(parents=True, exist_ok=True)

    main_text, main_tables = extract_text_from_file(
        main_path, performance_options)
    (dest / "main.txt").write_text(main_text, encoding="utf-8")

    all_tables = list(main_tables)
    si_texts = []

    for i, si_path in enumerate(si_paths):
        si_text, si_tables = extract_text_from_file(
            si_path, performance_options)
        (dest / ("SI_" + str(i + 1) + ".txt")).write_text(
            si_text, encoding="utf-8")
        si_texts.append(si_text)
        all_tables.extend(si_tables)

    with open(dest / "tables.json", "w", encoding="utf-8") as f:
        json.dump(all_tables, f, ensure_ascii=False, indent=2)

    merged = build_merged_text(main_text, si_texts, max_chars)
    (dest / "merged.txt").write_text(merged, encoding="utf-8")

    meta = {
        "paper_id": paper_id,
        "extracted_at": datetime.now().isoformat(),
        "main_file": Path(main_path).name,
        "si_files": [Path(p).name for p in si_paths],
        "main_chars": len(main_text),
        "si_chars": [len(t) for t in si_texts],
        "merged_chars": len(merged),
        "tables_found": len(all_tables),
        "ocr_used": ("[OCR" in main_text
                     or any("[OCR" in t for t in si_texts)),
        "text_cache_version": "1.1",
        "performance": dict(performance_options or {}),
        "identity": identity or {},
    }
    with open(dest / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    return meta


def export_tables_preview(tables: list, out_path: str,
                          max_tables: int = 10) -> None:
    """调试用：把抽出的表格转成 Markdown 文件预览。"""
    from ai_client import format_tables_markdown
    md = format_tables_markdown(tables, max_tables=max_tables)
    Path(out_path).write_text(md or "(no tables)", encoding="utf-8")

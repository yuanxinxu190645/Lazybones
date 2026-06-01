# F:\MOFCNM\src\extractor.py

import re
import json
import hashlib
from pathlib import Path
from datetime import datetime

NL = chr(10)


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

def extract_pdf_text(pdf_path: str) -> tuple:
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
        full_text = ocr_pdf(pdf_path)

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


def ocr_pdf(pdf_path: str) -> str:
    """OCR 处理扫描版 PDF（需要安装 rapidocr-onnxruntime）"""
    try:
        import importlib
        rapidocr = importlib.import_module("rapidocr_onnxruntime")
        RapidOCR = rapidocr.RapidOCR

        import fitz
        ocr = RapidOCR()
        doc = fitz.open(pdf_path)
        results = []
        for page in doc:
            pix = page.get_pixmap(dpi=200)
            img_bytes = pix.tobytes("png")
            result, _ = ocr(img_bytes)
            if result:
                results.append(" ".join([r[1] for r in result]))
        doc.close()
        return NL.join(results)

    except ModuleNotFoundError:
        return ("[OCR 不可用：如需处理扫描版 PDF，请运行 "
                "pip install rapidocr-onnxruntime]")
    except Exception as e:
        return "[OCR 处理失败：" + str(e) + "]"


def extract_text_from_file(filepath: str) -> tuple:
    """根据文件类型自动选择提取方法，统一返回 (text, tables)"""
    ext = Path(filepath).suffix.lower()
    if ext == ".pdf":
        return extract_pdf_text(filepath)
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


def generate_paper_id(main_path: str, doi: str = None) -> str:
    """
    生成规范的文献 ID。
    优先级：PDF 元数据 → 文件名解析 → MD5 兜底
    """
    import fitz

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
                return "_".join(parts)
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
        return clean[:50]

    md5 = get_file_md5(main_path)[:8]
    return (clean[:30] + "_" + md5) if clean else md5


# ── 第一层缓存处理 ──────────────────────────────────────

def process_to_text_cache(main_path: str, si_paths: list,
                          cache_dir: str, paper_id: str,
                          max_chars: int = 80000) -> dict:
    """
    处理文件到第一层文本缓存，返回 meta 信息。
    生成：main.txt / SI_N.txt / tables.json / merged.txt / meta.json
    """
    dest = Path(cache_dir) / paper_id
    dest.mkdir(parents=True, exist_ok=True)

    main_text, main_tables = extract_text_from_file(main_path)
    (dest / "main.txt").write_text(main_text, encoding="utf-8")

    all_tables = list(main_tables)
    si_texts = []

    for i, si_path in enumerate(si_paths):
        si_text, si_tables = extract_text_from_file(si_path)
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
        "text_cache_version": "1.0",
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

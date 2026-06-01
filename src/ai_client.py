# F:\MOFCNM\src\ai_client.py

import json
import re
import time
import logging
from openai import OpenAI

try:
    # openai >= 1.0
    from openai import (APIError, APIConnectionError, APITimeoutError,
                        RateLimitError)
except Exception:
    APIError = Exception
    APIConnectionError = Exception
    APITimeoutError = Exception
    RateLimitError = Exception


logger = logging.getLogger(__name__)

# 模块级换行常量,避免源码里出现裸字面量被传输链路吃掉
NL = chr(10)


# ── 服务商配置 ──────────────────────────────────────────
# context_window 字段用于 token 预算预警;为 0 表示未知

PROVIDERS = {
    "DeepSeek": {
        "base_url": "https://api.deepseek.com",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "context_window": 64000,
    },
    "阿里通义千问": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen-long", "qwen-plus", "qwen-max"],
        "context_window": 128000,
    },
    "Kimi (Moonshot)": {
        "base_url": "https://api.moonshot.cn/v1",
        "models": ["moonshot-v1-128k", "moonshot-v1-32k"],
        "context_window": 128000,
    },
    "智谱 GLM": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-4-long", "glm-4-plus"],
        "context_window": 128000,
    },
    "字节豆包": {
        "base_url": "https://ark.volces.com/api/v3",
        "models": ["doubao-pro-128k", "doubao-lite-128k"],
        "context_window": 128000,
    },
    "自定义": {
        "base_url": "",
        "models": [],
        "context_window": 0,
    },
}


# ── 基础工具 ────────────────────────────────────────────

def get_client(base_url: str, api_key: str,
               timeout: float = 600.0) -> OpenAI:
    """
    统一构造 OpenAI 客户端。
    timeout=600 是为了适配 DeepSeek-R1 长思考链,
    并显式禁用 SDK 内部重试(外层自己做重试+续写)。
    """
    try:
        import httpx
        http_client = httpx.Client(
            timeout=httpx.Timeout(
                connect=30.0,
                read=timeout,
                write=30.0,
                pool=30.0,
            ),
            limits=httpx.Limits(
                max_keepalive_connections=5,
                max_connections=10,
                keepalive_expiry=60.0,
            ),
            trust_env=True,
        )
        return OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=0,
            http_client=http_client,
        )
    except Exception:
        # httpx 配置失败时降级,保持原行为可用
        return OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=0,
        )


def estimate_tokens(text: str) -> int:
    """
    简单的 token 估算:
      中文字符(含日韩)按 1.5 字符 / token
      其余按 4 字符 / token
    粗估即可,用于预警与截断。
    """
    if not text:
        return 0
    cjk = 0
    other = 0
    for ch in text:
        if "\u3000" <= ch <= "\u9fff" or "\uff00" <= ch <= "\uffef":
            cjk += 1
        else:
            other += 1
    return int(cjk / 1.5 + other / 4) + 1


def truncate_to_token_budget(text: str, budget_tokens: int) -> str:
    """
    估算后超出预算时,按 char 比例截断,从中间砍。
    保留首尾,因为论文的标题/摘要和结论通常都很重要。
    """
    if budget_tokens <= 0 or estimate_tokens(text) <= budget_tokens:
        return text

    char_budget = int(budget_tokens * 3.0 * 0.75)
    if len(text) <= char_budget:
        return text

    head_len = int(char_budget * 0.65)
    tail_len = char_budget - head_len
    head = text[:head_len]
    tail = text[-tail_len:]
    return (head + NL +
            "[...内容因超出上下文已从中间截断...]" + NL +
            tail)


# ── 表格 → Markdown ────────────────────────────────────

def format_tables_markdown(tables: list,
                           max_tables: int = 15,
                           max_rows_per_table: int = 30) -> str:
    """
    把 pdfplumber 抽出来的表格转成 AI 友好的 Markdown,
    带表头元信息(页码、列名),便于 AI 理解上下文。
    """
    if not tables:
        return ""

    out_lines = ["", "[TABLES FROM PAPER]"]
    used = 0
    for idx, t in enumerate(tables):
        if used >= max_tables:
            out_lines.append(
                "[已忽略 " + str(len(tables) - used) +
                " 张额外表格,未展示]")
            break
        rows = t.get("data") or []
        if not rows:
            continue

        page = t.get("page", "?")
        # 表头:尝试用第一行;若全为 None 则跳过
        header = [(c or "").strip().replace(NL, " ") for c in rows[0]]
        if not any(header):
            ncol = max(len(r) for r in rows)
            header = ["列" + str(i + 1) for i in range(ncol)]
            body_rows = rows
        else:
            body_rows = rows[1:]

        body_rows = body_rows[:max_rows_per_table]

        # 标题元信息
        out_lines.append(
            "[Table " + str(idx + 1) + ", page " + str(page) +
            ", 列名: " + " | ".join(header) + "]")

        # 真正的 Markdown 表
        out_lines.append("| " + " | ".join(header) + " |")
        out_lines.append("|" + "|".join(["---"] * len(header)) + "|")
        for r in body_rows:
            cells = [(str(c or "").strip()
                      .replace("|", "/")
                      .replace(NL, " "))
                     for c in r]
            if len(cells) < len(header):
                cells += [""] * (len(header) - len(cells))
            elif len(cells) > len(header):
                cells = cells[:len(header)]
            out_lines.append("| " + " | ".join(cells) + " |")

        if len(rows) - 1 > max_rows_per_table:
            out_lines.append(
                "_(该表共 " + str(len(rows) - 1) + " 行,仅展示前 " +
                str(max_rows_per_table) + " 行)_")

        used += 1

    return NL.join(out_lines)


# ── 连接测试 ────────────────────────────────────────────

def test_connection(base_url: str, api_key: str, model: str) -> tuple:
    """测试 API 连接,返回 (success, message)"""
    try:
        client = get_client(base_url, api_key, timeout=30.0)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "reply 'ok' only"}],
            max_tokens=5,
            temperature=0,
        )
        reply = resp.choices[0].message.content
        return True, "连接成功,模型回复:" + str(reply)
    except Exception as e:
        return False, "连接失败:" + str(e)


# ── 带重试的 chat completion 调用 ───────────────────────

_RETRY_EXC = (RateLimitError, APITimeoutError, APIConnectionError)


def _call_with_retry(client, *,
                     model: str,
                     messages: list,
                     response_format=None,
                     temperature: float = 0,
                     max_retries: int = 3,
                     backoff_base: float = 2.0,
                     log_fn=None):
    """
    指数退避重试包装:2s、5s、10s。
    仅在限流 / 超时 / 连接错误时重试,其他异常直接抛出。
    log_fn(msg) 可选回调,用于把重试信息写到 UI 日志。
    """
    kwargs = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if response_format is not None:
        kwargs["response_format"] = response_format

    last_exc = None
    for attempt in range(max_retries):
        try:
            return client.chat.completions.create(**kwargs)
        except _RETRY_EXC as e:
            last_exc = e
            if attempt == max_retries - 1:
                break
            wait = backoff_base ** attempt + 1.0
            msg = ("⚠ 调用失败(" + type(e).__name__ + "),"
                   + str(int(wait)) + "s 后重试 (" +
                   str(attempt + 1) + "/" + str(max_retries) + ")")
            if log_fn:
                log_fn(msg)
            else:
                logger.warning(msg)
            time.sleep(wait)
        except APIError:
            raise
    raise last_exc


# ── 抽取主函数 ──────────────────────────────────────────

def extract_paper(merged_text: str,
                  tables: list,
                  system_prompt: str,
                  base_url: str,
                  api_key: str,
                  model: str,
                  lang: str = "zh",
                  *,
                  timeout: float = 600.0,
                  max_retries: int = 3,
                  max_input_tokens: int = 100000,
                  log_fn=None) -> dict:
    """
    调用 AI 抽取单篇文献信息。每次调用都是全新对话,零污染。
    """
    client = get_client(base_url, api_key, timeout=timeout)

    # 1) 表格转 Markdown
    table_str = format_tables_markdown(tables)

    # 2) 构造用户内容(表格段前后加换行,避免和正文糊在一起)
    if table_str:
        user_content = merged_text + NL + NL + table_str
    else:
        user_content = merged_text

    # 3) Token 预算检查与截断
    sys_tokens = estimate_tokens(system_prompt)
    user_tokens = estimate_tokens(user_content)
    total_in = sys_tokens + user_tokens
    if log_fn:
        log_fn("   tokens估算: prompt≈" + str(sys_tokens) +
               ", input≈" + str(user_tokens) +
               ", 总计≈" + str(total_in))

    if total_in > max_input_tokens:
        budget = max_input_tokens - sys_tokens - 500
        if log_fn:
            log_fn("   ⚠ 超出预算 " + str(max_input_tokens) +
                   " tokens,将输入截断到 " + str(budget) + " tokens")
        user_content = truncate_to_token_budget(user_content, budget)

    # 4) 调用模型(带重试)
    response = _call_with_retry(
        client,
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_content},
        ],
        response_format={"type": "json_object"},
        temperature=0,
        max_retries=max_retries,
        log_fn=log_fn,
    )

    raw = response.choices[0].message.content
    tokens_used = getattr(response, "usage", None)
    tokens_total = (tokens_used.total_tokens
                    if tokens_used is not None else 0)

    result = safe_parse_json(raw)
    result = _normalize_empty_values(result)
    result["_tokens_used"] = tokens_total
    return result


# ── JSON 容错解析 ───────────────────────────────────────

def safe_parse_json(raw: str) -> dict:
    """安全解析 JSON,容错处理"""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    m = re.search(r'```(?:json)?\s*([\s\S]+?)\s*```', raw)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    m = re.search(r'\{[\s\S]+\}', raw)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass

    return {"_raw_output": raw, "_parse_error": True}


def quick_ask(prompt: str,
              base_url: str,
              api_key: str,
              model: str,
              *,
              system: str = "你是一个简洁的科研助手。",
              timeout: float = 120.0,
              max_retries: int = 2,
              temperature: float = 0.2) -> str:
    """
    轻量一次性问答(不走 JSON mode),返回纯文本。
    用于"字段问答"等不需要结构化输出的场景。
    """
    client = get_client(base_url, api_key, timeout=timeout)
    resp = _call_with_retry(
        client,
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": prompt},
        ],
        temperature=temperature,
        max_retries=max_retries,
    )
    return resp.choices[0].message.content or ""

def _normalize_empty_values(data: dict) -> dict:
    """
    把 AI 返回里的所有"空值变体"统一为字符串 "N/A"。
    覆盖：null/None、空字符串、空数组 []、["N/A"]、[""]、[None]、
          [{"value":"N/A",...}] 这种只有一个 N/A 对象的退化数组。

    只处理顶层字段，不动嵌套结构里的真实数据。
    元数据字段（_ 开头）原样保留。
    """
    if not isinstance(data, dict):
        return data

    def _is_na_value(v):
        """判断单个值是否等价于 N/A"""
        if v is None:
            return True
        if isinstance(v, str):
            return v.strip() in ("", "N/A", "n/a", "None", "null", "NA")
        if isinstance(v, dict):
            inner = v.get("value", "")
            return _is_na_value(inner)
        return False

    cleaned = {}
    for key, val in data.items():
        # 元数据字段不动
        if key.startswith("_"):
            cleaned[key] = val
            continue

        # 顶层 None / 空字符串 / "None" 等
        if _is_na_value(val):
            cleaned[key] = "N/A"
            continue

        # 列表类型：判断是不是"全员 N/A"的伪空列表
        if isinstance(val, list):
            if len(val) == 0:
                cleaned[key] = "N/A"
                continue
            # 所有元素都是 N/A 变体 → 整个字段算空
            if all(_is_na_value(item) for item in val):
                cleaned[key] = "N/A"
                continue
            # 真实有值的 list：保留，但过滤掉里面零星的 N/A 元素
            filtered = [item for item in val if not _is_na_value(item)]
            cleaned[key] = filtered if filtered else "N/A"
            continue

        # 其它情况（真实字符串/数字/dict）原样保留
        cleaned[key] = val

    return cleaned

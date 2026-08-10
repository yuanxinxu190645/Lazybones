"""Lazy, fault-tolerant OCR acceleration.

DirectML works with NVIDIA/AMD/Intel GPUs on Windows and avoids coupling this
desktop application to a particular CUDA/PyTorch build.  GPU initialization or
inference failure is never fatal: the same request is retried on CPU.
"""

from __future__ import annotations

import threading
from typing import Any, Callable


_ENGINE_LOCK = threading.Lock()
_ENGINES: dict[str, Any] = {}


def _rapidocr_class():
    from rapidocr import RapidOCR
    return RapidOCR


def _create_engine(use_gpu: bool):
    rapidocr = _rapidocr_class()
    if use_gpu:
        engine = rapidocr(
            params={"EngineConfig.onnxruntime.use_dml": True})
        if not _engine_uses_provider(engine, "DmlExecutionProvider"):
            raise RuntimeError("DirectML provider did not initialize")
        return engine
    return rapidocr()


def _engine_uses_provider(engine: Any, provider: str) -> bool:
    """Verify the native session instead of trusting requested parameters."""
    for component_name in ("text_det", "text_cls", "text_rec"):
        component = getattr(engine, component_name, None)
        inference = getattr(component, "session", None)
        native_session = getattr(inference, "session", None)
        getter = getattr(native_session, "get_providers", None)
        if callable(getter) and provider in getter():
            return True
    return False


def get_ocr_engine(use_gpu: bool):
    key = "directml" if use_gpu else "cpu"
    with _ENGINE_LOCK:
        if key not in _ENGINES:
            _ENGINES[key] = _create_engine(use_gpu)
        return _ENGINES[key]


def clear_engine_cache() -> None:
    with _ENGINE_LOCK:
        _ENGINES.clear()


def extract_result_text(result: Any) -> list[str]:
    """Accept RapidOCR v3 output as well as its legacy row-list output."""
    if result is None:
        return []
    if isinstance(result, tuple) and len(result) == 2:
        result = result[0]

    texts = getattr(result, "txts", None)
    if texts is None and isinstance(result, dict):
        texts = result.get("txts") or result.get("texts")
    if texts is not None:
        return [str(text).strip() for text in texts if str(text).strip()]

    rows = result if isinstance(result, (list, tuple)) else []
    extracted = []
    for row in rows:
        if isinstance(row, dict):
            text = row.get("text") or row.get("txt")
        elif isinstance(row, (list, tuple)) and len(row) > 1:
            text = row[1]
        else:
            text = None
        if text is not None and str(text).strip():
            extracted.append(str(text).strip())
    return extracted


def run_ocr(image: bytes, prefer_gpu: bool = True,
            log_fn: Callable[[str], None] | None = None) -> tuple[list[str], str]:
    """Return recognized lines and the provider actually used."""
    if prefer_gpu:
        try:
            return extract_result_text(get_ocr_engine(True)(image)), "gpu_directml"
        except Exception as exc:
            if log_fn:
                log_fn("GPU OCR 失败，已自动切换 CPU：" +
                       f"{type(exc).__name__}: {str(exc)[:120]}")
    try:
        return extract_result_text(get_ocr_engine(False)(image)), "cpu"
    except ModuleNotFoundError as exc:
        raise RuntimeError("未安装 RapidOCR 运行环境") from exc


def self_test(prefer_gpu: bool = True) -> dict[str, Any]:
    """Initialize the provider and run a tiny in-memory inference."""
    try:
        from PIL import Image, ImageDraw
        import io

        image = Image.new("RGB", (480, 120), "white")
        ImageDraw.Draw(image).text((20, 40), "Lazybones OCR 2026", fill="black")
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        lines, provider = run_ocr(stream.getvalue(), prefer_gpu=prefer_gpu)
        return {
            "ok": True,
            "provider": provider,
            "recognized": " ".join(lines),
            "message": ("GPU DirectML 自检通过" if provider == "gpu_directml"
                        else "CPU OCR 自检通过（GPU 已回退）"),
        }
    except Exception as exc:
        return {
            "ok": False,
            "provider": "none",
            "recognized": "",
            "message": f"OCR 自检失败：{type(exc).__name__}: {str(exc)[:180]}",
        }

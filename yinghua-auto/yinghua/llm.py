"""LLM 建议答案（M2 stub · 火山方舟兼容接口）。"""

from __future__ import annotations

from typing import Any, Callable

LogFn = Callable[[str], None]


def suggest_option(
    question: str,
    options: list[str],
    *,
    cfg: dict[str, Any],
    log: LogFn = print,
) -> str | None:
    """返回建议选项号（如 'A'/'1'）；失败返回 None。

    prompt 约定（userscript）：「只说选项号」
    """
    llm = cfg.get("llm") or {}
    if not bool(llm.get("enabled", False)):
        log("[llm] 未启用")
        return None
    api_key = str(llm.get("api_key") or "").strip()
    endpoint = str(llm.get("endpoint_id") or "").strip()
    base = str(llm.get("base_url") or "https://ark.cn-beijing.volces.com/api/v3").rstrip("/")
    if not api_key or not endpoint:
        log("[llm] 缺少 api_key 或 endpoint_id")
        return None

    # M2 才真正请求；M1 仅占位
    log(f"[llm] stub: 将 POST {base}/chat/completions model={endpoint[:8]}…")
    _ = question, options
    return None

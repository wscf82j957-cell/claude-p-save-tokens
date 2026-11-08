"""把手机上的多轮记录收成一条用户消息。

系统提示保持固定，方便前缀缓存。会变的历史放在用户消息末尾。
服务不保存这段记录。
"""

from __future__ import annotations

from app.failures import ChatFailure

SYSTEM_PROMPT = (
    "你是这台电脑主人的本机文字助手。"
    "只输出文字。"
    "不要使用工具，不要读写文件，不要执行命令，不要访问网络。"
    "用户消息里有按时间排列的对话，请只回答最后一条用户消息。"
)

MAX_MESSAGES = 40
MAX_MESSAGE_CHARS = 8000
MAX_TOTAL_CHARS = 32000


def parse_messages(payload: object) -> list[dict[str, str]]:
    if not isinstance(payload, dict) or set(payload) != {"messages"}:
        raise ChatFailure("bad_request", 422, "请求格式不对。")
    raw = payload.get("messages")
    if not isinstance(raw, list) or not raw:
        raise ChatFailure("bad_request", 422, "需要至少一条用户消息。")
    if len(raw) > MAX_MESSAGES:
        raise ChatFailure("too_long", 422, "轮数太多，请清掉一些再发。")
    messages: list[dict[str, str]] = []
    total = 0
    for item in raw:
        if not isinstance(item, dict) or set(item) != {"role", "content"}:
            raise ChatFailure("bad_request", 422, "消息里只能有 role 和 content。")
        role = item.get("role")
        content = item.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            raise ChatFailure("bad_request", 422, "消息角色或内容不对。")
        text = content.strip()
        if not text:
            raise ChatFailure("bad_request", 422, "消息是空的。")
        if len(text) > MAX_MESSAGE_CHARS or total + len(text) > MAX_TOTAL_CHARS:
            raise ChatFailure("too_long", 422, "内容太长。")
        total += len(text)
        messages.append({"role": role, "content": text})
    if messages[-1]["role"] != "user":
        raise ChatFailure("bad_request", 422, "最后一条要是用户消息。")
    return messages


def render_transcript(messages: list[dict[str, str]]) -> str:
    lines = [
        "下面是手机页面按时间保留的对话，旧的在前，新的在后。",
        "请只回答最后一条用户消息。",
        "不要使用工具。",
        "",
    ]
    for message in messages:
        label = "用户" if message["role"] == "user" else "助手"
        lines.append(f"[{label}]")
        lines.append(message["content"])
        lines.append("")
    return "\n".join(lines).strip() + "\n"

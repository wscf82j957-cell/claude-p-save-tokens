"""返回给页面的错误。消息是固定短句，不带上游原文。"""

from __future__ import annotations


class ChatFailure(Exception):
    def __init__(self, code: str, http_status: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.message = message

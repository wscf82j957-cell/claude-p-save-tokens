"""用脚本里算出的地址启动。不要另外给 uvicorn 加 --host。"""

from __future__ import annotations

import logging
import sys

import uvicorn

from app.precheck import PrecheckError
from app.settings import Settings


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    try:
        settings = Settings.from_env()
    except PrecheckError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    uvicorn.run(
        "app.server:app",
        host=settings.host,
        port=settings.port,
        access_log=False,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

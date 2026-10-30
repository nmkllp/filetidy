from __future__ import annotations

import sys

from .cli import main


def _run() -> int:
    # Windows 控制台默认编码未必是 utf-8，碰到怪文件名别直接崩
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(errors="replace")
    return main()


if __name__ == "__main__":
    raise SystemExit(_run())

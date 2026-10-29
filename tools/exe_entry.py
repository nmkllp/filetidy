"""打包成 exe 时的入口。

为什么不直接用 `filetidy-gui.pyw`：那个文件里有一段"把仓库根目录塞进 sys.path"
的逻辑，是给"源码直接双击"用的。打成 exe 之后所有代码都在内部解包目录里，
那段逻辑没有意义，还可能帮倒忙。

更重要的原因：`--windowed` 模式下**没有控制台**。程序要是启动时崩了，
用户只会看到"什么都没发生"。所以这里兜底——写一份日志到用户主目录，
再尽量弹个框告诉他日志在哪。
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path


def _log_path() -> Path:
    return Path.home() / "filetidy-error.log"


def _report_failure() -> None:
    try:
        _log_path().write_text(traceback.format_exc(), encoding="utf-8")
    except OSError:
        return  # 连日志都写不了，只能无声无息了

    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance() or QApplication([])
        QMessageBox.critical(
            None,
            "filetidy 启动失败",
            f"出了点问题，详细信息写在这里：\n{_log_path()}",
        )
    except Exception:
        pass


def run() -> int:
    try:
        from filetidy.gui import main as gui_main

        # 默认从用户主目录开始 —— 比"C 盘根"友好（扫整个 C 盘要几分钟）
        return gui_main(["filetidy", str(Path.home())])
    except BaseException:
        _report_failure()
        raise


if __name__ == "__main__":
    raise SystemExit(run())

from __future__ import annotations

import sys
import traceback
from pathlib import Path


def _run() -> int:
    # 双击时的工作目录不一定是这个文件所在的目录，所以手动把仓库根目录
    # 加进 sys.path —— 否则 `import filetidy` 会失败，而且是静默失败。
    here = Path(__file__).resolve().parent
    sys.path.insert(0, str(here))

    from filetidy.gui import main

    # 默认从用户主目录开始：比"C 盘根"友好（扫 C 盘要几分钟），
    # 也比直接打开项目文件夹更接近真实用法。
    return main(["filetidy-gui", str(Path.home())])


def _report_failure() -> None:
    """没有控制台的时候，至少留下一个能查的痕迹。"""
    log = Path(__file__).resolve().with_name("filetidy-gui-error.log")
    try:
        log.write_text(traceback.format_exc(), encoding="utf-8")
    except OSError:
        return  # 连日志都写不了，那就只能无声无息了

    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance() or QApplication([])
        QMessageBox.critical(
            None,
            "filetidy 启动失败",
            f"出了点问题，详细信息在这里：\n{log}\n\n"
            "或者在终端里运行 py -m filetidy gui 看完整报错。",
        )
    except Exception:
        pass


if __name__ == "__main__":
    try:
        raise SystemExit(_run())
    except SystemExit:
        raise
    except BaseException:
        _report_failure()
        raise

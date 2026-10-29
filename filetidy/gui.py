"""图形界面（PySide6）。

这个文件是整个项目里唯一 import PySide6 的地方，而且**只能被 `tidy gui` 延迟导入**。
原因：CLI 那四个命令零依赖，不能因为 GUI 引入第三方包就让它们装不上。
所以 cli.py 里是 `from .gui import main` 写在函数内部，不是模块顶层。

结构上它和 cli.py 是平级的两张脸，共用同一套 core / fs：

    cli.py  ─┐
             ├─→ core.py ─→ models.py
    gui.py  ─┘    fs.py

界面本身不做任何计算：扫描在后台线程里跑，统计交给 core，这里只负责摆控件。
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QProgressBar,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .cli import human_size  # 共用的格式化函数，cli 不反过来 import gui，没有环
from .core import DirStat, child_totals
from .fs import ScanCancelled, scan
from .models import RawScan, ScanProgress

RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter


class ScanWorker(QThread):
    """在后台线程里扫盘。

    为什么必须有它：桌面 20 万个文件要扫 16 秒，C 盘要几分钟。如果在主线程里扫，
    窗口会一直冻住——用户看到的是"程序没反应"，然后点叉。

    线程和界面之间只通过信号通信。**不要在 worker 里碰任何控件**，
    那是跨线程操作 UI，会随机崩溃，而且极难复现。
    """

    progressed = Signal(int, int)  # 已扫文件数, 已扫目录数
    succeeded = Signal(object)  # RawScan
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, target: Path, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._target = target
        self._stop = False

    def cancel(self) -> None:
        """请求停止。真正的停止发生在扫描循环下一次检查时，不会立刻中断。"""
        self._stop = True

    def run(self) -> None:  # 这个函数在**子线程**里执行
        try:
            raw = scan(
                self._target,
                progress=self._report,
                cancel=lambda: self._stop,
            )
        except ScanCancelled:
            self.cancelled.emit()
            return
        except OSError as exc:
            self.failed.emit(str(exc))
            return
        self.succeeded.emit(raw)

    def _report(self, point: ScanProgress) -> None:
        # 发信号是线程安全的：Qt 会把它排队送回主线程再执行
        self.progressed.emit(point.files, point.dirs)


class MainWindow(QWidget):
    def __init__(self, start_path: str = ".") -> None:
        super().__init__()
        self.setWindowTitle("filetidy · 看看空间去哪了")
        self.resize(920, 640)

        self._worker: ScanWorker | None = None
        self._raw: RawScan | None = None  # 整棵树的扫描结果（进子目录时复用，不重扫）
        self._root: Path | None = None  # 当前正在看的目录
        self._stats: tuple[DirStat, ...] = ()  # 当前这一层的完整统计（过滤前）
        self._skipped = 0
        self._ignored = 0

        self._build_ui()
        self.path_edit.setText(str(start_path))

    # ── 界面搭建 ──────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("要扫描的目录")
        self.browse_btn = QPushButton("选择目录…")
        self.scan_btn = QPushButton("扫描")
        self.scan_btn.setDefault(True)
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setVisible(False)  # 只在扫描时出现，平时不占地方

        top = QHBoxLayout()
        top.addWidget(self.path_edit, 1)
        top.addWidget(self.browse_btn)
        top.addWidget(self.scan_btn)
        top.addWidget(self.cancel_btn)

        self.up_btn = QPushButton("↑ 上一级")
        self.up_btn.setEnabled(False)
        self.breadcrumb = QLabel("—")
        self.breadcrumb.setObjectName("breadcrumb")
        self.breadcrumb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("过滤…  Ctrl+F")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setFixedWidth(220)

        nav = QHBoxLayout()
        nav.addWidget(self.up_btn)
        nav.addWidget(self.breadcrumb, 1)
        nav.addWidget(self.search_edit)

        # 扫描结束前不显示具体比例：文件总数事先不知道，硬编一个百分比是假的。
        # range(0, 0) 表示"不确定进度"，Qt 会画一条来回跑的光带。
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)

        self.status = QLabel("选一个目录，然后点「扫描」")
        self.status.setObjectName("status")

        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["名称", "大小", "占比", "文件数"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)  # 行高一致，滚动更顺

        header = self.tree.header()
        # 必须关掉：QHeaderView 默认 stretchLastSection=True，会把最后一列撑满，
        # 于是「文件数」跑到最右边、中间留一大片空白，看起来像没对齐。
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2, 3):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)

        # 注意：不能用 header.setDefaultAlignment() —— 那是**整个表头**的属性，
        # 会把「名称」也右对齐，看起来和左边的内容错位。要逐列设。
        header_item = self.tree.headerItem()
        for col in (1, 2, 3):
            header_item.setTextAlignment(col, RIGHT)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addLayout(top)
        layout.addLayout(nav)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)
        layout.addWidget(self.tree, 1)

        self.browse_btn.clicked.connect(self._choose_dir)
        self.scan_btn.clicked.connect(self.start_scan)
        self.cancel_btn.clicked.connect(self.cancel_scan)
        self.path_edit.returnPressed.connect(self.start_scan)
        self.up_btn.clicked.connect(self.go_up)
        self.tree.itemActivated.connect(self._on_item_activated)
        self.search_edit.textChanged.connect(self._on_filter_changed)

        # 右键菜单：看一眼之后下一步通常是"去资源管理器里打开它"
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)

        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(
            self.search_edit.setFocus
        )

    # ── 扫描 ──────────────────────────────────────────────────

    def _choose_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "选择目录", self.path_edit.text())
        if chosen:
            self.path_edit.setText(chosen)
            self.start_scan()

    def start_scan(self) -> None:
        target = Path(self.path_edit.text().strip() or ".")
        if not target.is_dir():
            self.status.setText(f"这不是一个目录: {target}")
            return

        self.tree.clear()
        self._raw = None
        self._root = None
        self._set_busy(True)
        self.status.setText("正在扫描…")

        self._worker = ScanWorker(target, parent=self)
        self._worker.progressed.connect(self._on_progress)
        self._worker.succeeded.connect(self._on_scanned)
        self._worker.failed.connect(self._on_failed)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def cancel_scan(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.status.setText("正在停止…")

    def _set_busy(self, busy: bool) -> None:
        self.scan_btn.setEnabled(not busy)
        self.browse_btn.setEnabled(not busy)
        self.cancel_btn.setVisible(busy)
        self.progress.setVisible(busy)

    def _on_progress(self, files: int, dirs: int) -> None:
        self.status.setText(f"正在扫描…  已扫描 {files:,} 个文件 / {dirs:,} 个目录")

    def _on_failed(self, message: str) -> None:
        self.status.setText(f"出错了: {message}")

    def _on_cancelled(self) -> None:
        self.status.setText("已取消 —— 这次扫描不完整，数字不可信")

    def _on_finished(self) -> None:
        self._set_busy(False)
        self._worker = None

    def _on_scanned(self, raw: RawScan) -> None:
        self._raw = raw
        self._root = raw.root
        self._render_view()

    # ── 进入 / 返回 ───────────────────────────────────────────

    def _on_item_activated(self, item: QTreeWidgetItem, _column: int) -> None:
        target = item.data(0, Qt.ItemDataRole.UserRole)
        if not target or self._raw is None:
            return
        path = Path(target)
        if path == self._root:
            return  # 「直接放在此目录的文件」那行没有下一层
        # 这里故意不去 stat 磁盘确认目录还在：扫描之后它可能已经被删了。
        # 进不去就当空目录显示，比弹一个错误框友好，也少一次 I/O。
        self._root = path
        self._render_view()

    def go_up(self) -> None:
        if self._raw is None or self._root is None:
            return
        if self._root != self._raw.root:
            self._root = self._root.parent
            self._render_view()

    def _render_view(self) -> None:
        """把当前目录这一层画出来。

        只用扫描时记好的目录合计，不重新扫盘，也不逐文件重算。
        """
        if self._raw is None or self._root is None:
            return
        stats = child_totals(self._raw.dir_totals, self._root)
        self.show_results(
            self._root,
            stats,
            skipped=self._raw.skipped,
            ignored=self._raw.ignored_dirs,
        )

    # ── 展示结果 ──────────────────────────────────────────────

    def show_results(
        self,
        root: Path,
        stats: tuple[DirStat, ...],
        *,
        skipped: int = 0,
        ignored: int = 0,
    ) -> None:
        """接收一层的统计结果，画出来。

        「收下数据」和「画出来」分成两步，是为了过滤能反复重画：
        敲一个字就重画一次，但绝不需要重新扫盘。
        """
        self._root = root
        self._stats = stats
        self._skipped = skipped
        self._ignored = ignored
        self._fill_tree()

    def _on_filter_changed(self, _text: str) -> None:
        self._fill_tree()

    def _fill_tree(self) -> None:
        if self._root is None:
            return

        total = sum(stat.total_size for stat in self._stats)
        file_count = sum(stat.file_count for stat in self._stats)
        query = self.search_edit.text().strip().lower()
        at_top = self._raw is not None and self._root == self._raw.root

        self.tree.clear()
        shown = 0
        for stat in self._stats:
            name = stat.key or "(直接放在此目录的文件)"
            if query and query not in name.lower():
                continue
            shown += 1
            # 百分比永远按**完整总量**算，不受过滤影响。
            # 否则过滤之后每一项都变成 20%，看起来像所有东西平分了空间 —— 假的。
            percent = (stat.total_size / total * 100) if total else 0.0
            item = QTreeWidgetItem(
                [
                    name,
                    human_size(stat.total_size),
                    f"{percent:.1f}%",
                    f"{stat.file_count:,}",
                ]
            )
            # 存完整路径，双击时用来进入下一层；空字符串表示这一行钻不下去
            item.setData(
                0, Qt.ItemDataRole.UserRole, str(self._root / stat.key) if stat.key else ""
            )
            item.setTextAlignment(1, RIGHT)
            item.setTextAlignment(2, RIGHT)
            item.setTextAlignment(3, RIGHT)
            self.tree.addTopLevelItem(item)

        self.breadcrumb.setText(str(self._root))
        self.breadcrumb.setToolTip(str(self._root))
        self.up_btn.setEnabled(not at_top)

        # 把 CLI 里那两行也搬过来：漏掉的东西必须看得见
        notes = []
        if self._ignored:
            notes.append(f"已忽略 {self._ignored} 个目录")
        if self._skipped:
            notes.append(f"跳过 {self._skipped} 项")
        if query:
            notes.append(f"过滤：{shown}/{len(self._stats)} 项")
        suffix = ("    " + " · ".join(notes)) if notes else ""

        if total:
            self.status.setText(f"共 {human_size(total)} / {file_count:,} 个文件{suffix}")
        else:
            self.status.setText(f"这个目录是空的{suffix}")

    # ── 右键菜单 / 键盘 ───────────────────────────────────────

    def _show_context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None or self._root is None:
            return
        target = item.data(0, Qt.ItemDataRole.UserRole)
        # 「直接放在此目录的文件」那行没有子路径，就把当前目录当作目标
        target_path = Path(target) if target else self._root

        menu = QMenu(self)
        open_action = menu.addAction("在资源管理器中打开")
        copy_action = menu.addAction("复制路径")
        open_action.setEnabled(target_path.exists())

        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen is open_action:
            # 用 QDesktopServices 而不是 os.startfile：Qt 自己处理平台差异
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target_path)))
        elif chosen is copy_action:
            QApplication.clipboard().setText(str(target_path))

    def keyPressEvent(self, event) -> None:
        key = event.key()
        # 注意：如果焦点在输入框里，Backspace 会被输入框吃掉，不会走到这儿
        if key == Qt.Key.Key_Backspace:
            self.go_up()
            return
        if key == Qt.Key.Key_F5:
            self.start_scan()
            return
        if key == Qt.Key.Key_Escape and self.search_edit.text():
            self.search_edit.clear()
            return
        super().keyPressEvent(event)

    def scan_blocking(self, target: Path) -> None:
        """同步扫描一次，不开线程。给自动化验证和截图用。"""
        self._on_scanned(scan(target))


def main(argv: list[str] | None = None) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    window = MainWindow(argv[1] if argv and len(argv) > 1 else ".")
    window.show()
    return app.exec()

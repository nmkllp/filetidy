"""GUI 的无头测试。

没有显示器也能测：`QT_QPA_PLATFORM=offscreen` 让 Qt 在内存里画窗口，
不给用户弹任何东西。所以这些测试可以在终端里跑，也可以进 CI（装了 PySide6 的话）。

这里只测"数据和交互逻辑有没有接对"，不测手感——手感只有真人有。

注意：测试用的路径是假的（C:/root/...），磁盘上并不存在。
界面代码特意不依赖"目录是否真的存在"，就是为了让这一层能被这样测。
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

# 必须在 import PySide6 之前设好，否则 Qt 会去找真实显示器
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    import PySide6  # noqa: F401

    HAS_QT = True
except ImportError:  # pragma: no cover - 没装 PySide6 的机器上走这条
    HAS_QT = False

if HAS_QT:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from filetidy.core import build_dir_totals
    from filetidy.gui import MainWindow
    from filetidy.models import FileEntry, RawScan

ROOT = Path("C:/root")


def entry(rel: str, size: int) -> FileEntry:
    path = ROOT / rel
    return FileEntry(path=path, size=size, ext=path.suffix.lower())


@unittest.skipUnless(HAS_QT, "需要 PySide6")
class TestMainWindow(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # QApplication 整个进程只能有一个，所以放在类级别
        cls.app = QApplication.instance() or QApplication([])

    # ── 辅助 ──────────────────────────────────────────────────

    def make_window(self, files):
        window = MainWindow(".")
        raw = RawScan(
            files=tuple(files), root=ROOT, dir_totals=build_dir_totals(files)
        )
        # 模拟一次扫描完成：真实流程里 _on_scanned 会做这几步
        window._raw = raw
        window._root = raw.root
        window._render_view()
        return window

    def names(self, window):
        tree = window.tree
        return [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]

    # ── 基础渲染 ──────────────────────────────────────────────

    def test_rows_follow_the_data(self):
        window = self.make_window([entry("big/a.bin", 300), entry("small/b.bin", 100)])
        self.assertEqual(self.names(window), ["big", "small"])

    def test_percentage_is_relative_to_total(self):
        window = self.make_window([entry("big/a.bin", 300), entry("small/b.bin", 100)])
        self.assertEqual(window.tree.topLevelItem(0).text(2), "75.0%")
        self.assertEqual(window.tree.topLevelItem(1).text(2), "25.0%")

    def test_numeric_columns_are_right_aligned(self):
        window = self.make_window([entry("dir/a.bin", 10)])
        item = window.tree.topLevelItem(0)
        for col in (1, 2, 3):
            self.assertTrue(
                item.textAlignment(col) & Qt.AlignmentFlag.AlignRight,
                f"第 {col} 列没有右对齐",
            )
        self.assertFalse(item.textAlignment(0) & Qt.AlignmentFlag.AlignRight)

    def test_root_files_row_is_labelled(self):
        window = self.make_window([entry("loose.txt", 10)])
        self.assertEqual(self.names(window), ["(直接放在此目录的文件)"])

    def test_empty_directory_does_not_crash(self):
        window = self.make_window([])
        self.assertEqual(window.tree.topLevelItemCount(), 0)
        self.assertIn("空", window.status.text())

    def test_status_shows_total(self):
        window = self.make_window([entry("dir/a.bin", 1024)])
        self.assertIn("1.0 KB", window.status.text())

    def test_status_reports_ignored_and_skipped(self):
        """CLI 里显示的两行，界面上也必须有 —— 漏掉的东西必须看得见。"""
        window = MainWindow(".")
        files = [entry("dir/a.bin", 10)]
        raw = RawScan(
            files=tuple(files),
            root=ROOT,
            skipped=3,
            ignored_dirs=7,
            dir_totals=build_dir_totals(files),
        )
        window._raw = raw
        window._root = raw.root
        window._render_view()
        self.assertIn("已忽略 7", window.status.text())
        self.assertIn("跳过 3", window.status.text())

    # ── 钻取 ──────────────────────────────────────────────────

    def test_drill_down_into_a_folder(self):
        window = self.make_window(
            [entry("a/x.bin", 100), entry("a/y.bin", 50), entry("b/z.bin", 10)]
        )
        window._on_item_activated(window.tree.topLevelItem(0), 0)  # 进入 a
        self.assertEqual(window._root, ROOT / "a")
        # a 下面两个文件都在 a 里，所以只剩「直接放在此目录的文件」这一行
        self.assertEqual(self.names(window), ["(直接放在此目录的文件)"])
        self.assertIn("a", window.breadcrumb.text())

    def test_drill_down_is_instant_and_reuses_the_scan(self):
        """进子目录不该重新扫盘：_raw 必须还是同一个对象。"""
        window = self.make_window([entry("a/x.bin", 100)])
        before = window._raw
        window._on_item_activated(window.tree.topLevelItem(0), 0)
        self.assertIs(window._raw, before)

    def test_go_up_returns_to_parent(self):
        window = self.make_window([entry("a/x.bin", 100), entry("b/z.bin", 10)])
        window._on_item_activated(window.tree.topLevelItem(0), 0)
        window.go_up()
        self.assertEqual(window._root, ROOT)

    def test_go_up_does_nothing_at_the_top(self):
        window = self.make_window([entry("a/x.bin", 100)])
        self.assertFalse(window.up_btn.isEnabled())
        window.go_up()
        self.assertEqual(window._root, ROOT)

    def test_root_files_row_cannot_be_entered(self):
        window = self.make_window([entry("loose.txt", 10)])
        window._on_item_activated(window.tree.topLevelItem(0), 0)
        self.assertEqual(window._root, ROOT)

    def test_up_button_state_follows_depth(self):
        window = self.make_window([entry("a/x.bin", 100)])
        self.assertFalse(window.up_btn.isEnabled())
        window._on_item_activated(window.tree.topLevelItem(0), 0)
        self.assertTrue(window.up_btn.isEnabled())

    # ── 过滤 ──────────────────────────────────────────────────

    def test_filter_hides_non_matching_rows(self):
        window = self.make_window(
            [entry("照片/a.bin", 100), entry("视频/b.bin", 50), entry("文档/c.bin", 10)]
        )
        window.search_edit.setText("视频")
        self.assertEqual(self.names(window), ["视频"])

    def test_filter_is_case_insensitive(self):
        window = self.make_window([entry("Videos/a.bin", 100), entry("docs/b.bin", 10)])
        window.search_edit.setText("video")
        self.assertEqual(self.names(window), ["Videos"])

    def test_percentages_ignore_the_filter(self):
        """过滤只是藏起来，不该改变"占多少"这个事实。"""
        window = self.make_window(
            [entry("big/a.bin", 300), entry("small/b.bin", 100)]
        )
        window.search_edit.setText("small")
        # 只剩一行，但它仍然只占 25%
        self.assertEqual(window.tree.topLevelItemCount(), 1)
        self.assertEqual(window.tree.topLevelItem(0).text(2), "25.0%")

    def test_filter_shows_counts_in_status(self):
        window = self.make_window(
            [entry("照片/a.bin", 100), entry("视频/b.bin", 50), entry("文档/c.bin", 10)]
        )
        window.search_edit.setText("视频")
        self.assertIn("过滤：1/3", window.status.text())

    def test_clearing_filter_restores_all_rows(self):
        window = self.make_window([entry("a/x.bin", 100), entry("b/y.bin", 10)])
        window.search_edit.setText("a")  # 目录名是 "a"，不是路径 "a/"
        self.assertEqual(len(self.names(window)), 1)
        window.search_edit.clear()
        self.assertEqual(len(self.names(window)), 2)

    # ── 键盘 ──────────────────────────────────────────────────

    def send_key(self, window, key):
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QKeyEvent

        event = QKeyEvent(
            QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier
        )
        window.keyPressEvent(event)

    def test_backspace_goes_up(self):
        window = self.make_window([entry("a/x.bin", 100)])
        window._on_item_activated(window.tree.topLevelItem(0), 0)
        self.assertEqual(window._root, ROOT / "a")
        self.send_key(window, Qt.Key.Key_Backspace)
        self.assertEqual(window._root, ROOT)

    def test_escape_clears_the_filter(self):
        window = self.make_window([entry("a/x.bin", 100)])
        window.search_edit.setText("zzz")
        self.send_key(window, Qt.Key.Key_Escape)
        self.assertEqual(window.search_edit.text(), "")

    # ── 真实目录 ──────────────────────────────────────────────

    def test_scan_blocking_on_a_real_directory(self):
        window = MainWindow(".")
        window.scan_blocking(Path(__file__).resolve().parent.parent / "filetidy")
        self.assertGreater(window.tree.topLevelItemCount(), 0)
        self.assertIsNotNone(window._raw)


if __name__ == "__main__":
    unittest.main()

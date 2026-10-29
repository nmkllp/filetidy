"""cli 里那几个纯函数也值得测 —— 单元转换是最容易悄悄写错的地方。"""

from __future__ import annotations

import unittest
from pathlib import Path

from filetidy.cli import _bar, human_size, render, render_dirs
from filetidy.core import dir_sizes, summarize
from filetidy.models import FileEntry, RawScan


class TestHumanSize(unittest.TestCase):
    def test_bytes_stay_bytes(self):
        self.assertEqual(human_size(0), "0 B")
        self.assertEqual(human_size(1023), "1023 B")

    def test_kilobyte_boundary(self):
        self.assertEqual(human_size(1024), "1.0 KB")
        self.assertEqual(human_size(1536), "1.5 KB")

    def test_larger_units(self):
        self.assertEqual(human_size(1024**2), "1.0 MB")
        self.assertEqual(human_size(3 * 1024**3), "3.0 GB")

    def test_negative_rejected(self):
        with self.assertRaises(ValueError):
            human_size(-1)


class TestRender(unittest.TestCase):
    def test_empty_directory_does_not_crash(self):
        text = render(summarize([]), Path("C:/tmp"))
        self.assertIn("空", text)

    def test_report_mentions_skipped_when_present(self):
        report = summarize([FileEntry(path=Path("a.bin"), size=1, ext=".bin")], skipped=3)
        self.assertIn("跳过: 3", render(report, Path("C:/tmp")))

    def test_report_has_no_skipped_line_when_zero(self):
        report = summarize([FileEntry(path=Path("a.bin"), size=1, ext=".bin")])
        self.assertNotIn("跳过", render(report, Path("C:/tmp")))


class TestBar(unittest.TestCase):
    def test_full_and_empty(self):
        self.assertEqual(_bar(1.0, width=4), "████")
        self.assertEqual(_bar(0.0, width=4), "░░░░")

    def test_always_same_width(self):
        for f in (0.0, 0.13, 0.5, 0.99, 1.0):
            self.assertEqual(len(_bar(f, width=10)), 10)

    def test_out_of_range_is_clamped(self):
        self.assertEqual(_bar(-5.0, width=4), "░░░░")
        self.assertEqual(_bar(9.9, width=4), "████")


class TestRenderDirs(unittest.TestCase):
    root = Path("C:/root")

    def raw(self, entries, **kw) -> RawScan:
        return RawScan(
            files=tuple(entries),
            skipped=kw.get("skipped", 0),
            ignored_dirs=kw.get("ignored", 0),
            root=self.root,
        )

    def at(self, rel: str, size: int) -> FileEntry:
        path = self.root / rel
        return FileEntry(path=path, size=size, ext=path.suffix.lower())

    def test_lists_folder_names_and_percentages(self):
        raw = self.raw([self.at("big/x.bin", 300), self.at("small/x.bin", 100)])
        text = render_dirs(dir_sizes(raw.files, self.root), raw, top=10)
        self.assertIn("big", text)
        self.assertIn("75.0%", text)

    def test_top_limits_rows(self):
        entries = [self.at(f"d{i}/x.bin", i + 1) for i in range(6)]
        raw = self.raw(entries)
        text = render_dirs(dir_sizes(raw.files, self.root), raw, top=2)
        self.assertIn("共 6 项，显示前 2", text)

    def test_root_files_row_is_labelled(self):
        raw = self.raw([self.at("loose.txt", 10)])
        text = render_dirs(dir_sizes(raw.files, self.root), raw, top=10)
        self.assertIn("直接放在此目录的文件", text)

    def test_empty_directory_does_not_crash(self):
        raw = self.raw([])
        self.assertIn("空", render_dirs(dir_sizes([], self.root), raw, top=10))

    def test_suggests_next_drill_down_command(self):
        raw = self.raw([self.at("big/x.bin", 10)])
        text = render_dirs(dir_sizes(raw.files, self.root), raw, top=10)
        self.assertIn("filetidy dirs", text)


if __name__ == "__main__":
    unittest.main()

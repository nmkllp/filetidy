"""core 的测试：注意这里一个文件、一个临时目录都没造。

因为 core 是纯逻辑，测试它只需要喂数据。这就是"分层"给你的第一份回报。
运行: python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from filetidy.core import (
    DEFAULT_IGNORED_DIRS,
    build_dir_totals,
    child_totals,
    dir_sizes,
    is_ignored,
    summarize,
)
from filetidy.models import FileEntry


def entry(name: str, size: int) -> FileEntry:
    path = Path(name)
    return FileEntry(path=path, size=size, ext=path.suffix.lower())


class TestSummarize(unittest.TestCase):
    def test_empty_directory(self):
        report = summarize([])
        self.assertEqual(report.file_count, 0)
        self.assertEqual(report.total_size, 0)
        self.assertEqual(report.by_ext, ())
        self.assertEqual(report.largest, ())

    def test_total_size_and_count(self):
        report = summarize([entry("a.jpg", 100), entry("b.jpg", 250)])
        self.assertEqual(report.file_count, 2)
        self.assertEqual(report.total_size, 350)

    def test_grouping_by_extension(self):
        report = summarize(
            [entry("a.jpg", 100), entry("b.jpg", 200), entry("c.pdf", 50)]
        )
        stats = {s.ext: (s.count, s.total_size) for s in report.by_ext}
        self.assertEqual(stats[".jpg"], (2, 300))
        self.assertEqual(stats[".pdf"], (1, 50))

    def test_extensions_sorted_by_space_used(self):
        report = summarize(
            [entry("small.jpg", 10), entry("big.mkv", 9000), entry("mid.pdf", 500)]
        )
        self.assertEqual([s.ext for s in report.by_ext], [".mkv", ".pdf", ".jpg"])

    def test_no_extension_becomes_empty_key(self):
        report = summarize([entry("README", 10)])
        self.assertEqual(report.by_ext[0].ext, "")

    def test_top_limits_rows(self):
        files = [entry(f"f{i}.ext{i}", i + 1) for i in range(10)]
        report = summarize(files, top=3)
        self.assertEqual(len(report.by_ext), 3)

    def test_largest_sorted_and_limited(self):
        report = summarize(
            [entry("a", 1), entry("b", 999), entry("c", 500)], largest_n=2
        )
        self.assertEqual([f.path.name for f in report.largest], ["b", "c"])

    def test_skipped_is_passed_through(self):
        report = summarize([entry("a.txt", 1)], skipped=7)
        self.assertEqual(report.skipped, 7)

    def test_case_insensitive_extension_comes_from_scanner(self):
        # 小写化是 fs 的职责，core 只信任进来的数据
        report = summarize([FileEntry(path=Path("A.JPG"), size=1, ext=".jpg")])
        self.assertEqual(report.by_ext[0].ext, ".jpg")


class TestIsIgnored(unittest.TestCase):
    def test_exact_name_matches(self):
        self.assertTrue(is_ignored("node_modules", DEFAULT_IGNORED_DIRS))

    def test_case_insensitive(self):
        self.assertTrue(is_ignored("Node_Modules", DEFAULT_IGNORED_DIRS))

    def test_unrelated_name_passes(self):
        self.assertFalse(is_ignored("photos", DEFAULT_IGNORED_DIRS))

    def test_glob_pattern_matches(self):
        self.assertTrue(is_ignored("build-2024", ["build*"]))

    def test_question_mark_matches_one_char(self):
        self.assertTrue(is_ignored("tmp1", ["tmp?"]))
        self.assertFalse(is_ignored("tmp12", ["tmp?"]))

    def test_empty_patterns_ignores_nothing(self):
        self.assertFalse(is_ignored("node_modules", []))


class TestDirSizes(unittest.TestCase):
    """目录聚合。同样一个临时目录都不用造 —— 路径就在数据里，算账是纯的。"""

    root = Path("C:/root")

    def at(self, rel: str, size: int) -> FileEntry:
        path = self.root / rel
        return FileEntry(path=path, size=size, ext=path.suffix.lower())

    def test_groups_by_first_level_folder(self):
        stats = dir_sizes(
            [self.at("a/x.bin", 100), self.at("a/y.bin", 50), self.at("b/z.bin", 30)],
            self.root,
        )
        got = {s.key: (s.total_size, s.file_count) for s in stats}
        self.assertEqual(got["a"], (150, 2))
        self.assertEqual(got["b"], (30, 1))

    def test_files_directly_in_root_get_empty_key(self):
        stats = dir_sizes([self.at("loose.txt", 10), self.at("a/x.bin", 5)], self.root)
        self.assertIn("", {s.key for s in stats})

    def test_sorted_biggest_first(self):
        stats = dir_sizes(
            [self.at("small/x", 1), self.at("big/x", 900), self.at("mid/x", 40)], self.root
        )
        self.assertEqual([s.key for s in stats], ["big", "mid", "small"])

    def test_depth_two_goes_one_level_deeper(self):
        stats = dir_sizes(
            [self.at("a/b1/x", 100), self.at("a/b2/x", 10)], self.root, depth=2
        )
        # 用 Path 拼期望值而不是写死 "a\b1"：分隔符取决于操作系统，
        # 写死反斜杠的话这套测试在 Linux / CI 上会红
        expected = sorted([str(Path("a", "b1")), str(Path("a", "b2"))])
        self.assertEqual(sorted(s.key for s in stats), expected)

    def test_depth_deeper_than_tree_falls_back_to_real_folder(self):
        # depth=9 但树只有两层：不能把文件名当成目录名
        stats = dir_sizes([self.at("a/b/x.txt", 7)], self.root, depth=9)
        self.assertEqual([s.key for s in stats], [str(Path("a", "b"))])

    def test_files_outside_root_do_not_pollute_the_total(self):
        outside = FileEntry(path=Path("D:/elsewhere/x.bin"), size=10_000, ext=".bin")
        stats = dir_sizes([self.at("a/x.bin", 10), outside], self.root)
        self.assertEqual(sum(s.total_size for s in stats), 10)

    def test_empty_input(self):
        self.assertEqual(dir_sizes([], self.root), ())

    def test_depth_must_be_positive(self):
        with self.assertRaises(ValueError):
            dir_sizes([], self.root, depth=0)


class TestChildTotals(unittest.TestCase):
    """界面下钻用的快速聚合。

    它和 dir_sizes 算的是同一件事，只是输入不同：
    dir_sizes 吃文件列表，child_totals 吃"每目录直属合计"（扫描时顺手记的）。
    所以最重要的测试是最后那条：两者结果必须完全一致。
    """

    root = Path("C:/root")

    def totals(self, mapping: dict[str, tuple[int, int]]) -> dict[str, tuple[int, int]]:
        """把 {"a/b": (字节, 个数)} 变成绝对路径键，模拟扫描产出的形状。"""
        return {
            str(self.root / key if key else self.root): value
            for key, value in mapping.items()
        }

    def test_groups_descendants_into_first_segment(self):
        stats = child_totals(
            self.totals({"a": (100, 1), "a/b": (50, 1), "c": (10, 1)}), self.root
        )
        got = {s.key: (s.total_size, s.file_count) for s in stats}
        self.assertEqual(got["a"], (150, 2))  # a 自己 + a/b
        self.assertEqual(got["c"], (10, 1))

    def test_root_files_go_into_the_empty_key(self):
        stats = child_totals(self.totals({"": (7, 1), "a": (3, 1)}), self.root)
        got = {s.key: s.total_size for s in stats}
        self.assertEqual(got[""], 7)

    def test_deep_paths_roll_up_to_the_top_segment(self):
        stats = child_totals(self.totals({"a/b/c/d": (9, 1)}), self.root)
        self.assertEqual([(s.key, s.total_size) for s in stats], [("a", 9)])

    def test_sorted_biggest_first(self):
        stats = child_totals(
            self.totals({"small": (1, 1), "big": (100, 1), "mid": (10, 1)}), self.root
        )
        self.assertEqual([s.key for s in stats], ["big", "mid", "small"])

    def test_unrelated_paths_are_ignored(self):
        stats = child_totals(
            {str(self.root / "a"): (5, 1), "D:/elsewhere/x": (999, 9)}, self.root
        )
        self.assertEqual(sum(s.total_size for s in stats), 5)

    @unittest.skipUnless(sys.platform == "win32", "盘符根只在 Windows 上有意义")
    def test_drive_root_does_not_double_the_separator(self):
        """root 是 "C:\\\\" 这种带分隔符的盘符根时，前缀不能拼成 "C:\\\\\\\\"。"""
        stats = child_totals({"C:\\": (1, 1), "C:\\Users": (10, 1)}, Path("C:\\"))
        got = {s.key: s.total_size for s in stats}
        self.assertEqual(got[""], 1)  # 盘符根下的直属文件
        self.assertEqual(got["Users"], 10)

    def test_matches_dir_sizes_exactly(self):
        """两种算法必须给出同一个答案 —— 这是这次性能改造的安全网。"""
        files = [
            FileEntry(path=self.root / "a/x.bin", size=100, ext=".bin"),
            FileEntry(path=self.root / "a/y.bin", size=50, ext=".bin"),
            FileEntry(path=self.root / "a/sub/z.bin", size=25, ext=".bin"),
            FileEntry(path=self.root / "b/w.bin", size=10, ext=".bin"),
            FileEntry(path=self.root / "loose.txt", size=7, ext=".txt"),
        ]
        fast = child_totals(build_dir_totals(files), self.root)
        slow = dir_sizes(files, self.root)
        self.assertEqual(
            [(s.key, s.total_size, s.file_count) for s in fast],
            [(s.key, s.total_size, s.file_count) for s in slow],
        )


if __name__ == "__main__":
    unittest.main()

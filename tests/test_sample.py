"""在样本数据集上做的集成测试。

这是第三类测试，和前两个文件都不一样：
  test_core.py  纯函数，喂数据
  test_fs.py    真实文件系统，自己造几个文件
  test_sample.py 用**共享的样本数据集**跑一遍完整流程（造目录 → 扫描 → 聚合）

它的价值在于"端到端"：验证的是 fs + core 接在一起还对不对，
而且用的是一份结果可预测的数据 —— 所以可以断言精确数字，而不是"大概差不多"。
"""

from __future__ import annotations

import shutil
import unittest
from pathlib import Path

from filetidy.core import dir_sizes, find_duplicates
from filetidy.fs import read_head, scan, sha256_file
from tools.make_sample import EMPTY_DIRS, FILES, build

_TEST_TMP_ROOT = Path(__file__).resolve().parent.parent / ".tmp-tests"

# 样本里最明确的一个数字：那份 __pycache__ 有 5 KB，默认应该被忽略
IGNORED_PYC_BYTES = 5 * 1024


class TestOnSampleData(unittest.TestCase):
    def setUp(self) -> None:
        _TEST_TMP_ROOT.mkdir(exist_ok=True)
        self.root = build(_TEST_TMP_ROOT / "sample-case")
        self.raw = scan(self.root)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_pycache_is_pruned_by_default(self):
        self.assertEqual(self.raw.ignored_dirs, 1)
        names = {f.path.name for f in self.raw.files}
        self.assertNotIn("junk.pyc", names)

    def test_file_count_excludes_ignored(self):
        self.assertEqual(len(self.raw.files), len(FILES) - 1)

    def test_total_size_is_generated_bytes_minus_ignored(self):
        expected = sum(size for _, size, _ in FILES) - IGNORED_PYC_BYTES
        self.assertEqual(sum(f.size for f in self.raw.files), expected)

    def test_empty_directories_exist_but_contribute_no_files(self):
        for rel in EMPTY_DIRS:
            self.assertTrue((self.root / rel).is_dir(), rel)
            self.assertFalse(any((self.root / rel).iterdir()), rel)

    def test_three_duplicates_have_identical_size(self):
        # 这是下一步「重复文件检测」要用的前提：三份 1 KB，内容完全一样
        dups = [f for f in self.raw.files if "设计稿" in f.path.name]
        self.assertEqual(len(dups), 3)
        self.assertEqual({f.size for f in dups}, {1024})

    def test_dir_totals_sum_up_to_the_whole(self):
        # 不变式：各部分之和必须等于总量。破了就说明有文件被漏算或重复算了。
        stats = dir_sizes(self.raw.files, self.raw.root)
        self.assertEqual(
            sum(s.total_size for s in stats), sum(f.size for f in self.raw.files)
        )
        self.assertEqual(
            sum(s.file_count for s in stats), len(self.raw.files)
        )

    def test_turning_off_defaults_pulls_in_the_pycache(self):
        raw_all = scan(self.root, use_default_ignores=False)
        self.assertEqual(len(raw_all.files), len(FILES))
        self.assertIn("junk.pyc", {f.path.name for f in raw_all.files})

    # ── 重复检测：真正读文件，验证三层筛串起来是对的 ────────────────

    def test_finds_the_three_planted_copies(self):
        groups = find_duplicates(
            self.raw.files, read_head=read_head, read_hash=sha256_file
        )
        as_names = [sorted(p.name for p in g.paths) for g in groups]
        self.assertIn(
            ["设计稿-副本.md", "设计稿-旧版.md", "设计稿.md"], as_names
        )

    def test_planted_group_has_exact_size_and_waste(self):
        groups = find_duplicates(
            self.raw.files, read_head=read_head, read_hash=sha256_file
        )
        planted = next(g for g in groups if len(g.paths) == 3)
        self.assertEqual(planted.size, 1024)
        self.assertEqual(planted.wasted, 2048)  # 留一份，另外两份是白占的

    def test_only_the_planted_group_is_found(self):
        # 样本里除了那 3 份设计稿，没有别的重复内容
        groups = find_duplicates(
            self.raw.files, read_head=read_head, read_hash=sha256_file
        )
        self.assertEqual(len(groups), 1)


if __name__ == "__main__":
    unittest.main()

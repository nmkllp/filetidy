"""fs 层的测试：这一层必须真的碰硬盘，所以测试也得真造目录。

对比一下 tests/test_core.py —— 那里一个目录都没建。
这个差别就是"分层"给你的第二个回报：难的测试被关在一个小文件里，
别的测试全都保持便宜、快、互不干扰。
"""

from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path

from filetidy.fs import ScanCancelled, scan

# 临时目录故意放在项目里，而不是系统的 %TEMP%。
# 两个理由：受限环境（沙箱、某些 CI）常常不让写系统临时目录；
# 而且测试把文件造在哪里，出问题时你就得去哪里翻 —— 放在眼前比放在 C:\Users\x\AppData\Local\Temp 好找。
_TEST_TMP_ROOT = Path(__file__).resolve().parent.parent / ".tmp-tests"


def make_case_dir() -> Path:
    """造一个一次性的测试目录。

    这里故意不用 tempfile.mkdtemp：它在 Windows 上按 0o700 建目录，
    会生成一个"不再继承父目录"的权限列表。父目录继承下来的访问授权因此丢失，
    在受限（沙箱）环境里连自己都写不进去。普通 mkdir 保留继承，就没这问题。
    """
    _TEST_TMP_ROOT.mkdir(exist_ok=True)
    path = _TEST_TMP_ROOT / f"case-{uuid.uuid4().hex[:8]}"
    path.mkdir()
    return path


class TestScan(unittest.TestCase):
    def setUp(self) -> None:
        self.root = make_case_dir()
        (self.root / "a.txt").write_text("x", encoding="utf-8")  # 1 字节
        (self.root / "keep").mkdir()
        (self.root / "keep" / "b.md").write_text("xx", encoding="utf-8")  # 2 字节

        node_modules = self.root / "node_modules"
        node_modules.mkdir()
        (node_modules / "dep.js").write_text("x", encoding="utf-8")

    def tearDown(self) -> None:
        # ignore_errors：某些环境不允许删，别让"清理失败"掩盖"测试通过"
        shutil.rmtree(self.root, ignore_errors=True)

    def _names(self, raw) -> set[str]:
        return {f.path.name for f in raw.files}

    def test_recurses_into_subdirectories(self):
        raw = scan(self.root)
        self.assertIn("b.md", self._names(raw))

    def test_default_rules_prune_node_modules(self):
        raw = scan(self.root)
        self.assertNotIn("dep.js", self._names(raw))
        self.assertEqual(raw.ignored_dirs, 1)

    def test_defaults_can_be_turned_off(self):
        raw = scan(self.root, use_default_ignores=False)
        self.assertIn("dep.js", self._names(raw))
        self.assertEqual(raw.ignored_dirs, 0)

    def test_custom_exclude_supports_glob(self):
        raw = scan(self.root, exclude=["k*"])
        names = self._names(raw)
        self.assertNotIn("b.md", names)
        self.assertIn("a.txt", names)

    def test_custom_exclude_is_case_insensitive(self):
        raw = scan(self.root, exclude=["NODE_MODULES"])
        self.assertNotIn("dep.js", self._names(raw))

    # ── 进度回调：GUI 的地基 ──────────────────────────────────────

    def test_progress_reports_final_totals(self):
        points = []
        raw = scan(self.root, progress=points.append, progress_every=1)
        # 最后一条必须是准确的总数，界面靠它把进度条收到 100%
        self.assertEqual(points[-1].files, len(raw.files))
        # dirs 是"实际打开过的目录数"：root 和 keep，就两个。
        # node_modules 被剪掉了、根本没进去，所以不算 —— 这个语义要一致，
        # 否则界面上的"已扫描 N 个目录"会虚高。
        self.assertEqual(points[-1].dirs, 2)

    def test_progress_counts_never_go_backwards(self):
        points = []
        scan(self.root, progress=points.append, progress_every=1)
        files = [p.files for p in points]
        self.assertEqual(files, sorted(files))

    def test_progress_throttles_by_interval(self):
        """只有 2 个文件，间隔设 100 的话不该被频繁打扰。"""
        points = []
        scan(self.root, progress=points.append, progress_every=100)
        self.assertEqual(len(points), 1)  # 只有收尾那一次

    def test_no_callback_by_default(self):
        raw = scan(self.root)
        self.assertEqual(len(raw.files), 2)

    # ── 目录合计（界面下钻用的索引） ──────────────────────────

    def test_dir_totals_match_the_files(self):
        """索引不能和事实不一致：目录合计的总和必须等于文件总和。"""
        raw = scan(self.root)
        total = sum(size for size, _count in raw.dir_totals.values())
        self.assertEqual(total, sum(f.size for f in raw.files))

        count = sum(c for _size, c in raw.dir_totals.values())
        self.assertEqual(count, len(raw.files))

    def test_dir_totals_only_count_direct_children(self):
        """每个目录只记"直属"文件，不含子孙 —— 汇总留给 core 做。"""
        raw = scan(self.root)
        # root 下只有 a.txt 一个直属文件；b.md 在 keep/ 里
        self.assertEqual(raw.dir_totals[str(self.root)][1], 1)
        self.assertEqual(raw.dir_totals[str(self.root / "keep")][1], 1)

    def test_pruned_directories_are_absent_from_totals(self):
        raw = scan(self.root)
        self.assertNotIn(str(self.root / "node_modules"), raw.dir_totals)

    # ── 取消 ──────────────────────────────────────────────────

    def test_cancel_raises_instead_of_returning_half_a_result(self):
        """取消时抛异常，而不是返回"扫了一半"的结果。

        半张报告的数字看着像真的，但合计和百分比全是错的 —— 宁可明说没扫完。
        """
        with self.assertRaises(ScanCancelled):
            scan(self.root, cancel=lambda: True)

    def test_cancel_false_completes_normally(self):
        raw = scan(self.root, cancel=lambda: False)
        self.assertEqual(len(raw.files), 2)

    def test_cancel_is_checked_per_directory_not_per_file(self):
        """检查频率要能算出来：有多少个目录就查多少次，不多不少。"""
        checks = []

        def cancel() -> bool:
            checks.append(1)
            return False

        raw = scan(self.root, cancel=cancel)
        self.assertEqual(len(checks), 2)  # root + keep（node_modules 被剪掉，不进去）
        self.assertEqual(len(raw.files), 2)

    def test_sizes_are_real_byte_counts(self):
        raw = scan(self.root)
        sizes = {f.path.name: f.size for f in raw.files}
        self.assertEqual(sizes["a.txt"], 1)
        self.assertEqual(sizes["b.md"], 2)

    def test_extension_is_lowercased(self):
        raw = scan(self.root)
        exts = {f.path.name: f.ext for f in raw.files}
        self.assertEqual(exts["a.txt"], ".txt")

    def test_missing_path_raises(self):
        with self.assertRaises(FileNotFoundError):
            scan(self.root / "does-not-exist")

    def test_file_instead_of_directory_raises(self):
        with self.assertRaises(NotADirectoryError):
            scan(self.root / "a.txt")


if __name__ == "__main__":
    unittest.main()

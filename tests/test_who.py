"""`who` 命令的测试：归属匹配的逻辑，以及注册表字符串的解析。

两部分都不碰注册表：`match_app` 吃的是普通数据，`_extract_dir` 只是字符串处理。
所以这个文件在 Linux 上也能跑（CI 就是靠这个）。

`import winreg` 在 registry.py 里是写在函数内部的，模块顶层没有，
所以 `from filetidy.registry import _extract_dir` 在任何平台上都安全。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from filetidy.core import distinct_locations, match_app
from filetidy.models import AppLocation, AppRecord
from filetidy.registry import _extract_dir, _is_too_broad


def app(name: str, *locations: tuple[str, str]) -> AppRecord:
    return AppRecord(
        name=name,
        publisher="示例发行方",
        registry_key=f"HKLM\\...\\Uninstall\\{name}",
        locations=tuple(AppLocation(path=Path(p), source=s) for p, s in locations),
    )


class TestMatchApp(unittest.TestCase):
    def test_exact_directory_matches(self):
        apps = [app("Git", ("C:/Program Files/Git", "InstallLocation"))]
        match = match_app(Path("C:/Program Files/Git"), apps)
        self.assertIsNotNone(match)
        self.assertEqual(match.app.name, "Git")

    def test_child_path_matches(self):
        apps = [app("Git", ("C:/Program Files/Git", "InstallLocation"))]
        match = match_app(Path("C:/Program Files/Git/cmd/git.exe"), apps)
        self.assertIsNotNone(match)
        self.assertEqual(match.app.name, "Git")

    def test_unrelated_path_returns_none(self):
        apps = [app("Git", ("C:/Program Files/Git", "InstallLocation"))]
        self.assertIsNone(match_app(Path("C:/Users/me/Documents"), apps))

    def test_deepest_match_wins(self):
        """`C:\\Program Files` 和它下面的 `Git` 都是祖先，应该选更具体的那个。"""
        apps = [
            app("某个大杂烩", ("C:/Program Files", "InstallLocation")),
            app("Git", ("C:/Program Files/Git", "InstallLocation")),
        ]
        match = match_app(Path("C:/Program Files/Git/bin/git.exe"), apps)
        self.assertEqual(match.app.name, "Git")

    def test_install_location_is_high_confidence(self):
        apps = [app("Git", ("C:/Program Files/Git", "InstallLocation"))]
        match = match_app(Path("C:/Program Files/Git"), apps)
        self.assertEqual(match.confidence, "高")

    def test_icon_derived_location_is_medium_confidence(self):
        apps = [app("OBS", ("C:/Program Files/obs-studio/bin/64bit", "DisplayIcon"))]
        match = match_app(Path("C:/Program Files/obs-studio/bin/64bit/obs64.exe"), apps)
        self.assertEqual(match.confidence, "中")

    def test_path_is_not_a_prefix_of_a_sibling(self):
        """"C:\\Apps\\Foo" 不应该匹配 "C:\\Apps\\Foobar\\x"。"""
        apps = [app("Foo", ("C:/Apps/Foo", "InstallLocation"))]
        self.assertIsNone(match_app(Path("C:/Apps/Foobar/x.txt"), apps))

    def test_app_without_locations_never_matches(self):
        apps = [app("绿色软件")]
        self.assertIsNone(match_app(Path("D:/tools/green/app.exe"), apps))

    def test_empty_app_list(self):
        self.assertIsNone(match_app(Path("C:/anything"), []))


class TestDistinctLocations(unittest.TestCase):
    def test_nested_location_is_filtered_out(self):
        """Git 的图标在 ucrt64\\share\\git 里，藏在安装目录内部，不算"别的位置"。"""
        git = app(
            "Git",
            ("C:/Program Files/Git", "InstallLocation"),
            ("C:/Program Files/Git/ucrt64/share/git", "DisplayIcon"),
        )
        chosen = git.locations[0]
        self.assertEqual(distinct_locations(git, chosen), ())

    def test_parent_location_is_kept(self):
        """OBS 的卸载程序在安装目录之上，这是有用信息：告诉你应用根在哪。"""
        obs = app(
            "OBS",
            ("C:/Program Files/obs-studio/bin/64bit", "DisplayIcon"),
            ("C:/Program Files/obs-studio", "UninstallString"),
        )
        chosen = obs.locations[0]
        others = distinct_locations(obs, chosen)
        self.assertEqual(len(others), 1)
        self.assertTrue(str(others[0].path).replace("\\", "/").endswith("obs-studio"))

    def test_unrelated_sibling_is_kept(self):
        """同级的另一个目录（比如程序本体和数据目录分开放）也要保留。"""
        some = app(
            "某应用",
            ("C:/Apps/某应用", "InstallLocation"),
            ("D:/Data/某应用", "DisplayIcon"),
        )
        others = distinct_locations(some, some.locations[0])
        self.assertEqual(len(others), 1)


class TestTooBroad(unittest.TestCase):
    def test_drive_root_is_too_broad(self):
        self.assertTrue(_is_too_broad(Path("C:/")))

    @unittest.skipUnless(sys.platform == "win32", "Only meaningful on Windows")
    def test_program_files_is_too_broad(self):
        """把 `C:\\Program Files` 当证据，会让它下面的每个目录都"属于同一个程序"。"""
        import os

        self.assertTrue(_is_too_broad(Path(os.environ["ProgramFiles"])))

    @unittest.skipUnless(sys.platform == "win32", "Only meaningful on Windows")
    def test_real_install_dir_is_not_too_broad(self):
        import os

        self.assertFalse(_is_too_broad(Path(os.environ["ProgramFiles"]) / "SomeApp"))


class TestExtractDir(unittest.TestCase):
    def test_quoted_icon_with_index(self):
        got = _extract_dir('"C:\\Program Files\\Git\\git.ico",0')
        self.assertIsNotNone(got)
        self.assertTrue(str(got).endswith("Git"))

    def test_quoted_exe(self):
        got = _extract_dir('"C:\\Program Files\\obs-studio\\bin\\64bit\\obs64.exe"')
        self.assertIsNotNone(got)
        self.assertTrue(str(got).replace("\\", "/").endswith("bin/64bit"))

    def test_unquoted_exe_with_arguments(self):
        got = _extract_dir("C:\\Program Files\\X\\uninst.exe /S")
        self.assertIsNotNone(got)
        self.assertTrue(str(got).replace("\\", "/").endswith("Program Files/X"))

    def test_bare_directory(self):
        got = _extract_dir("C:\\Program Files\\Maxon Cinema 4D R22")
        self.assertIsNotNone(got)
        self.assertTrue(str(got).replace("\\", "/").endswith("Cinema 4D R22"))

    def test_msiexec_is_not_a_path(self):
        self.assertIsNone(_extract_dir("MsiExec.exe /X{6219E6AE-8668-4CB1-91B7}"))

    def test_empty_string(self):
        self.assertIsNone(_extract_dir(""))

    def test_relative_path_rejected(self):
        self.assertIsNone(_extract_dir("uninst.exe"))


if __name__ == "__main__":
    unittest.main()

"""数据结构：整个项目共用的一套"词汇"。

这一层故意没有任何逻辑，只有名词。它被 fs 和 core 同时依赖，
所以谁都不会反过来依赖别人 —— 这就是依赖只朝一个方向走。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class FileEntry:
    """一个文件被扫描到那一刻的"事实"。"""

    path: Path
    size: int
    ext: str  # 小写、带点，例如 ".jpg"；没有扩展名时是空字符串 ""


@dataclass(frozen=True)
class RawScan:
    """扫描的原始结果。

    skipped 是"被挡在门外"的条目数（没权限、符号链接等）。
    ignored_dirs 是"按规则主动不看"的目录数（.git、node_modules 等）。
    这两个数字分开记，因为它们性质完全不同：
      skipped  = 我想看，但看不了 —— 可能是问题，要让人知道
      ignored  = 我决定不看 —— 是我的选择，要让人能复核
    """

    files: tuple[FileEntry, ...]
    skipped: int = 0
    ignored_dirs: int = 0
    root: Path = Path(".")
    # 每个目录的**直属**文件合计：{目录路径字符串: (字节数, 文件数)}。
    # 扫描时顺手记下，因为那时候手里正好有目录路径和文件大小。
    # 有了它，界面点开一个目录只需要遍历目录（数量约为文件的十分之一），
    # 不用再逐文件做 Path 运算 —— 那是十几秒和几十毫秒的差别。
    dir_totals: dict[str, tuple[int, int]] = field(default_factory=dict)


@dataclass(frozen=True)
class ScanProgress:
    """扫描进度的一个快照。

    为什么需要它：20 万个文件要扫 16 秒。命令行里等 16 秒无所谓，
    但窗口里等 16 秒——界面会完全冻住，用户以为程序崩了，然后点叉。

    有了这个回调，界面可以每隔一会儿更新一次"已扫描 N 个文件"，
    至少让人知道它还在动。
    """

    files: int
    dirs: int
    current: Path


@dataclass(frozen=True)
class AppLocation:
    """一个应用"可能在这个目录里"的线索。

    source 说明这条线索从哪来，因为可信度不一样：
      InstallLocation  —— 安装器自己写的，最可信
      DisplayIcon      —— 图标文件所在的目录，通常就是安装目录
      UninstallString  —— 卸载程序所在目录，通常也是安装目录
    """

    path: Path
    source: str


@dataclass(frozen=True)
class AppRecord:
    """一个已登记的程序。数据来自注册表的卸载项。"""

    name: str
    publisher: str
    registry_key: str  # 完整路径，出问题时能让人去核对
    locations: tuple[AppLocation, ...]


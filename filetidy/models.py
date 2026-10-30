from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class FileEntry:

    path: Path
    size: int
    ext: str  # 小写、带点，例如 ".jpg"；没有扩展名时是空字符串 ""


@dataclass(frozen=True)
class RawScan:

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

    files: int
    dirs: int
    current: Path


@dataclass(frozen=True)
class AppLocation:

    path: Path
    source: str


@dataclass(frozen=True)
class AppRecord:

    name: str
    publisher: str
    registry_key: str  # 完整路径，出问题时能让人去核对
    locations: tuple[AppLocation, ...]

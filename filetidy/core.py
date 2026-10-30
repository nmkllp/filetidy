from __future__ import annotations

import fnmatch
import os
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .models import AppLocation, AppRecord, FileEntry


# 默认忽略的目录名。挑的都是"里面全是机器生成的东西、人不会去清理"的目录。
DEFAULT_IGNORED_DIRS: tuple[str, ...] = (
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    ".venv",
    "venv",
    ".idea",
    ".vscode",
    "build",
    "dist",
)


def is_ignored(name: str, patterns: Sequence[str]) -> bool:
    lowered = name.lower()
    return any(fnmatch.fnmatchcase(lowered, pattern.lower()) for pattern in patterns)


@dataclass(frozen=True)
class ExtStat:

    ext: str
    count: int
    total_size: int


@dataclass(frozen=True)
class Report:

    file_count: int
    total_size: int
    skipped: int
    ignored_dirs: int
    by_ext: tuple[ExtStat, ...]
    largest: tuple[FileEntry, ...]


def summarize(
    files: tuple[FileEntry, ...] | list[FileEntry],
    *,
    skipped: int = 0,
    ignored_dirs: int = 0,
    top: int = 8,
    largest_n: int = 5,
) -> Report:
    total_size = 0
    buckets: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # ext -> [个数, 字节数]

    for f in files:
        total_size += f.size
        bucket = buckets[f.ext]
        bucket[0] += 1
        bucket[1] += f.size

    by_ext = tuple(
        sorted(
            (ExtStat(ext=ext, count=c, total_size=s) for ext, (c, s) in buckets.items()),
            # 先按占的空间排，一样大再按名字排（保证稳定）
            key=lambda stat: (-stat.total_size, stat.ext),
        )[:top]
    )

    largest = tuple(sorted(files, key=lambda f: (-f.size, str(f.path)))[:largest_n])

    return Report(
        file_count=len(files),
        total_size=total_size,
        skipped=skipped,
        ignored_dirs=ignored_dirs,
        by_ext=by_ext,
        largest=largest,
    )


@dataclass(frozen=True)
class DirStat:

    key: str  # 相对 root 的目录路径；空字符串 "" 表示 root 直属的文件
    total_size: int
    file_count: int


def child_totals(
    dir_totals: Mapping[str, tuple[int, int]], root: Path
) -> tuple[DirStat, ...]:
    root_key = str(root)
    # root 是 "C:\\" 这种盘符根时，它自己就以分隔符结尾，不能再补一个
    prefix = root_key if root_key.endswith(os.sep) else root_key + os.sep
    buckets: dict[str, list[int]] = defaultdict(lambda: [0, 0])

    for key, (size, count) in dir_totals.items():
        if key == root_key:
            name = ""  # 直接躺在 root 下的文件
        elif key.startswith(prefix):
            # 取 root 之后的第一段，就是它所在的"下一层目录"
            name = key[len(prefix) :].split(os.sep, 1)[0]
        else:
            continue  # 不属于这棵子树，跳过
        bucket = buckets[name]
        bucket[0] += size
        bucket[1] += count

    return tuple(
        sorted(
            (DirStat(key=k, total_size=s, file_count=c) for k, (s, c) in buckets.items()),
            key=lambda stat: (-stat.total_size, stat.key),
        )
    )


def dir_sizes(
    files: Sequence[FileEntry],
    root: Path,
    *,
    depth: int = 1,
) -> tuple[DirStat, ...]:
    if depth < 1:
        raise ValueError("depth 至少是 1")

    buckets: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # key -> [字节数, 个数]

    for f in files:
        try:
            rel = f.path.relative_to(root)
        except ValueError:
            # 不在 root 底下的文件不该出现在这里。真出现了也绝不能算进分母，
            # 否则百分比会超过 100% 而没人发现。
            continue
        dir_parts = rel.parts[:-1]  # 去掉文件名，只剩目录层级
        key = str(Path(*dir_parts[:depth])) if dir_parts else ""
        bucket = buckets[key]
        bucket[0] += f.size
        bucket[1] += 1

    return tuple(
        sorted(
            (DirStat(key=k, total_size=s, file_count=c) for k, (s, c) in buckets.items()),
            key=lambda stat: (-stat.total_size, stat.key),
        )
    )


@dataclass(frozen=True)
class DuplicateGroup:

    size: int  # 每个文件多大
    digest: str  # 全文 hash（为了让人能复核，也为了输出稳定）
    paths: tuple[Path, ...]  # 至少两个

    @property
    def wasted(self) -> int:
        return self.size * (len(self.paths) - 1)


def find_duplicates(
    files: Sequence[FileEntry],
    *,
    read_head: Callable[[Path, int], bytes],
    read_hash: Callable[[Path], str],
    head_size: int = 4096,
    min_size: int = 1,
) -> tuple[DuplicateGroup, ...]:
    # ── 第一层：按大小分组，只留下"同大小且至少两个"的
    by_size: dict[int, list[FileEntry]] = defaultdict(list)
    for f in files:
        if f.size >= min_size:
            by_size[f.size].append(f)
    size_dupes = [group for group in by_size.values() if len(group) > 1]

    if not size_dupes:
        return ()

    # ── 第二层：同大小的一组里，再按"开头 N 字节"分组
    by_head: dict[tuple[int, bytes], list[FileEntry]] = defaultdict(list)
    for group in size_dupes:
        for f in group:
            try:
                head = read_head(f.path, head_size)
            except OSError:
                # 读不了就跳过。文件可能刚被删、或者没权限。
                # 宁可少报一组，也不能让整次扫描崩掉。
                continue
            by_head[(f.size, head)].append(f)

    # ── 第三层：只剩少数候选，才算全文 hash
    result: list[DuplicateGroup] = []
    for group in by_head.values():
        if len(group) < 2:
            continue
        by_hash: dict[str, list[FileEntry]] = defaultdict(list)
        for f in group:
            try:
                digest = read_hash(f.path)
            except OSError:
                continue
            by_hash[digest].append(f)

        for digest, same in by_hash.items():
            if len(same) >= 2:
                result.append(
                    DuplicateGroup(
                        size=same[0].size,
                        digest=digest,
                        paths=tuple(sorted(f.path for f in same)),
                    )
                )

    # 可回收空间最大的排前面 —— 那才是用户真正想看的
    return tuple(sorted(result, key=lambda g: (-g.wasted, str(g.paths[0]))))


@dataclass(frozen=True)
class AppMatch:

    app: AppRecord
    location: AppLocation
    confidence: str  # "高" 或 "中"


def match_app(path: Path, apps: Sequence[AppRecord]) -> AppMatch | None:
    best: AppMatch | None = None
    best_depth = -1

    for app in apps:
        for location in app.locations:
            if path != location.path and not path.is_relative_to(location.path):
                continue
            depth = len(location.path.parts)
            if depth <= best_depth:
                continue
            best_depth = depth
            confidence = "高" if location.source == "InstallLocation" else "中"
            best = AppMatch(app=app, location=location, confidence=confidence)

    return best


def distinct_locations(app: AppRecord, chosen: AppLocation) -> tuple[AppLocation, ...]:
    return tuple(
        location
        for location in app.locations
        if location != chosen and not location.path.is_relative_to(chosen.path)
    )

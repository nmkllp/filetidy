"""纯逻辑层：这里只有计算和判断，没有硬盘、没有屏幕、没有网络。

这是整个项目里最值钱的一层。它的特点是：
  输入 = 普通数据，输出 = 普通数据，同样的输入永远得到同样的输出。

好处很实在 —— 因为它不碰任何外部东西，所以测试起来极其便宜：
不用造临时目录，不用 mock，直接喂几个 FileEntry 进去看结果对不对。

规矩：这个文件里不许出现 open()、print()、os.walk()、requests。
一旦发现自己想写这些，说明那段代码该去 fs.py 或 cli.py。
"""

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
    """这个目录名是否应该被跳过。

    规则是 shell 通配符（`*` `?` `[abc]`），不是正则 —— 对人的友好度高得多，
    `node_modules`、`*.tmp`、`build*` 这类写法符合直觉。

    比较前两边都转小写：Windows 的文件系统本来就不区分大小写，
    `Node_Modules` 和 `node_modules` 是同一个目录，规则不应该只认一种写法。
    """
    lowered = name.lower()
    return any(fnmatch.fnmatchcase(lowered, pattern.lower()) for pattern in patterns)


@dataclass(frozen=True)
class ExtStat:
    """某一种扩展名的汇总。"""

    ext: str
    count: int
    total_size: int


@dataclass(frozen=True)
class Report:
    """扫描报告：给"人"之前最后一步的纯数据形态。"""

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
    """把一堆文件事实压成一份报告。"""
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
    """一个目录（或"直接放在 root 下的那堆文件"）的汇总。"""

    key: str  # 相对 root 的目录路径；空字符串 "" 表示 root 直属的文件
    total_size: int
    file_count: int


def child_totals(
    dir_totals: Mapping[str, tuple[int, int]], root: Path
) -> tuple[DirStat, ...]:
    """按"下一层目录"聚合，用扫描时记下的每目录直属合计。

    遍历的是目录（数量约为文件的十分之一），不是文件。

    输入是 {目录路径字符串: (直属字节, 直属文件数)}，输出是 root 下面
    **每一层**的合计（含子孙）。
    """
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


def build_dir_totals(files: Sequence[FileEntry]) -> dict[str, tuple[int, int]]:
    """从文件列表反推"每目录直属合计"。

    正常流程用不到它（`fs.scan` 已经顺手记好了）。它是给两种情况用的：
    手上只有文件列表的场景，以及测试 —— 造数据时不必真的跑一遍扫盘。

    注意它是**慢路径**：每个文件都要做一次 Path 运算。
    热路径上别调它。
    """
    totals: dict[str, list[int]] = {}
    for f in files:
        key = str(f.path.parent)
        bucket = totals.get(key)
        if bucket is None:
            bucket = totals[key] = [0, 0]
        bucket[0] += f.size
        bucket[1] += 1
    return {key: (value[0], value[1]) for key, value in totals.items()}


def dir_sizes(
    files: Sequence[FileEntry],
    root: Path,
    *,
    depth: int = 1,
) -> tuple[DirStat, ...]:
    """按目录聚合大小，从大到小排好。

    depth=1 → 只看 root 的下一层（像资源管理器，但每一行带大小和占比）
    depth=2 → 再往下一层

    注意这个函数**完全不碰硬盘**：files 里已经有每个文件的大小和完整路径了，
    这里只是把"路径前缀"当分组键重新加一遍。这就是分层的回报 ——
    加一个新视图不需要重新扫盘。
    """
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
    """一组内容完全相同的文件。"""

    size: int  # 每个文件多大
    digest: str  # 全文 hash（为了让人能复核，也为了输出稳定）
    paths: tuple[Path, ...]  # 至少两个

    @property
    def wasted(self) -> int:
        """除了留一份，其余都是白占的空间。"""
        return self.size * (len(self.paths) - 1)


def find_duplicates(
    files: Sequence[FileEntry],
    *,
    read_head: Callable[[Path, int], bytes],
    read_hash: Callable[[Path], str],
    head_size: int = 4096,
    min_size: int = 1,
) -> tuple[DuplicateGroup, ...]:
    """找出内容完全相同的文件。分三层筛，从便宜到贵。

    **为什么不能直接对每个文件算 hash**：20 万个文件各算一次 SHA-256，
    要读几百 GB、跑几十分钟。而九成九的文件根本不需要读。

    第一层 · 按大小分组
        大小不同 → 内容必然不同。只看元数据，不用读一个字节，就能排除绝大多数。
    第二层 · 比前 head_size 字节
        同样大小的文件其实很多（两个 1 KB 的配置文件、两个 100 MB 但内容不同的视频）。
        读 4 KB 就能把绝大多数分开，代价极小。
    第三层 · 全文 hash
        只剩极少数候选，这时候读全文才划算。

    **注意这个函数不认识硬盘。** 「怎么读文件头」「怎么算 hash」是作为参数传进来的。
    这样测试里可以传两个假的函数，一行真实文件都不用造 —— 而它仍然是纯逻辑。
    """
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
    """「这个路径属于哪个应用」的结论，以及为什么这么判。"""

    app: AppRecord
    location: AppLocation
    confidence: str  # "高" 或 "中"


def match_app(path: Path, apps: Sequence[AppRecord]) -> AppMatch | None:
    """给一个路径，猜它属于哪个已登记的应用。

    规则：看哪个应用的某个线索目录是它的祖先。**取最长的那一个**——
    因为 `C:\\Program Files` 和 `C:\\Program Files\\Git` 都是 `git.exe` 的祖先，
    但后者显然更准确。

    返回 None 就是"不知道"。这是正常的、也是诚实的结果：绿色软件、
    通过游戏平台装的游戏、很多现代应用根本不往注册表写路径。
    """
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
    """同一个应用登记的多条线索里，哪些算"另外的位置"。

    落在已匹配目录**内部**的不算 —— 那大概率只是安装目录里的某个子文件夹
    （Git 的图标在 `ucrt64\\share\\git` 里），列出来只会让人困惑。

    父目录要保留：`C:\\Program Files\\obs-studio` 相对于 `...\\bin\\64bit`
    是有用信息，它告诉你这个应用的根在哪。
    """
    return tuple(
        location
        for location in app.locations
        if location != chosen and not location.path.is_relative_to(chosen.path)
    )

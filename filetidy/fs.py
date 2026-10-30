from __future__ import annotations

import hashlib
import os
from collections.abc import Callable, Iterable
from pathlib import Path

from .core import DEFAULT_IGNORED_DIRS, is_ignored
from .models import FileEntry, RawScan, ScanProgress


class ScanCancelled(Exception):
    pass


def scan(
    root: str | Path,
    *,
    exclude: Iterable[str] = (),
    use_default_ignores: bool = True,
    progress: Callable[[ScanProgress], None] | None = None,
    progress_every: int = 500,
    cancel: Callable[[], bool] | None = None,
) -> RawScan:
    root = Path(root)
    if not root.exists():
        raise FileNotFoundError(f"路径不存在: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"这不是目录: {root}")
    # 统一成绝对路径。否则 scan(".") 产出的 entry 还带着 "." 前缀，
    # 下游想算"这个文件在 root 下面哪一层"就会很别扭。
    root = root.resolve()

    patterns: tuple[str, ...] = tuple(exclude)
    if use_default_ignores:
        patterns = DEFAULT_IGNORED_DIRS + patterns

    files: list[FileEntry] = []
    # 目录 -> [直属字节数, 直属文件数]。用字符串做键，不碰 Path：
    # 这里是每个文件都会走一遍的热路径，一次 Path 运算几十微秒，乘几十万次就是灾难。
    dir_totals: dict[str, list[int]] = {}
    skipped = 0
    ignored_dirs = 0
    scanned_dirs = 0
    pending: list[str] = [str(root)]

    while pending:
        # 每处理一个目录检查一次"要不要停"。放在这里而不是每个文件里：
        # 目录数远小于文件数，检查成本可以忽略，粒度也够（扫 C 盘也就差几十毫秒）。
        if cancel is not None and cancel():
            raise ScanCancelled()

        current = pending.pop()
        scanned_dirs += 1
        try:
            # scandir 把这个目录里所有条目的名字和属性一次性带回来。
            # with 是为了及时关掉底层的目录句柄 —— 忘了它在大目录树上会耗尽句柄。
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        # follow_symlinks=False 全部走属性缓存，不再碰磁盘
                        if entry.is_dir(follow_symlinks=False):
                            if is_ignored(entry.name, patterns):
                                ignored_dirs += 1
                            else:
                                pending.append(entry.path)
                        elif entry.is_symlink():
                            skipped += 1
                        elif entry.is_file(follow_symlinks=False):
                            size = entry.stat(follow_symlinks=False).st_size
                            files.append(
                                FileEntry(
                                    path=Path(entry.path),
                                    size=size,
                                    ext=os.path.splitext(entry.name)[1].lower(),
                                )
                            )
                            # 顺手记进这个目录的直属合计（一次字典操作，可以忽略）
                            bucket = dir_totals.get(current)
                            if bucket is None:
                                bucket = dir_totals[current] = [0, 0]
                            bucket[0] += size
                            bucket[1] += 1
                            # 每攒够一批就报一次进度。不能每个文件都报 ——
                            # 20 万次回调会把省下来的时间全赔进去。
                            if progress is not None and len(files) % progress_every == 0:
                                progress(
                                    ScanProgress(
                                        files=len(files),
                                        dirs=scanned_dirs,
                                        current=Path(current),
                                    )
                                )
                        else:
                            # 设备文件、命名管道之类，不是我们要数的东西
                            skipped += 1
                    except OSError:
                        # 单个条目读不了（权限、被删、路径太长），跳过就好，
                        # 别让它把整次扫描带崩
                        skipped += 1
        except OSError:
            # 这个目录本身打不开：记账，继续下一个
            skipped += 1

    # 排一次序，让输出稳定 —— 不然遍历顺序每次都不一样，没法对比、没法测
    files.sort(key=lambda entry: str(entry.path))
    if progress is not None:
        # 最后再报一次，保证界面能收到"扫完了"的准确数字
        progress(ScanProgress(files=len(files), dirs=scanned_dirs, current=root))
    return RawScan(
        files=tuple(files),
        skipped=skipped,
        ignored_dirs=ignored_dirs,
        root=root,
        dir_totals={key: (value[0], value[1]) for key, value in dir_totals.items()},
    )


def read_head(path: Path, n: int) -> bytes:
    with open(path, "rb") as handle:
        return handle.read(n)


def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()

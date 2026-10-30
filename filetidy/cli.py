from __future__ import annotations

import argparse
import sys
from pathlib import Path
from unicodedata import east_asian_width

from . import registry
from .core import (
    AppMatch,
    DirStat,
    DuplicateGroup,
    Report,
    dir_sizes,
    distinct_locations,
    find_duplicates,
    match_app,
    summarize,
)
from .fs import read_head, scan, sha256_file
from .models import AppRecord, RawScan, ScanProgress


def human_size(n: int) -> str:
    if n < 0:
        raise ValueError("大小不能是负数")
    units = ("B", "KB", "MB", "GB", "TB", "PB")
    value = float(n)
    i = 0
    while value >= 1024 and i < len(units) - 1:
        value /= 1024
        i += 1
    if i == 0:
        return f"{n} B"
    return f"{value:.1f} {units[i]}"


def _width(text: str) -> int:
    return sum(2 if east_asian_width(ch) in "WF" else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _width(text))


def render(report: Report, root: Path) -> str:
    lines = [
        f"扫描目录: {root}",
        f"文件数: {report.file_count}    总大小: {human_size(report.total_size)}",
    ]
    if report.ignored_dirs:
        lines.append(f"已忽略: {report.ignored_dirs} 个目录（.git / node_modules 等，见 --help）")
    if report.skipped:
        lines.append(f"跳过: {report.skipped} 项（没权限或符号链接，没算进上面的数字）")
    lines.append("")

    if not report.by_ext:
        lines.append("这个目录是空的。")
        return "\n".join(lines)

    lines.append("按类型（占空间最多的在前）:")
    labels = [stat.ext or "(无扩展名)" for stat in report.by_ext]
    w_label = max(_width(label) for label in labels)
    w_count = max(len(str(stat.count)) for stat in report.by_ext)
    w_size = max(len(human_size(stat.total_size)) for stat in report.by_ext)

    for label, stat in zip(labels, report.by_ext):
        size_text = human_size(stat.total_size)
        lines.append(
            f"  {_pad(label, w_label)}  {str(stat.count):>{w_count}} 个  {size_text:>{w_size}}"
        )

    lines.append("")
    lines.append("最大的文件:")
    if not report.largest:
        lines.append("  (无)")
    for f in report.largest:
        lines.append(f"  {human_size(f.size):>9}  {f.path}")

    return "\n".join(lines)


def _bar(fraction: float, width: int = 12) -> str:
    filled = int(round(max(0.0, min(1.0, fraction)) * width))
    return "█" * filled + "░" * (width - filled)


def render_dirs(stats: tuple[DirStat, ...], raw: RawScan, *, top: int) -> str:
    total = sum(stat.total_size for stat in stats)
    file_count = sum(stat.file_count for stat in stats)

    lines = [
        f"扫描目录: {raw.root}",
        f"总大小: {human_size(total)}    文件数: {file_count}",
    ]
    if raw.ignored_dirs:
        lines.append(f"已忽略: {raw.ignored_dirs} 个目录（.git / node_modules 等）")
    if raw.skipped:
        lines.append(f"跳过: {raw.skipped} 项（没权限或符号链接，没算进上面的数字）")
    lines.append("")

    if not stats:
        lines.append("这个目录是空的。")
        return "\n".join(lines)

    shown = stats[:top]
    lines.append(f"按子目录（共 {len(stats)} 项，显示前 {len(shown)}）:")
    w_size = max(len(human_size(stat.total_size)) for stat in shown)

    for stat in shown:
        fraction = (stat.total_size / total) if total else 0.0
        name = stat.key or "(直接放在此目录的文件)"
        lines.append(
            f"  {human_size(stat.total_size):>{w_size}}  {_bar(fraction)}"
            f"  {fraction * 100:>5.1f}%  {name}"
        )

    biggest = shown[0].key
    if biggest:
        lines.append("")
        lines.append("想看某一项里面是什么，把它的路径接到命令后面再跑一次:")
        lines.append(f'  py -m filetidy dirs "{raw.root / biggest}"')

    return "\n".join(lines)


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def render_dups(
    groups: tuple[DuplicateGroup, ...], raw: RawScan, *, top: int, min_size: int
) -> str:
    lines = [
        f"扫描目录: {raw.root}",
        f"文件数: {len(raw.files)}    最小判定大小: {human_size(min_size)}",
    ]
    if raw.ignored_dirs:
        lines.append(f"已忽略: {raw.ignored_dirs} 个目录（.git / node_modules 等）")
    if raw.skipped:
        lines.append(f"跳过: {raw.skipped} 项（没权限或符号链接）")
    lines.append("")

    if not groups:
        lines.append("没有找到内容完全相同的文件。")
        return "\n".join(lines)

    reclaimable = sum(group.wasted for group in groups)
    involved = sum(len(group.paths) for group in groups)
    lines.append(
        f"找到 {len(groups)} 组重复，涉及 {involved} 个文件，可回收 {human_size(reclaimable)}"
    )
    lines.append("")

    shown = groups[:top]
    lines.append(f"重复组（按可回收空间排序，显示前 {len(shown)} 组）:")
    for group in shown:
        lines.append(
            f"  {human_size(group.size):>9} ×{len(group.paths)}"
            f"   可回收 {human_size(group.wasted)}"
        )
        for path in group.paths:
            lines.append(f"      {_relative(path, raw.root)}")
        # 给出 hash 前 12 位：用户想人工核对时有个抓手
        lines.append(f"      sha256:{group.digest[:12]}…")

    if len(groups) > top:
        lines.append("")
        lines.append(f"（还有 {len(groups) - top} 组没显示，用 --top 调大）")

    return "\n".join(lines)


def _add_path_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path", nargs="?", default=".", help="要扫描的目录（默认: 当前目录）")
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="NAME",
        help="额外忽略的目录名，支持通配符如 '*.tmp'；可重复写多次",
    )
    parser.add_argument(
        "--no-default-ignores",
        action="store_true",
        help="关掉默认忽略表（.git / node_modules / __pycache__ 等），全部照扫",
    )
    parser.add_argument(
        "--progress",
        action="store_true",
        help="扫描时在 stderr 显示进度（大目录会等很久，有这个才知道没卡死）",
    )


def _progress_reporter():

    def report(point: ScanProgress) -> None:
        print(
            f"\r  已扫描 {point.files:,} 个文件 / {point.dirs:,} 个目录",
            end="",
            file=sys.stderr,
            flush=True,
        )

    return report


def _scan_from_args(args: argparse.Namespace) -> RawScan | None:
    progress = _progress_reporter() if getattr(args, "progress", False) else None
    try:
        raw = scan(
            Path(args.path),
            exclude=args.exclude,
            use_default_ignores=not args.no_default_ignores,
            progress=progress,
        )
    except (FileNotFoundError, NotADirectoryError) as exc:
        if progress is not None:
            print(file=sys.stderr)  # 补一个换行，别让错误信息和进度挤在同一行
        print(f"错误: {exc}", file=sys.stderr)
        return None
    if progress is not None:
        print(file=sys.stderr)
    return raw


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tidy",
        description="看清一个目录到底被什么占满了。只读，不删任何东西。",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="按文件类型汇总 + 列出最大文件")
    _add_path_options(p_scan)
    p_scan.add_argument("--top", type=int, default=8, help="列出多少种扩展名（默认: 8）")
    p_scan.add_argument("--largest", type=int, default=5, help="列出多少个最大文件（默认: 5）")

    p_dirs = sub.add_parser("dirs", help="按子目录汇总占用（哪个文件夹最大）")
    _add_path_options(p_dirs)
    p_dirs.add_argument("--top", type=int, default=12, help="列出多少个子目录（默认: 12）")
    p_dirs.add_argument(
        "--depth",
        type=int,
        default=1,
        help="聚到第几层：1=只看下一层（默认），2=再看深一层",
    )

    p_dups = sub.add_parser("dups", help="找出内容完全相同的文件")
    _add_path_options(p_dups)
    p_dups.add_argument("--top", type=int, default=10, help="显示多少组（默认: 10）")
    p_dups.add_argument(
        "--min-size",
        type=int,
        default=1,
        help="小于这个字节数的文件不参与比对（默认: 1，也就是跳过空文件）",
    )
    p_dups.add_argument(
        "--head-bytes",
        type=int,
        default=4096,
        help="第二层比对读多少字节（默认: 4096）",
    )

    p_who = sub.add_parser(
        "who", help="这个路径属于哪个应用（读注册表，仅 Windows）"
    )
    p_who.add_argument("path", help="要查的路径")

    p_gui = sub.add_parser("gui", help="打开图形界面（需要 PySide6）")
    p_gui.add_argument("path", nargs="?", default=".", help="启动时先展示这个目录")

    return parser


def render_who(
    target: Path, match: AppMatch | None, apps: tuple[AppRecord, ...]
) -> str:
    lines = [f"目标: {target}", ""]

    if match is None:
        # 「不知道」是一个正当结果，而且要说清楚为什么不知道。
        with_location = sum(1 for app in apps if app.locations)
        lines.append("没有找到归属。")
        lines.append("")
        lines.append(
            f"已检查 {len(apps)} 个登记过的程序，其中 {with_location} 个写了安装位置。"
        )
        lines.append("剩下的在注册表里没有路径 —— 绿色软件、游戏平台托管的游戏、")
        lines.append("以及一部分现代应用都是这样，凭注册表认不出来。")
        return "\n".join(lines)

    app = match.app
    lines.append(f"属于: {app.name}")
    if app.publisher:
        lines.append(f"发行方: {app.publisher}")
    lines.append(f"可信度: {match.confidence}（依据是 {match.location.source}）")
    lines.append(f"依据路径: {match.location.path}")
    lines.append(f"注册表: {app.registry_key}")

    others = distinct_locations(app, match.location)
    if others:
        lines.append("")
        lines.append("这个应用还登记了别的位置:")
        for loc in others:
            lines.append(f"  {loc.path}    ({loc.source})")

    return "\n".join(lines)


def _cmd_scan(args: argparse.Namespace) -> int:
    if args.top < 1 or args.largest < 1:
        print("错误: --top 和 --largest 必须是正整数", file=sys.stderr)
        return 2

    raw = _scan_from_args(args)
    if raw is None:
        return 1

    report = summarize(
        raw.files,
        skipped=raw.skipped,
        ignored_dirs=raw.ignored_dirs,
        top=args.top,
        largest_n=args.largest,
    )
    print(render(report, raw.root))
    return 0


def _cmd_dirs(args: argparse.Namespace) -> int:
    if args.top < 1 or args.depth < 1:
        print("错误: --top 和 --depth 必须是正整数", file=sys.stderr)
        return 2

    raw = _scan_from_args(args)
    if raw is None:
        return 1

    stats = dir_sizes(raw.files, raw.root, depth=args.depth)
    print(render_dirs(stats, raw, top=args.top))
    return 0


def _cmd_dups(args: argparse.Namespace) -> int:
    if args.top < 1 or args.head_bytes < 1 or args.min_size < 0:
        print(
            "错误: --top 和 --head-bytes 必须是正整数，--min-size 不能是负数",
            file=sys.stderr,
        )
        return 2

    raw = _scan_from_args(args)
    if raw is None:
        return 1

    # 注意这里把「怎么读」作为参数传进去：core 依然不认识硬盘
    groups = find_duplicates(
        raw.files,
        read_head=read_head,
        read_hash=sha256_file,
        head_size=args.head_bytes,
        min_size=args.min_size,
    )
    print(render_dups(groups, raw, top=args.top, min_size=args.min_size))
    return 0


def _cmd_who(args: argparse.Namespace) -> int:
    if not registry.is_supported():
        print(
            "错误: who 需要读 Windows 注册表，这个平台上用不了", file=sys.stderr
        )
        return 1

    # 路径可能已经不存在了（用户想知道"这个目录原本是谁的"），
    # 所以用 strict=False，解析不了就按原样比。
    target = Path(args.path)
    try:
        target = target.resolve(strict=False)
    except OSError:
        pass

    if not target.exists():
        print(f"注意: 路径不存在，按字面路径匹配: {target}", file=sys.stderr)

    apps = registry.list_apps()
    match = match_app(target, apps)
    print(render_who(target, match, apps))
    return 0


def _cmd_gui(args: argparse.Namespace) -> int:
    # 延迟导入：CLI 的四个命令零依赖，不能因为 GUI 用了第三方包就让它们装不上。
    # 没装 PySide6 的人敲 `tidy scan .` 依然正常，只有敲 `tidy gui` 才会看到这条提示。
    try:
        from .gui import main as gui_main
    except ImportError as exc:
        print("错误: gui 需要 PySide6，先装：py -m pip install pyside6", file=sys.stderr)
        print(f"  ({exc})", file=sys.stderr)
        return 1
    return gui_main([sys.argv[0], args.path])


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "scan":
        return _cmd_scan(args)
    if args.command == "dirs":
        return _cmd_dirs(args)
    if args.command == "dups":
        return _cmd_dups(args)
    if args.command == "who":
        return _cmd_who(args)
    if args.command == "gui":
        return _cmd_gui(args)

    print(f"错误: 不认识的命令 {args.command!r}", file=sys.stderr)
    return 2

"""造一份可重复的小样本目录，用来快速验证工具的行为。

为什么需要它：

1. 扫真盘一次要 70 秒。改一行代码、等 70 秒、再看结果，一天就废了。
2. 真盘的内容每天都在变，没法拿来做断言（"这个文件夹应该是 50.2 GB"明天就不成立了）。
3. 下一步要做重复文件检测，需要一个**已知有哪些重复**的数据集来验证。

这份样本是确定的：结构、内容、大小每次生成都一样。所以它既能当演示，
也能当断言依据。数据本身不提交（在 .gitignore 里），提交的是这个生成脚本。

用法:
    py tools/make_sample.py                 # 重建 samples/demo
    py -m filetidy dirs samples/demo        # 看效果
"""

from __future__ import annotations

import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
TARGET = HERE / "samples" / "demo"

KB = 1024


def fill(n: int, seed: int = 0) -> bytes:
    """生成 n 字节的确定内容。同一个 seed 永远得到同一串字节，所以"重复文件"是真的重复。"""
    pattern = bytes(((seed * 31 + i) % 251) for i in range(97))
    return (pattern * (n // len(pattern) + 1))[:n]


# 每个文件：(相对路径, 字节数, 内容种子)
# 种子相同 = 内容相同 = 真正的重复文件
FILES: list[tuple[str, int, int]] = [
    ("项目A/设计稿.md", 1 * KB, 7),
    ("项目A/assets/logo.bin", 8 * KB, 11),
    ("项目A/assets/背景图.png", 4 * KB, 13),
    # 下面两个和 项目A/设计稿.md 内容完全一致（种子都是 7）—— 三份重复
    ("项目B/设计稿-副本.md", 1 * KB, 7),
    ("归档/设计稿-旧版.md", 1 * KB, 7),
    ("视频/样片.mp4", 1000 * KB, 21),
    ("散落的文件.txt", 200, 31),
    ("无扩展名文件", 50, 37),
    # 这个目录名在默认忽略表里，应该一个字节都不被扫到
    ("项目A/__pycache__/junk.pyc", 5 * KB, 41),
]

# 空目录也要造出来：有人会用"空文件夹的数量"来判断是否清理干净
EMPTY_DIRS: list[str] = ["空目录", "项目A/空子目录"]


def build(target: Path = TARGET) -> Path:
    """在 target 处重建样本。返回真正的目录路径，方便调用方接着用它。

    注意这里不打印任何东西。打印是调用方的责任 —— 否则测试里每调一次
    就刷一遍屏幕，把真正的失败信息淹掉。（和 core.py 里「不许 print」是同一条道理）
    """
    target = Path(target)
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)

    for rel, size, seed in FILES:
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(fill(size, seed))

    for rel in EMPTY_DIRS:
        (target / rel).mkdir(parents=True, exist_ok=True)

    return target


def describe(target: Path) -> str:
    """生成给人看的说明文字。纯函数，只返回字符串。"""
    total = sum(size for _, size, _ in FILES)
    return "\n".join(
        [
            f"已生成: {target}",
            f"  {len(FILES)} 个文件, {len(EMPTY_DIRS)} 个空目录, 共 {total / KB:.0f} KB",
            "  其中 3 份 1 KB 的文件内容完全相同（种子都是 7）",
            "  另有 1 个 __pycache__ 目录，默认应被忽略",
        ]
    )


if __name__ == "__main__":
    print(describe(build()))


if __name__ == "__main__":
    build()

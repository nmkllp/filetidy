"""重复文件检测的测试。

注意：这个文件里**一个真实文件都没有**。

`read_head` 和 `read_hash` 是作为参数传进 `find_duplicates` 的，所以测试里可以
塞两个假函数进去 —— 核心逻辑照样被完整地测了，而且跑得飞快。

如果当初在 `core` 里直接 `open()`，这里每一条测试都得先造真文件。
这就是"把外部能力交给调用方"这个设计的回报。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from filetidy.core import find_duplicates
from filetidy.models import FileEntry

ROOT = Path("C:/root")


def entry(rel: str, size: int) -> FileEntry:
    path = ROOT / rel
    return FileEntry(path=path, size=size, ext=path.suffix.lower())


def readers(heads: dict[str, bytes], hashes: dict[str, str]):
    """按文件名查表的假读函数。"""

    def read_head(path: Path, n: int) -> bytes:
        return heads[path.name][:n]

    def read_hash(path: Path) -> str:
        return hashes[path.name]

    return read_head, read_hash


class TestFindDuplicates(unittest.TestCase):
    def test_identical_content_is_found(self):
        files = [entry("a.bin", 100), entry("b.bin", 100)]
        read_head, read_hash = readers(
            {"a.bin": b"x" * 100, "b.bin": b"x" * 100},
            {"a.bin": "same", "b.bin": "same"},
        )
        groups = find_duplicates(files, read_head=read_head, read_hash=read_hash)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0].paths), 2)

    def test_different_sizes_never_read_content(self):
        """第一层就该把它们分开，一个字节都不该读。"""

        def boom(*args, **kwargs):
            raise AssertionError("大小不同时不该读文件内容")

        groups = find_duplicates(
            [entry("a.bin", 100), entry("b.bin", 200)],
            read_head=boom,
            read_hash=boom,
        )
        self.assertEqual(groups, ())

    def test_same_size_different_head_is_rejected(self):
        """第二层：大小一样但开头不同，也不该走到算 hash 那一步。"""
        files = [entry("a.bin", 100), entry("b.bin", 100)]
        read_head, _ = readers(
            {"a.bin": b"A" * 100, "b.bin": b"B" * 100}, {"a.bin": "", "b.bin": ""}
        )

        def no_hash(path: Path) -> str:
            raise AssertionError("头不同就不该算全文 hash")

        groups = find_duplicates(files, read_head=read_head, read_hash=no_hash)
        self.assertEqual(groups, ())

    def test_same_size_and_head_but_different_hash(self):
        """第三层才是最终裁决。前两层都过得去，hash 不同就不能算重复。"""
        files = [entry("a.bin", 100), entry("b.bin", 100)]
        read_head, read_hash = readers(
            {"a.bin": b"x" * 100, "b.bin": b"x" * 100},
            {"a.bin": "hash-1", "b.bin": "hash-2"},
        )
        groups = find_duplicates(files, read_head=read_head, read_hash=read_hash)
        self.assertEqual(groups, ())

    def test_wasted_space_counts_all_but_one_copy(self):
        files = [entry(f"f{i}.bin", 100) for i in range(3)]
        read_head, read_hash = readers(
            {f"f{i}.bin": b"x" * 100 for i in range(3)},
            {f"f{i}.bin": "d" for i in range(3)},
        )
        groups = find_duplicates(files, read_head=read_head, read_hash=read_hash)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].wasted, 200)  # 3 份 100B，留一份，白占 200

    def test_unreadable_file_is_skipped_not_fatal(self):
        files = [entry(f"f{i}.bin", 100) for i in range(3)]

        def read_head(path: Path, n: int) -> bytes:
            if path.name == "f2.bin":
                raise OSError("文件被占用")
            return b"x" * 100

        def read_hash(path: Path) -> str:
            return "d"

        groups = find_duplicates(files, read_head=read_head, read_hash=read_hash)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0].paths), 2)  # f2 被跳过，剩下两个

    def test_min_size_filters_small_files(self):
        files = [entry("a.bin", 5), entry("b.bin", 5)]
        read_head, read_hash = readers(
            {"a.bin": b"x" * 5, "b.bin": b"x" * 5}, {"a.bin": "d", "b.bin": "d"}
        )
        # 默认 min_size=1，只跳过空文件，5 字节的文件照算
        self.assertEqual(
            len(find_duplicates(files, read_head=read_head, read_hash=read_hash)), 1
        )
        # 门槛抬到 6 字节，这两个就不参与了
        self.assertEqual(
            find_duplicates(files, read_head=read_head, read_hash=read_hash, min_size=6),
            (),
        )

    def test_empty_files_are_ignored_by_default(self):
        files = [entry("a.txt", 0), entry("b.txt", 0)]
        read_head, read_hash = readers(
            {"a.txt": b"", "b.txt": b""}, {"a.txt": "d", "b.txt": "d"}
        )
        self.assertEqual(
            find_duplicates(files, read_head=read_head, read_hash=read_hash), ()
        )

    def test_bigger_waste_is_sorted_first(self):
        files = [
            entry("small1.bin", 10),
            entry("small2.bin", 10),
            entry("big1.bin", 1000),
            entry("big2.bin", 1000),
        ]
        read_head, read_hash = readers(
            {f: b"x" * 10 for f in ("small1.bin", "small2.bin")}
            | {f: b"y" * 1000 for f in ("big1.bin", "big2.bin")},
            {f: "s" for f in ("small1.bin", "small2.bin")}
            | {f: "b" for f in ("big1.bin", "big2.bin")},
        )
        groups = find_duplicates(files, read_head=read_head, read_hash=read_hash)
        self.assertEqual([g.size for g in groups], [1000, 10])

    def test_no_duplicates_returns_empty(self):
        files = [entry("a.bin", 10), entry("b.bin", 20), entry("c.bin", 30)]
        read_head, read_hash = readers({}, {})
        self.assertEqual(
            find_duplicates(files, read_head=read_head, read_hash=read_hash), ()
        )


if __name__ == "__main__":
    unittest.main()

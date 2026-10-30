from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath

from .models import AppLocation, AppRecord

# 卸载信息藏在三个地方，必须都翻一遍，少一个就漏一批应用：
#   HKLM           64 位程序
#   HKLM WOW6432   32 位程序（在 64 位系统上被重定向到这里）
#   HKCU           只给当前用户装的程序
_HIVES: tuple[tuple[str, int, str], ...] = (
    ("HKLM", 0x80000002, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    (
        "HKLM",
        0x80000002,
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
    ),
    ("HKCU", 0x80000001, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
)

# 这些后缀说明这一串是"文件"而不是"目录"。用它判断比"有没有后缀"更准确 ——
# 有些安装目录本身带点（比如 C:\Program Files\node.js），不能被误判成文件。
_FILE_EXTS: tuple[str, ...] = (".exe", ".ico", ".dll")


def is_supported() -> bool:
    try:
        import winreg  # noqa: F401
    except ImportError:
        return False
    return True


def _broad_roots() -> tuple[str, ...]:
    env = os.environ
    raw = [
        env.get("ProgramFiles"),
        env.get("ProgramFiles(x86)"),
        env.get("ProgramW6432"),
        env.get("ProgramData"),
        env.get("SystemRoot"),
        env.get("SystemDrive", "C:") + "\\",
        env.get("LOCALAPPDATA"),
        env.get("APPDATA"),
        env.get("USERPROFILE"),
    ]
    return tuple(os.path.normcase(r) for r in raw if r)


def _is_too_broad(path: Path) -> bool:
    if len(path.parts) <= 1:  # 盘符根，比如 C:\
        return True
    return os.path.normcase(str(path)) in _broad_roots()


def _extract_dir(value: str) -> Path | None:
    text = value.strip()
    if not text:
        return None

    if text.startswith('"'):
        end = text.find('"', 1)
        if end == -1:
            return None
        raw = text[1:end]
    else:
        lower = text.lower()
        raw = text
        for ext in _FILE_EXTS:
            at = lower.find(ext)
            if at != -1:
                raw = text[: at + len(ext)]
                break

    # 去掉图标序号：git.ico,0 -> git.ico
    head, sep, tail = raw.rpartition(",")
    if sep and tail.isdigit():
        raw = head

    # 用 PureWindowsPath 而不是 Path：注册表里存的永远是 Windows 路径，
    # 而 Path 的行为取决于当前系统。用 PureWindowsPath，这段解析就能
    # 在任何平台上被测试（CI 里的 Linux 也能跑）。
    candidate = PureWindowsPath(raw)
    if not candidate.is_absolute():
        return None
    # 指向的是文件的话，取它所在目录
    if candidate.suffix.lower() in _FILE_EXTS:
        candidate = candidate.parent
    return Path(str(candidate))


def _value(key, name: str) -> str:
    import winreg

    try:
        value, _kind = winreg.QueryValueEx(key, name)
    except OSError:
        return ""
    return value if isinstance(value, str) else ""


def _locations_from(install_location: str, icon: str, uninstall: str) -> tuple[AppLocation, ...]:
    found: list[AppLocation] = []
    seen: set[str] = set()

    def add(raw: str, source: str) -> None:
        path = _extract_dir(raw) if raw else None
        if path is None:
            return
        if _is_too_broad(path):
            # 线索太宽，丢掉。宁可说"不知道"，也不要给一个错误答案。
            return
        key = str(path).lower()  # Windows 路径不区分大小写
        if key in seen:
            return
        seen.add(key)
        found.append(AppLocation(path=path, source=source))

    add(install_location, "InstallLocation")
    add(icon, "DisplayIcon")
    add(uninstall, "UninstallString")
    return tuple(found)


def list_apps() -> tuple[AppRecord, ...]:
    import winreg

    apps: list[AppRecord] = []

    for hive_name, hive, subkey in _HIVES:
        try:
            with winreg.OpenKey(hive, subkey) as root:
                count = winreg.QueryInfoKey(root)[0]
                for index in range(count):
                    try:
                        entry_name = winreg.EnumKey(root, index)
                        with winreg.OpenKey(root, entry_name) as entry:
                            display = _value(entry, "DisplayName").strip()
                            if not display:
                                # 大量系统补丁条目没有名字，不是我们要的
                                continue
                            locations = _locations_from(
                                _value(entry, "InstallLocation").strip(),
                                _value(entry, "DisplayIcon"),
                                _value(entry, "UninstallString"),
                            )
                            apps.append(
                                AppRecord(
                                    name=display,
                                    publisher=_value(entry, "Publisher").strip(),
                                    registry_key=f"{hive_name}\\{subkey}\\{entry_name}",
                                    locations=locations,
                                )
                            )
                    except OSError:
                        # 单个条目读不了就跳过，别让整次枚举失败
                        continue
        except OSError:
            # 整个 hive 打不开（比如权限不足），记不住就算了
            continue

    return tuple(sorted(apps, key=lambda app: app.name.lower()))

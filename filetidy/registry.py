"""Windows 注册表读取：找出「这台机器上装了什么，装在哪」。

这是第二个"外部世界"的入口，和 fs.py 平级。分开的理由很实在：
注册表是 Windows 独有的，而 fs.py 里的东西（os.scandir 之类）在哪都能跑。
如果混在一起，Linux 上连 import 都会失败，测试和 CI 全废。

所以原则是：**`import winreg` 只能出现在函数里面**，不能出现在模块顶层。
这样这个文件在所有平台上都能被 import，只有真的去读注册表时才会报错。
"""

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
    """这台机器上能不能读注册表。"""
    try:
        import winreg  # noqa: F401
    except ImportError:
        return False
    return True


def _broad_roots() -> tuple[str, ...]:
    """"公共根目录"清单：这些路径本身不能用来判断"子目录属于谁"。

    有些程序把 InstallLocation 直接写成 `C:\\Program Files`。如果把它当证据，
    Program Files 下**每一个**目录都会被判成"属于那个程序"——100% 的命中率，
    也 100% 是错的。假阳性比"认不出"更有害：用户会照着错误的结论去删东西。

    返回的是规范化后的小写字符串，方便直接比较。
    """
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
    """这条线索是不是宽到没有信息量。"""
    if len(path.parts) <= 1:  # 盘符根，比如 C:\
        return True
    return os.path.normcase(str(path)) in _broad_roots()


def _extract_dir(value: str) -> Path | None:
    """从 DisplayIcon / UninstallString 里抠出"安装目录"。

    这些值不是干净的路径，长这样：

        "C:\\Program Files\\Git\\git.ico",0
        "C:\\Program Files\\obs-studio\\bin\\64bit\\obs64.exe"
        "C:\\Program Files\\X\\uninst.exe" /S
        MsiExec.exe /X{6219E6AE-...}

    规则：
      - 有引号就取引号里的内容，并去掉 ",0" 这种图标序号后缀
      - 没引号就切到第一个 .exe / .ico / .dll 为止（**不能按空格切**：
        `C:\\Program Files\\Maxon Cinema 4D R22` 这种目录名里带空格，
        按空格切会变成 `C:\\Program`）
      - 抠不出以盘符开头的绝对路径，就返回 None（比如 MsiExec 那种）
    """
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
    """读一个字符串值，读不到就给空串（注册表里缺项是常态，不是异常）。"""
    import winreg

    try:
        value, _kind = winreg.QueryValueEx(key, name)
    except OSError:
        return ""
    return value if isinstance(value, str) else ""


def _locations_from(install_location: str, icon: str, uninstall: str) -> tuple[AppLocation, ...]:
    """把三条原始线索变成去重后的位置列表，顺序按可信度排。"""
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
    """读出所有登记过的程序。读不到的条目直接跳过，不抛异常。

    返回的顺序是稳定的（按名字排），这样输出不会每次都不一样。
    """
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

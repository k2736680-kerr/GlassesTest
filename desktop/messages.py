"""Short Chinese tips for desktop UI (never dump raw adb logs into labels)."""

from __future__ import annotations


def short_text(text: str, limit: int = 72) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)] + "…"


def humanize_adb_error(raw: object) -> str:
    text = str(raw or "").strip()
    if not text:
        return "ADB 异常，请点「重启 ADB」后重试"
    low = text.lower()

    if "cannot open" in low and "temp" in low and "adb" in low:
        return "ADB 启动失败：临时目录被占用。请关掉其他 adb/测试工具后再点「重启 ADB」"
    if "failed to start daemon" in low or "cannot connect to daemon" in low:
        return "ADB 服务未能启动。请关掉其他占用 ADB 的程序后再点「重启 ADB」"
    if "daemon not running" in low:
        return "ADB 服务未运行，请点「重启 ADB」"
    if "unauthorized" in low:
        return "手机未授权 USB 调试，请在手机上点允许后刷新"
    if "device offline" in low or "\toffline" in low:
        return "设备 offline，请换线/换口或重开 USB 调试后刷新"
    if "no devices/emulators found" in low:
        return "未发现设备，请检查 USB 调试与数据线"
    if "more than one device" in low:
        return "检测到多台设备，请先在下拉里选择目标手机"
    if "error: closed" in low or "connection reset" in low:
        return "ADB 连接已断开，请重新插线后点「刷新设备」"

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("*"):
            continue
        if line.lower().startswith("adb :") or "main.cpp" in line.lower():
            continue
        if "list of devices" in line.lower():
            continue
        return short_text(f"ADB 异常：{line}", 80)
    return "ADB 异常，请点「重启 ADB」后重试"


def humanize_ui_error(raw: object) -> str:
    text = str(raw or "").strip()
    if not text:
        return "操作失败"
    low = text.lower()
    if "adb" in low or "daemon" in low or "5037" in low:
        return humanize_adb_error(text)
    # Keep our own RuntimeError messages as-is when already Chinese/short.
    if any("\u4e00" <= ch <= "\u9fff" for ch in text) and "\n" not in text:
        return short_text(text, 80)
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), text)
    return short_text(first, 80)

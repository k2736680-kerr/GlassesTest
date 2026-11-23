"""Pull app-private media and read width/height."""
from __future__ import annotations

import logging
import os
import re
import subprocess
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("media_measure")


def _subprocess_hidden_kwargs() -> dict:
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    return {"startupinfo": startupinfo, "creationflags": flags}


def _path_has_non_ascii(path: str) -> bool:
    try:
        (path or "").encode("ascii")
        return False
    except UnicodeEncodeError:
        return True


def classify_pull_error(message: str, *, local_path: str = "", remote_path: str = "") -> str:
    text = (message or "").lower()
    if "not debuggable" in text:
        return "package_not_debuggable"
    if "failed to stat remote" in text or (
        "no such file or directory" in text and remote_path and remote_path.replace("\\", "/") in (message or "").replace("\\", "/")
        and "cannot create file/directory" not in text
    ):
        return "remote_missing"
    if "cannot create file/directory" in text:
        if _path_has_non_ascii(local_path) or _path_has_non_ascii(message):
            return "local_path_non_ascii"
        return "local_path_create_failed"
    if "permission denied" in text:
        return "permission_denied"
    if "device offline" in text or "no devices/emulators" in text:
        return "adb_device_offline"
    return "pull_failed"


def adb_pull_file(
    *,
    serial: str,
    remote_path: str,
    local_path: str,
    timeout: float = 60.0,
) -> str:
    """Pull a remote file. On Windows, adb often fails when the local path
    contains non-ASCII characters (e.g. 锦仓自动化测试). Stage via an ASCII
    temp path, then move into the final destination.
    """
    parent = os.path.dirname(local_path) or "."
    os.makedirs(parent, exist_ok=True)
    parent_exists = os.path.isdir(parent)
    non_ascii = _path_has_non_ascii(local_path)
    pull_target = local_path
    staging_path = ""
    if non_ascii:
        staging_dir = os.path.join(tempfile.gettempdir(), "glasstest_adb_pull")
        os.makedirs(staging_dir, exist_ok=True)
        staging_path = os.path.join(
            staging_dir,
            f"{uuid.uuid4().hex}{Path(local_path).suffix or '.bin'}",
        )
        pull_target = staging_path

    remote_exists = _remote_file_exists(serial, remote_path)
    logger.info(
        "adb pull begin serial=%s remote=%s remote_exists=%s local=%s "
        "local_parent_exists=%s local_non_ascii=%s staging=%s",
        serial,
        remote_path,
        remote_exists,
        local_path,
        parent_exists,
        non_ascii,
        bool(staging_path),
    )
    if staging_path:
        logger.info("adb pull staging via ASCII temp: %s", staging_path)

    result = subprocess.run(
        ["adb", "-s", serial, "pull", remote_path, pull_target],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        **_subprocess_hidden_kwargs(),
    )
    output = (result.stderr or result.stdout or "").strip()
    staged_ok = bool(staging_path) and os.path.isfile(pull_target)
    local_ok = os.path.isfile(pull_target)
    staged_size = os.path.getsize(pull_target) if local_ok else 0
    logger.info(
        "adb pull end rc=%s local_ok=%s size=%s output=%s",
        result.returncode,
        local_ok,
        staged_size,
        output[:500] if output else "",
    )
    if result.returncode != 0 or not local_ok:
        if staging_path and os.path.isfile(staging_path):
            try:
                os.remove(staging_path)
            except OSError:
                pass
        reason = classify_pull_error(output, local_path=local_path, remote_path=remote_path)
        if not remote_exists and reason == "pull_failed":
            reason = "remote_missing"
        raise RuntimeError(
            f"[{reason}] {output or 'adb pull failed'} "
            f"(remote={remote_path}, remote_exists={remote_exists}, "
            f"local={local_path}, local_non_ascii={non_ascii}, staged={bool(staging_path)})"
        )

    if staging_path:
        try:
            if os.path.isfile(local_path):
                os.remove(local_path)
            shutil.move(staging_path, local_path)
            logger.info("adb pull moved staging -> final size=%s", os.path.getsize(local_path))
        finally:
            if os.path.isfile(staging_path):
                try:
                    os.remove(staging_path)
                except OSError:
                    pass

    if not os.path.isfile(local_path):
        raise RuntimeError(
            f"[local_missing_after_pull] pull 后本地文件不存在: {local_path} "
            f"(remote={remote_path}, staged_ok={staged_ok})"
        )
    return local_path


def parse_device_path(full_path: str, package: str = "com.moyoung.glasses") -> str:
    """
    Convert any Android app-private absolute path to a run-as relative path.

    Handles different phones/users:
      /data/user/0/{package}/...
      /data/user/10/{package}/...
      /data/data/{package}/...
    """
    text = (full_path or "").replace("\\", "/").strip()
    match = re.search(rf"/(?:data/user/\d+|data/data)/{re.escape(package)}/(.*)", text)
    if match:
        return match.group(1)
    if text.startswith(("files/", "cache/", "shared_prefs/")):
        return text
    return text.lstrip("/")


def pull_via_run_as(
    *,
    serial: str,
    package: str,
    device_path: str,
    local_path: str,
    timeout: float = 60.0,
) -> str:
    rel = parse_device_path(device_path, package=package)
    os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
    cmd = [
        "adb",
        "-s",
        serial,
        "exec-out",
        "run-as",
        package,
        "cat",
        rel,
    ]
    logger.info(
        "pull run-as begin serial=%s rel=%s device_path=%s local=%s local_non_ascii=%s",
        serial,
        rel,
        device_path,
        local_path,
        _path_has_non_ascii(local_path),
    )
    result = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False, **_subprocess_hidden_kwargs())
    err = (result.stderr or b"").decode("utf-8", errors="replace").strip()
    payload = result.stdout or b""
    payload_text = payload[:512].decode("utf-8", errors="replace").strip()
    blob = err + "\n" + payload_text
    # 非 debug 包会把 run-as 错误写进 stdout，不能当成媒体文件。
    if (
        result.returncode != 0
        or not payload
        or payload.startswith(b"run-as:")
        or "No such file or directory" in blob
        or "not debuggable" in blob
    ):
        reason = classify_pull_error(blob, local_path=local_path, remote_path=device_path)
        raise RuntimeError(
            f"[{reason}] pull 失败: {err or payload_text or 'empty output'} "
            f"(rel={rel}, device_path={device_path}, local={local_path})"
        )
    Path(local_path).write_bytes(payload)
    logger.info("pull run-as ok size=%s -> %s", len(payload), local_path)
    return local_path


# 只扫这些公共根目录，不写死品牌文件夹。换眼镜/换 App 子目录名也能找到。
PUBLIC_SEARCH_ROOTS = (
    "/sdcard/Pictures",
    "/sdcard/Movies",
    "/sdcard/DCIM",
    "/sdcard/Download",
    "/sdcard/Recordings",
    "/sdcard/Music",
    "/sdcard/Android/media",
)
MEDIA_STORE_URIS = (
    "content://media/external/file",
    "content://media/external/images/media",
    "content://media/external/video/media",
    "content://media/external/audio/media",
)
_REMEMBERED_PUBLIC_DIRS: Dict[str, List[str]] = {}


def media_search_tokens(device_path: str) -> List[str]:
    """Names to look for. Compact stamps and '2026-10-29 14-59-12' both count."""
    name = Path(device_path).name
    tokens: List[str] = []

    def add(value: str) -> None:
        text = (value or "").strip()
        if text and text not in tokens:
            tokens.append(text)

    for match in re.finditer(r"(20\d{12,15})", name):
        digits = match.group(1)[:14]
        add(digits)
        if re.fullmatch(r"20\d{12}", digits):
            add(
                f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]} "
                f"{digits[8:10]}-{digits[10:12]}-{digits[12:14]}"
            )
    dashed = re.search(
        r"20\d{2}[-_]\d{2}[-_]\d{2}[ T_]\d{2}[-_:]\d{2}[-_:]\d{2}",
        name,
    )
    if dashed:
        add(dashed.group(0))
    return tokens


def parse_media_store_rows(text: str) -> List[str]:
    paths: List[str] = []
    for match in re.finditer(r"_data=([^,\r\n]+)", text or ""):
        path = match.group(1).strip()
        if path.startswith("/") and path not in paths:
            paths.append(path)
    return paths


def _public_name_rank(name: str) -> Tuple[int, int]:
    lower = name.lower()
    if "original cache" in lower or "/cache/" in lower or lower.startswith("cache_"):
        rank = 8
    elif lower.startswith("aw_image_") or lower.startswith("ff-stabilized"):
        rank = 0
    # 6stabilized- / Nstabilized- / stabilized- are real gallery exports
    elif "stabilized-" in lower or lower.startswith("image_"):
        rank = 1
    else:
        # Bare timestamp.mp4 from MediaStore is often a stale alias — prefer last.
        rank = 5
    return (rank, -len(name))


def _remote_file_exists(serial: str, path: str) -> bool:
    quoted = path.replace("'", "'\\''")
    listed = _adb_shell(serial, f"ls -l '{quoted}'", 10)
    text = f"{listed.stdout or ''}{listed.stderr or ''}"
    if listed.returncode != 0 or "No such file" in text:
        return False
    # ls -l of a file starts with '-' or shows size; directories start with 'd'
    for line in (listed.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("-") or (line and not line.startswith("d") and "No such" not in line):
            return True
    return False


def _adb_shell(serial: str, script: str, timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["adb", "-s", serial, "shell", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        **_subprocess_hidden_kwargs(),
    )


def _quote_token(token: str) -> str:
    return token.replace("'", "'\\''")


def _paths_in_dirs(serial: str, directories: List[str], tokens: List[str]) -> List[str]:
    found: List[str] = []
    for directory in directories:
        listed = _adb_shell(serial, f"ls -1 '{directory}'", 15)
        if listed.returncode != 0:
            continue
        for line in (listed.stdout or "").splitlines():
            name = line.strip()
            if not name or "No such file" in name:
                continue
            if any(token in name for token in tokens):
                found.append(f"{directory.rstrip('/')}/{name}")
    return found


def _paths_from_media_store(serial: str, tokens: List[str]) -> List[str]:
    found: List[str] = []
    for token in tokens:
        safe = _quote_token(token)
        for uri in MEDIA_STORE_URIS:
            script = (
                f"content query --uri {uri} --projection _data "
                f"--where \"_display_name LIKE '%{safe}%'\""
            )
            listed = _adb_shell(serial, script, 20)
            if listed.returncode != 0:
                continue
            for path in parse_media_store_rows(listed.stdout):
                if path not in found:
                    found.append(path)
        if found:
            break
    return found


def _paths_from_public_scan(serial: str, tokens: List[str]) -> List[str]:
    roots = " ".join(f"'{root}'" for root in PUBLIC_SEARCH_ROOTS)
    found: List[str] = []
    for token in tokens:
        safe = _quote_token(token)
        script = f"find {roots} -maxdepth 3 -type f -name '*{safe}*' 2>/dev/null"
        listed = _adb_shell(serial, script, 25)
        for line in (listed.stdout or "").splitlines():
            path = line.strip()
            if path.startswith("/") and path not in found:
                found.append(path)
        if found:
            break
    return found


def discover_public_media(serial: str, device_path: str, *, deep: bool = True) -> List[str]:
    tokens = media_search_tokens(device_path)
    if not tokens:
        return []
    remembered = list(_REMEMBERED_PUBLIC_DIRS.get(serial, []))
    found = _paths_in_dirs(serial, remembered, tokens)
    if not found:
        found = _paths_from_media_store(serial, tokens)
    if not found and deep:
        found = _paths_from_public_scan(serial, tokens)
    for path in found:
        parent = str(Path(path).parent).replace("\\", "/")
        known = _REMEMBERED_PUBLIC_DIRS.setdefault(serial, [])
        if parent not in known:
            known.append(parent)
    return found


def pull_from_public_media(*, serial: str, device_path: str, local_path: str, timeout: float = 60.0) -> str:
    """非 debug 包读不了私有目录。按时间戳查系统相册，不依赖固定品牌文件夹。

    MediaStore 的 ``_data`` 可能在 App 的相册整理/防抖流程中短暂指向一个
    临时文件：刚用 ``ls`` 检查存在，随后就被改名或删除。因此发现和 pull
    必须放在同一个可重试循环里，不能只检查一次后固定使用该路径。
    """
    tokens = media_search_tokens(device_path)
    if not tokens:
        raise RuntimeError(f"无法从路径提取可搜索的文件名: {device_path}")
    os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
    last_error = ""

    # 导入完成后相册发布仍可能异步改名；每轮重新查询 MediaStore 和实际文件。
    for attempt in range(1, 9):
        candidates = discover_public_media(serial, device_path, deep=attempt >= 3)
        existing = [path for path in candidates if _remote_file_exists(serial, path)]
        logger.info(
            "public media lookup attempt=%s tokens=%s candidates=%s existing=%s",
            attempt,
            tokens,
            candidates,
            existing,
        )
        for remote in sorted(existing, key=lambda item: _public_name_rank(Path(item).name)):
            logger.info("pull public attempt=%s %s -> %s", attempt, remote, local_path)
            try:
                adb_pull_file(
                    serial=serial,
                    remote_path=remote,
                    local_path=local_path,
                    timeout=timeout,
                )
                if os.path.isfile(local_path) and os.path.getsize(local_path) >= 128:
                    return local_path
                last_error = f"pull 后文件过小或缺失 ({remote})"
            except Exception as exc:
                last_error = f"{exc} ({remote})"
            logger.warning("public media pull failed attempt=%s: %s", attempt, last_error)
            # 该候选可能刚被 App 替换；下一轮重新发现，不要继续使用旧路径。
            try:
                if os.path.isfile(local_path):
                    os.remove(local_path)
            except OSError:
                pass
        time.sleep(1.5 if existing else 2.0)

    if last_error:
        raise RuntimeError(f"公共目录 pull 失败（已重试 8 次）: {last_error}")
    raise RuntimeError(f"公共相册未找到 {tokens[0]}（已重试 8 次）")


def pull_media_file(
    *,
    serial: str,
    package: str,
    device_path: str,
    local_path: str,
    timeout: float = 60.0,
) -> str:
    logger.info(
        "pull_media_file start device_path=%s local=%s local_non_ascii=%s",
        device_path,
        local_path,
        _path_has_non_ascii(local_path),
    )
    try:
        pulled = pull_via_run_as(
            serial=serial,
            package=package,
            device_path=device_path,
            local_path=local_path,
            timeout=timeout,
        )
        logger.info("pull_media_file done via=run_as local=%s", pulled)
        return pulled
    except Exception as exc:
        logger.info(
            "run-as 拉取失败 class=%s，改走公共相册: %s",
            classify_pull_error(str(exc), local_path=local_path, remote_path=device_path),
            exc,
        )
        try:
            pulled = pull_from_public_media(
                serial=serial,
                device_path=device_path,
                local_path=local_path,
                timeout=timeout,
            )
            logger.info("pull_media_file done via=public local=%s", pulled)
            return pulled
        except Exception as public_exc:
            raise RuntimeError(
                f"{public_exc} | prior_run_as={exc}"
            ) from public_exc


def measure_image(path: str) -> Dict[str, Any]:
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("需要 Pillow：pip install Pillow") from exc
    with Image.open(path) as img:
        width, height = img.size
    return {"width": width, "height": height, "path": path, "kind": "image"}


def _ffprobe_path() -> str:
    found = shutil.which("ffprobe")
    if found:
        return found
    root = Path(__file__).resolve().parent.parent
    candidates = [
        root / "runtime" / "ffmpeg" / "bin" / "ffprobe.exe",
        root / "runtime" / "FFmpeg" / "bin" / "ffprobe.exe",
        Path(r"D:\AutoTestTools\Tools\FFmpeg\bin\ffprobe.exe"),
        Path(r"C:\ffmpeg\bin\ffprobe.exe"),
    ]
    return next((str(path) for path in candidates if path.is_file()), "")


def measure_video(path: str) -> Dict[str, Any]:
    ffprobe = _ffprobe_path()
    cmd = [
        ffprobe or "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "csv=p=0:s=x",
        path,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
            **_subprocess_hidden_kwargs(),
        )
        if result.returncode == 0:
            text = (result.stdout or "").strip().splitlines()[0].strip()
            match = re.fullmatch(r"(\d+)x(\d+)", text)
            if match:
                return {
                    "width": int(match.group(1)),
                    "height": int(match.group(2)),
                    "path": path,
                    "kind": "video",
                    "tool": "ffprobe",
                }
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.warning("ffprobe 失败: %s", exc)

    # Fallback: OpenCV if installed
    try:
        import cv2  # type: ignore

        capture = cv2.VideoCapture(path)
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        capture.release()
        if width > 0 and height > 0:
            return {"width": width, "height": height, "path": path, "kind": "video", "tool": "opencv"}
    except Exception as exc:
        logger.warning("opencv 读视频失败: %s", exc)

    raise RuntimeError("无法读取视频分辨率（需要 ffprobe 或 opencv-python）")


def measure_local_file(path: str) -> Dict[str, Any]:
    lower = path.lower()
    if lower.endswith((".jpg", ".jpeg", ".png", ".webp", ".bmp")):
        return measure_image(path)
    if lower.endswith((".mp4", ".mov", ".avi", ".mkv", ".m4v")):
        return measure_video(path)
    # try image then video
    try:
        return measure_image(path)
    except Exception:
        return measure_video(path)


def sizes_match(actual: Tuple[int, int], expected: Tuple[int, int], *, swap_ok: bool = True) -> bool:
    if actual == expected:
        return True
    if swap_ok and (actual[0], actual[1]) == (expected[1], expected[0]):
        return True
    return False


def format_byte_size(num_bytes: Optional[int]) -> str:
    if num_bytes is None or num_bytes < 0:
        return ""
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024.0 or unit == "GB":
            if unit == "B":
                return f"{int(value)} B"
            text = f"{value:.1f}".rstrip("0").rstrip(".")
            return f"{text} {unit}"
        value /= 1024.0
    return f"{int(num_bytes)} B"


def _parse_ls_size(text: str) -> Optional[int]:
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("total ") or "No such file" in line:
            continue
        parts = line.split()
        # busybox/toybox: -rw-r--r-- 1 user group SIZE date name
        if len(parts) >= 5 and parts[0].startswith("-"):
            for token in parts[3:6]:
                if token.isdigit():
                    return int(token)
    return None


def _stat_remote_bytes(serial: str, remote_path: str) -> Optional[int]:
    quoted = remote_path.replace("'", "'\\''")
    for script in (
        f"stat -c %s '{quoted}'",
        f"ls -l '{quoted}'",
    ):
        listed = _adb_shell(serial, script, 10)
        text = f"{listed.stdout or ''}{listed.stderr or ''}"
        if listed.returncode != 0 and "No such file" in text:
            continue
        stdout = (listed.stdout or "").strip()
        if script.startswith("stat") and stdout.isdigit():
            return int(stdout)
        size = _parse_ls_size(listed.stdout or "")
        if size is not None:
            return size
    return None


def _stat_via_run_as(serial: str, package: str, device_path: str) -> Optional[int]:
    rel = parse_device_path(device_path, package=package)
    if not rel:
        return None
    listed = _adb_shell(serial, f"run-as {package} ls -l '{rel}'", 10)
    blob = f"{listed.stdout or ''}{listed.stderr or ''}"
    if "not debuggable" in blob or listed.returncode != 0:
        return None
    return _parse_ls_size(listed.stdout or "")


def _sizes_from_media_store(serial: str, tokens: List[str]) -> Dict[str, int]:
    found: Dict[str, int] = {}
    # file 总表通常够用；图片/视频/音频分表作为补充但限时。
    uris = ("content://media/external/file",) + tuple(
        u for u in MEDIA_STORE_URIS if u != "content://media/external/file"
    )
    for token in tokens:
        safe = _quote_token(token)
        for uri in uris[:2]:
            script = (
                f"content query --uri {uri} --projection _data,_size "
                f"--where \"_display_name LIKE '%{safe}%'\""
            )
            listed = _adb_shell(serial, script, 8)
            if listed.returncode != 0:
                continue
            for row in (listed.stdout or "").splitlines():
                data_match = re.search(r"_data=([^,\r\n]+)", row)
                size_match = re.search(r"_size=(\d+)", row)
                if not data_match or not size_match:
                    continue
                name = Path(data_match.group(1).strip()).name
                if name and name not in found:
                    found[name] = int(size_match.group(1))
    return found


def _sizes_from_logcat(log_path: str) -> Dict[str, int]:
    if not log_path or not os.path.isfile(log_path):
        return {}
    try:
        text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    found: Dict[str, int] = {}
    patterns = (
        r"BkAviEnhance start,\s*path=([^,\s]+),\s*bytes=(\d+)",
        r"download_file_item[^\n]*path=([^\s,]+)[^\n]*bytes=(\d+)",
        r"path=([^,\s]+)[^\n]*fileSize=(\d+)",
        r"fileSize=(\d+)[^\n]*path=([^,\s]+)",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            groups = match.groups()
            if pattern.startswith("fileSize="):
                size_text, path_text = groups[0], groups[1]
            else:
                path_text, size_text = groups[0], groups[1]
            name = Path(path_text.strip()).name
            if name and size_text.isdigit() and name not in found:
                found[name] = int(size_text)
    return found


def list_recent_media_store_paths(
    serial: str,
    *,
    since_epoch: float,
    until_epoch: float,
    limit: int = 40,
) -> List[str]:
    """按 date_added 扫系统相册，补 logcat 没解析到的本轮导入文件。"""
    if not serial:
        return []
    start = max(0, int(since_epoch) - 180)
    end = max(start, int(until_epoch) + 180)
    found: List[str] = []
    stamp_prefixes: List[str] = []
    for ts in (since_epoch - 180, since_epoch, until_epoch):
        try:
            stamp_prefixes.append(time.strftime("%Y%m%d%H", time.localtime(ts)))
        except Exception:
            pass
    stamp_prefixes = list(dict.fromkeys(stamp_prefixes))

    for uri in MEDIA_STORE_URIS:
        script = (
            f"content query --uri {uri} "
            f"--projection _data:date_added:date_modified "
            f"--where \"(date_added>={start} AND date_added<={end}) "
            f"OR (date_modified>={start} AND date_modified<={end})\""
        )
        listed = _adb_shell(serial, script, 20)
        if listed.returncode != 0:
            continue
        for path in parse_media_store_rows(listed.stdout or ""):
            lower = path.lower()
            if not any(lower.endswith(ext) for ext in (
                ".jpg", ".jpeg", ".png", ".webp", ".heic", ".bmp",
                ".mp4", ".mov", ".mkv", ".avi", ".3gp", ".webm",
                ".opus", ".wav", ".mp3", ".aac", ".m4a", ".ogg", ".amr",
            )):
                continue
            if path not in found:
                found.append(path)
            if len(found) >= limit:
                return found

    # 录音常不在 MediaStore 图片/视频里：只补扫录音目录，避免全盘 find 拖很久。
    has_audio = any(
        p.lower().endswith(ext)
        for p in found
        for ext in (".opus", ".m4a", ".mp3", ".wav", ".aac", ".ogg", ".amr")
    )
    if not has_audio:
        prefix = (stamp_prefixes[0] if stamp_prefixes else "20") + "*"
        script = (
            "find /sdcard/Music /sdcard/Recordings /sdcard/Android/media "
            "-maxdepth 3 -type f "
            f"\\( -name '{prefix}.opus' -o -name '{prefix}.m4a' "
            f"-o -name '{prefix}.mp3' -o -name '{prefix}.wav' "
            f"-o -name '{prefix}.aac' -o -name '{prefix}.ogg' \\) "
            "2>/dev/null | head -n 20"
        )
        listed = _adb_shell(serial, script, 8)
        for line in (listed.stdout or "").splitlines():
            path = line.strip()
            if path.startswith("/") and path not in found:
                found.append(path)
            if len(found) >= limit:
                break
    return found


def probe_import_file_sizes(
    *,
    serial: str,
    paths: List[str],
    package: str = "com.moyoung.glasses",
    logcat_path: str = "",
) -> Dict[str, Dict[str, Any]]:
    """Best-effort size lookup for imported media paths.

    Prefer public MediaStore / ls, then run-as, then import log hints
    (e.g. BkAviEnhance bytes=...).
    """
    result: Dict[str, Dict[str, Any]] = {}
    unique_paths = []
    seen = set()
    for raw in paths:
        path_text = str(raw or "").replace("\\", "/").strip()
        if not path_text:
            continue
        name = Path(path_text).name
        key = name.lower()
        if not key or key in seen:
            continue
        seen.add(key)
        unique_paths.append(path_text)

    log_sizes = _sizes_from_logcat(logcat_path)
    # 已有绝对路径时先直接 ls，避免 MediaStore/find 把导入后流程拖成几分钟。
    missing: List[str] = []
    for path_text in unique_paths:
        name = Path(path_text).name
        size_bytes = _stat_remote_bytes(serial, path_text)
        source = "direct_ls" if size_bytes is not None else ""
        if size_bytes is None:
            missing.append(path_text)
        result[name] = {
            "path": path_text,
            "name": name,
            "size_bytes": size_bytes,
            "size_text": format_byte_size(size_bytes),
            "source": source,
        }

    if missing:
        deadline = time.time() + 20.0  # 导入后大小探测总预算，超时就留给报告空白
        all_tokens: List[str] = []
        for path_text in missing:
            for token in media_search_tokens(path_text):
                if token not in all_tokens:
                    all_tokens.append(token)
        store_sizes = _sizes_from_media_store(serial, all_tokens[:8]) if all_tokens else {}
        for path_text in missing:
            name = Path(path_text).name
            size_bytes: Optional[int] = store_sizes.get(name)
            source = "mediastore" if size_bytes is not None else ""
            if size_bytes is None and time.time() < deadline and package:
                size_bytes = _stat_via_run_as(serial, package, path_text)
                if size_bytes is not None:
                    source = "run_as"
            if size_bytes is None:
                size_bytes = log_sizes.get(name)
                if size_bytes is not None:
                    source = "logcat"
            # 不做深层 public find：导入已成功，大小列可空，别拖一两分钟。
            result[name] = {
                "path": path_text,
                "name": name,
                "size_bytes": size_bytes,
                "size_text": format_byte_size(size_bytes),
                "source": source,
            }
    return result

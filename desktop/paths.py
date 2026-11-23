"""Resolve bundled resources and writable work dir for source / frozen exe."""

from __future__ import annotations

import os
import shutil
import string
import sys
from pathlib import Path

APP_NAME = "GlassesTest"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False)) and hasattr(sys, "_MEIPASS")


def exe_dir() -> Path:
    """Folder that contains GlassesTest.exe (or project root in source mode)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_root() -> Path:
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parent.parent


def code_root() -> Path:
    """Where Python packages live (project root or frozen unpack dir)."""
    return resource_root()


def _drive_exists(letter: str) -> bool:
    try:
        return Path(f"{letter}:\\").exists()
    except Exception:
        return False


def _fixed_non_c_root() -> Path | None:
    """Prefer D: then other fixed letters, never force others onto the developer PC path."""
    for letter in ("D", "E", "F", "G", "H"):
        if _drive_exists(letter):
            return Path(f"{letter}:\\{APP_NAME}_data")
    # Any remaining fixed A-Z except C.
    for letter in string.ascii_uppercase:
        if letter == "C":
            continue
        if _drive_exists(letter):
            return Path(f"{letter}:\\{APP_NAME}_data")
    return None


def work_root() -> Path:
    """Writable data root shared by GUI / workers.

    Priority:
    1) GLASSES_DATA_DIR env
    2) frozen exe → <exe同目录>/GlassesTest_data
    3) non-C fixed drive → X:\\GlassesTest_data（优先 D:）
    4) last resort → %LOCALAPPDATA%\\GlassesTest
    """
    override = (os.environ.get("GLASSES_DATA_DIR") or "").strip()
    if override:
        root = Path(override)
    elif is_frozen():
        root = exe_dir() / f"{APP_NAME}_data"
    else:
        # Desktop/source runs should NOT dump into the git project tree for testers.
        fixed = _fixed_non_c_root()
        if fixed is not None:
            root = fixed
        else:
            base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
            root = base / APP_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def output_root() -> Path:
    path = work_root() / "output"
    path.mkdir(parents=True, exist_ok=True)
    return path


def runtime_tools_dir() -> Path:
    path = work_root() / "runtime_tools"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _copy_if_needed(src: Path, dest: Path) -> bool:
    if not src.is_file():
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.is_file() and dest.stat().st_size == src.stat().st_size:
        return True
    shutil.copy2(src, dest)
    return True


def ensure_bundled_tools() -> dict[str, str]:
    """Copy adb/ffprobe out of the bundle so PATH can use them."""
    res = resource_root()
    tools = runtime_tools_dir()
    mapping = {
        "adb.exe": tools / "adb.exe",
        "AdbWinApi.dll": tools / "AdbWinApi.dll",
        "AdbWinUsbApi.dll": tools / "AdbWinUsbApi.dll",
        "ffprobe.exe": tools / "ffprobe.exe",
    }
    search_roots = [
        res / "bundled_tools",
        res / "runtime" / "platform-tools",
        res / "runtime" / "ffmpeg" / "bin",
        res,
        # Dev machine fallback while running from source.
        Path(__file__).resolve().parent.parent / "runtime" / "platform-tools",
        Path(__file__).resolve().parent.parent / "runtime" / "ffmpeg" / "bin",
        Path(__file__).resolve().parent.parent / "bundled_tools",
    ]
    found: dict[str, str] = {}
    for name, dest in mapping.items():
        src = None
        for root in search_roots:
            candidate = root / name
            if candidate.is_file():
                src = candidate
                break
        if src is None:
            continue
        if _copy_if_needed(src, dest):
            found[name] = str(dest)
    tools_path = str(tools)
    path_parts = os.environ.get("PATH", "").split(os.pathsep)
    if tools_path not in path_parts:
        os.environ["PATH"] = tools_path + os.pathsep + os.environ.get("PATH", "")
    return found


def ensure_work_configs() -> None:
    """Seed writable configs into the data root."""
    res = resource_root()
    # Also allow reading from the source project when developing.
    project = Path(__file__).resolve().parent.parent
    work = work_root()
    for name in (
        "task_config.json",
        "selectors.json",
        "device_registry.json",
        "import_validation_config.json",
        "图像视频标准输出规格.md",
    ):
        dest = work / name
        if dest.is_file():
            continue
        for src_root in (res, project):
            src = src_root / name
            if src.is_file():
                shutil.copy2(src, dest)
                break


def bootstrap() -> Path:
    """Prepare env and return writable data root.

    Data (output/logs) lives under work_root(); code packages stay in code_root().
    Never chdir away from the code tree in a way that breaks `python -m desktop.worker`.
    """
    ensure_bundled_tools()
    ensure_work_configs()
    data = work_root()
    code = code_root()
    output_root()
    # Keep ADB temp files under our work dir to avoid %TEMP%\\adb lock conflicts.
    tmp = data / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    os.environ["TEMP"] = str(tmp)
    os.environ["TMP"] = str(tmp)
    os.environ["GLASSES_DATA_DIR"] = str(data)
    os.environ.setdefault("GLASSES_SELECTORS_PATH", str(data / "selectors.json"))
    # Ensure imports work even if a caller chdir'd into the data directory.
    code_text = str(code)
    if code_text not in sys.path:
        sys.path.insert(0, code_text)
    path_parts = [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]
    if code_text not in path_parts:
        os.environ["PYTHONPATH"] = code_text + (os.pathsep + os.pathsep.join(path_parts) if path_parts else "")
    return data

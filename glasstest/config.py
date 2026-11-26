"""Project-wide configuration."""

from __future__ import annotations

import os


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value is None:
        return default
    return str(value).strip()


def _env_float(name: str, default: float) -> float:
    raw = _env(name, "")
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = _env(name, "")
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name, "")
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


# Device / App
DEVICE_SERIAL = _env("GLASSES_DEVICE_SERIAL", "")  # 空则使用当前在线设备，禁止写死旧序列号
APP_PACKAGE = _env("GLASSES_APP_PACKAGE", "com.moyoung.glasses")
DEVICE_CONTROL_TOKEN = _env("GLASSES_DEVICE_CONTROL_TOKEN", "da_echo_adb_control_20260409")
DEVICE_CONTROL_ACTION = _env(
    "GLASSES_DEVICE_CONTROL_ACTION",
    "com.moyoung.glasses.action.DEVICE_CONTROL",
)

# Defaults used by phone_operations.py
PHONE_DEVICE_SERIAL = DEVICE_SERIAL
PHONE_APP_PACKAGE = APP_PACKAGE

# Optional app icon selector (empty means disabled)
APP_ICON_TEXT = _env("GLASSES_APP_ICON_TEXT", "")
APP_ICON_DESC = _env("GLASSES_APP_ICON_DESC", "")
APP_ICON_RESOURCE_ID = _env("GLASSES_APP_ICON_RESOURCE_ID", "")

# Optional home-entry selector after app launch (empty means disabled)
HOME_ENTRY_TEXT = _env("GLASSES_HOME_ENTRY_TEXT", "")
HOME_ENTRY_DESC = _env("GLASSES_HOME_ENTRY_DESC", "")
HOME_ENTRY_RESOURCE_ID = _env("GLASSES_HOME_ENTRY_RESOURCE_ID", "")
HOME_ENTRY_XPATH = _env("GLASSES_HOME_ENTRY_XPATH", "")

# Optional import-button selector on home page (empty means keyword + UI-tree fallback)
HOME_IMPORT_TEXT = _env("GLASSES_HOME_IMPORT_TEXT", "")
HOME_IMPORT_DESC = _env("GLASSES_HOME_IMPORT_DESC", "")
HOME_IMPORT_RESOURCE_ID = _env("GLASSES_HOME_IMPORT_RESOURCE_ID", "")
HOME_IMPORT_XPATH = _env("GLASSES_HOME_IMPORT_XPATH", "")

# Wait timings
APP_LAUNCH_WAIT_SECONDS = _env_float("GLASSES_APP_LAUNCH_WAIT_SECONDS", 8.0)
HOME_READY_WAIT_SECONDS = _env_float("GLASSES_HOME_READY_WAIT_SECONDS", 2.0)
HOME_STABLE_TIMEOUT_SECONDS = _env_float("GLASSES_HOME_STABLE_TIMEOUT_SECONDS", 10.0)
HOME_STABLE_INTERVAL_SECONDS = _env_float("GLASSES_HOME_STABLE_INTERVAL_SECONDS", 0.8)
HOME_STABLE_REQUIRED_MATCHES = _env_int("GLASSES_HOME_STABLE_REQUIRED_MATCHES", 2)
HOME_PRE_CAPTURE_SILENCE_SECONDS = _env_float("GLASSES_HOME_PRE_CAPTURE_SILENCE_SECONDS", 1.5)

# Output paths (home tree)
OUTPUT_DIR = os.path.join("output", "app_home_tree")
HOME_TREE_FILE = os.path.join(OUTPUT_DIR, "home_page_control_tree.json")
HOME_XML_FILE = os.path.join(OUTPUT_DIR, "home_page_hierarchy.xml")
HOME_SCREENSHOT_FILE = os.path.join(OUTPUT_DIR, "home_page_screenshot.png")
HOME_ANNOTATED_SCREENSHOT_FILE = os.path.join(OUTPUT_DIR, "home_page_screenshot_annotated.png")

# Automation output paths. A run calls use_run_output() so its files stay together.
RUN_LOG_DIR = os.path.join("output", "run_logs")
FAILURE_ARTIFACT_DIR = os.path.join("output", "failures")
IMPORT_LOG_DIR = os.path.join("output", "import_logs")
IMPORT_SUCCESS_LOG_FILE = os.path.join(IMPORT_LOG_DIR, "import_success.jsonl")
IMPORT_ERROR_LOG_FILE = os.path.join(IMPORT_LOG_DIR, "import_errors.jsonl")
DIAGNOSTICS_DIR = os.path.join("output", "diagnostics")
SCREENSHOT_DIR = os.path.join(IMPORT_LOG_DIR, "diagnostics")
ACTIVE_RUN_DIR = ""


def use_run_output(run_dir: str) -> str:
    """Point logs and screenshots at this run's own folder."""
    global RUN_LOG_DIR, FAILURE_ARTIFACT_DIR, IMPORT_LOG_DIR
    global IMPORT_SUCCESS_LOG_FILE, IMPORT_ERROR_LOG_FILE
    global DIAGNOSTICS_DIR, SCREENSHOT_DIR, ACTIVE_RUN_DIR
    root = os.path.abspath(run_dir)
    # Prefer ASCII folder names; keep reading legacy 中文 dirs if already present.
    log_dir = os.path.join(root, "logs")
    shot_dir = os.path.join(root, "shots")
    if not os.path.isdir(log_dir) and os.path.isdir(os.path.join(root, "日志")):
        log_dir = os.path.join(root, "日志")
    if not os.path.isdir(shot_dir) and os.path.isdir(os.path.join(root, "截图")):
        shot_dir = os.path.join(root, "截图")
    os.makedirs(log_dir, exist_ok=True)
    os.makedirs(shot_dir, exist_ok=True)
    ACTIVE_RUN_DIR = root
    RUN_LOG_DIR = log_dir
    IMPORT_LOG_DIR = log_dir
    IMPORT_SUCCESS_LOG_FILE = os.path.join(log_dir, "import_success.jsonl")
    IMPORT_ERROR_LOG_FILE = os.path.join(log_dir, "import_errors.jsonl")
    FAILURE_ARTIFACT_DIR = shot_dir
    DIAGNOSTICS_DIR = shot_dir
    SCREENSHOT_DIR = shot_dir
    return root

# Automation defaults
AUTOMATION_DEFAULT_TASK = "photo"
AUTOMATION_DEFAULT_TIMES = 1
AUTOMATION_DEFAULT_DURATION_SECONDS = 60
AUTOMATION_DEFAULT_IMPORT_TIMEOUT_SECONDS = 1800.0
AUTOMATION_DEFAULT_INTERVAL_SECONDS = 0.0
AUTOMATION_DEFAULT_RETRY_LIMIT = 3
AUTOMATION_DEFAULT_CONTINUE_ON_FAILURE = True
AUTOMATION_DEFAULT_CAPTURE_HOME_TREE = True

# Runtime wait / polling
AUTOMATION_PRE_TRIGGER_WAIT_SECONDS = 0.8
AUTOMATION_POST_TRIGGER_WAIT_SECONDS = 4.0
# 下发 start_video/start_audio 后，眼镜真正开录有固定延迟（实测约 3s）。
# 配置里的 duration 表示「成片目标时长」；等待时额外补这段启动延迟，避免 设5秒只录到2秒。
GLASSES_CAPTURE_STARTUP_LAG_SECONDS = _env_float("GLASSES_CAPTURE_STARTUP_LAG_SECONDS", 3.0)
AUTOMATION_PROGRESS_POLL_INTERVAL_SECONDS = 1.5
AUTOMATION_PROGRESS_STABLE_REQUIRED = 2
AUTOMATION_IMPORT_STALL_TIMEOUT_SECONDS = 120.0
AUTOMATION_IMPORT_SUCCESS_SETTLE_SECONDS = 2.0
AUTOMATION_IMPORT_RETRY_LIMIT = 3
AUTOMATION_IMPORT_RETRY_BACKOFF_SECONDS = 3.0
AUTOMATION_IMPORT_RETRY_RELAUNCH_APP = True

# Log level
LOG_LEVEL = _env("GLASSES_LOG_LEVEL", "INFO")

# Task config path used by Web / CLI helpers
TASK_CONFIG_PATH = _env("GLASSES_TASK_CONFIG_PATH", "task_config.json")


def apply_runtime_overrides(
    *,
    device_serial: str | None = None,
    app_package: str | None = None,
    device_control_token: str | None = None,
) -> None:
    """Apply in-process overrides (used by Web UI without rewriting source)."""
    global DEVICE_SERIAL, APP_PACKAGE, PHONE_DEVICE_SERIAL, PHONE_APP_PACKAGE, DEVICE_CONTROL_TOKEN

    if device_serial is not None:
        serial = str(device_serial).strip()
        if serial:
            DEVICE_SERIAL = serial
            PHONE_DEVICE_SERIAL = serial
            os.environ["GLASSES_DEVICE_SERIAL"] = serial

    if app_package is not None:
        package = str(app_package).strip()
        if package:
            APP_PACKAGE = package
            PHONE_APP_PACKAGE = package
            os.environ["GLASSES_APP_PACKAGE"] = package

    if device_control_token is not None:
        token = str(device_control_token).strip()
        if token:
            DEVICE_CONTROL_TOKEN = token
            os.environ["GLASSES_DEVICE_CONTROL_TOKEN"] = token

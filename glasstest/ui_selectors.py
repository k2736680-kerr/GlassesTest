"""Load and expose UI / device-control selectors from selectors.json."""

from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict, List, Optional

_LOCK = threading.RLock()
_CACHE: Optional[Dict[str, Any]] = None
_CACHE_PATH: Optional[str] = None

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SELECTORS_PATH = os.path.join(_PROJECT_ROOT, "selectors.json")


def selectors_path() -> str:
    override = (os.environ.get("GLASSES_SELECTORS_PATH") or "").strip()
    return override or DEFAULT_SELECTORS_PATH


def load_selectors(path: Optional[str] = None, *, force_reload: bool = False) -> Dict[str, Any]:
    global _CACHE, _CACHE_PATH
    target = os.path.abspath(path or selectors_path())
    with _LOCK:
        if not force_reload and _CACHE is not None and _CACHE_PATH == target:
            return _CACHE
        if not os.path.isfile(target):
            data: Dict[str, Any] = {}
        else:
            with open(target, "r", encoding="utf-8") as file_obj:
                loaded = json.load(file_obj)
            data = loaded if isinstance(loaded, dict) else {}
        _CACHE = data
        _CACHE_PATH = target
        return data


def save_selectors(data: Dict[str, Any], path: Optional[str] = None) -> str:
    global _CACHE, _CACHE_PATH
    target = os.path.abspath(path or selectors_path())
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    with open(target, "w", encoding="utf-8") as file_obj:
        json.dump(data, file_obj, ensure_ascii=False, indent=2)
        file_obj.write("\n")
    with _LOCK:
        _CACHE = dict(data)
        _CACHE_PATH = target
    return target


def _section(name: str) -> Dict[str, Any]:
    data = load_selectors()
    section = data.get(name, {})
    return section if isinstance(section, dict) else {}


def _str(section: Dict[str, Any], key: str, default: str = "") -> str:
    value = section.get(key, default)
    text = str(value if value is not None else default).strip()
    return text if text else default


def import_card_resource_id() -> str:
    return _str(_section("import"), "card_resource_id", "com.moyoung.glasses:id/media_import_view")


def import_button_text() -> str:
    return _str(_section("import"), "button_text", "导入")


def import_xpath() -> str:
    return _str(_section("import"), "xpath", "")


def import_resource_id() -> str:
    return _str(_section("import"), "resource_id", "")


def import_text() -> str:
    return _str(_section("import"), "text", "")


def import_desc() -> str:
    return _str(_section("import"), "desc", "")


def home_tab_button_resource_id() -> str:
    return _str(_section("home"), "tab_button_resource_id", "")


def home_tab_text() -> str:
    return _str(_section("home"), "tab_text", "首页")


def home_content_resource_id() -> str:
    return _str(_section("home"), "content_resource_id", "com.moyoung.glasses:id/home_content")


def back_button_resource_id() -> str:
    return _str(_section("home"), "back_button_resource_id", "com.moyoung.glasses:id/iv_back")


def gallery_see_all_resource_id() -> str:
    return _str(_section("gallery"), "see_all_resource_id", "")


def gallery_title_resource_id() -> str:
    return _str(_section("gallery"), "title_resource_id", "")


def gallery_section_resource_id() -> str:
    return _str(_section("gallery"), "section_resource_id", "com.moyoung.glasses:id/home_gallery_section")


def media_grid_resource_id() -> str:
    return _str(
        _section("gallery"),
        "media_grid_resource_id",
        "com.moyoung.glasses:id/home_gallery_list",
    )


def launch_activity() -> str:
    return _str(
        _section("launch"),
        "activity",
        "com.moyoung.glasses/.users.welcome.SplashActivity",
    )


def dialog_dismiss_texts() -> List[str]:
    section = _section("dialogs")
    raw = section.get("dismiss_texts", [])
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    return ["确认", "确定", "知道了", "我知道了", "同意", "允许", "继续"]


def gallery_fallback_text() -> str:
    return _str(_section("gallery"), "fallback_text", "相册")


def see_all_fallback_text() -> str:
    return _str(_section("gallery"), "see_all_fallback_text", "查看全部")


def feature_grid_resource_id() -> str:
    return _str(_section("feature"), "grid_resource_id", "com.moyoung.glasses:id/home_discovery_list")


def feature_text_resource_id() -> str:
    return _str(_section("feature"), "text_resource_id", "com.moyoung.glasses:id/tv_feature")


def feature_audio_text() -> str:
    return _str(_section("feature"), "audio_text", "录音")


def device_control_action() -> str:
    return _str(
        _section("device_control"),
        "action",
        "com.moyoung.glasses.action.DEVICE_CONTROL",
    )


def device_control_token(default: str = "") -> str:
    return _str(_section("device_control"), "token", default)


def device_control_command(name: str, default: str = "") -> str:
    commands = _section("device_control").get("commands", {})
    if not isinstance(commands, dict):
        return default
    return _str(commands, name, default or name)

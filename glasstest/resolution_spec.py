"""Expected photo/video WxH: device-config API first, then 图像视频标准输出规格.md."""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from glasstest.device_config_api import parse_size_text

Size = Tuple[int, int]

# 图片：不区分 BK/全志，严格按对照表
PHOTO_BY_PIXELS: Dict[str, Dict[str, Size]] = {
    "200": {"landscape": (1600, 1200), "portrait": (900, 1200)},
    "800": {"landscape": (3280, 2464), "portrait": (1848, 2464)},  # 800w 与 200插值800
    "1200": {"landscape": (4160, 3120), "portrait": (2329, 3120)},  # 800插值12MP
    "1300": {"landscape": (4160, 3120), "portrait": (2329, 3120)},  # 1300w
    "800_to_3200": {"landscape": (6560, 4928), "portrait": (3696, 4928)},  # 800插值3200
}

# 800W→3200W 只做插值、不做画质优化（对照表特殊行）
PHOTO_INTERP_800_TO_3200_VERSIONS = {"A1V", "A28", "A2V"}
PHOTO_800_NATIVE = PHOTO_BY_PIXELS["800"]["landscape"]

# 视频 · 全志
VIDEO_ALLWINNER_BY_PIXELS: Dict[str, Dict[str, Size]] = {
    "800": {"landscape": (1600, 1200), "portrait": (1440, 1920)},
    "1200": {"landscape": (1920, 1080), "portrait": (1440, 1920)},  # 插值12MP
    "1300": {"landscape": (1920, 1440), "portrait": (1440, 1920)},  # 插值1300w
}

# 视频 · BK
VIDEO_BK_BY_PIXELS: Dict[str, Dict[str, Size]] = {
    "200": {"landscape": (1280, 720), "portrait": (480, 720)},
    "800": {"landscape": (1280, 720), "portrait": (816, 1088)},
    "1200": {"landscape": (1920, 1080), "portrait": (1224, 1632)},
    "1300": {"landscape": (1920, 1080), "portrait": (1224, 1632)},
}

# camera.value → 对照表档位。接口标明优先用 value，不要只看 pixels。
VALUE_TO_PIXEL_KEY: Dict[str, str] = {
    "200": "200",
    "800": "800",
    "1200": "1200",
    "1300": "1300",
    "3200": "1300",
    "bk200": "200",
    "bk200_800": "800",
    "bk800": "800",
    "bk800_1300": "1300",
}

BK_VIDEO_TYPES = {"bk_video", "bk"}
ALLWINNER_VIDEO_TYPES = {"standard_video", "allwinner", "allwin", "six_axis", "全志"}


def _camera(config: Dict[str, Any]) -> Dict[str, Any]:
    camera = config.get("camera")
    return camera if isinstance(camera, dict) else {}


def resolve_pixel_key(config: Dict[str, Any]) -> Dict[str, str]:
    """档位：camera.value → pixels → 对照表默认 800w。"""
    camera = _camera(config)
    value = str(camera.get("value") or "").strip().lower()
    if value in VALUE_TO_PIXEL_KEY:
        return {"pixel_key": VALUE_TO_PIXEL_KEY[value], "source": "camera.value"}
    pixels = str(camera.get("pixels") or "").strip()
    if pixels in PHOTO_BY_PIXELS:
        return {"pixel_key": pixels, "source": "camera.pixels"}
    if pixels.isdigit():
        num = int(pixels)
        for key in ("200", "800", "1200", "1300"):
            if num <= int(key) + 50:
                return {"pixel_key": key, "source": "camera.pixels"}
        return {"pixel_key": "1300", "source": "camera.pixels"}
    return {"pixel_key": "800", "source": "table_default"}


def resolve_chip(config: Dict[str, Any]) -> Dict[str, Any]:
    """芯片：接口 video_type / bk* value 标明则用接口；否则对照表默认全志。"""
    video_type = str(config.get("video_type") or "").strip().lower()
    value = str(_camera(config).get("value") or "").strip().lower()
    if video_type in BK_VIDEO_TYPES:
        return {"chip": "bk", "source": "video_type", "video_type": video_type, "is_bk": True}
    if video_type in ALLWINNER_VIDEO_TYPES:
        return {"chip": "allwinner", "source": "video_type", "video_type": video_type, "is_bk": False}
    if value.startswith("bk"):
        return {"chip": "bk", "source": "camera.value", "video_type": video_type, "is_bk": True}
    return {
        "chip": "allwinner",
        "source": "table_default",
        "video_type": video_type,
        "is_bk": False,
    }


def _pixel_key_from_config(config: Dict[str, Any]) -> str:
    return resolve_pixel_key(config)["pixel_key"]


def _is_bk(config: Dict[str, Any]) -> bool:
    return bool(resolve_chip(config)["is_bk"])


def _soft_version(config: Dict[str, Any]) -> str:
    return str(config.get("soft_version_name") or "").strip().upper()


def is_photo_800_to_3200(config: Dict[str, Any]) -> bool:
    """A28/A2V/A1V：800W 只插值到 3200W，照片按 6560×4928 / 3696×4928。"""
    if _soft_version(config) not in PHOTO_INTERP_800_TO_3200_VERSIONS:
        return False
    return _pixel_key_from_config(config) == "800"


def expected_photo_size(config: Dict[str, Any], orientation: str) -> Optional[Size]:
    """接口自定义分辨率优先；A28/A2V/A1V 的 800W 走对照表特殊插值行。"""
    orient = _norm_orient(orientation)
    camera = _camera(config)
    special = is_photo_800_to_3200(config)
    row = PHOTO_BY_PIXELS["800_to_3200"] if special else (
        PHOTO_BY_PIXELS.get(_pixel_key_from_config(config)) or PHOTO_BY_PIXELS["800"]
    )
    if orient == "landscape":
        api_size = parse_size_text(camera.get("size"))
        # 这三款接口 size 仍是 800w 原图，实际 App 输出是 2 倍插值，不能把传感器尺寸当期望。
        if api_size and not (special and api_size == PHOTO_800_NATIVE):
            return api_size
        return row["landscape"]
    diff = parse_size_text(config.get("picture_diff_portrait"))
    if diff:
        return diff
    return row["portrait"]


def expected_video_size(config: Dict[str, Any], orientation: str) -> Optional[Size]:
    """接口 bk_video_diff 优先，否则按芯片表 + 档位对照表。"""
    orient = _norm_orient(orientation)
    if orient == "landscape":
        diff = parse_size_text(config.get("bk_video_diff"))
        if diff:
            return diff
    else:
        diff = parse_size_text(config.get("bk_video_diff_portrait"))
        if diff:
            return diff

    pixel_key = _pixel_key_from_config(config)
    table = VIDEO_BK_BY_PIXELS if _is_bk(config) else VIDEO_ALLWINNER_BY_PIXELS
    row = table.get(pixel_key) or table.get("800") or next(iter(table.values()))
    return row[orient]


def _norm_orient(orientation: str) -> str:
    text = (orientation or "").strip().lower()
    if text in {"portrait", "竖屏", "vertical"}:
        return "portrait"
    return "landscape"


def build_expectation_summary(config: Dict[str, Any]) -> Dict[str, Any]:
    chip = resolve_chip(config)
    pixel = resolve_pixel_key(config)
    return {
        "soft_version_name": config.get("soft_version_name"),
        "video_type": config.get("video_type"),
        "camera": config.get("camera"),
        "pixel_key": pixel["pixel_key"],
        "pixel_key_source": pixel["source"],
        "photo_interp": "800_to_3200" if is_photo_800_to_3200(config) else "",
        "chip": chip["chip"],
        "chip_source": chip["source"],
        "is_bk": chip["is_bk"],
        "photo": {
            "landscape": expected_photo_size(config, "landscape"),
            "portrait": expected_photo_size(config, "portrait"),
        },
        "video": {
            "landscape": expected_video_size(config, "landscape"),
            "portrait": expected_video_size(config, "portrait"),
        },
    }

"""Name-based App UI navigation (no hardcoded coordinates)."""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET

from glasstest.phone_operations import PhoneOperations

logger = logging.getLogger("app_ui")


def _parse_bounds(raw: str) -> Optional[Tuple[int, int, int, int]]:
    nums = list(map(int, re.findall(r"\d+", raw or "")))
    if len(nums) != 4:
        return None
    return nums[0], nums[1], nums[2], nums[3]


def _center(bounds: Tuple[int, int, int, int]) -> Tuple[int, int]:
    return (bounds[0] + bounds[2]) // 2, (bounds[1] + bounds[3]) // 2


def list_texts(xml: str) -> List[str]:
    texts: List[str] = []
    for elem in ET.fromstring(xml).iter("node"):
        text = (elem.attrib.get("text") or "").strip()
        if text:
            texts.append(text)
    return texts


def find_click_bounds_for_text(xml: str, text: str) -> Optional[Tuple[int, int, int, int]]:
    root = ET.fromstring(xml)
    for elem in root.iter("node"):
        if elem.attrib.get("clickable") != "true":
            continue
        child_texts = [(c.attrib.get("text") or "").strip() for c in elem.iter("node")]
        if text in child_texts:
            return _parse_bounds(elem.attrib.get("bounds", ""))
    for elem in root.iter("node"):
        if (elem.attrib.get("text") or "").strip() == text:
            return _parse_bounds(elem.attrib.get("bounds", ""))
    return None


def text_visible(xml: str, text: str) -> bool:
    return text in list_texts(xml)


def tap_by_name(phone: PhoneOperations, text: str, *, settle: float = 1.2) -> None:
    xml = phone.dump_hierarchy()
    bounds = find_click_bounds_for_text(xml, text)
    if bounds is None:
        visible = list_texts(xml)
        raise RuntimeError(f"找不到可点文案「{text}」。可见: {visible[:40]}")
    x, y = _center(bounds)
    logger.info("按名称点击「%s」-> (%s,%s) %s", text, x, y, bounds)
    phone.click(x, y)
    time.sleep(settle)


def press_back(phone: PhoneOperations, times: int = 1) -> None:
    for _ in range(max(1, times)):
        if phone.tap_resource_id("com.moyoung.glasses:id/btn_title_bar_back", timeout=0.8):
            time.sleep(0.5)
            continue
        # fallback system back
        try:
            phone.adb_shell("input keyevent 4")
        except Exception:
            pass
        time.sleep(0.5)


def recover_to_main_tabs(phone: PhoneOperations) -> str:
    """
    尽量回到带底栏（首页/设置）的主界面，返回当前 dump xml。
    """
    for _ in range(5):
        xml = phone.dump_hierarchy()
        texts = list_texts(xml)
        if "设置" in texts or "首页" in texts:
            return xml
        press_back(phone, 1)
    xml = phone.dump_hierarchy()
    if "设置" not in list_texts(xml) and "首页" not in list_texts(xml):
        phone.launch_app()
        time.sleep(2.0)
        xml = phone.dump_hierarchy()
    return xml


def go_home_tab(phone: PhoneOperations) -> None:
    xml = recover_to_main_tabs(phone)
    if text_visible(xml, "首页"):
        tap_by_name(phone, "首页", settle=1.0)
        return
    # 已在首页内容但文案偶发缺失
    if "导入" in list_texts(phone.dump_hierarchy()):
        return
    raise RuntimeError("无法回到首页底栏")


def open_settings(phone: PhoneOperations) -> None:
    xml = recover_to_main_tabs(phone)
    texts = list_texts(xml)
    # 已在设置一级页（有相机/固件等入口）
    if "相机" in texts and "固件" in texts:
        logger.info("已在设置页，跳过再点设置")
        return
    if "设置" not in texts:
        phone.launch_app()
        time.sleep(1.5)
        xml = recover_to_main_tabs(phone)
        texts = list_texts(xml)
    if "设置" not in texts:
        raise RuntimeError(f"找不到底栏「设置」。可见: {texts[:40]}")
    tap_by_name(phone, "设置", settle=1.2)


def _parse_firmware_page(xml: str) -> Dict[str, Any]:
    texts = list_texts(xml)
    mcu = ""
    device_name = ""
    for index, text in enumerate(texts):
        if text == "主控固件版本" and index + 1 < len(texts):
            mcu = texts[index + 1]
        if text == "设备名称" and index + 1 < len(texts):
            device_name = texts[index + 1]
    return {"device_name": device_name, "mcu_firmware": mcu, "all_texts": texts}


def read_mcu_firmware_version(phone: PhoneOperations) -> Dict[str, Any]:
    """设置 → 固件 → 固件信息 → 主控固件版本（容错当前已在中途页面）."""
    phone.launch_app()
    time.sleep(1.5)
    xml = recover_to_main_tabs(phone)
    texts = list_texts(xml)

    # 已在固件信息详情
    if "主控固件版本" in texts:
        result = _parse_firmware_page(xml)
        logger.info("已在固件信息页: device=%s mcu=%s", result["device_name"], result["mcu_firmware"])
        press_back(phone, 2)
        try:
            go_home_tab(phone)
        except Exception as exc:
            logger.warning("返回首页失败: %s", exc)
        return result

    # 已在固件菜单
    if "固件信息" in texts and "升级" in texts:
        tap_by_name(phone, "固件信息", settle=1.5)
    else:
        open_settings(phone)
        xml = phone.dump_hierarchy()
        texts = list_texts(xml)
        if "固件" not in texts:
            raise RuntimeError(f"设置页找不到「固件」。可见: {texts[:40]}")
        tap_by_name(phone, "固件", settle=1.2)
        tap_by_name(phone, "固件信息", settle=1.5)

    xml = phone.dump_hierarchy()
    result = _parse_firmware_page(xml)
    logger.info("固件信息: device=%s mcu=%s", result["device_name"], result["mcu_firmware"])
    if not result["mcu_firmware"]:
        raise RuntimeError(f"固件信息页未读到主控固件版本。可见: {result['all_texts'][:40]}")

    press_back(phone, 2)
    try:
        go_home_tab(phone)
    except Exception as exc:
        logger.warning("返回首页失败: %s", exc)
    return result


def set_video_orientation(phone: PhoneOperations, orientation: str) -> Dict[str, Any]:
    """Set video orientation when the camera page exposes a 视频比例 row.

    Some firmware exposes only 视频时长, which means video is portrait-only.
    In that case return supported=False instead of trying to change photo ratio.
    """
    target = "竖屏" if orientation in {"竖屏", "portrait", "vertical"} else "横屏"
    phone.launch_app()
    time.sleep(1.0)
    xml = recover_to_main_tabs(phone)
    if "视频比例" not in list_texts(xml):
        open_settings(phone)
        xml = phone.dump_hierarchy()
        if "相机" not in list_texts(xml):
            raise RuntimeError(f"设置页找不到「相机」。可见: {list_texts(xml)[:40]}")
        tap_by_name(phone, "相机", settle=1.2)
        xml = phone.dump_hierarchy()
    texts = list_texts(xml)
    if "视频比例" not in texts:
        logger.info("相机设置未显示视频比例，视频仅支持当前默认方向")
        press_back(phone, 1)
        go_home_tab(phone)
        return {"supported": False, "target": target, "reason": "未显示视频比例"}
    current = ""
    for index, text in enumerate(texts):
        if text == "视频比例" and index + 1 < len(texts) and texts[index + 1] in {"横屏", "竖屏"}:
            current = texts[index + 1]
            break
    if not current:
        current = "横屏" if "横屏" in texts else ("竖屏" if "竖屏" in texts else "")
    if current != target:
        open_label = current if current in {"横屏", "竖屏"} else "视频比例"
        tap_by_name(phone, open_label, settle=1.2)
        texts2 = list_texts(phone.dump_hierarchy())
        if target not in texts2:
            raise RuntimeError(f"视频比例弹层找不到「{target}」。可见: {texts2[:40]}")
        tap_by_name(phone, target, settle=1.2)
    confirmed = False
    texts3 = list_texts(phone.dump_hierarchy())
    for index, text in enumerate(texts3):
        if text == "视频比例" and index + 1 < len(texts3) and texts3[index + 1] == target:
            confirmed = True
            break
    press_back(phone, 1)
    go_home_tab(phone)
    return {"supported": True, "target": target, "previous": current, "changed": current != target, "confirmed": confirmed}


def set_photo_orientation(phone: PhoneOperations, orientation: str) -> Dict[str, Any]:
    """设置 → 相机 → 点当前比例值 → 选 横屏/竖屏."""
    target = "竖屏" if orientation in {"竖屏", "portrait", "vertical"} else "横屏"
    phone.launch_app()
    time.sleep(1.0)
    xml = recover_to_main_tabs(phone)

    # 已在相机页
    if "照片比例" not in list_texts(xml):
        open_settings(phone)
        xml = phone.dump_hierarchy()
        if "相机" not in list_texts(xml):
            raise RuntimeError(f"设置页找不到「相机」。可见: {list_texts(xml)[:40]}")
        tap_by_name(phone, "相机", settle=1.2)
        xml = phone.dump_hierarchy()

    texts = list_texts(xml)
    if "照片比例" not in texts:
        raise RuntimeError(f"未进入相机页（无「照片比例」）。可见: {texts[:40]}")

    current = ""
    for index, text in enumerate(texts):
        if text == "照片比例" and index + 1 < len(texts) and texts[index + 1] in {"横屏", "竖屏"}:
            current = texts[index + 1]
            break
    if not current:
        if "横屏" in texts:
            current = "横屏"
        elif "竖屏" in texts:
            current = "竖屏"

    logger.info("照片比例当前=%s 目标=%s", current, target)
    if current == target and "视频时长" in texts:
        press_back(phone, 1)
        go_home_tab(phone)
        return {"target": target, "previous": current, "changed": False, "confirmed": True}

    open_label = current if current in {"横屏", "竖屏"} else ("横屏" if "横屏" in texts else "照片比例")
    tap_by_name(phone, open_label, settle=1.5)
    xml2 = phone.dump_hierarchy()
    texts2 = list_texts(xml2)
    if target not in texts2:
        # 再试点照片比例行
        if "照片比例" in texts2:
            tap_by_name(phone, "照片比例", settle=1.5)
            xml2 = phone.dump_hierarchy()
            texts2 = list_texts(xml2)
    if target not in texts2:
        raise RuntimeError(f"打开比例弹层后找不到「{target}」。可见: {texts2[:40]}")
    tap_by_name(phone, target, settle=1.5)

    xml3 = phone.dump_hierarchy()
    texts3 = list_texts(xml3)
    confirmed = False
    for index, text in enumerate(texts3):
        if text == "照片比例" and index + 1 < len(texts3) and texts3[index + 1] == target:
            confirmed = True
            break
    if not confirmed and target in texts3 and "视频时长" in texts3:
        confirmed = True
    if not confirmed:
        logger.warning("切换后未能确认照片比例=%s，可见=%s", target, texts3[:40])

    press_back(phone, 1)
    go_home_tab(phone)
    return {
        "target": target,
        "previous": current,
        "changed": current != target,
        "confirmed": confirmed,
    }

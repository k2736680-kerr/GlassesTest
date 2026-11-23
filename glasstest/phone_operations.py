#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from xml.etree import ElementTree as ET

import glasstest.config as app_config
from glasstest.config import (
    AUTOMATION_DEFAULT_IMPORT_TIMEOUT_SECONDS,
    AUTOMATION_IMPORT_STALL_TIMEOUT_SECONDS,
    AUTOMATION_IMPORT_SUCCESS_SETTLE_SECONDS,
    AUTOMATION_PROGRESS_POLL_INTERVAL_SECONDS,
    HOME_IMPORT_DESC,
    HOME_IMPORT_RESOURCE_ID,
    HOME_IMPORT_TEXT,
    HOME_IMPORT_XPATH,
    IMPORT_ERROR_LOG_FILE,
    IMPORT_LOG_DIR,
    IMPORT_SUCCESS_LOG_FILE,
    PHONE_APP_PACKAGE,
    PHONE_DEVICE_SERIAL,
)
import glasstest.ui_selectors as ui_selectors

try:
    import uiautomator2 as u2
except ImportError:
    u2 = None


DISPATCH_SUCCESS_KEYWORD = "dispatched"
BROADCAST_FATAL_KEYWORDS = (
    "invalid token",
    "missing command",
    "unknown command",
    "invalid duration",
    "unsupported action",
    "device not connected",
)

# Populated by refresh_selector_constants(); kept as module globals for call-site compatibility.
DEVICE_CONTROL_ACTION = app_config.DEVICE_CONTROL_ACTION
DEVICE_CONTROL_TOKEN = app_config.DEVICE_CONTROL_TOKEN
DEVICE_CONTROL_CMD_TAKE_PHOTO = "take_photo"
DEVICE_CONTROL_CMD_START_AUDIO = "start_audio"
DEVICE_CONTROL_CMD_STOP_AUDIO = "stop_audio"
DEVICE_CONTROL_CMD_START_VIDEO = "start_video"
DEVICE_CONTROL_CMD_STOP_VIDEO = "stop_video"
IMPORT_CARD_RESOURCE_ID = "com.moyoung.glasses:id/media_import_view"
GALLERY_SEE_ALL_RESOURCE_ID = ""
GALLERY_TITLE_RESOURCE_ID = ""
GALLERY_SECTION_RESOURCE_ID = "com.moyoung.glasses:id/home_gallery_section"
MEDIA_GRID_RESOURCE_ID = "com.moyoung.glasses:id/home_gallery_list"
FEATURE_GRID_RESOURCE_ID = "com.moyoung.glasses:id/home_discovery_list"
FEATURE_TEXT_RESOURCE_ID = "com.moyoung.glasses:id/tv_feature"
HOME_TAB_BUTTON_RESOURCE_ID = ""
HOME_TAB_TEXT = "首页"
HOME_CONTENT_RESOURCE_ID = "com.moyoung.glasses:id/home_content"
BACK_BUTTON_RESOURCE_ID = "com.moyoung.glasses:id/iv_back"
LAUNCH_ACTIVITY = "com.moyoung.glasses/.users.welcome.SplashActivity"
FEATURE_AUDIO_TEXT = "录音"
GALLERY_FALLBACK_TEXT = "相册"
SEE_ALL_FALLBACK_TEXT = "查看全部"
IMPORT_BUTTON_TEXT = "导入"
DIALOG_DISMISS_TEXTS = [
    "结束并开启导入文件",
    "确认", "确定", "知道了", "我知道了", "同意", "允许", "继续",
    "关闭", "稍后", "暂不", "跳过", "以后再说", "不再提示", "不再提醒", "好的", "OK", "Got it",
]


def refresh_selector_constants() -> None:
    """Reload selectors.json into module-level constants."""
    global DEVICE_CONTROL_ACTION, DEVICE_CONTROL_TOKEN
    global DEVICE_CONTROL_CMD_TAKE_PHOTO, DEVICE_CONTROL_CMD_START_AUDIO, DEVICE_CONTROL_CMD_STOP_AUDIO
    global DEVICE_CONTROL_CMD_START_VIDEO, DEVICE_CONTROL_CMD_STOP_VIDEO
    global IMPORT_CARD_RESOURCE_ID, GALLERY_SEE_ALL_RESOURCE_ID, GALLERY_TITLE_RESOURCE_ID
    global GALLERY_SECTION_RESOURCE_ID, MEDIA_GRID_RESOURCE_ID, FEATURE_GRID_RESOURCE_ID, FEATURE_TEXT_RESOURCE_ID
    global HOME_TAB_BUTTON_RESOURCE_ID, HOME_TAB_TEXT, HOME_CONTENT_RESOURCE_ID, BACK_BUTTON_RESOURCE_ID
    global LAUNCH_ACTIVITY, FEATURE_AUDIO_TEXT, GALLERY_FALLBACK_TEXT, SEE_ALL_FALLBACK_TEXT, IMPORT_BUTTON_TEXT
    global DIALOG_DISMISS_TEXTS

    ui_selectors.load_selectors(force_reload=True)
    file_token = ui_selectors.device_control_token("")
    DEVICE_CONTROL_TOKEN = file_token or app_config.DEVICE_CONTROL_TOKEN
    DEVICE_CONTROL_ACTION = ui_selectors.device_control_action() or app_config.DEVICE_CONTROL_ACTION
    DEVICE_CONTROL_CMD_TAKE_PHOTO = ui_selectors.device_control_command("take_photo", "take_photo")
    DEVICE_CONTROL_CMD_START_AUDIO = ui_selectors.device_control_command("start_audio", "start_audio")
    DEVICE_CONTROL_CMD_STOP_AUDIO = ui_selectors.device_control_command("stop_audio", "stop_audio")
    DEVICE_CONTROL_CMD_START_VIDEO = ui_selectors.device_control_command("start_video", "start_video")
    DEVICE_CONTROL_CMD_STOP_VIDEO = ui_selectors.device_control_command("stop_video", "stop_video")
    IMPORT_CARD_RESOURCE_ID = ui_selectors.import_card_resource_id()
    GALLERY_SEE_ALL_RESOURCE_ID = ui_selectors.gallery_see_all_resource_id()
    GALLERY_TITLE_RESOURCE_ID = ui_selectors.gallery_title_resource_id()
    GALLERY_SECTION_RESOURCE_ID = ui_selectors.gallery_section_resource_id()
    MEDIA_GRID_RESOURCE_ID = ui_selectors.media_grid_resource_id()
    FEATURE_GRID_RESOURCE_ID = ui_selectors.feature_grid_resource_id()
    FEATURE_TEXT_RESOURCE_ID = ui_selectors.feature_text_resource_id()
    HOME_TAB_BUTTON_RESOURCE_ID = ui_selectors.home_tab_button_resource_id()
    HOME_TAB_TEXT = ui_selectors.home_tab_text()
    HOME_CONTENT_RESOURCE_ID = ui_selectors.home_content_resource_id()
    BACK_BUTTON_RESOURCE_ID = ui_selectors.back_button_resource_id()
    LAUNCH_ACTIVITY = ui_selectors.launch_activity()
    FEATURE_AUDIO_TEXT = ui_selectors.feature_audio_text()
    GALLERY_FALLBACK_TEXT = ui_selectors.gallery_fallback_text()
    SEE_ALL_FALLBACK_TEXT = ui_selectors.see_all_fallback_text()
    IMPORT_BUTTON_TEXT = ui_selectors.import_button_text()
    DIALOG_DISMISS_TEXTS = ui_selectors.dialog_dismiss_texts() or list(DIALOG_DISMISS_TEXTS)


refresh_selector_constants()
IMPORT_MEDIA_COUNT_PATTERN = re.compile(r"已找到\s*(\d+)\s*个媒体文件")
IMPORT_PROGRESS_PATTERN = re.compile(r"\b(\d{1,3})%")
# 部分机型不显示 %，只显示「3/6」这类完成数/总数。
IMPORT_FRACTION_PATTERN = re.compile(r"(?<!\d)(\d{1,3})\s*/\s*(\d{1,3})(?!\d)")
IMPORT_DOWNLOAD_STATE_PATTERN = re.compile(r"updateDownloadState\s*:\s*([A-Z_]+)\s+currentDownloadState:([A-Z_]+)")
IMPORT_MEDIA_EVENT_PATTERN = re.compile(r"MediaDownloadEvent\{type=([A-Z_]+)")
IMPORT_PROGRESS_LOG_PATTERN = re.compile(r"downloadProgress=(\d+)")
IMPORT_TOTAL_COUNT_PATTERN = re.compile(r"totalCount=(\d+)")
IMPORT_SUCCESS_COUNT_PATTERN = re.compile(r"downloadSuccessCount=(\d+)")
IMPORT_WIFI_ERR_CODE_PATTERN = re.compile(r"wifiEnableErrorCode=(\d+)")
IMPORT_SUCCESS_PATH_LIST_PATTERN = re.compile(r"downloadSuccessPathList=\[(.*?)\]")
IMPORT_MEDIA_COUNT_LOG_PATTERN = re.compile(
    r"onNewMediaFileChanged:\s*CRPMediaFileInfo\{photoCount=(\d+),\s*videoCount=(\d+),\s*audioCount=(\d+)\}"
)
# New App (2.6.x) structured event logs
IMPORT_NEW_MEDIA_TYPE_PATTERN = re.compile(
    r"event=media_download(?:,|\s)type=([A-Z_]+)",
    re.IGNORECASE,
)
IMPORT_NEW_PROGRESS_PATTERN = re.compile(
    r"event=(?:media_download_progress|download_progress).*?\bprogress=(\d+)",
    re.IGNORECASE,
)
IMPORT_NEW_STAGE_PATTERN = re.compile(
    r"event=(?:task_snapshot|display_published|render).*?\bstage=([A-Z_]+)",
    re.IGNORECASE,
)
IMPORT_NEW_FILE_PATH_PATTERN = re.compile(
    r"event=download_file_item,path=(.+?\.(?:jpg|jpeg|png|webp|heic|bmp|mp4|mov|mkv|avi|3gp|webm|opus|wav|mp3|aac|m4a|ogg|amr))",
    re.IGNORECASE,
)
IMPORT_NEW_TERMINAL_PATTERN = re.compile(
    r"event=terminal_dispatch,type=([A-Z_]+)",
    re.IGNORECASE,
)
IMPORT_NEW_TOTAL_PATTERN = re.compile(r"\btotal=(\d+)")
IMPORT_NEW_SUCCESS_COUNT_PATTERN = re.compile(r"\bsuccess=(\d+)")
IMPORT_NEW_DOWNLOADED_PATTERN = re.compile(r"\bdownloaded=(\d+)")
# 不按 --pid 过滤时，只保留导入相关行，避免整机 logcat 把队列打爆。
IMPORT_LOG_KEEP_PATTERN = re.compile(
    r"downloadProgress|downloadSuccess|MediaDownload|updateDownloadState|"
    r"wifiEnable|wifiConnect|Wifi|WiFi|totalCount|downloadSuccessCount|downloadSuccessPathList|"
    r"onNewMediaFileChanged|download_file_item|media_download|terminal_dispatch|"
    r"processMediaFiles|mediaItems|moyoung|media_res|BkAvi|CRPMedia|"
    r"event=(?:media_download|download_progress|task_snapshot|display_published)|"
    r"导入|下载|传输|文件下载|Downloaded file|wifi_connect|WIFI_",
    re.IGNORECASE,
)


def _subprocess_hidden_kwargs() -> Dict[str, Any]:
    """Windows 下隐藏 adb 黑框，避免导入轮询时窗口狂闪。"""
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0  # SW_HIDE
    flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    return {"startupinfo": startupinfo, "creationflags": flags}
MEDIA_ACTIVITY_COUNT_PATTERNS = (
    re.compile(r"processMediaFiles:\s*items\.size\(\)\s*=\s*(\d+)", re.IGNORECASE),
    re.compile(r"processMediaFiles:\s*files\.length\s*=\s*(\d+)", re.IGNORECASE),
    re.compile(r"mediaItems size\s*=\s*(\d+)", re.IGNORECASE),
)
BATTERY_CHARGING_LOG_PATTERN = re.compile(r"onBatteryChange:\s*charging:\s*(true|false)", re.IGNORECASE)
BATTERY_CHARGING_EVENT_PATTERN = re.compile(r"onBatteryChangeEvent:\s*(true|false)", re.IGNORECASE)
BATTERY_LOW_LEVEL_PATTERN = re.compile(r"\blow[_\s-]*lvl\s*[:=]\s*(\d{1,3})\b", re.IGNORECASE)
BATTERY_POWER_OFF_LEVEL_PATTERN = re.compile(r"\bpower[_\s-]*off[_\s-]*lvl\s*[:=]\s*(\d{1,3})\b", re.IGNORECASE)
BATTERY_LEVEL_LOG_PATTERNS = (
    re.compile(r"\bbattery(?:_|\s)*(?:level|lvl)\s*[:=]\s*(\d{1,3})\b", re.IGNORECASE),
    re.compile(r"\bcurrent(?:_|\s)*battery(?:_|\s)*(?:level|lvl)?\s*[:=]\s*(\d{1,3})\b", re.IGNORECASE),
)
BATTERY_PERCENT_PATTERN = re.compile(r"\b(\d{1,3})\s*%")
BATTERY_PERCENT_ZH_PATTERN = re.compile(r"\u767e\u5206\u4e4b\s*(\d{1,3})")
BATTERY_LOW_KEYWORDS = (
    "battery too low",
    "low battery",
    "battery low",
    "insufficient battery",
    "battery insufficient",
    "power too low",
    "\u7535\u91cf\u8fc7\u4f4e",
    "\u7535\u91cf\u4e0d\u8db3",
    "\u4f4e\u7535\u91cf",
    "\u7535\u91cf\u4f4e",
)
BATTERY_CHARGING_HINTS = ("\u5145\u7535\u4e2d", "charging")
BATTERY_NOT_CHARGING_HINTS = ("\u672a\u5145\u7535", "not charging")
BATTERY_RESOURCE_KEYWORDS = ("battery", "\u7535\u91cf", "\u7535\u6c60")
UI_BATTERY_LOW_HINTS = ("\u7535\u91cf\u8fc7\u4f4e", "\u4f4e\u7535\u91cf", "\u7535\u91cf\u4e0d\u8db3")
STORAGE_FULL_KEYWORDS = (
    "no space left on device",
    "enospc",
    "database or disk is full",
    "disk full",
    "storage is full",
    "storage full",
    "insufficient storage",
    "not enough storage",
    "no enough space",
    "not enough space",
    "space full",
    "\u5b58\u50a8\u7a7a\u95f4\u4e0d\u8db3",
    "\u5b58\u50a8\u7a7a\u95f4\u5df2\u6ee1",
    "\u624b\u673a\u7a7a\u95f4\u4e0d\u8db3",
    "\u624b\u673a\u7a7a\u95f4\u5df2\u6ee1",
    "\u624b\u673a\u5b58\u50a8\u7a7a\u95f4\u4e0d\u8db3",
    "\u624b\u673a\u5b58\u50a8\u5df2\u6ee1",
    "\u5b58\u50a8\u4e0d\u8db3",
    "\u7a7a\u95f4\u4e0d\u8db3",
    "\u7a7a\u95f4\u5df2\u6ee1",
    "\u5b58\u50a8\u5df2\u6ee1",
    "\u5b58\u50a8\u5df2\u6ee1\uff0c\u8bf7\u6e05\u7406",
    "\u5185\u5b58\u4e0d\u8db3",
    "\u5bb9\u91cf\u4e0d\u8db3",
    "\u5269\u4f59\u7a7a\u95f4\u4e0d\u8db3",
    "\u78c1\u76d8\u5df2\u6ee1",
)

PHASE_IDLE = "IDLE"
PHASE_FOUND = "FOUND"
PHASE_CONNECT = "CONNECT"
PHASE_DOWNLOAD = "DOWNLOAD"
PHASE_SUCCESS = "SUCCESS"
PHASE_IDLE_AFTER_SUCCESS = "IDLE-after-success"
PHASE_FAILED = "FAILED"

HARD_ERROR_KEYWORDS = (
    "download_failed",
    "download failed",
    "type=wifi_connect_fail",
    "type=wifi_enable_fail",
    "type=download_failed",
    "updatedownloadstate :failed",
)
SOFT_WARNING_KEYWORDS = ("sqliteexception", "parseerrormsg: 指令写入错误")
UI_SUCCESS_HINTS = (
    "已成功导入",
    "导入完成",
    "导入成功",
    "全部完成",
    "导入结束",
    "传输完成",
    "下载完成",
    "同步完成",
)
UI_WAITING_HINTS = (
    "等待wi-fi连接",
    "等待wifi连接",
    "正在等待wi-fi连接",
    "正在等待wifi连接",
    "正在连接wi-fi",
    "正在连接wifi",
    "连接wi-fi",
    "连接wifi",
    "wi-fi连接中",
    "wifi连接中",
    "等待连接",
    "正在连接",
    "请将眼镜靠近手机",
    "请保持 app 开启",
    "请保持app开启",
)
# 界面已在传，但可能没有数字进度条（本轮日志里的「正在导入」）。
UI_IMPORTING_HINTS = (
    "正在导入",
    "导入中",
    "正在传输",
    "传输中",
    "正在下载",
    "下载中",
    "同步中",
)
# Status-bar / notification shade noise that pollutes stall logs on OPPO etc.
_SCREEN_NOISE_MARKERS = (
    "通知：",
    "通知:",
    "状态栏",
    "蓝牙已连接",
    "WLAN",
    "电池 ",
    "未连接到互联网",
    "信号满格",
    "音乐通知",
    "小布助手",
    "OPPO 社区",
    "我的 OPPO",
    "Snapchat",
)
_SCREEN_KEEP_MARKERS = (
    "导入",
    "wi-fi",
    "wifi",
    "等待",
    "媒体",
    "找到",
    "传输",
    "下载",
    "成功",
    "失败",
    "靠近",
    "保持",
    "进度",
    "已连接",
    "拍照",
    "录像",
    "录音",
    "相册",
)


def _summarize_screen_texts(texts: Sequence[str], limit: int = 8) -> str:
    """Keep import-relevant UI labels; drop notification / status-bar ads."""
    useful: List[str] = []
    seen = set()
    for raw in texts or []:
        text = str(raw or "").strip()
        if not text or text in seen or len(text) > 60:
            continue
        lowered = text.lower()
        if any(marker in text or marker.lower() in lowered for marker in _SCREEN_NOISE_MARKERS):
            continue
        keep = any(marker.lower() in lowered for marker in _SCREEN_KEEP_MARKERS)
        keep = keep or (text.startswith("V") and len(text) <= 8)
        keep = keep or (text.endswith("%") and any(ch.isdigit() for ch in text))
        keep = keep or ("/" in text and text[0].isdigit())
        keep = keep or text.replace(".", "", 1).isdigit()
        if not keep:
            continue
        seen.add(text)
        useful.append(text)
        if len(useful) >= limit:
            break
    if useful:
        return " | ".join(useful)
    # Fallback: first few short non-noise labels.
    for raw in texts or []:
        text = str(raw or "").strip()
        if not text or len(text) > 40:
            continue
        if any(marker in text for marker in _SCREEN_NOISE_MARKERS):
            continue
        if text not in seen:
            seen.add(text)
            useful.append(text)
        if len(useful) >= min(4, limit):
            break
    return " | ".join(useful) if useful else "(无关键界面文案)"
UI_IMPORT_FAILED_HINTS = (
    "import failed",
    "download failed",
    "导入失败",
    "下载失败",
    "传输失败",
    "获取 wi-fi 信息失败",
    "获取wifi信息失败",
)


LOG_FORMAT = "%(asctime)s | %(levelname)s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

logger = logging.getLogger("phone_operations")
logger.setLevel(logging.INFO)
logger.handlers.clear()
logger.propagate = False
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
logger.addHandler(handler)


@dataclass
class UiNode:
    node_id: str
    depth: int
    class_name: str = ""
    text: str = ""
    resource_id: str = ""
    content_desc: str = ""
    bounds: str = ""
    clickable: bool = False
    enabled: bool = False
    focusable: bool = False
    scrollable: bool = False
    children: Optional[List["UiNode"]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "depth": self.depth,
            "class_name": self.class_name,
            "text": self.text,
            "resource_id": self.resource_id,
            "content_desc": self.content_desc,
            "bounds": self.bounds,
            "clickable": self.clickable,
            "enabled": self.enabled,
            "focusable": self.focusable,
            "scrollable": self.scrollable,
            "children": [child.to_dict() for child in self.children or []],
        }


@dataclass
class ImportWaitResult:
    success: bool = False
    status: str = "running"
    phase: str = PHASE_IDLE
    progress_percent: Optional[int] = None
    media_count: Optional[int] = None
    first_progress_percent: Optional[int] = None
    progress_reached_100: bool = False
    duration_seconds: float = 0.0
    success_paths: List[str] = field(default_factory=list)
    current_cycle_success_paths: List[str] = field(default_factory=list)
    ignored_success_paths: List[str] = field(default_factory=list)
    unknown_time_success_paths: List[str] = field(default_factory=list)
    success_path_filter: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    error_signals: List[str] = field(default_factory=list)
    artifact_paths: Dict[str, str] = field(default_factory=dict)
    session_id: str = ""
    failure_reason: str = ""
    battery_low_detected: bool = False
    storage_full_detected: bool = False
    battery_level: Optional[int] = None
    battery_low_level: Optional[int] = None
    battery_power_off_level: Optional[int] = None
    battery_charging: Optional[bool] = None
    click_confirmed_at: str = ""
    first_waiting_wifi_at: str = ""
    first_progress_at: str = ""
    download_started_at: str = ""
    final_success_at: str = ""
    waiting_wifi_seconds: Optional[float] = None
    transfer_duration_seconds: Optional[float] = None
    expected_counts: Dict[str, int] = field(default_factory=dict)
    observed_transfer_counts: Dict[str, Optional[int]] = field(default_factory=dict)
    post_import_validation: Dict[str, Any] = field(default_factory=dict)
    summary_artifacts: Dict[str, str] = field(default_factory=dict)
    started_at: str = ""
    finished_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "status": self.status,
            "phase": self.phase,
            "progress_percent": self.progress_percent,
            "media_count": self.media_count,
            "first_progress_percent": self.first_progress_percent,
            "progress_reached_100": self.progress_reached_100,
            "duration_seconds": self.duration_seconds,
            "success_paths": list(self.success_paths),
            "current_cycle_success_paths": list(self.current_cycle_success_paths),
            "ignored_success_paths": list(self.ignored_success_paths),
            "unknown_time_success_paths": list(self.unknown_time_success_paths),
            "success_path_filter": dict(self.success_path_filter),
            "warnings": list(self.warnings),
            "error_signals": list(self.error_signals),
            "artifact_paths": dict(self.artifact_paths),
            "session_id": self.session_id,
            "failure_reason": self.failure_reason,
            "battery_low_detected": self.battery_low_detected,
            "storage_full_detected": self.storage_full_detected,
            "battery_level": self.battery_level,
            "battery_low_level": self.battery_low_level,
            "battery_power_off_level": self.battery_power_off_level,
            "battery_charging": self.battery_charging,
            "click_confirmed_at": self.click_confirmed_at,
            "first_waiting_wifi_at": self.first_waiting_wifi_at,
            "first_progress_at": self.first_progress_at,
            "download_started_at": self.download_started_at,
            "final_success_at": self.final_success_at,
            "waiting_wifi_seconds": self.waiting_wifi_seconds,
            "transfer_duration_seconds": self.transfer_duration_seconds,
            "expected_counts": dict(self.expected_counts),
            "observed_transfer_counts": dict(self.observed_transfer_counts),
            "post_import_validation": dict(self.post_import_validation),
            "summary_artifacts": dict(self.summary_artifacts),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    def __bool__(self) -> bool:  # keep backward-compatible truthy checks
        return self.success


@dataclass
class _LogcatMonitor:
    process: subprocess.Popen
    queue: "Queue[str]"
    reader_thread: Thread
    pid: int
    package_name: str
    started_at: float


def _ensure_u2() -> None:
    if u2 is None:
        raise RuntimeError("uiautomator2 is not installed")


def _require_device_serial(device_serial: Optional[str]) -> str:
    serial = (device_serial or app_config.PHONE_DEVICE_SERIAL or PHONE_DEVICE_SERIAL or "").strip()
    if not serial:
        raise ValueError("PHONE_DEVICE_SERIAL is not configured")
    return serial


def _parse_node(elem: ET.Element, depth: int = 0, path: str = "0") -> UiNode:
    children: List[UiNode] = []
    for index, child in enumerate(list(elem)):
        children.append(_parse_node(child, depth + 1, f"{path}.{index}"))
    return UiNode(
        node_id=path,
        depth=depth,
        class_name=elem.attrib.get("class", ""),
        text=elem.attrib.get("text", ""),
        resource_id=elem.attrib.get("resource-id", ""),
        content_desc=elem.attrib.get("content-desc", ""),
        bounds=elem.attrib.get("bounds", ""),
        clickable=elem.attrib.get("clickable", "false") == "true",
        enabled=elem.attrib.get("enabled", "false") == "true",
        focusable=elem.attrib.get("focusable", "false") == "true",
        scrollable=elem.attrib.get("scrollable", "false") == "true",
        children=children,
    )


def _normalize(value: str) -> str:
    return str(value or "").strip().lower()


def _now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _ts_to_text(timestamp: Optional[float]) -> str:
    if timestamp is None:
        return ""
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))
    except Exception:
        return ""


def _bounds_rect(bounds: str) -> Optional[Tuple[int, int, int, int]]:
    match = re.findall(r"\[(\d+),(\d+)\]", bounds or "")
    if len(match) != 2:
        return None
    x1, y1 = int(match[0][0]), int(match[0][1])
    x2, y2 = int(match[1][0]), int(match[1][1])
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2


def _bounds_center(bounds: str) -> Optional[Tuple[int, int]]:
    rect = _bounds_rect(bounds)
    if rect is None:
        return None
    x1, y1, x2, y2 = rect
    return int((x1 + x2) / 2), int((y1 + y2) / 2)


def _screen_size_from_root(root: ET.Element) -> Tuple[int, int]:
    width, height = 0, 0
    for elem in root.iter("node"):
        rect = _bounds_rect(str(elem.attrib.get("bounds", "") or ""))
        if rect is None:
            continue
        _x1, _y1, x2, y2 = rect
        width = max(width, x2)
        height = max(height, y2)
    if width < 200 or height < 200:
        return 1080, 2340
    return width, height


def feature_tap_point(
    xml_text: str,
    label_text: str,
    *,
    feature_text_id: str = "",
    feature_grid_id: str = "",
) -> Optional[Tuple[int, int]]:
    """Locate discovery tile by visible text + resource-id (not by pixel size).

    Home capture row also shows「录音」but has no ``tv_feature`` id — ignore it,
    otherwise we would start a glasses recording.
    """
    target = str(label_text or "").strip()
    text_id = feature_text_id or FEATURE_TEXT_RESOURCE_ID
    grid_id = feature_grid_id or FEATURE_GRID_RESOURCE_ID
    if not target or not xml_text:
        return None
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return None
    width, height = _screen_size_from_root(root)
    # (priority, -visible_area, cx, cy) — prefer id match, then more on-screen area.
    candidates: List[Tuple[int, int, int, int]] = []

    def _consider(bounds: str, priority: int) -> None:
        rect = _bounds_rect(bounds)
        if rect is None:
            return
        x1, y1, x2, y2 = rect
        # Discard only invalid / fully off-screen boxes. No min px size — phones differ.
        if x2 <= x1 or y2 <= y1:
            return
        if x2 <= 0 or y2 <= 0 or x1 >= width or y1 >= height:
            return
        vis_x1, vis_y1 = max(0, x1), max(0, y1)
        vis_x2, vis_y2 = min(width, x2), min(height, y2)
        visible = max(0, vis_x2 - vis_x1) * max(0, vis_y2 - vis_y1)
        if visible <= 0:
            return
        candidates.append((priority, -visible, int((x1 + x2) / 2), int((y1 + y2) / 2)))

    for elem in root.iter("node"):
        text = str(elem.attrib.get("text", "") or "").strip()
        if text != target:
            continue
        rid = str(elem.attrib.get("resource-id", "") or "").strip()
        if rid == text_id:
            _consider(str(elem.attrib.get("bounds", "") or ""), 0)
    for grid in root.iter("node"):
        if str(grid.attrib.get("resource-id", "") or "").strip() != grid_id:
            continue
        for elem in grid.iter("node"):
            text = str(elem.attrib.get("text", "") or "").strip()
            if text != target:
                continue
            _consider(str(elem.attrib.get("bounds", "") or ""), 1)
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][2], candidates[0][3]


def discovery_marker_y(xml_text: str) -> Optional[int]:
    """Y of the discovery title or list. Used to tell whether a swipe moved the page."""
    if not xml_text:
        return None
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return None
    for resource_id in (
        "com.moyoung.glasses:id/home_discovery_title",
        FEATURE_GRID_RESOURCE_ID,
    ):
        for elem in root.iter("node"):
            if str(elem.attrib.get("resource-id", "") or "").strip() != resource_id:
                continue
            rect = _bounds_rect(str(elem.attrib.get("bounds", "") or ""))
            if rect is not None:
                return rect[1]
    return None


def home_can_scroll(xml_text: str) -> bool:
    if not xml_text:
        return False
    return "home_scroll_view" in xml_text or "home_discovery_list" in xml_text


def _append_unique(items: List[str], value: str) -> None:
    text = str(value or "").strip()
    if text and text not in items:
        items.append(text)


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except Exception:
        return None


def _safe_bool(value: Any) -> Optional[bool]:
    text = _normalize(str(value))
    if text in ("true", "1", "yes", "y"):
        return True
    if text in ("false", "0", "no", "n"):
        return False
    return None


def _infer_media_type_from_path(path_value: str) -> str:
    value = str(path_value or "").strip().replace("\\", "/").rstrip("/")
    if not value:
        return ""
    name = os.path.basename(value).lower()
    if not name:
        return ""
    ext = os.path.splitext(name)[1]

    image_exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic"}
    audio_exts = {".opus", ".wav", ".mp3", ".aac", ".m4a", ".ogg", ".amr"}
    video_exts = {".mp4", ".mov", ".mkv", ".avi", ".3gp", ".webm"}

    if ext in image_exts:
        return "photo"
    if ext in audio_exts:
        return "audio"
    if ext in video_exts:
        return "video"
    # cache_*_video-<stamp> (no ext) and Nstabilized-<stamp>.mp4
    if "video-" in name or "stabilized-" in name:
        return "video"
    return ""


def _media_identity_key(path_value: str) -> str:
    """Collapse cache_*_<id>.ext and <id>.ext into one identity."""
    value = str(path_value or "").strip().replace("\\", "/").rstrip("/")
    name = os.path.basename(value).lower()
    if not name:
        return ""
    stem, ext = os.path.splitext(name)
    # cache_<hash>_20261027151028013.jpg / cache_<hash>_1793085906.opus -> <id>.ext
    if stem.startswith("cache_"):
        parts = [part for part in stem.split("_") if part]
        if len(parts) >= 3 and parts[-1]:
            return f"{parts[-1]}{ext}"
    # 20261027151028013.jpg style capture ids
    match = re.search(r"(20\d{12,16})$", stem)
    if match:
        return f"{match.group(1)}{ext}"
    return name


def _path_preference_score(path_value: str) -> int:
    lowered = str(path_value or "").replace("\\", "/").lower()
    base = os.path.basename(lowered)
    score = 0
    if "media_res/" in lowered:
        score += 3
    if "/cache/" in lowered or base.startswith("cache_"):
        score -= 2
    return score


def dedupe_import_paths(paths: Iterable[str]) -> List[str]:
    """Prefer media_res/final paths over cache duplicates of the same capture."""
    preferred: Dict[str, str] = {}
    order: List[str] = []
    for raw in paths:
        path = str(raw or "").strip()
        if not path:
            continue
        key = _media_identity_key(path)
        if not key:
            key = path
        score = _path_preference_score(path)
        previous = preferred.get(key)
        if previous is None:
            preferred[key] = path
            order.append(key)
            continue
        if score > _path_preference_score(previous):
            preferred[key] = path
    return [preferred[key] for key in order]


def classify_import_paths(paths: Iterable[str]) -> Dict[str, int]:
    counts = {"photo": 0, "audio": 0, "video": 0, "gallery": 0, "total": 0}
    seen: set = set()
    for raw in dedupe_import_paths(paths):
        media_type = _infer_media_type_from_path(str(raw))
        if not media_type:
            continue
        identity = _media_identity_key(str(raw))
        if not identity:
            continue
        key = (media_type, identity)
        if key in seen:
            continue
        seen.add(key)
        counts[media_type] += 1
    counts["gallery"] = counts["photo"] + counts["video"]
    counts["total"] = counts["gallery"] + counts["audio"]
    return counts


def _parse_clock_text(value: Any) -> Optional[float]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return time.mktime(parsed.timetuple())


def _format_capture_text(timestamp: Optional[float], has_fraction: bool = False) -> str:
    if timestamp is None:
        return ""
    try:
        dt_value = datetime.fromtimestamp(timestamp)
        if has_fraction:
            return dt_value.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        return dt_value.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


def _infer_capture_timestamp_from_name(path_value: str) -> Tuple[Optional[float], str, str]:
    normalized = str(path_value or "").strip().replace("\\", "/").rstrip("/")
    if not normalized:
        return None, "", ""

    name = os.path.basename(normalized)
    stem = os.path.splitext(name)[0]
    candidates = [name, stem]

    for candidate in candidates:
        dashed = re.search(
            r"(20\d{2})[-_](\d{2})[-_](\d{2})[ T_](\d{2})[-_:](\d{2})[-_:](\d{2})",
            candidate,
        )
        if dashed:
            try:
                parsed = datetime(
                    int(dashed.group(1)),
                    int(dashed.group(2)),
                    int(dashed.group(3)),
                    int(dashed.group(4)),
                    int(dashed.group(5)),
                    int(dashed.group(6)),
                )
                timestamp = time.mktime(parsed.timetuple())
                return timestamp, _format_capture_text(timestamp), "name_datetime"
            except Exception:
                pass
        match = re.search(r"(20\d{12,17})", candidate)
        if not match:
            continue
        digits = match.group(1)
        try:
            main_part = digits[:14]
            fractional = digits[14:]
            parsed = datetime.strptime(main_part, "%Y%m%d%H%M%S")
            fractional_seconds = 0.0
            has_fraction = False
            if fractional:
                has_fraction = True
                micro_text = (fractional + "000000")[:6]
                fractional_seconds = int(micro_text) / 1_000_000.0
            timestamp = time.mktime(parsed.timetuple()) + fractional_seconds
            return timestamp, _format_capture_text(timestamp, has_fraction=has_fraction), "name_datetime"
        except Exception:
            continue

    for candidate in candidates:
        match = re.search(r"(?<!\d)(1\d{9}|1\d{12})(?!\d)", candidate)
        if not match:
            continue
        digits = match.group(1)
        try:
            raw_value = int(digits)
        except Exception:
            continue
        has_fraction = len(digits) == 13
        timestamp = raw_value / 1000.0 if has_fraction else float(raw_value)
        if timestamp <= 0:
            continue
        year = datetime.fromtimestamp(timestamp).year
        if year < 2020 or year > 2038:
            continue
        return timestamp, _format_capture_text(timestamp, has_fraction=has_fraction), "name_epoch"

    return None, "", ""


def summarize_import_paths(paths: Iterable[str]) -> Dict[str, Any]:
    records: List[Dict[str, Any]] = []
    seen: set = set()
    names_by_type: Dict[str, List[str]] = {"photo": [], "audio": [], "video": [], "unknown": []}

    for raw in paths:
        path_text = str(raw or "").strip().replace("\\", "/").rstrip("/")
        if not path_text:
            continue
        name = os.path.basename(path_text)
        normalized_name = name.lower()
        if not normalized_name or normalized_name in seen:
            continue
        seen.add(normalized_name)

        media_type = _infer_media_type_from_path(path_text) or "unknown"
        captured_ts, captured_at, timestamp_source = _infer_capture_timestamp_from_name(path_text)
        record = {
            "path": path_text,
            "name": name,
            "media_type": media_type,
            "captured_ts": captured_ts,
            "captured_at": captured_at,
            "timestamp_source": timestamp_source,
        }
        records.append(record)
        names_by_type.setdefault(media_type, []).append(name)

    counts = classify_import_paths(record["path"] for record in records)
    return {
        "records": records,
        "counts": counts,
        "names_by_type": names_by_type,
    }


def filter_import_paths_by_capture_window(
    paths: Iterable[str],
    started_at: Any,
    finished_at: Any,
    # Glasses clock is often 1~3 minutes behind the PC; keep a wide before-slack.
    slack_before_seconds: float = 180.0,
    slack_after_seconds: float = 120.0,
) -> Dict[str, Any]:
    summary = summarize_import_paths(paths)
    start_ts = _parse_clock_text(started_at)
    end_ts = _parse_clock_text(finished_at)
    if start_ts is not None and end_ts is not None and end_ts < start_ts:
        start_ts, end_ts = end_ts, start_ts

    matched_records: List[Dict[str, Any]] = []
    unknown_records: List[Dict[str, Any]] = []
    outside_records: List[Dict[str, Any]] = []

    if start_ts is None or end_ts is None:
        matched_records = list(summary["records"])
        filter_mode = "no_window"
        window_start = None
        window_end = None
    else:
        filter_mode = "capture_time_window"
        window_start = start_ts - max(0.0, float(slack_before_seconds))
        window_end = end_ts + max(0.0, float(slack_after_seconds))
        for record in summary["records"]:
            captured_ts = record.get("captured_ts")
            if captured_ts is None:
                unknown_records.append(record)
                continue
            if window_start <= captured_ts <= window_end:
                matched_records.append(record)
            else:
                outside_records.append(record)

    ignored_records = list(outside_records) + list(unknown_records)
    matched_paths = dedupe_import_paths(
        [str(record.get("path", "")) for record in matched_records if record.get("path")]
    )
    ignored_paths = dedupe_import_paths(
        [str(record.get("path", "")) for record in ignored_records if record.get("path")]
    )
    unknown_paths = dedupe_import_paths(
        [str(record.get("path", "")) for record in unknown_records if record.get("path")]
    )

    return {
        "mode": filter_mode,
        "started_at": str(started_at or ""),
        "finished_at": str(finished_at or ""),
        "window_start": _format_capture_text(window_start),
        "window_end": _format_capture_text(window_end),
        "matched_paths": matched_paths,
        "ignored_paths": ignored_paths,
        "unknown_time_paths": unknown_paths,
        "matched_records": matched_records,
        "ignored_records": ignored_records,
        "unknown_time_records": unknown_records,
        "matched_counts": classify_import_paths(matched_paths),
        "ignored_counts": classify_import_paths(ignored_paths),
        "matched_names_by_type": summarize_import_paths(matched_paths)["names_by_type"],
        "ignored_names_by_type": summarize_import_paths(ignored_paths)["names_by_type"],
    }


def _infer_failure_reason_from_signals(signals: Iterable[str]) -> str:
    normalized = " ".join(_normalize(signal) for signal in signals)
    if any(keyword in normalized for keyword in STORAGE_FULL_KEYWORDS):
        return "storage_full"
    if "wifi_connect_fail" in normalized or "wifi_enable_fail" in normalized:
        return "wifi_channel_closed"
    if "download_failed" in normalized or "download failed" in normalized:
        return "download_failed"
    if "updatedownloadstate :failed" in normalized:
        return "import_state_failed"
    return ""


class PhoneOperations:
    def __init__(self, device: Optional["u2.Device"] = None):
        self.device = device

    def connect(self, device_serial: Optional[str] = None) -> "u2.Device":
        _ensure_u2()
        serial = _require_device_serial(device_serial)
        logger.debug("Connect device: %s", serial)
        self.device = u2.connect(serial)
        return self.device

    def ensure_device(self) -> "u2.Device":
        if self.device is not None:
            return self.device
        return self.connect()

    def launch_app(self, package_name: Optional[str] = None) -> None:
        # Keep device session warm for subsequent UI operations.
        self.ensure_device()
        pkg = (package_name or app_config.PHONE_APP_PACKAGE or "").strip()
        if not pkg:
            raise ValueError("PHONE_APP_PACKAGE is not configured")
        serial = _require_device_serial(None)
        logger.info("Launch app: %s", pkg)

        activity = (LAUNCH_ACTIVITY or "").strip()
        launch_cmds: List[List[str]] = []
        if activity:
            launch_cmds.append(
                ["adb", "-s", serial, "shell", "am", "start", "-n", activity]
            )
        launch_cmds.append(
            [
                "adb",
                "-s",
                serial,
                "shell",
                "monkey",
                "-p",
                pkg,
                "-c",
                "android.intent.category.LAUNCHER",
                "1",
            ]
        )

        last_error: Optional[str] = None
        for attempt in range(1, 4):
            for launch_cmd in launch_cmds:
                try:
                    result = self.adb_run(launch_cmd, check=False, timeout=12.0)
                    output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip().lower()
                    if "no activities found to run" in output or "error:" in output and "does not exist" in output:
                        last_error = output or "launch activity missing"
                        continue

                    time.sleep(2.0)
                    if self.is_app_running(pkg):
                        # Give splash/home a moment to settle before callers dump UI.
                        time.sleep(1.5)
                        return
                    last_error = "app process not detected after launch command"
                    logger.warning("Launch app attempt %s/3 not confirmed: %s", attempt, last_error)
                except subprocess.TimeoutExpired:
                    last_error = "adb launch command timeout"
                    logger.warning("Launch app attempt %s/3 timeout", attempt)
                except Exception as exc:
                    last_error = str(exc)
                    logger.warning("Launch app attempt %s/3 failed: %s", attempt, exc)

            # Reconnect the uiautomator2 session before retrying.
            try:
                self.connect(serial)
            except Exception:
                pass
            time.sleep(0.5)

        raise RuntimeError(f"Launch app failed after retries: {last_error or 'unknown error'}")

    def stop_app(self, package_name: Optional[str] = None) -> None:
        device = self.ensure_device()
        pkg = (package_name or app_config.PHONE_APP_PACKAGE or "").strip()
        if not pkg:
            raise ValueError("PHONE_APP_PACKAGE is not configured")
        logger.info("Stop app: %s", pkg)
        device.app_stop(pkg)

    def click(self, x: int, y: int) -> None:
        device = self.ensure_device()
        logger.debug("Click point: (%s, %s)", x, y)
        device.click(x, y)

    def tap_text(self, text: str, timeout: float = 3.0) -> bool:
        device = self.ensure_device()
        obj = device(text=text)
        if obj.wait(timeout=timeout):
            obj.click()
            logger.debug("Tap text: %s", text)
            return True
        return False

    def tap_resource_id(self, resource_id: str, timeout: float = 3.0) -> bool:
        rid = (resource_id or "").strip()
        if not rid:
            return False
        device = self.ensure_device()
        obj = device(resourceId=rid)
        if obj.wait(timeout=timeout):
            obj.click()
            logger.debug("Tap resource-id: %s", rid)
            return True
        return False

    def tap_first_text(self, candidates: Iterable[str], timeout: float = 0.8) -> bool:
        device = self.ensure_device()
        for text in candidates:
            value = str(text).strip()
            if not value:
                continue
            obj = device(text=value)
            if obj.wait(timeout=timeout):
                obj.click()
                logger.debug("Tap first matching text: %s", value)
                return True
        return False

    def text_exists(self, text: str, timeout: float = 1.0) -> bool:
        device = self.ensure_device()
        obj = device(text=text)
        return obj.wait(timeout=timeout)

    def any_text_exists(self, candidates: Iterable[str], timeout: float = 1.0) -> bool:
        device = self.ensure_device()
        for text in candidates:
            value = str(text).strip()
            if not value:
                continue
            obj = device(text=value)
            if obj.wait(timeout=timeout):
                logger.debug("Detected text: %s", value)
                return True
        return False

    def swipe(self, direction: str = "up", scale: float = 0.7) -> None:
        device = self.ensure_device()
        logger.debug("Swipe: direction=%s scale=%s", direction, scale)
        device.swipe_ext(direction, scale)

    def screenshot(self, save_path: str) -> str:
        device = self.ensure_device()
        os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
        device.screenshot(save_path)
        logger.info("Saved screenshot: %s", save_path)
        return save_path

    def dump_hierarchy(self) -> str:
        device = self.ensure_device()
        return device.dump_hierarchy()

    def build_control_tree(self) -> Dict[str, Any]:
        xml_text = self.dump_hierarchy()
        root = ET.fromstring(xml_text)
        return _parse_node(root).to_dict()

    def adb_run(
        self,
        args: Sequence[str],
        check: bool = False,
        timeout: Optional[float] = None,
    ) -> subprocess.CompletedProcess:
        result = subprocess.run(
            list(args),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout,
            **_subprocess_hidden_kwargs(),
        )
        if check and result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "adb failed")
        return result

    def adb_shell(self, command: str, device_serial: Optional[str] = None) -> str:
        serial = _require_device_serial(device_serial)
        full_cmd = ["adb", "-s", serial, "shell"] + command.split()
        logger.debug("Run adb shell: %s", command)
        result = self.adb_run(full_cmd, check=True)
        return result.stdout

    def list_connected_devices(self) -> List[str]:
        result = self.adb_run(["adb", "devices"], check=False)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip() or "adb devices failed"
            raise RuntimeError(detail)
        devices: List[str] = []
        for raw in (result.stdout or "").splitlines():
            line = raw.strip()
            if not line or line.startswith("List of devices"):
                continue
            if "\tdevice" in line:
                devices.append(line.split("\t", 1)[0].strip())
        return devices

    def list_adb_device_states(self) -> List[Dict[str, str]]:
        """Return all adb rows including unauthorized / offline."""
        result = self.adb_run(["adb", "devices"], check=False)
        rows: List[Dict[str, str]] = []
        for raw in (result.stdout or "").splitlines():
            line = raw.strip()
            if not line or line.startswith("List of devices"):
                continue
            parts = line.split()
            if len(parts) >= 2:
                rows.append({"serial": parts[0], "state": parts[1]})
        return rows

    def restart_adb_server(self) -> Dict[str, Any]:
        """Restart adb server; often needed after unplug/replug."""
        detail: Dict[str, Any] = {"killed": False, "started": False, "errors": []}
        try:
            self.adb_run(["adb", "kill-server"], check=False)
            detail["killed"] = True
        except Exception as exc:
            detail["errors"].append(f"kill-server: {exc}")
        time.sleep(0.8)
        # Stale %TEMP%\adb lock file/dir often blocks daemon restart on Windows.
        for env_key in ("TEMP", "TMP"):
            base = (os.environ.get(env_key) or "").strip()
            if not base:
                continue
            stale = Path(base) / "adb"
            try:
                if stale.is_file():
                    stale.unlink()
                elif stale.is_dir():
                    shutil.rmtree(stale, ignore_errors=True)
            except Exception as exc:
                detail["errors"].append(f"cleanup {stale}: {exc}")
        start = self.adb_run(["adb", "start-server"], check=False)
        if start.returncode == 0:
            detail["started"] = True
        else:
            detail["errors"].append(
                (start.stderr or start.stdout or "adb start-server failed").strip()
            )
        time.sleep(0.8)
        # Nudge device discovery.
        probe = self.adb_run(["adb", "devices"], check=False)
        if probe.returncode != 0:
            detail["errors"].append((probe.stderr or probe.stdout or "adb devices failed").strip())
        return detail

    def is_device_connected(self, device_serial: Optional[str] = None) -> bool:
        serial = _require_device_serial(device_serial)
        return serial in self.list_connected_devices()

    def ensure_device_connected(self, device_serial: Optional[str] = None) -> str:
        serial = (device_serial or app_config.PHONE_DEVICE_SERIAL or "").strip()
        online = self.list_connected_devices()
        if serial:
            if serial not in online:
                raise RuntimeError(f"ADB device is not online: {serial}")
            return serial
        if len(online) == 1:
            serial = online[0]
            app_config.apply_runtime_overrides(device_serial=serial)
            return serial
        if not online:
            raise RuntimeError("没有在线的 ADB 设备")
        raise RuntimeError("多台 ADB 设备在线，请指定 --device-serial")

    def is_screen_on(self, device_serial: Optional[str] = None) -> bool:
        output = self.adb_shell("dumpsys power", device_serial=device_serial).lower()
        return (
            "display power: state=on" in output
            or "mholdingdisplaysuspenserblocker=true" in output
            or "mwakefulness=awake" in output
        )

    def wake_screen(self, device_serial: Optional[str] = None) -> None:
        if self.is_screen_on(device_serial=device_serial):
            return
        serial = _require_device_serial(device_serial)
        logger.debug("Wake screen: %s", serial)
        self.adb_shell("input keyevent 26", device_serial=serial)
        time.sleep(0.5)
        self.adb_shell("input keyevent 82", device_serial=serial)
        time.sleep(0.4)

    def is_app_running(self, package_name: Optional[str] = None) -> bool:
        pkg = (package_name or app_config.PHONE_APP_PACKAGE or "").strip()
        if not pkg:
            raise ValueError("PHONE_APP_PACKAGE is not configured")
        try:
            output = self.adb_shell(f"pidof {pkg}").strip()
            if output:
                return True
        except RuntimeError:
            pass
        try:
            output = self.adb_shell("ps -A").strip().lower()
            return pkg.lower() in output
        except RuntimeError:
            return False

    def ensure_app_running(self, package_name: Optional[str] = None) -> None:
        pkg = (package_name or app_config.PHONE_APP_PACKAGE or "").strip()
        if self.is_app_running(pkg):
            return
        logger.info("App not running, launching: %s", pkg)
        self.launch_app(pkg)

    def _send_device_control_command(
        self,
        command: str,
        duration_seconds: Optional[int] = None,
        require_dispatched: bool = True,
    ) -> str:
        self.ensure_device_connected()
        serial = _require_device_serial(None)
        package = (app_config.PHONE_APP_PACKAGE or "").strip() or "com.moyoung.glasses"
        action = DEVICE_CONTROL_ACTION or app_config.DEVICE_CONTROL_ACTION
        token = DEVICE_CONTROL_TOKEN or app_config.DEVICE_CONTROL_TOKEN
        full_cmd = [
            "adb", "-s", serial, "shell", "am", "broadcast",
            "-a", action, "-p", package,
            "--es", "token", token, "--es", "command", command,
        ]
        if duration_seconds is not None:
            full_cmd.extend(["--ei", "duration", str(int(duration_seconds))])
        logger.info("Send device command: command=%s duration=%s", command, duration_seconds)
        result = self.adb_run(full_cmd, check=False)
        output = (result.stdout or "").strip()
        error_output = (result.stderr or "").strip()
        merged = "\n".join([part for part in (output, error_output) if part]).strip()
        normalized = _normalize(merged)
        if result.returncode != 0:
            raise RuntimeError(merged or "broadcast failed")
        for keyword in BROADCAST_FATAL_KEYWORDS:
            if keyword in normalized:
                raise RuntimeError(merged or f"broadcast failed: {keyword}")
        if require_dispatched and DISPATCH_SUCCESS_KEYWORD not in normalized:
            raise RuntimeError(merged or "broadcast missing dispatched")
        if error_output:
            logger.warning("Broadcast stderr: %s", error_output)
        if output:
            logger.debug("Broadcast output: %s", output)
        return merged

    def take_device_photo(self) -> str:
        return self._send_device_control_command(DEVICE_CONTROL_CMD_TAKE_PHOTO, require_dispatched=True)

    def start_device_audio_recording(self, duration_seconds: int = 0) -> str:
        if duration_seconds < 0:
            raise ValueError("duration_seconds must be >= 0")
        return self._send_device_control_command(
            DEVICE_CONTROL_CMD_START_AUDIO,
            duration_seconds=duration_seconds,
            require_dispatched=True,
        )

    def stop_device_audio_recording(self) -> str:
        return self._send_device_control_command(DEVICE_CONTROL_CMD_STOP_AUDIO, require_dispatched=True)

    def start_device_video_recording(self, duration_seconds: int = 0) -> str:
        if duration_seconds < 0:
            raise ValueError("duration_seconds must be >= 0")
        return self._send_device_control_command(
            DEVICE_CONTROL_CMD_START_VIDEO,
            duration_seconds=duration_seconds,
            require_dispatched=True,
        )

    def stop_device_video_recording(self) -> str:
        return self._send_device_control_command(DEVICE_CONTROL_CMD_STOP_VIDEO, require_dispatched=True)

    def ensure_capture_stopped(
        self,
        *,
        kinds: Tuple[str, ...] = ("audio", "video"),
        retries: int = 3,
        settle_seconds: float = 2.0,
    ) -> Dict[str, Any]:
        """Best-effort stop for glasses capture.

        App may return "dispatched" while the glasses keep recording. Repeat stop
        commands and wait so media files can finalize before import.
        """
        results: Dict[str, Any] = {"audio": [], "video": [], "errors": []}
        targets = {str(item or "").strip().lower() for item in kinds}
        for attempt in range(1, max(1, int(retries)) + 1):
            if "audio" in targets:
                try:
                    output = self.stop_device_audio_recording()
                    results["audio"].append({"attempt": attempt, "output": output})
                    logger.info("Force stop audio attempt %s ok", attempt)
                except Exception as exc:
                    results["errors"].append(f"audio#{attempt}: {exc}")
                    logger.warning("Force stop audio attempt %s failed: %s", attempt, exc)
            if "video" in targets:
                try:
                    output = self.stop_device_video_recording()
                    results["video"].append({"attempt": attempt, "output": output})
                    logger.info("Force stop video attempt %s ok", attempt)
                except Exception as exc:
                    results["errors"].append(f"video#{attempt}: {exc}")
                    logger.warning("Force stop video attempt %s failed: %s", attempt, exc)
            if attempt < max(1, int(retries)):
                time.sleep(max(0.3, float(settle_seconds)))
        if settle_seconds > 0:
            time.sleep(float(settle_seconds))
        return results

    def _inspect_import_ui(self, xml_text: str) -> Dict[str, Any]:
        state: Dict[str, Any] = {
            "has_import_card": False,
            "has_import_button": False,
            "import_button_bounds": "",
            "media_count": None,
            "progress_percent": None,
            "progress_done": None,
            "progress_total": None,
            "waiting_wifi": False,
            "importing_hint": False,
            "success_hint": False,
            "failed_hint": False,
            "texts": [],
            "battery_texts": [],
            "battery_level": None,
            "battery_charging": None,
            "battery_low_hint": False,
            "storage_full_hint": False,
            "storage_hint_texts": [],
            "failed_texts": [],
        }
        try:
            root = ET.fromstring(xml_text)
        except Exception:
            return state

        def _extract_battery_level(raw_text: str) -> Optional[int]:
            if not raw_text:
                return None
            percent_match = BATTERY_PERCENT_PATTERN.search(raw_text)
            if percent_match:
                value = _safe_int(percent_match.group(1))
                if value is not None and 0 <= value <= 100:
                    return value
            zh_percent_match = BATTERY_PERCENT_ZH_PATTERN.search(raw_text)
            if zh_percent_match:
                value = _safe_int(zh_percent_match.group(1))
                if value is not None and 0 <= value <= 100:
                    return value
            return None

        def _update_battery_state(raw_text: str, from_battery_node: bool) -> None:
            text_value = str(raw_text or "").strip()
            if not text_value:
                return
            lowered = text_value.lower()
            if from_battery_node:
                state["battery_texts"].append(text_value)

            if any(keyword in lowered for keyword in BATTERY_CHARGING_HINTS):
                state["battery_charging"] = True
            elif any(keyword in lowered for keyword in BATTERY_NOT_CHARGING_HINTS):
                state["battery_charging"] = False

            if any(keyword in lowered for keyword in BATTERY_LOW_KEYWORDS):
                state["battery_low_hint"] = True
                if text_value not in state["battery_texts"]:
                    state["battery_texts"].append(text_value)
            if any(keyword in text_value for keyword in UI_BATTERY_LOW_HINTS):
                state["battery_low_hint"] = True
                if text_value not in state["battery_texts"]:
                    state["battery_texts"].append(text_value)

            if from_battery_node:
                level = _extract_battery_level(text_value)
                if level is not None:
                    state["battery_level"] = level

        def _update_storage_state(raw_text: str) -> None:
            text_value = str(raw_text or "").strip()
            if not text_value:
                return
            lowered = text_value.lower()
            if any(keyword in lowered for keyword in STORAGE_FULL_KEYWORDS):
                state["storage_full_hint"] = True
                if text_value not in state["storage_hint_texts"]:
                    state["storage_hint_texts"].append(text_value)

        def walk(elem: ET.Element, in_import_card: bool = False) -> None:
            resource_id = str(elem.attrib.get("resource-id", "") or "")
            text = str(elem.attrib.get("text", "") or "").strip()
            content_desc = str(elem.attrib.get("content-desc", "") or "").strip()
            clickable = elem.attrib.get("clickable", "false") == "true"
            bounds = str(elem.attrib.get("bounds", "") or "")
            resource_lower = resource_id.lower()

            is_import_card = resource_id == IMPORT_CARD_RESOURCE_ID
            if is_import_card:
                state["has_import_card"] = True
            current_in_import_card = in_import_card or is_import_card
            is_battery_node = any(keyword in resource_lower for keyword in BATTERY_RESOURCE_KEYWORDS)
            if not is_battery_node and content_desc:
                lowered_desc = content_desc.lower()
                is_battery_node = any(keyword in lowered_desc for keyword in BATTERY_RESOURCE_KEYWORDS)

            if text:
                state["texts"].append(text)
                if current_in_import_card:
                    media_match = IMPORT_MEDIA_COUNT_PATTERN.search(text)
                    if media_match:
                        state["media_count"] = _safe_int(media_match.group(1))

                    progress_match = IMPORT_PROGRESS_PATTERN.search(text)
                    if progress_match:
                        progress_value = _safe_int(progress_match.group(1))
                        if progress_value is not None and 0 <= progress_value <= 100:
                            state["progress_percent"] = progress_value

                    fraction_match = IMPORT_FRACTION_PATTERN.search(text)
                    if fraction_match:
                        done = _safe_int(fraction_match.group(1))
                        total = _safe_int(fraction_match.group(2))
                        if (
                            done is not None
                            and total is not None
                            and total > 0
                            and 0 <= done <= total
                            and total <= 500
                        ):
                            state["progress_done"] = done
                            state["progress_total"] = total
                            state["media_count"] = total
                            # App 没画 % 时，用完成数自己算。
                            if state["progress_percent"] is None:
                                state["progress_percent"] = int(round(100.0 * done / total))

                    lowered = text.lower()
                    if any(keyword in lowered for keyword in UI_WAITING_HINTS):
                        state["waiting_wifi"] = True
                    if any(keyword in text for keyword in UI_IMPORTING_HINTS):
                        state["importing_hint"] = True
                    if any(keyword in text for keyword in UI_SUCCESS_HINTS):
                        state["success_hint"] = True
                    if any(keyword in lowered for keyword in UI_IMPORT_FAILED_HINTS):
                        state["failed_hint"] = True
                        if text not in state["failed_texts"]:
                            state["failed_texts"].append(text)
                _update_battery_state(text, is_battery_node)
                _update_storage_state(text)

            if content_desc:
                lowered_desc = content_desc.lower()
                if current_in_import_card and any(keyword in lowered_desc for keyword in UI_IMPORT_FAILED_HINTS):
                    state["failed_hint"] = True
                    if content_desc not in state["failed_texts"]:
                        state["failed_texts"].append(content_desc)
                _update_battery_state(content_desc, is_battery_node)
                _update_storage_state(content_desc)

            if text == IMPORT_BUTTON_TEXT and clickable:
                state["has_import_button"] = True
                if current_in_import_card and not state["import_button_bounds"]:
                    state["import_button_bounds"] = bounds

            if current_in_import_card and text == IMPORT_BUTTON_TEXT and clickable and not state["import_button_bounds"]:
                state["import_button_bounds"] = bounds

            for child in list(elem):
                walk(child, current_in_import_card)

        walk(root)
        if not state["has_import_button"]:
            state["has_import_button"] = any(text == IMPORT_BUTTON_TEXT for text in state["texts"])
        return state

    def _inspect_navigation_ui(self, xml_text: str) -> Dict[str, Any]:
        state: Dict[str, Any] = {
            "has_import_card": False,
            "has_home_content": False,
            "has_back_button": False,
            "has_home_tab": False,
            "home_tab_selected": False,
            "has_gallery_entry": False,
            "has_feature_grid": False,
        }
        try:
            root = ET.fromstring(xml_text)
        except Exception:
            return state

        home_tab_text = (HOME_TAB_TEXT or "").strip()
        for elem in root.iter("node"):
            resource_id = str(elem.attrib.get("resource-id", "") or "").strip()
            text = str(elem.attrib.get("text", "") or "").strip()
            selected = str(elem.attrib.get("selected", "false") or "").strip().lower() == "true"
            checked = str(elem.attrib.get("checked", "false") or "").strip().lower() == "true"
            if resource_id == IMPORT_CARD_RESOURCE_ID:
                state["has_import_card"] = True
            elif resource_id == HOME_CONTENT_RESOURCE_ID:
                state["has_home_content"] = True
            elif BACK_BUTTON_RESOURCE_ID and resource_id == BACK_BUTTON_RESOURCE_ID:
                state["has_back_button"] = True
            elif HOME_TAB_BUTTON_RESOURCE_ID and resource_id == HOME_TAB_BUTTON_RESOURCE_ID:
                state["has_home_tab"] = True
                if selected or checked:
                    state["home_tab_selected"] = True
            elif home_tab_text and text == home_tab_text:
                state["has_home_tab"] = True
                if selected or checked:
                    state["home_tab_selected"] = True
            elif resource_id and resource_id in {
                GALLERY_SEE_ALL_RESOURCE_ID,
                GALLERY_TITLE_RESOURCE_ID,
                GALLERY_SECTION_RESOURCE_ID,
            }:
                state["has_gallery_entry"] = True
            elif text and text == GALLERY_FALLBACK_TEXT:
                state["has_gallery_entry"] = True
            elif resource_id == FEATURE_GRID_RESOURCE_ID:
                state["has_feature_grid"] = True

        return state

    def _is_home_screen(self, navigation_state: Dict[str, Any]) -> bool:
        if navigation_state.get("has_back_button"):
            return False
        if navigation_state.get("has_home_content") and (
            navigation_state.get("has_import_card")
            or navigation_state.get("has_gallery_entry")
            or navigation_state.get("home_tab_selected")
        ):
            return True
        if navigation_state.get("has_home_content"):
            return True
        if navigation_state.get("home_tab_selected") and (
            navigation_state.get("has_import_card")
            or navigation_state.get("has_gallery_entry")
            or navigation_state.get("has_feature_grid")
        ):
            return True
        return False

    def ensure_home_screen(self, max_steps: int = 3) -> bool:
        for _ in range(max(1, max_steps) + 1):
            self.dismiss_blocking_dialogs(rounds=1)
            try:
                xml_text = self.dump_hierarchy()
                navigation_state = self._inspect_navigation_ui(xml_text)
                if self._is_home_screen(navigation_state):
                    return True
            except Exception:
                navigation_state = {}

            if navigation_state.get("has_back_button") and BACK_BUTTON_RESOURCE_ID and self.tap_resource_id(
                BACK_BUTTON_RESOURCE_ID, timeout=0.8
            ):
                time.sleep(0.5)
                continue
            if HOME_TAB_BUTTON_RESOURCE_ID and self.tap_resource_id(HOME_TAB_BUTTON_RESOURCE_ID, timeout=0.8):
                time.sleep(0.6)
                continue
            if HOME_TAB_TEXT and self.tap_text(HOME_TAB_TEXT, timeout=0.8):
                time.sleep(0.6)
                continue
            self._press_back_once()

        try:
            xml_text = self.dump_hierarchy()
            return self._is_home_screen(self._inspect_navigation_ui(xml_text))
        except Exception:
            return False

    def _click_import_by_hierarchy(self) -> bool:
        try:
            xml_text = self.dump_hierarchy()
            state = self._inspect_import_ui(xml_text)
            bounds = state.get("import_button_bounds") or ""
            center = _bounds_center(bounds)
            if center is None:
                return False
            x, y = center
            self.click(x, y)
            logger.debug("Clicked import button by UI hierarchy: (%s, %s)", x, y)
            return True
        except Exception as exc:
            logger.warning("Hierarchy import click failed: %s", exc)
            return False

    def _dump_hierarchy_timed(self, timeout: float = 8.0) -> str:
        box: Dict[str, Any] = {}

        def _run() -> None:
            try:
                box["xml"] = self.dump_hierarchy()
            except Exception as exc:
                box["error"] = exc

        worker = Thread(target=_run, daemon=True)
        worker.start()
        worker.join(timeout)
        if worker.is_alive():
            raise TimeoutError(f"dump_hierarchy 超过 {timeout:.0f}s")
        if box.get("error") is not None:
            raise box["error"]
        return str(box.get("xml") or "")

    def inspect_and_dismiss_dialogs(self) -> Dict[str, Any]:
        """Dump the current screen once, log nothing itself, click a known dialog button if present."""
        info: Dict[str, Any] = {"texts": [], "dismissed": ""}
        try:
            xml = self._dump_hierarchy_timed(8.0)
            root = ET.fromstring(xml)
        except Exception as exc:
            info["error"] = str(exc)
            logger.warning("检查界面失败: %s", exc)
            return info
        texts: List[str] = []
        seen = set()
        nodes: List[Tuple[str, Optional[Tuple[int, int]]]] = []
        for elem in root.iter("node"):
            label = (elem.attrib.get("text") or elem.attrib.get("content-desc") or "").strip()
            if label and label not in seen:
                seen.add(label)
                texts.append(label)
            if label:
                nodes.append((label, _bounds_center(elem.attrib.get("bounds") or "")))
        info["texts"] = texts[:40]
        # 「不再提示」通常是勾选，点了弹窗还在；必须再点关闭按钮。
        checkbox_labels = {"不再提示", "不再提醒"}
        close_labels = [item for item in DIALOG_DISMISS_TEXTS if item not in checkbox_labels]
        clicked_checkbox = False
        for label, center in nodes:
            if label in checkbox_labels and center is not None and not clicked_checkbox:
                self.click(center[0], center[1])
                clicked_checkbox = True
                time.sleep(0.2)
        for prefer in close_labels:
            for label, center in nodes:
                if label != prefer or center is None:
                    continue
                self.click(center[0], center[1])
                info["dismissed"] = label
                time.sleep(0.4)
                return info
        return info

    def dismiss_blocking_dialogs(self, rounds: int = 2) -> bool:
        """Dismiss common modal tips/permission dialogs that block home actions."""
        dismissed_any = False
        for _ in range(max(1, rounds)):
            info = self.inspect_and_dismiss_dialogs()
            label = str(info.get("dismissed") or "")
            if not label:
                break
            dismissed_any = True
            logger.info("Dismissed blocking dialog: %s", label)
        return dismissed_any

    def click_import_button(self) -> bool:
        try:
            device = self.ensure_device()
            logger.info("Searching and clicking import button")
            self.dismiss_blocking_dialogs(rounds=3)
            if not self.ensure_home_screen(max_steps=3):
                logger.warning("Home screen not confirmed before import click")
            self.dismiss_blocking_dialogs(rounds=2)

            # Priority: config.py / env overrides → selectors.json → hierarchy / text fallback.
            xpath = (HOME_IMPORT_XPATH or ui_selectors.import_xpath() or "").strip()
            resource_id = (HOME_IMPORT_RESOURCE_ID or ui_selectors.import_resource_id() or "").strip()
            text = (HOME_IMPORT_TEXT or ui_selectors.import_text() or "").strip()
            desc = (HOME_IMPORT_DESC or ui_selectors.import_desc() or "").strip()

            if xpath:
                try:
                    xpath_obj = device.xpath(xpath)
                    if xpath_obj.wait(timeout=3.0):
                        xpath_obj.click()
                        logger.info("Import clicked by xpath")
                        return True
                except Exception as exc:
                    logger.warning("Import xpath click failed: %s", exc)

            if resource_id and self.tap_resource_id(resource_id, timeout=3.0):
                logger.info("Import clicked by resource_id")
                return True

            if text and self.tap_text(text, timeout=3.0):
                logger.info("Import clicked by text")
                return True

            if desc:
                try:
                    obj = device(description=desc)
                    if obj.wait(timeout=3.0):
                        obj.click()
                        logger.info("Import clicked by content-desc")
                        return True
                except Exception as exc:
                    logger.warning("Import content-desc click failed: %s", exc)

            if self._click_import_by_hierarchy():
                return True

            if self.tap_text(IMPORT_BUTTON_TEXT, timeout=4.0):
                logger.info("Import clicked by fallback text")
                return True

            logger.warning("Import button not found")
            return False
        except Exception as exc:
            logger.error("click_import_button failed: %s", exc)
            return False

    def _get_app_pid(self, package_name: str) -> Optional[int]:
        try:
            output = self.adb_shell(f"pidof {package_name}").strip()
            if not output:
                return None
            return _safe_int(output.split()[0])
        except Exception:
            return None

    def _start_logcat_monitor(self, package_name: str) -> Tuple[Optional[_LogcatMonitor], List[str]]:
        warnings: List[str] = []
        serial = _require_device_serial(None)
        pid = self._get_app_pid(package_name) or 0

        # 注意：裸数字 `-T 1` 在 Android 里是「最近 1 行」不是「1 秒前」。
        # 部分 OPPO 机上 `--pid` 会直接空输出。改为按时间起点 + 全文流，再在 reader 里过滤。
        time_spec = datetime.now().strftime("%m-%d %H:%M:%S.000")
        cmd = ["adb", "-s", serial, "logcat", "-v", "time", "-T", time_spec]
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="ignore",
                bufsize=1,
                **_subprocess_hidden_kwargs(),
            )
        except Exception as exc:
            warnings.append(f"logcat monitor start failed: {exc}")
            return None, warnings

        queue: "Queue[str]" = Queue()
        keep_re = IMPORT_LOG_KEEP_PATTERN
        pkg_token = (package_name or "").strip()

        def _reader() -> None:
            try:
                if process.stdout is None:
                    return
                for raw in process.stdout:
                    line = raw.rstrip("\r\n")
                    if not line:
                        continue
                    if keep_re.search(line) or (pkg_token and pkg_token in line):
                        queue.put(line)
            except Exception as exc:
                queue.put(f"__READER_ERROR__ {exc}")

        thread = Thread(target=_reader, daemon=True)
        thread.start()
        logger.info("Started import logcat monitor (time=%s pid_hint=%s, no --pid filter)", time_spec, pid or "-")
        return _LogcatMonitor(process, queue, thread, pid, package_name, time.time()), warnings

    def _drain_logcat_lines(self, monitor: Optional[_LogcatMonitor]) -> List[str]:
        if monitor is None:
            return []
        lines: List[str] = []
        while True:
            try:
                lines.append(monitor.queue.get_nowait())
            except Empty:
                break
        return lines

    def _stop_logcat_monitor(self, monitor: Optional[_LogcatMonitor]) -> List[str]:
        if monitor is None:
            return []
        process = monitor.process
        try:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=2.0)
        except Exception:
            try:
                if process.poll() is None:
                    process.kill()
            except Exception:
                pass
        try:
            monitor.reader_thread.join(timeout=1.0)
        except Exception:
            pass
        logger.debug("Stopped import logcat monitor")
        return self._drain_logcat_lines(monitor)

    def _dump_recent_logcat(self, limit: int = 4000) -> List[str]:
        """导入监控若空，补一次 dump，尽量找回路径/进度行。"""
        serial = _require_device_serial(None)
        count = max(200, int(limit))
        try:
            completed = subprocess.run(
                ["adb", "-s", serial, "logcat", "-d", "-v", "time", "-t", str(count)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=20,
                check=False,
                **_subprocess_hidden_kwargs(),
            )
        except Exception as exc:
            logger.warning("logcat dump failed: %s", exc)
            return []
        keep_re = IMPORT_LOG_KEEP_PATTERN
        pkg = (app_config.PHONE_APP_PACKAGE or "").strip()
        lines: List[str] = []
        raw_total = 0
        for raw in (completed.stdout or "").splitlines():
            line = raw.rstrip("\r\n")
            if not line:
                continue
            raw_total += 1
            if keep_re.search(line) or (pkg and pkg in line):
                lines.append(line)
        logger.info("logcat dump fallback: kept %s/%s lines", len(lines), raw_total or count)
        if raw_total == 0:
            err = (completed.stderr or "").strip()
            logger.warning("logcat dump 为空 rc=%s err=%s", completed.returncode, err[:200] if err else "-")
        return lines

    def _recover_import_metrics_from_logs(
        self,
        lines: Sequence[str],
        result: ImportWaitResult,
        *,
        log_total_count: Optional[int] = None,
        log_success_count: Optional[int] = None,
    ) -> Tuple[Optional[int], Optional[int], bool]:
        """监控空/漏采时，从 dump 日志回补路径、计数、进度。"""
        saw_terminal = False
        for line in lines:
            if not line or line.startswith("__READER_ERROR__"):
                continue
            event = self._parse_import_log_line(line)
            for path in event["success_paths"]:
                _append_unique(result.success_paths, path)
            if event["media_count"] is not None:
                result.media_count = event["media_count"]
            if event["progress_percent"] is not None:
                result.progress_percent = event["progress_percent"]
                if int(event["progress_percent"]) >= 100:
                    result.progress_reached_100 = True
            if event.get("total_count") is not None:
                value = _safe_int(event.get("total_count"))
                if value is not None:
                    log_total_count = value if log_total_count is None else max(log_total_count, value)
            if event.get("download_success_count") is not None:
                value = _safe_int(event.get("download_success_count"))
                if value is not None:
                    log_success_count = (
                        value if log_success_count is None else max(log_success_count, value)
                    )
            if event.get("success_marker"):
                saw_terminal = True
            if event["phase"] in (PHASE_SUCCESS, PHASE_IDLE_AFTER_SUCCESS, PHASE_DOWNLOAD, PHASE_CONNECT):
                # 仅在结果还停在 IDLE/FOUND 时用日志阶段回补，避免覆盖已有 SUCCESS。
                if result.phase in (PHASE_IDLE, PHASE_FOUND, "", None) or not result.phase:
                    result.phase = event["phase"]
        return log_total_count, log_success_count, saw_terminal

    def _map_download_state(self, state_name: str, current_state: str) -> str:
        state = state_name.upper()
        current = current_state.upper()
        if state == "FOUND":
            return PHASE_FOUND
        if state == "CONNECT":
            return PHASE_CONNECT
        if state == "DOWNLOAD":
            return PHASE_DOWNLOAD
        if state == "SUCCESS":
            return PHASE_SUCCESS
        if state == "IDLE" and current == "SUCCESS":
            return PHASE_IDLE_AFTER_SUCCESS
        if state == "FAILED":
            return PHASE_FAILED
        return state

    def _parse_import_log_line(self, line: str) -> Dict[str, Any]:
        event: Dict[str, Any] = {
            "phase": None,
            "progress_percent": None,
            "media_count": None,
            "download_success_count": None,
            "total_count": None,
            "success_paths": [],
            "warnings": [],
            "errors": [],
            "success_marker": False,
            "activity": False,
            "battery_level": None,
            "battery_low_level": None,
            "battery_power_off_level": None,
            "battery_charging": None,
            "battery_low_detected": False,
            "storage_full_detected": False,
            "failure_reason": "",
        }
        text = str(line or "").strip()
        if not text:
            return event
        lowered = text.lower()

        if text.startswith("__READER_ERROR__"):
            event["warnings"].append(text)
            return event

        for keyword in SOFT_WARNING_KEYWORDS:
            if keyword in lowered:
                event["warnings"].append(text)
                break

        for keyword in STORAGE_FULL_KEYWORDS:
            if keyword in lowered:
                event["storage_full_detected"] = True
                if not event["failure_reason"]:
                    event["failure_reason"] = "storage_full"
                event["errors"].append(f"storage_full: {text}")
                event["activity"] = True
                break

        for keyword in HARD_ERROR_KEYWORDS:
            if keyword in lowered:
                event["errors"].append(text)
                event["activity"] = True
                if not event["failure_reason"]:
                    if "wifi_connect_fail" in keyword or "wifi_enable_fail" in keyword:
                        event["failure_reason"] = "wifi_channel_closed"
                    elif "download_failed" in keyword or "download failed" in keyword:
                        event["failure_reason"] = "download_failed"
                    elif "updatedownloadstate :failed" in keyword:
                        event["failure_reason"] = "import_state_failed"

        charging_match = BATTERY_CHARGING_LOG_PATTERN.search(text) or BATTERY_CHARGING_EVENT_PATTERN.search(text)
        if charging_match:
            event["battery_charging"] = _safe_bool(charging_match.group(1))

        low_level_match = BATTERY_LOW_LEVEL_PATTERN.search(text)
        if low_level_match:
            value = _safe_int(low_level_match.group(1))
            if value is not None and 0 <= value <= 100:
                event["battery_low_level"] = value

        power_off_match = BATTERY_POWER_OFF_LEVEL_PATTERN.search(text)
        if power_off_match:
            value = _safe_int(power_off_match.group(1))
            if value is not None and 0 <= value <= 100:
                event["battery_power_off_level"] = value

        for pattern in BATTERY_LEVEL_LOG_PATTERNS:
            level_match = pattern.search(text)
            if not level_match:
                continue
            value = _safe_int(level_match.group(1))
            if value is not None and 0 <= value <= 100:
                event["battery_level"] = value
                break

        if any(keyword in lowered for keyword in BATTERY_LOW_KEYWORDS):
            event["battery_low_detected"] = True
            event["failure_reason"] = "battery_low"
            event["errors"].append(f"battery_low: {text}")

        state_match = IMPORT_DOWNLOAD_STATE_PATTERN.search(text)
        if state_match:
            mapped = self._map_download_state(state_match.group(1), state_match.group(2))
            event["phase"] = mapped
            event["activity"] = True
            if mapped in (PHASE_SUCCESS, PHASE_IDLE_AFTER_SUCCESS):
                event["success_marker"] = True

        media_event_match = IMPORT_MEDIA_EVENT_PATTERN.search(text) or IMPORT_NEW_MEDIA_TYPE_PATTERN.search(text)
        if media_event_match:
            event["activity"] = True
            media_event = media_event_match.group(1).upper()
            if media_event in ("WIFI_START_ENABLE", "WIFI_START_CONNECT", "WIFI_CONNECT_SUCCESS"):
                event["phase"] = PHASE_CONNECT
            elif media_event in ("DOWNLOAD_PROGRESS", "DOWNLOAD_START"):
                event["phase"] = PHASE_DOWNLOAD
            elif media_event == "DOWNLOAD_SUCCESS":
                event["phase"] = PHASE_SUCCESS
                event["success_marker"] = True
            elif media_event == "DOWNLOAD_FILE":
                # DOWNLOAD_FILE often appears as a snapshot/list event, not a reliable
                # terminal signal for the current transfer round.
                event["phase"] = event["phase"] or PHASE_FOUND
            elif media_event in ("DOWNLOAD_FAILED", "WIFI_ENABLE_FAIL", "WIFI_CONNECT_FAIL"):
                event["phase"] = PHASE_FAILED
                event["errors"].append(text)
                if not event["failure_reason"]:
                    if media_event in ("WIFI_ENABLE_FAIL", "WIFI_CONNECT_FAIL"):
                        event["failure_reason"] = "wifi_channel_closed"
                    else:
                        event["failure_reason"] = "download_failed"

            progress_match = IMPORT_PROGRESS_LOG_PATTERN.search(text) or IMPORT_NEW_PROGRESS_PATTERN.search(text)
            if progress_match:
                event["progress_percent"] = _safe_int(progress_match.group(1))
            total_count_match = IMPORT_TOTAL_COUNT_PATTERN.search(text) or IMPORT_NEW_TOTAL_PATTERN.search(text)
            if total_count_match:
                total_count = _safe_int(total_count_match.group(1))
                event["media_count"] = total_count
                event["total_count"] = total_count
            success_count_match = (
                IMPORT_SUCCESS_COUNT_PATTERN.search(text)
                or IMPORT_NEW_SUCCESS_COUNT_PATTERN.search(text)
                or IMPORT_NEW_DOWNLOADED_PATTERN.search(text)
            )
            if success_count_match:
                event["download_success_count"] = _safe_int(success_count_match.group(1))
            wifi_err_match = IMPORT_WIFI_ERR_CODE_PATTERN.search(text)
            if wifi_err_match:
                err_code = _safe_int(wifi_err_match.group(1))
                if err_code and err_code != 0:
                    event["errors"].append(text)
            path_match = IMPORT_SUCCESS_PATH_LIST_PATTERN.search(text)
            if path_match:
                for part in path_match.group(1).split(","):
                    cleaned = part.strip()
                    if cleaned:
                        event["success_paths"].append(cleaned)
            if (
                media_event == "DOWNLOAD_FILE"
                and (event.get("total_count") in (None, 0))
                and (event.get("download_success_count") in (None, 0))
            ):
                event["warnings"].append("DOWNLOAD_FILE snapshot detected without transfer counters")

        # New App 2.6.x event stream (even when type= field is absent).
        new_progress = IMPORT_NEW_PROGRESS_PATTERN.search(text)
        if new_progress:
            event["activity"] = True
            event["phase"] = PHASE_DOWNLOAD
            event["progress_percent"] = _safe_int(new_progress.group(1))
            total_match = IMPORT_NEW_TOTAL_PATTERN.search(text)
            if total_match:
                total_count = _safe_int(total_match.group(1))
                event["media_count"] = total_count
                event["total_count"] = total_count
            downloaded_match = IMPORT_NEW_DOWNLOADED_PATTERN.search(text)
            if downloaded_match:
                event["download_success_count"] = _safe_int(downloaded_match.group(1))

        new_stage = IMPORT_NEW_STAGE_PATTERN.search(text)
        if new_stage:
            stage = new_stage.group(1).upper()
            event["activity"] = True
            if stage in ("CONNECTING", "CONNECT"):
                event["phase"] = PHASE_CONNECT
            elif stage in ("DOWNLOADING", "DOWNLOAD"):
                event["phase"] = PHASE_DOWNLOAD
            elif stage in ("SUCCESS",):
                event["phase"] = PHASE_SUCCESS
                event["success_marker"] = True
            elif stage in ("FAILED", "FAIL", "ERROR"):
                event["phase"] = PHASE_FAILED
                event["errors"].append(text)
                if not event["failure_reason"]:
                    event["failure_reason"] = "download_failed"

        new_terminal = IMPORT_NEW_TERMINAL_PATTERN.search(text)
        if new_terminal:
            terminal = new_terminal.group(1).upper()
            event["activity"] = True
            if terminal in ("DOWNLOAD_SUCCESS", "FILES_READY"):
                event["phase"] = PHASE_SUCCESS
                event["success_marker"] = True
            elif terminal in ("DOWNLOAD_FAILED", "WIFI_ENABLE_FAIL", "WIFI_CONNECT_FAIL"):
                event["phase"] = PHASE_FAILED
                event["errors"].append(text)
                if not event["failure_reason"]:
                    event["failure_reason"] = (
                        "wifi_channel_closed"
                        if "WIFI" in terminal
                        else "download_failed"
                    )

        new_path = IMPORT_NEW_FILE_PATH_PATTERN.search(text)
        if new_path:
            event["activity"] = True
            event["success_paths"].append(new_path.group(1).strip())

        media_count_match = IMPORT_MEDIA_COUNT_LOG_PATTERN.search(text)
        if media_count_match:
            photo = _safe_int(media_count_match.group(1)) or 0
            video = _safe_int(media_count_match.group(2)) or 0
            audio = _safe_int(media_count_match.group(3)) or 0
            total = photo + video + audio
            event["media_count"] = total
            event["activity"] = True
            if total > 0:
                event["phase"] = PHASE_FOUND

        if "downloaded file:" in lowered or "文件下载成功:" in text:
            # Per-file success should not be treated as whole-import success.
            event["activity"] = True
            matched_path = None
            en_match = re.search(r"Downloaded file:\s*(\S+)", text, flags=re.IGNORECASE)
            zh_match = re.search(r"文件下载成功:\s*(\S+)", text)
            if en_match:
                matched_path = en_match.group(1).strip()
            elif zh_match:
                matched_path = zh_match.group(1).strip()
            if matched_path:
                event["success_paths"].append(matched_path)

        if (
            event["battery_level"] is not None
            and event["battery_low_level"] is not None
            and event["battery_level"] <= event["battery_low_level"]
            and event["battery_charging"] is not True
        ):
            event["battery_low_detected"] = True
            event["failure_reason"] = "battery_low"
            event["errors"].append(
                "battery_low threshold reached: "
                f"level={event['battery_level']} low_lvl={event['battery_low_level']} "
                f"charging={event['battery_charging']}"
            )

        return event

    def _guess_phase_from_ui(self, ui_state: Dict[str, Any]) -> Optional[str]:
        if not ui_state.get("has_import_card"):
            # 部分机型导入浮层 resource-id 对不上，但仍有「正在导入」文案。
            if ui_state.get("importing_hint"):
                return PHASE_DOWNLOAD
            if ui_state.get("waiting_wifi"):
                return PHASE_CONNECT
            return None
        # 不在这里直接判 SUCCESS：历史成功文案会残留，必须结合本轮下载/日志证据。
        progress = ui_state.get("progress_percent")
        if isinstance(progress, int):
            return PHASE_DOWNLOAD
        if ui_state.get("importing_hint"):
            return PHASE_DOWNLOAD
        if ui_state.get("waiting_wifi"):
            return PHASE_CONNECT
        media_count = ui_state.get("media_count")
        if isinstance(media_count, int) and media_count > 0:
            return PHASE_FOUND
        return None

    def _append_jsonl(self, path: str, payload: Dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as file_obj:
            file_obj.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _save_import_log_text(self, session_id: str, lines: List[str]) -> Optional[str]:
        try:
            os.makedirs(app_config.IMPORT_LOG_DIR, exist_ok=True)
            log_path = os.path.join(app_config.IMPORT_LOG_DIR, f"{session_id}_导入.log")
            with open(log_path, "w", encoding="utf-8") as file_obj:
                file_obj.write("\n".join(lines))
            return log_path
        except Exception as exc:
            logger.warning("Failed to save import log text: %s", exc)
            return None

    def _save_import_diagnostics(self, session_id: str, result: ImportWaitResult) -> Dict[str, str]:
        artifacts: Dict[str, str] = {}
        os.makedirs(app_config.SCREENSHOT_DIR, exist_ok=True)
        os.makedirs(app_config.IMPORT_LOG_DIR, exist_ok=True)
        screenshot_path = os.path.join(app_config.SCREENSHOT_DIR, f"{session_id}_导入失败.png")
        hierarchy_path = os.path.join(app_config.IMPORT_LOG_DIR, f"{session_id}_界面.xml")
        result_path = os.path.join(app_config.IMPORT_LOG_DIR, f"{session_id}_result.json")

        try:
            self.screenshot(screenshot_path)
            artifacts["screenshot"] = screenshot_path
        except Exception as exc:
            _append_unique(result.warnings, f"save screenshot failed: {exc}")

        try:
            xml_text = self.dump_hierarchy()
            with open(hierarchy_path, "w", encoding="utf-8") as file_obj:
                file_obj.write(xml_text)
            artifacts["hierarchy"] = hierarchy_path
        except Exception as exc:
            _append_unique(result.warnings, f"save hierarchy failed: {exc}")

        try:
            with open(result_path, "w", encoding="utf-8") as file_obj:
                json.dump(result.to_dict(), file_obj, ensure_ascii=False, indent=2)
            artifacts["result"] = result_path
        except Exception as exc:
            _append_unique(result.warnings, f"save result json failed: {exc}")

        return artifacts

    def _press_back_once(self) -> None:
        try:
            device = self.ensure_device()
            device.press("back")
        except Exception:
            try:
                self.adb_shell("input keyevent 4")
            except Exception:
                pass
        time.sleep(0.45)

    def _return_home(self, max_steps: int = 2) -> bool:
        return self.ensure_home_screen(max_steps=max_steps)

    def _scroll_home_content(self) -> None:
        """Short upward swipe — large 70%→34% swipes overshoot discovery on short phones."""
        device = self.ensure_device()
        width, height = 1080, 2340
        try:
            info = device.info or {}
            width = int(info.get("displayWidth") or width)
            height = int(info.get("displayHeight") or height)
        except Exception:
            pass
        # Stay left of the side-screen handle and above the bottom navigation pill.
        x = max(80, min(width - 160, int(width * 0.45)))
        y_from = int(height * 0.72)
        y_to = int(height * 0.52)
        logger.info("首页发现区在屏幕下方，短滑后再查找 (%s→%s)", y_from, y_to)
        device.swipe(x, y_from, x, y_to, 0.25)
        time.sleep(0.55)

    def _tap_feature_item(self, label_text: str) -> bool:
        """Tap a discovery-feature tile (e.g. 录音), never the home capture buttons.

        Home page also has a capture button labeled 录音 that *starts recording*.
        When the album grows, the discovery tile sits below the fold: scroll until
        its label is fully on screen, then tap that tile only.
        """
        target = str(label_text or "").strip()
        if not target:
            return False
        last_marker: Optional[int] = None
        for attempt in range(3):
            try:
                xml_text = self.dump_hierarchy()
            except Exception:
                return False
            point = feature_tap_point(xml_text, target)
            if point is not None:
                self.click(point[0], point[1])
                return True
            if attempt >= 2 or not home_can_scroll(xml_text):
                break
            marker = discovery_marker_y(xml_text)
            # Already mid-screen — more swipes push discovery off the top (real-device probe).
            try:
                _w, screen_h = _screen_size_from_root(ET.fromstring(xml_text))
            except Exception:
                screen_h = 1600
            if marker is not None and marker < int(screen_h * 0.45):
                break
            if (
                attempt > 0
                and marker is not None
                and last_marker is not None
                and marker >= last_marker - 40
            ):
                break
            last_marker = marker
            try:
                self._scroll_home_content()
            except Exception:
                return False
        return False

    def _page_total_count_from_xml(self, xml_text: str) -> Optional[int]:
        """录音/相册页标题下的「共 N 个」。"""
        try:
            root = ET.fromstring(xml_text)
        except Exception:
            return None
        for elem in root.iter("node"):
            rid = str(elem.attrib.get("resource-id", "") or "").strip()
            text = str(elem.attrib.get("text", "") or "").strip()
            if rid.endswith("tv_sub_title") or rid.endswith("tv_subtitle"):
                matched = re.search(r"共\s*(\d+)\s*个", text)
                if matched:
                    return _safe_int(matched.group(1))
            matched = re.search(r"^共\s*(\d+)\s*个$", text)
            if matched:
                return _safe_int(matched.group(1))
        return None

    def _visible_media_grid_count_from_xml(self, xml_text: str) -> Optional[int]:
        try:
            root = ET.fromstring(xml_text)
        except Exception:
            return None
        # 录音页是列表 RecyclerView（id/rv），不是相册网格。
        for rid_want in (MEDIA_GRID_RESOURCE_ID, "com.moyoung.glasses:id/rv"):
            for elem in root.iter("node"):
                rid = str(elem.attrib.get("resource-id", "") or "").strip()
                if rid != rid_want:
                    continue
                children = list(elem)
                if not children:
                    return 0
                count = 0
                for child in children:
                    child_class = str(child.attrib.get("class", "") or "")
                    clickable = str(child.attrib.get("clickable", "false")).lower() == "true"
                    if clickable or "ViewGroup" in child_class or "FrameLayout" in child_class or "LinearLayout" in child_class:
                        count += 1
                return count if count > 0 else len(children)
        return None

    def _extract_media_activity_count(self, lines: Iterable[str]) -> Optional[int]:
        latest: Optional[int] = None
        for line in lines:
            text = str(line or "")
            for pattern in MEDIA_ACTIVITY_COUNT_PATTERNS:
                matched = pattern.search(text)
                if not matched:
                    continue
                value = _safe_int(matched.group(1))
                if value is not None and value >= 0:
                    latest = value
        return latest

    def _open_gallery_section(self) -> bool:
        if self.tap_resource_id(GALLERY_SECTION_RESOURCE_ID, timeout=1.2):
            return True
        if self.tap_resource_id(GALLERY_SEE_ALL_RESOURCE_ID, timeout=1.2):
            return True
        if self.tap_text(SEE_ALL_FALLBACK_TEXT, timeout=1.2):
            return True
        if self.tap_resource_id(GALLERY_TITLE_RESOURCE_ID, timeout=1.2):
            return True
        if self.tap_text(GALLERY_FALLBACK_TEXT, timeout=1.2):
            return True
        return False

    def _open_audio_section(self) -> bool:
        # Never fall back to bare tap_text("录音"): that clicks the home
        # capture button and starts recording on the glasses.
        if self._tap_feature_item(FEATURE_AUDIO_TEXT):
            return True
        logger.warning("滑动后仍未找到发现区「录音」入口，跳过录音页打开（避免误点首页开录按钮）")
        return False

    def _capture_category_snapshot(
        self,
        session_id: str,
        stage: str,
        category: str,
        include_screenshot: bool,
    ) -> Dict[str, Any]:
        category_name = str(category or "").strip().lower()
        if category_name not in ("gallery", "audio"):
            raise ValueError(f"unsupported category: {category}")

        package_name = (app_config.PHONE_APP_PACKAGE or "").strip()
        opened = False
        lines: List[str] = []
        warnings: List[str] = []
        monitor: Optional[_LogcatMonitor] = None

        if not self.ensure_home_screen(max_steps=3):
            _append_unique(warnings, f"{category_name} snapshot started without confirmed home screen")

        if package_name:
            monitor, monitor_warnings = self._start_logcat_monitor(package_name)
            for warning in monitor_warnings:
                _append_unique(warnings, warning)

        try:
            if category_name == "gallery":
                opened = self._open_gallery_section()
            else:
                opened = self._open_audio_section()

            if opened:
                end_at = time.time() + 2.2
                while time.time() < end_at:
                    batch = self._drain_logcat_lines(monitor)
                    if batch:
                        lines.extend(batch)
                    time.sleep(0.2)
            else:
                _append_unique(warnings, f"{category_name} section open failed")
        finally:
            tail = self._stop_logcat_monitor(monitor)
            if tail:
                lines.extend(tail)

        xml_text = ""
        visible_count: Optional[int] = None
        log_count = self._extract_media_activity_count(lines)
        hierarchy_path = ""
        screenshot_path = ""
        source = "unavailable"
        count: Optional[int] = None
        page_total: Optional[int] = None
        if opened:
            try:
                xml_text = self.dump_hierarchy()
                page_total = self._page_total_count_from_xml(xml_text)
                visible_count = self._visible_media_grid_count_from_xml(xml_text)
            except Exception as exc:
                _append_unique(warnings, f"dump hierarchy failed: {exc}")

        if page_total is not None:
            count = page_total
            source = "page_subtitle_total"
        elif log_count is not None:
            count = log_count
            source = "media_activity_log"
        elif visible_count is not None:
            count = visible_count
            source = "visible_grid_count"

        if opened:
            os.makedirs(app_config.SCREENSHOT_DIR, exist_ok=True)
            os.makedirs(app_config.IMPORT_LOG_DIR, exist_ok=True)
            label = {"gallery": "相册", "audio": "录音"}.get(category_name, category_name)
            hierarchy_path = os.path.join(app_config.IMPORT_LOG_DIR, f"{session_id}_{label}.xml")
            try:
                if xml_text:
                    with open(hierarchy_path, "w", encoding="utf-8") as file_obj:
                        file_obj.write(xml_text)
            except Exception as exc:
                _append_unique(warnings, f"save {category_name} hierarchy failed: {exc}")
                hierarchy_path = ""
            if include_screenshot:
                screenshot_path = os.path.join(app_config.SCREENSHOT_DIR, f"{session_id}_{label}.png")
                try:
                    self.screenshot(screenshot_path)
                except Exception as exc:
                    _append_unique(warnings, f"save {category_name} screenshot failed: {exc}")
                    screenshot_path = ""

        self._return_home(max_steps=2)
        # Opening wrong "录音" UI can start capture; always best-effort stop afterward.
        if category_name == "audio":
            try:
                self.ensure_capture_stopped(kinds=("audio",), retries=2, settle_seconds=1.0)
            except Exception as exc:
                _append_unique(warnings, f"post-audio-snapshot stop failed: {exc}")
        return {
            "category": category_name,
            "opened": opened,
            "count": count,
            "source": source,
            "visible_count": visible_count,
            "page_total": page_total,
            "log_count": log_count,
            "log_line_count": len(lines),
            "warnings": warnings,
            "screenshot_path": screenshot_path,
            "hierarchy_path": hierarchy_path,
        }

    def capture_media_inventory_snapshot(
        self,
        session_id: str,
        stage: str,
        include_screenshot: bool = False,
        check_gallery: bool = True,
        check_audio: bool = True,
    ) -> Dict[str, Any]:
        self.ensure_device_connected()
        self.wake_screen()
        self.ensure_app_running(app_config.PHONE_APP_PACKAGE)
        self.ensure_home_screen(max_steps=3)

        gallery: Dict[str, Any]
        audio: Dict[str, Any]
        if check_gallery:
            gallery = self._capture_category_snapshot(
                session_id=session_id,
                stage=stage,
                category="gallery",
                include_screenshot=include_screenshot,
            )
        else:
            gallery = {
                "category": "gallery",
                "opened": False,
                "count": None,
                "source": "skipped",
                "visible_count": None,
                "log_count": None,
                "log_line_count": 0,
                "warnings": [],
                "screenshot_path": "",
                "hierarchy_path": "",
            }

        if check_audio:
            audio = self._capture_category_snapshot(
                session_id=session_id,
                stage=stage,
                category="audio",
                include_screenshot=include_screenshot,
            )
        else:
            audio = {
                "category": "audio",
                "opened": False,
                "count": None,
                "source": "skipped",
                "visible_count": None,
                "log_count": None,
                "log_line_count": 0,
                "warnings": [],
                "screenshot_path": "",
                "hierarchy_path": "",
            }

        warnings: List[str] = []
        for warning in gallery.get("warnings", []):
            _append_unique(warnings, warning)
        for warning in audio.get("warnings", []):
            _append_unique(warnings, warning)

        artifacts: Dict[str, str] = {}
        if gallery.get("screenshot_path"):
            artifacts["gallery_screenshot"] = str(gallery["screenshot_path"])
        if audio.get("screenshot_path"):
            artifacts["audio_screenshot"] = str(audio["screenshot_path"])
        if gallery.get("hierarchy_path"):
            artifacts["gallery_hierarchy"] = str(gallery["hierarchy_path"])
        if audio.get("hierarchy_path"):
            artifacts["audio_hierarchy"] = str(audio["hierarchy_path"])

        return {
            "session_id": session_id,
            "stage": stage,
            "captured_at": _now_text(),
            "gallery": gallery,
            "audio": audio,
            "warnings": warnings,
            "artifacts": artifacts,
        }

    def wait_import_completion(
        self,
        timeout_seconds: Optional[float] = None,
        poll_interval_seconds: Optional[float] = None,
        stall_timeout_seconds: Optional[float] = None,
        click_before_wait: bool = False,
        expected_counts: Optional[Dict[str, int]] = None,
    ) -> ImportWaitResult:
        timeout = float(timeout_seconds if timeout_seconds is not None else AUTOMATION_DEFAULT_IMPORT_TIMEOUT_SECONDS)
        poll_interval = max(
            0.3,
            float(
                poll_interval_seconds
                if poll_interval_seconds is not None
                else AUTOMATION_PROGRESS_POLL_INTERVAL_SECONDS
            ),
        )
        stall_timeout = float(
            stall_timeout_seconds if stall_timeout_seconds is not None else AUTOMATION_IMPORT_STALL_TIMEOUT_SECONDS
        )
        settle_seconds = float(AUTOMATION_IMPORT_SUCCESS_SETTLE_SECONDS)

        result = ImportWaitResult(success=False, status="running", phase=PHASE_IDLE, started_at=_now_text())
        session_id = time.strftime("%Y%m%d_%H%M%S")
        result.session_id = session_id
        normalized_expected: Dict[str, int] = {}
        for key in ("photo", "audio", "video", "total", "gallery"):
            try:
                raw = 0 if expected_counts is None else expected_counts.get(key, 0)
                normalized_expected[key] = max(0, int(raw))
            except Exception:
                normalized_expected[key] = 0
        if normalized_expected.get("gallery", 0) <= 0:
            normalized_expected["gallery"] = normalized_expected.get("photo", 0) + normalized_expected.get("video", 0)
        if normalized_expected.get("total", 0) <= 0:
            normalized_expected["total"] = (
                normalized_expected.get("photo", 0)
                + normalized_expected.get("audio", 0)
                + normalized_expected.get("video", 0)
            )
        result.expected_counts = normalized_expected
        result.observed_transfer_counts = {"total_count_max": None, "download_success_count_max": None}
        package_name = (app_config.PHONE_APP_PACKAGE or "").strip()
        if not package_name:
            result.status = "failed"
            result.failure_reason = "config_error"
            result.error_signals.append("PHONE_APP_PACKAGE is empty")
            result.finished_at = _now_text()
            return result

        monitor, monitor_warnings = self._start_logcat_monitor(package_name)
        for warning in monitor_warnings:
            _append_unique(result.warnings, warning)

        log_lines: List[str] = []
        start_ts = time.time()
        deadline = start_ts + timeout
        click_deadline = start_ts + min(25.0, timeout)
        last_activity_ts = start_ts
        last_click_attempt_ts = 0.0
        success_marker_ts: Optional[float] = None
        last_logged_phase = result.phase
        last_logged_progress: Optional[int] = result.progress_percent

        click_done = not click_before_wait
        auto_started_without_click = False
        ignored_early_success_hint = False
        seen_import_activity = False
        seen_download_activity = False
        ui_failed_hint_detected = False
        has_logcat_monitor = monitor is not None
        log_total_count: Optional[int] = None
        log_success_count: Optional[int] = None
        saw_log_terminal_success = False
        battery_level: Optional[int] = None
        battery_low_level: Optional[int] = None
        battery_power_off_level: Optional[int] = None
        battery_charging: Optional[bool] = None
        click_confirmed_ts: Optional[float] = None
        first_waiting_wifi_ts: Optional[float] = None
        first_progress_ts: Optional[float] = None
        download_started_ts: Optional[float] = None
        final_success_ts: Optional[float] = None

        logger.info(
            "Wait import completion: timeout=%.1fs poll=%.1fs stall=%.1fs",
            timeout,
            poll_interval,
            stall_timeout,
        )

        last_screen_check_ts = 0.0
        idle_dismiss_count = 0
        last_screen_texts: List[str] = []
        mid_dump_done = False
        import_reclick_done = False
        try:
            while time.time() < deadline:
                now = time.time()
                silent_for = now - last_activity_ts

                # 一段时间完全没进度就看屏幕，关弹窗；不要干等到总超时。
                if silent_for >= 15.0 and (now - last_screen_check_ts) >= 12.0:
                    last_screen_check_ts = now
                    # Already succeeded — do not keep dumping noisy screens / stall-wait.
                    if result.phase in (PHASE_SUCCESS, PHASE_IDLE_AFTER_SUCCESS) or success_marker_ts is not None:
                        continue
                    screen = self.inspect_and_dismiss_dialogs()
                    last_screen_texts = list(screen.get("texts") or [])
                    screen_blob = " ".join(str(t) for t in last_screen_texts).lower()
                    ui_still_importing = any(
                        hint.lower() in screen_blob for hint in UI_IMPORTING_HINTS
                    ) or any(hint.lower() in screen_blob for hint in UI_WAITING_HINTS)
                    if ui_still_importing:
                        # 界面在传/等 Wi-Fi，只是没有数字进度条——不算卡住。
                        last_activity_ts = now
                        logger.info(
                            "导入进行中（界面无数字进度条）已 %.0fs，当前界面: %s",
                            silent_for,
                            _summarize_screen_texts(last_screen_texts),
                        )
                    else:
                        logger.info(
                            "已 %.0fs 无进度，当前界面: %s",
                            silent_for,
                            _summarize_screen_texts(last_screen_texts),
                        )
                    dismissed = str(screen.get("dismissed") or "")
                    if dismissed and idle_dismiss_count < 3:
                        idle_dismiss_count += 1
                        logger.info("无响应时关闭弹窗: %s", dismissed)
                        last_activity_ts = now
                        time.sleep(poll_interval)
                        continue

                if click_before_wait and not click_done and (now - last_click_attempt_ts) >= 1.0:
                    last_click_attempt_ts = now
                    if self.click_import_button():
                        click_done = True
                        last_activity_ts = now
                        click_confirmed_ts = now
                        result.click_confirmed_at = _ts_to_text(click_confirmed_ts)
                        logger.info("Import click confirmed")
                    elif now >= click_deadline:
                        result.status = "click_failed"
                        if not result.failure_reason:
                            result.failure_reason = "click_failed"
                        _append_unique(result.error_signals, "import button not found within click window")
                        break

                # 已找到媒体但迟迟不进 Wi-Fi/下载：再点一次导入（部分机型要点两次）。
                if (
                    not import_reclick_done
                    and click_done
                    and result.phase == PHASE_FOUND
                    and (result.media_count or 0) > 0
                    and not seen_download_activity
                    and first_waiting_wifi_ts is None
                    and result.progress_percent is None
                    and click_confirmed_ts is not None
                    and (now - click_confirmed_ts) >= 20.0
                ):
                    import_reclick_done = True
                    if self.click_import_button():
                        last_activity_ts = now
                        logger.info("已找到媒体但未开始传输，再次点击导入")
                    else:
                        logger.info("已找到媒体但未开始传输，未再次点到导入按钮")

                batch = self._drain_logcat_lines(monitor)
                # 实时流仍空时，中途 dump 一次，避免只能靠 UI media_count 误判成功。
                if (
                    not mid_dump_done
                    and not batch
                    and len(log_lines) < 5
                    and click_done
                    and (now - start_ts) >= 18.0
                ):
                    mid_dump_done = True
                    batch = self._dump_recent_logcat(limit=3000)
                    if batch:
                        logger.info("中途 logcat dump：补到 %s 行", len(batch))
                if batch:
                    log_lines.extend(batch)
                    if len(log_lines) > 8000:
                        log_lines = log_lines[-8000:]

                for line in batch:
                    event = self._parse_import_log_line(line)
                    for warning in event["warnings"]:
                        _append_unique(result.warnings, warning)
                    for err in event["errors"]:
                        _append_unique(result.error_signals, err)
                    for path in event["success_paths"]:
                        _append_unique(result.success_paths, path)

                    if event.get("battery_level") is not None:
                        battery_level = _safe_int(event.get("battery_level"))
                    if event.get("battery_low_level") is not None:
                        battery_low_level = _safe_int(event.get("battery_low_level"))
                    if event.get("battery_power_off_level") is not None:
                        battery_power_off_level = _safe_int(event.get("battery_power_off_level"))
                    if event.get("battery_charging") is not None:
                        battery_charging = _safe_bool(event.get("battery_charging"))
                    if event.get("battery_low_detected"):
                        result.battery_low_detected = True
                    if event.get("storage_full_detected"):
                        result.storage_full_detected = True
                    if event.get("failure_reason") and not result.failure_reason:
                        result.failure_reason = str(event.get("failure_reason"))

                    if event["media_count"] is not None:
                        result.media_count = event["media_count"]
                        last_activity_ts = now
                        if event["media_count"] > 0:
                            seen_import_activity = True
                    if event.get("total_count") is not None:
                        current_total = _safe_int(event.get("total_count"))
                        if current_total is not None:
                            log_total_count = current_total if log_total_count is None else max(log_total_count, current_total)
                    if event.get("download_success_count") is not None:
                        current_success = _safe_int(event.get("download_success_count"))
                        if current_success is not None:
                            log_success_count = (
                                current_success if log_success_count is None else max(log_success_count, current_success)
                            )
                    # App/log 没给 % 时，用 已完成/总数 自己算。
                    if (
                        event["progress_percent"] is None
                        and log_total_count is not None
                        and log_total_count > 0
                        and log_success_count is not None
                        and 0 <= log_success_count <= log_total_count
                    ):
                        event["progress_percent"] = int(round(100.0 * log_success_count / log_total_count))

                    if event["phase"]:
                        if event["phase"] != result.phase:
                            result.phase = event["phase"]
                            last_activity_ts = now
                        if event["phase"] == PHASE_CONNECT and first_waiting_wifi_ts is None:
                            first_waiting_wifi_ts = now
                            result.first_waiting_wifi_at = _ts_to_text(first_waiting_wifi_ts)
                        if event["phase"] in (PHASE_FOUND, PHASE_CONNECT, PHASE_DOWNLOAD, PHASE_SUCCESS, PHASE_IDLE_AFTER_SUCCESS):
                            seen_import_activity = True
                        if event["phase"] == PHASE_DOWNLOAD:
                            seen_download_activity = True
                            if download_started_ts is None and first_progress_ts is not None:
                                download_started_ts = now
                                result.download_started_at = _ts_to_text(download_started_ts)
                        if event["phase"] in (PHASE_SUCCESS, PHASE_IDLE_AFTER_SUCCESS):
                            saw_log_terminal_success = True

                    if event["progress_percent"] is not None:
                        progress_value = _safe_int(event["progress_percent"])
                        if first_progress_ts is None and progress_value is not None:
                            first_progress_ts = now
                            result.first_progress_at = _ts_to_text(first_progress_ts)
                            result.first_progress_percent = progress_value
                        if download_started_ts is None and progress_value is not None and progress_value > 0:
                            download_started_ts = now
                            result.download_started_at = _ts_to_text(download_started_ts)
                        if event["progress_percent"] != result.progress_percent:
                            result.progress_percent = event["progress_percent"]
                            last_activity_ts = now
                        if progress_value is not None and progress_value >= 100:
                            result.progress_reached_100 = True
                        if event["progress_percent"] >= 0:
                            seen_download_activity = True

                    if event["activity"]:
                        last_activity_ts = now

                    if click_before_wait and not click_done and event["phase"] in (
                        PHASE_CONNECT,
                        PHASE_DOWNLOAD,
                        PHASE_SUCCESS,
                        PHASE_IDLE_AFTER_SUCCESS,
                    ):
                        click_done = True
                        click_confirmed_ts = now
                        result.click_confirmed_at = _ts_to_text(click_confirmed_ts)
                        if not auto_started_without_click:
                            _append_unique(result.warnings, "import flow started before click was detected")
                            auto_started_without_click = True

                    if event["success_marker"] and (click_done or seen_import_activity):
                        if success_marker_ts is None:
                            success_marker_ts = now
                        last_activity_ts = now

                try:
                    xml_text = self.dump_hierarchy()
                    ui_state = self._inspect_import_ui(xml_text)

                    ui_media_count = ui_state.get("media_count")
                    if ui_media_count is not None:
                        result.media_count = ui_media_count
                        if ui_media_count > 0:
                            seen_import_activity = True

                    ui_battery_level = _safe_int(ui_state.get("battery_level"))
                    if ui_battery_level is not None:
                        battery_level = ui_battery_level

                    ui_battery_charging = _safe_bool(ui_state.get("battery_charging"))
                    if ui_battery_charging is not None:
                        battery_charging = ui_battery_charging

                    if ui_state.get("battery_low_hint"):
                        result.battery_low_detected = True
                        if not result.failure_reason:
                            result.failure_reason = "battery_low"
                        hint_text = ", ".join(ui_state.get("battery_texts") or [])
                        if hint_text:
                            _append_unique(result.error_signals, f"battery_low: ui_hint={hint_text}")
                        else:
                            _append_unique(result.error_signals, "battery_low: ui_hint_detected")

                    if ui_state.get("storage_full_hint"):
                        result.storage_full_detected = True
                        if not result.failure_reason:
                            result.failure_reason = "storage_full"
                        storage_text = ", ".join(ui_state.get("storage_hint_texts") or [])
                        if storage_text:
                            _append_unique(result.error_signals, f"storage_full: ui_hint={storage_text}")
                        else:
                            _append_unique(result.error_signals, "storage_full: ui_hint_detected")

                    if ui_state.get("failed_hint"):
                        failed_text = ", ".join(ui_state.get("failed_texts") or [])
                        if failed_text:
                            _append_unique(result.error_signals, f"import_failed: ui_hint={failed_text}")
                        else:
                            _append_unique(result.error_signals, "import_failed: ui_hint_detected")
                        if click_done or seen_import_activity:
                            ui_failed_hint_detected = True
                            last_activity_ts = now
                            seen_import_activity = True

                    if ui_state.get("waiting_wifi") and first_waiting_wifi_ts is None:
                        first_waiting_wifi_ts = now
                        result.first_waiting_wifi_at = _ts_to_text(first_waiting_wifi_ts)

                    if ui_state.get("importing_hint") or ui_state.get("waiting_wifi"):
                        # 心跳：有「正在导入/靠近手机」就刷新活动时间，避免误报无进度。
                        last_activity_ts = now
                        seen_import_activity = True
                        if ui_state.get("importing_hint"):
                            seen_download_activity = True
                            if download_started_ts is None:
                                download_started_ts = now
                                result.download_started_at = _ts_to_text(download_started_ts)

                    ui_progress = ui_state.get("progress_percent")
                    if ui_progress is not None:
                        parsed_ui_progress = _safe_int(ui_progress)
                        if first_progress_ts is None and parsed_ui_progress is not None:
                            first_progress_ts = now
                            result.first_progress_at = _ts_to_text(first_progress_ts)
                            result.first_progress_percent = parsed_ui_progress
                        if download_started_ts is None and parsed_ui_progress is not None and parsed_ui_progress > 0:
                            download_started_ts = now
                            result.download_started_at = _ts_to_text(download_started_ts)
                        if ui_progress != result.progress_percent:
                            result.progress_percent = ui_progress
                            last_activity_ts = now
                        if parsed_ui_progress is not None and parsed_ui_progress >= 100:
                            result.progress_reached_100 = True
                        seen_download_activity = True
                        seen_import_activity = True

                    ui_phase = self._guess_phase_from_ui(ui_state)
                    if ui_phase:
                        if ui_phase != result.phase:
                            result.phase = ui_phase
                            last_activity_ts = now
                        if ui_phase in (PHASE_FOUND, PHASE_CONNECT, PHASE_DOWNLOAD, PHASE_SUCCESS):
                            seen_import_activity = True
                        if ui_phase == PHASE_DOWNLOAD:
                            seen_download_activity = True
                            if download_started_ts is None and first_progress_ts is not None:
                                download_started_ts = now
                                result.download_started_at = _ts_to_text(download_started_ts)

                    if click_before_wait and not click_done and (ui_state.get("waiting_wifi") or ui_progress is not None):
                        click_done = True
                        click_confirmed_ts = now
                        result.click_confirmed_at = _ts_to_text(click_confirmed_ts)
                        if not auto_started_without_click:
                            _append_unique(result.warnings, "UI indicates import started before click detection")
                            auto_started_without_click = True

                    if ui_state.get("success_hint") and success_marker_ts is None:
                        # UI "success" text may persist from previous rounds; only trust it
                        # when we have current-round download activity or log terminal success.
                        strong_ui_success = bool(
                            seen_download_activity
                            or saw_log_terminal_success
                            or result.progress_reached_100
                            or (log_success_count is not None and log_success_count > 0)
                            or bool(result.success_paths)
                        )
                        # 部分机型不显示进度，只有「已找到 N 个」+ 成功文案；
                        # 要求：点过导入、先忽略过一轮残留成功、本轮已看到媒体数、且距点击足够久。
                        late_ui_success = bool(
                            ignored_early_success_hint
                            and click_done
                            and seen_import_activity
                            and (result.media_count or 0) > 0
                            and click_confirmed_ts is not None
                            and (now - click_confirmed_ts) >= 15.0
                        )
                        if strong_ui_success or late_ui_success:
                            result.phase = PHASE_SUCCESS
                            success_marker_ts = now
                            if late_ui_success and not strong_ui_success:
                                _append_unique(
                                    result.warnings,
                                    "accepted late UI success hint after media found (no progress UI)",
                                )
                        elif not ignored_early_success_hint:
                            _append_unique(result.warnings, "ignored early UI success hint before current import activity")
                            ignored_early_success_hint = True
                except Exception as exc:
                    _append_unique(result.warnings, f"dump/parse UI failed: {exc}")

                result.battery_level = battery_level
                result.battery_low_level = battery_low_level
                result.battery_power_off_level = battery_power_off_level
                result.battery_charging = battery_charging

                if (
                    result.battery_level is not None
                    and result.battery_low_level is not None
                    and result.battery_level <= result.battery_low_level
                    and result.battery_charging is not True
                ):
                    result.battery_low_detected = True
                    if not result.failure_reason:
                        result.failure_reason = "battery_low"
                    _append_unique(
                        result.error_signals,
                        "battery_low threshold reached: "
                        f"level={result.battery_level} low_lvl={result.battery_low_level} "
                        f"charging={result.battery_charging}",
                    )

                if result.phase != last_logged_phase:
                    logger.info("Import phase -> %s", result.phase)
                    last_logged_phase = result.phase
                if result.progress_percent != last_logged_progress and result.progress_percent is not None:
                    if (
                        log_total_count is not None
                        and log_total_count > 0
                        and log_success_count is not None
                    ):
                        logger.info(
                            "Import progress -> %s%% (%s/%s)",
                            result.progress_percent,
                            log_success_count,
                            log_total_count,
                        )
                    else:
                        logger.info("Import progress -> %s%%", result.progress_percent)
                    last_logged_progress = result.progress_percent

                if result.battery_low_detected:
                    if not result.failure_reason:
                        result.failure_reason = "battery_low"
                    result.status = "battery_low"
                    break

                if result.storage_full_detected:
                    if not result.failure_reason:
                        result.failure_reason = "storage_full"
                    result.status = "storage_full"
                    break

                if ui_failed_hint_detected:
                    if not result.failure_reason:
                        inferred_reason = _infer_failure_reason_from_signals(result.error_signals)
                        result.failure_reason = inferred_reason or "import_failed"
                    if result.failure_reason == "storage_full":
                        result.storage_full_detected = True
                        result.status = "storage_full"
                    else:
                        result.status = "failed"
                    break

                severe = any(
                    ("download_failed" in _normalize(signal))
                    or ("wifi_connect_fail" in _normalize(signal))
                    or ("wifi_enable_fail" in _normalize(signal))
                    or ("updatedownloadstate :failed" in _normalize(signal))
                    for signal in result.error_signals
                )
                if severe:
                    result.status = "failed"
                    if not result.failure_reason:
                        inferred_reason = _infer_failure_reason_from_signals(result.error_signals)
                        result.failure_reason = inferred_reason or "import_failed"
                    break

                counts_completed = (
                    log_total_count is not None
                    and log_total_count > 0
                    and log_success_count is not None
                    and log_success_count >= log_total_count
                )
                if counts_completed and success_marker_ts is None:
                    success_marker_ts = now

                if success_marker_ts is None and result.phase in (PHASE_SUCCESS, PHASE_IDLE_AFTER_SUCCESS):
                    # 必须有本轮传输证据，不能只靠「找到媒体 / 残留成功文案」。
                    if (
                        saw_log_terminal_success
                        or counts_completed
                        or result.progress_reached_100
                        or seen_download_activity
                        or (log_success_count is not None and log_success_count > 0)
                        or bool(result.success_paths)
                    ):
                        success_marker_ts = now
                elif has_logcat_monitor and seen_import_activity:
                    if saw_log_terminal_success and success_marker_ts is None:
                        success_marker_ts = now

                if success_marker_ts is not None and (now - success_marker_ts) >= settle_seconds:
                    # Prefer logcat counts, but also accept UI/log SUCCESS after real transfer activity.
                    evidence_ok = bool(
                        counts_completed
                        or saw_log_terminal_success
                        or result.progress_reached_100
                        or seen_download_activity
                        or (log_success_count is not None and log_success_count > 0)
                        or bool(result.success_paths)
                        or (
                            result.phase in (PHASE_SUCCESS, PHASE_IDLE_AFTER_SUCCESS)
                            and ignored_early_success_hint
                            and (result.media_count or 0) > 0
                            and click_confirmed_ts is not None
                            and (now - click_confirmed_ts) >= 20.0
                        )
                    )
                    if evidence_ok:
                        result.success = True
                        result.status = "success"
                        result.failure_reason = ""
                        final_success_ts = now
                        result.final_success_at = _ts_to_text(final_success_ts)
                        if result.phase != PHASE_IDLE_AFTER_SUCCESS:
                            result.phase = PHASE_SUCCESS
                        logger.info("导入已完成，结束等待（phase=%s）", result.phase)
                        break

                # Never stall-fail after SUCCESS — that left the desktop UI stuck "进行中" then retrying.
                if (
                    stall_timeout > 0
                    and (now - last_activity_ts) >= stall_timeout
                    and result.phase not in (PHASE_SUCCESS, PHASE_IDLE_AFTER_SUCCESS)
                    and success_marker_ts is None
                    and (click_done or result.phase in (PHASE_FOUND, PHASE_CONNECT, PHASE_DOWNLOAD))
                ):
                    screen_hint = _summarize_screen_texts(last_screen_texts, limit=12)
                    stalled_msg = f"import stalled for {stall_timeout:.1f}s at phase {result.phase}"
                    if screen_hint:
                        stalled_msg += f"; screen={screen_hint}"
                    _append_unique(result.error_signals, stalled_msg)
                    result.status = "stalled"
                    if not result.failure_reason:
                        result.failure_reason = "stalled"
                    logger.error("导入无响应，停止等待: %s", stalled_msg)
                    logger.error(
                        "导入卡住诊断 phase=%s progress=%s media_count=%s "
                        "waiting_wifi_s=%s first_progress_at=%s download_started_at=%s "
                        "success_paths=%s screen=%s",
                        result.phase,
                        result.progress_percent,
                        result.media_count,
                        None
                        if first_waiting_wifi_ts is None
                        else round(now - first_waiting_wifi_ts, 2),
                        _ts_to_text(first_progress_ts) if first_progress_ts else "",
                        _ts_to_text(download_started_ts) if download_started_ts else "",
                        len(result.success_paths),
                        screen_hint,
                    )
                    break

                time.sleep(poll_interval)

            if result.status == "running":
                result.status = "timeout"
                if not result.failure_reason:
                    result.failure_reason = "timeout"
        finally:
            tail_lines = self._stop_logcat_monitor(monitor)
            if tail_lines:
                log_lines.extend(tail_lines)
                if len(log_lines) > 8000:
                    log_lines = log_lines[-8000:]

        # 实时监控若几乎为空（常见于 --pid / 设备裁剪），补 dump 再解析路径与计数。
        if len(log_lines) < 8:
            dumped = self._dump_recent_logcat(limit=5000)
            if dumped:
                before = len(result.success_paths)
                log_lines.extend(dumped)
                if len(log_lines) > 8000:
                    log_lines = log_lines[-8000:]
                log_total_count, log_success_count, dump_terminal = self._recover_import_metrics_from_logs(
                    dumped,
                    result,
                    log_total_count=log_total_count,
                    log_success_count=log_success_count,
                )
                if dump_terminal:
                    saw_log_terminal_success = True
                logger.info(
                    "logcat dump 回补: paths %s→%s total=%s success=%s terminal=%s",
                    before,
                    len(result.success_paths),
                    log_total_count,
                    log_success_count,
                    saw_log_terminal_success,
                )
                _append_unique(result.warnings, "import metrics recovered from logcat dump")

        # dump 回补后若证据足够，把 timeout/stalled 纠正为成功（此前常因空 logcat 误失败）。
        if (
            not result.success
            and result.status in ("timeout", "stalled", "running")
            and result.failure_reason not in ("battery_low", "storage_full", "click_failed")
        ):
            recovered_ok = bool(
                saw_log_terminal_success
                or result.progress_reached_100
                or result.success_paths
                or (
                    log_total_count is not None
                    and log_success_count is not None
                    and log_total_count > 0
                    and log_success_count >= log_total_count
                )
            )
            if recovered_ok:
                result.success = True
                result.status = "success"
                result.failure_reason = ""
                if final_success_ts is None:
                    final_success_ts = time.time()
                    result.final_success_at = _ts_to_text(final_success_ts)
                if result.phase not in (PHASE_SUCCESS, PHASE_IDLE_AFTER_SUCCESS):
                    result.phase = PHASE_SUCCESS
                logger.info("依据 dump/路径回补将导入结果纠正为成功")

        result.finished_at = _now_text()
        result.duration_seconds = round(time.time() - start_ts, 2)
        if result.success and final_success_ts is None:
            final_success_ts = time.time()
            result.final_success_at = _ts_to_text(final_success_ts)
        if click_confirmed_ts is not None and not result.click_confirmed_at:
            result.click_confirmed_at = _ts_to_text(click_confirmed_ts)
        if first_waiting_wifi_ts is not None and not result.first_waiting_wifi_at:
            result.first_waiting_wifi_at = _ts_to_text(first_waiting_wifi_ts)
        if first_progress_ts is not None and not result.first_progress_at:
            result.first_progress_at = _ts_to_text(first_progress_ts)
        if download_started_ts is not None and not result.download_started_at:
            result.download_started_at = _ts_to_text(download_started_ts)

        # 等待 Wi-Fi = 首次进入等待/CONNECT → 首次出现进度或开始下载。
        wifi_end_ts = first_progress_ts if first_progress_ts is not None else download_started_ts
        if (
            first_waiting_wifi_ts is not None
            and wifi_end_ts is not None
            and wifi_end_ts >= first_waiting_wifi_ts
        ):
            result.waiting_wifi_seconds = round(wifi_end_ts - first_waiting_wifi_ts, 2)
        else:
            result.waiting_wifi_seconds = None

        transfer_started_ts = download_started_ts if download_started_ts is not None else first_progress_ts
        if transfer_started_ts is None and click_confirmed_ts is not None:
            transfer_started_ts = click_confirmed_ts
        if transfer_started_ts is not None and final_success_ts is not None and final_success_ts >= transfer_started_ts:
            result.transfer_duration_seconds = round(final_success_ts - transfer_started_ts, 2)
        elif result.success and result.duration_seconds is not None:
            # 没有细粒度时间戳时，至少用整段导入耗时，避免表里全是「-」。
            result.transfer_duration_seconds = float(result.duration_seconds)
        else:
            result.transfer_duration_seconds = None

        result.observed_transfer_counts = {
            "total_count_max": log_total_count,
            "download_success_count_max": log_success_count,
        }
        if not result.success and not result.failure_reason:
            result.failure_reason = result.status or "failed"

        log_path = self._save_import_log_text(session_id, log_lines)
        if log_path:
            result.artifact_paths["logcat"] = log_path

        if not result.success:
            result.artifact_paths.update(self._save_import_diagnostics(session_id, result))
            screenshot_path = result.artifact_paths.get("screenshot", "")
            if screenshot_path:
                logger.warning("Import failure screenshot saved: %s", screenshot_path)

        record = result.to_dict()
        record["session_id"] = session_id
        record["package_name"] = package_name
        record["log_line_count"] = len(log_lines)
        record["logcat_path"] = result.artifact_paths.get("logcat", "")
        record["error_screenshot_path"] = result.artifact_paths.get("screenshot", "")
        record["error_hierarchy_path"] = result.artifact_paths.get("hierarchy", "")
        record["error_result_path"] = result.artifact_paths.get("result", "")
        log_file = app_config.IMPORT_SUCCESS_LOG_FILE if result.success else app_config.IMPORT_ERROR_LOG_FILE
        self._append_jsonl(log_file, record)

        if result.success:
            logger.info(
                "Import completed: phase=%s progress=%s duration=%.2fs",
                result.phase,
                result.progress_percent,
                result.duration_seconds,
            )
        else:
            logger.warning(
                "Import not completed: status=%s reason=%s phase=%s duration=%.2fs "
                "progress=%s paths=%s wifi_s=%s transfer_s=%s artifacts=%s",
                result.status,
                result.failure_reason,
                result.phase,
                result.duration_seconds,
                result.progress_percent,
                len(result.success_paths),
                result.waiting_wifi_seconds,
                result.transfer_duration_seconds,
                sorted(result.artifact_paths.keys()),
            )

        return result

    def run(self) -> None:
        self.ensure_device_connected()
        self.connect()
        logger.info("Phone operations module is ready")


def run_phone_operations() -> None:
    PhoneOperations().run()


def main() -> None:
    parser = argparse.ArgumentParser(description="Phone operations module")
    parser.add_argument("--device", default=PHONE_DEVICE_SERIAL, help="Device serial")
    parser.add_argument("--package", default=PHONE_APP_PACKAGE, help="App package")
    args = parser.parse_args()
    if args.device:
        logger.info("Current device serial: %s", args.device)
    if args.package:
        logger.info("Current app package: %s", args.package)
    run_phone_operations()


if __name__ == "__main__":
    main()

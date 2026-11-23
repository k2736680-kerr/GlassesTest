#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resolution check: firmware → device-config API → set 横/竖 → capture → import → measure."""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import glasstest.config as app_config
from glasstest.app_ui import read_mcu_firmware_version, set_photo_orientation, set_video_orientation
from glasstest.device_config_api import fetch_device_config, soft_version_from_mcu_firmware
from glasstest.media_measure import measure_local_file, pull_media_file, pull_via_run_as, sizes_match
from glasstest.media_paths import (
    MEDIA_RES_REL,
    extract_paths_from_import_result,
    media_recency_key,
    pick_latest_typed_paths,
    resolve_media_rel_path,
)
from glasstest.phone_operations import PhoneOperations
from glasstest.resolution_spec import build_expectation_summary, expected_photo_size, expected_video_size
from glasstest.config import PHONE_APP_PACKAGE, RUN_LOG_DIR

LOG_FORMAT = "%(asctime)s | %(levelname)s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
logger = logging.getLogger("resolution_check")
logger.setLevel(logging.INFO)
logger.handlers.clear()
logger.propagate = False
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
logger.addHandler(_handler)


def _now_slug() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _attach_file_logger(report_dir: str, slug: str, filename: str = "") -> str:
    path = os.path.join(report_dir, filename or f"runtime_debug_{slug}.log")
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
    handler.setLevel(logging.INFO)
    logger.addHandler(handler)
    return path


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _size_text(size: Optional[Tuple[int, int]]) -> str:
    if not size:
        return "-"
    return f"{size[0]}×{size[1]}"


def _normalize_orientation(value: str) -> str:
    text = str(value or "").strip().lower()
    if text in {"portrait", "竖屏", "vertical"}:
        return "portrait"
    if text in {"landscape", "横屏", "horizontal"}:
        return "landscape"
    raise ValueError(f"不支持的方向: {value!r}，可选 landscape/portrait")


def _run_as_exists(phone: PhoneOperations, serial: str, package: str, rel_path: str) -> bool:
    result = phone.adb_run(
        ["adb", "-s", serial, "shell", "run-as", package, "ls", rel_path],
        check=False,
        timeout=20,
    )
    blob = f"{result.stdout or ''}\n{result.stderr or ''}"
    if result.returncode != 0 or "No such file" in blob:
        return False
    return bool((result.stdout or "").strip())


def _list_media_res_names(phone: PhoneOperations, serial: str, package: str) -> List[str]:
    result = phone.adb_run(
        ["adb", "-s", serial, "shell", "run-as", package, "ls", MEDIA_RES_REL],
        check=False,
        timeout=20,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]


def _list_media_res_fallback(phone: PhoneOperations, serial: str, package: str) -> List[str]:
    """If logcat paths missing, list app media_res (run-as relative, phone-user agnostic)."""
    try:
        names = _list_media_res_names(phone, serial, package)
        return [
            f"{MEDIA_RES_REL}/{name}"
            for name in sorted(names, key=media_recency_key, reverse=True)
        ]
    except Exception as exc:
        logger.warning("列举 media_res 失败: %s", exc)
        return []


def _list_public_gallery_fallback(serial: str, media: str) -> List[str]:
    """Non-debuggable APK: fall back to newest public gallery files (Pictures/Movies)."""
    from glasstest.media_measure import PUBLIC_SEARCH_ROOTS, _adb_shell

    if media == "photo":
        name_filter = "-name '*.jpg' -o -name '*.jpeg' -o -name '*.png' -o -name 'aw_image_*'"
    else:
        name_filter = "-name '*.mp4' -o -name '*stabilized-*' -o -name 'video-*'"
    roots = " ".join(f"'{root}'" for root in PUBLIC_SEARCH_ROOTS)
    script = f"find {roots} -maxdepth 3 -type f \\( {name_filter} \\) 2>/dev/null"
    listed = _adb_shell(serial, script, 30)
    paths = [line.strip() for line in (listed.stdout or "").splitlines() if line.strip().startswith("/")]
    paths = sorted(paths, key=media_recency_key, reverse=True)
    if paths:
        logger.info("公共相册回退找到 %s 个%s文件，最新=%s", len(paths), media, paths[0])
    return paths[:20]


def _resolve_device_media_path(phone: PhoneOperations, serial: str, package: str, path: str) -> str:
    resolved = resolve_media_rel_path(
        path,
        package=package,
        exists_fn=lambda rel: _run_as_exists(phone, serial, package, rel),
        list_names_fn=lambda: _list_media_res_names(phone, serial, package),
    )
    if resolved != path:
        logger.info("媒体路径映射 %s -> %s", path, resolved)
    return resolved


def run_one_case(
    *,
    phone: PhoneOperations,
    serial: str,
    package: str,
    orientation: str,
    media: str,
    video_seconds: int,
    expected: Tuple[int, int],
    pull_dir: Path,
) -> Dict[str, Any]:
    case: Dict[str, Any] = {
        "orientation": orientation,
        "media": media,
        "expected": expected,
        "actual": None,
        "passed": False,
        "detail": "",
        "local_file": "",
        "device_path": "",
    }
    orientation = _normalize_orientation(orientation)
    orient_label = "竖屏" if orientation == "portrait" else "横屏"
    logger.info("=== 用例 %s / %s ===", orient_label, media)
    if media == "photo":
        set_info = set_photo_orientation(phone, orient_label)
        case["orientation_set"] = set_info
    else:
        set_info = {"target": orient_label, "changed": False, "confirmed": True}
        case["orientation_set"] = set_info
        video_info = set_video_orientation(phone, orientation)
        case["video_orientation_set"] = video_info
        if not video_info.get("supported") and orientation == "landscape":
            case["skipped"] = True
            case["passed"] = None
            case["detail"] = "设备相机设置未显示「视频比例」，按规则跳过横屏视频；该设备视频默认仅支持竖屏"
            case["elapsed_seconds"] = 0.0
            return case

    started = time.time()
    if media == "photo":
        phone.take_device_photo()
        time.sleep(2.5)
    else:
        phone.start_device_video_recording(duration_seconds=0)
        time.sleep(max(3, int(video_seconds)))
        phone.stop_device_video_recording()
        time.sleep(2.5)

    import_result = phone.wait_import_completion(
        expected_counts={
            "photo": 1 if media == "photo" else 0,
            "video": 1 if media == "video" else 0,
            "audio": 0,
            "gallery": 1,
            "total": 1,
        },
        click_before_wait=True,
    )
    case["import_success"] = bool(getattr(import_result, "success", False))
    case["import_status"] = getattr(import_result, "status", "")
    artifacts = getattr(import_result, "artifact_paths", {}) or {}
    import_log = str(artifacts.get("logcat") or "")
    if import_log:
        case["import_log"] = import_log
    evidence = _video_process_evidence(import_log) if media == "video" else {}
    if evidence:
        case["video_process"] = evidence
    paths = extract_paths_from_import_result(import_result)
    if not paths:
        logger.info(
            "导入结果无路径 status=%s success=%s，尝试 media_res / 公共相册回退",
            getattr(import_result, "status", ""),
            getattr(import_result, "success", False),
        )
        paths = _list_media_res_fallback(phone, serial, package)
    typed = pick_latest_typed_paths(paths, media)
    if not typed:
        public_paths = _list_public_gallery_fallback(serial, media)
        typed = pick_latest_typed_paths(public_paths, media) or list(public_paths)
    if not typed:
        case["detail"] = "导入后未找到媒体路径"
        case["elapsed_seconds"] = round(time.time() - started, 2)
        return case

    # 日志路径通常按发现顺序追加，回退目录列表则已按媒体名时间戳倒序。
    # 统一按文件名时间戳优先，避免取到旧导入文件。
    device_path = max(typed, key=media_recency_key)
    device_path = _resolve_device_media_path(phone, serial, package, device_path)
    case["device_path"] = device_path
    if app_config.ACTIVE_RUN_DIR:
        kind_name = "照片" if media == "photo" else "视频"
        suffix = Path(device_path).suffix or (".jpg" if media == "photo" else ".mp4")
        local_name = f"{orient_label}_{kind_name}{suffix}"
    else:
        local_name = f"{orient_label}_{media}_{Path(device_path).name}"
    local_path = str(pull_dir / local_name)
    case["pull_local_path"] = local_path
    case["pull_local_non_ascii"] = any(ord(ch) > 127 for ch in local_path)
    try:
        try:
            pull_media_file(serial=serial, package=package, device_path=device_path, local_path=local_path)
            case["measured_source"] = "public_or_processed"
        except Exception as pull_exc:
            case["pull_error"] = str(pull_exc)
            case["pull_error_class"] = _extract_error_class(str(pull_exc))
            logger.warning(
                "主拉取失败 class=%s path=%s err=%s",
                case["pull_error_class"],
                local_path,
                pull_exc,
            )
            # 增强/相册改名失败时，最终公共文件可能不存在。原始导入文件仍是有效证据，
            # 用它测量真实输出，而不是只报告 adb 找不到处理后的文件。
            if media != "video":
                raise
            raw_name = f"{orient_label}_视频_原始{Path(device_path).suffix or '.avi'}"
            raw_path = str(pull_dir / raw_name)
            pull_via_run_as(
                serial=serial,
                package=package,
                device_path=device_path,
                local_path=raw_path,
            )
            local_path = raw_path
            case["measured_source"] = "raw_import"
            case["public_pull_error"] = str(pull_exc)
        measured = measure_local_file(local_path)
        actual = (int(measured["width"]), int(measured["height"]))
        case["actual"] = actual
        case["local_file"] = local_path
        case["measure"] = measured
        # 方向是被测功能本身，不能接受宽高互换，否则横屏输出可误判为竖屏通过。
        case["passed"] = sizes_match(actual, expected, swap_ok=False)
        case["detail"] = _case_detail(case, expected, actual)
    except Exception as exc:
        err_text = str(exc)
        case["measure_error"] = err_text
        case["measure_error_class"] = _extract_error_class(err_text)
        logger.error(
            "测量失败 class=%s orientation=%s media=%s device_path=%s local=%s err=%s",
            case["measure_error_class"],
            orient_label,
            media,
            device_path,
            local_path,
            exc,
        )
        case["detail"] = _case_detail(case, expected, None, measurement_error=err_text)
    case["elapsed_seconds"] = round(time.time() - started, 2)
    return case


def _extract_error_class(message: str) -> str:
    text = message or ""
    match = re.search(r"\[([a-z0-9_]+)\]", text)
    if match:
        return match.group(1)
    lowered = text.lower()
    if "not debuggable" in lowered:
        return "package_not_debuggable"
    if "cannot create file/directory" in lowered and any(ord(ch) > 127 for ch in text):
        return "local_path_non_ascii"
    if "cannot create file/directory" in lowered:
        return "local_path_create_failed"
    if "failed to stat remote" in lowered or "公共相册未找到" in text:
        return "remote_missing"
    if "import stalled" in lowered or "status=stalled" in lowered:
        return "import_stalled"
    return "unknown"


def _video_process_evidence(log_path: str) -> Dict[str, str]:
    """Extract the App's own video-enhance failure from one import log."""
    if not log_path or not os.path.isfile(log_path):
        return {}
    try:
        text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    evidence: Dict[str, str] = {}
    patterns = {
        "start": r"BkAviEnhance start,\s*path=([^,\s]+),\s*bytes=(\d+),\s*mode=([^\s]+)",
        "auth": r"BkAviEnhance auth=([A-Za-z0-9_]+)",
        "error": r"error message\s*:\s*([^\r\n;]+)",
        "source": r"sourceVideoPath\s*=\s*([^\r\n;]+)",
        "target": r"targetVideoPath\s*=\s*([^;\r\n]*)",
    }
    for key, pattern in patterns.items():
        matches = list(re.finditer(pattern, text))
        if not matches:
            continue
        match = matches[-1]
        evidence[key] = " ".join(part.strip() for part in match.groups() if part and part.strip())
    mismatch = re.search(r"resolution_mismatch:(\d+)x(\d+)", evidence.get("error", ""))
    if mismatch:
        evidence["detected_size"] = f"{mismatch.group(1)}x{mismatch.group(2)}"
    return evidence


def _case_detail(
    case: Dict[str, Any],
    expected: Tuple[int, int],
    actual: Optional[Tuple[int, int]],
    measurement_error: str = "",
) -> str:
    evidence = case.get("video_process") or {}
    parts: List[str] = []
    if actual:
        outcome = "通过" if case.get("passed") else "失败"
        source = "原始导入文件" if case.get("measured_source") == "raw_import" else "最终文件"
        parts.append(f"期望 {_size_text(expected)}，实际 {_size_text(actual)}（{source}，{outcome}）")
    elif measurement_error:
        parts.append(f"最终文件测量失败：{measurement_error}")
    if evidence.get("detected_size"):
        parts.append(
            "视频格式/分辨率不符：App 视频增强检测到 "
            f"{evidence['detected_size']}，与期望 {_size_text(expected)} 不一致"
        )
    elif evidence.get("error"):
        parts.append(f"App 视频增强失败：{evidence['error']}")
    if evidence.get("detected_size") or evidence.get("error"):
        parts.append(
            "证据："
            f"原始文件 {evidence.get('start') or '-'}；"
            f"鉴权 {evidence.get('auth') or '-'}；"
            f"错误 {evidence.get('error') or '-'}；"
            f"源文件 {evidence.get('source') or '-'}；"
            f"增强输出 {evidence.get('target') or '（空，未生成）'}"
        )
    return "。".join(parts)


def _report_rel_href(target: str) -> str:
    text = str(target or "").strip()
    if not text:
        return ""
    abs_path = Path(os.path.abspath(text))
    root = app_config.ACTIVE_RUN_DIR
    if root:
        try:
            return Path(os.path.relpath(str(abs_path), root)).as_posix()
        except Exception:
            pass
    return abs_path.name


def _evidence_link(target: str, label: str) -> str:
    href = _report_rel_href(target)
    if not href:
        return ""
    return f'<a href="{escape(href, quote=True)}" target="_blank" rel="noopener">{escape(label)}</a>'


def _save_html(report: Dict[str, Any], path: str) -> str:
    rows = []
    for case in report.get("cases") or []:
        expected = case.get("expected")
        actual = case.get("actual")
        badge = (
            '<span style="color:#7a6a1f;font-weight:700">跳过</span>'
            if case.get("skipped")
            else (
                '<span style="color:#1f7a45;font-weight:700">通过</span>'
                if case.get("passed")
                else '<span style="color:#a43b2a;font-weight:700">失败</span>'
            )
        )
        evidence = " / ".join(
            link
            for link in (
                _evidence_link(str(case.get("local_file") or ""), "文件"),
                _evidence_link(str(case.get("import_log") or ""), "导入日志"),
            )
            if link
        ) or '<span class="muted">-</span>'
        rows.append(
            "<tr>"
            f"<td>{escape(str(case.get('orientation')))}</td>"
            f"<td>{escape(str(case.get('media')))}</td>"
            f"<td>{escape(_size_text(tuple(expected) if expected else None))}</td>"
            f"<td>{escape(_size_text(tuple(actual) if actual else None))}</td>"
            f"<td>{badge}</td>"
            f"<td>{escape(str(case.get('detail') or ''))}</td>"
            f"<td>{escape(str(case.get('measure_error_class') or case.get('pull_error_class') or '-'))}</td>"
            f"<td>{evidence}</td>"
            "</tr>"
        )
    cfg = report.get("device_config") or {}
    cam = cfg.get("camera") or {}
    expect = report.get("expectations") or {}
    chip_label = "BK" if expect.get("is_bk") else "全志"
    chip_source = str(expect.get("chip_source") or "table_default")
    pixel_source = str(expect.get("pixel_key_source") or "table_default")
    fatal = str(report.get("fatal_error") or "").strip()
    fatal_html = (
        f'<p style="color:#a43b2a;font-weight:700">前置失败：{escape(fatal)}</p>' if fatal else ""
    )
    try:
        from glasstest.report_bundle import summarize_conclusion

        summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
        run_status = "failed" if fatal or int(summary.get("failed") or 0) > 0 else "succeeded"
        conclusion_text = summarize_conclusion(report, "resolution", run_status)
    except Exception:
        conclusion_text = "失败" if fatal else "通过"
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>分辨率检查报告</title>
<style>
body{{font:14px/1.5 Segoe UI,PingFang SC,Microsoft YaHei,sans-serif;margin:24px;background:#f4f6f4;color:#1b1f18}}
.card,.panel{{background:#fff;border:1px solid #d9ddd3;border-radius:14px;padding:18px;margin-bottom:16px}}
table{{width:100%;border-collapse:collapse}} th,td{{padding:10px;border-bottom:1px solid #e6e9e1;text-align:left}}
.muted{{color:#687164}}
a{{color:#1f5f8a;text-decoration:none}} a:hover{{text-decoration:underline}}
.badge.good{{color:#1f7a45;background:#e8f5ec;padding:6px 12px;border-radius:999px;font-weight:700}}
.badge.bad{{color:#a43b2a;background:#fdecea;padding:6px 12px;border-radius:999px;font-weight:700}}
.badge.warn{{color:#9a6400;background:#fff4db;padding:6px 12px;border-radius:999px;font-weight:700}}
</style></head><body>
<div class="card">
<h1>分辨率检查报告</h1>
<p><strong>结论：{escape(conclusion_text)}</strong></p>
<p class="muted">日志和文件请点表格「证据」列链接查看，本页不再内嵌大段截图/日志。</p>
{fatal_html}
<p class="muted">设备 {escape(str(report.get('device_serial') or '-'))}
别名 {escape(str(report.get('device_label') or '-'))} ·
眼镜 {escape(str(report.get('glasses_label') or '-'))}</p>
<p>主控固件：{escape(str(report.get('mcu_firmware') or '-'))} →
软件版本名称：{escape(str(report.get('soft_version_name') or '-'))}</p>
<p>摄像头：value={escape(str(cam.get('value')))} pixels={escape(str(cam.get('pixels')))}
size={escape(str(cam.get('size')))} · video_type={escape(str(cfg.get('video_type') or '') or '（空）')}</p>
<p>芯片：{escape(chip_label)}（来源 {escape(chip_source)}）· 像素档：{escape(str(expect.get('pixel_key') or '-'))}（来源 {escape(pixel_source)}）{" · 照片特殊：800插值3200（只插值不做优化）" if expect.get("photo_interp") == "800_to_3200" else ""}</p>
<p>汇总：通过 {escape(str((report.get('summary') or {}).get('passed', 0)))} ·
失败 {escape(str((report.get('summary') or {}).get('failed', 0)))} ·
跳过 {escape(str((report.get('summary') or {}).get('skipped', 0)))}</p>
<p>开始 {escape(str(report.get('started_at') or '-'))} · 结束 {escape(str(report.get('finished_at') or '-'))}</p>
</div>
<div class="card">
<h2>用例结果</h2>
<table><thead><tr><th>方向</th><th>媒体</th><th>期望</th><th>实际</th><th>结果</th><th>说明</th><th>错误分类</th><th>证据</th></tr></thead>
<tbody>{''.join(rows) or '<tr><td colspan="8" class="muted">无用例</td></tr>'}</tbody></table>
</div>
</body></html>"""
    Path(path).write_text(html, encoding="utf-8")
    return path


def run_resolution_check(
    *,
    report_dir: str = "",
    run_slug: str = "",
    device_label: str = "",
    device_serial: str = "",
    glasses_label: str = "",
    orientations: Optional[Sequence[str]] = None,
    medias: Optional[Sequence[str]] = None,
    video_seconds: int = 5,
    biz_code: str = "ai_glasses",
    version_name_override: str = "",
    update_latest: bool = True,
) -> Dict[str, Any]:
    serial = (device_serial or app_config.PHONE_DEVICE_SERIAL or "").strip()
    if serial:
        app_config.apply_runtime_overrides(device_serial=serial)
    active_dir = report_dir.strip() if report_dir else os.path.join(RUN_LOG_DIR, "resolution")
    if report_dir.strip():
        app_config.use_run_output(active_dir)
    os.makedirs(active_dir, exist_ok=True)
    slug = (run_slug or "").strip() or _now_slug()
    debug_dir = app_config.RUN_LOG_DIR if app_config.ACTIVE_RUN_DIR else active_dir
    debug_name = "debug.log" if app_config.ACTIVE_RUN_DIR else f"runtime_debug_{slug}.log"
    debug_log = _attach_file_logger(debug_dir, slug, debug_name)
    pull_dir = Path(app_config.SCREENSHOT_DIR if app_config.ACTIVE_RUN_DIR else active_dir)
    if not app_config.ACTIVE_RUN_DIR:
        pull_dir = pull_dir / f"media_{slug}"
    pull_dir.mkdir(parents=True, exist_ok=True)
    abs_active = os.path.abspath(active_dir)
    abs_pull = os.path.abspath(str(pull_dir))
    logger.info("分辨率检查输出目录: %s", abs_active)
    logger.info("媒体拉取目录: %s", abs_pull)
    if any(ord(ch) > 127 for ch in abs_pull):
        logger.warning(
            "输出路径含非 ASCII 字符（常见于中文目录名）。Windows 上旧版 adb pull 直写会失败；"
            "当前版本会经 ASCII 临时目录中转。仍建议测试包放到纯英文路径，例如 D:\\GlassesTest。"
        )

    orients = [_normalize_orientation(o) for o in (orientations or ["landscape", "portrait"]) if o]
    media_list = [str(m).strip().lower() for m in (medias or ["photo", "video"]) if str(m).strip().lower() in {"photo", "video"}]
    if not orients:
        orients = ["landscape"]
    if not media_list:
        media_list = ["photo"]

    phone = PhoneOperations()
    serial = phone.ensure_device_connected(serial or None)
    phone.wake_screen()

    report: Dict[str, Any] = {
        "started_at": _now_text(),
        "device_serial": serial,
        "device_label": device_label,
        "glasses_label": glasses_label,
        "cases": [],
        "summary": {"total": 0, "passed": 0, "failed": 0, "skipped": 0},
        "runtime_debug_log_path": debug_log,
        "fatal_error": "",
    }
    fatal: Optional[BaseException] = None

    try:
        # 1) firmware → soft version
        if version_name_override.strip():
            soft = version_name_override.strip()
            mcu = ""
            logger.info("使用覆盖 version_name=%s", soft)
        else:
            fw = read_mcu_firmware_version(phone)
            mcu = str(fw.get("mcu_firmware") or "")
            soft = soft_version_from_mcu_firmware(mcu)
            report["firmware_info"] = fw
            if not soft:
                raise RuntimeError(f"无法从主控固件解析软件版本名称: {mcu!r}")
        report["mcu_firmware"] = mcu
        report["soft_version_name"] = soft

        # 2) API config
        config_data = fetch_device_config(soft, biz_code=biz_code)
        report["device_config"] = config_data
        report["expectations"] = build_expectation_summary(config_data)
        logger.info(
            "device-config ok: soft=%s camera=%s video_type=%s",
            soft,
            config_data.get("camera"),
            config_data.get("video_type"),
        )

        package = PHONE_APP_PACKAGE or "com.moyoung.glasses"

        # 3) cases
        for orient in orients:
            for media in media_list:
                if media == "photo":
                    expected = expected_photo_size(config_data, orient)
                else:
                    expected = expected_video_size(config_data, orient)
                if not expected:
                    report["cases"].append(
                        {
                            "orientation": orient,
                            "media": media,
                            "expected": None,
                            "passed": False,
                            "detail": "无法计算期望分辨率",
                        }
                    )
                    continue
                try:
                    case = run_one_case(
                        phone=phone,
                        serial=serial,
                        package=package,
                        orientation=orient,
                        media=media,
                        video_seconds=video_seconds,
                        expected=expected,
                        pull_dir=pull_dir,
                    )
                except Exception as exc:
                    logger.exception("用例失败")
                    case = {
                        "orientation": orient,
                        "media": media,
                        "expected": expected,
                        "actual": None,
                        "passed": False,
                        "detail": f"执行异常: {exc}",
                    }
                report["cases"].append(case)
    except BaseException as exc:
        fatal = exc
        report["fatal_error"] = str(exc)
        logger.error("分辨率检查前置/流程失败: %s", exc)
    finally:
        report["finished_at"] = _now_text()
        report["summary"]["total"] = len(report["cases"])
        report["summary"]["passed"] = sum(1 for c in report["cases"] if c.get("passed") is True)
        report["summary"]["skipped"] = sum(1 for c in report["cases"] if c.get("skipped"))
        case_failed = report["summary"]["total"] - report["summary"]["passed"] - report["summary"]["skipped"]
        if report.get("fatal_error"):
            report["summary"]["failed"] = max(case_failed, 1)
        else:
            report["summary"]["failed"] = case_failed

        if app_config.ACTIVE_RUN_DIR:
            json_path = os.path.join(app_config.RUN_LOG_DIR, "result.json")
            html_path = os.path.join(app_config.ACTIVE_RUN_DIR, "report.html")
        else:
            json_path = os.path.join(active_dir, f"resolution_check_{slug}.json")
            html_path = os.path.join(active_dir, f"resolution_check_{slug}.html")
        with open(json_path, "w", encoding="utf-8") as file_obj:
            json.dump(report, file_obj, ensure_ascii=False, indent=2)
        _save_html(report, html_path)
        if update_latest and not app_config.ACTIVE_RUN_DIR:
            latest = os.path.join(RUN_LOG_DIR, "complex_run_latest.html")
            os.makedirs(RUN_LOG_DIR, exist_ok=True)
            Path(latest).write_text(Path(html_path).read_text(encoding="utf-8"), encoding="utf-8")
            report["latest_html"] = latest
        report["json_report_path"] = json_path
        report["html_report_path"] = html_path
        logger.info(
            "分辨率检查结束: passed=%s failed=%s skipped=%s fatal=%s html=%s",
            report["summary"]["passed"],
            report["summary"]["failed"],
            report["summary"].get("skipped", 0),
            bool(report.get("fatal_error")),
            html_path,
        )

    if fatal is not None:
        raise RuntimeError(f"分辨率检查失败: {fatal}；详见 {report.get('html_report_path')}") from fatal
    if report["summary"]["failed"] > 0:
        raise RuntimeError(
            f"分辨率检查失败 {report['summary']['failed']} 项，详见 {report.get('html_report_path')}"
        )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="分辨率检查")
    parser.add_argument("--report-dir", default="")
    parser.add_argument("--run-slug", default="")
    parser.add_argument("--device-label", default="")
    parser.add_argument("--device-serial", default="")
    parser.add_argument("--glasses-label", default="")
    parser.add_argument("--orientations", default="landscape,portrait", help="landscape,portrait")
    parser.add_argument("--medias", default="photo,video", help="photo,video")
    parser.add_argument("--video-seconds", type=int, default=5)
    parser.add_argument("--biz-code", default="ai_glasses")
    parser.add_argument("--version-name", default="", help="覆盖自动读取的软件版本名称")
    parser.add_argument("--no-latest", action="store_true")
    args = parser.parse_args()
    orients = [x.strip() for x in args.orientations.split(",") if x.strip()]
    medias = [x.strip() for x in args.medias.split(",") if x.strip()]
    try:
        run_resolution_check(
            report_dir=args.report_dir,
            run_slug=args.run_slug,
            device_label=args.device_label,
            device_serial=args.device_serial,
            glasses_label=args.glasses_label,
            orientations=orients,
            medias=medias,
            video_seconds=args.video_seconds,
            biz_code=args.biz_code,
            version_name_override=args.version_name,
            update_latest=not args.no_latest,
        )
    except Exception as exc:
        logger.error("执行失败: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()

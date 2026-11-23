#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run complex multi-task cycles, import media, and generate concise summaries."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from html import escape
from pathlib import Path

import glasstest.config as app_config
from typing import Any, Dict, List, Optional, Tuple

from glasstest.config import (
    AUTOMATION_DEFAULT_IMPORT_TIMEOUT_SECONDS,
    AUTOMATION_IMPORT_RETRY_BACKOFF_SECONDS,
    AUTOMATION_IMPORT_RETRY_LIMIT,
    AUTOMATION_IMPORT_RETRY_RELAUNCH_APP,
    AUTOMATION_IMPORT_STALL_TIMEOUT_SECONDS,
    AUTOMATION_PROGRESS_POLL_INTERVAL_SECONDS,
    IMPORT_ERROR_LOG_FILE,
    PHONE_APP_PACKAGE,
    RUN_LOG_DIR,
)
from glasstest.media_measure import format_byte_size, list_recent_media_store_paths, probe_import_file_sizes
from glasstest.phone_operations import (
    ImportWaitResult,
    PhoneOperations,
    classify_import_paths,
    filter_import_paths_by_capture_window,
    logger as phone_operations_logger,
    summarize_import_paths,
)
from glasstest.test_flow import FlowConfig, logger as test_flow_logger, run_flow


LOG_FORMAT = "%(asctime)s | %(levelname)s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

logger = logging.getLogger("complex_flow")
logger.setLevel(logging.INFO)
logger.handlers.clear()
logger.propagate = False
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
_console_handler = logging.StreamHandler(sys.stdout)
_console_handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
logger.addHandler(_console_handler)

# Per-run overrides (multi-device suite writes into a dedicated folder).
_ACTIVE_REPORT_DIR: str = RUN_LOG_DIR
_UPDATE_LATEST: bool = True
_DEVICE_LABEL: str = ""


def _get_report_dir() -> str:
    return _ACTIVE_REPORT_DIR or RUN_LOG_DIR


class _NoiseFilter(logging.Filter):
    _NOISE_KEYWORDS = (
        "Run adb shell:",
        "Click point:",
        "Tap text:",
        "Tap resource-id:",
        "Tap first matching text:",
        "Connect device:",
        "Swipe:",
        "Broadcast output:",
        "Clicked import button by UI hierarchy",
        "Started import logcat monitor",
        "Stopped import logcat monitor",
        "执行报告已保存:",
        "任务报告:",
    )

    def filter(self, record: logging.LogRecord) -> bool:
        text = record.getMessage()
        return not any(keyword in text for keyword in self._NOISE_KEYWORDS)


def load_config(config_path: str) -> Dict[str, Any]:
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")
    with open(path, "r", encoding="utf-8") as file_obj:
        return json.load(file_obj)


def _now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _now_slug() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except Exception:
        return None


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except Exception:
        return None


def _append_jsonl(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as file_obj:
        file_obj.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _setup_runtime_debug_logging(run_slug: str) -> str:
    report_dir = app_config.RUN_LOG_DIR if app_config.ACTIVE_RUN_DIR else _get_report_dir()
    os.makedirs(report_dir, exist_ok=True)
    name = "debug.log" if app_config.ACTIVE_RUN_DIR else f"runtime_debug_{run_slug}.log"
    debug_path = os.path.join(report_dir, name)

    targets = [logger, phone_operations_logger, test_flow_logger]
    for target in targets:
        target.setLevel(logging.DEBUG)
        file_handler = logging.FileHandler(debug_path, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
        target.addHandler(file_handler)

        for handler in target.handlers:
            if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
                handler.addFilter(_NoiseFilter())

    return debug_path


def _save_complex_report(report: Dict[str, Any], run_slug: str) -> str:
    report_dir = app_config.RUN_LOG_DIR if app_config.ACTIVE_RUN_DIR else _get_report_dir()
    os.makedirs(report_dir, exist_ok=True)
    name = "result.json" if app_config.ACTIVE_RUN_DIR else f"complex_run_{run_slug}.json"
    path = os.path.join(report_dir, name)
    with open(path, "w", encoding="utf-8") as file_obj:
        json.dump(report, file_obj, ensure_ascii=False, indent=2)
    return path


def _build_expected_counts(tasks: List[Dict[str, Any]]) -> Dict[str, int]:
    photo = 0
    audio = 0
    video = 0
    for task in tasks:
        task_name = str(task.get("task", "")).strip().lower()
        times = max(0, _safe_int(task.get("times", 0)) or 0)
        if task_name == "photo":
            photo += times
        elif task_name == "audio":
            audio += times
        elif task_name == "video":
            video += times
    gallery = photo + video
    total = gallery + audio
    return {"photo": photo, "audio": audio, "video": video, "gallery": gallery, "total": total}


def _to_text(value: Any) -> str:
    if value is None:
        return "-"
    text = str(value).strip()
    return text if text else "-"


def _get_snapshot_section(snapshot: Dict[str, Any], category: str) -> Dict[str, Any]:
    section = snapshot.get(category, {})
    return section if isinstance(section, dict) else {}


def _build_post_import_validation(
    expected_counts: Dict[str, int],
    after_snapshot: Dict[str, Any],
    import_result: ImportWaitResult,
    path_filter: Optional[Dict[str, Any]] = None,
    check_gallery: bool = True,
    check_audio: bool = True,
    before_snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    before = before_snapshot if isinstance(before_snapshot, dict) else {}
    before_gallery = _get_snapshot_section(before, "gallery")
    before_audio = _get_snapshot_section(before, "audio")
    after_gallery = _get_snapshot_section(after_snapshot, "gallery")
    after_audio = _get_snapshot_section(after_snapshot, "audio")

    gallery_opened = bool(after_gallery.get("opened", False))
    audio_opened = bool(after_audio.get("opened", False))
    gallery_before = _safe_int(before_gallery.get("count"))
    audio_before = _safe_int(before_audio.get("count"))
    gallery_after = _safe_int(after_gallery.get("count"))
    audio_after = _safe_int(after_audio.get("count"))

    def _reliable_delta(before_sec: Dict[str, Any], after_sec: Dict[str, Any]) -> Tuple[Optional[int], str]:
        """Only trust before/after when count source matches and delta is not negative."""
        before_count = _safe_int(before_sec.get("count"))
        after_count = _safe_int(after_sec.get("count"))
        if before_count is None or after_count is None:
            return None, "unavailable"
        before_source = str(before_sec.get("source") or "").strip()
        after_source = str(after_sec.get("source") or "").strip()
        if before_source and after_source and before_source != after_source:
            # 前后计数口径不一致（例如共N个 vs 可见格子）→ 宁可不核，避免误杀。
            return None, "source_mismatch"
        delta = after_count - before_count
        if delta < 0:
            return None, "negative_delta"
        return delta, "page_delta"

    gallery_delta, gallery_delta_note = _reliable_delta(before_gallery, after_gallery)
    audio_delta, audio_delta_note = _reliable_delta(before_audio, after_audio)

    filter_info = path_filter if isinstance(path_filter, dict) else {}
    matched_paths = list(filter_info.get("matched_paths") or [])
    if matched_paths:
        validated_paths = matched_paths
        file_validation_source = "filtered_current_cycle_paths"
    elif import_result.current_cycle_success_paths:
        validated_paths = list(import_result.current_cycle_success_paths)
        file_validation_source = "current_cycle_success_paths"
    else:
        validated_paths = list(import_result.success_paths)
        file_validation_source = "raw_success_paths" if validated_paths else "unavailable"
    ignored_paths = list(filter_info.get("ignored_paths") or import_result.ignored_success_paths)
    unknown_time_paths = list(filter_info.get("unknown_time_paths") or import_result.unknown_time_success_paths)

    validated_summary = summarize_import_paths(validated_paths)
    ignored_summary = summarize_import_paths(ignored_paths)
    raw_summary = summarize_import_paths(import_result.success_paths)

    classified = (
        validated_summary.get("counts", {})
        if isinstance(validated_summary.get("counts"), dict)
        else classify_import_paths(validated_paths)
    )
    classified_gallery = _safe_int(classified.get("gallery"))
    classified_audio = _safe_int(classified.get("audio"))
    classified_total = _safe_int(classified.get("total"))

    observed = import_result.observed_transfer_counts or {}
    download_success_total = _safe_int(observed.get("download_success_count_max"))
    observed_total_count = _safe_int(observed.get("total_count_max"))
    raw_classified_total = (
        _safe_int(raw_summary.get("counts", {}).get("total"))
        if isinstance(raw_summary.get("counts"), dict)
        else None
    )
    raw_candidates = [
        download_success_total,
        observed_total_count,
        raw_classified_total,
    ]
    raw_transferred_total = max((value for value in raw_candidates if value is not None), default=None)

    paths_unavailable = (not validated_paths) or file_validation_source == "unavailable"
    media_count = _safe_int(getattr(import_result, "media_count", None))

    # 路径回补常缺录音：UI「已找到 N 个」/「x/N」比 MediaStore 路径更可信。
    ui_transfer_candidates = [
        value for value in (media_count, download_success_total, observed_total_count) if value is not None and value > 0
    ]
    ui_transfer_best = max(ui_transfer_candidates) if ui_transfer_candidates else None
    if ui_transfer_best is not None and (
        paths_unavailable
        or classified_total is None
        or classified_total < ui_transfer_best
    ):
        transferred_total = ui_transfer_best
        if media_count == ui_transfer_best:
            transfer_source = "import_media_count"
        elif download_success_total == ui_transfer_best:
            transfer_source = "download_success_count_max"
        else:
            transfer_source = "total_count_max"
    elif not paths_unavailable and classified_total is not None:
        transferred_total = classified_total
        transfer_source = file_validation_source
    elif download_success_total is not None:
        transferred_total = download_success_total
        transfer_source = "download_success_count_max"
    elif observed_total_count is not None:
        transferred_total = observed_total_count
        transfer_source = "total_count_max"
    else:
        transferred_total = raw_classified_total
        transfer_source = "raw_success_paths"

    gallery_expected = max(0, _safe_int(expected_counts.get("gallery")) or 0)
    audio_expected = max(0, _safe_int(expected_counts.get("audio")) or 0)
    total_expected = max(0, _safe_int(expected_counts.get("total")) or 0)

    import_phase = str(getattr(import_result, "phase", "") or "").strip().upper()
    ui_import_done = bool(getattr(import_result, "success", False)) or import_phase in (
        "SUCCESS",
        "IDLE_AFTER_SUCCESS",
    ) or bool(getattr(import_result, "progress_reached_100", False))

    # ---- 数量核验优先级 ----
    # 1) 导入前后页增量（最准，本轮新增）
    # 2) 导入 UI/log 的 media_count、download_success
    # 3) 路径分类（常漏录音，只作补充，不作唯一硬证据）
    if check_gallery:
        if gallery_delta is not None:
            gallery_actual = gallery_delta
            gallery_source = "page_delta"
            gallery_match = gallery_actual == gallery_expected
        elif not paths_unavailable and classified_gallery is not None and classified_gallery == gallery_expected:
            gallery_actual = classified_gallery
            gallery_source = file_validation_source
            gallery_match = True
        elif not paths_unavailable and classified_gallery is not None and classified_gallery > 0:
            gallery_actual = classified_gallery
            gallery_source = file_validation_source
            gallery_match = None  # 路径可能漏视频，不算已核实
        else:
            gallery_actual = None
            gallery_source = "unavailable"
            gallery_match = None
    else:
        gallery_actual = None
        gallery_source = "skipped"
        gallery_match = True

    if check_audio:
        if audio_delta is not None:
            audio_actual = audio_delta
            audio_source = "page_delta"
            audio_match = audio_actual == audio_expected
        elif not paths_unavailable and classified_audio is not None and classified_audio == audio_expected:
            audio_actual = classified_audio
            audio_source = file_validation_source
            audio_match = True
        elif not paths_unavailable and classified_audio is not None and classified_audio > 0:
            audio_actual = classified_audio
            audio_source = file_validation_source
            audio_match = None
        else:
            audio_actual = None
            audio_source = "unavailable"
            audio_match = None
    else:
        audio_actual = None
        audio_source = "skipped"
        audio_match = True

    # 传输总数：优先「相册增量+录音增量」；对齐时才认 UI media_count 为已核实。
    delta_total: Optional[int] = None
    if (not check_gallery or gallery_delta is not None) and (not check_audio or audio_delta is not None):
        delta_total = (gallery_delta or 0) + (audio_delta or 0)

    if delta_total is not None:
        transferred_total = delta_total
        transfer_source = "page_delta_total"
        transfer_match = transferred_total == total_expected
    elif media_count is not None and media_count == total_expected:
        transferred_total = media_count
        transfer_source = "import_media_count"
        transfer_match = True
    elif download_success_total is not None and download_success_total == total_expected:
        transferred_total = download_success_total
        transfer_source = "download_success_count_max"
        transfer_match = True
    elif transferred_total is not None:
        # 有 UI 个数但对不上：无增量时不可靠（录音可能不计入），标未核实。
        transfer_match = None
    else:
        transfer_match = None

    total_validated: Optional[int] = None
    if (not check_gallery or gallery_match is True) and (not check_audio or audio_match is True):
        total_validated = (gallery_actual or 0) + (audio_actual or 0)
    total_match = None if total_validated is None else (total_validated == total_expected)

    # verified = 有可靠计数且已比过；unverified = 没可靠计数；mismatch = 可靠计数对不上
    count_verified = (
        (not check_gallery or gallery_match is not None)
        and (not check_audio or audio_match is not None)
        and transfer_match is not None
    )

    status = "ok"
    success = True
    reason_detail = ""
    failed_parts: List[str] = []
    unavailable_parts: List[str] = []

    if check_gallery and not gallery_opened:
        unavailable_parts.append("gallery page not opened")
    if check_audio and not audio_opened:
        unavailable_parts.append("audio page not opened")

    if check_gallery and gallery_match is False:
        failed_parts.append(f"gallery expected={gallery_expected}, actual={gallery_actual}")
    elif check_gallery and gallery_match is None:
        unavailable_parts.append("gallery count unverified")

    if check_audio and audio_match is False:
        failed_parts.append(f"audio expected={audio_expected}, actual={audio_actual}")
    elif check_audio and audio_match is None:
        unavailable_parts.append("audio count unverified")

    if transfer_match is False:
        failed_parts.append(f"transferred expected={total_expected}, actual={transferred_total}")
    elif transfer_match is None:
        unavailable_parts.append("transferred total unverified")

    # 防误杀：导入 UI 已成功，且另有对齐的成功计数信号时，增量对不上也不硬判失败。
    success_count_signal = (
        (media_count is not None and media_count == total_expected)
        or (download_success_total is not None and download_success_total == total_expected)
        or (observed_total_count is not None and observed_total_count == total_expected)
    )
    if failed_parts and ui_import_done and success_count_signal:
        unavailable_parts.extend(failed_parts)
        failed_parts = []

    if failed_parts:
        # 可靠增量明确对不上，且没有冲突的成功计数 → 真失败
        status = "validation_failed"
        success = False
        reason_detail = "; ".join(failed_parts)
    elif unavailable_parts:
        # 导入 UI 成功但数量核不准 → 通过，明确写「数量未核实」
        if ui_import_done:
            status = "count_unverified"
            success = True
            tips: List[str] = []
            if gallery_before is not None and gallery_after is not None:
                tips.append(f"相册 {gallery_before}->{gallery_after}")
            elif gallery_after is not None:
                tips.append(f"相册可见 {gallery_after}")
            if audio_before is not None and audio_after is not None:
                tips.append(f"录音 {audio_before}->{audio_after}")
            elif audio_after is not None:
                tips.append(f"录音可见 {audio_after}")
            if media_count is not None:
                tips.append(f"导入卡显示 {media_count}")
            reason_detail = (
                "导入已完成，但本轮数量未能准确核实（" + "，".join(tips) + "）"
                if tips
                else "导入已完成，但本轮数量未能准确核实"
            )
        else:
            status = "validation_unavailable"
            success = False
            reason_detail = "; ".join(unavailable_parts)
    elif count_verified:
        status = "ok"
        success = True
        reason_detail = "数量已核实"

    artifacts: Dict[str, str] = {}
    for key in ("gallery_screenshot", "audio_screenshot", "gallery_hierarchy", "audio_hierarchy"):
        value = str(after_snapshot.get("artifacts", {}).get(key, "") or "")
        if value:
            artifacts[key] = value

    size_map = filter_info.get("file_sizes") if isinstance(filter_info.get("file_sizes"), dict) else {}
    matched_with_sizes = _names_by_type_with_sizes(
        validated_summary.get("names_by_type", {}),
        size_map,
    )
    ignored_with_sizes = _names_by_type_with_sizes(
        ignored_summary.get("names_by_type", {}),
        size_map,
    )
    total_bytes = _sum_size_bytes(size_map, validated_paths)

    gallery_display = gallery_actual if gallery_actual is not None else gallery_after
    audio_display = audio_actual if audio_actual is not None else audio_after

    return {
        "success": success,
        "status": status,
        "reason_detail": reason_detail,
        "count_verified": bool(count_verified and success and not failed_parts),
        "gallery": {
            "expected": gallery_expected,
            "before": gallery_before,
            "after": gallery_after,
            "delta": gallery_display,
            "source": gallery_source,
            "matched": gallery_match,
            "checked": bool(check_gallery),
            "page_opened": gallery_opened,
        },
        "audio": {
            "expected": audio_expected,
            "before": audio_before,
            "after": audio_after,
            "delta": audio_display,
            "source": audio_source,
            "matched": audio_match,
            "checked": bool(check_audio),
            "page_opened": audio_opened,
        },
        "total": {
            "expected": total_expected,
            "transferred": transferred_total,
            "validated": total_validated,
            "matched": total_match,
            "transfer_matched": transfer_match,
            "source": transfer_source,
            "raw_observed_total": raw_transferred_total,
            "download_success_total": download_success_total,
            "observed_total_count": observed_total_count,
            "page_delta_total": delta_total,
        },
        "files": {
            "source": file_validation_source,
            "matched_total": len(validated_paths),
            "ignored_total": len(ignored_paths),
            "unknown_time_total": len(unknown_time_paths),
            "matched_names_by_type": validated_summary.get("names_by_type", {}),
            "ignored_names_by_type": ignored_summary.get("names_by_type", {}),
            "matched_files_by_type": matched_with_sizes,
            "ignored_files_by_type": ignored_with_sizes,
            "file_sizes": size_map,
            "total_size_bytes": total_bytes,
            "total_size_text": format_byte_size(total_bytes),
            "window": {
                "mode": str(filter_info.get("mode", "") or ""),
                "started_at": str(filter_info.get("started_at", "") or ""),
                "finished_at": str(filter_info.get("finished_at", "") or ""),
                "window_start": str(filter_info.get("window_start", "") or ""),
                "window_end": str(filter_info.get("window_end", "") or ""),
            },
        },
        "artifacts": artifacts,
        "after_snapshot": after_snapshot,
    }


def _names_by_type_with_sizes(
    names_by_type: Any,
    size_map: Dict[str, Any],
) -> Dict[str, List[Dict[str, Any]]]:
    result: Dict[str, List[Dict[str, Any]]] = {}
    if not isinstance(names_by_type, dict):
        return result
    for media_type, names in names_by_type.items():
        rows: List[Dict[str, Any]] = []
        if not isinstance(names, list):
            result[str(media_type)] = rows
            continue
        for name in names:
            name_text = str(name or "").strip()
            if not name_text:
                continue
            info = size_map.get(name_text) if isinstance(size_map.get(name_text), dict) else {}
            size_bytes = info.get("size_bytes") if isinstance(info, dict) else None
            size_text = str(info.get("size_text") or "").strip() if isinstance(info, dict) else ""
            if not size_text and size_bytes is not None:
                size_text = format_byte_size(size_bytes)
            label = f"{name_text} ({size_text})" if size_text else name_text
            rows.append(
                {
                    "name": name_text,
                    "size_bytes": size_bytes,
                    "size_text": size_text,
                    "label": label,
                }
            )
        result[str(media_type)] = rows
    return result


def _sum_size_bytes(size_map: Dict[str, Any], paths: List[str]) -> Optional[int]:
    total = 0
    found = 0
    seen = set()
    for path_text in paths:
        name = Path(str(path_text or "")).name
        if not name or name in seen:
            continue
        seen.add(name)
        info = size_map.get(name) if isinstance(size_map.get(name), dict) else {}
        size_bytes = info.get("size_bytes") if isinstance(info, dict) else None
        if isinstance(size_bytes, int) and size_bytes >= 0:
            total += size_bytes
            found += 1
    return total if found else None


def _build_cycle_summary(
    cycle_index: int,
    expected_counts: Dict[str, int],
    import_result: ImportWaitResult,
    attempt_used: int,
) -> Dict[str, Any]:
    validation = import_result.post_import_validation or {}
    gallery = validation.get("gallery", {}) if isinstance(validation.get("gallery"), dict) else {}
    audio = validation.get("audio", {}) if isinstance(validation.get("audio"), dict) else {}
    total = validation.get("total", {}) if isinstance(validation.get("total"), dict) else {}
    files = validation.get("files", {}) if isinstance(validation.get("files"), dict) else {}
    if not files:
        current_summary = summarize_import_paths(import_result.current_cycle_success_paths)
        ignored_summary = summarize_import_paths(import_result.ignored_success_paths)
        size_map = (
            import_result.success_path_filter.get("file_sizes")
            if isinstance(import_result.success_path_filter, dict)
            and isinstance(import_result.success_path_filter.get("file_sizes"), dict)
            else {}
        )
        total_size_bytes = _sum_size_bytes(size_map, list(import_result.current_cycle_success_paths))
        files = {
            "matched_total": len(import_result.current_cycle_success_paths),
            "ignored_total": len(import_result.ignored_success_paths),
            "matched_names_by_type": current_summary.get("names_by_type", {}),
            "ignored_names_by_type": ignored_summary.get("names_by_type", {}),
            "matched_files_by_type": _names_by_type_with_sizes(
                current_summary.get("names_by_type", {}), size_map
            ),
            "ignored_files_by_type": _names_by_type_with_sizes(
                ignored_summary.get("names_by_type", {}), size_map
            ),
            "file_sizes": size_map,
            "total_size_bytes": total_size_bytes,
            "total_size_text": format_byte_size(total_size_bytes),
        }
    transfer_success = bool(validation.get("transfer_success", import_result.success))
    validation_status = str(validation.get("status", "not_run"))
    validation_success = bool(validation.get("success", False))

    observed = import_result.observed_transfer_counts or {}
    transferred_total = _safe_int(total.get("transferred"))
    if transferred_total is None:
        transferred_candidates = [
            _safe_int(observed.get("download_success_count_max")),
            _safe_int(observed.get("total_count_max")),
        ]
        transferred_total = max((value for value in transferred_candidates if value is not None), default=None)

    summary_artifacts = import_result.summary_artifacts or {}
    artifact_paths = import_result.artifact_paths or {}
    screenshot_path = (
        str(artifact_paths.get("screenshot", "") or "")
        or str(summary_artifacts.get("gallery_screenshot", "") or "")
        or str(summary_artifacts.get("audio_screenshot", "") or "")
    )
    problem_detail = str(validation.get("reason_detail", "") or "").strip()
    if not problem_detail:
        problem_detail = str(import_result.failure_reason or "").strip()
    overall_success = bool(import_result.success)
    # 数量未核实时保留备注；其它「证据不全」类成功轮次不刷屏。
    if overall_success and validation_status == "validation_unavailable":
        if "未解析到文件路径" not in problem_detail and "未能准确核实" not in problem_detail:
            problem_detail = ""

    return {
        "cycle": cycle_index,
        "import_success": overall_success,
        "overall_success": overall_success,
        "transfer_success": transfer_success,
        "status": import_result.status,
        "failure_reason": import_result.failure_reason,
        "problem_detail": problem_detail,
        "attempt_used": attempt_used,
        "expected": dict(expected_counts),
        "waiting_wifi_seconds": import_result.waiting_wifi_seconds,
        "transfer_duration_seconds": import_result.transfer_duration_seconds,
        "transferred_total": transferred_total,
        "validation_status": validation_status,
        "validation_success": validation_success,
        "gallery_delta": gallery.get("delta"),
        "audio_delta": audio.get("delta"),
        "current_cycle_file_count": files.get("matched_total"),
        "ignored_file_count": files.get("ignored_total"),
        "current_cycle_files": files.get("matched_names_by_type"),
        "ignored_files": files.get("ignored_names_by_type"),
        "current_cycle_files_detail": files.get("matched_files_by_type"),
        "ignored_files_detail": files.get("ignored_files_by_type"),
        "file_sizes": files.get("file_sizes") or {},
        "total_size_bytes": files.get("total_size_bytes"),
        "total_size_text": files.get("total_size_text") or "",
        "logcat_path": str(artifact_paths.get("logcat", "") or ""),
        "screenshot_path": screenshot_path,
        "result_json_path": str(artifact_paths.get("result", "") or ""),
    }


def _format_seconds(value: Any) -> str:
    parsed = _safe_float(value)
    return "-" if parsed is None else f"{parsed:.2f}"


def _format_expected_text(expected: Dict[str, Any]) -> str:
    return "{photo}/{audio}/{video}/{total}".format(
        photo=_to_text(expected.get("photo")),
        audio=_to_text(expected.get("audio")),
        video=_to_text(expected.get("video")),
        total=_to_text(expected.get("total")),
    )


def _render_file_group(names: Any, label: str, max_items: int = 8) -> str:
    if not isinstance(names, list):
        return ""
    values: List[str] = []
    for item in names:
        if isinstance(item, dict):
            label_text = str(item.get("label") or item.get("name") or "").strip()
        else:
            label_text = str(item or "").strip()
        if label_text:
            values.append(label_text)
    if not values:
        return ""
    shown = values[:max_items]
    remaining = len(values) - len(shown)
    suffix = f" ... (+{remaining})" if remaining > 0 else ""
    return f"<div><strong>{escape(label)}({len(values)}):</strong> {escape(', '.join(shown) + suffix)}</div>"


def _render_file_details(item: Dict[str, Any]) -> str:
    current_detail = (
        item.get("current_cycle_files_detail", {})
        if isinstance(item.get("current_cycle_files_detail"), dict)
        else {}
    )
    ignored_detail = (
        item.get("ignored_files_detail", {})
        if isinstance(item.get("ignored_files_detail"), dict)
        else {}
    )
    current = current_detail or (
        item.get("current_cycle_files", {}) if isinstance(item.get("current_cycle_files"), dict) else {}
    )
    ignored = ignored_detail or (
        item.get("ignored_files", {}) if isinstance(item.get("ignored_files"), dict) else {}
    )

    blocks: List[str] = []
    for key, label in (("photo", "P"), ("audio", "A"), ("video", "V")):
        rendered = _render_file_group(current.get(key), label)
        if rendered:
            blocks.append(rendered)

    ignored_blocks: List[str] = []
    for key, label in (("photo", "P"), ("audio", "A"), ("video", "V"), ("unknown", "U")):
        rendered = _render_file_group(ignored.get(key), label)
        if rendered:
            ignored_blocks.append(rendered)

    if not blocks and not ignored_blocks:
        transferred = item.get("transferred_total")
        note = str(item.get("problem_detail") or "").strip()
        if transferred not in (None, "", "-"):
            tip = f"导入约 {escape(_to_text(transferred))} 个（未解析到具体文件名）"
            if note and "未解析到文件路径" in note:
                tip = escape(note)
            return f'<span class="muted">{tip}</span>'
        return '<span class="muted">-</span>'

    body = "".join(blocks)
    if ignored_blocks:
        body += '<div class="muted small-gap">已忽略的历史文件：</div>' + "".join(ignored_blocks)
    size_text = str(item.get("total_size_text") or "").strip()
    size_suffix = f"，共 {size_text}" if size_text else ""
    return (
        "<details>"
        f"<summary>本轮文件 {_to_text(item.get('current_cycle_file_count'))} 个，"
        f"已忽略 {_to_text(item.get('ignored_file_count'))} 个{escape(size_suffix)}</summary>"
        f"{body}"
        "</details>"
    )


def _artifact_web_href(target: str) -> str:
    """Build a portable href relative to the run folder (日志/…, 截图/…).

    Never embed machine-absolute paths. Web serves 报告.html under
    /artifacts/<run>/ so these relatives resolve after unpacking anywhere.
    """
    text = str(target or "").strip()
    if not text:
        return ""
    abs_path = Path(os.path.abspath(text))
    run_root = (app_config.ACTIVE_RUN_DIR or "").strip()
    if run_root:
        try:
            return Path(os.path.relpath(str(abs_path), os.path.abspath(run_root))).as_posix()
        except Exception:
            pass
    try:
        output_root = Path(os.path.abspath("output"))
        relative = abs_path.relative_to(output_root)
    except ValueError:
        # Outside output/: best-effort relative to current log dir for local open.
        try:
            return Path(os.path.relpath(str(abs_path), os.path.abspath(RUN_LOG_DIR))).as_posix()
        except Exception:
            return Path(abs_path.name).as_posix()
    parts = relative.parts
    # output/<时间_类型>/日志/x.log → 日志/x.log (portable with 报告.html)
    if len(parts) >= 2:
        return Path(*parts[1:]).as_posix()
    return relative.as_posix()


def _html_link(target: str, label: str, from_dir: str = "") -> str:
    del from_dir  # kept for call-site compatibility; hrefs are run-relative
    href = _artifact_web_href(target)
    if not href:
        return ""  # 缺证据不占位，避免「日志 / - / -」把列撑乱
    return f'<a href="{escape(href, quote=True)}" target="_blank" rel="noopener">{escape(label)}</a>'


def _html_badge(text: str, tone: str) -> str:
    return f'<span class="badge {escape(tone)}">{escape(text)}</span>'



def _humanize_status(status: Any) -> str:
    text = str(status or "").strip()
    mapping = {
        "success": "成功",
        "ok": "成功",
        "failed": "失败",
        "timeout": "超时",
        "stalled": "进度卡住",
        "battery_low": "低电量",
        "storage_full": "存储已满",
        "validation_failed": "校验失败",
        "validation_unavailable": "校验缺失",
        "count_unverified": "数量未核实",
        "not_run": "未校验",
        "click_failed": "未点到导入",
        "running": "进行中",
    }
    return mapping.get(text, text or "-")


def _humanize_reason(detail: Any) -> str:
    text = str(detail or "").strip()
    if not text:
        return "-"

    if ";" in text:
        parts = [part.strip() for part in text.split(";") if part.strip()]
        if len(parts) > 1:
            return "；".join(_humanize_reason(part) for part in parts)

    lowered = text.lower()
    direct_map = {
        "audio page not opened": "导入后没有成功打开录音页，无法完成录音数量校验",
        "gallery page not opened": "导入后没有成功打开相册页，无法完成相册数量校验",
        "audio count unavailable": "录音页未读到本轮精确数量",
        "gallery count unavailable": "相册页未读到本轮精确数量",
        "audio count unverified": "录音数量未能核实",
        "gallery count unverified": "相册数量未能核实",
        "transferred total unavailable": "传输总数无法获取",
        "transferred total unverified": "传输总数未能核实",
        "battery_low": "眼镜电量过低",
        "storage_full": "手机存储空间不足",
        "timeout": "导入超时",
        "stalled": "导入进度长时间无变化",
        "download_failed": "文件传输失败",
        "wifi_channel_closed": "传输过程中 Wi-Fi 通道被关闭",
        "validation_failed": "导入完成，但校验失败",
        "validation_unavailable": "导入完成，文件路径证据不完整",
        "count_unverified": "导入完成，但数量未能准确核实",
    }
    if text in direct_map:
        return direct_map[text]
    if "battery_low" in lowered:
        return "眼镜电量过低"
    if "storage_full" in lowered:
        return "手机存储空间不足"
    if "wifi channel" in lowered or "wifi_channel_closed" in lowered:
        return "传输过程中 Wi-Fi 通道被关闭"
    if "download_failed" in lowered:
        return "文件传输失败"
    if "import_failed" in lowered:
        return "导入失败"

    match = re.match(r"^(gallery|audio|transferred)\s+expected=(\d+),\s*actual=([\w-]+)$", lowered)
    if match:
        category, expected, actual = match.groups()
        label_map = {
            "gallery": "相册数量",
            "audio": "录音数量",
            "transferred": "传输文件数",
        }
        return f"{label_map.get(category, category)}不一致（期望 {expected}，实际 {actual}）"

    return text


def _cycle_result_badge(
    overall_success: bool,
    transfer_success: bool,
    validation_status: str,
    validation_success: bool,
) -> str:
    if overall_success and validation_status == "count_unverified":
        return _html_badge("成功·数量未核实", "warn")
    if overall_success:
        return _html_badge("成功", "good")
    if transfer_success and validation_status == "validation_unavailable":
        return _html_badge("校验缺失", "warn")
    if transfer_success and not validation_success:
        return _html_badge("校验失败", "bad")
    return _html_badge("导入失败", "bad")


def _join_html_links(items: List[str]) -> str:
    filtered = [item for item in items if item]
    return " / ".join(filtered) if filtered else '<span class="muted">-</span>'


def _save_html_summary(report: Dict[str, Any], run_slug: str) -> Tuple[str, str]:
    report_dir = _get_report_dir()
    if app_config.ACTIVE_RUN_DIR:
        path = os.path.join(app_config.ACTIVE_RUN_DIR, "report.html")
        latest_path = path
    else:
        path = os.path.join(report_dir, f"complex_run_{run_slug}_summary.html")
        latest_path = os.path.join(report_dir, "complex_run_latest.html")
    device_title = str(report.get("device_label") or _DEVICE_LABEL or "").strip()
    title_suffix = f" · {device_title}" if device_title else ""
    problems = report.get("problems", []) if isinstance(report.get("problems"), list) else []
    cycles_data = report.get("cycles_data", []) if isinstance(report.get("cycles_data"), list) else []
    summary = report.get("summary", {}) if isinstance(report.get("summary"), dict) else {}
    planned_cycles = int(summary.get("cycles_total") or report.get("cycles") or len(cycles_data) or 0)
    finished_cycles = len(cycles_data)

    cycle_rows: List[str] = []
    problem_entries: List[Dict[str, Any]] = []
    for cycle in cycles_data:
        item = cycle.get("cycle_summary", {}) if isinstance(cycle.get("cycle_summary"), dict) else {}
        import_data = cycle.get("import", {}) if isinstance(cycle.get("import"), dict) else {}
        import_validation = (
            import_data.get("post_import_validation", {})
            if isinstance(import_data.get("post_import_validation"), dict)
            else {}
        )
        expected = item.get("expected", {}) if isinstance(item.get("expected"), dict) else {}
        validation_status = str(item.get("validation_status", "not_run"))
        transfer_success_raw = item.get("transfer_success")
        if transfer_success_raw is None:
            transfer_success_raw = import_validation.get("transfer_success")
        if transfer_success_raw is None and validation_status not in ("not_run", ""):
            transfer_success_raw = True
        if transfer_success_raw is None:
            transfer_success_raw = import_data.get("success", False)
        transfer_success = bool(transfer_success_raw)
        validation_success = bool(item.get("validation_success", False))
        overall_success = bool(item.get("overall_success", item.get("import_success", False)))
        detail = str(item.get("problem_detail", "") or item.get("failure_reason", "") or "").strip()
        if not detail:
            signals = import_data.get("error_signals", []) if isinstance(import_data.get("error_signals"), list) else []
            if signals:
                detail = str(signals[0]).strip()
        detail_text = _humanize_reason(detail)
        artifact_paths = import_data.get("artifact_paths", {}) if isinstance(import_data.get("artifact_paths"), dict) else {}
        summary_artifacts = (
            import_data.get("summary_artifacts", {})
            if isinstance(import_data.get("summary_artifacts"), dict)
            else {}
        )

        links = _join_html_links(
            [
                _html_link(str(item.get("logcat_path", "") or artifact_paths.get("logcat", "") or ""), "日志", report_dir),
                _html_link(str(summary_artifacts.get("gallery_screenshot", "") or ""), "相册截图", report_dir),
                _html_link(str(summary_artifacts.get("audio_screenshot", "") or ""), "录音截图", report_dir),
                _html_link(str(item.get("result_json_path", "") or artifact_paths.get("result", "") or ""), "结果文件", report_dir),
            ]
        )

        cycle_rows.append(
            "<tr>"
            f"<td>{escape(_to_text(item.get('cycle')))}</td>"
            f"<td>{escape(_format_expected_text(expected))}</td>"
            f"<td>{_cycle_result_badge(overall_success, transfer_success, validation_status, validation_success)}</td>"
            f"<td>{escape(_format_seconds(item.get('waiting_wifi_seconds')))}</td>"
            f"<td>{escape(_format_seconds(item.get('transfer_duration_seconds')))}</td>"
            f"<td>{escape(_to_text(item.get('transferred_total')))}</td>"
            f"<td>{escape(str(item.get('total_size_text') or '-'))}</td>"
            f"<td>{_render_file_details(item)}</td>"
            f"<td>{escape(_to_text(item.get('gallery_delta')))}</td>"
            f"<td>{escape(_to_text(item.get('audio_delta')))}</td>"
            f"<td class=\"detail\">{escape(detail_text if detail_text != '-' else '正常')}</td>"
            f"<td>{links}</td>"
            "</tr>"
        )
        if not overall_success:
            problem_entries.append(
                {
                    "cycle": item.get("cycle"),
                    "status": validation_status if transfer_success and not validation_success else item.get("status"),
                    "failure_reason": detail_text,
                    "logcat_path": item.get("logcat_path", ""),
                    "screenshot_path": item.get("screenshot_path", ""),
                    "result_json_path": item.get("result_json_path", ""),
                }
            )

    if not problem_entries and problems:
        problem_entries = problems
    problem_count = len(problem_entries)

    problem_rows: List[str] = []
    for item in problem_entries:
        review_links = _join_html_links(
            [
                _html_link(str(item.get("logcat_path", "") or ""), "日志", report_dir),
                _html_link(str(item.get("screenshot_path", "") or ""), "错误截图", report_dir),
                _html_link(str(item.get("result_json_path", "") or ""), "结果文件", report_dir),
            ]
        )
        problem_rows.append(
            "<tr>"
            f"<td>{escape(_to_text(item.get('cycle')))}</td>"
            f"<td>{escape(_humanize_status(item.get('status')))}</td>"
            f"<td class=\"detail\">{escape(_to_text(item.get('failure_reason')))}</td>"
            f"<td>{review_links}</td>"
            "</tr>"
        )

    problems_html = (
        "<table><thead><tr><th>轮次</th><th>结果</th><th>问题说明</th><th>证据</th></tr></thead><tbody>"
        + "".join(problem_rows)
        + "</tbody></table>"
        if problem_rows
        else '<div class="ok-box">本次没有发现异常轮次。正常情况下只看这一份网页报告就够了，不需要逐个翻原始日志。</div>'
    )

    try:
        from glasstest.report_bundle import summarize_conclusion

        run_status = "failed" if problem_count else "succeeded"
        conclusion_text = summarize_conclusion(report, "import", run_status)
    except Exception:
        conclusion_text = "失败" if problem_count else "通过"

    html_text = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>复杂流程摘要报告{escape(title_suffix)} {escape(run_slug)}</title>
  <style>
    :root {{
      --bg: #f4f6f4;
      --card: #ffffff;
      --ink: #1b1f18;
      --muted: #687164;
      --line: #d9ddd3;
      --good: #1f7a45;
      --good-bg: #e8f5ec;
      --bad: #a43b2a;
      --bad-bg: #fdecea;
      --warn: #9a6400;
      --warn-bg: #fff4db;
      --neutral: #5d6a78;
      --neutral-bg: #eef2f6;
      --accent: #174a7c;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font: 14px/1.5 "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      color: var(--ink);
      background: linear-gradient(180deg, #edf3ec 0%, var(--bg) 260px);
    }}
    .wrap {{ max-width: 1320px; margin: 0 auto; padding: 24px; }}
    .hero, .panel {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 18px;
      box-shadow: 0 10px 30px rgba(27, 31, 24, 0.05);
    }}
    .hero {{ padding: 24px; margin-bottom: 18px; }}
    h1 {{ margin: 0 0 8px; font-size: 28px; }}
    h2 {{ margin: 0 0 14px; font-size: 18px; }}
    p {{ margin: 6px 0; }}
    .muted {{ color: var(--muted); }}
    .hero-note {{
      margin-top: 14px;
      padding: 12px 14px;
      border-radius: 14px;
      background: #f7faf6;
      border: 1px solid var(--line);
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 12px;
      margin-top: 18px;
    }}
    .stat {{
      padding: 14px 16px;
      border-radius: 14px;
      background: #f8faf7;
      border: 1px solid var(--line);
    }}
    .stat .label {{ color: var(--muted); font-size: 12px; }}
    .stat .value {{ font-size: 24px; font-weight: 700; margin-top: 4px; }}
    .panel {{ padding: 18px; margin-bottom: 18px; }}
    .ok-box {{
      padding: 14px 16px;
      background: var(--good-bg);
      color: var(--good);
      border-radius: 14px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: #fff;
      overflow: hidden;
      border-radius: 14px;
    }}
    th, td {{
      padding: 11px 10px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
    }}
    th {{
      font-size: 12px;
      color: var(--muted);
      background: #f8faf7;
      position: sticky;
      top: 0;
    }}
    tr:last-child td {{ border-bottom: none; }}
    tbody tr:hover {{ background: #fbfcfa; }}
    .badge {{
      display: inline-block;
      padding: 4px 10px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 700;
      white-space: nowrap;
    }}
    .badge.good {{ color: var(--good); background: var(--good-bg); }}
    .badge.bad {{ color: var(--bad); background: var(--bad-bg); }}
    .badge.warn {{ color: var(--warn); background: var(--warn-bg); }}
    .badge.neutral {{ color: var(--neutral); background: var(--neutral-bg); }}
    .detail {{ min-width: 220px; }}
    details {{ min-width: 240px; }}
    details summary {{ cursor: pointer; color: var(--accent); }}
    .small-gap {{ margin-top: 6px; }}
    a {{ color: #0d5fb6; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .foot {{ color: var(--muted); font-size: 12px; margin-top: 10px; }}
    .section-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 14px;
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>导入测试报告{escape(title_suffix)}</h1>
      <p class="muted">先看「全部轮次」。日志和截图点证据列链接查看，不在本页内嵌大段内容。</p>
      <div class="hero-note">
        <div><strong>结论：{escape(conclusion_text)}</strong></div>
        <div>设备：{escape(device_title or _to_text(report.get("device_serial") or "-"))}</div>
        <div>计划轮回：{escape(str(planned_cycles))}（完成 {escape(str(finished_cycles))} 轮）</div>
        <div>开始时间：{escape(_to_text(report.get("started_at")))}</div>
        <div>结束时间：{escape(_to_text(report.get("finished_at") or "-"))}</div>
      </div>
      <div class="grid">
        <div class="stat"><div class="label">计划轮回</div><div class="value">{escape(str(planned_cycles))}</div></div>
        <div class="stat"><div class="label">已完成轮回</div><div class="value">{escape(str(finished_cycles))}</div></div>
        <div class="stat"><div class="label">成功轮次</div><div class="value">{escape(_to_text(summary.get("cycles_import_success", 0)))}</div></div>
        <div class="stat"><div class="label">异常轮次</div><div class="value">{escape(str(problem_count))}</div></div>
      </div>
    </section>

    <section class="panel">
      <div class="section-head">
        <h2>全部轮次</h2>
      </div>
      <table>
        <thead>
          <tr>
            <th>轮次</th>
            <th>期望数量 p/a/v/t</th>
            <th>结果</th>
            <th>等待 Wi-Fi（秒）</th>
            <th>传输耗时（秒）</th>
            <th>传输数量</th>
            <th>文件大小</th>
            <th>文件名</th>
            <th>相册数量</th>
            <th>录音数量</th>
            <th>说明</th>
            <th>证据</th>
          </tr>
        </thead>
        <tbody>
          {"".join(cycle_rows)}
        </tbody>
      </table>
      <div class="foot">p/a/v/t = 拍照/录音/视频/总数。点「证据」里的日志/截图链接查看详情。若本轮未解析到文件路径，文件名/大小可能为空，但仍会显示导入媒体数与相册可见数量。</div>
    </section>

    <section class="panel">
      <div class="section-head">
        <h2>异常轮次</h2>
      </div>
      {problems_html}
    </section>
  </div>
</body>
</html>
"""

    targets = [path]
    if _UPDATE_LATEST:
        targets.append(latest_path)
    for target_path in targets:
        with open(target_path, "w", encoding="utf-8") as file_obj:
            file_obj.write(html_text)
    return path, latest_path if _UPDATE_LATEST else path


def run_complex_flow(
    config_path: str,
    quick: bool = True,
    *,
    report_dir: str = "",
    run_slug: str = "",
    device_label: str = "",
    device_serial: str = "",
    update_latest: bool = True,
) -> Dict[str, Any]:
    global _ACTIVE_REPORT_DIR, _UPDATE_LATEST, _DEVICE_LABEL
    if report_dir.strip():
        app_config.use_run_output(report_dir.strip())
        _ACTIVE_REPORT_DIR = report_dir.strip()
    else:
        _ACTIVE_REPORT_DIR = RUN_LOG_DIR
    _UPDATE_LATEST = bool(update_latest) and not app_config.ACTIVE_RUN_DIR
    _DEVICE_LABEL = (device_label or "").strip()

    config = load_config(config_path)
    cycles = int(config.get("cycles", 1))
    tasks = list(config.get("tasks", []))
    if not tasks:
        raise ValueError("配置文件中没有定义任务")

    import_timeout = float(config.get("import_timeout_seconds", AUTOMATION_DEFAULT_IMPORT_TIMEOUT_SECONDS))
    import_stall_timeout = float(config.get("import_stall_timeout_seconds", AUTOMATION_IMPORT_STALL_TIMEOUT_SECONDS))
    import_poll_interval = float(config.get("import_poll_interval_seconds", AUTOMATION_PROGRESS_POLL_INTERVAL_SECONDS))
    import_retry_limit = max(1, int(config.get("import_retry_limit", AUTOMATION_IMPORT_RETRY_LIMIT)))
    import_retry_backoff_seconds = max(
        0.0, float(config.get("import_retry_backoff_seconds", AUTOMATION_IMPORT_RETRY_BACKOFF_SECONDS))
    )
    import_retry_relaunch_app = bool(config.get("import_retry_relaunch_app", AUTOMATION_IMPORT_RETRY_RELAUNCH_APP))

    run_slug = (run_slug or "").strip() or _now_slug()
    debug_log_path = _setup_runtime_debug_logging(run_slug)
    active_dir = _get_report_dir()
    if app_config.ACTIVE_RUN_DIR:
        summary_html_path = os.path.join(app_config.ACTIVE_RUN_DIR, "report.html")
        latest_summary_html_path = summary_html_path
        json_report_path = os.path.join(app_config.RUN_LOG_DIR, "result.json")
    else:
        summary_html_path = os.path.join(active_dir, f"complex_run_{run_slug}_summary.html")
        latest_summary_html_path = os.path.join(active_dir, "complex_run_latest.html")
        json_report_path = os.path.join(active_dir, f"complex_run_{run_slug}.json")
    logger.info("开始执行复杂流程，总轮回: %s", cycles)
    if _DEVICE_LABEL:
        logger.info("设备标签: %s", _DEVICE_LABEL)
    logger.info(
        "导入监控参数: timeout=%.1fs, stall=%.1fs, poll=%.1fs, retry=%s",
        import_timeout,
        import_stall_timeout,
        import_poll_interval,
        import_retry_limit,
    )
    logger.info("调试日志文件: %s", debug_log_path)

    resolved_serial = (device_serial or os.environ.get("GLASSES_DEVICE_SERIAL", "") or "").strip()
    abs_report_dir = os.path.abspath(active_dir)
    logger.info("复杂流程输出目录: %s", abs_report_dir)
    if any(ord(ch) > 127 for ch in abs_report_dir):
        logger.warning(
            "输出路径含非 ASCII 字符。导入后的文件大小探测/拉文件会走兼容逻辑；"
            "仍建议测试包使用纯英文路径，便于排查。"
        )
    report: Dict[str, Any] = {
        "started_at": _now_text(),
        "config_path": config_path,
        "quick": bool(quick),
        "cycles": cycles,
        "device_serial": resolved_serial,
        "device_label": _DEVICE_LABEL,
        "runtime_debug_log_path": debug_log_path,
        "json_report_path": json_report_path,
        "summary_html_path": summary_html_path,
        "latest_summary_html_path": latest_summary_html_path,
        "recommended_report_path": latest_summary_html_path,
        "import": {
            "timeout_seconds": import_timeout,
            "stall_timeout_seconds": import_stall_timeout,
            "poll_interval_seconds": import_poll_interval,
            "retry_limit": import_retry_limit,
            "retry_backoff_seconds": import_retry_backoff_seconds,
            "retry_relaunch_app": import_retry_relaunch_app,
        },
        "cycles_data": [],
        "problems": [],
        "summary": {
            "cycles_total": cycles,
            "cycles_import_success": 0,
            "cycles_import_failed": 0,
            "cycles_import_retry_used": 0,
            "task_total": 0,
            "task_success": 0,
            "task_failed": 0,
        },
    }

    phone_ops = PhoneOperations()
    run_error: Optional[BaseException] = None
    try:
        if quick:
            phone_ops.ensure_device_connected()
            phone_ops.wake_screen()
            phone_ops.launch_app(PHONE_APP_PACKAGE)

        expected_counts = _build_expected_counts(tasks)

        _run_cycles(
            phone_ops=phone_ops,
            report=report,
            run_slug=run_slug,
            cycles=cycles,
            tasks=tasks,
            quick=quick,
            expected_counts=expected_counts,
            import_timeout=import_timeout,
            import_stall_timeout=import_stall_timeout,
            import_poll_interval=import_poll_interval,
            import_retry_limit=import_retry_limit,
            import_retry_backoff_seconds=import_retry_backoff_seconds,
            import_retry_relaunch_app=import_retry_relaunch_app,
        )
    except BaseException as exc:
        run_error = exc
        logger.error("复杂流程中途异常: %s", exc)
        raise
    finally:
        try:
            logger.info("流程结束：强制停止录音/录像（防止眼镜端残留录制）")
            phone_ops.ensure_capture_stopped(kinds=("audio", "video"), retries=2, settle_seconds=1.0)
        except Exception as exc:
            logger.warning("流程结束强制停录失败: %s", exc)
        # 无论成功/失败：整次流程结束后才落盘报告（轮次中间不写）。
        report["finished_at"] = _now_text()
        if run_error is not None:
            report.setdefault("problems", []).append(
                {
                    "cycle": len(report.get("cycles_data") or []) + 1,
                    "status": "error",
                    "failure_reason": f"流程中途异常: {run_error}",
                    "failure_reason_raw": repr(run_error),
                    "logcat_path": "",
                    "screenshot_path": "",
                    "result_json_path": "",
                }
            )
        final_report_path = _save_complex_report(report, run_slug)
        final_summary_html_path, latest_summary_path = _save_html_summary(report, run_slug)
        logger.info("\n=== 全部轮回执行结束（再写报告）===")
        logger.info("最终 JSON 报告: %s", final_report_path)
        logger.info("最终网页报告: %s", final_summary_html_path)
        logger.info("首选查看报告: %s", latest_summary_path)

    # 失败用 summary 计数表达；不要再抛异常，否则桌面端长时间停在「进行中」后还冒出 Traceback。
    return report


def _run_cycles(
    *,
    phone_ops: PhoneOperations,
    report: Dict[str, Any],
    run_slug: str,
    cycles: int,
    tasks: List[Dict[str, Any]],
    quick: bool,
    expected_counts: Dict[str, int],
    import_timeout: float,
    import_stall_timeout: float,
    import_poll_interval: float,
    import_retry_limit: int,
    import_retry_backoff_seconds: float,
    import_retry_relaunch_app: bool,
) -> None:
    for cycle_index in range(1, cycles + 1):
        logger.info("\n=== 开始第 %s/%s 轮回 ===", cycle_index, cycles)
        cycle_data: Dict[str, Any] = {
            "cycle": cycle_index,
            "started_at": _now_text(),
            "tasks_finished_at": "",
            "finished_at": "",
            "expected_counts": dict(expected_counts),
            "tasks": [],
            "import": {},
            "cycle_summary": {},
        }

        for task_config in tasks:
            task_type = task_config.get("task")
            times = int(task_config.get("times", 1))
            duration = int(task_config.get("duration_seconds", 0))

            if not task_type:
                logger.warning("跳过空任务配置")
                continue

            # Rapid consecutive photos often fail on glasses; give each shot settle time.
            interval_seconds = 5.0 if str(task_type).strip().lower() == "photo" and times > 1 else 0.0
            logger.info(
                "执行任务: %s, times=%s, duration=%ss, interval=%.1fs",
                task_type,
                times,
                duration,
                interval_seconds,
            )
            flow_config = FlowConfig(
                task=task_type,
                times=times,
                duration_seconds=duration,
                interval_seconds=interval_seconds,
                retry_limit=3,
                continue_on_failure=True,
                quick=quick,
            )
            task_entry: Dict[str, Any] = {
                "task": task_type,
                "times": times,
                "duration_seconds": duration,
                "success": False,
                "report_path": "",
                "error": "",
            }
            report["summary"]["task_total"] += 1

            try:
                success, report_path = run_flow(flow_config)
                task_entry["success"] = bool(success)
                task_entry["report_path"] = report_path or ""
                if success:
                    report["summary"]["task_success"] += 1
                else:
                    report["summary"]["task_failed"] += 1
                logger.info("任务 %s 执行完成，成功: %s", task_type, success)
                if report_path:
                    logger.info("任务报告: %s", report_path)
            except Exception as exc:
                task_entry["error"] = str(exc)
                report["summary"]["task_failed"] += 1
                logger.error("任务 %s 执行失败: %s", task_type, exc)
            cycle_data["tasks"].append(task_entry)

        cycle_data["tasks_finished_at"] = _now_text()

        # Glasses may keep recording even after App returns stop dispatched.
        try:
            logger.info("导入前强制确认录音/录像已停止")
            phone_ops.ensure_capture_stopped(kinds=("audio", "video"), retries=2, settle_seconds=2.0)
        except Exception as exc:
            logger.warning("导入前强制停录失败: %s", exc)

        logger.info("\n开始执行导入操作")
        snapshot_session_fallback = f"cycle{cycle_index}_{run_slug}"
        check_gallery = max(0, _safe_int(expected_counts.get("gallery")) or 0) > 0
        check_audio = max(0, _safe_int(expected_counts.get("audio")) or 0) > 0
        before_snapshot: Dict[str, Any] = {}
        try:
            logger.info("导入前采样相册/录音数量（用于核对本轮增量）…")
            before_snapshot = phone_ops.capture_media_inventory_snapshot(
                session_id=f"{snapshot_session_fallback}_before",
                stage="before",
                include_screenshot=False,
                check_gallery=check_gallery,
                check_audio=check_audio,
            )
            g_before = _get_snapshot_section(before_snapshot, "gallery").get("count")
            a_before = _get_snapshot_section(before_snapshot, "audio").get("count")
            logger.info("导入前数量: 相册=%s 录音=%s", g_before, a_before)
        except Exception as exc:
            before_snapshot = {"error": str(exc)}
            logger.warning("导入前采样失败: %s", exc)
        cycle_data["before_snapshot"] = before_snapshot

        import_attempts: List[Dict[str, Any]] = []
        final_import_result: Optional[ImportWaitResult] = None
        import_attempt_used = 0

        for import_attempt in range(1, import_retry_limit + 1):
            import_attempt_used = import_attempt
            logger.info("导入尝试 %s/%s", import_attempt, import_retry_limit)

            if import_attempt > 1:
                logger.warning("上一轮导入失败，准备重试")
                if import_retry_relaunch_app:
                    try:
                        phone_ops.wake_screen()
                        phone_ops.launch_app(PHONE_APP_PACKAGE)
                    except Exception as exc:
                        logger.warning("导入重试前拉起 App 失败: %s", exc)
                if import_retry_backoff_seconds > 0:
                    time.sleep(import_retry_backoff_seconds)

            attempt_result = phone_ops.wait_import_completion(
                timeout_seconds=import_timeout,
                poll_interval_seconds=import_poll_interval,
                stall_timeout_seconds=import_stall_timeout,
                click_before_wait=True,
                expected_counts=expected_counts,
            )
            attempt_payload = attempt_result.to_dict()
            attempt_payload["attempt"] = import_attempt
            import_attempts.append(attempt_payload)
            final_import_result = attempt_result

            if attempt_result.success:
                break
            if attempt_result.failure_reason == "battery_low":
                logger.warning("检测到眼镜低电量，跳过后续导入重试")
                break
            if attempt_result.failure_reason == "storage_full":
                logger.warning("检测到手机存储空间不足，跳过后续导入重试")
                break
            if import_attempt < import_retry_limit:
                logger.warning(
                    "导入失败，准备重试: status=%s reason=%s phase=%s errors=%s",
                    attempt_result.status,
                    attempt_result.failure_reason,
                    attempt_result.phase,
                    len(attempt_result.error_signals),
                )

        if final_import_result is None:
            raise RuntimeError("导入流程未返回结果")

        # 导入 UI 已成功：下面只是给报告补文件名/大小/截图，不是还在导入。
        if final_import_result.success:
            logger.info("导入 UI 已成功，开始收集报告证据（手机本地文件/大小/相册截图）…")

        # logcat 空时，用 MediaStore 时间窗回补本轮文件，恢复文件名/大小列。
        if final_import_result.success and not final_import_result.success_paths:
            try:
                from glasstest.phone_operations import _parse_clock_text

                probe_serial = str(
                    report.get("device_serial")
                    or getattr(app_config, "PHONE_DEVICE_SERIAL", "")
                    or ""
                ).strip()
                start_ts = _parse_clock_text(cycle_data.get("started_at", ""))
                end_ts = _parse_clock_text(
                    cycle_data.get("tasks_finished_at", "")
                    or final_import_result.finished_at
                    or cycle_data.get("started_at", "")
                )
                if probe_serial and start_ts is not None and end_ts is not None:
                    logger.info("正在手机相册/媒体库回补本轮文件路径…")
                    recovered = list_recent_media_store_paths(
                        probe_serial,
                        since_epoch=start_ts,
                        until_epoch=end_ts,
                    )
                    if recovered:
                        final_import_result.success_paths = list(recovered)
                        logger.info("MediaStore 回补导入文件 %s 个", len(recovered))
                    else:
                        logger.info("MediaStore 未回补到本轮文件路径")
            except Exception as exc:
                logger.warning("MediaStore 回补失败: %s", exc)

        path_filter = filter_import_paths_by_capture_window(
            final_import_result.success_paths,
            cycle_data.get("started_at", ""),
            cycle_data.get("tasks_finished_at", ""),
            slack_before_seconds=180.0,
            slack_after_seconds=120.0,
        )
        final_import_result.current_cycle_success_paths = list(path_filter.get("matched_paths", []))
        final_import_result.ignored_success_paths = list(path_filter.get("ignored_paths", []))
        final_import_result.unknown_time_success_paths = list(path_filter.get("unknown_time_paths", []))
        size_probe_paths = list(
            dict.fromkeys(
                list(path_filter.get("matched_paths", []))
                + list(path_filter.get("ignored_paths", []))
            )
        )
        try:
            probe_serial = str(
                report.get("device_serial")
                or getattr(app_config, "PHONE_DEVICE_SERIAL", "")
                or ""
            ).strip()
            if size_probe_paths:
                logger.info("正在探测本轮 %s 个文件大小（优先直接 ls）…", len(size_probe_paths))
            file_sizes = probe_import_file_sizes(
                serial=probe_serial,
                paths=size_probe_paths,
                package=PHONE_APP_PACKAGE,
                logcat_path=str((final_import_result.artifact_paths or {}).get("logcat", "") or ""),
            )
            path_filter["file_sizes"] = file_sizes
            sized = sum(1 for info in file_sizes.values() if info.get("size_bytes") is not None)
            logger.info("本轮文件大小探测完成: %s/%s 有大小", sized, len(file_sizes))
        except Exception as exc:
            path_filter["file_sizes"] = {}
            logger.warning("本轮文件大小探测失败: %s", exc)
        final_import_result.success_path_filter = dict(path_filter)

        # Post-import validation by category.
        if final_import_result.success:
            after_snapshot: Dict[str, Any] = {}
            snapshot_session = final_import_result.session_id or snapshot_session_fallback
            try:
                # 给相册/录音页一点索引时间，减少「文件已到但页数未刷新」的误杀。
                time.sleep(2.0)
                logger.info("正在打开相册/录音页核对数量并截图…")
                after_snapshot = phone_ops.capture_media_inventory_snapshot(
                    session_id=snapshot_session,
                    stage="after",
                    include_screenshot=True,
                    check_gallery=check_gallery,
                    check_audio=check_audio,
                )
            except Exception as exc:
                after_snapshot = {"error": str(exc)}
                logger.warning("导入后采样失败: %s", exc)

            validation = _build_post_import_validation(
                expected_counts=expected_counts,
                after_snapshot=after_snapshot if isinstance(after_snapshot, dict) else {},
                import_result=final_import_result,
                path_filter=path_filter,
                check_gallery=check_gallery,
                check_audio=check_audio,
                before_snapshot=before_snapshot if isinstance(before_snapshot, dict) else {},
            )
            validation["transfer_success"] = True
            final_import_result.post_import_validation = validation
            final_import_result.summary_artifacts = dict(validation.get("artifacts", {}))
            reason_detail = str(validation.get("reason_detail", "")).strip()
            if validation.get("count_verified"):
                logger.info("导入数量已核实: %s", reason_detail or "ok")
            elif validation.get("status") == "count_unverified":
                logger.warning("导入数量未核实: %s", reason_detail or validation.get("status"))
            elif not validation.get("success", False):
                final_import_result.success = False
                final_import_result.status = str(validation.get("status", "validation_failed"))
                final_import_result.failure_reason = str(validation.get("status", "validation_failed"))
                if reason_detail and reason_detail not in final_import_result.error_signals:
                    final_import_result.error_signals.append(reason_detail)
                for key, value in validation.get("artifacts", {}).items():
                    if value and key not in final_import_result.artifact_paths:
                        final_import_result.artifact_paths[key] = value

                validation_record = final_import_result.to_dict()
                validation_record["cycle"] = cycle_index
                validation_record["validation_only_failure"] = True
                _append_jsonl(app_config.IMPORT_ERROR_LOG_FILE, validation_record)
                logger.warning("导入数量校验失败: %s", reason_detail or final_import_result.status)
            else:
                logger.info("导入后校验: %s", reason_detail or validation.get("status"))

        cycle_data["import"] = final_import_result.to_dict()
        cycle_data["import"]["attempt"] = import_attempt_used
        cycle_data["import"]["attempts"] = import_attempts

        cycle_summary = _build_cycle_summary(
            cycle_index=cycle_index,
            expected_counts=expected_counts,
            import_result=final_import_result,
            attempt_used=import_attempt_used,
        )
        cycle_data["cycle_summary"] = cycle_summary
        cycle_data["finished_at"] = _now_text()

        if import_attempt_used > 1:
            report["summary"]["cycles_import_retry_used"] += 1

        if cycle_summary.get("import_success"):
            report["summary"]["cycles_import_success"] += 1
            logger.info("导入成功，进入下一轮")
        else:
            report["summary"]["cycles_import_failed"] += 1
            logger.warning(
                "导入最终失败: status=%s reason=%s phase=%s",
                final_import_result.status,
                final_import_result.failure_reason,
                final_import_result.phase,
            )
            raw_reason = cycle_summary.get("problem_detail") or final_import_result.failure_reason
            report["problems"].append(
                {
                    "cycle": cycle_index,
                    "status": cycle_summary.get("validation_status")
                    if cycle_summary.get("transfer_success") and not cycle_summary.get("validation_success")
                    else final_import_result.status,
                    "failure_reason": _humanize_reason(raw_reason),
                    "failure_reason_raw": str(raw_reason or ""),
                    "logcat_path": cycle_summary.get("logcat_path", ""),
                    "screenshot_path": cycle_summary.get("screenshot_path", ""),
                    "result_json_path": cycle_summary.get("result_json_path", ""),
                }
            )

        report["cycles_data"].append(cycle_data)
        # 不在轮次中间落盘/更新 latest：全部轮回跑完才写 JSON+HTML（见 run_complex_flow 末尾）。
        logger.info(
            "第 %s/%s 轮回已完成（成功=%s），报告待全部轮回结束后再生成",
            cycle_index,
            cycles,
            bool(cycle_summary.get("import_success")),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="执行复杂任务流程")
    parser.add_argument("--config", type=str, default="task_config.json", help="任务配置文件路径")
    parser.add_argument("--report-dir", type=str, default="", help="报告输出目录（多机套件用）")
    parser.add_argument("--run-slug", type=str, default="", help="报告文件名时间戳后缀")
    parser.add_argument("--device-label", type=str, default="", help="设备别名，写入报告标题")
    parser.add_argument("--device-serial", type=str, default="", help="设备序列号，写入报告")
    parser.add_argument(
        "--no-latest",
        action="store_true",
        help="不更新 complex_run_latest.html（多机套件由汇总报告接管）",
    )
    quick_group = parser.add_mutually_exclusive_group()
    quick_group.add_argument(
        "--quick",
        dest="quick",
        action="store_true",
        help="快速模式：直接发送命令，不重启 App",
    )
    quick_group.add_argument(
        "--no-quick",
        dest="quick",
        action="store_false",
        help="非快速模式：执行启动前检查",
    )
    parser.set_defaults(quick=True)
    args = parser.parse_args()

    try:
        if args.device_serial.strip():
            app_config.apply_runtime_overrides(device_serial=args.device_serial.strip())
        run_complex_flow(
            args.config,
            args.quick,
            report_dir=args.report_dir,
            run_slug=args.run_slug,
            device_label=args.device_label,
            device_serial=args.device_serial,
            update_latest=not args.no_latest,
        )
    except Exception as exc:
        logger.error("执行失败: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()

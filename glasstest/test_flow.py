#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ADB -> App -> 眼镜 的单设备自动化执行流程。

能力覆盖：
1. 拍照 / 录音 / 视频任务循环
2. 广播结果校验与失败重试
3. 导入按钮点击与进度轮询
4. 偶发 Wi-Fi 系统弹窗自动确认
5. 失败截图与执行报告输出
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import glasstest.config as app_config

from glasstest.config import (
    APP_PACKAGE,
    AUTOMATION_DEFAULT_CONTINUE_ON_FAILURE,
    AUTOMATION_DEFAULT_DURATION_SECONDS,
    AUTOMATION_DEFAULT_INTERVAL_SECONDS,
    AUTOMATION_DEFAULT_RETRY_LIMIT,
    AUTOMATION_DEFAULT_TASK,
    AUTOMATION_DEFAULT_TIMES,
    AUTOMATION_POST_TRIGGER_WAIT_SECONDS,
    AUTOMATION_PRE_TRIGGER_WAIT_SECONDS,
    FAILURE_ARTIFACT_DIR,
    LOG_LEVEL,
    PHONE_APP_PACKAGE,
    PHONE_DEVICE_SERIAL,
    RUN_LOG_DIR,
)
from glasstest.phone_operations import PhoneOperations


WIFI_POPUP_KEYWORDS = [
    "连接",
    "connect",
    "允许",
    "allow",
    "仅此一次",
    "始终允许",
    "确定",
    "同意",
    "继续",
    "ok",
]
VIDEO_UNSUPPORTED_KEYWORDS = ["video control unsupported", "unknown command: start_video"]


LOG_FORMAT = "%(asctime)s | %(levelname)s | %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

logger = logging.getLogger("test_flow")
logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))
logger.handlers.clear()
logger.propagate = False

handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
logger.addHandler(handler)


class VideoUnsupportedError(RuntimeError):
    """视频控制指令未接通时抛出。"""


@dataclass
class FlowConfig:
    task: str
    times: int
    duration_seconds: int
    interval_seconds: float
    retry_limit: int
    continue_on_failure: bool
    quick: bool


def _now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _now_slug() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _normalize_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _iter_tree_nodes(node: Dict[str, Any]):
    yield node
    for child in node.get("children", []) or []:
        yield from _iter_tree_nodes(child)


def _iter_interactive_nodes(tree: Dict[str, Any]) -> List[Dict[str, Any]]:
    if isinstance(tree.get("interactive_nodes"), list) and tree["interactive_nodes"]:
        return tree["interactive_nodes"]

    root = tree.get("tree") or tree
    nodes: List[Dict[str, Any]] = []
    for node in _iter_tree_nodes(root):
        if node.get("clickable"):
            nodes.append(node)
    return nodes


def _parse_bounds(bounds: Any) -> Optional[Tuple[int, int, int, int]]:
    if isinstance(bounds, list) and len(bounds) == 4:
        return int(bounds[0]), int(bounds[1]), int(bounds[2]), int(bounds[3])
    if isinstance(bounds, str):
        match = re.findall(r"\[(\d+),(\d+)\]", bounds)
        if len(match) == 2:
            (x1, y1), (x2, y2) = match
            return int(x1), int(y1), int(x2), int(y2)
    return None


def _center_from_bounds(bounds: Any) -> Optional[Tuple[int, int]]:
    parsed = _parse_bounds(bounds)
    if parsed is None:
        return None
    x1, y1, x2, y2 = parsed
    return int((x1 + x2) / 2), int((y1 + y2) / 2)


def _node_matches(node: Dict[str, Any], keywords: Iterable[str]) -> bool:
    haystack = " ".join(
        [
            _normalize_text(node.get("text")),
            _normalize_text(node.get("resource_id")),
            _normalize_text(node.get("resource-id")),
            _normalize_text(node.get("content_desc")),
            _normalize_text(node.get("content-desc")),
            _normalize_text(node.get("class_name")),
            _normalize_text(node.get("class")),
        ]
    )
    return any(_normalize_text(k) and _normalize_text(k) in haystack for k in keywords)


def _find_node_by_keywords(tree: Dict[str, Any], keywords: List[str]) -> Optional[Dict[str, Any]]:
    for node in _iter_interactive_nodes(tree):
        if _node_matches(node, keywords):
            return node
    return None


def _ensure_output_dirs() -> None:
    os.makedirs(app_config.RUN_LOG_DIR, exist_ok=True)
    os.makedirs(app_config.FAILURE_ARTIFACT_DIR, exist_ok=True)


def _capture_failure_artifacts(
    phone_ops: PhoneOperations,
    round_index: int,
    attempt_index: int,
    tag: str,
) -> Dict[str, str]:
    artifacts: Dict[str, str] = {}
    base_name = f"{_now_slug()}_round{round_index:03d}_attempt{attempt_index:02d}_{tag}"
    screenshot_path = os.path.join(app_config.FAILURE_ARTIFACT_DIR, f"{base_name}.png")
    hierarchy_path = os.path.join(app_config.RUN_LOG_DIR, f"{base_name}.xml")

    try:
        phone_ops.screenshot(screenshot_path)
        artifacts["screenshot"] = screenshot_path
    except Exception as exc:
        logger.warning("失败截图保存失败: %s", exc)

    try:
        xml_text = phone_ops.dump_hierarchy()
        with open(hierarchy_path, "w", encoding="utf-8") as file_obj:
            file_obj.write(xml_text)
        artifacts["hierarchy"] = hierarchy_path
    except Exception as exc:
        logger.warning("失败层级树保存失败: %s", exc)

    return artifacts


def _click_node(phone_ops: PhoneOperations, node: Dict[str, Any]) -> bool:
    center = _center_from_bounds(node.get("bounds"))
    if center is None:
        center = _center_from_bounds(node.get("boundsInScreen"))
    if center is None:
        return False

    x, y = center
    phone_ops.click(x, y)
    return True


def _try_handle_wifi_popup(phone_ops: PhoneOperations) -> bool:
    live_tree = {"tree": phone_ops.build_control_tree()}
    popup_node = _find_node_by_keywords(live_tree, WIFI_POPUP_KEYWORDS)
    if popup_node and _click_node(phone_ops, popup_node):
        logger.info("检测到 Wi-Fi 弹窗并自动确认")
        return True

    if phone_ops.tap_first_text(["确定", "允许", "连接", "仅此一次", "OK", "Allow"], timeout=0.5):
        logger.info("通过文本匹配确认 Wi-Fi 弹窗")
        return True
    return False


def _is_video_unsupported_error(exc: Exception) -> bool:
    message = _normalize_text(str(exc))
    return any(keyword in message for keyword in VIDEO_UNSUPPORTED_KEYWORDS)


def _trigger_task(phone_ops: PhoneOperations, config: FlowConfig) -> Dict[str, Any]:
    output: Dict[str, Any] = {}
    task = config.task
    duration = max(0, int(config.duration_seconds))

    if task == "photo":
        output["start"] = phone_ops.take_device_photo()
        time.sleep(AUTOMATION_POST_TRIGGER_WAIT_SECONDS)
        return output

    if task == "audio":
        # Glasses/App need a positive duration to actually start capture.
        # Still send explicit stop + settle so recording does not linger.
        started = False
        start_duration = max(1, duration)
        try:
            output["start"] = phone_ops.start_device_audio_recording(duration_seconds=start_duration)
            started = True
            wait_seconds = float(start_duration)
            logger.info("录音执行等待 %.1fs（duration=%s，结束后强制停录）", wait_seconds, start_duration)
            time.sleep(wait_seconds)
            output["stop"] = phone_ops.stop_device_audio_recording()
            time.sleep(max(2.0, AUTOMATION_POST_TRIGGER_WAIT_SECONDS))
            output["force_stop"] = phone_ops.ensure_capture_stopped(
                kinds=("audio",),
                retries=2,
                settle_seconds=2.0,
            )
        finally:
            if started:
                try:
                    phone_ops.ensure_capture_stopped(kinds=("audio",), retries=1, settle_seconds=1.5)
                except Exception as exc:
                    logger.warning("录音收尾强制停止失败: %s", exc)
        return output

    if task == "video":
        started = False
        start_duration = max(1, duration)
        try:
            output["start"] = phone_ops.start_device_video_recording(duration_seconds=start_duration)
            started = True
            wait_seconds = float(start_duration)
            logger.info("视频执行等待 %.1fs（duration=%s，结束后强制停录）", wait_seconds, start_duration)
            time.sleep(wait_seconds)
            output["stop"] = phone_ops.stop_device_video_recording()
            time.sleep(max(2.0, AUTOMATION_POST_TRIGGER_WAIT_SECONDS))
            output["force_stop"] = phone_ops.ensure_capture_stopped(
                kinds=("video",),
                retries=2,
                settle_seconds=2.0,
            )
        finally:
            if started:
                try:
                    phone_ops.ensure_capture_stopped(kinds=("video",), retries=1, settle_seconds=1.5)
                except Exception as exc:
                    logger.warning("视频收尾强制停止失败: %s", exc)
        return output

    raise ValueError(f"不支持的任务类型: {task}")


def _prepare_runtime(phone_ops: PhoneOperations) -> None:
    active_serial = app_config.PHONE_DEVICE_SERIAL
    phone_ops.ensure_device_connected(active_serial)
    phone_ops.connect(active_serial)
    phone_ops.wake_screen(active_serial)
    phone_ops.ensure_app_running(PHONE_APP_PACKAGE or APP_PACKAGE)


def _run_single_attempt(
    phone_ops: PhoneOperations,
    config: FlowConfig,
    round_index: int,
    attempt_index: int,
) -> Dict[str, Any]:
    attempt_started_at = _now_text()
    started = time.time()

    result: Dict[str, Any] = {
        "attempt": attempt_index,
        "started_at": attempt_started_at,
        "status": "failed",
        "error": "",
        "trigger_output": {},
        "wifi_popup_clicked": False,
        "artifacts": {},
    }

    try:
        if not config.quick:
            _prepare_runtime(phone_ops)
            time.sleep(AUTOMATION_PRE_TRIGGER_WAIT_SECONDS)
        else:
            active_serial = app_config.PHONE_DEVICE_SERIAL
            phone_ops.ensure_device_connected(active_serial)
            phone_ops.connect(active_serial)
            phone_ops.wake_screen(active_serial)
            phone_ops.launch_app(PHONE_APP_PACKAGE or APP_PACKAGE)
            time.sleep(2.0)

        result["trigger_output"] = _trigger_task(phone_ops, config)
        
        if not config.quick:
            result["wifi_popup_clicked"] = _try_handle_wifi_popup(phone_ops)

        result["status"] = "success"
    except Exception as exc:
        if _is_video_unsupported_error(exc):
            raise VideoUnsupportedError(str(exc)) from exc
        result["error"] = str(exc)
        result["artifacts"] = _capture_failure_artifacts(phone_ops, round_index, attempt_index, "failed")
        if config.task in ("audio", "video"):
            try:
                phone_ops.ensure_capture_stopped(kinds=(config.task,), retries=2, settle_seconds=1.0)
            except Exception as stop_exc:
                logger.warning("失败后强制停录失败: %s", stop_exc)
    finally:
        result["finished_at"] = _now_text()
        result["elapsed_seconds"] = round(time.time() - started, 2)
    return result


def _run_round(
    phone_ops: PhoneOperations,
    config: FlowConfig,
    round_index: int,
) -> Dict[str, Any]:
    round_started = time.time()
    round_result: Dict[str, Any] = {
        "round": round_index,
        "task": config.task,
        "started_at": _now_text(),
        "finished_at": "",
        "elapsed_seconds": 0.0,
        "success": False,
        "attempts": [],
        "error": "",
    }

    for attempt_index in range(1, config.retry_limit + 1):
        logger.info("第 %s 轮，第 %s/%s 次尝试", round_index, attempt_index, config.retry_limit)
        attempt_result = _run_single_attempt(
            phone_ops=phone_ops,
            config=config,
            round_index=round_index,
            attempt_index=attempt_index,
        )
        round_result["attempts"].append(attempt_result)

        if attempt_result["status"] == "success":
            round_result["success"] = True
            break

        round_result["error"] = attempt_result.get("error", "")
        logger.warning("第 %s 轮尝试失败: %s", round_index, round_result["error"] or "未知错误")

    round_result["finished_at"] = _now_text()
    round_result["elapsed_seconds"] = round(time.time() - round_started, 2)
    return round_result


def _build_run_report(config: FlowConfig) -> Dict[str, Any]:
    return {
        "started_at": _now_text(),
        "task": config.task,
        "config": {
            "times": config.times,
            "duration_seconds": config.duration_seconds,
            "interval_seconds": config.interval_seconds,
            "retry_limit": config.retry_limit,
            "continue_on_failure": config.continue_on_failure,
            "quick": config.quick,
        },
        "rounds": [],
        "summary": {
            "total_rounds": config.times,
            "success_rounds": 0,
            "failed_rounds": 0,
            "video_disabled": False,
        },
    }


def run_flow(config: FlowConfig) -> Tuple[bool, str]:
    _ensure_output_dirs()
    report = _build_run_report(config)
    phone_ops = PhoneOperations()

    logger.info(
        "开始执行任务: task=%s times=%s duration=%ss retry=%s",
        config.task,
        config.times,
        config.duration_seconds,
        config.retry_limit,
    )

    if not config.quick:
        try:
            _prepare_runtime(phone_ops)
        except Exception as exc:
            raise RuntimeError(f"启动前检查失败: {exc}") from exc

    for round_index in range(1, config.times + 1):
        logger.info("开始第 %s/%s 轮", round_index, config.times)
        try:
            round_result = _run_round(
                phone_ops=phone_ops,
                config=config,
                round_index=round_index,
            )
        except VideoUnsupportedError as exc:
            logger.error("视频控制未接通，自动停止视频任务: %s", exc)
            report["summary"]["video_disabled"] = True
            report["summary"]["failed_rounds"] += 1
            report["rounds"].append(
                {
                    "round": round_index,
                    "task": config.task,
                    "started_at": _now_text(),
                    "finished_at": _now_text(),
                    "elapsed_seconds": 0.0,
                    "success": False,
                    "attempts": [],
                    "error": str(exc),
                }
            )
            break

        report["rounds"].append(round_result)
        if round_result["success"]:
            report["summary"]["success_rounds"] += 1
        else:
            report["summary"]["failed_rounds"] += 1
            if not config.continue_on_failure:
                logger.error("检测到失败且配置为 stop-on-failure，提前结束")
                break

        if round_index < config.times and config.interval_seconds > 0:
            logger.info("轮次间隔等待 %.1fs", config.interval_seconds)
            time.sleep(config.interval_seconds)

    report["finished_at"] = _now_text()
    report_path = os.path.join(app_config.RUN_LOG_DIR, f"run_{_now_slug()}.json")
    with open(report_path, "w", encoding="utf-8") as file_obj:
        json.dump(report, file_obj, ensure_ascii=False, indent=2)

    overall_success = report["summary"]["failed_rounds"] == 0 and not report["summary"]["video_disabled"]
    logger.info("执行结束。成功轮次=%s 失败轮次=%s", report["summary"]["success_rounds"], report["summary"]["failed_rounds"])
    logger.info("执行报告已保存: %s", report_path)
    return overall_success, report_path


def _parse_args() -> FlowConfig:
    parser = argparse.ArgumentParser(description="智能眼镜控制指令发送工具")
    parser.add_argument(
        "--task",
        choices=["photo", "audio", "video"],
        default=AUTOMATION_DEFAULT_TASK,
        help="任务类型: photo | audio | video",
    )
    parser.add_argument("--times", type=int, default=AUTOMATION_DEFAULT_TIMES, help="循环次数")
    parser.add_argument(
        "--duration-seconds",
        type=int,
        default=AUTOMATION_DEFAULT_DURATION_SECONDS,
        help="录音/视频时长(秒)，拍照任务可忽略",
    )
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=AUTOMATION_DEFAULT_INTERVAL_SECONDS,
        help="轮次间隔(秒)",
    )
    parser.add_argument(
        "--retry-limit",
        type=int,
        default=AUTOMATION_DEFAULT_RETRY_LIMIT,
        help="单轮失败重试次数",
    )
    parser.add_argument(
        "--continue-on-failure",
        dest="continue_on_failure",
        action="store_true",
        help="某轮失败后继续执行后续轮次",
    )
    parser.add_argument(
        "--stop-on-failure",
        dest="continue_on_failure",
        action="store_false",
        help="某轮失败后立即停止",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="快速模式：直接发送命令，不启动App",
    )

    parser.set_defaults(
        continue_on_failure=AUTOMATION_DEFAULT_CONTINUE_ON_FAILURE,
        quick=False,
    )

    args = parser.parse_args()

    times = max(1, int(args.times))
    duration_seconds = max(0, int(args.duration_seconds))
    retry_limit = max(1, int(args.retry_limit))
    interval_seconds = max(0.0, float(args.interval_seconds))

    return FlowConfig(
        task=str(args.task).strip().lower(),
        times=times,
        duration_seconds=duration_seconds,
        interval_seconds=interval_seconds,
        retry_limit=retry_limit,
        continue_on_failure=bool(args.continue_on_failure),
        quick=bool(args.quick),
    )


def main() -> int:
    config = _parse_args()
    try:
        success, _ = run_flow(config)
        return 0 if success else 1
    except Exception as exc:
        logger.exception("执行流程失败: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())

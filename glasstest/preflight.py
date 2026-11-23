#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compatibility preflight checks for GlassesTest."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import glasstest.config as app_config
from glasstest.phone_operations import PhoneOperations, refresh_selector_constants
import glasstest.ui_selectors as ui_selectors


def _now_slug() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _check_result(
    name: str,
    ok: bool,
    message: str,
    *,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "name": name,
        "ok": bool(ok),
        "message": message,
        "details": details or {},
    }


def run_preflight(
    *,
    device_serial: Optional[str] = None,
    app_package: Optional[str] = None,
    probe_broadcast: bool = True,
    launch_app: bool = True,
) -> Dict[str, Any]:
    """Run layered diagnostics and return a structured report."""
    refresh_selector_constants()

    if device_serial:
        app_config.apply_runtime_overrides(device_serial=device_serial)
    if app_package:
        app_config.apply_runtime_overrides(app_package=app_package)

    serial = (device_serial or app_config.PHONE_DEVICE_SERIAL or "").strip()
    package = (app_package or app_config.PHONE_APP_PACKAGE or "").strip()
    checks: List[Dict[str, Any]] = []
    artifacts: Dict[str, str] = {}
    started = time.time()

    phone = PhoneOperations()

    # 1) ADB devices
    try:
        devices = phone.list_connected_devices()
        if not devices:
            checks.append(_check_result("adb_devices", False, "未发现任何 ADB 设备", details={"devices": []}))
            return _finalize_report(checks, artifacts, serial, package, started)
        if serial and serial not in devices:
            checks.append(
                _check_result(
                    "adb_devices",
                    False,
                    f"目标设备不在线: {serial}",
                    details={"devices": devices, "target": serial},
                )
            )
            return _finalize_report(checks, artifacts, serial, package, started)
        if not serial:
            serial = devices[0]
            app_config.apply_runtime_overrides(device_serial=serial)
        checks.append(
            _check_result(
                "adb_devices",
                True,
                f"设备在线: {serial}",
                details={"devices": devices, "target": serial},
            )
        )
    except Exception as exc:
        checks.append(_check_result("adb_devices", False, f"adb devices 失败: {exc}"))
        return _finalize_report(checks, artifacts, serial, package, started)

    # 2) uiautomator2 connect
    try:
        phone.ensure_device_connected(serial)
        phone.wake_screen(serial)
        phone.connect(serial)
        checks.append(_check_result("uiautomator2", True, "uiautomator2 连接成功"))
    except Exception as exc:
        checks.append(_check_result("uiautomator2", False, f"uiautomator2 连接失败: {exc}"))
        return _finalize_report(checks, artifacts, serial, package, started)

    # 3) Launch app + dump home
    diag_dir = os.path.join(app_config.DIAGNOSTICS_DIR, f"preflight_{_now_slug()}")
    os.makedirs(diag_dir, exist_ok=True)

    if launch_app:
        try:
            phone.launch_app(package)
            time.sleep(2.0)
            phone.ensure_home_screen(max_steps=3)
            checks.append(_check_result("app_launch", True, f"已拉起 App: {package}"))
        except Exception as exc:
            checks.append(_check_result("app_launch", False, f"拉起 App 失败: {exc}"))
            return _finalize_report(checks, artifacts, serial, package, started)
    else:
        checks.append(_check_result("app_launch", True, "已跳过 App 拉起", details={"skipped": True}))

    xml_path = os.path.join(diag_dir, "home_hierarchy.xml")
    png_path = os.path.join(diag_dir, "home_screenshot.png")
    try:
        xml_text = phone.dump_hierarchy()
        with open(xml_path, "w", encoding="utf-8") as file_obj:
            file_obj.write(xml_text)
        phone.screenshot(png_path)
        artifacts["hierarchy_xml"] = xml_path
        artifacts["screenshot"] = png_path
        checks.append(
            _check_result(
                "home_dump",
                True,
                "已保存首页 hierarchy / 截图",
                details={"hierarchy_xml": xml_path, "screenshot": png_path},
            )
        )
    except Exception as exc:
        checks.append(_check_result("home_dump", False, f"首页 dump 失败: {exc}"))
        return _finalize_report(checks, artifacts, serial, package, started)

    # 4) Import / home selector presence
    # After a successful import, App may hide the import card until new media exists.
    # Do NOT take a real photo here — that pollutes the next complex-run counts.
    try:
        phone.dismiss_blocking_dialogs(rounds=2)
        phone.ensure_home_screen(max_steps=2)
        xml_text = phone.dump_hierarchy()
        import_state = phone._inspect_import_ui(xml_text)
        nav_state = phone._inspect_navigation_ui(xml_text)
        has_import = bool(import_state.get("has_import_button") or import_state.get("has_import_card"))
        on_home = phone._is_home_screen(nav_state)

        selector_details = {
            "import_button_text": ui_selectors.import_button_text(),
            "import_card_resource_id": ui_selectors.import_card_resource_id(),
            "has_import_button": bool(import_state.get("has_import_button")),
            "has_import_card": bool(import_state.get("has_import_card")),
            "import_button_bounds": import_state.get("import_button_bounds") or "",
            "on_home_screen": on_home,
            "navigation": nav_state,
        }
        if has_import:
            checks.append(
                _check_result(
                    "import_selector",
                    True,
                    "已定位到导入相关控件",
                    details=selector_details,
                )
            )
        elif on_home:
            checks.append(
                _check_result(
                    "import_selector",
                    True,
                    "首页正常，当前无待导入媒体所以导入入口隐藏（属正常，不会额外拍照）",
                    details=selector_details,
                )
            )
        else:
            checks.append(
                _check_result(
                    "import_selector",
                    False,
                    "未找到导入按钮/导入卡片，且不在首页，请更新 selectors.json",
                    details=selector_details,
                )
            )
        if on_home:
            checks.append(_check_result("home_screen", True, "当前判定为首页", details=nav_state))
        else:
            checks.append(
                _check_result(
                    "home_screen",
                    False,
                    "未能确认首页（可能仍在子页面）",
                    details=nav_state,
                )
            )
    except Exception as exc:
        checks.append(_check_result("import_selector", False, f"选择器检查失败: {exc}"))

    # 5) Optional broadcast probe (non-destructive unknown command)
    if probe_broadcast:
        try:
            output = phone._send_device_control_command(
                "preflight_ping",
                require_dispatched=False,
            )
            normalized = (output or "").lower()
            if "invalid token" in normalized:
                checks.append(
                    _check_result(
                        "broadcast_probe",
                        False,
                        "广播 token 无效，请更新 config / 环境变量 / selectors.json",
                        details={"output": output[:500]},
                    )
                )
            else:
                checks.append(
                    _check_result(
                        "broadcast_probe",
                        True,
                        "DEVICE_CONTROL 广播通道可达（探测命令已发出）",
                        details={"output": output[:500]},
                    )
                )
        except Exception as exc:
            message = str(exc)
            lowered = message.lower()
            if "invalid token" in lowered:
                checks.append(
                    _check_result(
                        "broadcast_probe",
                        False,
                        "广播 token 无效，请更新 config / 环境变量 / selectors.json",
                        details={"error": message[:500]},
                    )
                )
            elif "unknown command" in lowered:
                checks.append(
                    _check_result(
                        "broadcast_probe",
                        True,
                        "token 有效（探测命令返回 unknown command，符合预期）",
                        details={"error": message[:500]},
                    )
                )
            else:
                checks.append(
                    _check_result(
                        "broadcast_probe",
                        False,
                        f"广播探测失败（检查 token / App 是否支持 ADB 控制）: {exc}",
                    )
                )
    else:
        checks.append(
            _check_result(
                "broadcast_probe",
                True,
                "已跳过广播探测",
                details={"skipped": True},
            )
        )

    return _finalize_report(checks, artifacts, serial, package, started)


def _finalize_report(
    checks: List[Dict[str, Any]],
    artifacts: Dict[str, str],
    serial: str,
    package: str,
    started: float,
) -> Dict[str, Any]:
    ok = all(bool(item.get("ok")) for item in checks) if checks else False
    report = {
        "ok": ok,
        "device_serial": serial,
        "app_package": package,
        "duration_seconds": round(time.time() - started, 2),
        "checks": checks,
        "artifacts": artifacts,
        "failed_checks": [item["name"] for item in checks if not item.get("ok")],
        "finished_at": datetime.now().isoformat(timespec="seconds"),
    }

    diag_root = app_config.DIAGNOSTICS_DIR
    os.makedirs(diag_root, exist_ok=True)
    report_path = os.path.join(diag_root, f"preflight_{_now_slug()}.json")
    with open(report_path, "w", encoding="utf-8") as file_obj:
        json.dump(report, file_obj, ensure_ascii=False, indent=2)
    report["report_path"] = report_path
    return report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="GlassesTest preflight diagnostics")
    parser.add_argument("--device", default="", help="ADB device serial")
    parser.add_argument("--package", default="", help="App package name")
    parser.add_argument("--no-broadcast", action="store_true", help="Skip DEVICE_CONTROL probe")
    parser.add_argument("--no-launch", action="store_true", help="Skip app launch")
    args = parser.parse_args(argv)

    report = run_preflight(
        device_serial=args.device or None,
        app_package=args.package or None,
        probe_broadcast=not args.no_broadcast,
        launch_app=not args.no_launch,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())

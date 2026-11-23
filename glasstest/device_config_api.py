"""Fetch Altair device-config and map App MCU firmware → soft_version_name."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

DEFAULT_API_BASE = "https://altair.moyoung.com"
DEVICE_CONFIG_PATH = "/api/v1/device-config"


def soft_version_from_mcu_firmware(raw: str) -> str:
    """MOY-A093-0.1.9 → A09 （MOY- 后取 3 位 = 后台软件版本名称）."""
    text = (raw or "").strip()
    if not text:
        return ""
    match = re.search(r"MOY-([A-Za-z0-9]{3})", text, flags=re.IGNORECASE)
    if match:
        return match.group(1).upper()
    match = re.search(r"\b([A-Za-z]\d{2})\b", text)
    if match:
        return match.group(1).upper()
    return ""


def fetch_device_config(
    version_name: str,
    *,
    biz_code: str = "",
    release_version: str = "",
    allwin_version: str = "",
    api_base: str = DEFAULT_API_BASE,
    timeout: float = 20.0,
) -> Dict[str, Any]:
    version_name = (version_name or "").strip()
    if not version_name:
        raise ValueError("version_name 为空")
    params: Dict[str, str] = {"version_name": version_name}
    if biz_code.strip():
        params["biz_code"] = biz_code.strip()
    if release_version.strip():
        params["release_version"] = release_version.strip()
    if allwin_version.strip():
        params["allwin_version"] = allwin_version.strip()
    url = f"{api_base.rstrip('/')}{DEVICE_CONFIG_PATH}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "GlassesTest/resolution-check"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"device-config HTTP {exc.code}: {body[:300]}") from exc
    except Exception as exc:
        raise RuntimeError(f"device-config 请求失败: {exc}") from exc

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"device-config 返回非 JSON: {body[:200]}") from exc
    if not isinstance(payload, dict) or not payload.get("success"):
        raise RuntimeError(f"device-config 失败: {payload}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError(f"device-config data 无效: {payload}")
    data = dict(data)
    data["_request"] = {"url": url, "http_status": status, "version_name": version_name}
    return data


def parse_size_text(raw: Any) -> Optional[tuple[int, int]]:
    text = str(raw or "").strip().lower().replace(" ", "")
    if not text:
        return None
    text = text.replace("*", "x").replace("×", "x")
    match = re.fullmatch(r"(\d+)x(\d+)", text)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))

# -*- coding: utf-8 -*-
"""Read-only UI probe: dump home discovery/gallery/audio positions. No product logic changes."""
from __future__ import annotations

import re
import time
from pathlib import Path

import uiautomator2 as u2

from glasstest.phone_operations import (
    FEATURE_GRID_RESOURCE_ID,
    FEATURE_TEXT_RESOURCE_ID,
    discovery_marker_y,
    feature_tap_point,
    home_can_scroll,
)

OUT = Path("output/diagnostics/ui_probe_home")
SERIAL = "HURC8PVOHYIBHMEQ"
LABELS = ("发现", "录音", "相册", "查看全部", "AI 记忆", "对话翻译", "拍照", "录像")


def dump_nodes(xml: str) -> None:
    print(f"FEATURE_TEXT_ID={FEATURE_TEXT_RESOURCE_ID}")
    print(f"FEATURE_GRID_ID={FEATURE_GRID_RESOURCE_ID}")
    print(f"can_scroll={home_can_scroll(xml)} marker_y={discovery_marker_y(xml)}")
    print(f"feature_tap_point(录音)={feature_tap_point(xml, '录音')}")
    print(f"feature_tap_point(相册)={feature_tap_point(xml, '相册')}")
    print("--- nodes of interest ---")
    for m in re.finditer(r"<node\b[^>]*>", xml):
        tag = m.group(0)
        text = re.search(r'\btext="([^"]*)"', tag)
        rid = re.search(r'\bresource-id="([^"]*)"', tag)
        bounds = re.search(r'\bbounds="([^"]*)"', tag)
        desc = re.search(r'\bcontent-desc="([^"]*)"', tag)
        clickable = re.search(r'\bclickable="([^"]*)"', tag)
        t = text.group(1) if text else ""
        r = rid.group(1) if rid else ""
        b = bounds.group(1) if bounds else ""
        c = desc.group(1) if desc else ""
        interesting = (
            t in LABELS
            or "discovery" in r
            or "feature" in r
            or "gallery" in r
            or "scroll" in r
            or "录音" in c
            or "相册" in c
        )
        if not interesting:
            continue
        print(
            f"text={t!r:12} clickable={clickable.group(1) if clickable else '?':5} "
            f"rid={r[-55:] or '-':55} bounds={b} desc={c!r}"
        )


def save(xml: str, name: str) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.xml"
    path.write_text(xml, encoding="utf-8")
    print(f"saved {path}")
    return path


def main() -> None:
    d = u2.connect(SERIAL)
    print("serial", d.serial, "display", d.info.get("displayWidth"), d.info.get("displayHeight"))
    d.press("home")
    time.sleep(0.5)
    d.app_stop("com.moyoung.glasses")
    time.sleep(0.8)
    d.app_start("com.moyoung.glasses")
    time.sleep(2.5)

    xml0 = d.dump_hierarchy()
    save(xml0, "01_home_initial")
    print("\n===== 01 initial home =====")
    dump_nodes(xml0)

    w = int(d.info.get("displayWidth") or 720)
    h = int(d.info.get("displayHeight") or 1600)
    # Same swipe as product code currently uses
    x = max(80, min(w - 160, int(w * 0.45)))
    y_from = int(h * 0.70)
    y_to = int(h * 0.34)
    print(f"\nproduct swipe: ({x},{y_from}) -> ({x},{y_to})")
    d.swipe(x, y_from, x, y_to, 0.35)
    time.sleep(0.7)
    xml1 = d.dump_hierarchy()
    save(xml1, "02_after_product_swipe_1")
    print("\n===== 02 after product swipe #1 =====")
    dump_nodes(xml1)

    d.swipe(x, y_from, x, y_to, 0.35)
    time.sleep(0.7)
    xml2 = d.dump_hierarchy()
    save(xml2, "03_after_product_swipe_2")
    print("\n===== 03 after product swipe #2 =====")
    dump_nodes(xml2)

    d.swipe(x, y_from, x, y_to, 0.35)
    time.sleep(0.7)
    xml3 = d.dump_hierarchy()
    save(xml3, "04_after_product_swipe_3")
    print("\n===== 04 after product swipe #3 =====")
    dump_nodes(xml3)

    # Also screenshot each step if possible
    try:
        d.screenshot(str(OUT / "01_home_initial.png"))
        # can't easily re-get earlier states; screenshot current only
        d.screenshot(str(OUT / "04_after_product_swipe_3.png"))
        print("screenshots saved under", OUT)
    except Exception as exc:
        print("screenshot failed", exc)


if __name__ == "__main__":
    main()

"""Phone-agnostic mapping from import/logcat paths to app-private media files."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

from glasstest.media_measure import parse_device_path

MEDIA_RES_REL = "files/moyoung/wifi/media_res"
ExistsFn = Callable[[str], bool]
ListNamesFn = Callable[[], Sequence[str]]


def classify_path(path: str) -> str:
    lower = (path or "").lower()
    name = Path(path or "").name.lower()
    if lower.endswith((".jpg", ".jpeg", ".png", ".webp")):
        return "photo"
    if lower.endswith((".mp4", ".mov", ".mkv", ".avi")):
        return "video"
    # cache_…_video-20261104… (no extension) and any *stabilized-* public name
    if "stabilized-" in lower or "video-" in name or name.startswith("video-"):
        return "video"
    return "unknown"


def media_recency_key(path: str) -> Tuple[int, str]:
    name = Path(path).name
    match = re.search(r"(\d{12,})", name)
    return (int(match.group(1)) if match else -1, name)


def candidate_media_names(name: str) -> List[str]:
    """Different phones/app versions use cache_*, image_*, stabilized-*, or bare timestamps."""
    lower = (name or "").lower()
    stamp_match = re.search(r"(\d{12,})", name or "")
    stamp = stamp_match.group(1) if stamp_match else ""
    names: List[str] = [name] if name else []
    if stamp:
        if lower.endswith((".jpg", ".jpeg", ".png", ".webp")):
            suffix = ".png" if lower.endswith(".png") else ".jpg"
            names.extend([f"image_{stamp}{suffix}", f"{stamp}{suffix}", f"{stamp}.jpg"])
        elif (
            lower.endswith((".mp4", ".mov", ".mkv"))
            or "video-" in lower
            or "stabilized-" in lower
        ):
            # Public gallery may use stabilized- / 6stabilized- / bare stamp.
            names.extend(
                [
                    f"stabilized-{stamp}.mp4",
                    f"6stabilized-{stamp}.mp4",
                    f"{stamp}.mp4",
                    f"video-{stamp}.mp4",
                    f"video-{stamp}",
                ]
            )
    out: List[str] = []
    seen = set()
    for item in names:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def extract_paths_from_import_result(result: object) -> List[str]:
    payload = result.to_dict() if hasattr(result, "to_dict") else (
        result if isinstance(result, dict) else getattr(result, "__dict__", {}) or {}
    )
    candidates: List[str] = []
    if isinstance(payload, dict):
        for key in ("current_cycle_success_paths", "success_paths", "unknown_time_success_paths"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates.extend(str(v) for v in value if v)
    seen = set()
    out: List[str] = []
    for path in candidates:
        if path and path not in seen:
            seen.add(path)
            out.append(path)
    return out


def pick_latest_typed_paths(paths: Iterable[str], media: str) -> List[str]:
    items = [str(p) for p in paths if p]
    typed = [p for p in items if classify_path(p) == media]
    if not typed:
        typed = [p for p in items if classify_path(p) != "unknown"] or items
    return typed


def resolve_media_rel_path(
    reported_path: str,
    *,
    package: str,
    exists_fn: ExistsFn,
    list_names_fn: Optional[ListNamesFn] = None,
) -> str:
    """Map a logcat/cache path to a relative run-as path that exists."""
    rel = parse_device_path(reported_path, package=package)
    if exists_fn(rel):
        return rel

    for candidate_name in candidate_media_names(Path(reported_path).name):
        candidate = f"{MEDIA_RES_REL}/{candidate_name}"
        if candidate != rel and exists_fn(candidate):
            return candidate

    stamp_match = re.search(r"(\d{12,})", Path(reported_path).name)
    if stamp_match and list_names_fn is not None:
        stamp = stamp_match.group(1)
        for listed in list_names_fn() or []:
            if stamp in listed:
                return f"{MEDIA_RES_REL}/{listed}"
    return rel

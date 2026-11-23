"""Helpers to embed screenshots / log excerpts into a single HTML report."""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any, Dict, List, Optional


def file_to_data_uri(path: str | Path, *, max_bytes: int = 4_000_000) -> str:
    file_path = Path(path)
    if not file_path.is_file():
        return ""
    try:
        data = file_path.read_bytes()
    except OSError:
        return ""
    if not data or len(data) > max_bytes:
        return ""
    mime, _ = mimetypes.guess_type(str(file_path))
    if not mime:
        suffix = file_path.suffix.lower()
        mime = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".gif": "image/gif",
        }.get(suffix, "application/octet-stream")
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def read_log_excerpt(path: str | Path, *, max_lines: int = 300, max_chars: int = 180_000) -> str:
    file_path = Path(path)
    if not file_path.is_file():
        return ""
    try:
        text = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = text.splitlines()
    if len(lines) > max_lines:
        lines = lines[-max_lines:]
        text = "\n".join(lines)
        text = f"…（仅显示末尾 {max_lines} 行）\n{text}"
    else:
        text = "\n".join(lines)
    if len(text) > max_chars:
        text = "…\n" + text[-max_chars:]
    return text


def img_tag(path: str | Path, *, alt: str = "截图", max_width: int = 720) -> str:
    uri = file_to_data_uri(path)
    if not uri:
        return ""
    from html import escape

    return (
        f'<figure class="shot"><figcaption>{escape(alt)}</figcaption>'
        f'<img src="{uri}" alt="{escape(alt)}" style="max-width:{max_width}px;width:100%;height:auto;border:1px solid #d9ddd3;border-radius:10px;" />'
        f"</figure>"
    )


def pre_block(text: str, *, title: str = "日志") -> str:
    from html import escape

    body = escape(text or "").strip()
    if not body:
        return ""
    return (
        f'<details class="logblock" open>'
        f"<summary>{escape(title)}</summary>"
        f'<pre style="white-space:pre-wrap;background:#f7f8f5;border:1px solid #d9ddd3;border-radius:10px;padding:12px;max-height:420px;overflow:auto;">{body}</pre>'
        f"</details>"
    )


def summarize_conclusion(payload: Dict[str, Any], job_kind: str, status: str = "") -> str:
    if not isinstance(payload, dict):
        payload = {}
    if job_kind == "resolution":
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        fatal = str(payload.get("fatal_error") or "").strip()
        passed = int(summary.get("passed") or 0)
        failed = int(summary.get("failed") or 0)
        skipped = int(summary.get("skipped") or 0)
        if fatal:
            return f"失败 · 前置错误：{fatal}"
        if failed > 0:
            return f"失败 · 通过 {passed} / 失败 {failed} / 跳过 {skipped}"
        if status == "stopped":
            return "已停止"
        return f"通过 · 通过 {passed} / 跳过 {skipped}"

    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    success = int(summary.get("cycles_import_success") or 0)
    failed = int(summary.get("cycles_import_failed") or 0)
    total = int(summary.get("cycles_total") or payload.get("cycles") or 0)
    problems = payload.get("problems") if isinstance(payload.get("problems"), list) else []
    reason = ""
    if problems and isinstance(problems[0], dict):
        reason = str(
            problems[0].get("failure_reason")
            or problems[0].get("failure_reason_raw")
            or problems[0].get("status")
            or ""
        ).strip()
    if not reason:
        cycles = payload.get("cycles_data") if isinstance(payload.get("cycles_data"), list) else []
        if cycles and isinstance(cycles[0], dict):
            csum = cycles[0].get("cycle_summary") if isinstance(cycles[0].get("cycle_summary"), dict) else {}
            reason = str(csum.get("problem_detail") or "").strip()
    if status == "stopped":
        return "已停止"
    if failed > 0 or (total > 0 and success < total):
        suffix = f" · {reason}" if reason else ""
        return f"失败 · 成功轮次 {success}/{total or '-'}{suffix}"
    if total > 0 and success >= total:
        return f"通过 · 成功轮次 {success}/{total}"
    if status == "succeeded":
        return "通过"
    if status == "failed":
        return f"失败{(' · ' + reason) if reason else ''}"
    return status or "已完成"


def _walk_paths(node: Any, keys: tuple[str, ...], found: List[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in keys and isinstance(value, str) and value.strip():
                found.append(value.strip())
            else:
                _walk_paths(value, keys, found)
    elif isinstance(node, list):
        for item in node:
            _walk_paths(item, keys, found)


def collect_run_artifacts(report_dir: Optional[Path], payload: Dict[str, Any]) -> Dict[str, Any]:
    shots: List[str] = []
    logs: List[str] = []
    if report_dir and report_dir.is_dir():
        for shot_name in ("shots", "截图"):
            shot_dir = report_dir / shot_name
            if not shot_dir.is_dir():
                continue
            for path in sorted(shot_dir.glob("*.png")) + sorted(shot_dir.glob("*.jpg")):
                shots.append(str(path))
            break
        for log_name in ("logs", "日志"):
            log_dir = report_dir / log_name
            if not log_dir.is_dir():
                continue
            for name in ("run.log", "debug.log", "运行日志.log", "调试日志.log"):
                candidate = log_dir / name
                if candidate.is_file():
                    logs.append(str(candidate))
            break
    _walk_paths(
        payload,
        ("screenshot_path", "gallery_screenshot", "audio_screenshot", "error_screenshot_path"),
        shots,
    )
    _walk_paths(payload, ("logcat_path", "runtime_debug_log_path"), logs)
    # unique preserve order
    uniq_shots: List[str] = []
    seen = set()
    for item in shots:
        if item in seen:
            continue
        if Path(item).is_file():
            seen.add(item)
            uniq_shots.append(item)
    uniq_logs: List[str] = []
    seen_l = set()
    for item in logs:
        if item in seen_l:
            continue
        if Path(item).is_file():
            seen_l.add(item)
            uniq_logs.append(item)
    excerpt = ""
    for log_path in uniq_logs[:2]:
        part = read_log_excerpt(log_path)
        if part:
            excerpt = (excerpt + "\n\n" if excerpt else "") + f"## {Path(log_path).name}\n{part}"
    return {"screenshots": uniq_shots[:12], "logs": uniq_logs[:6], "log_excerpt": excerpt}


def build_embedded_sections(report_dir: str | Path, payload: Dict[str, Any], job_kind: str, status: str = "") -> str:
    root = Path(report_dir) if report_dir else None
    arts = collect_run_artifacts(root, payload)
    conclusion = summarize_conclusion(payload, job_kind, status)
    from html import escape

    shot_html = []
    for index, path in enumerate(arts.get("screenshots") or [], start=1):
        tag = img_tag(path, alt=f"截图 {index} · {Path(path).name}")
        if tag:
            shot_html.append(tag)
    logs_html = []
    for path in arts.get("logs") or []:
        block = pre_block(read_log_excerpt(path), title=f"日志 · {Path(path).name}")
        if block:
            logs_html.append(block)
    tone = "bad" if conclusion.startswith("失败") else ("warn" if conclusion.startswith("已停止") else "good")
    return f"""
<section class="panel conclusion">
  <h2>结论</h2>
  <p class="badge {tone}" style="font-size:16px;padding:8px 14px;">{escape(conclusion)}</p>
  <p class="muted">下面截图和日志已内嵌在本文件，可单独发送此 HTML 做问题分析。</p>
</section>
<section class="panel">
  <h2>截图参考</h2>
  {''.join(shot_html) if shot_html else '<p class="muted">本次没有可用截图。</p>'}
</section>
<section class="panel">
  <h2>日志（便于分析）</h2>
  {''.join(logs_html) if logs_html else '<p class="muted">本次没有可用日志摘录。</p>'}
</section>
"""

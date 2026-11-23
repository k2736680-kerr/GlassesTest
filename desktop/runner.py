"""Background test runner used by the desktop window."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from desktop.paths import bootstrap, code_root, is_frozen, output_root, work_root

LogCallback = Callable[[str], None]
StatusCallback = Callable[[Dict[str, Any]], None]


@dataclass
class RunState:
    status: str = "idle"  # idle | running | succeeded | failed | stopped
    job_kind: str = "import"
    message: str = ""
    started_at: str = ""
    finished_at: str = ""
    report_dir: str = ""
    html_report_path: str = ""
    json_report_path: str = ""
    log_path: str = ""
    return_code: Optional[int] = None
    conclusion: str = ""
    screenshots: List[str] = field(default_factory=list)
    log_excerpt: str = ""


class DesktopRunner:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state = RunState()
        self._process: Optional[subprocess.Popen] = None
        self._worker: Optional[threading.Thread] = None
        self._stop = False
        self._log_cb: Optional[LogCallback] = None
        self._status_cb: Optional[StatusCallback] = None
        self._tail_stop = threading.Event()

    def set_callbacks(self, log_cb: LogCallback, status_cb: StatusCallback) -> None:
        self._log_cb = log_cb
        self._status_cb = status_cb

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            s = self._state
            return {
                "status": s.status,
                "job_kind": s.job_kind,
                "message": s.message,
                "started_at": s.started_at,
                "finished_at": s.finished_at,
                "report_dir": s.report_dir,
                "html_report_path": s.html_report_path,
                "json_report_path": s.json_report_path,
                "log_path": s.log_path,
                "return_code": s.return_code,
                "conclusion": s.conclusion,
                "screenshots": list(s.screenshots),
                "log_excerpt": s.log_excerpt,
            }

    def _emit_status(self) -> None:
        if self._status_cb:
            try:
                self._status_cb(self.snapshot())
            except Exception:
                pass

    def _emit_log(self, line: str) -> None:
        if self._log_cb:
            try:
                self._log_cb(line)
            except Exception:
                pass

    def is_running(self) -> bool:
        with self._lock:
            return self._state.status == "running"

    def start(
        self,
        *,
        device_serial: str,
        job_kind: str = "import",
        quick: bool = True,
        task_config: Optional[Dict[str, Any]] = None,
        resolution_options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            if self._state.status == "running":
                raise RuntimeError("已有任务在运行")
            serial = (device_serial or "").strip()
            if not serial:
                raise RuntimeError("请先选择手机")
            kind = job_kind if job_kind in ("import", "resolution") else "import"
            root = bootstrap()
            suite_id = datetime.now().strftime("%Y%m%d_%H%M%S")
            # 目录名只用 ASCII，避免 Windows adb/探测在中文路径上变慢或告警。
            label = "resolution" if kind == "resolution" else "import"
            report_dir = output_root() / f"{suite_id}_{label}"
            report_dir.mkdir(parents=True, exist_ok=True)
            (report_dir / "logs").mkdir(exist_ok=True)
            (report_dir / "shots").mkdir(exist_ok=True)

            if kind == "import" and task_config:
                cfg_path = report_dir / "task_config.json"
                cfg_path.write_text(json.dumps(task_config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                config_path = str(cfg_path)
            else:
                config_path = str(work_root() / "task_config.json")

            self._stop = False
            self._state = RunState(
                status="running",
                job_kind=kind,
                message=f"正在启动{label}…",
                started_at=datetime.now().isoformat(timespec="seconds"),
                report_dir=str(report_dir),
            )
            self._emit_status()
            self._worker = threading.Thread(
                target=self._run,
                args=(serial, kind, quick, config_path, resolution_options or {}, report_dir, root),
                daemon=True,
            )
            self._worker.start()
            return self.snapshot()

    def stop(self) -> Dict[str, Any]:
        with self._lock:
            if self._state.status != "running":
                raise RuntimeError("当前没有运行中的任务")
            self._stop = True
            self._state.message = "正在停止…"
            process = self._process
            self._emit_status()
        if process is not None and process.poll() is None:
            try:
                if os.name == "nt":
                    process.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
                    time.sleep(0.4)
                    if process.poll() is None:
                        process.terminate()
                else:
                    process.terminate()
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
        return self.snapshot()

    def _worker_command(
        self,
        *,
        serial: str,
        kind: str,
        quick: bool,
        config_path: str,
        resolution_options: Dict[str, Any],
        report_dir: Path,
        run_slug: str,
    ) -> List[str]:
        if is_frozen():
            cmd = [sys.executable, "--desktop-worker"]
        else:
            cmd = [sys.executable, "-m", "desktop.worker"]
        cmd.extend(
            [
                "--kind",
                kind,
                "--device-serial",
                serial,
                "--report-dir",
                str(report_dir),
                "--run-slug",
                run_slug,
            ]
        )
        if kind == "resolution":
            opts = resolution_options or {}
            orients = opts.get("orientations") or ["landscape", "portrait"]
            medias = opts.get("medias") or ["photo"]
            cmd.extend(
                [
                    "--orientations",
                    ",".join(str(x) for x in orients),
                    "--medias",
                    ",".join(str(x) for x in medias),
                    "--video-seconds",
                    str(max(0, min(600, int(opts.get("video_seconds") or 5)))),
                    "--biz-code",
                    str(opts.get("biz_code") or "ai_glasses"),
                ]
            )
            if opts.get("version_name"):
                cmd.extend(["--version-name", str(opts.get("version_name"))])
        else:
            cmd.extend(["--config", config_path])
            cmd.append("--quick" if quick else "--no-quick")
        return cmd

    def _tail_log(self, path: Path) -> None:
        pos = 0
        while not self._tail_stop.is_set():
            try:
                if path.is_file():
                    with open(path, "r", encoding="utf-8", errors="replace") as fh:
                        fh.seek(pos)
                        chunk = fh.read()
                        pos = fh.tell()
                    if chunk:
                        for line in chunk.splitlines():
                            if line.strip():
                                self._emit_log(line)
            except Exception:
                pass
            time.sleep(0.4)

    def _run(
        self,
        serial: str,
        kind: str,
        quick: bool,
        config_path: str,
        resolution_options: Dict[str, Any],
        report_dir: Path,
        root: Path,
    ) -> None:
        run_slug = datetime.now().strftime("%Y%m%d_%H%M%S")
        console_log = report_dir / "logs" / "run.log"
        cmd = self._worker_command(
            serial=serial,
            kind=kind,
            quick=quick,
            config_path=config_path,
            resolution_options=resolution_options,
            report_dir=report_dir,
            run_slug=run_slug,
        )
        env = os.environ.copy()
        env["GLASSES_DEVICE_SERIAL"] = serial
        env["GLASSES_DATA_DIR"] = str(work_root())
        env["GLASSES_DESKTOP_WORK_ROOT"] = str(root)
        # 避免中文日志在 Windows 管道里变成乱码
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        # Worker must import desktop/glasstest from the code tree, while writing
        # reports into GLASSES_DATA_DIR (may be D:\GlassesTest_data or exe-side).
        code = code_root()
        env["PYTHONPATH"] = str(code) + os.pathsep + env.get("PYTHONPATH", "")
        worker_cwd = str(code if not is_frozen() else root)
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0  # type: ignore[attr-defined]
        if os.name == "nt":
            creationflags |= int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
        self._emit_log(f"开始任务: {' '.join(cmd)}")
        self._tail_stop.clear()
        tailer = threading.Thread(target=self._tail_log, args=(console_log,), daemon=True)
        log_fh = None
        try:
            log_fh = open(console_log, "w", encoding="utf-8", errors="replace")
            process = subprocess.Popen(
                cmd,
                cwd=worker_cwd,
                env=env,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
        except Exception as exc:
            if log_fh is not None:
                try:
                    log_fh.close()
                except Exception:
                    pass
            with self._lock:
                self._state.status = "failed"
                self._state.message = f"启动失败: {exc}"
                self._state.finished_at = datetime.now().isoformat(timespec="seconds")
                self._state.log_path = str(console_log)
                self._state.return_code = -1
                self._state.conclusion = self._state.message
            self._emit_status()
            return

        with self._lock:
            self._process = process
            self._state.log_path = str(console_log)
            self._state.message = "测试执行中…"
        self._emit_status()
        tailer.start()
        code = process.wait()
        self._tail_stop.set()
        tailer.join(timeout=2)
        try:
            if log_fh is not None:
                log_fh.close()
        except Exception:
            pass

        html_path = report_dir / "report.html"
        if not html_path.is_file():
            legacy_html = report_dir / "报告.html"
            if legacy_html.is_file():
                html_path = legacy_html
        json_path = report_dir / "logs" / "result.json"
        if not json_path.is_file():
            for candidate in (
                report_dir / "日志" / "结果.json",
                report_dir / "logs" / "结果.json",
                report_dir / "日志" / "result.json",
            ):
                if candidate.is_file():
                    json_path = candidate
                    break
        if not console_log.is_file():
            legacy_run = report_dir / "日志" / "运行日志.log"
            if legacy_run.is_file():
                console_log = legacy_run
        if kind == "resolution" and not json_path.is_file():
            candidates = sorted(report_dir.glob("resolution_check_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
            if candidates:
                json_path = candidates[0]
                maybe_html = json_path.with_suffix(".html")
                if maybe_html.is_file():
                    html_path = maybe_html
        stopped = self._stop or code < 0
        with self._lock:
            self._process = None
            self._state.return_code = code
            self._state.finished_at = datetime.now().isoformat(timespec="seconds")
            self._state.log_path = str(console_log) if console_log.is_file() else ""
            if html_path.is_file():
                self._state.html_report_path = str(html_path)
            if json_path.is_file():
                self._state.json_report_path = str(json_path)
            if stopped and code != 0:
                self._state.status = "stopped"
                self._state.message = "已停止"
            elif code == 0:
                self._state.status = "succeeded"
                self._state.message = "测试完成"
            else:
                self._state.status = "failed"
                self._state.message = f"测试失败（exit={code}）"
            self._fill_result_locked()
        self._emit_status()
        self._emit_log(f"任务结束: {self._state.status} · {self._state.conclusion or self._state.message}")

    def _fill_result_locked(self) -> None:
        from glasstest.report_bundle import collect_run_artifacts, summarize_conclusion

        report_dir = Path(self._state.report_dir) if self._state.report_dir else None
        json_path = Path(self._state.json_report_path) if self._state.json_report_path else None
        payload: Dict[str, Any] = {}
        if json_path and json_path.is_file():
            try:
                payload = json.loads(json_path.read_text(encoding="utf-8"))
            except Exception:
                payload = {}
        conclusion = summarize_conclusion(payload, self._state.job_kind, self._state.status)
        self._state.conclusion = conclusion
        arts = collect_run_artifacts(report_dir, payload) if report_dir else {"screenshots": [], "log_excerpt": ""}
        self._state.screenshots = list(arts.get("screenshots") or [])
        self._state.log_excerpt = str(arts.get("log_excerpt") or "")
        if self._state.log_path and Path(self._state.log_path).is_file() and not self._state.log_excerpt:
            try:
                lines = Path(self._state.log_path).read_text(encoding="utf-8", errors="replace").splitlines()
                self._state.log_excerpt = "\n".join(lines[-200:])
            except Exception:
                pass


_runner: Optional[DesktopRunner] = None


def get_runner() -> DesktopRunner:
    global _runner
    if _runner is None:
        _runner = DesktopRunner()
    return _runner

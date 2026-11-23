"""Tkinter desktop console for GlassesTest testers."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any, Dict, List, Optional

from desktop.messages import humanize_adb_error, humanize_ui_error, short_text
from desktop.paths import bootstrap, output_root, work_root
from desktop.runner import get_runner


class GlassesTestApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("GlassesTest")
        self.geometry("1040x700")
        self.minsize(900, 620)
        self._devices: List[str] = []
        self._task_rows: List[Dict[str, Any]] = []

        bootstrap()
        self._runner = get_runner()
        self._runner.set_callbacks(self._on_log, self._on_status)

        self._build_ui()
        self.after(200, self.refresh_devices)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        pad = {"padx": 10, "pady": 6}
        top = ttk.Frame(self)
        top.pack(fill="x", **pad)
        ttk.Label(top, text="GlassesTest", font=("Microsoft YaHei UI", 16, "bold")).pack(side="left")
        ttk.Button(top, text="刷新设备", command=self.refresh_devices).pack(side="right", padx=4)
        ttk.Button(top, text="重启 ADB", command=lambda: self.refresh_devices(restart_adb=True)).pack(side="right", padx=4)
        ttk.Button(top, text="打开输出目录", command=self.open_output_dir).pack(side="right", padx=4)

        out_row = ttk.Frame(self)
        out_row.pack(fill="x", padx=10, pady=(0, 4))
        self.output_path_var = tk.StringVar(value=f"输出目录：{output_root()}")
        ttk.Label(out_row, textvariable=self.output_path_var, foreground="#555", wraplength=980).pack(anchor="w")

        body = ttk.Frame(self)
        body.pack(fill="x", **pad)
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)

        left = ttk.Frame(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        right = ttk.LabelFrame(body, text="最终结果")
        right.grid(row=0, column=1, sticky="nsew")

        device = ttk.LabelFrame(left, text="设备")
        device.pack(fill="x", pady=(0, 6))
        row = ttk.Frame(device)
        row.pack(fill="x", padx=8, pady=6)
        ttk.Label(row, text="手机").pack(side="left")
        self.device_var = tk.StringVar()
        self.device_combo = ttk.Combobox(row, textvariable=self.device_var, state="readonly", width=36)
        self.device_combo.pack(side="left", padx=8)
        self.device_tip = ttk.Label(row, text="连接手机后点刷新", foreground="#666", wraplength=360)
        self.device_tip.pack(side="left", padx=8, fill="x", expand=True)

        caps = ttk.LabelFrame(left, text="能力")
        caps.pack(fill="both", expand=True)
        crow = ttk.Frame(caps)
        crow.pack(fill="x", padx=8, pady=6)
        self.kind_var = tk.StringVar(value="import")
        ttk.Radiobutton(crow, text="文件导入", value="import", variable=self.kind_var, command=self._sync_kind).pack(
            side="left", padx=4
        )
        ttk.Radiobutton(
            crow, text="分辨率检查", value="resolution", variable=self.kind_var, command=self._sync_kind
        ).pack(side="left", padx=4)
        self.quick_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(crow, text="快速模式", variable=self.quick_var).pack(side="left", padx=12)

        self.import_frame = ttk.Frame(caps)
        self.res_frame = ttk.Frame(caps)

        irow = ttk.Frame(self.import_frame)
        irow.pack(fill="x", padx=8, pady=4)
        ttk.Label(irow, text="轮回次数").pack(side="left")
        self.cycles_var = tk.StringVar(value="1")
        ttk.Entry(irow, textvariable=self.cycles_var, width=6).pack(side="left", padx=4)
        ttk.Label(irow, text="导入超时(秒)").pack(side="left", padx=(12, 2))
        self.import_timeout_var = tk.StringVar(value="1800")
        ttk.Entry(irow, textvariable=self.import_timeout_var, width=8).pack(side="left")
        ttk.Label(irow, text="卡住超时(秒)").pack(side="left", padx=(12, 2))
        self.stall_timeout_var = tk.StringVar(value="120")
        ttk.Entry(irow, textvariable=self.stall_timeout_var, width=8).pack(side="left")
        ttk.Label(irow, text="重试次数").pack(side="left", padx=(12, 2))
        self.retry_var = tk.StringVar(value="3")
        ttk.Entry(irow, textvariable=self.retry_var, width=6).pack(side="left")

        head = ttk.Frame(self.import_frame)
        head.pack(fill="x", padx=8, pady=(4, 0))
        for text, width in (("任务", 12), ("次数", 8), ("时长(秒)", 10), ("", 8)):
            ttk.Label(head, text=text, width=width).pack(side="left", padx=2)
        self.task_list = ttk.Frame(self.import_frame)
        self.task_list.pack(fill="x", padx=8, pady=4)
        task_btns = ttk.Frame(self.import_frame)
        task_btns.pack(fill="x", padx=8, pady=(0, 6))
        ttk.Button(task_btns, text="添加任务", command=lambda: self._add_task_row()).pack(side="left")
        ttk.Label(task_btns, text="可设类型 / 次数 / 时长", foreground="#666").pack(side="left", padx=8)

        rrow = ttk.Frame(self.res_frame)
        rrow.pack(fill="x", padx=8, pady=4)
        self.orient_l = tk.BooleanVar(value=True)
        self.orient_p = tk.BooleanVar(value=True)
        self.media_photo = tk.BooleanVar(value=True)
        self.media_video = tk.BooleanVar(value=False)
        ttk.Checkbutton(rrow, text="横屏", variable=self.orient_l).pack(side="left", padx=4)
        ttk.Checkbutton(rrow, text="竖屏", variable=self.orient_p).pack(side="left", padx=4)
        ttk.Checkbutton(rrow, text="照片", variable=self.media_photo).pack(side="left", padx=4)
        ttk.Checkbutton(rrow, text="视频", variable=self.media_video).pack(side="left", padx=4)
        ttk.Label(rrow, text="视频时长(秒)").pack(side="left", padx=(12, 2))
        self.res_video_seconds = tk.StringVar(value="5")
        ttk.Entry(rrow, textvariable=self.res_video_seconds, width=6).pack(side="left")

        self.result_title_var = tk.StringVar(value="尚未运行")
        self.result_detail_var = tk.StringVar(value="跑完后这里显示成功/失败摘要。\n详细报告请点「打开报告」或「打开输出目录」。")
        ttk.Label(
            right,
            textvariable=self.result_title_var,
            font=("Microsoft YaHei UI", 18, "bold"),
            wraplength=280,
        ).pack(anchor="w", padx=12, pady=(14, 8))
        ttk.Label(
            right,
            textvariable=self.result_detail_var,
            foreground="#444",
            wraplength=280,
            justify="left",
        ).pack(anchor="nw", padx=12, pady=(0, 12), fill="both", expand=True)

        actions = ttk.Frame(self)
        actions.pack(fill="x", **pad)
        self.btn_start = ttk.Button(actions, text="开始测试", command=self.start_run)
        self.btn_start.pack(side="left", padx=4)
        self.btn_stop = ttk.Button(actions, text="停止", command=self.stop_run, state="disabled")
        self.btn_stop.pack(side="left", padx=4)
        self.btn_report = ttk.Button(actions, text="打开报告", command=self.open_report, state="disabled")
        self.btn_report.pack(side="left", padx=4)
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(actions, textvariable=self.status_var, wraplength=620).pack(side="left", padx=12, fill="x", expand=True)

        log_box = ttk.Labelframe(self, text="实时日志")
        log_box.pack(fill="both", expand=True, **pad)
        self.log_text = tk.Text(log_box, height=18, wrap="word", font=("Consolas", 10))
        self.log_text.pack(fill="both", expand=True, padx=6, pady=6)
        self.log_text.configure(state="disabled")

        self._sync_kind()
        self._load_default_tasks()

    def _clear_task_rows(self) -> None:
        for item in self._task_rows:
            try:
                item["frame"].destroy()
            except Exception:
                pass
        self._task_rows.clear()

    def _add_task_row(self, task: Optional[Dict[str, Any]] = None) -> None:
        task = task or {"task": "photo", "times": 1, "duration_seconds": 0}
        frame = ttk.Frame(self.task_list)
        frame.pack(fill="x", pady=2)
        kind_var = tk.StringVar(value=str(task.get("task") or "photo"))
        times_var = tk.StringVar(value=str(task.get("times") or 1))
        duration_var = tk.StringVar(value=str(task.get("duration_seconds") or 0))
        kind = ttk.Combobox(frame, textvariable=kind_var, values=("photo", "audio", "video"), state="readonly", width=10)
        kind.pack(side="left", padx=2)
        ttk.Entry(frame, textvariable=times_var, width=8).pack(side="left", padx=2)
        ttk.Entry(frame, textvariable=duration_var, width=10).pack(side="left", padx=2)

        def remove() -> None:
            if len(self._task_rows) <= 1:
                messagebox.showwarning("提示", "至少保留一条任务")
                return
            target = None
            for item in self._task_rows:
                if item["frame"] is frame:
                    target = item
                    break
            if target:
                self._task_rows.remove(target)
            frame.destroy()

        ttk.Button(frame, text="删除", command=remove, width=6).pack(side="left", padx=4)
        self._task_rows.append(
            {"frame": frame, "task": kind_var, "times": times_var, "duration": duration_var}
        )

    def _load_default_tasks(self) -> None:
        self._clear_task_rows()
        cfg_path = work_root() / "task_config.json"
        data: Dict[str, Any] = {}
        if cfg_path.is_file():
            try:
                data = json.loads(cfg_path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        self.cycles_var.set(str(data.get("cycles") or 1))
        self.import_timeout_var.set(str(int(float(data.get("import_timeout_seconds") or 1800))))
        self.stall_timeout_var.set(str(int(float(data.get("import_stall_timeout_seconds") or 120))))
        self.retry_var.set(str(int(data.get("import_retry_limit") or 3)))
        tasks = data.get("tasks") if isinstance(data.get("tasks"), list) else []
        if not tasks:
            tasks = [
                {"task": "photo", "times": 1, "duration_seconds": 0},
                {"task": "audio", "times": 1, "duration_seconds": 5},
                {"task": "video", "times": 1, "duration_seconds": 5},
            ]
        for task in tasks:
            if isinstance(task, dict):
                self._add_task_row(task)

    def _sync_kind(self) -> None:
        if self.kind_var.get() == "resolution":
            self.import_frame.pack_forget()
            self.res_frame.pack(fill="x")
            self.btn_start.configure(text="开始分辨率检查")
        else:
            self.res_frame.pack_forget()
            self.import_frame.pack(fill="x")
            self.btn_start.configure(text="开始测试")

    def _append_log(self, line: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line.rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _on_log(self, line: str) -> None:
        self.after(0, lambda: self._append_log(line))

    def _on_status(self, state: Dict[str, Any]) -> None:
        self.after(0, lambda: self._apply_status(state))

    def _apply_status(self, state: Dict[str, Any]) -> None:
        status = state.get("status") or ""
        message = humanize_ui_error(state.get("message") or "")
        status_label = {
            "running": "进行中",
            "succeeded": "成功",
            "failed": "失败",
            "stopped": "已停止",
            "idle": "就绪",
        }.get(status, status)
        self.status_var.set(short_text(f"{status_label} · {message}", 90))
        running = status == "running"
        self.btn_start.configure(state="disabled" if running else "normal")
        self.btn_stop.configure(state="normal" if running else "disabled")
        has_report = bool(state.get("html_report_path"))
        self.btn_report.configure(state="normal" if has_report else "disabled")

        if status == "running":
            self.result_title_var.set("进行中")
            self.result_detail_var.set(message or "测试执行中…")
            return
        if status in ("succeeded", "failed", "stopped"):
            conclusion = str(state.get("conclusion") or message or status_label)
            title = "成功" if status == "succeeded" else ("已停止" if status == "stopped" else "失败")
            # Prefer explicit success/fail wording from conclusion when present.
            if conclusion.startswith("通过"):
                title = "成功"
            elif conclusion.startswith("失败"):
                title = "失败"
            self.result_title_var.set(title)
            detail_lines = [conclusion]
            report_dir = str(state.get("report_dir") or "").strip()
            if report_dir:
                detail_lines.append(f"本次目录：{Path(report_dir).name}")
            detail_lines.append("详细报告请点「打开报告」或「打开输出目录」。")
            self.result_detail_var.set("\n".join(detail_lines))

    def _set_device_feedback(self, tip: str, *, status: str = "", log_detail: str = "") -> None:
        clean = short_text(tip or "无设备", 90)
        self.device_tip.configure(text=clean)
        self.status_var.set(short_text(status or clean, 90))
        if log_detail.strip():
            self._append_log(log_detail.strip())

    def refresh_devices(self, restart_adb: bool = False) -> None:
        self.status_var.set("正在刷新设备…" if not restart_adb else "正在重启 ADB…")

        def work() -> None:
            tip = ""
            status = ""
            detail = ""
            devices: List[str] = []
            try:
                from glasstest.phone_operations import PhoneOperations

                phone = PhoneOperations()
                if restart_adb:
                    restart_info = phone.restart_adb_server()
                    if restart_info.get("errors"):
                        detail = "重启 ADB 细节: " + " | ".join(str(x) for x in restart_info["errors"])
                try:
                    devices = phone.list_connected_devices()
                    states = phone.list_adb_device_states()
                except Exception as exc:
                    # Daemon may still be recovering after restart; one more soft probe.
                    states = phone.list_adb_device_states()
                    if not states:
                        raise
                    devices = [s["serial"] for s in states if s.get("state") == "device"]
                    if not devices:
                        raise
                    detail = (detail + "\n" if detail else "") + f"ADB 原始信息: {exc}"

                if devices:
                    tip = f"在线 {len(devices)} 台"
                    status = f"已发现 {len(devices)} 台设备"
                    if restart_adb:
                        status = f"ADB 已重启 · {status}"
                else:
                    unauthorized = [s for s in states if s.get("state") == "unauthorized"]
                    offline = [s for s in states if s.get("state") in ("offline", "no permissions")]
                    if unauthorized:
                        tip = "请在手机上点允许 USB 调试后刷新"
                    elif offline:
                        tip = "设备 offline：换线/换口或重开 USB 调试后刷新"
                    elif states:
                        tip = "设备状态异常：" + ", ".join(
                            f"{s.get('serial')}:{s.get('state')}" for s in states
                        )
                    else:
                        tip = "未发现设备，请检查 USB 调试 / 数据线"
                    status = tip
            except Exception as exc:
                tip = humanize_adb_error(exc)
                status = tip
                detail = (detail + "\n" if detail else "") + f"ADB 原始信息:\n{exc}"

            def apply() -> None:
                self._devices = devices
                self.device_combo["values"] = devices
                if devices:
                    current = self.device_var.get()
                    self.device_var.set(current if current in devices else devices[0])
                else:
                    self.device_var.set("")
                self._set_device_feedback(tip, status=status, log_detail=detail)

            self.after(0, apply)

        threading.Thread(target=work, daemon=True).start()

    def _read_task_config(self) -> Dict[str, Any]:
        tasks = []
        for item in self._task_rows:
            name = str(item["task"].get() or "photo").strip().lower()
            if name not in ("photo", "audio", "video"):
                continue
            times = max(1, int(float(item["times"].get() or 1)))
            duration = max(0.0, float(item["duration"].get() or 0))
            tasks.append({"task": name, "times": times, "duration_seconds": duration})
        if not tasks:
            raise RuntimeError("请至少配置一条任务（photo/audio/video）")
        return {
            "cycles": max(1, int(float(self.cycles_var.get() or 1))),
            "import_timeout_seconds": max(1.0, float(self.import_timeout_var.get() or 1800)),
            "import_stall_timeout_seconds": max(1.0, float(self.stall_timeout_var.get() or 120)),
            "import_poll_interval_seconds": 1.0,
            "import_retry_limit": max(1, int(float(self.retry_var.get() or 3))),
            "import_retry_backoff_seconds": 3.0,
            "import_retry_relaunch_app": True,
            "tasks": tasks,
        }

    def _read_resolution_options(self) -> Dict[str, Any]:
        orientations = []
        if self.orient_l.get():
            orientations.append("landscape")
        if self.orient_p.get():
            orientations.append("portrait")
        medias = []
        if self.media_photo.get():
            medias.append("photo")
        if self.media_video.get():
            medias.append("video")
        if not orientations:
            raise RuntimeError("请至少选择横屏或竖屏")
        if not medias:
            raise RuntimeError("请至少选择照片或视频")
        return {
            "orientations": orientations,
            "medias": medias,
            "video_seconds": max(0, int(float(self.res_video_seconds.get() or 5))),
            "biz_code": "ai_glasses",
            "version_name": "",
        }

    def start_run(self) -> None:
        serial = self.device_var.get().strip()
        if not serial:
            messagebox.showwarning("提示", "请先刷新并选择手机")
            return
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")
        self.result_title_var.set("进行中")
        self.result_detail_var.set("测试执行中…")
        try:
            kind = self.kind_var.get()
            kwargs: Dict[str, Any] = {
                "device_serial": serial,
                "job_kind": kind,
                "quick": bool(self.quick_var.get()),
            }
            if kind == "import":
                kwargs["task_config"] = self._read_task_config()
            else:
                kwargs["resolution_options"] = self._read_resolution_options()
            self._runner.start(**kwargs)
        except Exception as exc:
            tip = humanize_ui_error(exc)
            self.status_var.set(tip)
            self._append_log(f"无法开始: {exc}")
            messagebox.showerror("无法开始", tip)

    def stop_run(self) -> None:
        try:
            self._runner.stop()
        except Exception as exc:
            tip = humanize_ui_error(exc)
            self.status_var.set(tip)
            self._append_log(f"停止失败: {exc}")
            messagebox.showerror("停止失败", tip)

    def _copy_path_to_clipboard(self, text: str) -> None:
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.update_idletasks()
        except Exception:
            pass

    @staticmethod
    def _minimal_system_env() -> dict:
        """Only system vars — never inherit PyInstaller _MEI PATH/DLL dirs."""
        system_root = os.environ.get("SystemRoot") or r"C:\Windows"
        system32 = os.path.join(system_root, "System32")
        env = {
            "SystemRoot": system_root,
            "windir": system_root,
            "ComSpec": os.path.join(system32, "cmd.exe"),
            "PATH": f"{system32};{system_root}",
            "TEMP": os.environ.get("TEMP") or system32,
            "TMP": os.environ.get("TMP") or system32,
            "USERPROFILE": os.environ.get("USERPROFILE") or "",
            "USERNAME": os.environ.get("USERNAME") or "",
            "USERDOMAIN": os.environ.get("USERDOMAIN") or "",
            "APPDATA": os.environ.get("APPDATA") or "",
            "LOCALAPPDATA": os.environ.get("LOCALAPPDATA") or "",
            "HOMEDRIVE": os.environ.get("HOMEDRIVE") or "",
            "HOMEPATH": os.environ.get("HOMEPATH") or "",
            "SESSIONNAME": os.environ.get("SESSIONNAME") or "Console",
        }
        return {key: value for key, value in env.items() if value}

    def _reveal_path(self, target: Path) -> None:
        """Open folder/file without spawning explorer.exe from this process.

        Frozen onefile + direct explorer.exe = 0xc0000142. Ask the existing
        Windows shell via COM instead (through system PowerShell, minimal env).
        """
        path = Path(target).resolve()
        if not path.exists():
            raise FileNotFoundError(str(path))
        text = str(path)
        if os.name != "nt":
            subprocess.Popen(["xdg-open", text], shell=False)
            return

        system_root = os.environ.get("SystemRoot") or r"C:\Windows"
        system32 = os.path.join(system_root, "System32")
        powershell = os.path.join(system_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        if not os.path.isfile(powershell):
            powershell = os.path.join(system32, "WindowsPowerShell", "v1.0", "powershell.exe")
        # Escape single quotes for PowerShell single-quoted string.
        lit = text.replace("'", "''")
        if path.is_dir():
            ps_cmd = f"(New-Object -ComObject Shell.Application).Explore('{lit}')"
        else:
            ps_cmd = (
                f"$s=New-Object -ComObject Shell.Application; "
                f"$s.ShellExecute('{lit}')"
            )
        flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)) | 0x00000008 | 0x00000200
        subprocess.Popen(
            [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-Command", ps_cmd],
            cwd=system32,
            env=self._minimal_system_env(),
            shell=False,
            close_fds=True,
            creationflags=flags,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def open_report(self) -> None:
        state = self._runner.snapshot()
        path = state.get("html_report_path") or ""
        if not path or not Path(path).is_file():
            messagebox.showinfo("提示", "还没有报告文件")
            return
        file_path = Path(path)
        try:
            import webbrowser

            webbrowser.open(file_path.resolve().as_uri())
            return
        except Exception:
            pass
        try:
            self._reveal_path(file_path)
        except Exception as exc:
            self._copy_path_to_clipboard(str(file_path))
            self._append_log(f"打开报告失败: {exc}")
            messagebox.showinfo(
                "打开报告",
                f"无法自动打开报告，路径已复制到剪贴板：\n\n{file_path}",
            )

    def open_output_dir(self) -> None:
        state = self._runner.snapshot()
        report_dir = str(state.get("report_dir") or "").strip()
        if report_dir and Path(report_dir).is_dir():
            path = Path(report_dir)
        else:
            path = output_root()
        path.mkdir(parents=True, exist_ok=True)
        text = str(path.resolve())
        # 先复制路径，保证就算系统弹窗失败也有用。
        self._copy_path_to_clipboard(text)
        try:
            self._reveal_path(path)
        except Exception as exc:
            self._append_log(f"打开输出目录失败: {exc}")
            messagebox.showinfo(
                "输出目录",
                f"无法自动打开，路径已复制到剪贴板：\n\n{text}\n\n请打开资源管理器，Ctrl+V 粘贴到地址栏。",
            )

    def _on_close(self) -> None:
        if self._runner.is_running():
            if not messagebox.askyesno("退出", "测试仍在运行，确定退出并停止？"):
                return
            try:
                self._runner.stop()
            except Exception:
                pass
        self.destroy()


def main() -> None:
    app = GlassesTestApp()
    app.mainloop()


if __name__ == "__main__":
    main()

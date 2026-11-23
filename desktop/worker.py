"""CLI worker entry used by the desktop GUI (source and frozen exe)."""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="GlassesTest desktop worker")
    parser.add_argument("--kind", choices=("import", "resolution"), required=True)
    parser.add_argument("--device-serial", required=True)
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--run-slug", default="")
    parser.add_argument("--config", default="task_config.json")
    parser.add_argument("--quick", action="store_true", default=True)
    parser.add_argument("--no-quick", action="store_false", dest="quick")
    parser.add_argument("--orientations", default="landscape,portrait")
    parser.add_argument("--medias", default="photo")
    parser.add_argument("--video-seconds", type=int, default=5)
    parser.add_argument("--biz-code", default="ai_glasses")
    parser.add_argument("--version-name", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    from desktop.paths import bootstrap

    bootstrap()
    args = build_parser().parse_args(argv)
    serial = args.device_serial.strip()
    if args.kind == "resolution":
        from glasstest.run_resolution_check import run_resolution_check

        orients = [x.strip() for x in str(args.orientations).split(",") if x.strip()]
        medias = [x.strip() for x in str(args.medias).split(",") if x.strip()]
        report = run_resolution_check(
            report_dir=args.report_dir,
            run_slug=args.run_slug,
            device_serial=serial,
            orientations=orients or ["landscape", "portrait"],
            medias=medias or ["photo"],
            video_seconds=args.video_seconds,
            biz_code=args.biz_code,
            version_name_override=args.version_name,
            update_latest=False,
        )
        summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
        failed = int(summary.get("failed") or 0)
        fatal = str(report.get("fatal_error") or "").strip()
        return 1 if failed or fatal else 0

    from glasstest.run_complex_flow import run_complex_flow

    report = run_complex_flow(
        args.config,
        quick=bool(args.quick),
        report_dir=args.report_dir,
        run_slug=args.run_slug,
        device_serial=serial,
        update_latest=False,
    )
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    failed = int(summary.get("cycles_import_failed") or 0)
    total = int(summary.get("cycles_total") or report.get("cycles") or 0)
    success = int(summary.get("cycles_import_success") or 0)
    if failed > 0:
        return 1
    if total > 0 and success < total:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

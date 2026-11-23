"""python -m desktop"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--desktop-worker":
        from desktop.worker import main as worker_main

        return int(worker_main(argv[1:]))
    from desktop.app import main as app_main

    app_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

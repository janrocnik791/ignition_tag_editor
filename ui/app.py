"""Zagonska tocka namizne aplikacije (mejnik C2)."""

from __future__ import annotations

import argparse
import sys
from typing import Optional, Sequence

from PySide6.QtWidgets import QApplication

from .main_window import MainWindow


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ignition Tag Editor")
    parser.add_argument(
        "project",
        nargs="?",
        help="Pot do mape projekta ali project.sqlite (neobvezno).",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("Ignition Tag Editor")

    window = MainWindow()
    if args.project:
        window.open_project_path(args.project)
    if args.smoke_test:
        window.close()
        return 0
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

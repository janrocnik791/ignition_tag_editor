"""Read-only indekser Ignition tag izvozov v SQLite.

Ta paket NE spreminja datotek v ``data/raw``. Odpira jih izkljucno za branje
in gradi indeks v ``data/generated``.
"""

from .build import build_index
from .model import IndexedFile, TagRow, classify_file

__all__ = ["build_index", "IndexedFile", "TagRow", "classify_file"]

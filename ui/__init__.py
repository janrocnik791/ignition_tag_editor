"""PySide6 uporabniski vmesnik za Ignition Tag Editor.

Jedrni paket ``editor`` ostaja brez Qt odvisnosti. Ta paket vsebuje samo
predstavitveni sloj in ga je zato mogoce razvijati neodvisno od headless storitev.
"""

from .main_window import MainWindow  # noqa: F401

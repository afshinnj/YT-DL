"""Download history."""
from .store import HistoryEntry, add, clear, load

__all__ = ("HistoryEntry", "load", "add", "clear")

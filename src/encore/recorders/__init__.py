"""Recorders: the sources of IR events.

Only the web layer exists so far. The desktop layer (UI Automation) and the native-API
layer (Excel, Outlook) come later and write into the same IR.
"""

from __future__ import annotations

from .web import RecorderError, record

__all__ = ["RecorderError", "record"]

"""Code generators, driven by the IR.

One generator per target. Only the Playwright/Python one exists so far; the pywinauto
generator lands in v0.3 and reads exactly the same IR - that is what the IR is for.
"""

from __future__ import annotations

from .playwright_py import GenOptions, generate

__all__ = ["GenOptions", "generate"]

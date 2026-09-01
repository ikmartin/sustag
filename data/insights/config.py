"""Where the audit writes, and the stores it walks."""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent
REPORT = HERE / "report.md"
IMAGES = HERE / "images"
THEMES = HERE / "themes.toml"

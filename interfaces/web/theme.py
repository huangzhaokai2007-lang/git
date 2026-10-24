"""Shared presentation tokens; no business state or data access."""
from pathlib import Path

CSS = "<style>" + Path(__file__).with_name("theme.css").read_text(encoding="utf-8") + "</style>"

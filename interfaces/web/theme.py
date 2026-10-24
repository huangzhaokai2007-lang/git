"""Shared presentation tokens; no business state or data access."""
from pathlib import Path

def load_css() -> str:
    """Read current styles on each page render, including CSS-only edits."""
    return "<style>" + Path(__file__).with_name("theme.css").read_text(encoding="utf-8") + "</style>"


CSS = load_css()

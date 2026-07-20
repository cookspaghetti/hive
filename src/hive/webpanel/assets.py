"""Shared web-panel visual assets."""

from pathlib import Path

RESOURCE_DIR = Path(__file__).parent / "resources"
LOGO_PATH = RESOURCE_DIR / "logo.png"
PANEL_CSS_PATH = RESOURCE_DIR / "panel.css"
PANEL_JS_PATH = RESOURCE_DIR / "panel.js"
PANEL_TEMPLATE_PATH = RESOURCE_DIR / "panel.html"
LOGO_HTML = '<span class="brandmark"><img src="/logo.png" alt=""></span>'

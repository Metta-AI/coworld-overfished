"""Build the single-file replay viewer from the `viewer/` sources.

The same HTML serves three surfaces: the static replay bundle, the live global viewer in the game
container, and the container replay fallback. Inlining CSS and JS keeps every surface one request.
"""

from __future__ import annotations

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_VIEWER_DIR = PACKAGE_DIR.parent.parent / "viewer"


def build_index_html(source_dir: Path) -> str:
    html = (source_dir / "index.html").read_text(encoding="utf-8")
    css = (source_dir / "viewer.css").read_text(encoding="utf-8")
    js = (source_dir / "viewer.js").read_text(encoding="utf-8")
    html = html.replace('<link rel="stylesheet" href="viewer.css">', f"<style>\n{css}\n</style>")
    html = html.replace('<script src="viewer.js"></script>', f"<script>\n{js}\n</script>")
    if "viewer.css" in html or "viewer.js" in html:
        raise RuntimeError("viewer/index.html must reference viewer.css and viewer.js exactly once each")
    return html


def write_bundle(source_dir: Path, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "index.html"
    target.write_text(build_index_html(source_dir), encoding="utf-8")
    return target

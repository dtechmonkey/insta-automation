"""Render slide templates (HTML/CSS) to 1080x1350 JPEGs with a headless browser."""
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.sync_api import sync_playwright

WIDTH, HEIGHT = 1080, 1350  # Instagram 4:5 portrait


def render_slides(slides, templates_dir, out_dir, base_context):
    env = Environment(loader=FileSystemLoader(str(templates_dir)), autoescape=select_autoescape(["html"]))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})
        for i, slide in enumerate(slides, 1):
            html = env.get_template(slide["template"]).render(**base_context, **slide)
            page.set_content(html, wait_until="networkidle")
            page.evaluate("document.fonts.ready")
            page.evaluate("window.fitText && window.fitText()")
            path = out_dir / f"{i:02d}.jpg"
            page.screenshot(path=str(path), type="jpeg", quality=92)
            paths.append(path)
        browser.close()
    return paths

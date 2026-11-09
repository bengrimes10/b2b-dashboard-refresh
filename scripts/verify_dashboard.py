#!/usr/bin/env python3
"""Open the built HTML in Chromium and verify the four report tabs."""
import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument("html", type=Path)
args = parser.parse_args()
assert args.html.exists(), args.html
errors = []
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    page.goto(args.html.resolve().as_uri())
    for name in ("Flows", "New enrollees", "Warm leads", "B2B sales"):
        page.get_by_role("tab", name=name).click()
        assert page.locator("#main").inner_text().strip(), f"Empty {name} tab"
    show_more = page.get_by_text("Show 25 more", exact=False)
    if show_more.count():
        show_more.first.click()
    screenshot = args.html.parent / "B2B_sales_verification.png"
    page.screenshot(path=str(screenshot), full_page=True)
    browser.close()
if errors:
    raise RuntimeError("Browser errors: " + "; ".join(errors))
print("Verified four tabs; screenshot:", screenshot)

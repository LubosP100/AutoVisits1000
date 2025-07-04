"""
google_headless_rotation.py  — updated July 2025

Automates a headless Chromium session to open Google, type a search query, open the first result,
and (optionally) rotate the IP address via a running Tor instance **without reopening the control
connection each time**.

Usage remains the same:
    python google_headless_rotation.py "openai gpt-4o" --iterations 5 --tor

Key improvements
----------------
✓ Re-uses a single authenticated Tor `Controller` across all iterations → no more WinError 10061.
✓ Increases Google load timeout to 45 s (Tor can be slow).
✓ Extra logging and graceful shutdown of the Tor controller.
"""

import argparse
import os
import time
from contextlib import contextmanager
from typing import Optional

from playwright.sync_api import (
    sync_playwright,
    TimeoutError as PlaywrightTimeoutError,
)

# Optional Tor integration -----------------------------------------------------
try:
    from stem import Signal
    from stem.control import Controller
except ImportError:  # Stem not installed or not needed if --tor isn't used
    Controller = None  # type: ignore

################################################################################
# Helper utilities                                                              #
################################################################################

@contextmanager
def new_browser(playwright, proxy: Optional[str]):
    """Launch a fresh headless Chromium context behind *proxy* (if provided)."""
    browser = playwright.chromium.launch(
        headless=True,
        proxy={"server": proxy} if proxy else None,
    )
    context = browser.new_context()
    try:
        yield context
    finally:
        context.close()
        browser.close()


def run_search(context, query: str):
    """Perform a single Google search and click the first result."""
    page = context.new_page()

    # Google can be slow over Tor – give it 45 s.
    page.goto("https://www.google.com", timeout=45_000)

    # EU consent banner? Try to reject cookies.
    try:
        consent = page.query_selector("button[aria-label='Reject all']")
        if consent:
            consent.click()
    except PlaywrightTimeoutError:
        pass  # Ignore if not present

    # Fill query and search
    page.fill("input[name='q']", query)
    page.press("input[name='q']", "Enter")

    # Wait for at least one result <h3>
    page.wait_for_selector("h3", timeout=20_000)
    first_result = page.query_selector("h3")
    if first_result:
        first_result.click()
    else:
        print("[Warn] No search results found – skipping click.")

    # Give target page a moment to load (optional)
    time.sleep(3)

################################################################################
# Main routine                                                                  #
################################################################################


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Headless Google search with optional Tor IP rotation."
    )
    parser.add_argument("query", help="Search query text")
    parser.add_argument(
        "--iterations", type=int, default=2, help="Repeat count (default: 2)"
    )
    parser.add_argument(
        "--proxy-list",
        nargs="*",
        default=[],
        help="Space-separated list of proxy URLs (e.g. socks5://host:port)",
    )
    parser.add_argument(
        "--tor",
        action="store_true",
        help="Route traffic through Tor and renew circuit each iteration",
    )
    args = parser.parse_args(argv)

    # --------------------------------------------------------------------- Tor
    if args.tor:
        if Controller is None:
            raise RuntimeError(
                "Missing 'stem' library – install with `pip install stem` or remove --tor"
            )
        tor_pass = os.getenv("TOR_CONTROL_PASSWD")
        if not tor_pass:
            raise RuntimeError("Environment variable TOR_CONTROL_PASSWD is not set")

        try:
            controller = Controller.from_port(port=9051)
            controller.authenticate(password=tor_pass)
            print("✔ Tor control port authenticated")
        except Exception as e:
            raise RuntimeError(f"Could not authenticate to Tor control port: {e}") from e
    else:
        controller = None

    # Build proxy list for iterations
    proxies: list[Optional[str]]
    if args.tor:
        proxies = ["socks5://127.0.0.1:9050"] * args.iterations
    else:
        proxies = args.proxy_list or [None]

    # ------------------------------------------------------------- Playwright
    with sync_playwright() as p:
        for i in range(args.iterations):
            proxy = proxies[i % len(proxies)]

            # Rotate IP if using Tor
            if controller is not None:
                try:
                    controller.signal(Signal.NEWNYM)
                    print("✔ NEWNYM signal sent – waiting 5 s for new circuit …")
                    time.sleep(5)
                except Exception as e:
                    print(f"[Error] NEWNYM failed: {e}")

            print(f"\n--- Iteration {i + 1}/{args.iterations} (proxy = {proxy}) ---")
            try:
                with new_browser(p, proxy) as ctx:
                    run_search(ctx, args.query)
            except PlaywrightTimeoutError as e:
                print(f"[Error] Timeout while interacting with Google: {e}")

    # Clean up
    if controller is not None:
        controller.close()
        print("✔ Tor controller closed")


if __name__ == "__main__":
    main()


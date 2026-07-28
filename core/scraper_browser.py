# core/scraper_browser.py
"""Launcher for the Selenium browser used by the Telegram scraper.

We drive ``chrome-headless-shell`` rather than a full browser: on this
workload it costs ~34% less memory than headless Chrome for the same scrape
(see docs/TELEGRAM_SCRAPER_BROWSER_SELECTION.md). Install it with
``scripts/install_scraper_browser.sh``.

The one sharp edge is that chrome-headless-shell cannot be launched by
chromedriver when a persistent ``--user-data-dir`` is in play. ChromeDriver
only appends its initial ``data:,`` page when it creates its own throwaway
profile; with a caller-supplied profile it appends nothing, the shell opens
zero tabs, and session creation dies with "unable to discover open pages".
ChromeDriver also strips bare URL arguments, so the initial page cannot be
injected through ``ChromeOptions``.

So we start the browser ourselves — with the profile *and* an explicit
``about:blank`` — and attach Selenium to the running process over
``debuggerAddress``. Persistent profiles matter here because a Telegram Web
login lives in localStorage and IndexedDB; without them every restart means
re-scanning a QR code.

Each Telegram account needs its own profile directory. Accounts must never
share one: Telegram Web keys its session off that storage, so two accounts
pointed at the same profile will clobber each other's login.

    from core.scraper_browser import ScraperBrowser

    with ScraperBrowser(profile_dir="/var/lib/tg-scraper/account1") as browser:
        browser.driver.get("https://web.telegram.org/k/")
        ...
"""
import json
import logging
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from selenium import webdriver
from selenium.webdriver.chrome.service import Service

logger = logging.getLogger(__name__)

DEFAULT_INSTALL_DIR = "/opt/chrome-for-testing"
STARTUP_TIMEOUT = 60.0
SHUTDOWN_TIMEOUT = 20.0

# Flags that cut memory and background chatter without changing what the page
# renders. Deliberately excludes --single-process: it saves a little memory but
# makes renderer crashes take the whole browser down, which is a bad trade for a
# long-running scraper.
LEAN_FLAGS = (
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-client-side-phishing-detection",
    "--disable-component-update",
    "--disable-domain-reliability",
    "--disable-hang-monitor",
    "--disable-sync",
    "--disable-translate",
    "--disable-breakpad",
    "--no-pings",
    "--no-service-autorun",
    "--mute-audio",
    "--password-store=basic",
    "--disable-features=Translate,BackForwardCache,MediaRouter,OptimizationHints,"
    "AcceptCHFrame,CalculateNativeWinOcclusion,InterestFeedContentSuggestions",
)


class BrowserNotInstalled(RuntimeError):
    """chrome-headless-shell or chromedriver is missing from the install dir."""


class BrowserStartupError(RuntimeError):
    """The browser started but never exposed a usable DevTools page target."""


@dataclass(frozen=True)
class BrowserPaths:
    shell: Path
    driver: Path


def resolve_paths(install_dir: Optional[str] = None) -> BrowserPaths:
    """Locate the installed browser and driver, or explain how to install them."""
    root = Path(install_dir or os.environ.get("SCRAPER_BROWSER_DIR")
                or DEFAULT_INSTALL_DIR)
    paths = BrowserPaths(
        shell=root / "chrome-headless-shell-linux64" / "chrome-headless-shell",
        driver=root / "chromedriver-linux64" / "chromedriver",
    )
    missing = [str(p) for p in (paths.shell, paths.driver) if not p.is_file()]
    if missing:
        raise BrowserNotInstalled(
            f"missing {', '.join(missing)} — run scripts/install_scraper_browser.sh"
            f" (or set SCRAPER_BROWSER_DIR if it is installed elsewhere)"
        )
    return paths


def _localhost_opener() -> urllib.request.OpenerDirector:
    """An opener that ignores HTTP(S)_PROXY, so DevTools polling stays local."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


class ScraperBrowser:
    """A chrome-headless-shell process with a persistent profile, plus a driver.

    Args:
        profile_dir: per-account profile. Created if absent, reused across runs.
        install_dir: overrides ``SCRAPER_BROWSER_DIR`` / the default location.
        extra_flags: appended after ``LEAN_FLAGS``; later flags win.
        user_agent: override the UA, which otherwise advertises "HeadlessChrome".
        window_size: viewport, as ``(width, height)``.
        load_images: leave False to skip image decoding — the single biggest
            memory saving on a media-heavy chat.
        startup_timeout: seconds to wait for a DevTools page target.
    """

    def __init__(
        self,
        profile_dir: str,
        install_dir: Optional[str] = None,
        extra_flags: Iterable[str] = (),
        user_agent: Optional[str] = None,
        window_size: tuple = (1280, 900),
        load_images: bool = False,
        startup_timeout: float = STARTUP_TIMEOUT,
    ):
        self.profile_dir = Path(profile_dir)
        self.paths = resolve_paths(install_dir)
        self.extra_flags = tuple(extra_flags)
        self.user_agent = user_agent
        self.window_size = window_size
        self.load_images = load_images
        self.startup_timeout = startup_timeout

        self._proc: Optional[subprocess.Popen] = None
        self._driver: Optional[webdriver.Chrome] = None
        self._port: Optional[int] = None

    @property
    def driver(self) -> webdriver.Chrome:
        if self._driver is None:
            raise RuntimeError("browser is not started; call start() first")
        return self._driver

    @property
    def devtools_port(self) -> Optional[int]:
        return self._port

    def _build_command(self) -> list:
        flags = list(LEAN_FLAGS)
        flags.append(f"--window-size={self.window_size[0]},{self.window_size[1]}")
        if not self.load_images:
            flags.append("--blink-settings=imagesEnabled=false")
        if self.user_agent:
            flags.append(f"--user-agent={self.user_agent}")
        flags.extend(self.extra_flags)
        return [
            str(self.paths.shell),
            *flags,
            f"--user-data-dir={self.profile_dir}",
            # Port 0 lets the browser pick a free port and write it to
            # DevToolsActivePort, which avoids racing another process for a
            # port we picked ourselves.
            "--remote-debugging-port=0",
            # Required: without an explicit page the shell opens no tab and
            # chromedriver cannot attach. See the module docstring.
            "about:blank",
        ]

    def _read_devtools_port(self) -> Optional[int]:
        active = self.profile_dir / "DevToolsActivePort"
        try:
            first = active.read_text().splitlines()[0].strip()
            return int(first)
        except (OSError, IndexError, ValueError):
            return None

    def _wait_for_page_target(self) -> int:
        """Block until DevTools reports a page target; return its port."""
        opener = _localhost_opener()
        deadline = time.monotonic() + self.startup_timeout
        port = None

        while time.monotonic() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                raise BrowserStartupError(
                    f"browser exited during startup with code {self._proc.returncode}"
                )
            if port is None:
                port = self._read_devtools_port()
            if port is not None:
                try:
                    with opener.open(
                        f"http://127.0.0.1:{port}/json/list", timeout=2
                    ) as resp:
                        targets = json.load(resp)
                    if any(t.get("type") == "page" for t in targets):
                        return port
                except (urllib.error.URLError, OSError, ValueError):
                    pass
            time.sleep(0.1)

        raise BrowserStartupError(
            f"no DevTools page target within {self.startup_timeout:.0f}s "
            f"(profile={self.profile_dir})"
        )

    def start(self) -> "ScraperBrowser":
        if self._driver is not None:
            return self

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        # A stale port file from a previous crash would otherwise be read as if
        # it belonged to the process we are about to start.
        (self.profile_dir / "DevToolsActivePort").unlink(missing_ok=True)

        logger.info("starting chrome-headless-shell (profile=%s)", self.profile_dir)
        self._proc = subprocess.Popen(
            self._build_command(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        try:
            self._port = self._wait_for_page_target()
            options = webdriver.ChromeOptions()
            options.debugger_address = f"127.0.0.1:{self._port}"
            self._driver = webdriver.Chrome(
                service=Service(executable_path=str(self.paths.driver)),
                options=options,
            )
        except Exception:
            self.stop()
            raise

        logger.info("browser ready on DevTools port %s", self._port)
        return self

    def stop(self) -> None:
        if self._driver is not None:
            try:
                self._driver.quit()
            except Exception:
                logger.warning("driver.quit() failed", exc_info=True)
            self._driver = None

        # We started the browser, so chromedriver will not reap it for us.
        if self._proc is not None:
            if self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=SHUTDOWN_TIMEOUT)
                except subprocess.TimeoutExpired:
                    logger.warning("browser ignored SIGTERM; killing")
                    self._proc.kill()
                    try:
                        self._proc.wait(timeout=SHUTDOWN_TIMEOUT)
                    except subprocess.TimeoutExpired:
                        logger.error("browser did not exit after SIGKILL")
            self._proc = None

        self._port = None

    def __enter__(self) -> "ScraperBrowser":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()


def selftest(install_dir: Optional[str] = None) -> int:
    """Smoke-test the install: start, persist a value, restart, read it back."""
    import http.server
    import threading

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    profile = Path("/tmp/tg-scraper-selftest")
    shutil.rmtree(profile, ignore_errors=True)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<!doctype html><title>selftest</title>ok")

        def log_message(self, *args):
            pass

    # localStorage needs a real origin; about:blank and data: URLs are opaque
    # and would throw instead of persisting.
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/"

    try:
        with ScraperBrowser(str(profile), install_dir=install_dir) as b:
            b.driver.get(url)
            b.driver.execute_script("localStorage.setItem('probe','1');")
            ua = b.driver.execute_script("return navigator.userAgent;")
        with ScraperBrowser(str(profile), install_dir=install_dir) as b:
            b.driver.get(url)
            value = b.driver.execute_script("return localStorage.getItem('probe');")
    except (BrowserNotInstalled, BrowserStartupError) as exc:
        print(f"FAIL: {exc}")
        return 1
    finally:
        server.shutdown()
        shutil.rmtree(profile, ignore_errors=True)

    print(f"user agent: {ua}")
    if value == "1":
        print("PASS: browser starts and the profile survives a restart")
        return 0
    print(f"FAIL: profile did not persist (localStorage probe -> {value!r})")
    return 1


if __name__ == "__main__":
    raise SystemExit(selftest())

"""Browser-side plumbing: Playwright import, page settling, launch and attach (CDP) sessions."""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from .web_geom import CaptureError, Preset
from .web_js import _FONTS_READY, _RESTORE_OVERLAYS, _STATE, _UNFREEZE


def _pw():
    try:
        from playwright.sync_api import Error, TimeoutError, sync_playwright
    except ImportError:
        raise CaptureError("Playwright is not installed. Run: pip install playwright && "
                           "python -m playwright install chromium") from None
    return sync_playwright, Error, TimeoutError


def _first_line(e: BaseException) -> str:
    return (str(e).strip().splitlines() or [type(e).__name__])[0][:300]


def _settle(page, cap_ms: float = 10000) -> None:
    _, _, PWTimeout = _pw()
    for state, ms in (("load", cap_ms), ("networkidle", 2000)):
        try:
            page.wait_for_load_state(state, timeout=ms)
        except PWTimeout:
            pass
    try:
        page.evaluate(_FONTS_READY)
    except Exception:
        pass
    page.wait_for_timeout(250)


class _Launched:
    navigate = True

    def __init__(self, pw, browser: str, p: Preset, user_data_dir, headless: bool, args: list[str]):
        opts: dict[str, Any] = {"viewport": {"width": p.width, "height": p.height},
                                "device_scale_factor": p.scale}
        if p.mobile:
            opts.update(user_agent=p.user_agent, has_touch=True)
            if browser != "firefox":
                opts["is_mobile"] = True
        bt = getattr(pw, browser)
        self.browser = None
        if user_data_dir:
            self.ctx = bt.launch_persistent_context(str(Path(user_data_dir).expanduser()),
                                                    headless=headless, args=args, **opts)
            self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        else:
            self.browser = bt.launch(headless=headless, args=args)
            self.ctx = self.browser.new_context(**opts)
            self.page = self.ctx.new_page()

    def after_load(self) -> None:
        pass

    def screenshot(self, out: Path, full_page: bool, timeout_ms: float) -> None:
        # caret="initial": Playwright's default "hide" writes style attributes on inputs, a DOM change
        # between the privacy scan and the shot; the freeze style already makes carets transparent.
        self.page.screenshot(path=str(out), full_page=full_page, type="png", timeout=timeout_ms, caret="initial")

    def close(self) -> None:
        for fn in (self.ctx.close, self.browser.close if self.browser else None):
            try:
                fn and fn()
            except Exception:
                pass


class _Attached:
    """A tab in a running Chromium; emulation is per-tab and always cleared on close()."""

    def __init__(self, pw, cdp_url: str, url, contains, p: Preset, timeout_ms: float):
        try:
            self.browser = pw.chromium.connect_over_cdp(cdp_url, timeout=min(timeout_ms, 15000))
        except Exception as e:
            raise CaptureError(f"cannot attach to a browser at {cdp_url} ({_first_line(e)}). Is it "
                               "running with --remote-debugging-port, and is the port right?") from None
        self.p, self.cdp, self.scroll, self.reloaded = p, None, None, False
        self.page = next((pg for ctx in self.browser.contexts for pg in ctx.pages
                          if contains and contains in pg.url), None)
        self.ours = self.page is None
        if self.ours and not url:
            raise CaptureError(f"no open tab has {contains!r} in its URL, and no url was given to open")
        ctx = self.browser.contexts[0] if self.browser.contexts else self.browser.new_context()
        if self.ours:
            self.page = ctx.new_page()
        self.navigate = self.ours
        self.popups: list = []
        try:
            self.page.context.on("page", lambda pg: self.popups.append(pg))
            if not self.ours:
                self.scroll = self.page.evaluate("() => [scrollX, scrollY]")
            self.cdp = self.page.context.new_cdp_session(self.page)
            self.cdp.send("Emulation.setDeviceMetricsOverride", {
                "width": p.width, "height": p.height, "deviceScaleFactor": p.scale, "mobile": p.mobile})
            if p.mobile:
                self.cdp.send("Emulation.setUserAgentOverride", {"userAgent": p.user_agent})
                self.cdp.send("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 5})
        except BaseException:
            self.close()
            raise

    def after_load(self) -> None:
        # An already open tab keeps its desktop layout until reloaded with the phone UA.
        if not self.ours and self.p.mobile and self.page.evaluate("innerWidth") != self.p.width:
            self.page.reload(wait_until="domcontentloaded")
            self.reloaded = True
            _settle(self.page)

    def screenshot(self, out: Path, full_page: bool, timeout_ms: float) -> None:
        # Through our CDP session: Playwright's page.screenshot ignores the external DPR override.
        params: dict[str, Any] = {"format": "png", "fromSurface": True}
        if full_page:
            st = self.page.evaluate(_STATE)
            params.update(captureBeyondViewport=True,
                          clip={"x": 0, "y": 0, "width": st["dw"], "height": st["dh"], "scale": 1})
        out.write_bytes(base64.b64decode(self.cdp.send("Page.captureScreenshot", params)["data"]))

    def close(self) -> None:
        steps = []
        if not self.ours:
            steps.append(lambda: self.page.evaluate(_RESTORE_OVERLAYS))
            for fr in list(self.page.frames):   # animations, media and carets back as they were
                steps.append(lambda fr=fr: fr.evaluate(_UNFREEZE))
        if self.cdp:
            steps.append(lambda: self.cdp.send("Emulation.clearDeviceMetricsOverride"))
            if self.p.mobile:
                steps.append(lambda: self.cdp.send("Emulation.setUserAgentOverride", {"userAgent": ""}))
                steps.append(lambda: self.cdp.send("Emulation.setTouchEmulationEnabled", {"enabled": False}))
            steps.append(self.cdp.detach)
        if self.reloaded:   # we reloaded someone's tab with the phone UA: give the desktop page back
            steps.append(lambda: self.page.reload(wait_until="domcontentloaded", timeout=15000))
        if not self.ours and self.scroll:   # after the layout is back to its own size
            steps.append(lambda: self.page.evaluate(
                f"() => window.scrollTo({{left: {self.scroll[0]}, top: {self.scroll[1]}, behavior: 'instant'}})"))
        for pg in self.popups:   # tabs our clicks opened from our page
            steps.append(lambda pg=pg: pg.opener() == self.page and pg.close())
        if self.ours:
            steps.append(self.page.close)
        for step in steps:
            try:
                step()
            except Exception:
                pass

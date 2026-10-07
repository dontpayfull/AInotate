"""Web capture: Playwright screenshot + element rects (CSS px) for AInotate.

Two modes:
  launch  own headless Chromium/Firefox/WebKit with the preset as context options;
  attach  `cdp_url` of a running Chromium (BrowserOS neo, Chrome --remote-debugging-port):
          reuse the tab whose URL contains `page_url_contains` or open a new one, emulate the
          preset on that tab only through CDP and always restore it.

Rects are relative to the screenshot (viewport, or the whole document with `full_page`).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .web_cdp import _Attached, _Launched, _first_line, _pw, _settle
from .web_geom import (IPHONE_UA, PRESETS, AmbiguousTarget, CaptureError, Preset, TargetSpecError,
                       _Ambiguous, _ambiguity_message, _mask_text, describe, frame_content_box, inside, intersect,
                       nice_scale, offset_rect, parse_action, parse_target, parse_target_arg, plan_scroll,
                       png_size, rect, to_capture_coords)
from .web_js import (_FREEZE, _HIDE_OVERLAYS, _SCROLL_INTO_VIEW, _STATE, _TOP_INSET, _WATCH_COUNT,
                     _WATCH_START)
from .web_targets import (_Cand, _arrange, _find, _measure, _final_rects)

__all__ = ["AmbiguousTarget", "CaptureError", "TargetSpecError", "WebCapture", "Preset", "PRESETS",
           "IPHONE_UA", "capture", "cli_capture", "parse_target", "parse_target_arg", "parse_action"]

SHOT_ATTEMPTS = 3


@dataclass
class WebCapture:
    image_path: Path
    scale: float
    viewport: tuple[int, int]
    rects: dict[str, dict[str, float]] = field(default_factory=dict)
    missing: dict[str, str] = field(default_factory=dict)
    url: str = ""
    warnings: list[str] = field(default_factory=list)
    extra: Any = None   # what `on_page(page)` returned (e.g. privacy findings)
    near: dict[str, list[str]] = field(default_factory=dict)   # missing text target -> closest visible texts (masked)

    def to_dict(self) -> dict[str, Any]:
        from ..privacy import sanitize_url
        d = {"image_path": str(self.image_path), "scale": self.scale,
             "viewport": list(self.viewport), "rects": self.rects, "missing": self.missing,
             "url": sanitize_url(self.url) if self.url else "", "warnings": self.warnings}
        if self.image_path is None:   # shoot: the raw capture is internal and already deleted
            del d["image_path"]
        if self.near:
            d["near"] = self.near
        return d


# ---------------------------------------------------------------- capture
def capture(url: str | None = None, *, cdp_url: str | None = None, page_url_contains: str | None = None,
            preset: str = "laptop", targets: dict[str, dict] | None = None,
            actions: list[dict] | None = None, hide_overlays: bool = True, full_page: bool = False,
            out_path: str | Path | None = None, timeout_ms: float = 30000,
            browser: str = "chromium", user_data_dir: str | Path | None = None,
            headless: bool = True, on_page=None) -> WebCapture:
    """Open (or attach to) a page, run actions, measure targets, take the screenshot.

    Order: settle -> actions -> resolve targets (a text/selector target with several visible
    matches and no nth/first raises AmbiguousTarget) -> freeze (CSS animations, transitions,
    media, carets) -> scroll -> `on_page(page)` (e.g. the privacy scan) -> screenshot at once.
    A MutationObserver watches the DOM from the start of `on_page` to the end of the screenshot;
    if it changed, both are redone (up to SHOT_ATTEMPTS in all), then CaptureError: what
    `on_page` saw is exactly what the image shows. Its return value lands in `WebCapture.extra`."""
    if preset not in PRESETS:
        raise TargetSpecError(f"unknown preset {preset!r}; choose one of {', '.join(PRESETS)}")
    if browser not in ("chromium", "firefox", "webkit"):
        raise TargetSpecError(f"unknown browser {browser!r}")
    tspecs = {str(n): parse_target(t) for n, t in (targets or {}).items()}
    aspecs = [parse_action(a) for a in (actions or [])]
    if url and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", url):
        url = "https://" + url
    if not url and not (cdp_url and page_url_contains):
        raise TargetSpecError("give a url, or cdp_url + page_url_contains to reuse an open tab")
    if out_path:
        out = Path(out_path).expanduser()
    else:
        fd, tmp = tempfile.mkstemp(prefix="ainotate-capture-", suffix=".png")
        os.close(fd)   # Windows: an open handle would lock the file against the screenshot write
        out = Path(tmp)
    out.parent.mkdir(parents=True, exist_ok=True)
    p = PRESETS[preset]
    sync_playwright, PWError, PWTimeout = _pw()
    job = dict(url=url, cdp_url=cdp_url, contains=page_url_contains, preset=p, targets=tspecs,
               actions=aspecs, hide=hide_overlays, full_page=full_page, out=out, timeout_ms=timeout_ms,
               browser=browser, user_data_dir=user_data_dir, headless=headless, on_page=on_page)
    try:
        with sync_playwright() as pw:
            return _run_with_retries(pw, job)
    except BaseException as e:
        if not out_path:
            out.unlink(missing_ok=True)   # no empty temp PNG left behind
        if isinstance(e, CaptureError) or not isinstance(e, PWError):
            raise
        _raise_browser_error(e, browser, PWTimeout)
    raise AssertionError("unreachable")


def _raise_browser_error(e: BaseException, browser: str, PWTimeout) -> None:
    if isinstance(e, PWTimeout):
        raise CaptureError(f"timed out: {_first_line(e)}") from None
    msg = str(e)
    if "Executable doesn't exist" in msg or "playwright install" in msg:
        raise CaptureError(f"Playwright's {browser} browser is not installed. "
                           f"Run: python -m playwright install {browser}") from None
    raise CaptureError(f"browser error: {_first_line(e)}") from None


class _StalledLoad(CaptureError):
    pass


def _run_with_retries(pw, job: dict) -> WebCapture:
    """Some servers stall Chromium's multiplexed HTTP/2 subresource requests forever (the
    document arrives, DOMContentLoaded never fires): retry over HTTP/1.1 (own Chromium). A
    stalled load is also often a one-off: one more try on a fresh page before giving up."""
    plans = [[]]
    if not job["cdp_url"] and job["browser"] == "chromium":
        plans.append(["--disable-http2"])
    notes: list[str] = []
    for k, args in enumerate(plans):
        try:
            return _run(pw, job, args, notes)
        except (_StalledLoad, _NavTimeout) as e:
            notes.append(f"retried: {e}")
            if k + 1 < len(plans):
                continue
            if not isinstance(e, _StalledLoad):
                raise
            return _run(pw, job, args, notes)   # fresh page, same flags, last try
    raise AssertionError("unreachable")


def _run(pw, job: dict, launch_args: list[str], notes: list[str] | None = None) -> WebCapture:
    p, timeout_ms, full_page, warnings = job["preset"], job["timeout_ms"], job["full_page"], list(notes or [])
    sess = (_Attached(pw, job["cdp_url"], job["url"], job["contains"], p, timeout_ms) if job["cdp_url"]
            else _Launched(pw, job["browser"], p, job["user_data_dir"], job["headless"], launch_args))
    try:
        page = sess.page
        page.set_default_timeout(timeout_ms)
        if sess.navigate:
            _goto(page, job["url"], timeout_ms, warnings)
        sess.after_load()
        if job["hide"]:
            page.evaluate(_HIDE_OVERLAYS)
        for i, a in enumerate(job["actions"]):
            _act(page, i, a, timeout_ms)
            if job["hide"] and a["kind"] == "click":
                page.evaluate(_HIDE_OVERLAYS)
        cands, missing, near, ambiguous = {}, {}, {}, []
        deadline = time.monotonic() + min(timeout_ms, 5000) / 1000
        for name, t in job["targets"].items():
            try:
                c, why, close = _find(page, t, max(0.0, (deadline - time.monotonic()) * 1000), unique=True)
            except _Ambiguous as e:
                ambiguous.append((name, t, e))
                continue
            if c:
                cands[name] = c
            else:
                missing[name] = f"{describe(t)}: {why}"
                if close:
                    near[name] = close
        if ambiguous:
            raise AmbiguousTarget("\n".join(_ambiguity_message(f"target {n!r}:", t, e) for n, t, e in ambiguous),
                                  [c for _, _, e in ambiguous for c in e.candidates], ambiguous[0][0])
        _freeze(page)
        _arrange(page, cands, full_page)
        rects, more, warns, st, img, extra = _shoot(page, sess, cands, full_page, job["out"], timeout_ms,
                                                    job["on_page"])
        missing.update(more)
        css_w = st["dw"] if full_page else st["vw"]
        from ..privacy import sanitize_url
        return WebCapture(job["out"], nice_scale(img[0], css_w, st["dpr"]), (int(st["vw"]), int(st["vh"])),
                          rects, missing, sanitize_url(page.url), warnings + warns, extra, near)
    finally:
        sess.close()


def _each_frame(page, js: str) -> list:
    """Evaluate `js` in every attached frame; frames that cannot run it are skipped."""
    out = []
    for frame in page.frames:
        try:
            if not frame.is_detached():
                out.append(frame.evaluate(js))
        except Exception:
            continue
    return out


def _freeze(page) -> None:
    _each_frame(page, _FREEZE)


class _NavTimeout(CaptureError):
    """No response at all; some servers hang Chromium's HTTP/2 even for the document."""


def _goto(page, url: str, timeout_ms: float, warnings: list[str]) -> None:
    _, PWError, PWTimeout = _pw()
    clean = _mask_text(url)
    try:
        resp = page.goto(url, wait_until="commit", timeout=timeout_ms)
    except PWTimeout:
        raise _NavTimeout(f"navigation to {clean} timed out after {timeout_ms / 1000:g}s") from None
    except PWError as e:
        raise CaptureError(f"could not load {clean}: {_first_line(e)}") from None
    try:
        page.wait_for_load_state("domcontentloaded", timeout=min(timeout_ms, 15000))
    except PWTimeout:
        raise _StalledLoad(f"{clean} answered but its document never finished loading "
                           f"(no DOMContentLoaded after {min(timeout_ms, 15000) / 1000:g}s)") from None
    if resp is not None and resp.status >= 400:
        warnings.append(f"page answered HTTP {resp.status}")
    _settle(page)


def _act(page, i: int, a: dict, timeout_ms: float) -> None:
    if a["kind"] == "wait_ms":
        page.wait_for_timeout(a["ms"])
        return
    t = a["target"]
    try:
        c, why, _ = _find(page, t, a.get("timeout_ms", timeout_ms if a["kind"] == "wait" else min(timeout_ms, 10000)),
                          unique=a["kind"] != "wait")
    except _Ambiguous as e:
        raise AmbiguousTarget(_ambiguity_message(f"action #{i + 1} {a['kind']}: target", t, e),
                              e.candidates, f"action{i + 1}") from None
    if not c:
        raise CaptureError(f"action #{i + 1} {a['kind']}: target {describe(t)} not found ({why})")
    try:
        if a["kind"] == "click":
            _click(c.handle, a.get("timeout_ms", min(timeout_ms, 8000)))
            page.wait_for_timeout(300)
            _settle(page, 10000)
        elif a["kind"] == "fill":
            c.handle.fill(a["value"], timeout=a.get("timeout_ms", timeout_ms))
        elif a["kind"] == "scroll_to":
            c.handle.evaluate(_SCROLL_INTO_VIEW)
            page.wait_for_timeout(300)
    except CaptureError:
        raise
    except Exception as e:
        raise CaptureError(f"action #{i + 1} {a['kind']} on {describe(t)} failed: {_first_line(e)}") from None


def _click(handle, timeout_ms: float) -> None:
    """Real click; if a transparent layer (e.g. a checkbox over its label) intercepts it, click
    that point anyway like a user's tap would, and as a last resort dispatch a DOM click."""
    try:
        handle.click(timeout=timeout_ms)
        return
    except Exception as e:
        first = e
    for attempt in (lambda: handle.click(force=True, timeout=3000), lambda: handle.evaluate("el => el.click()")):
        try:
            attempt()
            return
        except Exception:
            pass
    raise first


def _moved(ms: dict, after: dict, st: dict, st2: dict) -> bool:
    return st2["sy"] != st["sy"] or st2["sx"] != st["sx"] or any(
        "rect" in ms[n] and "rect" in after[n] and any(abs(ms[n]["rect"][k] - after[n]["rect"][k]) > 1.5
                                                   for k in "xywh") for n in ms)


def _shoot(page, sess, cands, full_page: bool, out: Path, timeout_ms: float, on_page=None):
    """Measure, then `on_page` (privacy scan) and the screenshot back to back with a DOM
    MutationObserver around both; re-measure. Redo all of it if the DOM changed or the layout
    moved (SHOT_ATTEMPTS in all). A DOM that never holds still raises CaptureError when
    `on_page` must match the image (privacy); a layout that keeps moving keeps the last shot."""
    for attempt in range(SHOT_ATTEMPTS):
        if attempt:
            page.wait_for_timeout(300)
        st = page.evaluate(_STATE)
        inset = 0 if full_page else page.evaluate(_TOP_INSET)
        ms = _measure(page, cands)
        _each_frame(page, _WATCH_START)
        extra = on_page(page) if on_page else None
        sess.screenshot(out, full_page, timeout_ms)
        counts = _each_frame(page, _WATCH_COUNT)
        changes = sum(n if n > 0 else 1 for n in counts if n != 0)
        after = _measure(page, cands)
        moved = _moved(ms, after, st, page.evaluate(_STATE))
        if not changes and not moved:
            break
        if attempt == SHOT_ATTEMPTS - 1 and (changes or moved) and on_page:
            why = f"{changes} DOM change(s)" if changes else "layout kept moving"
            raise CaptureError(f"page kept changing; privacy cannot be guaranteed ({why} "
                               f"between the privacy scan and the screenshot, {SHOT_ATTEMPTS} tries)")
    rects, missing, warns = _final_rects(ms if not moved else after, st, inset, full_page)
    if moved:
        warns.append("layout moved during capture; target rects may be slightly shifted")
    return rects, missing, warns, st, png_size(out), extra


# ---------------------------------------------------------------- CLI
def cli_capture(argv: list[str] | None = None) -> int:
    """`ainotate capture URL ...`: prints the WebCapture as JSON. Raises CaptureError on failure."""
    ap = argparse.ArgumentParser(prog="ainotate capture",
                                 description="Screenshot a web page and measure element rects.")
    ap.add_argument("url", nargs="?", help="page to open (optional with --cdp + --tab-contains)")
    ap.add_argument("--preset", choices=list(PRESETS), default="laptop")
    ap.add_argument("--target", action="append", default=[], metavar="NAME=JSON",
                    help='e.g. login=\'{"role":"button","name":"Login"}\' (repeatable)')
    ap.add_argument("--action", action="append", default=[], metavar="JSON",
                    help='e.g. \'{"click":{"text":"Menu"}}\' (repeatable, run in order)')
    ap.add_argument("--cdp", metavar="URL", help="attach to a running Chromium, e.g. http://127.0.0.1:9222")
    ap.add_argument("--tab-contains", "--page-url-contains", dest="tab_contains", metavar="STR",
                    help="with --cdp: reuse the tab whose URL contains STR")
    ap.add_argument("--full-page", action="store_true")
    ap.add_argument("--out", metavar="PATH", help="PNG path (default: a temp file)")
    ap.add_argument("--no-hide-overlays", action="store_true")
    ap.add_argument("--browser", choices=["chromium", "firefox", "webkit"], default="chromium")
    ap.add_argument("--user-data-dir", metavar="DIR", help="persistent profile (launch mode)")
    ap.add_argument("--timeout-ms", type=float, default=30000)
    args = ap.parse_args(argv)
    try:
        targets = dict(parse_target_arg(t) for t in args.target)
        actions = [json.loads(a) for a in args.action]
        for a in actions:
            parse_action(a)
    except json.JSONDecodeError as e:
        ap.error(f"--action: invalid JSON ({e.msg})")
    except TargetSpecError as e:
        ap.error(str(e))
    if not args.url and not (args.cdp and args.tab_contains):
        ap.error("URL is required unless --cdp and --tab-contains pick an open tab")
    cap = capture(args.url, cdp_url=args.cdp, page_url_contains=args.tab_contains, preset=args.preset,
                  targets=targets, actions=actions, hide_overlays=not args.no_hide_overlays,
                  full_page=args.full_page, out_path=args.out, timeout_ms=args.timeout_ms,
                  browser=args.browser, user_data_dir=args.user_data_dir)
    print(json.dumps(cap.to_dict(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    import sys
    from ..spec import TargetNotFound
    try:
        sys.exit(cli_capture())
    except AmbiguousTarget as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(5)
    except TargetNotFound as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(6)
    except CaptureError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(4)


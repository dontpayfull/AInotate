"""AInotate MCP server (stdio): annotated screenshots for Claude Desktop and any MCP client.

Every tool returns one JSON status (`ok`, paths, image size, warnings, resolved targets,
suggestions) as text + structuredContent and, when it produces an image, a JPEG preview
downscaled to at most 1024 px so the model can check the result without a huge context.
The full-resolution file stays on disk. Failures come back as `ok: false` with the exact
problem and a suggested fix, never a stack trace.

Run: `ainotate mcp`. Claude Desktop setup and permissions: `mcp_docs.SETUP`.
"""
import json
import sys
from pathlib import Path
from typing import Callable, Optional, Union

import anyio.to_thread
from mcp import types
from PIL import Image

from .mcp_docs import (  # noqa: F401  (re-export)
    CYAN, DRAFT_NEXT, INSTRUCTIONS, MAGENTA, PREVIEW_MAX, PREVIEW_QUALITY, SETUP, _flat, _jpeg, _small,
    grid_preview, preview_jpeg, _FALLBACK_SPEC, _INSTALL, _WORKFLOW, _StrayToStderr, _draft_next, _fix_for,
    _mask, _masked_list, _skill_md, annotate_guide_text, bug_ticket_text, guard_stdout, spec_reference_text,
)

try:                                    # mcp 2.x
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:                     # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server


mcp = _Server("ainotate", instructions=INSTRUCTIONS)


# ---------------------------------------------------------------- result helpers

def _as_text(payload: dict) -> types.TextContent:
    return types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, default=str))


def _result(payload: dict, preview: Optional[bytes] = None, error: bool = False) -> types.CallToolResult:
    content: list = [_as_text(payload)]
    if preview:
        import base64
        content.append(types.ImageContent.model_validate(
            {"type": "image", "data": base64.b64encode(preview).decode(), "mimeType": "image/jpeg"}))
    return types.CallToolResult.model_validate(
        {"content": content, "structuredContent": payload, "isError": error})


def _image_info(path) -> dict:
    p = Path(path).expanduser()
    with Image.open(p) as im:
        return {"path": str(p), "width": im.width, "height": im.height}


# ---------------------------------------------------------------- errors

def _error(tool: str, e: BaseException, **extra) -> types.CallToolResult:
    msg = _mask(str(e) or type(e).__name__)
    payload = {"ok": False, "tool": tool,
               "error": {"type": type(e).__name__, "message": msg, "fix": _fix_for(e)}}
    for attr in ("near", "candidates"):          # OCR hints, masked like the message
        if getattr(e, attr, None):
            payload["error"][attr] = _masked_list(getattr(e, attr))
    payload.update(extra)
    return _result(payload, error=True)


async def _run(tool: str, fn: Callable[[], types.CallToolResult]) -> types.CallToolResult:
    """Run blocking work in a thread (Playwright's sync API refuses an event loop thread) and
    turn exceptions into structured errors. Stray prints are kept off the protocol once, for
    the whole process, by main() (a per-call redirect of the global sys.stdout races between
    concurrent calls)."""
    def work():
        try:
            return fn()
        except Exception as e:   # noqa: BLE001 - every failure becomes a structured error
            return _error(tool, e)
    return await anyio.to_thread.run_sync(work)


def _spec(spec) -> dict:
    if isinstance(spec, str):
        from .spec import SpecError
        try:
            spec = json.loads(spec)
        except json.JSONDecodeError as e:
            raise SpecError(f"spec is not valid JSON: {e}")
    return spec


# ---------------------------------------------------------------- annotate

def _annotate(spec, draft: bool, debug: bool, tool: str) -> types.CallToolResult:
    from .output import save
    from .render import render
    from .shoot import legend
    spec = _spec(spec)
    res = render(spec, debug=debug)
    warnings = list(res.warnings)
    name = spec.get("name", "") if isinstance(spec, dict) else ""
    draft = draft or debug
    paths = save(res.image, name, draft=draft, on_warning=warnings.append)
    jpeg, _ = preview_jpeg(paths[0])
    payload = {"ok": True, "tool": tool, "paths": [str(p) for p in paths],
               "image": {"path": str(paths[0]), "width": res.image.width, "height": res.image.height},
               "draft": draft, "warnings": [_mask(w) for w in warnings],
               "legend": legend(spec.get("marks") or []),
               "redactions": list(getattr(res, "redactions", []) or []),
               "next": ("Check the preview: every mark sits on its element, labels do not cover the "
                        "text that matters, no sensitive data anywhere in the frame. Fix the spec "
                        "and call again if not. " + _draft_next(draft)
                        + (" Debug overlay: cyan = target rects, magenta = label boxes." if debug else ""))}
    return _result(payload, jpeg)


@mcp.tool()
async def annotate(spec: Union[dict, str], draft: bool = True) -> types.CallToolResult:
    """Render an annotation spec onto its `input` image. Draft by default (temp dir): check the
    returned preview, then call again with draft=false to save to the output folder.
    spec: {"input": path, "scale": 1|2.., "crop": "auto"|[x1,y1,x2,y2], "name": "...",
    "marks": [{"type": "step"|"box"|"arrow"|"highlight"|"spotlight"|"redact"|"text"|...,
    "rect": {x,y,w,h} or [x1,y1,x2,y2], "label": "...", "n": 1, "color": "look|bad|good|info"}]}.
    Every coordinate in one unit; scale converts it to image px. Full reference: ainotate://spec.
    Returns paths, size, warnings, legend (step numbers + labels), redactions and a preview."""
    return await _run("annotate", lambda: _annotate(spec, draft, False, "annotate"))


@mcp.tool()
async def preview(spec: Union[dict, str]) -> types.CallToolResult:
    """Draft render with the debug overlay (cyan target rects, magenta label boxes); saves
    nothing permanent. Use it to check placement before annotate."""
    return await _run("preview", lambda: _annotate(spec, True, True, "preview"))


# ---------------------------------------------------------------- web

@mcp.tool()
async def capture_web(url: Optional[str] = None, cdp_url: Optional[str] = None,
                      page_url_contains: Optional[str] = None, preset: str = "laptop",
                      targets: Optional[dict[str, dict]] = None, actions: Optional[list[dict]] = None,
                      hide_overlays: bool = True, full_page: bool = False, timeout_ms: float = 30000,
                      browser: str = "chromium", user_data_dir: Optional[str] = None) -> types.CallToolResult:
    """Screenshot a web page and measure named targets. Presets: laptop 1280x800@2, wide
    1600x1000@2, phone 390x844@3. targets: {"name": {"text": "Checkout"} | {"selector": "css"} |
    {"role": "button", "name": "Save"}, optional "within", "nth", "exact", "fit"}. actions run first:
    {"click": T}, {"fill": T, "value": "..."}, {"wait": T}, {"scroll_to": T}, {"wait_ms": 500}.
    cdp_url + page_url_contains attach to a running logged-in browser instead of launching one.
    Returns the image path, rects in CSS px, scale (use as the spec scale), missing targets,
    a spec stub, and a preview with the resolved rects outlined."""
    def work():
        from .capture import web
        c = web.capture(url, cdp_url=cdp_url, page_url_contains=page_url_contains, preset=preset,
                        targets=targets or {}, actions=actions or [], hide_overlays=hide_overlays,
                        full_page=full_page, timeout_ms=timeout_ms, browser=browser, user_data_dir=user_data_dir)
        s = c.scale
        boxes = [(n, [r["x"] * s, r["y"] * s, (r["x"] + r["w"]) * s, (r["y"] + r["h"]) * s])
                 for n, r in c.rects.items()]
        jpeg, (w, h) = preview_jpeg(c.image_path, boxes)
        suggestions = []
        if c.missing:
            suggestions.append("Not found: " + ", ".join(f"{n} ({why})" for n, why in c.missing.items())
                               + ". Check the visible text or selector, add a {\"wait\": T} action, "
                                 "or open the menu first with a click action.")
        suggestions.append(f"Annotate with input={c.image_path}, scale={s} and these rects as "
                           "mark rects; check each outline in the preview sits on its element.")
        payload = {"ok": True, "tool": "capture_web",
                   "image": {"path": str(c.image_path), "width": w, "height": h},
                   "scale": s, "viewport": list(c.viewport), "url": getattr(c, "url", "") or url,
                   "rects": c.rects, "missing": c.missing, "warnings": list(getattr(c, "warnings", [])),
                   "spec_stub": {"input": str(c.image_path), "scale": s, "crop": "auto", "marks": []},
                   "suggestions": suggestions}
        return _result(payload, jpeg)
    return await _run("capture_web", work)


@mcp.tool()
async def shoot(spec: Union[dict, str], draft: bool = True) -> types.CallToolResult:
    """One call from URL to annotated image: spec = render spec without input/scale, plus
    url (or cdp_url + page_url_contains), preset, actions, and marks with "target": {...}
    (same forms as capture_web) instead of "rect". Draft by default: check the preview, then
    call again with draft=false to save. Returns paths, warnings, legend, redactions (secrets
    hidden automatically), the capture's resolved rects and a preview."""
    def work():
        try:
            from .shoot import shoot as _shoot
        except ImportError as e:
            if (getattr(e, "name", "") or "").startswith("ainotate"):
                return _result({"ok": False, "tool": "shoot", "error": {
                    "type": "NotAvailable", "message": "shoot is not available in this AInotate install yet",
                    "fix": "Use capture_web(targets=...) and then annotate(spec) with the returned "
                           "rects and scale."}}, error=True)
            raise
        res = _shoot(_spec(spec), draft=draft)
        paths = [str(p) for p in res.paths]
        jpeg, (w, h) = preview_jpeg(paths[0])
        cap = getattr(res, "capture", None)
        payload = {"ok": True, "tool": "shoot", "paths": paths,
                   "image": {"path": paths[0], "width": w, "height": h}, "draft": draft,
                   "warnings": [_mask(x) for x in res.warnings],
                   "legend": list(getattr(res, "legend", None) or []),
                   "redactions": list(getattr(res, "redactions", None) or []),
                   "next": "Check the preview: marks on the right elements, no sensitive data in frame. "
                           + _draft_next(draft)}
        if cap is not None:
            payload["capture"] = cap.to_dict() if hasattr(cap, "to_dict") else {
                k: getattr(cap, k, None) for k in ("image_path", "scale", "rects", "missing")}
        return _result(payload, jpeg)
    return await _run("shoot", work)


# ---------------------------------------------------------------- locate

@mcp.tool()
async def locate(image_path: str, text: str, all: bool = False, nth: Optional[int] = None,
                 within: Optional[list[float]] = None, exact: bool = False,
                 lang: Optional[str] = None, backend: Optional[str] = None) -> types.CallToolResult:
    """Find text on an image by OCR. Returns rect [x1,y1,x2,y2] in image px (spec scale 1)
    per match, best first; several equally good matches = ambiguous (ask the user, or pass
    nth 0-based in reading order, or within [x1,y1,x2,y2]). all=true lists weaker matches too.
    The preview outlines the matches."""
    def work():
        from . import ocr
        langs_arg = [lang] if lang else None
        items = ocr.read(image_path, langs=langs_arg, backend=backend)
        cands = ocr.locate(image_path, text, nth=nth, within=within, exact=exact, langs=langs_arg, backend=backend, items=items)
        top = [c for c in cands if c["top"]]
        if not top:
            raise ocr.not_found(text, items, within=within)
        show = cands if all else top
        jpeg, (w, h) = preview_jpeg(image_path, [(str(i), c["rect"]) for i, c in enumerate(show)])
        matches = _masked_list([{k: c.get(k) for k in ("rect", "text", "score", "top", "estimated")}
                                for c in show])
        suggestions = []
        if len(top) > 1:
            suggestions.append(f"{len(top)} equally good matches: ask the user which one, or call "
                               "again with nth (0-based, reading order) or within.")
        else:
            suggestions.append(f"Use rect {top[0]['rect']} with scale 1 (or divide by your spec scale).")
        if any(c["estimated"] for c in show):
            suggestions.append("estimated=true: box cut out of a longer word by character count; "
                               "check it with zoom.")
        payload = {"ok": True, "tool": "locate", "image": {"path": str(Path(image_path).expanduser()),
                   "width": w, "height": h}, "query": text, "ambiguous": len(top) > 1,
                   "matches": matches, "weaker_hidden": 0 if all else len(cands) - len(top),
                   "suggestions": suggestions}
        return _result(payload, jpeg)
    return await _run("locate", work)


# ---------------------------------------------------------------- desktop

def _captured(tool: str, path, extra: Optional[dict] = None) -> types.CallToolResult:
    jpeg, (w, h) = preview_jpeg(path)
    payload = {"ok": True, "tool": tool, "image": {"path": str(path), "width": w, "height": h},
               "next": "Locate elements with locate(text) or grid + zoom; annotate with scale 1."}
    payload.update(extra or {})
    return _result(payload, jpeg)


@mcp.tool()
async def capture_screen(region: Optional[Union[list[int], str]] = None) -> types.CallToolResult:
    """Capture the screen, or region [x, y, w, h] in screen coordinates."""
    def work():
        from .capture import desktop
        return _captured("capture_screen", desktop.screen(region=region))
    return await _run("capture_screen", work)


@mcp.tool()
async def list_windows(filter: Optional[str] = None) -> types.CallToolResult:
    """Visible windows: [{id, app, title, bounds}]; filter = case-insensitive app/title substring."""
    def work():
        from .capture import desktop
        wins = desktop.list_windows(filter)
        sugg = ["capture_window(id) captures one without moving or resizing it."] if wins else \
            ["No window matched; call without filter to see all."]
        return _result({"ok": True, "tool": "list_windows", "count": len(wins), "windows": wins,
                        "suggestions": sugg})
    return await _run("list_windows", work)


@mcp.tool()
async def capture_window(id: Union[int, str], targets: Optional[dict] = None) -> types.CallToolResult:
    """Capture one window by id from list_windows (never moves or resizes it). macOS: targets
    {name: {"element": label, "role"?, "nth"?, "exact"?}} measures UI elements (Accessibility) and
    returns rects plus the scale to put in the annotate spec."""
    def work():
        from .capture import desktop
        path = desktop.window(id)
        if not targets:
            return _captured("capture_window", path, {"window_id": id})
        from PIL import Image
        from .capture import ax
        w = desktop._find(id)
        if not w:
            raise desktop.CaptureError(f"window {id} is gone; call list_windows again")
        with Image.open(path) as im:
            m = ax.window_targets(w, im.size, targets)
        return _captured("capture_window", path, {"window_id": id, **m, "next": "annotate with these rects "
                         "and \"scale\": %s (rects are in window points)." % m["scale"]})
    return await _run("capture_window", work)


@mcp.tool()
async def clipboard_image() -> types.CallToolResult:
    """Save the image on the clipboard (e.g. a screenshot the user just copied) as PNG."""
    def work():
        from .capture import clipboard
        return _captured("clipboard_image", clipboard.grab())
    return await _run("clipboard_image", work)


# ---------------------------------------------------------------- locate visually

@mcp.tool()
async def grid(image_path: str, step: int = 100) -> types.CallToolResult:
    """Coordinate grid every `step` image px to find the area of an element; then zoom into it
    for an exact rect. The preview IS the grid (labels in source px)."""
    def work():
        from .tools import grid as _grid
        r = _grid(image_path, step)
        src = _image_info(image_path)
        return _result({"ok": True, "tool": "grid", "image": src, "grid_path": str(r.path), "step": step,
                        "next": "zoom(image_path, x1, y1, x2, y2) on the area (under ~500 px wide) "
                                "to read the exact rect."}, grid_preview(image_path, step))
    return await _run("grid", work)


@mcp.tool()
async def zoom(image_path: str, x1: float, y1: float, x2: float, y2: float,
               step: int = 20) -> types.CallToolResult:
    """Enlarged crop of [x1,y1,x2,y2] with a fine grid whose labels are SOURCE image px: read
    the rect straight off the preview (scale 1). Keep regions under ~500 px wide."""
    def work():
        from .tools import zoom as _zoom
        r = _zoom(image_path, x1, y1, x2, y2, step)
        jpeg, _ = preview_jpeg(r.path)
        return _result({"ok": True, "tool": "zoom", "zoom_path": str(r.path),
                        "region": [x1, y1, x2, y2], "note": r.note, "warnings": r.warnings,
                        "zoom_size": list(r.size)}, jpeg)
    return await _run("zoom", work)


# ---------------------------------------------------------------- export

@mcp.tool()
async def make_guide(steps: list[dict], title: str = "", formats: Optional[list[str]] = None,
                     name: str = "", draft: bool = True) -> types.CallToolResult:
    """Step-by-step guide from annotated images: steps [{"image": path, "text": "...", "alt"?}],
    formats any of md, html, pdf (default md + html); every step image is put on one canvas
    width. name = folder name in the output folder (default "guide"). Draft by default (temp
    dir): check the preview (the first step's image), then call again with draft=false to save.
    Returns the file per format."""
    def work():
        from .export import guide
        res = guide(steps, title, formats=formats or ["md", "html"], name=name, draft=draft)
        first = steps[0]["image"] if steps and isinstance(steps[0], dict) else None
        jpeg = preview_jpeg(first)[0] if first else None
        return _result({"ok": True, "tool": "make_guide", "files": {k: str(v) for k, v in res.items()},
                        "pdf_backend": res.pdf_backend, "warnings": list(res.warnings),
                        "steps": len(steps), "draft": draft,
                        "next": _draft_next(draft, "Give the user the file paths, each on its own line.")},
                       jpeg)
    return await _run("make_guide", work)


@mcp.tool()
async def compare(before: str, after: str, labels: Optional[list[str]] = None, layout: str = "auto",
                  name: str = "", draft: bool = True) -> types.CallToolResult:
    """Before/after plate: both images side by side or stacked, labelled (default "Before",
    "After"; pass labels in the audience's language). layout = "auto" | "side" | "stack".
    name = file name (default "compare"). Draft by default: check the preview, then call again with
    draft=false to save it to the output folder."""
    def work():
        from .export import compare as _compare
        p = _compare(before, after, labels=tuple(labels) if labels else ("Before", "After"),
                     layout=layout, name=name, draft=draft)
        jpeg, (w, h) = preview_jpeg(p)
        return _result({"ok": True, "tool": "compare", "paths": [str(p)], "draft": draft,
                        "image": {"path": str(p), "width": w, "height": h},
                        "next": _draft_next(draft)}, jpeg)
    return await _run("compare", work)


@mcp.tool()
async def animate(frames: list[str], format: str = "apng", duration_ms: Optional[int] = None,
                  durations_ms: Optional[int] = None, loop: int = 0, crossfade_ms: int = 0,
                  name: str = "", draft: bool = True) -> types.CallToolResult:
    """Animation from image paths: apng (full color, default) or gif (plays everywhere: Slack,
    mail, Jira). duration_ms per frame (default 1500). name = file name (default "animation").
    Draft by default: the preview shows the first frame; check it, then call again with
    draft=false to save to the output folder."""
    def work():
        from .export import gif
        d_ms = duration_ms if duration_ms is not None else durations_ms
        p = gif(frames, durations_ms=d_ms, loop=loop, crossfade_ms=crossfade_ms,
                format=format, name=name, draft=draft)
        jpeg, (w, h) = preview_jpeg(p)
        return _result({"ok": True, "tool": "animate", "paths": [str(p)], "frames": len(frames),
                        "format": format, "draft": draft, "image": {"path": str(p), "width": w, "height": h},
                        "next": _draft_next(draft)}, jpeg)
    return await _run("animate", work)


@mcp.tool()
async def copy_to_clipboard(path: str) -> types.CallToolResult:
    """Put an image on the system clipboard so the user can paste it (Slack, Jira, mail)."""
    def work():
        from .export import copy_to_clipboard as _copy
        _copy(path)
        return _result({"ok": True, "tool": "copy_to_clipboard", "path": str(Path(path).expanduser())})
    return await _run("copy_to_clipboard", work)


@mcp.tool()
async def doctor() -> types.CallToolResult:
    """Check this machine: Pillow, font, output folder, Playwright, CDP browser, screen capture
    permission, window listing, OCR, clipboard, mcp. Failed checks carry the exact fix."""
    def work():
        from dataclasses import asdict

        from .doctor import run_checks
        checks = run_checks()
        failed = [c for c in checks if not c.ok and c.required]
        return _result({"ok": not failed, "tool": "doctor", "os": sys.platform,
                        "checks": [asdict(c) for c in checks],
                        "fixes": [f"{c.name}: {c.fix}" for c in checks if not c.ok and c.fix]},
                       error=bool(failed))
    return await _run("doctor", work)


# ---------------------------------------------------------------- resource + prompts

@mcp.resource("ainotate://spec", name="spec-reference", mime_type="text/markdown",
              description="AInotate spec reference: marks, units, options, crop, colors, patterns")
def spec_reference() -> str:
    return spec_reference_text()


@mcp.prompt(name="annotate-guide", description="Step-by-step visual guide with AInotate")
def annotate_guide(goal: str = "", url: str = "") -> str:
    return annotate_guide_text(goal, url)


@mcp.prompt(name="bug-ticket", description="Bug report or ticket with an annotated screenshot")
def bug_ticket(issue: str = "", url: str = "", tracker: str = "") -> str:
    return bug_ticket_text(issue, url, tracker)


def main() -> int:
    guard_stdout()
    mcp.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())

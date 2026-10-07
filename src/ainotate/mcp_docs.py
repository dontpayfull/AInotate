"""Long-form text for the MCP server: instructions, error fixes, spec resource and prompt bodies;
plus the plumbing behind the tools: JPEG previews, error masking, the stray-print guard.
Re-exported by mcp_server."""
from __future__ import annotations

import io
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw

PREVIEW_MAX = 1024
PREVIEW_QUALITY = 80
CYAN, MAGENTA = (0, 200, 255), (255, 0, 160)
from typing import Optional

INSTRUCTIONS = (
    "AInotate draws annotated screenshots (numbered steps, boxes, arrows with labels, solid "
    "redaction) so you can SHOW a UI point instead of describing it. Reach for AInotate PROACTIVELY "
    "unprompted whenever an answer benefits from showing a UI location ('where is X', 'how do I do Y'), "
    "verifying visual changes, writing bug tickets, or whenever you would otherwise write spatial descriptions "
    "('top-right', 'under the menu'). Show, don't describe. Workflow: capture "
    "(capture_web / capture_window / capture_screen / clipboard_image / the user's file) -> "
    "locate exact rects (capture_web targets, locate by OCR, grid then zoom) -> annotate (a "
    "draft by default) -> look at the returned preview and fix the spec -> call again with "
    "draft=false to save -> deliver the path. annotate, shoot, compare, animate and make_guide "
    "all draft by default: check the preview, then call again with draft=false. Never estimate "
    "coordinates from a preview: previews are downscaled. Spec reference: resource "
    "ainotate://spec. Prompts: annotate-guide, bug-ticket."
)


# ---------------------------------------------------------------- previews

def _flat(im: Image.Image) -> Image.Image:
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, "white")
        bg.paste(im, mask=im.getchannel("A"))
        return bg
    return im.convert("RGB")


def _small(path) -> tuple[Image.Image, float, tuple[int, int]]:
    """First frame of an image, flattened and shrunk to PREVIEW_MAX; (image, factor, full size)."""
    with Image.open(Path(path).expanduser()) as src:
        size = src.size
        im = _flat(src)
    k = min(1.0, PREVIEW_MAX / max(size))
    if k < 1:
        im = im.resize((max(1, round(size[0] * k)), max(1, round(size[1] * k))), Image.LANCZOS)
    return im, k, size


def _jpeg(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=PREVIEW_QUALITY, optimize=True)
    return buf.getvalue()


def preview_jpeg(path, boxes: Optional[list] = None) -> tuple[bytes, tuple[int, int]]:
    """JPEG preview (<= PREVIEW_MAX px) of an image file, optionally with labelled outlines.
    boxes: [(label, [x1, y1, x2, y2] in full-size image px)]. Returns (jpeg bytes, full size)."""
    im, k, size = _small(path)
    if boxes:
        from .style import load_font
        d, f = ImageDraw.Draw(im), load_font(13)
        for label, (x1, y1, x2, y2) in boxes:
            r = [x1 * k, y1 * k, x2 * k, y2 * k]
            d.rectangle(r, outline=MAGENTA, width=2)
            if label:
                ly = r[1] - 16 if r[1] >= 16 else r[1] + 2
                tb = d.textbbox((r[0], ly), str(label), font=f)
                d.rectangle([tb[0] - 2, tb[1] - 1, tb[2] + 2, tb[3] + 1], fill=MAGENTA)
                d.text((r[0], ly), str(label), fill="white", font=f)
    return _jpeg(im), size


def grid_preview(path, step: int) -> bytes:
    """The grid redrawn on the downscaled image with readable labels in SOURCE pixels (the
    full-size grid file's labels would shrink below legibility in a 1024 px preview)."""
    from .style import load_font
    im, k, (W, H) = _small(path)
    d, f = ImageDraw.Draw(im), load_font(13)
    every = max(1, math.ceil(42 / max(1e-6, step * k)))   # keep labels ~42 px apart
    for i, x in enumerate(range(0, W, step)):
        d.line([(x * k, 0), (x * k, im.height)], fill=(255, 0, 80), width=1)
        if i % every == 0:
            d.text((x * k + 2, 2), str(x), fill=(255, 0, 80), font=f)
    for i, y in enumerate(range(0, H, step)):
        d.line([(0, y * k), (im.width, y * k)], fill=(255, 0, 80), width=1)
        if i % every == 0 and y:
            d.text((2, y * k + 2), str(y), fill=(255, 0, 80), font=f)
    return _jpeg(im)


# ---------------------------------------------------------------- errors + stdout

def _mask(text) -> str:
    """privacy.mask_text: error text can quote OCR or page text that holds a secret."""
    from .privacy import mask_text
    return mask_text(str(text))


def _masked_list(items) -> list:
    return [dict(it, text=_mask(it["text"])) if isinstance(it, dict) and "text" in it else it
            for it in items]


class _StrayToStderr(io.TextIOBase):
    """sys.stdout while the server runs: text written with print() goes to stderr, while
    `.buffer` / `.fileno()` stay the real stdout so the stdio transport still finds the wire."""

    def __init__(self, wire, err):
        self._wire, self._err = wire, err

    @property
    def buffer(self):
        return self._wire.buffer

    @property
    def encoding(self):
        return self._err.encoding

    def fileno(self):
        return self._wire.fileno()

    def writable(self):
        return True

    def write(self, s):
        return self._err.write(s)

    def flush(self):
        self._err.flush()

    def isatty(self):
        return False


def guard_stdout() -> None:
    """Once, at server start: route stray text output (print, library chatter) to stderr."""
    if not isinstance(sys.stdout, _StrayToStderr):
        sys.stdout = _StrayToStderr(sys.stdout, sys.stderr)


_INSTALL = {"playwright": "web", "Quartz": "desktop", "mss": "desktop", "Vision": "ocr", "winrt": "ocr"}


SETUP = """Run: `ainotate mcp`, `python -m ainotate.mcp_server`, or `uvx --from 'ainotate[mcp,web]' ainotate mcp`.

Claude Desktop, `claude_desktop_config.json`
  macOS:   ~/Library/Application Support/Claude/claude_desktop_config.json
  Windows: %APPDATA%\\Claude\\claude_desktop_config.json

    {"mcpServers": {"ainotate": {"command": "ainotate", "args": ["mcp"]}}}

If `ainotate` is not on the PATH Claude Desktop sees, give the full path to it
(macOS: "/Users/you/.local/bin/ainotate"; Windows: "C:\\\\Users\\\\you\\\\...\\\\Scripts\\\\ainotate.exe").
Without installing (uv):

    {"mcpServers": {"ainotate": {"command": "uvx",
       "args": ["--from", "ainotate[mcp,web]", "ainotate", "mcp"]}}}

Screen and window capture on macOS need Screen Recording permission for the app that runs
the server (Claude, for Claude Desktop). Web capture needs `playwright install chromium` once.
"""

DRAFT_NEXT = "Check the preview, then call again with draft=false to save."


def _draft_next(draft: bool, final: str = "Deliver the first path on its own line.") -> str:
    return DRAFT_NEXT if draft else final


_IMAGE_MISSING = ("Check the path: the file must exist on this machine (use an absolute path). If the "
                  "user pasted or copied the image, call clipboard_image() and use the path it returns.")


def _fix_for(e: BaseException) -> str:
    name, mod = type(e).__name__, type(e).__module__
    msg = str(e)
    if isinstance(e, ImportError):
        missing = (getattr(e, "name", None) or "").split(".")[0]
        if missing == "ainotate":
            return "This feature is not in this AInotate install yet; update AInotate."
        extra = _INSTALL.get(missing, "web")
        hint = f"pip install 'ainotate[{extra}]'"
        if missing == "playwright":
            hint += " && playwright install chromium"
        return hint + " (then restart the MCP server). doctor() lists what is missing."
    if name == "TextNotFound":
        return ("Use one of the closest OCR texts above, a shorter query, or grid + zoom to read "
                "the rect off the image.")
    if name == "AmbiguousText":
        return "Several matches: pick one with nth (0-based, reading order) or narrow with within."
    if name == "TargetNotFound":
        return ("Target not found: check the exact visible text (the closest matches are listed), "
                "use role+name or a selector, or add an action that opens the menu first.")
    if "image not found" in msg or (isinstance(e, FileNotFoundError) and name != "SpecError"):
        bad = getattr(e, "input", None)
        return (f"`{bad}`: " if bad else "") + _IMAGE_MISSING
    if name == "SpecError":
        return "Fix every problem listed and call again. Marks and options: resource ainotate://spec."
    if name == "RenderError":
        return ("The spec is valid but cannot be drawn as is: put the label in empty space with "
                "label_at, drop marks (max 6), or widen crop. preview(spec) shows the target rects "
                "(cyan) and label boxes (magenta).")
    if name == "TargetSpecError":
        if "preset" in msg or "browser" in msg:
            return _mask(msg)
        return ('Targets: {"text": ...} | {"selector": css} | {"role": ..., "name": ...}, optional '
                'within, nth, exact, fit. Actions: {"click": T}, {"fill": T, "value": ...}, {"wait": T}, '
                '{"scroll_to": T}, {"wait_ms": 500}.')
    if name == "CaptureError" and mod.startswith("ainotate.capture.web"):
        return ("Check the URL or the target. For a logged-in page attach to a running browser: "
                "cdp_url (e.g. http://127.0.0.1:9222) + page_url_contains. doctor() checks "
                "Playwright and CDP.")
    if name == "CaptureError":
        return ("On macOS the Screen Recording permission belongs to the app running this server "
                "(Claude for Claude Desktop); restart it after enabling. list_windows() gives "
                "current window ids.")
    if name == "OcrError":
        return "Install an OCR engine (see message), or locate visually with grid + zoom."
    if name == "ToolError":
        return "Check the arguments: region inside the image, step > 0."
    if name == "OutputError":
        return ("Point AINOTATE_OUTPUT_DIR or ~/.config/ainotate/config.toml (output_dir) at a "
                "writable folder; doctor() checks it.")
    if name == "ExportError":
        bad = getattr(e, "input", None)
        if bad:
            return f"Fix the `{bad}` argument as the message says, then call again."
        return "Fix the input the message names (a path, format or option), then call again."
    if isinstance(e, (FileNotFoundError, IsADirectoryError)):
        return "Check the path; the file must exist on this machine."
    return "Unexpected failure: run doctor() and report this message if it persists."


_FALLBACK_SPEC = """Spec: {"input": image path, "scale": spec units -> image px, "crop": "auto" |
[x1,y1,x2,y2] | {x,y,w,h}, "crop_pad": 260, "dim": 0.55, "name": "file name", "marks": [...]}
Units: every coordinate (rect, crop, label_at, at, pad) in ONE unit. DOM rects from capture_web:
dicts {x,y,w,h} with the returned scale. locate / grid / zoom pixels: lists [x1,y1,x2,y2], scale 1.
Colors: look (orange, default), bad (red: only for wrong/bug), good (green), info (blue).
Per-mark options: color, label_at [x,y] (label top-left), "arrow": false, "box": false, pad,
badge (tl tr bl br l r t b none). Max 6 marks, labels 1-4 words, UI text quoted verbatim.
Leave label placement to the engine (free space first); label_at only to fix a "covers page text"
warning, after widening the crop. Redact (solid) anything sensitive and set an explicit crop when redacting.
"""


def _skill_md() -> Optional[str]:
    here = Path(__file__).resolve()
    for p in (here.parents[2] / "skill" / "SKILL.md", Path("~/.agents/skills/ainotate/SKILL.md").expanduser(),
              Path("~/.claude/skills/ainotate/SKILL.md").expanduser()):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        start = text.find("## 2.")
        return text[start:] if start >= 0 else text
    return None


_WORKFLOW = """1. Capture: capture_web (targets for every element you will mark; cdp_url + page_url_contains
   for a logged-in tab), capture_window (list_windows first), capture_screen, clipboard_image, or
   the user's own file.
2. Locate: rects from capture_web (spec scale = returned scale), locate(image, "visible text") by
   OCR, or grid then zoom for icons. Never read coordinates off a preview: it is downscaled.
3. Annotate: it returns a draft by default (preview(spec) adds the debug overlay). Spec reference:
   ainotate://spec.
4. Verify the returned preview: each mark on its element, labels not covering what matters, no
   emails, tokens or customer data anywhere in the frame (redact, solid). Fix and repeat.
5. Deliver: call again with draft=false to save, put the path on its own line, refer to marks by
   number. compare, animate and make_guide also draft first: check, then draft=false."""


def spec_reference_text() -> str:
    import re

    from . import render
    skill = _skill_md()
    if skill:   # sections are numbered in the skill ("## 2. Locate"); here they stand alone
        skill = re.sub(r"(?m)^(#{2,3}) \d+\. ", r"\1 ", skill).strip() + "\n"
    head = "# AInotate spec reference\n\n"
    marks = "## All marks (from the renderer)\n\n```\n" + (render.__doc__ or "").strip() + "\n```\n"
    return head + (skill or "## Spec\n\n" + _FALLBACK_SPEC) + "\n" + marks


def annotate_guide_text(goal: str = "", url: str = "") -> str:
    where = f" on {url}" if url else ""
    return (f"Make a step-by-step visual guide{where}: {goal or 'the task the user described'}.\n\n"
            f"{_WORKFLOW}\n\nGuide rules: one image per step with one `step` mark (n = step number), "
            "same preset and crop width for every image, labels 1-4 words in the reader's language. "
            "Finish with make_guide(steps=[{image, text}], title, name): check its preview, then call it "
            "again with draft=false and give the user the file paths.")


def bug_ticket_text(issue: str = "", url: str = "", tracker: str = "") -> str:
    where = f" at {url}" if url else ""
    dest = f" for {tracker}" if tracker else ""
    return (f"Write a bug ticket{dest} about: {issue or 'the problem the user described'}{where}.\n\n"
            f"{_WORKFLOW}\n\nTicket rules: `bad` (red) only on the wrong thing, `step` marks for the "
            "reproduction steps, `good` for what is expected; for a fix use compare(before, after). "
            "Ticket body: Summary, Steps (numbers match the badges), Expected vs Actual, URL and "
            "environment, the attached image path (saved with draft=false). Use the ticket's language. "
            "copy_to_clipboard(path) lets the user paste the image.")

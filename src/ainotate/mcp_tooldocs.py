"""Tool descriptions, parameter descriptions and behavior hints for the MCP server.

Kept apart from mcp_server so the wiring stays short. Each description says what the tool does,
when to pick it over its siblings, what it returns and what it touches (files, clipboard,
permissions, network). Every tool answers with one JSON status (text + structuredContent) and,
when it makes an image, a JPEG preview of at most 1024 px; failures are `ok: false` with
`error.message` and `error.fix`.
"""
from typing import Annotated, Optional, Union

from mcp import types
from pydantic import Field

# ---------------------------------------------------------------- behavior hints

def _hints(read_only: bool, idempotent: bool = False, open_world: bool = False,
           destructive: bool = False) -> types.ToolAnnotations:
    return types.ToolAnnotations(read_only_hint=read_only, destructive_hint=destructive,
                                 idempotent_hint=idempotent, open_world_hint=open_world)


_LOCAL_READ = _hints(read_only=True, idempotent=True)
_LOCAL_WRITE = _hints(read_only=False)

ANN = {
    "annotate": _LOCAL_WRITE, "preview": _LOCAL_READ,
    "capture_web": _hints(read_only=False, open_world=True),
    "shoot": _hints(read_only=False, open_world=True),
    "locate": _LOCAL_READ, "capture_screen": _LOCAL_READ, "list_windows": _LOCAL_READ,
    "capture_window": _LOCAL_READ, "clipboard_image": _LOCAL_READ, "grid": _LOCAL_READ,
    "zoom": _LOCAL_READ, "make_guide": _LOCAL_WRITE, "compare": _LOCAL_WRITE, "animate": _LOCAL_WRITE,
    "copy_to_clipboard": _hints(read_only=False, idempotent=True, destructive=True),
    "doctor": _LOCAL_READ,
}

# ---------------------------------------------------------------- shared parameters

_DRAFT_TEXT = ("true (default): write to a temp folder so you can check the preview first. false: save "
               "to the output folder (~/Pictures/AInotate unless configured) and the backup folder.")

SPEC = Annotated[Union[dict, str], Field(description=(
    "Render spec as a JSON object (or a JSON string). Full schema: resource ainotate://spec."))]
DRAFT = Annotated[bool, Field(description=_DRAFT_TEXT)]
IMAGE_PATH = Annotated[str, Field(description="Path of a PNG, JPEG or WebP image on this machine (~ allowed).")]
NAME = Annotated[str, Field(description="Base file name in the output folder, no extension. Empty = tool default.")]
LOOK = Annotated[Optional[str], Field(description=(
    'Mark style: "default" (bold, built in) or the name of an installed look package (e.g. "neat"). '
    "Omit it to use the spec's look, else the user's default (AINOTATE_LOOK, config.toml). A look "
    "that is not installed or fails falls back to default with a warning listing the installed looks."))]
TARGETS_WEB = Annotated[Optional[dict[str, dict]], Field(description=(
    'Named elements to measure: {"name": {"text": "Checkout"} | {"selector": "css"} | '
    '{"role": "button", "name": "Save"}}, each with optional "within", "nth" (0-based), "exact", "fit".'))]
ACTIONS = Annotated[Optional[list[dict]], Field(description=(
    'Steps run in the page before the capture, in order: {"click": T}, {"fill": T, "value": "..."}, '
    '{"wait": T}, {"scroll_to": T}, {"wait_ms": 500}; T is a target as in targets.'))]
REGION = Annotated[Optional[Union[list[int], str]], Field(description=(
    'Part of the screen: [x, y, w, h] in screen points, or the same four numbers as a string '
    '"x,y,w,h". Omit for the whole main display (all displays on Windows).'))]
X1 = Annotated[float, Field(description="Left edge of the region, source image px.")]
Y1 = Annotated[float, Field(description="Top edge of the region, source image px.")]
X2 = Annotated[float, Field(description="Right edge of the region, source image px (greater than x1).")]
Y2 = Annotated[float, Field(description="Bottom edge of the region, source image px (greater than y1).")]

def _p(tp, text):
    return Annotated[tp, Field(description=text)]


# capture_web
URL = _p(Optional[str], "Page to load (http/https). Omit when attaching with cdp_url.")
CDP_URL = _p(Optional[str], "DevTools address of a running browser to attach to, e.g. http://127.0.0.1:9222; "
             "captures its logged-in tab instead of launching a new browser.")
PAGE_URL_CONTAINS = _p(Optional[str], "With cdp_url: substring of the URL of the tab to capture.")
PRESET = _p(str, 'Viewport: "laptop" 1280x800@2, "wide" 1600x1000@2 or "phone" 390x844@3.')
HIDE_OVERLAYS = _p(bool, "Hide cookie banners and similar overlays before the capture and after each click.")
FULL_PAGE = _p(bool, "Capture the whole scrollable page instead of the viewport.")
TIMEOUT_MS = _p(float, "Time limit in ms for loading and for each action or target.")
BROWSER = _p(str, 'Engine to launch: "chromium" (default), "firefox" or "webkit".')
USER_DATA_DIR = _p(Optional[str], "Browser profile folder to reuse (cookies, logins) when launching.")
# locate
TEXT = _p(str, "Visible text to find: a word or short phrase as it appears on the image.")
ALL = _p(bool, "Also list weaker matches, not only the best ones.")
NTH = _p(Optional[int], "Pick one of several equal matches, 0-based in reading order.")
WITHIN = _p(Optional[list[float]], "Search only inside [x1, y1, x2, y2], image px.")
EXACT = _p(bool, "Require the exact text (case and spacing), not a close match.")
LANG = _p(Optional[str], 'OCR language code, e.g. "en", "ro", "de". Omit for the default.')
BACKEND = _p(Optional[str], 'OCR engine: "vision" (macOS), "winrt" (Windows), "tesseract", or omit for auto.')
# desktop
FILTER = _p(Optional[str], "Case-insensitive substring of the app name or window title.")
WINDOW_ID = _p(Union[int, str], "Window id from list_windows.")
TARGETS_AX = _p(Optional[dict], 'macOS only. Named controls to measure: {"name": {"element": "label", '
                '"role"?: "button", "nth"?: 0, "exact"?: true}}.')
GRID_STEP = _p(int, "Grid spacing in image px (> 0).")
ZOOM_STEP = _p(int, "Fine grid spacing in source px (> 0).")
# export
STEPS = _p(list[dict], 'Guide steps in order: [{"image": path, "text": "...", "title"?: "...", "alt"?: "..."}].')
TITLE = _p(str, "Guide title.")
FORMATS = _p(Optional[list[str]], 'Any of "md", "html", "pdf". Default md + html.')
BEFORE = _p(str, "Path of the before image.")
AFTER = _p(str, "Path of the after image.")
LABELS = _p(Optional[list[str]], 'Two labels, before then after. Default ["Before", "After"].')
LAYOUT = _p(str, '"auto" (picks by aspect ratio), "side" (side by side) or "stack" (one above the other).')
FRAMES = _p(list[str], "Image paths, one per frame, in order. Frames are scaled to the first one.")
FORMAT = _p(str, '"apng" (full color, default) or "gif".')
DURATION_MS = _p(Optional[int], "Time per frame in ms (default 1500).")
DURATIONS_MS = _p(Optional[int], "Old name of duration_ms; used only when duration_ms is not given.")
LOOP = _p(int, "Repeat count; 0 = forever.")
CROSSFADE_MS = _p(int, "Length of a blended transition between frames in ms; 0 = cut.")


# ---------------------------------------------------------------- descriptions

_DRAFT_FLOW = ("Draft by default (temp folder): check the preview, then call again with draft=false to save "
               "to the output folder. ")

DESC = {
"annotate": (
    "Draw marks on an existing image: numbered steps, boxes, arrows, labels, highlights, spotlight, "
    "text and solid redaction. Use it once you have an image and exact rects (from capture_web, "
    "capture_window targets, locate, or grid + zoom); for a web page in one call use shoot instead. "
    + _DRAFT_FLOW +
    'spec: {"input": path, "scale": 1|2.., "crop": "auto"|[x1,y1,x2,y2], "name": "...", "marks": '
    '[{"type": "step"|"box"|"arrow"|"highlight"|"spotlight"|"redact"|"text"|..., "rect": {x,y,w,h} or '
    '[x1,y1,x2,y2], "label": "...", "n": 1, "color": "look|bad|good|info"}]}. Every coordinate in one '
    "unit; scale converts it to image px. Full reference: resource ainotate://spec. Writes PNG files "
    "only; never changes the input image. Returns paths, image size, warnings, legend (step numbers "
    "+ labels), redactions and a preview."),
"preview": (
    "Debug render of an annotate spec: draws the marks plus cyan target rects and magenta label "
    "boxes so you can see why a label moved or a mark misses its element. Use it when the annotate "
    "preview looks wrong; it is not a deliverable. Writes only a temp file, saves nothing to the "
    "output folder. Returns the temp path, warnings and the preview."),
"capture_web": (
    "Screenshot a web page in a headless browser (Playwright) and measure named elements, so you can "
    "annotate with exact rects. Use it for web UIs; for desktop apps use capture_window, for what is "
    "on screen right now capture_screen. Pass url to load a page, or cdp_url + page_url_contains to "
    "attach to a browser that is already running and logged in (the tab is captured as is). Loads "
    "the page over the network; actions (click, fill) run in that page and can change state on the "
    "site, so only pass actions the user asked for. Presets: laptop 1280x800@2, wide 1600x1000@2, "
    "phone 390x844@3. Needs ainotate[web] and a Playwright browser (doctor checks). Returns the PNG "
    "path (temp folder), rects in CSS px, scale (use it as the spec scale), missing targets with the "
    "reason, a spec stub and a preview with the rects outlined."),
"shoot": (
    "One call from URL to annotated image: capture_web + annotate, with marks that point at "
    'elements by "target" instead of "rect". Use it for web pages when you know the visible text, '
    "role or selector of every element; use capture_web + annotate when you need to inspect rects "
    "first. spec = render spec without input/scale, plus url (or cdp_url + page_url_contains), preset, "
    "actions and marks with \"target\": {...} (same forms as capture_web). Emails, tokens, card "
    "numbers and password fields are redacted with solid blocks automatically. Loads the page over "
    "the network; actions run in the page. " + _DRAFT_FLOW +
    "Returns paths, warnings, legend, redactions, the capture's resolved rects and a preview."),
"locate": (
    "Find visible text on an image by OCR and return its rect, for images where you have no DOM or "
    "accessibility data (desktop captures, the user's screenshots). Rects are [x1,y1,x2,y2] in image "
    "px (spec scale 1), best match first; several equally good matches = ambiguous: ask the user, or "
    "pass nth (0-based, reading order) or within [x1,y1,x2,y2]. all=true also lists weaker matches. "
    "Runs on this machine (macOS Vision, Windows OCR or Tesseract); reads the image, writes nothing. "
    "If the text is not found, use grid + zoom. Returns matches with score and a preview outlining them."),
"capture_screen": (
    "Screenshot the whole screen or one region, as the user sees it right now. Use it for something "
    "that spans several windows or is not in any one window; prefer capture_window for one app "
    "(no overlap from other windows) and capture_web for web pages (exact element rects). macOS needs "
    "the Screen Recording permission for the app that runs this server (Claude Desktop, the "
    "terminal...); without it the capture comes back blank and the error says how to fix it. Linux "
    "needs mss (X11) or grim (Wayland). Writes a PNG to the temp folder. Returns its path and "
    "size, and a preview; then locate elements with locate or grid + zoom and annotate with scale 1."),
"list_windows": (
    "List the visible windows: [{id, app, title, bounds}], to get the id for capture_window. filter = "
    "case-insensitive substring of the app name or title. Reads window metadata only (on macOS the "
    "titles need the Screen Recording permission). Returns count, windows and a suggestion."),
"capture_window": (
    "Screenshot one window by id (from list_windows); never moves, resizes or focuses it. On macOS "
    "other windows covering it do not show; on Windows and Linux they may, since the fallback grabs "
    "the window's screen area. Use it for desktop apps; capture_screen for several windows, capture_web "
    "for web pages. macOS only: targets {name: {\"element\": label, \"role\"?, \"nth\"?, \"exact\"?}} "
    "measures buttons and fields through Accessibility (needs that permission) and returns their "
    "rects plus the scale for the annotate spec. Needs Screen Recording on macOS. Writes a PNG to the "
    "temp folder. Returns its path, size, window_id, rects and scale when targets were given, and a preview."),
"clipboard_image": (
    "Save the image that is on the clipboard (e.g. a screenshot the user just copied) as a PNG in the "
    "temp folder, so you can annotate it. Use it when the user says they copied a screenshot; for a "
    "file they sent, pass its path to annotate or locate directly. Reads the clipboard, does not "
    "change it. Linux needs wl-paste or xclip. Returns the PNG path, size and a preview."),
"grid": (
    "Overlay a labelled coordinate grid every `step` px on an image, to find roughly where an element "
    "is when locate cannot read it (icons, images, no text). Then call zoom on that area for the exact "
    "rect. Writes the grid image to the temp folder; the input is not changed. The preview IS the "
    "grid, labels in source image px. Returns the grid path and the source image size."),
"zoom": (
    "Enlarged crop of [x1,y1,x2,y2] with a fine grid labelled in SOURCE image px, so you can read an "
    "exact rect straight off the preview (spec scale 1). Use it after grid, or to check a rect from "
    "locate. Keep regions under ~500 px wide or the labels get too dense. Writes the crop to the temp "
    "folder; the input is not changed. Returns the zoom path, region, size, notes and the preview."),
"make_guide": (
    "Build a step-by-step guide from annotated images: Markdown, HTML and/or PDF, one image and text "
    "per step, every image at its own size. Use it after annotate when the user wants a how-to or "
    "documentation; for a single image just deliver the annotate output. " + _DRAFT_FLOW +
    "PDF uses Playwright when available, else a Pillow page per step. Writes files only. Returns the "
    "file path per format, the PDF backend, warnings and a preview of the first step."),
"compare": (
    "Before/after plate: two images side by side or stacked, each with a label (default \"Before\", "
    "\"After\"; pass labels in the audience's language). Use it to show a UI change or a fix. "
    + _DRAFT_FLOW + "Writes one PNG; the inputs are not changed. Returns the path, size and a preview."),
"animate": (
    "Animation from image paths, one frame per image: APNG (full color, default) or GIF (plays "
    "everywhere: Slack, mail, Jira). Use it to show a short sequence of states; for steps the user "
    "should read, make_guide is clearer. " + _DRAFT_FLOW +
    "The preview shows the first frame. Writes one file; the inputs are not changed. Returns the "
    "path, frame count, format, size and a preview."),
"copy_to_clipboard": (
    "Put an image on the system clipboard so the user can paste it into Slack, Jira or mail. It "
    "REPLACES whatever the clipboard holds, so tell the user, and use it only when they want to "
    "paste; otherwise give them the file path. Works on macOS, Windows and Linux (wl-copy or xclip). "
    "Returns the path that was copied."),
"doctor": (
    "Check what AInotate can do on this machine: Pillow, font, output folder, Playwright and its "
    "browser, a CDP browser, screen capture permission, window listing, OCR engines, clipboard and "
    "mcp. Use it first when a tool fails with a missing dependency or permission, or before a long "
    "task on a new machine. Read-only: changes nothing. Returns ok, os, every check with its status "
    "and the exact fix for each failed one."),
}

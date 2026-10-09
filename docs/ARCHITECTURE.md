# AInotate architecture

AInotate turns a screenshot plus a few element positions into a polished annotated image
(Skitch-style arrows, numbered steps, boxes, callout labels, solid redaction), so AI agents can
show instead of describe. It runs on macOS, Windows and Linux, with any browser.

License: AGPL-3.0-or-later. Third-party notices in `NOTICE`.

## Layout

```
pyproject.toml            package "ainotate", entry point `ainotate = ainotate.cli:main`
src/ainotate/
  __init__.py             __version__
  __main__.py             `python -m ainotate` == the CLI
  style.py                palette, label fills, shadow, fonts (bundled Inter), mark_unit()
  geometry.py             rect helpers, edge_point, overlap_ratio, Skitch arrow outline
  spec.py                 validate(spec) -> marks; raises SpecError
  placement.py            content-aware label placer (prefix-sum content map, rule edge discount)
  label_plan.py           plans all automatic labels together (several orders, best total score)
  render.py               render(spec, debug=False) -> RenderResult
  output.py               config + save(image, name, draft=False) -> list[Path]
  tools.py                grid(), zoom(), info()
  cli.py                  argparse front end; maps errors to exit codes
  fonts/Inter-Bold.ttf    bundled (SIL OFL 1.1) + OFL.txt
  capture/
    __init__.py
    web.py                Playwright capture orchestration; also attaches to any
                          running Chromium over CDP (BrowserOS neo, Chrome --remote-debugging-port)
    web_targets.py        web target resolution, candidate matching, fit & layout arrangement
    desktop.py            screen / region / window capture per OS
    clipboard.py          image from the clipboard per OS
    neo.py                neo-latest fallback (BrowserOS neo auto-captures)
  shoot.py                targets -> rects: OCR text targets + auto privacy on images
                          (prepare_image_spec, called by render) and the web flow
                          url + marks with `target` -> capture -> privacy scan -> render -> save
  mcp_server.py           MCP stdio server exposing the same operations (Claude Desktop etc.)
skills/ainotate/          the agent skill (SKILL.md + references/), uses the `ainotate` CLI
tests/                    pytest; no network in unit tests; web tests marked `web`
docs/                     this file
```

## Contracts (keep stable; other modules depend on them)

- `ainotate.spec.SpecError(Exception)`: invalid spec; message lists every problem. Subclasses for
  targets: `AmbiguousText(SpecError)` (`.candidates`, CLI exit 5), `TargetNotFound(SpecError)`
  (`.near`, exit 6) and `TextNotFound(TargetNotFound)` (OCR found no match).
- `ainotate.render.RenderError(Exception)`: valid spec that cannot be drawn (no room for a label,
  rect outside the image or crop, frame too small).
- `ainotate.render.render(spec: dict, debug: bool = False) -> RenderResult`
  - `RenderResult.image`: `PIL.Image.Image`, RGB; RGBA only when `frame.bg` is "transparent".
    `RenderResult.warnings: list[str]`, `RenderResult.redactions: list[str]` (privacy.summary()
    lines: kind, count, masked preview; never a secret value).
  - Order: validate -> resolve `"target": {"text", "nth"?, "within"?, "exact"?}` marks by OCR on
    `input` (rects in spec units, `scale` aware; `pad` still applies) -> `"privacy": "auto"` adds
    solid redact marks for what OCR reads (default "off" for images) -> crop -> marks ->
    `beautify.apply_frame(img, spec["frame"], u)` last, `u` = the mark unit of the final crop.
  - Automatic redactions are tagged `"auto": true`: they never drive `crop: "auto"`, never fail
    the "rect outside the image/crop" checks and are not label obstacles.
  - Pure: no printing, no `sys.exit`, no file writes (OCR reads the input).
- `ainotate.output.save(image, name: str = "", draft: bool = False) -> list[pathlib.Path]`
  - draft: temp dir, one path. Otherwise `<output_dir>/<prefix> <YYYY-MM-DD at HH.MM.SS> <name>.png`
    and, if configured, an identical copy in `<backup_dir>`; never overwrites (" (2)" suffix).
  - Config precedence: env (`AINOTATE_OUTPUT_DIR`, `AINOTATE_BACKUP_DIR`, `AINOTATE_PREFIX`) >
    `~/.config/ainotate/config.toml` (keys `output_dir`, `backup_dir`, `prefix`) >
    defaults (`~/Pictures/AInotate`, no backup, prefix `AInotate`).
- `ainotate.capture.web.capture(url=None, *, cdp_url=None, page_url_contains=None,
  preset="laptop", targets: dict[str, dict] = {}, actions: list[dict] = [],
  hide_overlays=True, full_page=False, out_path=None, timeout_ms=30000,
  browser="chromium", user_data_dir=None, headless=True, on_page=None) -> WebCapture`
  - `WebCapture.image_path: Path`, `.scale: float` (image px per CSS px), `.viewport: (w, h)`,
    `.rects: dict[name, {"x","y","w","h"}]` in CSS px, `.missing: dict[name, reason]`,
    `.url` (sanitized), `.warnings`, `.extra` = what `on_page(page)` returned; `.near` = closest visible texts.
  - Order: settle -> actions -> resolve targets (ambiguous targets raise `AmbiguousTarget` with candidates) ->
    freeze -> scroll -> `on_page(page)` (privacy scan) -> screenshot at once. A MutationObserver monitors
    DOM mutations between `on_page` and the screenshot: redoes up to 3 times if changed.
  - Target forms: `{"text": "Checkout"}`, `{"selector": "css"}`, `{"role": "button", "name": "Save"}`,
    optional `"within": "css"`, `"nth": 0`, `"fit": "auto" | "text" | "element"`. Only visible elements count;
    iframes: same-origin elements resolved with frame offsets added.
  - Actions run before measuring: `{"click": <target>}`, `{"fill": <target>, "value": "..."}`,
    `{"wait": <target>}`, `{"scroll_to": <target>}`, `{"wait_ms": 500}`.
  - Presets: laptop 1280x800@2, wide 1600x1000@2, phone 390x844@3 (mobile + iPhone UA).
  - With `cdp_url`, attach to an existing browser, pick the tab whose URL contains
    `page_url_contains` (or open `url` in a new tab), emulate the preset on that tab only and
    restore it afterwards.
- `ainotate.capture.desktop`: `screen(out_path=None, region=None, display=None) -> Path`,
  `list_windows(filter=None) -> list[dict(id, app, title, bounds)]`, `window(id, out_path=None) -> Path`.
- `ainotate.capture.clipboard.grab(out_path=None) -> Path` (raises if no image on the clipboard).
- `ainotate.shoot.shoot(spec: dict, draft=False) -> ShootResult(paths, warnings, capture, legend,
  redactions, size)`: spec = render spec without `input`/`scale`, plus `url` (or `cdp_url` +
  `page_url_contains`), `preset`, `actions`, `full_page`, `hide_overlays`, `timeout_ms`, `browser`,
  `user_data_dir`, and marks that use `"target": {...}` (web forms above) or `keys` marks with `target`.
  The whole spec is validated before a browser starts. Defaults: `crop: "auto"`, no frame, `privacy: "auto"`
  (DOM scan of the live page: filled password fields, keys, tokens, cards, emails; empty password fields stay visible).
  The raw unredacted capture is a temporary file deleted in a `finally` block. A mark whose target is missing
  raises `TargetNotFound` with closest visible texts (`.near`). Browser chrome without `url` shows sanitized page address.
  `legend` = `[{"n", "label"}]` of the step marks; `redactions` = summary lines.
- `ainotate.shoot.prepare_image_spec(spec) -> (spec, redactions)`: the image half (OCR targets,
  auto privacy); render() calls it.

## Spec (input to render)

See `skills/ainotate/SKILL.md`. Marks: step, box, arrow, highlight, spotlight, redact, text, magnify, click,
keys, blur, pixelate. All coordinates in one unit; `scale` converts to image pixels. Top-level keys
beyond the marks: `crop` ("auto" | "tight" | [x1,y1,x2,y2] | {x,y,w,h}), `crop_pad`, `dim`,
`arrow_style` ("skitch" | "straight" | "curved" | "line"), `name`, `frame` (true | background |
{bg, pad, radius, shadow, chrome, url, theme, aspect, balance}), `privacy` ("auto" | "off" |
{kinds, allow, patterns}).

## CLI

`ainotate --help` lists the commands by group; every `ainotate COMMAND --help` ends with an example
and the exit codes. Commands are registered in `cli.py` with `@command(name, summary, example,
args)`; modules behind an optional extra are imported only when their command runs (`LAZY`).

- Annotate: `annotate SPEC` and `shoot SPEC`, both with `--draft`, `--debug`, `--privacy auto|off`
  (overrides the spec), `--copy` (clipboard) and `--json` (`{paths, warnings, legend, redactions,
  size, copied}` on stdout). Without `--json`: paths on stdout, `WARN:` / `REDACTED:` on stderr.
- Capture: `capture`, `screen`, `windows`, `window`, `clipboard`, `neo-latest`. Flags: `--browser`,
  `--user-data-dir`.
- Find things on an image: `locate`, `grid`, `zoom`, `info`.
- Share: `guide`, `animate` (apng | gif), `compare`, `copy`.
- Setup: `doctor`, `mcp` (stdio server with stdout guarded).

Exit codes (single source: `cli.EXIT_CODES`): 0 ok, 1 cannot save/export, 2 invalid spec or usage,
3 render error, 4 capture failed or a backend missing (browser, OCR engine, optional extra; also
`doctor` with a failed required check), 5 ambiguous target, 6 target not found.

Extras: `web` (playwright), `mcp` (mcp>=2), `ocr` (Apple Vision / Windows.Media.Ocr bindings),
`desktop` (Quartz / mss), `all`.

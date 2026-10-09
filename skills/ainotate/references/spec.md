# AInotate spec and CLI reference

Everything the renderer accepts. `SKILL.md` has the workflow; this file is for lookups.

## Two kinds of spec

**`annotate` spec** (an image you already have):

| key | meaning |
|---|---|
| `input` | image path (required) |
| `scale` | spec units -> image px. DOM rects: the `scale` from `capture`; OCR/grid/zoom px: `1` |
| `marks` | list of marks (required; a typo here would save the raw shot, so it is checked) |
| `name` | words appended to the saved file name |
| `crop` | `"auto"` (default for shoot; keeps context around marks), `"tight"` (marks + padding only), `[x1,y1,x2,y2]` or `{x,y,w,h}`; omit for the full frame |
| `crop_pad` | margin around the marks for `"auto"` and `"tight"` (default 260 image px at 2000px width) |
| `dim` | spotlight darkness 0-1 (default 0.55) |
| `arrow_style` | `"skitch"` (default, tapered), `"straight"`, `"curved"`, or `"line"` |
| `look` | mark style: `"default"` (built in) or an installed look package such as `"neat"`; see below |
| `frame` | backdrop and window chrome, see below |
| `privacy` | `"off"` (default for images), `"auto"` (OCR the image, redact findings) or an object |

**`shoot` spec** (a web page): the same keys without `input` and `scale`, plus:

| key | meaning |
|---|---|
| `url` | page to open |
| `cdp_url` + `page_url_contains` | attach to a running Chromium and reuse the tab whose URL contains the string (or open `url` there) |
| `preset` | `laptop` 1280x800 @2 (default), `wide` 1600x1000 @2, `phone` 390x844 @3 with an iPhone user agent |
| `actions` | run in order before measuring, see below |
| `full_page` | capture the whole document instead of the viewport |
| `hide_overlays` | hide cookie banners and consent layers (default true) |
| `browser` | `chromium` (default), `firefox`, `webkit` (launch mode only) |
| `timeout_ms` | navigation and wait timeout (default 30000) |

**Looks.** `"look"` picks the mark style. `"default"` is the only one built in; other packages add
looks (entry point group `ainotate.looks`, a subclass of `ainotate.looks.Look`), for example the
separate `ainotate-neat` package adds `"neat"`. Pick one per spec, with `--look` on `annotate` and
`shoot`, or with the MCP `look` argument; without one, env `AINOTATE_LOOK`, then `look` in
`~/.config/ainotate/config.toml`, then `"default"`. A look that is not installed, or fails to
load, start or draw, never breaks a render: the default look is used and a warning says why and
lists the installed looks.

`shoot` defaults `crop` to `"auto"` and `privacy` to `"auto"`. With chrome `"browser"` and no
`url` in the frame, the address bar shows the captured page's address.

## Targets and actions

A mark can carry `"target"` instead of `"rect"`:
- web (`shoot`, `capture`): `{"text": "Checkout"}`, `{"selector": "css"}`,
  `{"role": "button", "name": "Save"}`; optional `"within": "css"`, `"nth": 0`,
  `"exact": true`, `"fit": "auto" | "text" | "element"`. Only visible elements count. Same-origin iframes
  are measured with the frame offset added; cross-origin frames cannot be read.
- image (`annotate`, by OCR): `{"text": "Save"}` with optional `nth`, `within`, `exact`.

A text target resolves to the element that holds the text. By default, `fit: "auto"` shrinks
to the text bounds if the element is substantially wider; set `fit: "element"` to keep the
full container outline, or `fit: "text"` to always snap tightly to the words.

Actions: `{"click": T}`, `{"fill": T, "value": "..."}`, `{"wait": T}`, `{"scroll_to": T}`,
`{"wait_ms": 500}`, where `T` is a target. Open menus with a `click` before the marks that point
into them.

## Marks

| type | needs | options |
|---|---|---|
| `step` | `rect` or `target`, `n` | `label`, `label_at`, `arrow`, `box`, `badge`, `pad`, `color` |
| `box` | `rect` or `target` | `label`, `label_at`, `arrow`, `box`, `pad`, `color` |
| `arrow` | `rect` or `target`, `label` | `label_at`, `color` |
| `click` | `at` or `rect`/`target` (center) | `label`, `label_at`, `arrow`, `color` |
| `keys` | `keys` (1-6 names), `at`, `rect` or `target` | `at` = top-left; with `rect`/`target` keycaps sit beside it like a label |
| `magnify` | `rect` (what to enlarge) | `at` (loupe center in source units, else a calm free spot), `zoom` 1.2-8 (default 2), `shape` `circle`/`rounded`, `color` |
| `highlight` | `rect` or `target` | `color` |
| `spotlight` | `rect` or `target` | spec `dim` |
| `text` | `text`, `at` (top-left) | `color` |
| `redact` | `rect` or `target` | `fill`, `pad` (default outset; `0` allowed). Solid. The only mark for secrets |
| `blur` | `rect` or `target` | `radius`. Decorative; refused with `"sensitive": true` |
| `pixelate` | `rect` or `target` | `block`. Decorative; blur and pixelation can be reversed |

Option details:
- `label_at: [x, y]` is the label's top-left corner, in spec units.
- `"arrow": false` drops the arrow from a label; `"box": false` drops the outline.
- `pad` is the gap between target and outline (spec units); `0` supported on box and redact.
- `badge`: `tl tr bl br l r t b none`. Default picks the spot covering the least text.
- `color`: `look` (orange, default), `bad` (red, wrong/bug only), `good` (green), `info`
  (blue), or any `#hex` / css color. Labels use a deeper shade of the same hue.

Warnings (printed, image still saved): more than 6 marks, labels over 4 words, labels that
overlap each other or cover another mark's target, crossing arrows, a loupe that had to shrink.
Errors (nothing saved): unknown keys or types, rects mostly outside the image or crop, a label
with no room. The message names the mark number.

## Frame

`"frame": true` (auto background), a preset name, a color, a list of 2-5 colors, or an object:

| key | values |
|---|---|
| `bg` | `auto` (from the screenshot), `sunset ocean slate mint lavender graphite white`, `transparent`, `#hex`, `["#hex", "#hex"]` |
| `chrome` | `null`, `browser` (traffic lights + address bar), `window` |
| `url` | address bar text (needs `chrome: "browser"`) |
| `theme` | chrome colors `auto`, `light`, `dark` |
| `aspect` | `16:9`, `4:3`, `1:1`, `twitter`, `linkedin`, `W:H` |
| `pad`, `radius`, `shadow` | px, px, true/false/0-1 |
| `balance` | shift so off-center content looks centered |

## Privacy

`"privacy": "auto"` finds and solidly redacts: `password_field api_key jwt token credit_card iban
email phone ip name_field address_field`, plus `custom` regexes. On the web it reads the live DOM
(input values included) on the frozen page right before the screenshot; on images it uses OCR.
Password inputs with entered values are solidly redacted; empty password fields stay visible so
login forms remain readable. `name_field` and `address_field` are detected from the live DOM only.
An object narrows it:

```json
"privacy": {"kinds": ["email", "api_key", "token"],
            "allow": ["support@example.com", "@example.com"],
            "patterns": ["ORD-[0-9]{6}"]}
```

Embedded frames from another site cannot be read: they are reported as a warning, not
redacted. Auto-redaction is a safety net, not a guarantee: read the image before sending.

## CLI

| command | what |
|---|---|
| `shoot SPEC` | url -> capture -> targets -> privacy -> render -> save |
| `annotate SPEC` | render a spec on an image (`-` reads stdin) |
| `capture URL` | screenshot + rects as JSON: `--preset`, `--target name=JSON`, `--action JSON`, `--cdp URL`, `--tab-contains STR`, `--full-page`, `--out`, `--browser`, `--user-data-dir` |
| `locate IMG TEXT` | OCR text -> `[x1,y1,x2,y2]`: `--all`, `--json`, `--nth`, `--within`, `--exact`, `--lang`, `--backend` |
| `grid IMG` / `zoom IMG x1 y1 x2 y2` / `info IMG` | find positions by eye, at full resolution |
| `screen`, `windows [FILTER]`, `window ID`, `clipboard` | desktop sources |
| `window ID --target NAME=JSON` | macOS: rects of UI elements (`{"element", "role"?, "nth"?, "exact"?}`) in window points plus the spec `scale`, as JSON |
| `elements --app NAME` | macOS: the labeled UI elements of a running app (`--role`, `--filter`, `--json`) |
| `install-skill` | copy this skill to `~/.claude/skills` and `~/.agents/skills`; `--zip PATH` for an upload |
| `guide [STEPS]` | Markdown + HTML (+ PDF) guide: `--step IMG TEXT` (repeatable), `--formats md,html,pdf`, `--title`, `--intro`, `--out-dir` |
| `compare A B` | before/after plate: `--labels`, `--layout side/stack/auto` |
| `animate FRAMES...` | APNG (default) or `--format gif`, `--duration ms`, `--crossfade ms` |
| `copy IMG` | image to the clipboard |
| `doctor` | checks every dependency and permission, prints the exact fix (`--json`) |
| `mcp` | MCP stdio server (see `mcp.md`) |
| `neo-latest` | newest BrowserOS neo auto-screenshot (fallback, see `neo-capture.md`) |

Flags on `annotate` and `shoot`: `--draft` (temp dir, nothing in the output folder), `--copy`
(also to the clipboard), `--json` (prints `paths, warnings, legend, redactions, size`),
`--privacy auto|off` (default off for `annotate`, auto for `shoot`), and `--debug` (draws
the target rects and label boxes without saving). Every command has `--help` with an example.

Steps file for `guide`: `[{"image": "1.png", "text": "Open Settings", "alt": "..."}]` or
`{"title": "...", "intro": "...", "steps": [...]}`; relative image paths resolve next to it.
A step's optional `"title"` is a short bold summary next to its number; `"text"` then
explains it below, and can use `**bold**`, `` `code` `` and lines starting with `- ` as a list.

## Output

Final images: `<output_dir>/<prefix> <YYYY-MM-DD at HH.MM.SS> <name>.png`, plus an identical
copy in `backup_dir` if set. Never overwrites (adds " (2)"). Configuration, highest first:
env `AINOTATE_OUTPUT_DIR`, `AINOTATE_BACKUP_DIR`, `AINOTATE_PREFIX`, `AINOTATE_LOOK`; then
`~/.config/ainotate/config.toml` keys `output_dir`, `backup_dir`, `prefix`, `look`; then defaults
`~/Pictures/AInotate`, no backup, prefix `AInotate`, look `default`. A spec's `look` beats both.

## More patterns

- **Before/after:** two images, same preset and crop; `good` on the result; `compare`.
- **Tiny controls:** `magnify` the control; keep the crop wide enough for orientation.
- **Dark pages:** `spotlight` barely shows; crop tight instead.
- **Shortcuts:** a `keys` mark beside the control it triggers.
- **Guides:** one image per step, same preset and crop width, alt text that says the action.

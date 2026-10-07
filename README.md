# AInotate

**Annotated screenshots for AI agents. Show, don't describe.**

AInotate turns a web page, a window or any screenshot into a clear annotated
image: numbered steps, Skitch-style arrows, boxes, labels, a magnifier,
keycaps, solid redaction and an optional polished frame. It is built for AI
agents that would otherwise write "top right, under the second menu..."
in a chat reply, a bug ticket or a how-to guide.

You describe the marks in a small JSON spec. AInotate finds the elements
(by text, role or CSS selector on web pages, by OCR on images), places the
labels where they cover the least content, redacts secrets and saves the
result. The agent then reads the image to check it before sending.

## Gallery

Every image below was made with `shoot` against a public site, in one
call: open the page, find the elements, place the labels, draw, frame.
The specs are in [docs/images/specs](https://github.com/dontpayfull/AInotate/tree/main/docs/images/specs).

![Hacker News with three Skitch-style arrows: Discussion, Post a link and a numbered Sign in step, in a browser window on a sunset gradient](https://raw.githubusercontent.com/dontpayfull/AInotate/main/docs/images/skitch-arrows.png)
<p align="center"><b>Skitch-style arrows</b>: tapered, with a soft shadow; labels sit in free space and the arrows reach the target</p>

| | |
|---|---|
| ![The DontPayFull home page: step 1 on the store search box, step 2 on Join now, a blue arrow to Saving tips](https://raw.githubusercontent.com/dontpayfull/AInotate/main/docs/images/dontpayfull.png) | ![The Playwright repository on GitHub: the Code button outlined green as Clone it, an arrow to the Star button, the About text highlighted](https://raw.githubusercontent.com/dontpayfull/AInotate/main/docs/images/github.png) |
| **Numbered steps** on the busy header of [DontPayFull](https://www.dontpayfull.com), coupons for 20,000+ stores | **Box, arrow and highlight** on GitHub |
| ![Three numbered steps in the Appearance panel of a Wikipedia article, framed in a browser window on a blue gradient](https://raw.githubusercontent.com/dontpayfull/AInotate/main/docs/images/step-by-step.png) | ![A bug report: one table outlined green as sorted, the other red as not sorted, with every email address blacked out automatically](https://raw.githubusercontent.com/dontpayfull/AInotate/main/docs/images/bug-report.png) |
| **Step-by-step guide** with browser chrome and a gradient frame | **Bug report**, emails redacted automatically by the privacy scan |
| ![A circular magnifier enlarging a small edit link, next to keycaps for Alt Shift E](https://raw.githubusercontent.com/dontpayfull/AInotate/main/docs/images/loupe-keys.png) | ![Before and after plate: a checkbox and a Remove button, then the message It's gone](https://raw.githubusercontent.com/dontpayfull/AInotate/main/docs/images/before-after.png) |
| **Magnifier and keycaps** for tiny controls and shortcuts | **Before / after** plate from two shots |

![The same two arrows drawn in the four arrow styles: skitch, curved, straight and line](https://raw.githubusercontent.com/dontpayfull/AInotate/main/docs/images/arrow-styles.png)
<p align="center"><b>Four arrow styles</b>: <code>skitch</code> (default: straight when the way is clear, a gentle bend when it is long and diagonal, a curve around text), <code>curved</code>, <code>straight</code>, <code>line</code></p>

<p align="center">
<img src="https://raw.githubusercontent.com/dontpayfull/AInotate/main/docs/images/phone.png" width="360" alt="Wikipedia on a phone-sized viewport with the menu and search buttons numbered">
<br><b>Phone preset</b>: mobile viewport, touch and user agent
</p>

## Labels that stay off the content

![Left: labels at a fixed spot under each link cover the first headline. Right: AInotate puts the same labels in free space and points at the links with arrows](https://raw.githubusercontent.com/dontpayfull/AInotate/main/docs/images/placement.png)

A label dropped at a fixed spot next to its target usually lands on the
text the reader needs. AInotate places every label itself:

- It maps the page content first (text, icons, lines, images) and tries
  hundreds of spots per label. Empty space near the target wins; a spot
  over text is used only when the frame has no free room.
- Arrows are priced too: a long arrow, or one that crosses text, another
  label or another mark, loses to a shorter, cleaner one. When the
  straight path crosses text, the arrow curves around it.
- All labels are planned together, so the first one cannot take the only
  clean spot a later one needs.
- Step badges sit on the corner of their box that covers the least.

The image is never enlarged to make room. If a label still has to cover
something, AInotate says so in a warning, and `label_at` pins a label
exactly where you want it.

## Quickstart (30 seconds)

AInotate needs Python 3.10+:

```bash
pipx install "ainotate[all]"   # or: uv tool install "ainotate[all]"
ainotate doctor                # checks everything, prints exact fixes
```

`doctor` tells you the one command that downloads Chromium for web
capture. Then save this as `hn.json`:

```json
{"url": "https://news.ycombinator.com",
 "frame": {"chrome": "browser"},
 "marks": [
  {"type": "step", "n": 1, "target": {"text": "new", "exact": true},
   "label": "Newest"},
  {"type": "step", "n": 2, "target": {"text": "login"},
   "label": "Sign in"}]}
```

```bash
ainotate shoot hn.json --draft    # temp file; drop --draft to save
```

The output path is printed on stdout. Final images go to
`~/Pictures/AInotate` unless you configure another folder.

## Features

- **Marks:** numbered `step`, `box`, `arrow`, `click` ripple, `keys`
  (keycaps), `magnify` (loupe), `highlight`, `spotlight`, `text`, solid
  `redact`, and decorative `blur` / `pixelate`.
- **Placement that reads well:** labels go to free space near their
  target and are planned together; arrows are tapered Skitch-style
  shapes with a soft shadow that curve around text (see
  [Labels that stay off the content](#labels-that-stay-off-the-content)).
  Warnings for overlapping labels, crossing arrows, more than 6 marks or
  labels over 4 words.
- **Web capture in one call:** `ainotate shoot` opens a page (laptop, wide
  or phone preset), runs actions (click, fill, wait, scroll), measures
  each target, redacts, annotates and saves.
- **Any browser:** its own Playwright Chromium, Firefox or WebKit, or
  attach over CDP to a Chromium you already use and are logged into
  (Chrome, Edge, Brave, Arc, BrowserOS). The preset is applied to that one
  tab and restored afterwards.
- **Any image:** `locate` finds text by OCR (Apple Vision, Windows OCR or
  Tesseract); `grid` and `zoom` locate icons at full resolution. Screen,
  window and clipboard capture are built in.
- **Frames:** gradient backgrounds (auto from the screenshot, or presets),
  rounded corners, shadow, browser or window chrome, social aspect ratios.
- **Sharing:** guides in Markdown, HTML and PDF; before/after plates;
  APNG and GIF animations; copy to the clipboard.
- **Strict specs:** a typo stops the run with a list of every problem
  instead of saving a wrong or unredacted image. Clear exit codes.
- **Diacritics:** bundled Inter font, so Romanian, German, French and
  other Latin-script labels render correctly everywhere.

## For AI agents

**Skill.** [skill/SKILL.md](https://github.com/dontpayfull/AInotate/blob/main/skill/SKILL.md) is an agent skill (Claude Code
and other agents that read `SKILL.md`). Copy or link the `skill` folder
into your agent's skills directory as `ainotate`. It teaches the workflow:
pick a source, locate exact rects, annotate, **read the image to verify**,
deliver. References cover the full spec, every browser surface an agent
may have (Playwright MCP, Chrome DevTools MCP, Claude in Chrome, CDP) and
the MCP server.

**MCP server.** `ainotate mcp` exposes the same operations to Claude
Desktop and any MCP client. Each tool returns JSON plus a small preview
image so the model can check its work. Claude Desktop config:

```json
{"mcpServers": {
  "ainotate": {"command": "ainotate", "args": ["mcp"]}}}
```

Paths for macOS and Windows, permissions and the tool list:
[skill/references/mcp.md](https://github.com/dontpayfull/AInotate/blob/main/skill/references/mcp.md).

**Python.** The same pipeline is importable:

```python
from ainotate.shoot import shoot
res = shoot({"url": "https://en.wikipedia.org/wiki/Screenshot",
             "marks": [{"type": "box",
                        "target": {"text": "View history"},
                        "label": "Past edits"}]}, draft=True)
print(res.paths[0], res.redactions)
```

## Privacy

- **Redaction is solid.** `redact` paints an opaque block. AInotate never
  uses blur to hide data: blur and pixelation can be reversed. `blur` and
  `pixelate` exist only to de-emphasize clutter, and a mark flagged
  `"sensitive": true` is refused for them.
- **Automatic redaction is on for web shots.** `shoot` scans the live
  page (text and input values) for emails, phone numbers, card numbers,
  IBANs, API keys, tokens, JWTs, password and personal-data fields, and
  blacks them out. On images it runs on OCR when you pass
  `--privacy auto`. You can allow-list your own addresses or add regexes.
- **It is best-effort.** Detection misses things: text drawn in images or
  canvas, unusual formats, data split across elements, embedded frames
  from other sites (reported, not redacted). Always look at the image
  before you share it. The skill makes this check mandatory for agents.
- **Local.** Capture, OCR and rendering run on your machine. AInotate makes
  no network requests of its own beyond loading the pages you ask it to
  capture.

## Platform support

| | macOS | Windows | Linux |
|---|---|---|---|
| Annotate, frames, export | verified | experimental | experimental |
| Web capture (Playwright, CDP) | verified | experimental | experimental |
| Screen and window capture | verified | experimental | experimental (X11) |
| OCR (`locate`, text targets) | verified (Vision) | experimental (Windows OCR) | experimental (Tesseract) |
| Clipboard | verified | experimental | experimental |
| MCP server | verified | experimental | experimental |

**Experimental** means implemented and unit-tested with mocks, not yet
verified on real Windows or Linux machines. Reports are welcome.

## Exit codes

`0` ok, `1` cannot save, `2` invalid spec or usage, `3` cannot draw
(no room for a label, rect outside the image), `4` capture failed or an
optional dependency is missing, `5` a text target is ambiguous, `6` a
target was not found. Messages say what to fix.

## License

Copyright © 2026 [DontPayFull](https://www.dontpayfull.com).
AInotate is free software under the
[GNU Affero General Public License v3.0 or later](https://github.com/dontpayfull/AInotate/blob/main/LICENSE). If you run a
modified AInotate as a network service, the AGPL requires you to offer its
source to the users of that service.

Bundled and derived third-party work (the Inter font, arrow proportions
from Arrowshot, a label-placement idea from github/awesome-copilot) and
dependency licenses are listed in [NOTICE](https://github.com/dontpayfull/AInotate/blob/main/NOTICE).

---

<p align="center">
Made with ❤️ by the <a href="https://www.dontpayfull.com">DontPayFull</a> team<br>
<sub>Coupons &amp; discount codes for 20,000+ stores</sub>
</p>

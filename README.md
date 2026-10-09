# AInotate

**Annotated screenshots for AI agents: the agent shows you the button instead of describing where it is.**

[![Tests](https://github.com/dontpayfull/AInotate/actions/workflows/tests.yml/badge.svg)](https://github.com/dontpayfull/AInotate/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/ainotate)](https://pypi.org/project/ainotate/)
[![Python](https://img.shields.io/pypi/pyversions/ainotate)](https://pypi.org/project/ainotate/)
[![License: AGPL-3.0-or-later](https://img.shields.io/badge/license-AGPL--3.0--or--later-blue)](https://github.com/dontpayfull/AInotate/blob/main/LICENSE)
[![MCP server](https://img.shields.io/badge/MCP-server-8A2BE2)](https://github.com/dontpayfull/AInotate/blob/main/skill/references/mcp.md)

![Hacker News with Skitch-style arrows: Discussion, Post a link and a numbered Sign in step, in a browser window on a sunset gradient](https://raw.githubusercontent.com/dontpayfull/AInotate/main/docs/images/skitch-arrows.png)

Ask an agent how to post a link on Hacker News and you usually get
directions: the orange bar at the top, the last link after *jobs*. With
AInotate it sends the picture above instead and says "click *submit*,
where the blue arrow points".

It captures a web page, a window or a screenshot you already have, finds
the elements you mean, draws numbered steps, arrows, boxes and labels
where they hide the least, blacks out emails and tokens, and looks at the
result before sending it. It works in Claude Code, Cowork, Claude Desktop,
Cursor and any other MCP client, and from the command line or Python.

We built it at [DontPayFull](https://www.dontpayfull.com) because our own
agents kept answering "where is it?" with a paragraph. A picture with a
number on the right button settles the question in a second, in a chat,
a ticket or a guide.

## What it is for

The obvious case is "where is it?": a setting three menus deep, a button
that is only an icon. The agent replies with the screen, the control boxed
and numbered. For "walk me through it" it makes one image per step, and
`ainotate guide` binds them into a Markdown, HTML or PDF guide.

Some steps belong to you, not the agent: a permission switch, a 2FA code,
a payment or consent screen. The agent doesn't click those. It shows what
to press and says what the click will do.

It also helps with bug reports (what is wrong in red, what is right in
green, personal data blacked out before anyone sees it) and with docs,
where the screenshot points at the thing the text is about and can be
re-shot from the same spec when the UI changes.

## Set it up in your agent

**1. Install**, one of:

| With | Command |
|---|---|
| Homebrew (macOS) | `brew install dontpayfull/tap/ainotate` |
| pipx or uv (Python 3.10+) | `pipx install "ainotate[all]"` or `uv tool install "ainotate[all]"` |
| Claude plugin or Desktop extension | nothing to install first: step 2 brings AInotate along |

Then `ainotate doctor` checks the machine and prints the exact fix for
anything missing, including the one command that downloads Chromium for
web capture. To update later: `brew upgrade ainotate`, `pipx upgrade
ainotate` or `uv tool upgrade ainotate`; the plugin with `/plugin
marketplace update ainotate`; the Desktop extension by opening the newer
`.mcpb` from Releases.

**2. Connect it** to the agent you use:

| Agent | How |
|---|---|
| Claude Code | `/plugin marketplace add dontpayfull/AInotate`, then `/plugin install ainotate@ainotate`: skill and MCP server in one step |
| Cowork | Customize > Plugins > Add marketplace > `dontpayfull/AInotate`, then install AInotate |
| Claude Desktop | download `ainotate-<version>.mcpb` from [Releases](https://github.com/dontpayfull/AInotate/releases/latest) and open it: a one-click extension |
| Cursor, VS Code, other MCP clients | command `ainotate`, arguments `mcp` (stdio); also listed in the [MCP Registry](https://registry.modelcontextprotocol.io) as `io.github.dontpayfull/ainotate` |

```json
{"mcpServers": {"ainotate": {"command": "ainotate", "args": ["mcp"]}}}
```

AInotate runs on your own computer: Cowork reaches it through the Claude
desktop app, so keep the app open while a task uses it. The plugin starts
the installed `ainotate`, or runs it with `uvx` when it is not installed.

The **skill** teaches an agent when and how to annotate: pick a source,
find exact positions, annotate, read the image to verify, deliver. The
plugin brings it along; without the plugin it ships with the package:

```bash
ainotate install-skill             # Claude Code and Codex (~/.claude/skills, ~/.agents/skills)
ainotate install-skill --zip ~/Desktop   # a ZIP for Claude Desktop, Cowork, claude.ai:
                                         # Customize > Skills > Upload a skill
```

Config paths for every OS, permissions and the tool list:
[skill/references/mcp.md](https://github.com/dontpayfull/AInotate/blob/main/skill/references/mcp.md).

**3. Ask.** "Show me where to switch Wikipedia to dark mode." The agent
captures the page, annotates it, checks it and sends the image. With the
skill it also does this on its own whenever an answer depends on where
something is on the screen: a "how do I" question, a visual bug, a
before/after of a change.

## Gallery

Every image below was made in one `shoot` call against a public site:
open the page, find the elements, place the labels, draw, frame. The specs
are in [docs/images/specs](https://github.com/dontpayfull/AInotate/tree/main/docs/images/specs).

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

## From the command line

Save this as `hn.json`:

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

The output path is printed on stdout. Images are saved to
`~/Pictures/AInotate` unless you configure another folder. `ainotate --help`
lists every command (capture, annotate, locate by OCR, grid, zoom, window
capture with UI element targets, elements, guide, compare, animate, copy,
install-skill); the full spec is in
[skill/references/spec.md](https://github.com/dontpayfull/AInotate/blob/main/skill/references/spec.md). Exit codes: `0` ok,
`1` cannot save, `2` invalid spec, `3` cannot draw, `4` capture failed or an
optional dependency is missing, `5` ambiguous text target, `6` target not
found.

**Python.** The same pipeline is importable:

```python
from ainotate.shoot import shoot
res = shoot({"url": "https://en.wikipedia.org/wiki/Screenshot",
             "marks": [{"type": "box",
                        "target": {"text": "View history"},
                        "label": "Past edits"}]}, draft=True)
print(res.paths[0], res.redactions)
```

## Features

**What it draws.** Numbered steps, boxes, arrows with labels, a click
ripple, keycaps for shortcuts, a magnifier for small controls,
highlights, a spotlight that dims everything else, free text, and solid
redaction (`blur` and `pixelate` exist too, for clutter only). Labels go
to free space and are planned together, as described
[above](#labels-that-stay-off-the-content). It warns when labels overlap,
arrows cross, or a frame carries more than six marks.

**Where the images come from.** `ainotate shoot` opens a page in a
laptop, wide or phone viewport, runs clicks and form fills, finds each
target by text, role or CSS selector, redacts, draws and saves in one
call. It uses its own Chromium, Firefox or WebKit, or attaches over CDP to
a Chromium you are already logged into (Chrome, Edge, Brave, Arc,
BrowserOS) and puts that tab back the way it was. Any other image works
too: `locate` finds text by OCR (Apple Vision, Windows OCR or Tesseract),
`grid` and `zoom` pin down icons, and screen, window and clipboard capture
are built in. On macOS, `ainotate window ID --target` reads buttons,
switches and fields straight from the Accessibility API, so a mark sits
exactly on the control even when it is just an icon.

**What you can do with them.** Frame them on a gradient with browser or
window chrome, build guides in Markdown, HTML or PDF, put a before and an
after side by side, make an APNG or GIF, or copy the result to the
clipboard. A spec with a typo stops with every problem listed rather than
saving a wrong or unredacted image, and every failure has its own exit
code. The bundled Inter font renders accents and diacritics the same on
every machine.

## Privacy

Redaction paints a solid block. AInotate never blurs data to hide it,
because blur and pixelation can be reversed; marks flagged
`"sensitive": true` are refused for them.

On web shots automatic redaction is on: before the screenshot, `shoot`
reads the live page, input values included, and blacks out emails, phone
numbers, card numbers, IBANs, API keys, tokens, JWTs and password or
personal-data fields. On images it does the same by OCR when you pass
`--privacy auto`. You can allow-list your own addresses and add patterns.

Detection is best-effort. It can miss text drawn into images or canvas,
odd formats, data split across elements, and frames embedded from other
sites (those are reported, not redacted). Look at the image before you
share it; the skill makes agents do exactly that. Everything runs on your
machine, and AInotate makes no network requests beyond the pages you ask
it to capture.

## Platform support

| | macOS | Windows | Linux |
|---|---|---|---|
| Annotate, frames, export | verified | experimental | experimental |
| Web capture (Playwright, CDP) | verified | experimental | experimental |
| Screen and window capture | verified | experimental | experimental (X11) |
| UI elements (`window --target`, `elements`) | verified (Accessibility) | not yet | not yet |
| OCR (`locate`, text targets) | verified (Vision) | experimental (Windows OCR) | experimental (Tesseract) |
| Clipboard | verified | experimental | experimental |
| MCP server | verified | experimental | experimental |

**Experimental** means implemented and unit-tested with mocks, not yet
verified on real Windows or Linux machines. Reports are welcome.

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

<!-- mcp-name: io.github.dontpayfull/ainotate -->

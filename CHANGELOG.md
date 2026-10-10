# Changelog

All notable changes to AInotate.

## 0.1.8 - 2026-10-10

### Changed
- Guides: a wider page so full-window screenshots stay readable, an optional bold `title` per
  step, `**bold**`, `` `code` `` and `- ` lists in step text, and click-to-zoom on a picture (#1).
- Guide images keep their own size by default; `--width N` still puts them on one canvas (#2).
- Tesseract runs on one thread, so one OCR pass no longer takes every core (#2).
- `--privacy auto` without an OCR engine says how to fix it: install one, or use `--privacy off`
  (with rect coordinates when marks use text targets) (#2).

### Added
- Looks: other packages can supply a look for the marks (entry point group `ainotate.looks`,
  a subclass of `ainotate.render.Look`), picked with `"look"` in the spec, `--look`, the MCP
  `look` argument, `AINOTATE_LOOK` or `look` in config.toml. The built-in look stays the default
  and renders pixel-identical. A look that fails to load falls back to the default with a
  warning (#3).

Thanks to @testy-cool for #1, #2 and #3.

## 0.1.7 - 2026-10-10

### Added
- A logo: a winking mouse pointer (`docs/brand/`), used in the README, the Desktop extension
  and the directory listings.

### Fixed
- The MCP server reports its version and website in `serverInfo` (was an empty version).

## 0.1.6 - 2026-10-10

### Added
- Gemini CLI extension (`gemini-extension.json`): `gemini extensions install
  https://github.com/dontpayfull/AInotate` brings the skill and the MCP server.
- Codex: the repository works as a Codex plugin marketplace too
  (`codex plugin marketplace add dontpayfull/AInotate`).
- Cursor one-click install link and `npx skills add dontpayfull/AInotate` in the README.
- `llms-install.md`: install steps an agent (Cline and others) can follow on its own.
- `glama.json` for the Glama directory; `scripts/bump.py` sets the version in every manifest.

### Changed
- The skill moved from `skill/` to `skills/ainotate/`, the folder every agent and skill
  directory looks in. The package still installs it with `ainotate install-skill`.
- MCP Registry entry: `uvx --with` instead of `--from`, so clients that add the package
  version to the command (VS Code and others) start the server correctly.
- The plugin and extension launchers pin `ainotate[all]` to the release they ship with.

## 0.1.5 - 2026-10-09

### Changed
- README: "What it is for", Features and Privacy back to short scannable lists.

## 0.1.4 - 2026-10-09

### Added
- Homebrew: `brew install dontpayfull/tap/ainotate` (tap `dontpayfull/homebrew-tap`, follows
  each PyPI release on its own).

### Changed
- README rewritten in plain prose: why we built it, what it is for, features and privacy;
  install options as a table, every CLI command, how to update each install.

## 0.1.3 - 2026-10-09

### Added
- Claude plugin and marketplace in the repository: `/plugin marketplace add dontpayfull/AInotate`
  in Claude Code, Customize > Plugins > Add marketplace in Cowork; skill and MCP server together.
- Claude Desktop extension (`.mcpb`) attached to every release, installed and run with uv.
- Listed in the MCP Registry as `io.github.dontpayfull/ainotate`, published by the release workflow.

### Fixed
- UI element targets: a label only matches as a whole word and never when negated, so
  "Allow" can no longer pick "Don't Allow"; only the captured window is searched; a hung app
  times out; a tree cut short at 4000 elements says so instead of "not found"; a capture whose
  size is not a uniform multiple of the window gets image-pixel rects; duplicate app processes
  are reported with their pids; text typed into fields is never used as a label.
- Targets are checked before anything is captured, and malformed ones exit with code 2.
- `install-skill`: only a folder whose frontmatter names exactly `ainotate` is replaced; a
  link to another checkout is left alone; the installer never deletes its own source; an update
  copies beside the old skill and swaps, so a failed copy keeps the previous one; links inside
  the skill are not followed; `--zip` creates the folder and refuses to write inside the skill.
- `python -m ainotate.cli` works again; the MCP window capture preview outlines the targets.

## 0.1.2 - 2026-10-09

### Added
- `ainotate install-skill`: the agent skill now ships inside the package and installs into
  `~/.claude/skills` (Claude Code) and `~/.agents/skills` (Codex and others); `--zip` writes an
  upload for Claude Desktop, Cowork and claude.ai.
- macOS UI element targets: `ainotate window ID --target NAME='{"element": "Allow"}'` and the
  MCP `capture_window(id, targets)` measure buttons, switches and fields through the
  Accessibility API; `ainotate elements --app NAME` lists them.
- Skill and MCP instructions: steps only the user may take (permissions, 2FA, payments) are
  shown, never clicked, with what the click does.
- CI: unit tests on macOS and Linux for every push.

### Changed
- `doctor` names the app macOS gives privacy permissions to (Terminal, Claude, Cursor...),
  instead of "the app running this command".
- README: what AInotate is for, setup per agent first, Cowork, badges.

## 0.1.1 - 2026-10-07

### Changed
- PyPI metadata: keywords and classifiers, so the package shows up under screenshot, AI,
  documentation and testing categories; project links include DontPayFull.
- README: DontPayFull credit, copyright line and footer.

## 0.1.0 - 2026-10-07

First public release.

### Added
- Annotated screenshots from a JSON spec: numbered steps, boxes, labels, Skitch-style tapered
  arrows with a soft shadow, highlight, spotlight, text notes and solid redaction.
- `ainotate` package (src layout, AGPL-3.0-or-later) with the `ainotate` command and
  `python -m ainotate`. Rendering is pure: it returns the image and warnings.
- Content-aware placement: a map of the page content (text, icons, lines); labels go to free
  space near their target and are planned together; arrows are priced by length and by what
  they cross, curve around text and bend gently when long; step badges take the corner that
  covers the least. A warning when a label has to cover text. Four arrow styles: `skitch`,
  `curved`, `straight`, `line`.
- `grid`, `zoom` and `info` to read exact positions at full resolution; one units rule with
  `scale`.
- `ainotate shoot`: URL plus marks with `target` in one call: capture, run actions, measure,
  redact, annotate, save. Presets laptop, wide and phone.
- Web capture for any browser: own Playwright (Chromium, Firefox, WebKit) or attach over CDP
  to a running Chromium (Chrome, Edge, Brave, Arc, BrowserOS neo) with per-tab emulation that
  is always restored. Targets by text, role or CSS selector; actions click, fill, wait,
  scroll_to, wait_ms; overlay hiding; same-origin iframe offsets; retry over HTTP/1.1 when a
  navigation stalls silently.
- OCR text targeting on any image: `ainotate locate` and `"target": {"text": ...}` on marks,
  with Apple Vision, Windows.Media.Ocr or Tesseract. Exit 5 for ambiguous text, 6 for text
  not found (closest matches listed).
- Automatic privacy redaction: emails, phones, cards (Luhn), IBANs (mod 97), API keys, JWTs,
  tokens, password and personal-data fields, custom regexes and allow lists. On by default for
  web shots (live DOM, input values included), opt-in on images (OCR). Solid blocks only.
- Marks `magnify` (loupe), `click` (ripple and pointer), `keys` (keycaps), decorative `blur`
  and `pixelate` (refused for data marked sensitive); overlap and crossing warnings.
- Frames: auto or preset gradient backgrounds, rounded corners, soft shadow, browser or window
  chrome with the page address, aspect presets for social posts.
- Export: step guides in Markdown, HTML and PDF; APNG and GIF animations; before/after
  plates; copy to the clipboard.
- Desktop capture: screen, region, window list and window capture, clipboard image, on macOS,
  Windows and Linux. Windows: explicit ctypes signatures, DWM frame bounds, per-monitor DPI,
  multi-monitor origin.
- `ainotate doctor`: parallel checks of Python, Pillow, font, output folders, Playwright, CDP,
  screen recording permission, window listing, OCR, clipboard and MCP, each with the exact
  fix for the current OS; `--json`.
- MCP stdio server (`ainotate mcp`) for Claude Desktop and other clients: tools for shoot,
  capture, annotate, preview, locate, grid, zoom, desktop capture, guides, compare, animate,
  clipboard and doctor; JSON results with small previews; spec resource and two prompts.
- CLI flags `--draft`, `--debug`, `--copy`, `--json`, `--privacy`; exit codes 0 ok, 1 save,
  2 spec or usage, 3 render, 4 capture or missing backend, 5 ambiguous text, 6 not found.
- Configurable output: env, `~/.config/ainotate/config.toml`, defaults; never overwrites.
- Agent skill in `skill/` that drives the CLI, with references for the spec, browsers and the
  MCP server.
- Bundled Inter Bold (SIL OFL 1.1), so labels render the same everywhere, diacritics included.

### Platform status
- macOS verified. Windows and Linux are experimental: implemented and unit-tested with
  mocks, not yet verified on real machines.

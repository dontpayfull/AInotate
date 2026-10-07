# AInotate as an MCP server (Claude Desktop and other MCP clients)

`ainotate mcp` runs a stdio MCP server with the same operations as the CLI. Every tool returns
one JSON status (`ok`, paths, image size, warnings, suggestions) and, when it makes an image, a
JPEG preview of at most 1024 px so the model can check the result cheaply. Full-size files stay
on disk. Failures come back as `ok: false` with the problem and a suggested fix.

## Install

```bash
pipx install "ainotate[all]"     # or: uv tool install "ainotate[all]"
ainotate doctor                  # prints the Chromium download command
```

## Claude Desktop

Edit `claude_desktop_config.json` (Claude menu > Settings > Developer > Edit Config), then quit
and reopen Claude.

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json` (Windows support is experimental:
  implemented and unit-tested with mocks, not yet verified on real machines)

Installed with pipx or uv tool:
```json
{"mcpServers": {"ainotate": {"command": "ainotate", "args": ["mcp"]}}}
```

Without installing, through `uvx`:
```json
{"mcpServers": {"ainotate": {"command": "uvx",
  "args": ["--from", "ainotate[all]", "ainotate", "mcp"]}}}
```

Claude Desktop does not read your shell profile, so `ainotate` or `uvx` may not be on its PATH.
Use the full path from `which ainotate` (macOS) or `where ainotate` (Windows):
```json
{"mcpServers": {"ainotate": {
  "command": "/Users/you/.local/bin/ainotate", "args": ["mcp"]}}}
```
```json
{"mcpServers": {"ainotate": {
  "command": "C:\\Users\\you\\.local\\bin\\ainotate.exe", "args": ["mcp"]}}}
```

Optional environment (inside the server entry): `"env": {"AINOTATE_OUTPUT_DIR": "...",
"AINOTATE_CDP": "http://127.0.0.1:9222"}`.

### Permissions

- macOS screen and window capture need Screen Recording permission for the app that runs the
  server: **Claude** (System Settings > Privacy & Security > Screen & System Audio Recording),
  then restart Claude. The `doctor` tool checks it.
- Web capture needs Chromium downloaded once; `doctor` gives the command.

## Other clients

Any MCP client that starts stdio servers works with the same command and args: Claude Code
(`claude mcp add ainotate -- ainotate mcp`), Cursor, Windsurf, Zed and others, each in its own
config file.

## Tools

| tool | what it does |
|---|---|
| `shoot` | url (or `cdp_url` + `page_url_contains`) + marks with `target` -> annotated image (`draft: true` by default) |
| `capture_web` | screenshot + rects for named targets; returns `scale` and a spec stub |
| `annotate` | render a spec on an image (`draft: true` by default) |
| `preview` | draft with the debug overlay (cyan rects, magenta label boxes) |
| `locate` | OCR: rect of a visible text on any image; flags ambiguous matches |
| `grid`, `zoom` | locate icons and unlabeled areas at full resolution |
| `capture_screen`, `list_windows`, `capture_window`, `clipboard_image` | desktop sources |
| `make_guide` | Markdown / HTML / PDF guide from annotated steps (`draft: true` by default) |
| `compare` | before/after plate (`draft: true` by default) |
| `animate` | APNG or GIF from several images (`draft: true` by default) |
| `copy_to_clipboard` | put an image on the clipboard for pasting |
| `doctor` | check dependencies and permissions, with exact fixes |

Resource `ainotate://spec`: the spec reference. Prompts: `annotate-guide` (step-by-step guide),
`bug-ticket` (bug report with an annotated screenshot).

## Workflow the server expects

1. Capture (`shoot` / `capture_web` / desktop tools / the user's file).
2. Locate exact rects (targets, `locate`, or `grid` then `zoom`). Never read coordinates off a
   preview: previews are downscaled.
3. `annotate` (or `shoot`): returns a draft by default. Look at the returned preview, fix the spec.
4. Check that no email, token or customer data shows anywhere in the frame.
5. Call again with `draft: false` to save permanently and give the user the path.

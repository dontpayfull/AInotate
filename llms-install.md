# Installing AInotate (for AI agents such as Cline)

AInotate is a local MCP server over stdio. It needs no API key and no account, and makes no
network calls of its own except to the pages you ask it to capture.

1. Make sure `uv` is installed: `uv --version`. If it is missing, install it with
   `curl -LsSf https://astral.sh/uv/install.sh | sh` (macOS, Linux) or
   `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"` (Windows).
   The server needs Python 3.10 or newer; uv downloads one when needed.

2. Add the server to the MCP settings (for Cline: `cline_mcp_settings.json`):

   ```json
   {
     "mcpServers": {
       "ainotate": {
         "command": "uvx",
         "args": ["--from", "ainotate[all]", "ainotate", "mcp"],
         "disabled": false
       }
     }
   }
   ```

   If the user installed AInotate already (`brew install dontpayfull/tap/ainotate`,
   `pipx install "ainotate[all]"` or `uv tool install "ainotate[all]"`), use
   `"command": "ainotate", "args": ["mcp"]` instead.

3. Web capture needs Chromium once. Run
   `uvx --from "ainotate[all]" playwright install chromium`, or call the `doctor` tool,
   which prints the exact command for anything missing.

4. Check it works: call the `doctor` tool, then capture and annotate a public page, for
   example "Show me where the search box is on https://en.wikipedia.org", and look at the
   image it returns.

Notes:
- Desktop window capture and Apple Vision OCR are macOS-only. On macOS, the app that runs
  the agent (VS Code, Cursor, Terminal) needs the Screen Recording permission for window
  capture; `doctor` names the app to allow.
- The agent skill that teaches when and how to annotate is in `skills/ainotate/SKILL.md`;
  agents that read skills can install it with `uvx --from "ainotate[all]" ainotate install-skill`.

# Getting an image and element rects from any browser

AInotate needs two things: a screenshot file, and the rects of the elements you will mark in the
same units as a `scale` that converts them to image pixels. Pick the first option that works.

| Option | Logins | DOM privacy scan | Who measures |
|---|---|---|---|
| 1. `ainotate shoot` / `capture`, own browser | no (or `--user-data-dir`) | yes | AInotate |
| 2. `ainotate ... --cdp` attached to a running Chromium | yes | yes | AInotate |
| 3. Playwright MCP | its own profile | no: use OCR privacy | you |
| 4. Chrome DevTools MCP | yes | no: use OCR privacy | you |
| 5. Claude in Chrome | yes | no: use OCR privacy | you |

Options 1 and 2 do everything in one call. With 3-5 you capture and measure yourself, then run
`ainotate annotate` with `"privacy": "auto"` so OCR still catches emails and keys.

## 1. AInotate's own browser (Playwright)

```bash
ainotate shoot spec.json --draft          # url + marks with "target"
ainotate capture https://en.wikipedia.org/wiki/Screenshot \
  --target search='{"selector": "#searchInput"}'   # JSON: image_path, scale, rects
```
Needs `pip install "ainotate[web]"` and a Chromium download once (`ainotate doctor` prints the
exact command). Headless and logged out. For a persistent profile: `capture --user-data-dir DIR`.
Firefox or WebKit: `--browser firefox|webkit` (launch mode only).

## 2. Attach to a browser that is already running (CDP)

Any Chromium-based browser started with a remote debugging port: Chrome, Edge, Brave, Arc,
Vivaldi, Chromium, BrowserOS. AInotate reuses the tab whose URL contains a string (or opens a
new tab), emulates the preset on that tab only and restores it afterwards.

```bash
ainotate capture --cdp http://127.0.0.1:9222 --tab-contains example.com/cart \
  --target total='{"text": "Total"}'
```
In a `shoot` spec: `"cdp_url": "http://127.0.0.1:9222", "page_url_contains": "example.com/cart"`
(add `"url"` to open a new tab instead). Check the port first:
`curl -s http://127.0.0.1:9222/json/version`.

Starting a browser with a port (close it normally first):
- macOS: `open -na "Google Chrome" --args --remote-debugging-port=9222
  --user-data-dir="$HOME/.chrome-ainotate"`
- Windows: `"C:\Program Files\Google\Chrome\Application\chrome.exe"
  --remote-debugging-port=9222 --user-data-dir=%LOCALAPPDATA%\chrome-ainotate`
- Linux: `google-chrome --remote-debugging-port=9222 --user-data-dir=$HOME/.chrome-ainotate`

Recent Chrome versions refuse remote debugging on the default profile, hence the separate
`--user-data-dir` (sign in there once). Edge, Brave and Arc take the same flags. A debugging port
gives full control of that browser: keep it on 127.0.0.1 and close it when done.

**BrowserOS neo** already listens: its CDP port is `ports.cdp` in
`~/Library/Application Support/BrowserClaw/.browseros/config.json` (it changes on restart, so
read it each time; `ainotate doctor` reports it). Details and the manual fallback:
`neo-capture.md`.

Firefox and Safari cannot be attached. Use option 1 with `--browser firefox|webkit`, or capture
the window (`ainotate windows Safari`, `ainotate window ID`) and locate by OCR.

## The DOM-rect recipe (options 3-5)

Run this in the page (evaluate / script tool), in the same state you will capture:
```js
(() => {
  const el = [...document.querySelectorAll('button')]
    .find(b => b.textContent.trim() === 'Save' && b.getBoundingClientRect().width > 0);
  el.scrollIntoView({block: 'center'});
  const r = el.getBoundingClientRect();
  return {x: r.x, y: r.y, w: r.width, h: r.height,
          vw: innerWidth, dpr: devicePixelRatio};
})()
```
Then take the screenshot without scrolling, and compute
`scale = image width / vw` (`ainotate info shot.png` gives the real width). Do not assume
`scale = dpr`: tools often resize screenshots. Rects are viewport-relative; a full-page
screenshot needs `y + scrollY`. Iframes: add the frame's offset (see `neo-capture.md`).
Annotate with the rects as dicts and that scale:
```json
{"input": "shot.png", "scale": 2, "crop": "auto", "privacy": "auto",
 "marks": [{"type": "box", "rect": {"x": 812, "y": 96, "w": 88, "h": 36},
            "label": "Save"}]}
```

## 3. Playwright MCP

- Size the viewport with `browser_resize` (1280 x 800 is a good laptop frame).
- Measure with `browser_evaluate` and the function above.
- `browser_take_screenshot` with a `filename`: the reply names the saved file. Use that path as
  `input`. Device scale is usually 1, so `scale` is usually 1; compute it anyway.
- For logged-in pages it can run against a running browser (`--cdp-endpoint`). If it does,
  point `ainotate capture --cdp` at the same endpoint and let AInotate measure.

## 4. Chrome DevTools MCP

- `resize_page` for the viewport, `evaluate_script` with the function above.
- `take_screenshot` with `filePath` (PNG) writes a file you can use as `input`; without it the
  image only comes back inline and there is no file to annotate.
- When it is connected to your browser with `--browser-url http://127.0.0.1:9222`, the same
  port works for `ainotate capture --cdp http://127.0.0.1:9222`, which is simpler.

## 5. Claude in Chrome

Its screenshots come back into the conversation, not as files. Two ways:
- Capture the Chrome window yourself: `ainotate windows Chrome`, `ainotate window ID`, then
  `ainotate locate shot.png "Save"` (OCR) or `grid` + `zoom`. `scale: 1`.
- If you need DOM precision: measure with the page JavaScript tool and add the toolbar offset.
  Window image px = `(rect.x + (outerWidth - innerWidth) / 2, rect.y + outerHeight -
  innerHeight) * dpr`. This assumes no side panel or docked DevTools; check with `zoom`.

## Always

- Measure and capture in the same page state: no scrolling, no menu closing in between.
- Hide cookie banners in your own tab rather than clicking consent (AInotate's own capture does
  this by default).
- Read the result. Rects from another tool are your measurement, not AInotate's.

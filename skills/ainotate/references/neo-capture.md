# Capturing web pages in BrowserOS neo

Read this before capturing a page in BrowserOS neo. Every recipe here was hit in real runs.

## First choice: let AInotate attach over CDP

neo is a Chromium with a CDP port, so AInotate can do the whole job (per-tab viewport preset,
targets, overlays, privacy scan, restore) in one call. The port changes on restart; read it:
```bash
PORT=$(python3 -c 'import json,os;print(json.load(open(os.path.expanduser(
  "~/Library/Application Support/BrowserClaw/.browseros/config.json")))["ports"]["cdp"])')
ainotate capture --cdp http://127.0.0.1:$PORT --tab-contains example.com/cart \
  --target checkout='{"role": "button", "name": "Checkout"}'
```
Or a `shoot` spec with `"cdp_url": "http://127.0.0.1:<port>"` and `"page_url_contains"`.
`ainotate doctor` shows the port it found. Use the manual recipe below only when you must stay
inside neo's own tools (a tab that only your neo session can see, or CDP is unreachable).

## Manual recipe: measure, then capture

**Call 1, `run`:** size your tab, prepare the page, measure. Do not clear the override here.
```js
const mine = (await browser.pages.list()).find(t => t.ownership === "mine" && t.url.includes("example.com/cart"));
const p = mine ? browser.page(mine.pageId) : await browser.open("https://example.com/cart");
// neo's window spans a huge screen (~3552px): pages sprawl and text is tiny.
// Emulate a laptop viewport for THIS tab only (other tabs and the window are untouched).
await p.cdp("Emulation.setDeviceMetricsOverride",
  JSON.stringify({width: 1280, height: 800, deviceScaleFactor: 2, mobile: false}));
try {
  await p.wait({for: "selector", value: "button.checkout", timeout: 15000});  // open() returns before load
  const r = await p.evaluate({code: `
    hideOverlays();   // paste the helper from "Overlays" below
    const el = [...document.querySelectorAll('button')]
      .find(b => b.textContent.trim() === 'Checkout' && b.getBoundingClientRect().width > 0);
    el.scrollIntoView({block: 'center'});
    const b = el.getBoundingClientRect();
    return {x: b.x, y: b.y, w: b.width, h: b.height, vw: innerWidth, page: 'ok'};`});
  return {id: p.id, r: r.value};
} catch (e) {
  await p.cdp("Emulation.clearDeviceMetricsOverride", "{}");   // on error, leave the tab as found
  throw e;
}
```

**Call 2: the granular `screenshot` tool** on the same page, with `format: "png"` and
`size` = the preset's image size (the default caps at 1024x768):
`screenshot(page=<id>, format="png", size={width: 2560, height: 1600})`.
The result shows the image and its file, `[Image: source: /…/tool-results/mcp-browseros-neo-blob-….png,
original 2560x1600 …]`. Use that path as the spec `input`. This is the reliable path: it is exactly
your tab, at exactly the emulated size.

**Call 3, when done with the page:** `run` with `await browser.page(ID).cdp("Emulation.clearDeviceMetricsOverride", "{}")`.

Fallback, if your client shows no `source:` path: `p.screenshot()` inside `run` (returns only a
byte count; neo writes JPEGs to `~/.browserclaw/screenshots/`), then
`ainotate neo-latest --size 2560x1600` before clearing the override. Every neo call
auto-captures, and neo can switch your session silently, so check the printed age and Read the
image before using it.

| Preset | `setDeviceMetricsOverride` params | Image | `scale` |
|---|---|---|---|
| Laptop (default: chat, tickets, guides) | `width 1280, height 800, deviceScaleFactor 2, mobile false` | 2560x1600 | 2 |
| Wide (dashboards, big tables) | `1600, 1000, 2, false` | 3200x2000 | 2 |
| Phone (mobile bugs and guides) | `390, 844, 3, true` | 1170x2532 | 3 |

Only on your own tabs (`ownership === "mine"`). `p.cdp(method, paramsJson)` takes params as a
JSON string. Page handles: `p.id` is a number property; `pageId` exists only on `pages.list()` rows.
Rects are viewport-relative: measure in the same state you capture (no scrolling in between).

**Privacy on the manual path:** run AInotate's DOM scanner in the same `evaluate` and add solid
redactions for what it finds. Python gives you the code:
`python -c 'from ainotate.privacy import neo_code; print(neo_code())'` (paste it as the
`evaluate` body; it returns findings in CSS px, same units as your rects). Or set
`"privacy": "auto"` in the annotate spec to OCR the image. Either way, read the result.

## Phone preset still shows the desktop site
Check `innerWidth` after the override. If it is not 390, the site picks its layout by user agent:
```js
await p.cdp("Emulation.setUserAgentOverride", JSON.stringify({userAgent:
  "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"}));
await p.cdp("Page.reload", "{}");
```
Clear it afterwards with `Emulation.setUserAgentOverride` and an empty `userAgent`.
(`ainotate capture --preset phone` sets the user agent for you.)

## Overlays (cookie banners, consent iframes, chat widgets)
Hide them in your own tab; never click consent:
```js
function hideOverlays() {
  for (const e of document.querySelectorAll('body *')) {
    const s = getComputedStyle(e), id = (e.id + ' ' + e.getAttribute('class')).toLowerCase();
    const floating = s.position === 'fixed' || s.position === 'sticky' || e.tagName === 'IFRAME';
    // only floating layers: a page wrapper with class "has-consent" must stay visible
    if (floating && (/cmp|consent|cookie|onetrust|didomi|truste/.test(id) ||
        (/cookie|consent|privacy/i.test(e.textContent) && e.textContent.length < 800))) e.style.display = 'none';
  }
}
```
Keep the site's own sticky header unless it hides your target.

## Finding the right element
- Filter to visible: `getBoundingClientRect().width > 0` and not `visibility:hidden`.
- The same text often exists twice (nav and hidden menu, sidebar and table header). Pick the one
  inside the right container, or the first in document order: sort by `rect.y + scrollY`.
- Tiny inline links (11px text): pass the rect as is; the renderer keeps the outline tight.
- Below the fold: `el.scrollIntoView({block: 'start'}); scrollBy(0, -120)` (room for sticky
  headers), then measure in the same `evaluate`.
- Open menus and dropdowns: click in the same `run` (`p.act` or `el.click()` in `evaluate`), wait for
  the menu selector, then measure, then capture. The menu must still be open in call 2.

## Iframes (same-origin only)
Rects inside an iframe are relative to the iframe. Add its offset:
```js
const f = document.querySelector('iframe#result');
const fr = f.getBoundingClientRect();
const inner = f.contentDocument.querySelector('h1').getBoundingClientRect();
return {x: fr.x + f.clientLeft + inner.x, y: fr.y + f.clientTop + inner.y, w: inner.width, h: inner.height};
```
Cross-origin frames throw on `contentDocument`: mark the whole iframe instead, or open its URL in
its own tab.

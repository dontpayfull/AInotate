---
name: ainotate
description: Use when an answer depends on where something is or how it looks on a screen - "where is X", "how do I do Y in this app/site", explaining a UI or a screenshot the user sends, reporting a bug or writing a ticket (Jira, GitHub, Freshdesk, Slack) about something visible, showing before/after of a change, writing a step-by-step guide, or any reply that would otherwise say "top right, under the..." in words. Also when the user says "ainotate", "show me", "take a screenshot", "annotate".
---

# ainotate

Show, don't describe: an annotated screenshot plus short text that refers to the marks by
number. Plain-text or code answers: skip it.

## When to reach for it unprompted
- Explaining where a control or setting is on a website or app.
- Answering "where is / how do I" questions about a UI.
- Reporting a bug found during browsing, testing, or scraping.
- Whenever you are about to use spatial words ("top right", "under the second menu", "left sidebar").
- Verifying UI changes: before and after.

CLI `ainotate` (fallback `python -m ainotate`); `ainotate doctor` checks the machine. Every spec
option: `references/spec.md`. Exit codes: 2 bad spec, 3 cannot draw, 4 capture failed or a
dependency is missing (the message says what to install), 5 text matches several places (ask
which, or pass `nth`), 6 target not found (closest text listed). Fix the spec, never work around.

## 1. Pick a source

| Source | How |
|---|---|
| Public web page | `ainotate shoot spec.json`: capture, find targets, redact, annotate, save |
| Logged-in page | the same spec with `"cdp_url"` + `"page_url_contains"` (user's running browser). **Read `references/browsers.md`** for every browser surface |
| User's screenshot | their file; clipboard: `ainotate clipboard` |
| Desktop app | `ainotate windows <app>`, then `ainotate window <id>`; `ainotate screen` |

Never move or resize the user's windows; capture and crop.

## 2. Locate - never guess coordinates from a preview

The image you see when you Read a file is downscaled; positions read off it are wrong.
- Web: `"target"` on each mark (`shoot` resolves it), or `ainotate capture URL --target
  save='{"text": "Save"}'` for rects in CSS px plus the `scale`. Targets: `{"text"}`,
  `{"selector"}`, `{"role", "name"}`, optional `within`, `nth`, `exact`.
- Images with text: `ainotate locate img.png "Save"` (OCR), or `"target": {"text": "Save"}`.
- Icons: `ainotate grid img.png`, then `ainotate zoom img.png x1 y1 x2 y2` (fine grid in source
  px; one zoom per element, under ~500px wide). `ainotate info img.png` gives the real size.

**Units, one rule:** every coordinate (`rect`, `crop`, `label_at`, `at`, `pad`) is in one unit;
`scale` converts it to image pixels. DOM rects: dicts `{x,y,w,h}` with the returned scale.
OCR/grid/zoom pixels: lists `[x1,y1,x2,y2]` with `scale: 1`. Never mix.

## 3. Annotate

```json
{"url": "https://news.ycombinator.com", "name": "hn header",
 "marks": [
  {"type": "step", "n": 1, "target": {"text": "new", "exact": true}, "label": "Newest"},
  {"type": "step", "n": 2, "target": {"text": "login"}, "label": "Sign in"}]}
```
`ainotate shoot spec.json --draft`. `"actions"` (`click`, `fill`, `wait`, `scroll_to`) run
before measuring, e.g. to open a menu. Existing image: same marks with `"rect"`, plus `"input"`
and `"scale"`, then `ainotate annotate spec.json --draft`. Presets: `laptop`, `wide`, `phone`.

| type | use |
|---|---|
| `step` | numbered badge + outline; `n` matches your text |
| `box` | "this thing", with a label |
| `arrow` | label pointing at a target, no outline |
| `click` / `keys` | click ripple / keycaps like `["⌘", "K"]` |
| `magnify` | loupe that enlarges a small control |
| `highlight` / `spotlight` / `text` | tint / dim the rest / free note |
| `redact` | solid block for anything sensitive |
| `blur` / `pixelate` | decorative only; secrets always `redact` |

**Placement:** leave labels to the engine. It puts each label in free space near its target,
plans all labels together and curves arrows around text; it covers text only when the frame has
no room, and then warns. Fix that warning by widening the crop toward empty space (margins,
gutters) or shortening the label; use `label_at` only as a last resort, and never onto text.
**Crop:** findable in 2 seconds. `"auto"` (default) frames the marks; an explicit crop when a
landmark matters (header, sidebar) and always when redacting. Far-apart marks: one image each.
**Frame** (guides, docs): `"frame": {"chrome": "browser", "bg": "ocean"}`; tickets stay plain.
**Privacy:** `shoot` redacts emails, phones, cards, keys, tokens and password fields by default;
on images only with `"privacy": "auto"` (OCR). Best-effort: you still verify.
**Colors:** `look` orange (default), `bad` red (only wrong/bug), `good` green, `info` blue.
Max 6 marks, labels 1-4 words in the audience's language, UI text quoted verbatim. User's own
marks: keep them, use a color they did not, say which are yours.

## 4. Verify (required)

Read the output image. Each mark sits on its element, labels and arrows do not cover text that
matters, and **no sensitive data shows anywhere in the frame**, marked or not. `--debug` draws
the exact rects (cyan) and label boxes (magenta). Fix and rerun; then save without `--draft`.

## 5. Deliver

Saves go to the output folder (default `~/Pictures/AInotate`), never overwriting; `--copy` also
puts the image on the clipboard.
- **Chat:** image path on its own line, then 1-3 sentences: "At ① open the cart; ② ...".
- **Ticket:** Summary, Steps (numbers match badges), Expected vs Actual, URL/env, image, in
  the ticket's language.
- **Guide:** one image per step, same preset and crop width, then `ainotate guide steps.json`.
  Before/after: `ainotate compare a.png b.png`. Flows: `ainotate animate` (APNG or GIF).

## Patterns and mistakes

- **Tables:** `box` the whole row, label beside it via `label_at`, `"arrow": false`.
- **Dense pages:** a "covers page text" warning means no free room. Widen the crop first; then
  `label_at` into empty space next to its own target. Crossing arrows confuse more than they help.
- Measure the visible control (the bordered box), not an inner `<input>` or icon.
- Hidden, duplicate or offscreen element: the outline lands on nothing. Hidden at this preset
  (sidebar on phone): say so and show where it is instead. Never mark empty space.
- Red means wrong; for emphasis use `look`.

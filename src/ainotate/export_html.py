"""Guide HTML (self-contained, embedded images) and PDF writers. Re-exported by export."""
from __future__ import annotations

import base64
import html
import mimetypes
from pathlib import Path

from PIL import Image, ImageDraw

from . import style

_CSS = """
:root{color-scheme:light dark;--bg:#fff;--fg:#1d1d1f;--muted:#6e6e73;--accent:#0071e3;--line:#e5e5ea;--shadow:0 2px 6px rgba(0,0,0,.10),0 10px 30px rgba(0,0,0,.10)}
@media (prefers-color-scheme:dark){:root{--bg:#1c1c1e;--fg:#f5f5f7;--muted:#98989d;--accent:#2f8cff;--line:#3a3a3c;--shadow:0 2px 6px rgba(0,0,0,.5),0 10px 30px rgba(0,0,0,.5)}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:17px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;-webkit-font-smoothing:antialiased}
main{max-width:1100px;margin:0 auto;padding:56px 24px 80px}
h1{font-size:2.1rem;line-height:1.2;letter-spacing:-.02em;margin:0 0 12px}
.intro{color:var(--muted);font-size:1.1rem;margin:0 0 40px}
section.step{margin:0 0 48px;break-inside:avoid;page-break-inside:avoid}
h2{display:flex;gap:14px;align-items:flex-start;font-size:1.15rem;line-height:28px;font-weight:400;margin:0 0 16px;letter-spacing:-.01em}
h2 .n{flex:none;min-width:28px;height:28px;border-radius:14px;background:var(--accent);color:#fff;font-size:1rem;font-weight:700;display:inline-flex;align-items:center;justify-content:center}
img{display:block;max-width:100%;height:auto;border-radius:12px;box-shadow:var(--shadow);border:1px solid var(--line)}
h1,.intro,h2{max-width:860px}
section.step img{margin-left:42px;max-width:calc(100% - 42px)}
h2 .t{font-weight:600}
p.say{max-width:818px;margin:-8px 0 16px 42px;line-height:1.6}
img.zoomable{cursor:zoom-in}
#zoom{position:fixed;inset:0;z-index:10;display:flex;overflow:auto;padding:24px;background:rgba(0,0,0,.94);cursor:zoom-out;opacity:0;visibility:hidden;transition:opacity .2s ease,visibility 0s .2s}
#zoom.open{opacity:1;visibility:visible;transition:opacity .2s ease}
#zoom img{max-width:none;margin:auto;box-shadow:none;transform:scale(.96);transition:transform .2s ease}
#zoom.open img{transform:none}
@media (prefers-reduced-motion:reduce){#zoom,#zoom img{transition:none}}
@page{size:A4;margin:16mm}
@media print{#zoom{display:none}body{background:#fff;color:#000;font-size:12pt}main{max-width:none;padding:0}img{box-shadow:none;border:1px solid #ccc;border-radius:6px}section.step{margin-bottom:22pt}section.step img{margin-left:0;max-width:100%}h2 .n{-webkit-print-color-adjust:exact;print-color-adjust:exact}}
"""

# Click a step image shown smaller than its real size to see it full size; click or Esc closes.
_ZOOM_JS = (
    "const z=document.getElementById('zoom');"
    "const mark=()=>document.querySelectorAll('section.step img').forEach(i=>"
    "i.classList.toggle('zoomable',i.naturalWidth>i.clientWidth+4));"
    "addEventListener('load',mark);addEventListener('resize',mark);"
    "addEventListener('click',e=>{if(e.target.closest('#zoom')){z.classList.remove('open');return}"
    "const i=e.target.closest('img.zoomable');if(i){z.firstChild.src=i.src;z.classList.add('open')}});"
    "addEventListener('keydown',e=>{if(e.key==='Escape')z.classList.remove('open')});"
)


def _html(title, intro, steps) -> str:
    parts = []
    for i, s in enumerate(steps, 1):
        mime = mimetypes.guess_type(str(s["image"]))[0] or "image/png"
        b64 = base64.b64encode(s["image"].read_bytes()).decode("ascii")
        text = html.escape(s["text"]).replace("\n", "<br>")
        title = html.escape(s.get("title", ""))
        if title:
            head_html = f'<span class="t">{title}</span>'
            body = f'<p class="say">{text}</p>' if text else ""
        else:
            head_html, body = f"<span>{text or f'Step {i}'}</span>", ""
        parts.append(
            f'<section class="step"><h2><span class="n">{i}</span>{head_html}</h2>{body}'
            f'<img src="data:{mime};base64,{b64}" alt="{html.escape(s["alt"], quote=True)}"></section>')
    head = f"<h1>{html.escape(title)}</h1>" if title else ""
    lead = f'<p class="intro">{html.escape(intro).replace(chr(10), "<br>")}</p>' if intro else ""
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{html.escape(title or "Guide")}</title><style>{_CSS}</style></head>'
            f'<body><main>{head}{lead}{"".join(parts)}</main>'
            f'<div id="zoom"><img alt=""></div><script>{_ZOOM_JS}</script></body></html>')


def _pdf_playwright(html_path: Path, pdf_path: Path) -> None:
    from playwright.sync_api import sync_playwright   # ImportError -> caller falls back
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(html_path.resolve().as_uri())
            page.pdf(path=str(pdf_path), format="A4", print_background=True,
                     margin={"top": "16mm", "bottom": "16mm", "left": "16mm", "right": "16mm"})
        finally:
            browser.close()


def _body_font(size):
    for p in ("/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/Supplemental/Arial.ttf",
              "C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/TTF/DejaVuSans.ttf"):
        if Path(p).is_file():
            try:
                from PIL import ImageFont
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return style.load_font(size)


def _wrap(draw, text, font, width) -> list[str]:
    lines: list[str] = []
    for para in (text or "").split("\n"):
        cur = ""
        for word in para.split():
            trial = f"{cur} {word}".strip()
            if draw.textlength(trial, font=font) <= width or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
    return lines


MIN_IMAGE_H = 420   # px on a 150 dpi page (~7 cm): below this the image moves to the next page


def _pdf_pillow(title, intro, steps, pdf_path: Path) -> None:
    """A4 pages at 150 dpi, each step on a new page: badge + wrapped text, then the image.
    Text that does not fit continues on the next page; when less than MIN_IMAGE_H is left
    for the image (or its natural height, if smaller) it goes on a page of its own, so long
    step text never squashes the screenshot."""
    from .export import _open, _rgb   # lazy: export imports this module
    W, H, M = 1240, 1754, 118     # A4 at 150 dpi, ~20 mm margin
    pages: list = []
    st = {"d": None, "y": M}

    def new_page():
        page = Image.new("RGB", (W, H), "white")
        pages.append(page)
        st["d"], st["y"] = ImageDraw.Draw(page), M
        if title and len(pages) > 1:
            st["d"].text((M, M), title, font=_body_font(24), fill=(150, 150, 155))
            st["y"] += 56

    def text(lines, font, step, x, fill):
        for ln in lines:
            if st["y"] + step > H - M:
                new_page()
            st["d"].text((x, st["y"]), ln, font=font, fill=fill)
            st["y"] += step

    for i, s in enumerate(steps, 1):
        new_page()
        d = st["d"]
        if i == 1 and title:
            f = style.load_font(54)
            text(_wrap(d, title, f, W - 2 * M), f, 66, M, (29, 29, 31))
            st["y"] += 6
            if intro:
                fi = _body_font(30)
                text(_wrap(d, intro, fi, W - 2 * M), fi, 40, M, (110, 110, 115))
            st["y"] += 30
        if st["y"] + 2 * 22 + 46 > H - M:   # no room for the badge and a first line
            new_page()
        r, y0, d = 22, st["y"], st["d"]
        d.ellipse([M, y0, M + 2 * r, y0 + 2 * r], fill=(0, 113, 227))
        d.text((M + r, y0 + r), str(i), font=style.load_font(26), fill="white", anchor="mm")
        tf = style.load_font(36)
        st["y"] = y0 + 4
        text(_wrap(d, "\n".join(x for x in (s.get("title", ""), s["text"]) if x) or f"Step {i}", tf, W - 2 * M - 2 * r - 20), tf, 46, M + 2 * r + 20,
             (29, 29, 31))
        y = max(st["y"], y0 + 2 * r if st["d"] is d else 0) + 28
        im = _rgb(_open(s["image"]))
        box_w = W - 2 * M
        k_full = min(box_w / im.width, 1.0 if im.width < box_w else 9)
        if H - M - y < min(MIN_IMAGE_H, im.height * k_full):
            new_page()
            y = st["y"]
        box_h = H - M - y
        k = min(k_full, box_h / im.height)
        im = im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))), Image.LANCZOS)
        pages[-1].paste(im, (M, y))
        st["d"].rounded_rectangle([M - 1, y - 1, M + im.width, y + im.height], radius=10,
                                  outline=(210, 210, 215))
    pages[0].save(pdf_path, "PDF", save_all=True, append_images=pages[1:], resolution=150.0)

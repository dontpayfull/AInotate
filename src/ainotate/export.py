"""Share what you annotated: step guides (md/html/pdf), animated GIF/APNG, before/after plates,
and copy-to-clipboard. Pure functions; the command line front end is `ainotate.cli`.

GIF vs APNG: GIF is capped at 256 colors per frame, so gradients and anti-aliased text edges
band even with good quantization; APNG keeps full 24-bit color + alpha and is usually crisper
for screenshots. GIF is smaller on busy frames and plays everywhere (Slack, email, Jira).
`gif()` therefore defaults to APNG (`format="apng"`) and offers `format="gif"` for chat/mail."""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

from PIL import Image, ImageDraw

from . import style
from .export_html import (  # noqa: F401  (re-export)
    _CSS, _body_font, _html, _pdf_pillow, _pdf_playwright, _wrap,
)
from .output import load_config, save, slug


class ExportError(Exception):
    """An export could not be produced (bad input, missing tool, clipboard failure).
    `.input` names the offending argument when there is one ("steps[2].image", "before",
    "frames[1]", "format"...), so callers can say exactly what to fix."""

    def __init__(self, msg: str, input: Optional[str] = None):
        super().__init__(msg)
        self.input = input


# ---------------------------------------------------------------- helpers

def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d at %H.%M.%S")


def _base_dir(draft: bool) -> Path:
    """The configured output folder, or a fresh temp dir for drafts."""
    if draft:
        return Path(tempfile.mkdtemp(prefix="ainotate-"))
    d = load_config().output_dir
    d.mkdir(parents=True, exist_ok=True)
    return d


def _default_dir(kind: str, draft: bool = False) -> Path:
    cfg = load_config()
    base = _base_dir(draft)
    stem = f"{cfg.prefix} {_stamp()} {kind}"
    for n in range(1, 1000):
        d = base / (stem + (f" ({n})" if n > 1 else ""))
        try:
            d.mkdir(parents=True)      # exclusive: two guides in the same second never share a dir
            return d
        except FileExistsError:
            continue
    raise ExportError(f"no free folder name near {base / stem}")


def _free_path(path: Path) -> Path:
    if not path.exists():
        return path
    for n in range(2, 1000):
        p = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not p.exists():
            return p
    raise ExportError(f"no free file name near {path}")


def _open(path, what: Optional[str] = None) -> Image.Image:
    p = Path(path).expanduser()
    tag = f"{what}: " if what else ""
    if not p.is_file():
        raise ExportError(f"{tag}image not found: {p}", what)
    try:
        im = Image.open(p)
        im.load()
    except Exception as e:  # noqa: BLE001
        raise ExportError(f"{tag}cannot read image {p}: {e}", what)
    return im


def _rgb(im: Image.Image) -> Image.Image:
    if im.mode == "RGB":
        return im
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, "white")
        bg.paste(im, mask=im.getchannel("A"))
        return bg
    return im.convert("RGB")


# ---------------------------------------------------------------- guide

def _norm_steps(steps) -> list[dict]:
    if not steps:
        raise ExportError("guide needs at least one step", "steps")
    out = []
    for i, s in enumerate(steps, 1):
        if not isinstance(s, dict) or "image" not in s:
            raise ExportError(f"step {i}: needs an object with an 'image' path", f"steps[{i - 1}]")
        img = Path(str(s["image"])).expanduser()
        if not img.is_file():
            raise ExportError(f"step {i}: image not found: {img}", f"steps[{i - 1}].image")
        text = str(s.get("text", "")).strip()
        title = str(s.get("title", "")).strip()
        alt = str(s.get("alt") or "").strip() or (title or text.replace("\n", " ") or f"Step {i}")
        out.append({"image": img, "title": title, "text": text, "alt": alt})
    return out


def _md_text(raw: str) -> str:
    """Blank line after a "- " list so the next sentence is not read as part of its last item."""
    out, prev = [], False
    for line in raw.split("\n"):
        item = line.strip().startswith(("- ", "* "))
        if prev and not item and line.strip():
            out.append("")
        out.append(line)
        prev = item
    return "\n".join(out)


def _md(title, intro, steps, image_names) -> str:
    lines = [f"# {title}", ""] if title else []
    if intro:
        lines += [intro.strip(), ""]
    for i, (s, name) in enumerate(zip(steps, image_names), 1):
        text = _md_text(s["text"]).replace("\n", "\n   ").replace("\n   \n", "\n\n") or f"Step {i}"
        if s.get("title"):
            lines += [f"{i}. **{s['title']}**", ""] + ([f"   {text}", ""] if s["text"] else [])
            lines += [f"   ![{s['alt'].replace(']', ')').replace('[', '(')}]({name})", ""]
            continue
        lines += [f"{i}. {text}", "", f"   ![{s['alt'].replace(']', ')').replace('[', '(')}]({name})", ""]
    return "\n".join(lines).rstrip() + "\n"


class GuideResult(dict):
    """dict[format, Path] plus `.pdf_backend` ("playwright" | "pillow" | None) and `.warnings`."""
    pdf_backend: Optional[str] = None
    warnings: list


def _same_width(st: list[dict], width: Optional[int], tmp: Path) -> list[dict]:
    """Every step image on one canvas width `width`: narrower images are centered on white
    (transparent when the image has alpha), wider ones are scaled down. Unchanged images keep
    their original file."""
    sizes = []
    for s in st:
        with Image.open(s["image"]) as im:
            sizes.append(im.size)
    W = int(width)
    out = []
    for i, (s, (w, h)) in enumerate(zip(st, sizes), 1):
        if w == W:
            out.append(s)
            continue
        im = _open(s["image"], f"steps[{i - 1}].image")
        alpha = im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info
        im = im.convert("RGBA") if alpha else _rgb(im)
        if w > W:
            im = im.resize((W, max(1, round(h * W / w))), Image.LANCZOS)
        else:
            canvas = Image.new(im.mode, (W, h), (255, 255, 255, 0) if alpha else (255, 255, 255))
            canvas.paste(im, ((W - w) // 2, 0))
            im = canvas
        p = tmp / f"step-{i:02d}.png"
        im.save(p)
        out.append(dict(s, image=p))
    return out


def guide(steps: list, title: str = "", out_dir=None, formats: Sequence[str] = ("md", "html"),
          intro: str = "", *, name: str = "", draft: bool = False,
          width: Optional[int] = None) -> GuideResult:
    """Build a step-by-step guide. steps: [{"image": path, "text": "...", "title": optional, "alt": optional}].
    title is a short bold summary shown above the text.
    Writes into out_dir (default: a new dir `<prefix> <stamp> <name or "guide">` in the AInotate
    output dir, or in a temp dir when `draft`) and returns {format: Path}. md: relative images
    copied to `images/`; html: one self-contained file; pdf: Playwright print of the HTML (A4)
    when it works, else a Pillow page-per-step PDF (see result.pdf_backend).
    width: by default (None or 0) every step image keeps its own size. A width in pixels puts
    every step image on that one canvas width: narrower ones are centered, padded with white or
    transparency, wider ones are scaled down."""
    formats = tuple(f.lower().lstrip(".") for f in formats)
    bad = [f for f in formats if f not in ("md", "html", "pdf")]
    if bad:
        raise ExportError(f"unknown format(s): {', '.join(bad)} (use md, html, pdf)", "formats")
    if width is not None and (not isinstance(width, int) or isinstance(width, bool) or width < 0):
        raise ExportError(f"width must be a positive number of pixels (got {width!r})", "width")
    st = _norm_steps(steps)
    out = Path(out_dir).expanduser() if out_dir else _default_dir(slug(name) or "guide", draft)
    out.mkdir(parents=True, exist_ok=True)
    stem = slug(title) or slug(name) or "guide"
    res = GuideResult()
    res.warnings = []
    with tempfile.TemporaryDirectory(prefix="ainotate-guide-") as td:
        if width:
            st = _same_width(st, width, Path(td))
        _write_guide(res, st, title, intro, out, stem, formats)
    return res


def _write_guide(res: GuideResult, st, title, intro, out: Path, stem: str, formats) -> None:
    if "md" in formats:
        img_dir = out / "images"
        img_dir.mkdir(exist_ok=True)
        names = []
        for i, s in enumerate(st, 1):
            dest = img_dir / f"step-{i:02d}{s['image'].suffix.lower() or '.png'}"
            shutil.copyfile(s["image"], dest)
            names.append(f"images/{dest.name}")
        p = out / f"{stem}.md"
        p.write_text(_md(title, intro, st, names), encoding="utf-8")
        res["md"] = p
    html_text = None
    if "html" in formats or "pdf" in formats:
        html_text = _html(title, intro, st)
    if "html" in formats:
        p = out / f"{stem}.html"
        p.write_text(html_text, encoding="utf-8")
        res["html"] = p
    if "pdf" in formats:
        pdf = out / f"{stem}.pdf"
        try:
            with tempfile.TemporaryDirectory() as td:
                tmp = Path(td) / "guide.html"
                tmp.write_text(html_text, encoding="utf-8")
                _pdf_playwright(tmp, pdf)
            res.pdf_backend = "playwright"
        except Exception as e:  # noqa: BLE001  (no playwright, no browser binary, launch failure)
            res.warnings.append(f"Playwright PDF unavailable ({type(e).__name__}); used Pillow fallback")
            _pdf_pillow(title, intro, st, pdf)
            res.pdf_backend = "pillow"
        res["pdf"] = pdf


# ---------------------------------------------------------------- gif / apng

def _fit_frames(paths, max_width, format="apng") -> list[Image.Image]:
    if not paths:
        raise ExportError("need at least one frame", "frames")
    raw = [_open(p, f"frames[{i}]") for i, p in enumerate(paths)]
    alpha = format in ("apng", "png") and any(im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info for im in raw)
    ims = [im.convert("RGBA") if alpha else _rgb(im) for im in raw]
    w0, h0 = ims[0].size
    k = min(1.0, max_width / w0) if max_width else 1.0
    W, H = max(1, round(w0 * k)), max(1, round(h0 * k))
    out = []
    for im in ims:
        k2 = min(W / im.width, H / im.height)
        if (im.width, im.height) != (W, H):
            im = im.resize((max(1, round(im.width * k2)), max(1, round(im.height * k2))), Image.LANCZOS)
        if im.size != (W, H):   # letterbox on the first frame's edge color (transparent when alpha)
            bg = (0, 0, 0, 0) if alpha else ims[0].getpixel((0, 0))
            canvas = Image.new("RGBA" if alpha else "RGB", (W, H), bg)
            canvas.paste(im, ((W - im.width) // 2, (H - im.height) // 2))
            im = canvas
        out.append(im)
    return out


def _quantize(im: Image.Image) -> Image.Image:
    """Median-cut with Floyd-Steinberg dithering: smooth gradients, no banding on UI shadows."""
    return im.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.FLOYDSTEINBERG)


def gif(frames: list, out=None, durations_ms=None, loop: int = 0, max_width: int = 1200,
        crossfade_ms: int = 0, format: str = "apng", *, name: str = "", draft: bool = False) -> Path:
    """Animate frames. format="apng" (default; full color, .png) or "gif" (256 colors per
    frame with its own adaptive palette, universal support). durations_ms: int or list per
    frame (default 1500). crossfade_ms > 0 inserts blended in-betweens (~4 steps).
    loop=0 means forever. Frames are scaled to max_width and letterboxed to the first frame.
    Saved to `out` when given, else `<output_dir>/<prefix> <stamp> <name or "animation">`
    (a temp dir when `draft`)."""
    format = format.lower()
    if format not in ("apng", "png", "gif"):
        raise ExportError("format must be 'apng' or 'gif'", "format")
    ims = _fit_frames(frames, max_width, format=format)
    n = len(ims)
    if loop < 0 or not isinstance(loop, int) or isinstance(loop, bool):
        raise ExportError(f"loop must be a non-negative integer (got {loop!r})", "loop")
    if durations_ms is None:
        durs = [1500] * n
    elif isinstance(durations_ms, (int, float)):
        durs = [int(durations_ms)] * n
    else:
        durs = [int(x) for x in durations_ms]
        if len(durs) != n:
            raise ExportError(f"durations_ms has {len(durs)} values for {n} frames", "durations_ms")
    if any(d <= 0 for d in durs):
        raise ExportError(f"duration must be positive (got {durations_ms!r})", "durations_ms")
    seq, seq_d = [], []
    for i, im in enumerate(ims):
        seq.append(im)
        seq_d.append(durs[i])
        if crossfade_ms > 0 and i + 1 < n:
            steps = 4
            for k in range(1, steps + 1):
                seq.append(Image.blend(im, ims[i + 1], k / (steps + 1)))
                seq_d.append(max(20, crossfade_ms // steps))
    is_gif = format == "gif"
    ext = ".gif" if is_gif else ".png"
    if out:
        path = Path(out).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path = _free_path(path if path.suffix else path.with_suffix(ext))
    else:
        cfg = load_config()
        path = _free_path(_base_dir(draft) / f"{cfg.prefix} {_stamp()} {slug(name) or 'animation'}{ext}")
    try:
        if is_gif:
            q = [_quantize(im) for im in seq]
            q[0].save(path, format="GIF", save_all=True, append_images=q[1:], duration=seq_d,
                      loop=loop, optimize=False, disposal=1)
        else:
            seq[0].save(path, format="PNG", save_all=True, append_images=seq[1:], duration=seq_d,
                        loop=loop, optimize=False)
    except BaseException as e:
        path.unlink(missing_ok=True)
        if isinstance(e, ExportError):
            raise
        raise ExportError(f"could not encode {format.upper()}: {e}") from None
    return path


def apng(frames: list, out=None, durations_ms=None, loop: int = 0, max_width: int = 1200,
         crossfade_ms: int = 0, *, name: str = "", draft: bool = False) -> Path:
    return gif(frames, out, durations_ms, loop, max_width, crossfade_ms, format="apng", name=name, draft=draft)


# ---------------------------------------------------------------- compare

_BEFORE_TAG = (0x3A, 0x3A, 0x3C)   # graphite: red means "bad" in AInotate, "before" is just neutral


def _pill(canvas: Image.Image, text: str, color, pos, u: float | None = None) -> int:
    """Draw a tag pill at pos; color is a mark color name or an RGB tuple. Returns its height."""
    W, H = canvas.size
    u = u or style.mark_unit(W, H, 1.0)
    font = style.load_font(max(12, round(u * 1.15)))
    pw, ph = style.pill_size(font, text, u)
    if isinstance(color, str):
        fill = style.rgba(style.LABEL_FILL.get(style.color_of(color), style.color_of(color)))
    else:
        fill = (*color, 255)
    d = ImageDraw.Draw(canvas)
    x, y = pos
    d.rounded_rectangle([x, y, x + pw, y + ph], radius=ph / 2, fill=fill)
    d.text((x + pw / 2, y + ph / 2), text, font=font, fill="white", anchor="mm")
    return ph


def compare(before, after, out=None, labels=("Before", "After"), layout: str = "auto",
            divider: bool = True, *, name: str = "", draft: bool = False) -> Path:
    """One plate with both images at the same height (side) or width (stack), pill labels at
    the top-left of each, in a thin bar above each image (Before=graphite, After=green; red
    stays reserved for "bad"), and a thin divider. layout "auto": side by
    side for portrait-ish images (w/h < 1.2), stacked for wide ones. Saved via output.save
    as `<prefix> <stamp> <name or "compare">` (draft: temp dir), or to `out`."""
    if layout not in ("side", "stack", "auto"):
        raise ExportError("layout must be side, stack or auto", "layout")
    labels = tuple(labels)
    if len(labels) != 2 or not all(isinstance(x, str) and x.strip() for x in labels):
        raise ExportError(f"labels must be two non-empty texts (got {list(labels)!r})", "labels")
    a, b = _rgb(_open(before, "before")), _rgb(_open(after, "after"))
    if layout == "auto":
        layout = "side" if a.width / a.height < 1.2 else "stack"
    gap = 14 if divider else 0
    u = style.mark_unit(max(a.width, b.width), max(a.height, b.height), 1.0)
    pad = round(u * 0.6)
    f_bar = style.load_font(max(12, round(u * 1.15)))
    bh = round(max(style.pill_size(f_bar, l, u)[1] for l in labels) + 2 * pad)
    if layout == "side":
        h = a.height
        b = b.resize((max(1, round(b.width * h / b.height)), h), Image.LANCZOS)
        W, H = a.width + b.width + gap, h + bh
        limit = 4096
        offs = [(0, bh), (a.width + gap, bh)]
    else:
        w = a.width
        b = b.resize((w, max(1, round(b.height * w / b.width))), Image.LANCZOS)
        W, H = w, bh + a.height + gap + bh + b.height
        limit = 6000
        offs = [(0, bh), (0, bh + a.height + gap + bh)]
    bg = (232, 232, 237)
    plate = Image.new("RGB", (W, H), bg)
    plate.paste(a, offs[0])
    plate.paste(b, offs[1])
    if divider and gap:
        d = ImageDraw.Draw(plate)
        if layout == "side":
            x = a.width + gap // 2
            d.line([(x, 0), (x, H)], fill=(190, 190, 197), width=2)
        else:
            y = offs[0][1] + a.height + gap // 2
            d.line([(0, y), (W, y)], fill=(190, 190, 197), width=2)
    _pill(plate, labels[0], _BEFORE_TAG, (offs[0][0] + pad, offs[0][1] - bh + pad), u)
    _pill(plate, labels[1], "good", (offs[1][0] + pad, offs[1][1] - bh + pad), u)
    if max(W, H) > limit:
        k = limit / max(W, H)
        plate = plate.resize((round(W * k), round(H * k)), Image.LANCZOS)
    if out:
        p = Path(out).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        p = _free_path(p if p.suffix else p.with_suffix(".png"))
        plate.save(p, optimize=True)
        return p
    return save(plate, name or "compare", draft=draft)[0]


# ---------------------------------------------------------------- clipboard

_INSTALL_HINT = {
    "Linux": "install wl-clipboard (Wayland: apt install wl-clipboard) or xclip (X11: apt install xclip)",
}


def _run(cmd, timeout=20, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)


def _as_png(path) -> tuple[Path, Optional[Path]]:
    p = Path(path).expanduser()
    if not p.is_file():
        raise ExportError(f"image not found: {p}", "path")
    if p.suffix.lower() == ".png":
        return p, None
    td = Path(tempfile.mkdtemp(prefix="ainotate-clip-"))
    try:
        tmp = td / "clip.png"
        _rgb(_open(p)).save(tmp)
        return tmp, tmp
    except BaseException:
        shutil.rmtree(td, ignore_errors=True)
        raise


def copy_to_clipboard(path) -> None:
    """Put the image on the system clipboard (macOS, Windows, Linux). Raises ExportError."""
    png, tmp = _as_png(path)
    system = platform.system()
    try:
        if system == "Darwin":
            # the path travels as an argument (argv), never inside the AppleScript source
            r = _run(["osascript", "-e", "on run argv",
                      "-e", "set the clipboard to (read (POSIX file (item 1 of argv)) as «class PNGf»)",
                      "-e", "end run", str(png.resolve())])
            if r.returncode != 0:
                raise ExportError(f"osascript failed: {r.stderr.strip() or r.stdout.strip()}")
            v = _run(["osascript", "-e", "clipboard info"])
            if "PNGf" not in (v.stdout or "") and "PNG" not in (v.stdout or ""):
                raise ExportError("clipboard did not take the image (clipboard info shows no PNG)")
        elif system == "Windows":
            # the path travels in an environment variable, never inside the script source
            ps = ("Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
                  "$i=[System.Drawing.Image]::FromFile($env:AINOTATE_CLIP_PATH); "
                  "[System.Windows.Forms.Clipboard]::SetImage($i); $i.Dispose()")
            exe = shutil.which("powershell") or shutil.which("pwsh")
            if not exe:
                raise ExportError("PowerShell not found on PATH")
            r = _run([exe, "-NoProfile", "-STA", "-Command", ps],
                     env=dict(os.environ, AINOTATE_CLIP_PATH=str(png.resolve())))
            if r.returncode != 0:
                raise ExportError(f"PowerShell clipboard failed: {r.stderr.strip()}")
        elif system == "Linux":
            if shutil.which("wl-copy"):
                with open(png, "rb") as f:
                    r = subprocess.run(["wl-copy", "--type", "image/png"], stdin=f, capture_output=True, timeout=15)
            elif shutil.which("xclip"):
                r = subprocess.run(["xclip", "-selection", "clipboard", "-t", "image/png", "-i", str(png)],
                                   capture_output=True, timeout=15)
            else:
                raise ExportError("no clipboard tool found: " + _INSTALL_HINT["Linux"])
            if r.returncode != 0:
                raise ExportError(f"clipboard tool failed: {(r.stderr or b'').decode(errors='replace').strip()}")
        else:
            raise ExportError(f"clipboard not supported on {system}")
    except FileNotFoundError as e:
        raise ExportError(f"clipboard tool missing: {e}")
    finally:
        if tmp:
            shutil.rmtree(tmp.parent, ignore_errors=True)

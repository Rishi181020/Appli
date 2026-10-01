"""Builds the README artwork in this folder: header-*.svg, demo-*.svg and flow-*.svg (light and dark), plus logo.svg.

Text is drawn as outlines, so the SVGs look the same everywhere (GitHub shows them as images, which can't load fonts).
Needs the dashboard's two typefaces, fetched once with npm:

    pip install fonttools uharfbuzz
    cd <somewhere> && npm pack @fontsource/plus-jakarta-sans @fontsource/inter
    (untar both, then)
    python docs/assets/make_assets.py --fonts <folder holding the two unpacked packages>
"""

from __future__ import annotations

import argparse
import glob
import io
import os
from dataclasses import dataclass

import uharfbuzz as hb
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.ttLib import TTFont

HERE = os.path.dirname(os.path.abspath(__file__))

# Brand colours, from dashboard/src/styles.css
MINT = "#2ef5b0"
LIME = "#c8ff6e"
MINT_INK = "#05281c"   # logo strokes, text on mint
MINT_TEXT = "#00845c"  # text-safe green on white
VIOLET = "#6a55f0"     # used only for what *you* do


@dataclass
class Theme:
    name: str
    panel: str
    panel2: str
    border: str
    border_strong: str
    text: str
    muted: str
    green: str
    green_soft: str
    violet: str
    violet_soft: str
    shadow: float


LIGHT = Theme("light", "#ffffff", "#f7f8f9", "#e5e7ea", "#d5d9dd", "#111413", "#5d656c", MINT_TEXT,
              "rgba(0,201,141,0.12)", VIOLET, "rgba(106,85,240,0.12)", 0.10)
DARK = Theme("dark", "#131616", "#181c1c", "#252b2b", "#323a3a", "#edf1f0", "#98a3a1", "#2ee6a6",
             "rgba(0,240,160,0.12)", "#a898ff", "rgba(168,152,255,0.16)", 0.45)


# ---------------------------------------------------------------- text as outlines

class Face:
    def __init__(self, path: str):
        tt = TTFont(path)
        tt.flavor = None
        buf = io.BytesIO()
        tt.save(buf)
        self.tt = TTFont(io.BytesIO(buf.getvalue()))
        self.glyphs = self.tt.getGlyphSet()
        self.order = self.tt.getGlyphOrder()
        self.upem = self.tt["head"].unitsPerEm
        self.hb = hb.Font(hb.Face(buf.getvalue()))

    def shape(self, s: str, size: float, tracking: float = 0):
        b = hb.Buffer()
        b.add_str(s)
        b.guess_segment_properties()
        hb.shape(self.hb, b, {"kern": True, "liga": True})
        k = size / self.upem
        x, out = 0.0, []
        for info, pos in zip(b.glyph_infos, b.glyph_positions):
            out.append((self.order[info.codepoint], x + pos.x_offset * k, pos.y_offset * k))
            x += pos.x_advance * k + tracking * size
        return out, x - tracking * size, k

    def width(self, s: str, size: float, tracking: float = 0) -> float:
        return self.shape(s, size, tracking)[1]


FACES: dict[str, Face] = {}


def face(kind: str) -> Face:
    return FACES[kind]


def load_faces(root: str):
    def find(pattern):
        hits = glob.glob(os.path.join(root, "**", pattern), recursive=True)
        if not hits:
            raise SystemExit(f"font not found under {root}: {pattern}")
        return hits[0]

    FACES["head800"] = Face(find("plus-jakarta-sans-latin-800-normal.woff"))
    FACES["head700"] = Face(find("plus-jakarta-sans-latin-700-normal.woff"))
    FACES["ui400"] = Face(find("inter-latin-400-normal.woff"))
    FACES["ui500"] = Face(find("inter-latin-500-normal.woff"))
    FACES["ui600"] = Face(find("inter-latin-600-normal.woff"))


class Glyphs:
    """Per-SVG store of glyph outlines: each one is written once in <defs> and placed with <use>."""

    def __init__(self):
        self.ids: dict[tuple[str, str], str] = {}
        self.defs: list[str] = []

    def reset(self):
        self.ids.clear()
        self.defs.clear()

    def use(self, kind: str, name: str) -> str | None:
        """The id of the glyph's outline, or None for an empty glyph (a space)."""
        key = (kind, name)
        if key not in self.ids:
            pen = SVGPathPen(face(kind).glyphs, ntos=lambda v: str(round(v)))
            face(kind).glyphs[name].draw(pen)
            d = pen.getCommands()
            self.ids[key] = f"g{len(self.ids)}" if d else None
            if d:
                self.defs.append(f'<path id="{self.ids[key]}" d="{d}"/>')
        return self.ids[key]

    def svg_defs(self) -> str:
        return "".join(self.defs)


G = Glyphs()


def text(s, kind, size, x, y, fill, anchor="start", tracking=0.0):
    """`s` as placed glyph outlines, baseline at y."""
    f = face(kind)
    glyphs, w, k = f.shape(s, size, tracking)
    if anchor == "middle":
        x -= w / 2
    elif anchor == "end":
        x -= w
    parts = []
    for name, gx, gy in glyphs:
        gid = G.use(kind, name)
        if not gid:
            continue
        parts.append(f'<use href="#{gid}" transform="matrix({k:.4g} 0 0 {-k:.4g} {x + gx:.1f} {y - gy:.1f})"/>')
    return f'<g fill="{fill}">{"".join(parts)}</g>'


# ---------------------------------------------------------------- logo

# The mark: an "A" whose crossbar is a check. The check is drawn over the A (with a gap cut in the A where they
# cross) and its long stroke carries on past the right leg: the job gets done.
A_LEGS = "M8.6 24.4L16 7.6 23.4 24.4"
CHECK = "M11.6 18.2l3 2.9 8.6-9.2"


def logo_defs(uid: str) -> str:
    return (f'<linearGradient id="{uid}t" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="32" y2="32">'
            f'<stop offset="0" stop-color="{MINT}"/><stop offset="1" stop-color="{LIME}"/></linearGradient>')


def logo_mark(uid: str, cls: str = "") -> str:
    """32x32 mark. With cls="draw", the parts carry classes the header animates."""
    c = (lambda name: f' class="{name}"') if cls else (lambda name: "")
    return (f'<rect width="32" height="32" rx="9.5" fill="url(#{uid}t)"{c("tile")}/>'
            f'<path d="{A_LEGS}" fill="none" stroke="{MINT_INK}" stroke-width="3.2" stroke-linecap="round" '
            f'stroke-linejoin="round" pathLength="100"{c("legs")}/>'
            f'<path d="{CHECK}" fill="none" stroke="url(#{uid}t)" stroke-width="6" stroke-linecap="round" '
            f'stroke-linejoin="round" pathLength="100"{c("gap")}/>'
            f'<path d="{CHECK}" fill="none" stroke="{MINT_INK}" stroke-width="3.2" stroke-linecap="round" '
            f'stroke-linejoin="round" pathLength="100"{c("tick")}/>')


def make_logo() -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="256" height="256" role="img" '
            f'aria-label="Appli"><defs>{logo_defs("l")}</defs>{logo_mark("l")}</svg>\n')


REDUCED = "@media (prefers-reduced-motion: reduce){*{animation:none!important}}"


# ---------------------------------------------------------------- header: mark + wordmark, drawn once on load

def make_header(t: Theme) -> str:
    W, H = 600, 180
    size = 124
    mark = 132
    word_w = face("head800").width("Appli", size, -0.035)
    gap = 30
    x0 = (W - (mark + gap + word_w)) / 2
    my = (H - mark) / 2
    base = H / 2 + size * 0.36  # optical centre of the cap height
    s = mark / 32
    css = f"""
.tile{{transform-box:fill-box;transform-origin:center;animation:pop .7s cubic-bezier(.3,1.5,.5,1) both}}
.legs{{stroke-dasharray:100;animation:draw .65s .35s cubic-bezier(.6,0,.3,1) both}}
.gap,.tick{{stroke-dasharray:100;animation:draw .5s .95s cubic-bezier(.5,0,.2,1) both}}
.word{{animation:rise .7s .55s cubic-bezier(.2,.8,.2,1) both}}
@keyframes pop{{from{{transform:scale(.4);opacity:0}}to{{transform:none;opacity:1}}}}
@keyframes draw{{from{{stroke-dashoffset:100}}to{{stroke-dashoffset:0}}}}
@keyframes rise{{from{{transform:translateX(-14px);opacity:0}}to{{transform:none;opacity:1}}}}
{REDUCED}"""
    word = text("Appli", "head800", size, x0 + mark + gap, base, t.text, tracking=-0.035)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" '
            f'aria-label="Appli"><style>{css}</style><defs>{logo_defs("h")}{G.svg_defs()}</defs>'
            f'<g transform="translate({x0:.1f} {my:.1f}) scale({s:.4f})">{logo_mark("h", "draw")}</g>'
            f'<g class="word">{word}</g></svg>\n')


# ---------------------------------------------------------------- demo: a form fills itself, stops at Submit, you submit

T = 14.0  # seconds per loop


def pct(t: float) -> str:
    return f"{max(0.0, min(100.0, t / T * 100)):.2f}%"


class Anim:
    """Collects CSS keyframes. Each element's resting style is the 'ready for review' frame, so with motion off
    (or before the animation starts) the picture still says what Appli does."""

    def __init__(self):
        self.rules: list[str] = []
        self.n = 0

    def add(self, frames: list[tuple[float, str]], ease: str = "linear", extra: str = "") -> str:
        self.n += 1
        name = f"k{self.n}"
        body = "".join(f"{pct(t)}{{{style}}}" for t, style in frames)
        self.rules.append(f"@keyframes {name}{{{body}}}")
        self.rules.append(f".{name}{{animation:{name} {T}s {ease} infinite{extra}}}")
        return name

    def css(self) -> str:
        return "\n".join(self.rules)


def check_badge(cx, cy, t: Theme, cls="") -> str:
    return (f'<g class="{cls}"><circle cx="{cx}" cy="{cy}" r="10" fill="{t.green}"/>'
            f'<path d="M{cx - 4.2} {cy + 0.2}l2.8 2.7 5.6-6" fill="none" stroke="{t.panel}" stroke-width="2.2" '
            f'stroke-linecap="round" stroke-linejoin="round"/></g>')


def make_demo(t: Theme) -> str:
    W, H = 1000, 620
    a = Anim()
    out: list[str] = []
    ui5, ui4, ui6 = "ui500", "ui400", "ui600"

    # window
    wx, wy, ww, wh = 24, 20, 952, 572
    out.append(f'<rect x="{wx}" y="{wy}" width="{ww}" height="{wh}" rx="18" fill="{t.panel}" stroke="{t.border}" '
               f'filter="url(#sh)"/>')
    out.append(f'<path d="M{wx} {wy + 56}h{ww}" stroke="{t.border}"/>')
    for i in range(3):
        out.append(f'<circle cx="{wx + 26 + i * 20}" cy="{wy + 28}" r="5.5" fill="{t.border_strong}"/>')
    out.append(f'<rect x="{wx + 100}" y="{wy + 14}" width="520" height="28" rx="14" fill="{t.panel2}" '
               f'stroke="{t.border}"/>')
    out.append(text("jobs.example.com/northwind/apply", ui4, 13.5, wx + 120, wy + 33, t.muted))

    # Appli status, top right of the window: Filling -> Ready for review -> Submitted
    px, py = wx + ww - 236, wy + 13
    def pill(label, bg, fg):
        return (f'<rect x="{px}" y="{py}" width="216" height="30" rx="15" fill="{bg}"/>'
                f'<g transform="translate({px + 7} {py + 5}) scale({20 / 32})">{logo_mark("s")}</g>'
                f'{text(label, ui6, 13.5, px + 36, py + 20, fg)}')
    a.rules.append("@keyframes blink{0%,100%{opacity:1}50%{opacity:.25}}.blink{animation:blink 1s ease-in-out infinite}")
    filling = a.add([(0, "opacity:0"), (0.3, "opacity:1"), (6.75, "opacity:1"), (6.85, "opacity:0"), (T, "opacity:0")])
    ready = a.add([(0, "opacity:0"), (6.75, "opacity:0"), (6.85, "opacity:1"), (9.35, "opacity:1"),
                   (9.45, "opacity:0"), (T, "opacity:0")])
    done = a.add([(0, "opacity:0"), (9.35, "opacity:0"), (9.45, "opacity:1"), (12.9, "opacity:1"),
                  (13.5, "opacity:0"), (T, "opacity:0")])
    out.append(f'<g class="{filling}" opacity="0">{pill("Filling the form", t.panel2, t.muted)}'
               f'<circle class="blink" cx="{px + 200}" cy="{py + 15}" r="4" fill="{t.green}"/></g>')
    out.append(f'<g class="{ready}">{pill("Ready for review", t.green_soft, t.green)}</g>')
    out.append(f'<g class="{done}" opacity="0">{pill("Submitted", t.violet_soft, t.violet)}</g>')

    # form header
    x0, x1 = 84, 916
    out.append(text("Software Engineer, New Grad", "head700", 27, x0, 128, t.text, tracking=-0.02))
    out.append(text("Northwind Labs, San Francisco", ui4, 15.5, x0, 156, t.muted))
    out.append(f'<path d="M{x0} 180H{x1}" stroke="{t.border}"/>')

    # fields: (label, x, y, w, h)
    colw = (x1 - x0 - 24) / 2
    c2 = x0 + colw + 24
    fields = {
        "name": ("Full name", x0, 222, colw, 46),
        "email": ("Email", c2, 222, colw, 46),
        "resume": ("Resume", x0, 314, colw, 46),
        "sponsor": ("Will you need visa sponsorship?", c2, 314, colw, 46),
        "letter": ("Cover letter", x0, 406, x1 - x0, 84),
    }
    for key, (label, fx, fy, fw, fh) in fields.items():
        out.append(text(label, ui5, 14, fx, fy - 10, t.text))
        out.append(f'<rect x="{fx}" y="{fy}" width="{fw}" height="{fh}" rx="11" fill="{t.panel2}" '
                   f'stroke="{t.border_strong}"/>')

    dyn: list[str] = []  # the moving layer: fades in at the start of each loop and out at the end

    def typed(value, fx, fy, start, end, kind=ui5, size=16.5):
        """A value typed in from start to end, with a caret, a focus ring and (optionally) a check."""
        w = face(kind).width(value, size)
        n = max(len(value), 1)
        step = f"animation-timing-function:steps({n},end)"
        cover = a.add([(0, "transform:scaleX(1)"), (start, f"transform:scaleX(1);{step}"), (end, "transform:scaleX(0)"),
                       (T, "transform:scaleX(0)")])
        caret = a.add([(0, "opacity:0;transform:translateX(0)"), (start - 0.01, "opacity:0;transform:translateX(0)"),
                       (start, f"opacity:1;transform:translateX(0);{step}"), (end, f"opacity:1;transform:translateX({w:.1f}px)"),
                       (end + 0.25, f"opacity:0;transform:translateX({w:.1f}px)"), (T, "opacity:0")])
        return (text(value, kind, size, fx, fy, t.text)
                + f'<rect class="{cover} cover" x="{fx - 1}" y="{fy - size}" width="{w + 3:.1f}" height="{size * 1.35:.1f}" '
                  f'fill="{t.panel2}"/>'
                + f'<rect class="{caret}" x="{fx}" y="{fy - size * 0.95:.1f}" width="2" height="{size * 1.2:.1f}" '
                  f'rx="1" fill="{t.green}" opacity="0"/>')

    def focus(fx, fy, fw, fh, start, end):
        k = a.add([(0, "opacity:0"), (start - 0.1, "opacity:0"), (start, "opacity:1"), (end + 0.2, "opacity:1"),
                   (end + 0.4, "opacity:0"), (T, "opacity:0")])
        return (f'<rect class="{k}" x="{fx - 3}" y="{fy - 3}" width="{fw + 6}" height="{fh + 6}" rx="13" fill="none" '
                f'stroke="{t.green}" stroke-opacity=".45" stroke-width="3" opacity="0"/>')

    def pop(start):
        return a.add([(0, "transform:scale(0)"), (start, "transform:scale(0)"), (start + 0.18, "transform:scale(1.18)"),
                      (start + 0.32, "transform:scale(1)"), (T, "transform:scale(1)")])

    # timeline (seconds)
    steps = {"name": (0.8, 1.6), "email": (1.85, 3.0), "resume": (3.25, 3.7), "sponsor": (3.95, 4.35),
             "l1": (4.6, 5.7), "l2": (5.75, 6.6)}

    # name / email
    for key, value in (("name", "Alex Rivera"), ("email", "alex.rivera@example.com")):
        _, fx, fy, fw, fh = fields[key]
        s0, s1 = steps[key]
        dyn.append(focus(fx, fy, fw, fh, s0, s1))
        dyn.append(typed(value, fx + 16, fy + 29, s0, s1))
        dyn.append(check_badge(fx + fw - 24, fy + fh / 2, t, f"{pop(s1 + 0.1)} pop"))

    # resume: the best-matching file is attached, with its match %
    _, fx, fy, fw, fh = fields["resume"]
    s0, s1 = steps["resume"]
    chip_in = a.add([(0, "opacity:0;transform:translateY(8px)"), (s0, "opacity:0;transform:translateY(8px)"),
                     (s0 + 0.3, "opacity:1;transform:translateY(0)"), (T, "opacity:1;transform:translateY(0)")],
                    ease="cubic-bezier(.2,.8,.2,1)")
    doc = (f'<path d="M{fx + 16} {fy + 13}h9l5 5v15h-14z" fill="none" stroke="{t.green}" stroke-width="1.8" '
           f'stroke-linejoin="round"/><path d="M{fx + 25} {fy + 13}v5h5" fill="none" stroke="{t.green}" '
           f'stroke-width="1.8" stroke-linejoin="round"/>')
    fname = "Alex Resume SWE.pdf"
    mw = face(ui6).width("86% match", 13) + 20
    mx = fx + fw - 48 - mw
    dyn.append(focus(fx, fy, fw, fh, s0, s1))
    dyn.append(f'<g class="{chip_in}">{doc}{text(fname, ui5, 16.5, fx + 42, fy + 29, t.text)}</g>')
    dyn.append(f'<g class="{pop(s1 - 0.1)} pop"><rect x="{mx:.1f}" y="{fy + 11}" width="{mw:.1f}" height="24" rx="12" '
               f'fill="{t.green_soft}"/>{text("86% match", ui6, 13, mx + mw / 2, fy + 28, t.green, anchor="middle")}</g>')
    dyn.append(check_badge(fx + fw - 24, fy + fh / 2, t, f"{pop(s1 + 0.1)} pop"))

    # sponsorship dropdown
    _, fx, fy, fw, fh = fields["sponsor"]
    s0, s1 = steps["sponsor"]
    no_in = a.add([(0, "opacity:0"), (s1 - 0.05, "opacity:0"), (s1, "opacity:1"), (T, "opacity:1")])
    dyn.append(focus(fx, fy, fw, fh, s0, s1))
    dyn.append(f'<path d="M{fx + fw - 64} {fy + 20}l5 5 5-5" fill="none" stroke="{t.muted}" stroke-width="1.8" '
               f'stroke-linecap="round" stroke-linejoin="round"/>')
    dyn.append(f'<g class="{no_in}">{text("No", ui5, 16.5, fx + 16, fy + 29, t.text)}</g>')
    dyn.append(check_badge(fx + fw - 24, fy + fh / 2, t, f"{pop(s1 + 0.1)} pop"))

    # cover letter, written in your voice
    _, fx, fy, fw, fh = fields["letter"]
    dyn.append(focus(fx, fy, fw, fh, steps["l1"][0], steps["l2"][1]))
    dyn.append(typed("I'd like to join Northwind because your developer tools team builds what I loved", fx + 16,
                     fy + 32, *steps["l1"], kind=ui4, size=16))
    dyn.append(typed("building at school: a CI dashboard that 40 student teams relied on every week.", fx + 16,
                     fy + 58, *steps["l2"], kind=ui4, size=16))
    dyn.append(check_badge(fx + fw - 24, fy + 22, t, f"{pop(steps['l2'][1] + 0.1)} pop"))

    # Submit: Appli stops here. You click it.
    bx, by, bw, bh = x0, 520, 196, 48
    btn_label = "Submit application"
    out.append(f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="24" fill="{t.text}"/>')
    out.append(text(btn_label, ui6, 15.5, bx + bw / 2, by + 30, t.panel, anchor="middle"))
    ring_on = a.add([(0, "opacity:0"), (6.8, "opacity:0"), (7.0, "opacity:1"), (9.2, "opacity:1"),
                     (9.3, "opacity:0"), (T, "opacity:0")])
    dyn.append(f'<g class="{ring_on}"><rect class="pulse" x="{bx - 5}" y="{by - 5}" width="{bw + 10}" '
               f'height="{bh + 10}" rx="29" fill="none" stroke="{t.violet}" stroke-width="2.5"/></g>')
    sent = a.add([(0, "opacity:0"), (9.3, "opacity:0"), (9.4, "opacity:1"), (T, "opacity:1")])
    dyn.append(f'<g class="{sent}" opacity="0"><rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="24" '
               f'fill="{t.violet}"/>'
               f'<path d="M{bx + 44} {by + 24.5}l4.5 4.5 9-9.5" fill="none" stroke="#fff" stroke-width="2.6" '
               f'stroke-linecap="round" stroke-linejoin="round"/>'
               f'{text("Submitted", ui6, 15.5, bx + 68, by + 30, "#fff")}</g>')

    # the note beside the button
    nx = bx + bw + 24
    note_ready = a.add([(0, "opacity:0"), (6.8, "opacity:0"), (7.1, "opacity:1"), (9.35, "opacity:1"),
                        (9.45, "opacity:0"), (T, "opacity:0")])
    note_done = a.add([(0, "opacity:0"), (9.45, "opacity:0"), (9.6, "opacity:1"), (T, "opacity:1")])
    dyn.append(f'<g class="{note_ready}">{text("Appli stops here. Check it over, then submit it yourself.", ui5, 15.5, nx, by + 30, t.muted)}</g>')
    dyn.append(f'<g class="{note_done}" opacity="0">{text("Appli saw the confirmation page and marked the job Submitted.", ui5, 15.5, nx, by + 30, t.muted)}</g>')

    # your cursor, labelled "You"
    cx, cy = bx + bw * 0.62, by + 22
    glide = a.add([(0, "opacity:0;transform:translate(520px,-70px)"), (7.4, "opacity:0;transform:translate(520px,-70px)"),
                   (7.6, "opacity:1;transform:translate(520px,-70px)"), (9.0, "opacity:1;transform:translate(0,0)"),
                   (12.6, "opacity:1;transform:translate(0,0)"), (13.1, "opacity:0;transform:translate(0,0)"),
                   (T, "opacity:0")], ease="cubic-bezier(.45,0,.2,1)")
    press = a.add([(0, "transform:scale(1)"), (9.1, "transform:scale(1)"), (9.2, "transform:scale(.82)"),
                   (9.35, "transform:scale(1)"), (T, "transform:scale(1)")])
    ripple = a.add([(0, "opacity:0;transform:scale(.3)"), (9.2, "opacity:0;transform:scale(.3)"),
                    (9.25, "opacity:.6;transform:scale(.4)"), (9.9, "opacity:0;transform:scale(1.6)"),
                    (T, "opacity:0;transform:scale(1.6)")], ease="ease-out")
    you_w = face(ui6).width("You", 13) + 18
    cursor = (f'<circle class="{ripple} ctr" cx="0" cy="0" r="22" fill="{t.violet}" opacity="0"/>'
              f'<g class="{press} tip"><path d="M0 0l0 21 5.2-5 3.4 7.6 3.6-1.6-3.4-7.4 7.2-.4z" fill="{t.violet}" '
              f'stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/>'
              f'<rect x="16" y="22" width="{you_w:.1f}" height="22" rx="11" fill="{t.violet}"/>'
              f'{text("You", ui6, 13, 16 + you_w / 2, 37.5, "#fff", anchor="middle")}</g>')
    dyn.append(f'<g transform="translate({cx:.1f} {cy:.1f})"><g class="{glide}">{cursor}</g></g>')

    layer = a.add([(0, "opacity:0"), (0.3, "opacity:1"), (13.0, "opacity:1"), (13.6, "opacity:0"), (T, "opacity:0")])

    css = f"""
.cover{{transform-box:fill-box;transform-origin:right center;transform:scaleX(0)}}
.pop,.ctr,.tip{{transform-box:fill-box;transform-origin:center}}
.tip{{transform-origin:0 0}}
.pulse{{transform-box:fill-box;transform-origin:center;animation:pulse 1.6s ease-out infinite}}
@keyframes pulse{{0%{{opacity:.9;transform:scale(1)}}100%{{opacity:0;transform:scale(1.08,1.25)}}}}
{a.css()}
{REDUCED}"""
    shadow = (f'<filter id="sh" x="-10%" y="-10%" width="120%" height="130%"><feDropShadow dx="0" dy="14" '
              f'stdDeviation="16" flood-color="#000" flood-opacity="{t.shadow}"/></filter>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" '
            f'aria-label="Appli fills a job application, then stops at Submit for you to review and submit.">'
            f'<style>{css}</style><defs>{shadow}{logo_defs("s")}{G.svg_defs()}</defs>'
            f'{"".join(out)}<g class="{layer}">{"".join(dyn)}</g></svg>\n')


# ---------------------------------------------------------------- flow: how a job moves through Appli (static)

def make_flow(t: Theme) -> str:
    W, H = 1000, 318
    out: list[str] = []
    ui6, ui4 = "ui600", "ui400"
    arrow = t.border_strong
    w, h = 210, 56

    def node(x, y, label, sub, kind):
        fill, stroke, fg = {
            "plain": (t.panel, t.border_strong, t.text),
            "green": (t.green_soft, "none", t.green),
            "you": (t.violet_soft, "none", t.violet),
            "side": (t.panel2, t.border, t.muted),
        }[kind]
        return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="28" fill="{fill}" stroke="{stroke}"/>'
                + text(label, ui6, 16, x + w / 2, y + 25, fg, anchor="middle")
                + text(sub, ui4, 13, x + w / 2, y + 43, t.muted, anchor="middle"))

    def line(d, dashed=True):
        dash = ' stroke-dasharray="4 5"' if dashed else ""
        return (f'<path d="{d}" fill="none" stroke="{arrow}" stroke-width="2" stroke-linecap="round" '
                f'stroke-linejoin="round" marker-end="url(#ar)"{dash}/>')

    def note(s, x, y, anchor="start"):
        return text(s, ui4, 13, x, y, t.muted, anchor=anchor)

    # the main path: Appli's steps in green, yours in violet
    y = 124
    q, f, r, d = 20, 270, 520, 770
    out.append(node(q, y, "Queued", "links, a file or Find jobs", "plain"))
    out.append(node(f, y, "Filling", "in a browser tab", "green"))
    out.append(node(r, y, "Ready for review", "the form waits for you", "green"))
    out.append(node(d, y, "Submitted", "you clicked Submit", "you"))
    for x0, x1 in ((q, f), (f, r), (r, d)):
        out.append(line(f"M{x0 + w + 6} {y + h / 2}H{x1 - 8}", dashed=False))

    # resume match under 80%: a tailored draft, back to the front of the queue once you build it
    ty = 16
    out.append(node(145, ty, "Tailoring resume", "match under 80%", "side"))
    out.append(line(f"M330 {y - 4}V{ty + h + 8}"))
    out.append(line(f"M180 {ty + h + 4}V{y - 8}"))
    out.append(note("you build it", 168, 104, anchor="end"))

    # screened out
    out.append(node(r, ty, "Skipped", "sponsorship, clearance, closed", "side"))
    out.append(line(f"M440 {y - 4}V{ty + h / 2 + 14}Q440 {ty + h / 2} 454 {ty + h / 2}H{r - 8}"))

    # a required question nobody has answered yet
    ny = 230
    out.append(node(f, ny, "Needs your help", "a required question is left", "you"))
    out.append(line(f"M330 {y + h + 4}V{ny - 8}"))
    out.append(line(f"M420 {ny - 4}V{y + h + 8}"))
    out.append(note("you answer", 428, 212))

    # closing the tab without submitting puts the job back in the queue
    by = 304
    out.append(line(f"M600 {y + h + 4}V{by - 14}Q600 {by} 586 {by}H139Q125 {by} 125 {by - 14}V{y + h + 8}"))
    out.append(note("tab closed without submitting", 612, 268))

    marker = (f'<marker id="ar" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="7" markerHeight="7" '
              f'orient="auto-start-reverse"><path d="M1 1l6 4-6 4" fill="none" stroke="{arrow}" stroke-width="1.8" '
              f'stroke-linecap="round" stroke-linejoin="round"/></marker>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" '
            f'aria-label="How a job moves: Queued, Filling, Ready for review, Submitted. Filling can also lead to '
            f'Tailoring resume, Skipped or Needs your help; closing the tab sends the job back to Queued.">'
            f'<defs>{marker}{G.svg_defs()}</defs>{"".join(out)}</svg>\n')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fonts", required=True, help="folder holding the unpacked @fontsource packages")
    args = ap.parse_args()
    load_faces(args.fonts)
    builds = {"logo.svg": make_logo}
    for t in (LIGHT, DARK):
        builds[f"header-{t.name}.svg"] = lambda t=t: make_header(t)
        builds[f"demo-{t.name}.svg"] = lambda t=t: make_demo(t)
        builds[f"flow-{t.name}.svg"] = lambda t=t: make_flow(t)
    for name, build in builds.items():
        G.reset()
        svg = build()
        with open(os.path.join(HERE, name), "w", encoding="utf-8") as f:
            f.write(svg)
        print(f"{name}: {len(svg) // 1024} KB")


if __name__ == "__main__":
    main()

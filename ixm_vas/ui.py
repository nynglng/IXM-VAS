"""The dark theme: palette, fonts, and the few widgets the window is built from.

Tk has no rounded corners and no anti-aliasing for shapes, so rounded
surfaces are drawn as images: each pixel's coverage of the rounded rectangle
is computed and blended onto the known background color. Only the four corner
squares need per-pixel math; every other row is a straight run of one or two
colors, which keeps even a large card cheap to draw. Images are cached by
everything that defines them.

Pure tkinter on purpose: the window process must not import anything pip
might replace during an update (a loaded .pyd is locked on Windows).
"""

from __future__ import annotations

import math
import tkinter as tk
import tkinter.font as tkfont
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple

# ============================================================
# PALETTE -- charcoal, not black; off-white, not white. One accent.
# ============================================================
BG = "#0F1217"            # window
SURFACE = "#161A22"       # top bar, cards
SURFACE_2 = "#1C212B"     # hover on surface, inputs
SURFACE_3 = "#232937"
BORDER = "#262C38"
BORDER_STRONG = "#323A4A"
OUTLINE = "#2A3140"       # 1px edge around the whole window

TEXT = "#E4E7EC"
TEXT_MUTED = "#8B94A5"
TEXT_FAINT = "#5D6677"

ACCENT = "#E9B949"        # muted amber -- the one bright thing on screen
ACCENT_HOVER = "#F2C863"
ACCENT_PRESS = "#D4A53A"
ON_ACCENT = "#1A1405"

ERROR_TEXT = "#FF8A8A"
TRACK = "#232937"

CLOSE_HOVER = "#C42B1C"   # Windows' own close-button red

LOG_BG = "#0B0E12"
LOG_TEXT = "#AEB6C4"


@dataclass(frozen=True)
class Look:
    fill: str
    hover: str
    press: str
    fg: str
    border: Optional[str] = None
    dot: Optional[str] = None       # status pills
    prefix_fg: Optional[str] = None


PRIMARY = Look(fill=ACCENT, hover=ACCENT_HOVER, press=ACCENT_PRESS, fg=ON_ACCENT)
SECONDARY = Look(fill=SURFACE_2, hover=SURFACE_3, press=SURFACE_2, fg=TEXT, border=BORDER_STRONG)
DANGER = Look(fill="#3A1A1D", hover="#462023", press="#3A1A1D", fg="#FFA3A3", border="#6B2A2F")
DISABLED = Look(fill="#191D26", hover="#191D26", press="#191D26", fg="#566074", border=BORDER)

# Status pills: a dark tint of the color, a quiet border, the color itself
# only on the dot and the word.
OK = Look(fill="#11261D", hover="#153022", press="#11261D", fg="#5BD69A",
          border="#1F4A36", dot="#3DD68C", prefix_fg=TEXT_MUTED)
BAD = Look(fill="#2A1618", hover="#341A1D", press="#2A1618", fg="#FF8F8F",
           border="#552628", dot="#FF6B6B", prefix_fg=TEXT_MUTED)
WAIT = Look(fill="#29230F", hover="#29230F", press="#29230F", fg="#F2C45A",
            border="#544519", dot="#E9B949", prefix_fg=TEXT_MUTED)


# ============================================================
# FONTS
# ============================================================
class Fonts:
    def __init__(self, root: tk.Misc) -> None:
        families = set(tkfont.families(root))
        ui = "Segoe UI Variable Text" if "Segoe UI Variable Text" in families else "Segoe UI"
        semi = "Segoe UI Semibold" if "Segoe UI Semibold" in families else ui
        self.body = tkfont.Font(root, family=ui, size=10)
        self.small = tkfont.Font(root, family=ui, size=9)
        self.small_semi = tkfont.Font(root, family=semi, size=9)
        self.overline = tkfont.Font(root, family=semi, size=8)
        self.button = tkfont.Font(root, family=semi, size=10)
        self.brand = tkfont.Font(root, family=semi, size=10)
        self.title = tkfont.Font(root, family=semi, size=16)
        self.mono = tkfont.Font(root, family="Consolas", size=9)
        self.has_icons = "Segoe MDL2 Assets" in families
        self.icon = tkfont.Font(root, family="Segoe MDL2 Assets" if self.has_icons else ui, size=8)


# Window-control glyphs from Segoe MDL2 Assets (Windows 10 and 11).
GLYPH_MIN, GLYPH_MAX, GLYPH_RESTORE, GLYPH_CLOSE = "", "", "", ""
GLYPH_FALLBACK = {GLYPH_MIN: "_", GLYPH_MAX: "[ ]", GLYPH_RESTORE: "[ ]", GLYPH_CLOSE: "X"}


# ============================================================
# ROUNDED SHAPES
# ============================================================
_cache: Dict[tuple, tk.PhotoImage] = {}


def _rgb(c: str) -> Tuple[int, int, int]:
    return int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)


def _hex(c: Tuple[float, float, float]) -> str:
    return "#%02x%02x%02x" % tuple(int(round(v)) for v in c)


def _mix(a, b, t: float):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


def _coverage(px: float, py: float, x0: float, y0: float, x1: float, y1: float, r: float) -> float:
    """How much of the pixel centred at (px, py) a rounded rectangle covers, 0..1."""
    hw, hh = (x1 - x0) / 2, (y1 - y0) / 2
    qx = abs(px - (x0 + hw)) - (hw - r)
    qy = abs(py - (y0 + hh)) - (hh - r)
    d = math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - r
    return min(1.0, max(0.0, 0.5 - d))


def rounded(w: int, h: int, r: float, fill: str, bg: str,
            border: Optional[str] = None, bw: int = 1) -> tk.PhotoImage:
    """An anti-aliased rounded rectangle, pre-blended onto bg."""
    w, h = max(1, int(w)), max(1, int(h))
    key = (w, h, r, fill, bg, border, bw)
    img = _cache.get(key)
    if img is not None:
        return img

    r = max(0.0, min(r, w / 2, h / 2))
    F, B = _rgb(fill), _rgb(bg)
    E = _rgb(border) if border else None
    hexF = _hex(F)
    hexE = _hex(E) if E else hexF

    def pixel(x: int, y: int) -> str:
        px, py = x + 0.5, y + 0.5
        c = _mix(B, E or F, _coverage(px, py, 0, 0, w, h, r))
        if E:
            c = _mix(c, F, _coverage(px, py, bw, bw, w - bw, h - bw, max(r - bw, 0.0)))
        return _hex(c)

    rr = int(math.ceil(r))
    straight_edge = [hexE] * bw + [hexF] * max(0, w - 2 * bw) + [hexE] * bw if E else [hexF] * w
    rows = []
    for y in range(h):
        if rr and (y < rr or y >= h - rr):
            if w - 2 * rr <= 0:
                row = [pixel(x, y) for x in range(w)]
            else:
                mid = hexE if (E and (y < bw or y >= h - bw)) else hexF
                row = ([pixel(x, y) for x in range(rr)] + [mid] * (w - 2 * rr)
                       + [pixel(x, y) for x in range(w - rr, w)])
        else:
            row = straight_edge
        rows.append("{" + " ".join(row) + "}")

    img = tk.PhotoImage(width=w, height=h)
    img.put(" ".join(rows))
    _cache[key] = img
    return img


# ============================================================
# WIDGETS
# ============================================================
class Button(tk.Canvas):
    """A rounded button, or a status pill (with a dot and a muted prefix).

    Fires on release, and only if the pointer is still over it.
    """

    def __init__(self, master, text: str, command: Callable[[], None], *, look: Look,
                 font: tkfont.Font, bg: str, padx: int, pady: int, radius: float,
                 min_width: int = 0, prefix: str = "", grey_when_disabled: bool = True) -> None:
        super().__init__(master, bg=bg, highlightthickness=0, borderwidth=0, cursor="hand2")
        self.command = command
        self.look = look
        self.font = font
        self.surface = bg
        self.padx, self.pady, self.radius = padx, pady, radius
        self.min_width = min_width
        self.text = text
        self.prefix = prefix
        self.grey_when_disabled = grey_when_disabled
        self.enabled = True
        self._inside = False
        self._pressed = False

        self._bg_item = self.create_image(0, 0, anchor="nw")
        self._dot_item = self.create_image(0, 0, anchor="center", state="hidden")
        self._prefix_item = self.create_text(0, 0, anchor="w", font=font)
        self._text_item = self.create_text(0, 0, anchor="w", font=font)

        self.bind("<Enter>", lambda _: self._set(inside=True))
        self.bind("<Leave>", lambda _: self._set(inside=False, pressed=False))
        self.bind("<ButtonPress-1>", lambda _: self._set(pressed=True))
        self.bind("<ButtonRelease-1>", self._release)
        self._render()

    def update_look(self, text: Optional[str] = None, look: Optional[Look] = None,
                    prefix: Optional[str] = None) -> None:
        changed = False
        if text is not None and text != self.text:
            self.text, changed = text, True
        if look is not None and look != self.look:
            self.look, changed = look, True
        if prefix is not None and prefix != self.prefix:
            self.prefix, changed = prefix, True
        if changed:
            self._render()

    def set_enabled(self, enabled: bool) -> None:
        if enabled != self.enabled:
            self.enabled = enabled
            self.configure(cursor="hand2" if enabled else "arrow")
            self._render()

    def _set(self, inside: Optional[bool] = None, pressed: Optional[bool] = None) -> None:
        if inside is not None:
            self._inside = inside
        if pressed is not None:
            self._pressed = pressed
        self._render()

    def _release(self, e) -> None:
        fire = self.enabled and self._pressed and 0 <= e.x < self.winfo_width() \
            and 0 <= e.y < self.winfo_height()
        self._set(pressed=False)
        if fire:
            self.command()

    def _render(self) -> None:
        look = self.look
        if not self.enabled and self.grey_when_disabled:
            look = DISABLED
        if not self.enabled:
            fill = look.fill
        elif self._pressed and self._inside:
            fill = look.press
        elif self._inside:
            fill = look.hover
        else:
            fill = look.fill

        f = self.font
        gap = f.measure(" ")
        text_w = f.measure(self.text)
        prefix_w = f.measure(self.prefix) + gap if self.prefix else 0
        dot_d = int(f.metrics("linespace") * 0.42) if look.dot else 0
        dot_w = dot_d + int(gap * 1.6) if dot_d else 0
        content = dot_w + prefix_w + text_w

        h = f.metrics("linespace") + 2 * self.pady
        w = max(self.min_width, content + 2 * self.padx)
        self.configure(width=w, height=h)
        self.itemconfigure(self._bg_item, image=rounded(w, h, self.radius, fill, self.surface,
                                                        border=look.border))
        x = (w - content) / 2
        cy = h / 2
        if dot_d:
            self.itemconfigure(self._dot_item, state="normal",
                               image=rounded(dot_d, dot_d, dot_d / 2, look.dot, fill))
            self.coords(self._dot_item, x + dot_d / 2, cy)
            x += dot_w
        else:
            self.itemconfigure(self._dot_item, state="hidden")
        self.itemconfigure(self._prefix_item, text=self.prefix,
                           fill=look.prefix_fg or look.fg)
        self.coords(self._prefix_item, x, cy)
        x += prefix_w
        self.itemconfigure(self._text_item, text=self.text, fill=look.fg)
        self.coords(self._text_item, x, cy)


class Card(tk.Canvas):
    """A rounded panel. Put widgets in .inner; the panel grows to fit them."""

    def __init__(self, master, *, bg: str, fill: str = SURFACE, border: str = BORDER,
                 radius: float = 14, pad: int = 28) -> None:
        super().__init__(master, bg=bg, highlightthickness=0, borderwidth=0)
        self.surface, self.fill, self.border = bg, fill, border
        self.radius, self.pad = radius, pad
        # Our own record of the size, never read back with cget(): Tk 9
        # answers in the units it was given ("10c" for a fresh canvas), not
        # pixels. That crashed every card in the 1.0.0 release.
        self._size = (0, 0)
        self._bg_item = self.create_image(0, 0, anchor="nw")
        self.inner = tk.Frame(self, bg=fill)
        self.create_window(pad, pad, window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._fit)

    def _fit(self, _e=None) -> None:
        w = self.inner.winfo_reqwidth() + 2 * self.pad
        h = self.inner.winfo_reqheight() + 2 * self.pad
        if (w, h) == self._size:
            return
        self._size = (w, h)
        self.configure(width=w, height=h)
        self.itemconfigure(self._bg_item, image=rounded(w, h, self.radius, self.fill,
                                                        self.surface, border=self.border))


class Link(tk.Label):
    """Quiet text that acts as a button."""

    def __init__(self, master, text: str, command: Callable[[], None], *, font, bg: str,
                 fg: str = TEXT_MUTED, hover: str = TEXT, **kw) -> None:
        super().__init__(master, text=text, font=font, bg=bg, fg=fg, cursor="hand2", **kw)
        self._fg, self._hover = fg, hover
        self.bind("<Enter>", lambda _: self.configure(fg=self._hover))
        self.bind("<Leave>", lambda _: self.configure(fg=self._fg))
        self.bind("<ButtonRelease-1>", lambda _: command())


class WindowButton(tk.Label):
    """Minimize / maximize / close, in the Windows style. Size it with a
    fixed-size parent frame."""

    def __init__(self, master, glyph: str, command: Callable[[], None], *, fonts: Fonts,
                 hover_bg: str = SURFACE_3, hover_fg: str = TEXT) -> None:
        text = glyph if fonts.has_icons else GLYPH_FALLBACK.get(glyph, glyph)
        super().__init__(master, text=text, font=fonts.icon, bg=SURFACE, fg=TEXT_MUTED)
        self.fonts = fonts
        self._hover_bg, self._hover_fg = hover_bg, hover_fg
        self.bind("<Enter>", lambda _: self.configure(bg=self._hover_bg, fg=self._hover_fg))
        self.bind("<Leave>", lambda _: self.configure(bg=SURFACE, fg=TEXT_MUTED))
        self.bind("<ButtonRelease-1>", self._release)
        self.command = command

    def set_glyph(self, glyph: str) -> None:
        self.configure(text=glyph if self.fonts.has_icons else GLYPH_FALLBACK.get(glyph, glyph))

    def _release(self, e) -> None:
        if 0 <= e.x < self.winfo_width() and 0 <= e.y < self.winfo_height():
            self.command()

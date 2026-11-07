"""Tema grafico dell'app: palette, font e stili ttk costruiti sul tema "clam".

Il tema "clam" disegna tutto con primitive Tk (niente immagini), quindi si
lascia personalizzare nei colori e scala bene con i DPI dello schermo.
"""

from __future__ import annotations

import base64
import math
import struct
import tkinter as tk
import zlib
from dataclasses import dataclass
from tkinter import font as tkfont
from tkinter import ttk


@dataclass(frozen=True)
class Palette:
    bg: str = "#F3F4F8"
    surface: str = "#FFFFFF"
    surface_alt: str = "#F7F8FB"
    border: str = "#E1E4EC"
    border_strong: str = "#C9CFDB"
    text: str = "#1D2130"
    muted: str = "#646C80"
    subtle: str = "#9CA3B4"
    accent: str = "#6A44D8"
    accent_hover: str = "#5A36C6"
    accent_pressed: str = "#4A2AAA"
    accent_soft: str = "#EEEAFD"
    accent_disabled: str = "#C9BDF2"
    header_from: str = "#6040D6"
    header_to: str = "#2E1A78"
    header_sub: str = "#D8CFFF"
    ok: str = "#2E7D53"
    warn: str = "#8F5208"
    warn_soft: str = "#FFF6E3"
    warn_border: str = "#F1D9A6"
    danger: str = "#C0392B"
    selection: str = "#E2D9FB"
    selection_idle: str = "#ECE8F7"
    tooltip_bg: str = "#262A36"


P = Palette()


def mix(color_a: str, color_b: str, t: float) -> str:
    """Colore intermedio tra due colori esadecimali (t da 0 a 1)."""
    a = [int(color_a[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(color_b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


# --- icone disegnate al volo (PNG con trasparenza, senza file esterni) ------------

def _rgb(color: str) -> tuple[int, int, int]:
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


def _png_base64(width: int, height: int, rows: list[bytes]) -> str:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + row for row in rows)
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9))
           + chunk(b"IEND", b""))
    return base64.b64encode(png).decode("ascii")


def _render(root: tk.Misc, width: int, height: int, shader, ss: int = 4) -> tk.PhotoImage:
    """Immagine antialiasata: `shader(x, y)` restituisce un colore "#rrggbb" o None
    per ogni campione (coordinate in pixel)."""
    rows = []
    n = ss * ss
    for y in range(height):
        row = bytearray()
        for x in range(width):
            r = g = b = hits = 0
            for sy in range(ss):
                for sx in range(ss):
                    color = shader(x + (sx + 0.5) / ss, y + (sy + 0.5) / ss)
                    if color:
                        cr, cg, cb = _rgb(color)
                        r += cr
                        g += cg
                        b += cb
                        hits += 1
            if hits:
                row += bytes((r // hits, g // hits, b // hits, round(255 * hits / n)))
            else:
                row += b"\x00\x00\x00\x00"
        rows.append(bytes(row))
    return tk.PhotoImage(master=root, data=_png_base64(width, height, rows))


def _round_rect_sdf(x, y, x0, y0, x1, y1, r) -> float:
    """Distanza (negativa dentro) da un rettangolo con angoli arrotondati."""
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    hx, hy = (x1 - x0) / 2 - r, (y1 - y0) / 2 - r
    qx, qy = abs(x - cx) - hx, abs(y - cy) - hy
    outside = math.hypot(max(qx, 0), max(qy, 0))
    return outside + min(max(qx, qy), 0) - r


def _segment_distance(px_, py_, ax, ay, bx, by) -> float:
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px_ - ax) * dx + (py_ - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px_ - (ax + t * dx), py_ - (ay + t * dy))


def make_icons(root: tk.Misc, px) -> dict[str, tk.PhotoImage]:
    """Caselle di spunta, pallini di stato e icona cartella, alla scala dello schermo."""
    icons: dict[str, tk.PhotoImage] = {}

    s = px(16)
    gap = px(8)  # spazio tra la casella e il testo, incluso nell'immagine
    border_w = max(1.0, s / 14)
    radius = s * 0.2
    check = [(0.26 * s, 0.52 * s), (0.43 * s, 0.69 * s), (0.75 * s, 0.34 * s)]
    stroke = s * 0.075

    def checkbox(fill: str, border: str, mark: str | None):
        def shader(x, y):
            if x > s:
                return None
            d = _round_rect_sdf(x, y, 0.5, 0.5, s - 0.5, s - 0.5, radius)
            if d > 0:
                return None
            if mark and min(_segment_distance(x, y, *check[0], *check[1]),
                            _segment_distance(x, y, *check[1], *check[2])) <= stroke:
                return mark
            return border if d > -border_w else fill
        return _render(root, s + gap, s, shader)

    icons["cb_off"] = checkbox(P.surface, P.border_strong, None)
    icons["cb_off_hover"] = checkbox(P.surface, P.accent, None)
    icons["cb_off_disabled"] = checkbox(P.surface_alt, P.border, None)
    icons["cb_on"] = checkbox(P.accent, P.accent, "#FFFFFF")
    icons["cb_on_hover"] = checkbox(P.accent_hover, P.accent_hover, "#FFFFFF")
    icons["cb_on_disabled"] = checkbox(P.accent_disabled, P.accent_disabled, "#FFFFFF")

    def radio(fill: str, border: str, mark: str | None):
        c = s / 2

        def shader(x, y):
            if x > s:
                return None
            dist = math.hypot(x - c, y - c)
            if dist > c - 0.5:
                return None
            if mark and dist <= s * 0.2:
                return mark
            return border if dist > c - 0.5 - border_w else fill
        return _render(root, s + gap, s, shader)

    icons["rb_off"] = radio(P.surface, P.border_strong, None)
    icons["rb_off_hover"] = radio(P.surface, P.accent, None)
    icons["rb_off_disabled"] = radio(P.surface_alt, P.border, None)
    icons["rb_on"] = radio(P.accent, P.accent, "#FFFFFF")
    icons["rb_on_hover"] = radio(P.accent_hover, P.accent_hover, "#FFFFFF")
    icons["rb_on_disabled"] = radio(P.accent_disabled, P.accent_disabled, "#FFFFFF")

    # pallini di stato per la tabella (con margine a destra prima del testo)
    d = px(9)
    w, h = d + px(7), px(16)
    cy = h / 2

    def dot(fill: str, ring: bool = False):
        def shader(x, y):
            dist = math.hypot(x - d / 2 - 0.5, y - cy)
            if dist > d / 2:
                return None
            if ring and dist < d / 2 - max(1.2, d / 6):
                return None
            return fill
        return _render(root, w, h, shader)

    icons["st_change"] = dot(P.accent)
    icons["st_warn"] = dot("#E39A12")
    icons["st_ok"] = dot("#3BA272")
    icons["st_excluded"] = dot(P.subtle, ring=True)

    # cartella
    fw, fh = px(16), px(14)
    total_w = fw + px(6)

    def folder(x, y):
        if x > fw:
            return None
        tab = _round_rect_sdf(x, y, 0.5, 1, fw * 0.48, fh * 0.45, 1.5)
        body = _round_rect_sdf(x, y, 0.5, fh * 0.25, fw - 0.5, fh - 0.5, 1.8)
        if body <= 0:
            return P.accent_disabled if y > fh * 0.25 + 1.2 else "#B3A2EC"
        if tab <= 0:
            return "#B3A2EC"
        return None

    icons["folder"] = _render(root, total_w, fh, folder)
    return icons


def _family(root: tk.Misc, *candidates: str) -> str:
    available = set(tkfont.families(root))
    for name in candidates:
        if name in available:
            return name
    return tkfont.nametofont("TkDefaultFont").actual("family")


def apply_theme(root: tk.Tk, px, icons: dict[str, tk.PhotoImage]) -> dict[str, tkfont.Font]:
    """Configura font e stili; restituisce i font dell'app per nome.

    `px(n)` converte pixel "a 96 DPI" nei pixel reali dello schermo;
    `icons` sono le immagini create da `make_icons` (caselle di spunta).
    """
    family = _family(root, "Segoe UI", "Helvetica", "Arial")
    semibold_family = _family(root, "Segoe UI Semibold", family)
    symbol_family = _family(root, "Segoe UI Symbol", family)

    for name, size in (("TkDefaultFont", 10), ("TkTextFont", 10), ("TkMenuFont", 10),
                       ("TkHeadingFont", 10), ("TkCaptionFont", 10), ("TkTooltipFont", 9),
                       ("TkSmallCaptionFont", 9), ("TkIconFont", 10)):
        try:
            tkfont.nametofont(name).configure(family=family, size=size)
        except tk.TclError:
            pass

    fonts = {
        "base": tkfont.nametofont("TkDefaultFont"),
        "bold": tkfont.Font(root, family=semibold_family, size=10),
        "small": tkfont.Font(root, family=family, size=9),
        "small_bold": tkfont.Font(root, family=semibold_family, size=9),
        "section": tkfont.Font(root, family=semibold_family, size=8),
        "italic": tkfont.Font(root, family=family, size=10, slant="italic"),
        "title": tkfont.Font(root, family=semibold_family, size=15),
        "detail_title": tkfont.Font(root, family=semibold_family, size=12),
        "logo": tkfont.Font(root, family=symbol_family, size=22),
        "empty_icon": tkfont.Font(root, family=symbol_family, size=40),
        "empty_title": tkfont.Font(root, family=semibold_family, size=14),
        "mono": tkfont.Font(root, family=_family(root, "Cascadia Mono", "Consolas", "Courier New"), size=9),
    }

    style = ttk.Style(root)
    style.theme_use("clam")
    root.configure(background=P.bg)

    style.configure(
        ".", background=P.bg, foreground=P.text, font=fonts["base"],
        bordercolor=P.border, lightcolor=P.bg, darkcolor=P.bg, troughcolor=P.border,
        focuscolor=P.accent, selectbackground=P.accent, selectforeground="#FFFFFF",
        insertcolor=P.text, fieldbackground=P.surface, arrowcolor=P.muted,
    )
    style.map(".", foreground=[("disabled", P.subtle)])

    # --- contenitori ------------------------------------------------------------
    style.configure("TFrame", background=P.bg)
    style.configure("Surface.TFrame", background=P.surface)
    style.configure("Card.TFrame", background=P.surface, bordercolor=P.border,
                    lightcolor=P.border, darkcolor=P.border, relief="solid", borderwidth=1)
    style.configure("Notice.TFrame", background=P.warn_soft, bordercolor=P.warn_border,
                    lightcolor=P.warn_border, darkcolor=P.warn_border, relief="solid", borderwidth=1)
    style.configure("TSeparator", background=P.border)
    style.configure("TPanedwindow", background=P.bg)
    style.configure("Sash", sashthickness=px(10), gripcount=0, background=P.bg,
                    lightcolor=P.bg, darkcolor=P.bg, bordercolor=P.bg)

    # --- etichette --------------------------------------------------------------
    style.configure("TLabel", background=P.bg, foreground=P.text)
    style.configure("Muted.TLabel", foreground=P.muted)
    style.configure("Status.TLabel", foreground=P.muted)
    style.configure("Card.TLabel", background=P.surface)
    style.configure("CardMuted.TLabel", background=P.surface, foreground=P.muted)
    style.configure("CardSection.TLabel", background=P.surface, foreground=P.muted, font=fonts["section"])
    style.configure("CardError.TLabel", background=P.surface, foreground=P.danger)
    style.configure("CardWarn.TLabel", background=P.surface, foreground=P.warn)
    style.configure("Example.TLabel", background=P.surface, foreground=P.accent)
    style.configure("DetailTitle.TLabel", background=P.surface, font=fonts["detail_title"])
    style.configure("Notice.TLabel", background=P.warn_soft, foreground=P.warn)
    style.configure("NoticeClose.TLabel", background=P.warn_soft, foreground=P.warn, font=fonts["bold"])
    style.configure("EmptyIcon.TLabel", background=P.surface, foreground=P.accent_disabled, font=fonts["empty_icon"])
    style.configure("EmptyTitle.TLabel", background=P.surface, font=fonts["empty_title"])
    style.configure("Step.TLabel", background=P.accent_soft, foreground=P.accent_pressed,
                    font=fonts["small_bold"], anchor="center")

    # --- pulsanti ---------------------------------------------------------------
    style.configure("TButton", background=P.surface, foreground=P.text, bordercolor=P.border_strong,
                    lightcolor=P.surface, darkcolor=P.surface, focuscolor=P.surface,
                    focusthickness=0, padding=(px(14), px(6)), anchor="center")
    style.map(
        "TButton",
        background=[("disabled", P.surface_alt), ("pressed", P.border), ("active", P.surface_alt)],
        lightcolor=[("disabled", P.surface_alt), ("pressed", P.border), ("active", P.surface_alt)],
        darkcolor=[("disabled", P.surface_alt), ("pressed", P.border), ("active", P.surface_alt)],
        bordercolor=[("disabled", P.border), ("focus", P.accent), ("active", P.subtle)],
        focuscolor=[("pressed", P.border), ("active", P.surface_alt)],
        foreground=[("disabled", P.subtle)],
    )
    style.configure("Small.TButton", padding=(px(10), px(3)))
    style.configure("Accent.TButton", background=P.accent, foreground="#FFFFFF", bordercolor=P.accent,
                    lightcolor=P.accent, darkcolor=P.accent, focuscolor=P.accent, font=fonts["bold"])
    style.map(
        "Accent.TButton",
        background=[("disabled", P.accent_disabled), ("pressed", P.accent_pressed), ("active", P.accent_hover)],
        lightcolor=[("disabled", P.accent_disabled), ("pressed", P.accent_pressed), ("active", P.accent_hover)],
        darkcolor=[("disabled", P.accent_disabled), ("pressed", P.accent_pressed), ("active", P.accent_hover)],
        bordercolor=[("disabled", P.accent_disabled), ("focus", P.accent_pressed),
                     ("pressed", P.accent_pressed), ("active", P.accent_hover)],
        focuscolor=[("disabled", P.accent_disabled), ("pressed", P.accent_pressed), ("active", P.accent_hover)],
        foreground=[("disabled", "#FFFFFF")],
    )

    # --- caselle di spunta (indicatore disegnato: spunta vera, non la "X" di clam) ---
    style.element_create(
        "Organizer.Checkbutton.indicator", "image", icons["cb_off"],
        ("disabled", "selected", icons["cb_on_disabled"]),
        ("disabled", icons["cb_off_disabled"]),
        ("selected", "active", icons["cb_on_hover"]),
        ("selected", icons["cb_on"]),
        ("active", icons["cb_off_hover"]),
        sticky="",
    )
    style.layout("TCheckbutton", [
        ("Checkbutton.padding", {"sticky": "nswe", "children": [
            ("Organizer.Checkbutton.indicator", {"side": "left", "sticky": ""}),
            ("Checkbutton.focus", {"side": "left", "sticky": "w", "children": [
                ("Checkbutton.label", {"sticky": "nswe"}),
            ]}),
        ]}),
    ])
    style.configure("TCheckbutton", background=P.surface, foreground=P.text,
                    padding=(0, px(3)), focuscolor=P.surface, focusthickness=1)
    style.map("TCheckbutton", background=[("active", P.surface)], foreground=[("disabled", P.subtle)],
              focuscolor=[("focus", P.border_strong)])

    # --- scelte alternative (radiobutton con indicatore disegnato come le caselle) --
    style.element_create(
        "Organizer.Radiobutton.indicator", "image", icons["rb_off"],
        ("disabled", "selected", icons["rb_on_disabled"]),
        ("disabled", icons["rb_off_disabled"]),
        ("selected", "active", icons["rb_on_hover"]),
        ("selected", icons["rb_on"]),
        ("active", icons["rb_off_hover"]),
        sticky="",
    )
    style.layout("TRadiobutton", [
        ("Radiobutton.padding", {"sticky": "nswe", "children": [
            ("Organizer.Radiobutton.indicator", {"side": "left", "sticky": ""}),
            ("Radiobutton.focus", {"side": "left", "sticky": "w", "children": [
                ("Radiobutton.label", {"sticky": "nswe"}),
            ]}),
        ]}),
    ])
    style.configure("TRadiobutton", background=P.surface, foreground=P.text,
                    padding=(0, px(3)), focuscolor=P.surface, focusthickness=1)
    style.map("TRadiobutton", background=[("active", P.surface)], foreground=[("disabled", P.subtle)],
              focuscolor=[("focus", P.border_strong)])

    # --- filtri a "pillola" (radiobutton senza indicatore) -------------------------
    style.layout("Chip.TRadiobutton", [
        ("Radiobutton.border", {"sticky": "nswe", "children": [
            ("Radiobutton.padding", {"sticky": "nswe", "children": [
                ("Radiobutton.label", {"sticky": "nswe"}),
            ]}),
        ]}),
    ])
    style.configure("Chip.TRadiobutton", background=P.bg, foreground=P.muted, padding=(px(12), px(5)),
                    bordercolor=P.border, lightcolor=P.bg, darkcolor=P.bg, relief="solid", borderwidth=1)
    style.map(
        "Chip.TRadiobutton",
        background=[("selected", P.accent_soft), ("active", P.surface)],
        lightcolor=[("selected", P.accent_soft), ("active", P.surface)],
        darkcolor=[("selected", P.accent_soft), ("active", P.surface)],
        bordercolor=[("selected", P.accent_disabled), ("active", P.border_strong)],
        foreground=[("disabled", P.subtle), ("selected", P.accent_pressed), ("active", P.text)],
        font=[("selected", fonts["bold"])],
    )

    # --- campi di testo -----------------------------------------------------------
    field_common = dict(fieldbackground=P.surface, bordercolor=P.border_strong, lightcolor=P.surface,
                        darkcolor=P.surface, padding=(px(7), px(4)), insertcolor=P.text,
                        selectbackground=P.selection, selectforeground=P.text)
    style.configure("TEntry", **field_common)
    style.configure("TCombobox", **field_common, background=P.surface, arrowsize=px(12))
    for name in ("TEntry", "TCombobox"):
        style.map(
            name,
            bordercolor=[("disabled", P.border), ("focus", P.accent), ("hover", P.subtle)],
            lightcolor=[("focus", P.accent_soft)],
            darkcolor=[("focus", P.accent_soft)],
            fieldbackground=[("disabled", P.surface_alt), ("readonly", P.surface)],
            foreground=[("disabled", P.subtle)],
            background=[("disabled", P.surface_alt), ("pressed", P.border), ("active", P.surface_alt)],
            arrowcolor=[("disabled", P.subtle)],
        )
    style.configure("Invalid.TCombobox", bordercolor=P.danger)
    style.map("Invalid.TCombobox", bordercolor=[("focus", P.danger), ("hover", P.danger)],
              lightcolor=[("focus", "#F8DCD8")], darkcolor=[("focus", "#F8DCD8")])
    root.option_add("*TCombobox*Listbox.background", P.surface)
    root.option_add("*TCombobox*Listbox.foreground", P.text)
    root.option_add("*TCombobox*Listbox.selectBackground", P.accent)
    root.option_add("*TCombobox*Listbox.selectForeground", "#FFFFFF")
    root.option_add("*TCombobox*Listbox.font", fonts["base"])
    root.option_add("*TCombobox*Listbox.relief", "flat")

    # --- tabelle --------------------------------------------------------------------
    linespace = fonts["base"].metrics("linespace")
    style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])  # senza bordo: lo da la card
    style.configure("Treeview", background=P.surface, fieldbackground=P.surface, foreground=P.text,
                    rowheight=linespace + px(10), borderwidth=0, indent=px(18))
    style.map("Treeview",
              background=[("selected", "focus", P.selection), ("selected", P.selection_idle)],
              foreground=[("selected", P.text)])
    style.configure("Treeview.Heading", background=P.surface_alt, foreground=P.muted,
                    font=fonts["small_bold"], relief="flat", padding=(px(8), px(6)),
                    bordercolor=P.border, lightcolor=P.surface_alt, darkcolor=P.surface_alt)
    style.map("Treeview.Heading",
              background=[("active", P.border)],
              lightcolor=[("active", P.border)], darkcolor=[("active", P.border)])
    style.configure("Compare.Treeview", rowheight=linespace + px(4))

    # --- barre di scorrimento e avanzamento -------------------------------------------
    for orient in ("Vertical", "Horizontal"):
        style.configure(f"{orient}.TScrollbar", background=P.border, troughcolor=P.surface,
                        bordercolor=P.surface, lightcolor=P.border, darkcolor=P.border,
                        arrowcolor=P.muted, gripcount=0, arrowsize=px(13), relief="flat")
        style.map(f"{orient}.TScrollbar",
                  background=[("pressed", P.subtle), ("active", P.border_strong)],
                  lightcolor=[("pressed", P.subtle), ("active", P.border_strong)],
                  darkcolor=[("pressed", P.subtle), ("active", P.border_strong)],
                  arrowcolor=[("disabled", P.border_strong)])
    style.configure("Horizontal.TProgressbar", troughcolor=P.border, background=P.accent,
                    bordercolor=P.border, lightcolor=P.accent, darkcolor=P.accent, thickness=px(8))

    return fonts

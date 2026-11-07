"""Finestra principale dell'app (tkinter, in italiano).

Flusso: scegli cartella -> Analizza -> anteprima raggruppata per cartella (con
filtri, ricerca, dettagli, correzioni ed esclusioni) -> Applica -> Annulla.

Formato dei nomi file e delle cartelle personalizzabile con pattern come
"{nn} - {titolo}" o "{artista} - {album} ({disco})".
"""

from __future__ import annotations

import ctypes
import json
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import applier, converter
from .applier import ApplyResult, UndoResult, journal_info
from .convert_dialog import ConvertDialog, ConvertRequest
from .converter import ConvertResult, format_size
from .filename_parser import expand_pattern
from .planner import (
    DEFAULT_FILENAME_PATTERN,
    DEFAULT_FOLDER_PATTERN,
    FILENAME_PRESETS,
    FOLDER_PRESETS,
    PATTERN_FIELDS,
    CancelledError,
    FolderRename,
    Plan,
    PlanItem,
    analyze,
    pattern_error,
    replan,
)
from .scanner import scan_audio
from .theme import P, apply_theme, make_icons, mix
from .widgets import EditDialog, HelpDialog, Tooltip


def _app_dir() -> Path:
    """Cartella dell'app: accanto all'eseguibile (PyInstaller) o radice del progetto."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _resource(name: str) -> Path:
    """File incluso nell'app (nell'eseguibile PyInstaller sta in _MEIPASS)."""
    base = Path(getattr(sys, "_MEIPASS", _app_dir()))
    return base / name


CONFIG_PATH = _app_dir() / "config.json"
LOG_PATH = applier.JOURNAL_DIR / "errori.log"
MAX_RECENT = 8

SOURCE_LABELS = {
    "tag": "Tag",
    "nome file": "Nome file",
    "nome cartella": "Cartella",
    "internet": "Internet",
    "manuale": "Manuale",
    "analisi": "Analisi",
}

# colonne della tabella: chiave -> (intestazione, larghezza, allineamento)
# (fonte dei dati e affidabilita dell'abbinamento online sono nel pannello dettagli)
COLUMNS = {
    "stato": ("Stato", 118, "w"),
    "nuovo": ("Nuovo nome", 280, "w"),
    "titolo": ("Titolo", 170, "w"),
    "artista": ("Artista", 130, "w"),
    "album": ("Album", 150, "w"),
    "traccia": ("Nº", 42, "center"),
    "anno": ("Anno", 54, "center"),
}
TREE_COLUMN = ("File", 260)

STATUS_LABELS = {
    "warn": "Da controllare",
    "change": "Da modificare",
    "ok": "Già a posto",
    "excluded": "Escluso",
}
STATUS_ORDER = {"warn": 0, "change": 1, "ok": 2, "excluded": 3}
FILTERS = (
    ("all", "Tutti"),
    ("change", "Da modificare"),
    ("warn", "Da controllare"),
    ("ok", "Già a posto"),
    ("excluded", "Esclusi"),
)
COMPARE_FIELDS = (("title", "Titolo"), ("artist", "Artista"), ("album", "Album"),
                  ("albumartist", "Artista album"), ("track", "Nº traccia"),
                  ("year", "Anno"), ("genre", "Genere"))


def _enable_dpi_awareness() -> None:
    """Testo nitido sugli schermi ad alta risoluzione (Windows)."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    try:
        # icona dell'app (non quella di Python) nella barra delle applicazioni
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MusicOrganizer.App")
    except Exception:
        pass


def _log_exception(context: str, exc_text: str) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] {context}\n{exc_text}\n")
    except OSError:
        pass


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


class App(tk.Tk):
    def __init__(self):
        _enable_dpi_awareness()
        super().__init__()
        self.withdraw()
        self.scale = max(1.0, self.winfo_fpixels("1i") / 96)
        self.icons = make_icons(self, self.px)
        self.fonts = apply_theme(self, self.px, self.icons)
        self.title("Music Organizer")
        self.minsize(self.px(1000), self.px(640))
        try:
            self.iconbitmap(default=str(_resource("icon.ico")))
        except tk.TclError:
            pass

        # stato dell'analisi
        self.plan: Plan | None = None
        self.groups: list[tuple[Path, list[int]]] = []   # cartella -> indici dei brani
        self.analysis_settings: dict | None = None
        self.excluded_paths: set[str] = set()             # esclusioni, mantenute tra le analisi
        self.manual_by_path: dict[str, dict] = {}         # correzioni manuali non ancora applicate
        self.collapsed: set[str] = set()                  # cartelle chiuse nella tabella
        self.convert_cfg: dict = {}                       # ultime scelte della conversione
        self.sort_column: str | None = None
        self.sort_desc = False

        # lavoro in background
        self.queue: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.cancel_event = threading.Event()
        self.busy_kind: str | None = None
        self._closing = False
        self._after_ids: dict[str, str] = {}

        self.config_data = self._read_config()
        self._build_ui()
        self._apply_config()
        self._bind_shortcuts()
        self._refresh_controls()
        self._show_empty_state()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll_queue)
        self.deiconify()

    def px(self, n: float) -> int:
        return int(round(n * self.scale))

    # ==========================================================================
    # costruzione dell'interfaccia
    # ==========================================================================

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self._build_header()
        self._build_settings()
        self._build_results()
        self._build_footer()

    def _build_header(self):
        self.header = tk.Canvas(self, height=self.px(64), highlightthickness=0, borderwidth=0,
                                background=P.header_to)
        self.header.grid(row=0, column=0, sticky="ew")
        self.header.bind("<Configure>", self._draw_header)
        self.header.tag_bind("help", "<Button-1>", lambda e: self._show_help())
        self.header.tag_bind("help", "<Enter>", lambda e: self.header.config(cursor="hand2"))
        self.header.tag_bind("help", "<Leave>", lambda e: self.header.config(cursor=""))

    def _draw_header(self, _event=None):
        c = self.header
        w, h = c.winfo_width(), c.winfo_height()
        c.delete("all")
        steps = 48
        for i in range(steps):
            x0 = w * i / steps
            c.create_rectangle(x0, 0, x0 + w / steps + 1, h, outline="",
                               fill=mix(P.header_from, P.header_to, i / (steps - 1)))
        pad = self.px(20)
        c.create_text(pad, h / 2, text="♫", fill="#FFFFFF", font=self.fonts["logo"], anchor="w")
        x = pad + self.px(38)
        c.create_text(x, h / 2 - self.px(10), text="Music Organizer", fill="#FFFFFF",
                      font=self.fonts["title"], anchor="w")
        c.create_text(x, h / 2 + self.px(13), fill=P.header_sub, font=self.fonts["small"], anchor="w",
                      text="Metadati corretti e nomi file uniformi per tutta la tua libreria musicale")
        c.create_text(w - pad, h / 2, text="?  Guida", fill="#FFFFFF", font=self.fonts["bold"],
                      anchor="e", tags=("help",))

    def _build_settings(self):
        outer = ttk.Frame(self, padding=(self.px(16), self.px(14), self.px(16), self.px(10)))
        outer.grid(row=1, column=0, sticky="ew")
        outer.columnconfigure(0, weight=1)
        card = ttk.Frame(outer, style="Card.TFrame", padding=(self.px(16), self.px(12)))
        card.grid(row=0, column=0, sticky="ew")
        card.columnconfigure(1, weight=1)

        # --- cartella -------------------------------------------------------------
        ttk.Label(card, text="Cartella della musica", style="Card.TLabel", font=self.fonts["bold"]).grid(
            row=0, column=0, sticky="w", padx=(0, self.px(12)))
        self.folder_var = tk.StringVar()
        self.folder_combo = ttk.Combobox(card, textvariable=self.folder_var)
        self.folder_combo.grid(row=0, column=1, sticky="ew")
        self.folder_combo.bind("<Return>", lambda e: self._analyze())
        self.folder_combo.bind("<<ComboboxSelected>>", lambda e: self._update_notice())
        self.folder_var.trace_add("write", lambda *_: self._debounce("folder", 300, self._update_notice))
        self.browse_btn = ttk.Button(card, text="Sfoglia…", command=self._choose_folder)
        self.browse_btn.grid(row=0, column=2, padx=(self.px(8), 0))
        Tooltip(self.browse_btn, "Scegli la cartella della musica (Ctrl+O).\n"
                                 "Le ultime cartelle usate sono nel menu a tendina.")
        self.analyze_btn = ttk.Button(card, text="Analizza", style="Accent.TButton", width=12,
                                      command=self._analyze)
        self.analyze_btn.grid(row=0, column=3, padx=(self.px(8), 0))
        Tooltip(self.analyze_btn, "Legge i file e prepara l'anteprima. Nessun file viene modificato. (F5)")

        ttk.Separator(card).grid(row=1, column=0, columnspan=4, sticky="ew", pady=self.px(12))

        opts = ttk.Frame(card, style="Surface.TFrame")
        opts.grid(row=2, column=0, columnspan=4, sticky="ew")
        opts.columnconfigure(1, weight=1)

        # --- ricerca online -------------------------------------------------------
        left = ttk.Frame(opts, style="Surface.TFrame")
        left.grid(row=0, column=0, sticky="nw", padx=(0, self.px(40)))
        ttk.Label(left, text="RICERCA ONLINE", style="CardSection.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, self.px(4)))
        self.online_var = tk.BooleanVar(value=True)
        self.covers_var = tk.BooleanVar(value=False)
        self.online_cb = ttk.Checkbutton(left, text="Completa i dati mancanti da internet",
                                         variable=self.online_var, command=self._on_search_option_change)
        self.online_cb.grid(row=1, column=0, sticky="w")
        Tooltip(self.online_cb, "Cerca su Deezer (e MusicBrainz per i brani rari) solo i campi che "
                                "mancano. I dati già presenti non vengono mai sovrascritti.")
        self.covers_cb = ttk.Checkbutton(left, text="Scarica le copertine mancanti",
                                         variable=self.covers_var, command=self._on_search_option_change)
        self.covers_cb.grid(row=2, column=0, sticky="w")
        Tooltip(self.covers_cb, "Se un album non ha copertina, la scarica, la salva come cover.jpg "
                                "e la incorpora nei file (MP3, FLAC, M4A, OGG, Opus, WAV).")

        # --- nomi e cartelle --------------------------------------------------------
        right = ttk.Frame(opts, style="Surface.TFrame")
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(2, weight=1)
        ttk.Label(right, text="NOMI E CARTELLE", style="CardSection.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, self.px(4)))
        indent = self.px(16) + self.px(8)  # allinea il testo a quello delle caselle di spunta
        ttk.Label(right, text="Nome dei file", style="Card.TLabel", padding=(indent, 0, 0, 0)).grid(
            row=1, column=0, sticky="w", padx=(0, self.px(12)))
        self.pattern_var = tk.StringVar(value=DEFAULT_FILENAME_PATTERN)
        self.pattern_combo = ttk.Combobox(right, textvariable=self.pattern_var, values=FILENAME_PRESETS, width=38)
        self.pattern_combo.grid(row=1, column=1, sticky="w", pady=self.px(2))
        self.filename_example = ttk.Label(right, text="", style="Example.TLabel")
        self.filename_example.grid(row=1, column=2, sticky="w", padx=(self.px(12), 0))

        self.folder_rename_var = tk.BooleanVar(value=False)
        self.folder_rename_cb = ttk.Checkbutton(right, text="Rinomina le cartelle", variable=self.folder_rename_var,
                                                command=self._on_settings_change)
        self.folder_rename_cb.grid(row=2, column=0, sticky="w", padx=(0, self.px(12)))
        Tooltip(self.folder_rename_cb, "Rinomina le cartelle degli album in base ai dati corretti, "
                                       "es. «2Pac - Greatest Hits [CD1]» → «2Pac - Greatest Hits (CD1)». "
                                       "I file non audio (.log, .txt…) restano dentro.")
        self.folder_pattern_var = tk.StringVar(value=DEFAULT_FOLDER_PATTERN)
        self.folder_pattern_combo = ttk.Combobox(right, textvariable=self.folder_pattern_var,
                                                 values=FOLDER_PRESETS, width=38)
        self.folder_pattern_combo.grid(row=2, column=1, sticky="w", pady=self.px(2))
        self.folder_example = ttk.Label(right, text="", style="Example.TLabel")
        self.folder_example.grid(row=2, column=2, sticky="w", padx=(self.px(12), 0))

        self.reorganize_var = tk.BooleanVar(value=False)
        self.reorganize_cb = ttk.Checkbutton(right, text="Sposta i file in cartelle Artista\\Album",
                                             variable=self.reorganize_var, command=self._on_settings_change)
        self.reorganize_cb.grid(row=3, column=0, columnspan=3, sticky="w", pady=(self.px(2), 0))
        Tooltip(self.reorganize_cb, "Sposta ogni brano in «Artista\\Album» dentro la cartella scelta "
                                    "(le compilation vanno in «Various Artists»). "
                                    "Sostituisce la rinomina delle cartelle.")

        fields_help = "Campi disponibili:\n" + "\n".join(f"{k}  {v}" for k, v in PATTERN_FIELDS.items())
        for combo, kind in ((self.pattern_combo, "file"), (self.folder_pattern_combo, "folder")):
            combo.bind("<<ComboboxSelected>>", self._on_settings_change)
            combo.bind("<Return>", self._on_settings_change)
            combo.bind("<FocusOut>", self._on_settings_change)
            Tooltip(combo, fields_help + ("\n\nDeve contenere {titolo}." if kind == "file"
                                          else "\n\nDeve contenere {album}."))
        self.pattern_var.trace_add("write", lambda *_: self._on_pattern_typing())
        self.folder_pattern_var.trace_add("write", lambda *_: self._on_pattern_typing())

    def _build_results(self):
        area = ttk.Frame(self, padding=(self.px(16), 0, self.px(16), 0))
        area.grid(row=2, column=0, sticky="nsew")
        area.columnconfigure(0, weight=1)
        area.rowconfigure(2, weight=1)

        # --- barra degli avvisi (nascosta finche non serve) -----------------------------
        self.notice = ttk.Frame(area, style="Notice.TFrame", padding=(self.px(12), self.px(7)))
        self.notice.columnconfigure(0, weight=1)
        self.notice_label = ttk.Label(self.notice, text="", style="Notice.TLabel", wraplength=self.px(900))
        self.notice_label.grid(row=0, column=0, sticky="w")
        self.notice_btn = ttk.Button(self.notice, text="Rianalizza", style="Small.TButton", command=self._analyze)
        self.notice_btn.grid(row=0, column=1, padx=(self.px(12), 0))

        # --- filtri e ricerca --------------------------------------------------------------
        bar = ttk.Frame(area)
        bar.grid(row=1, column=0, sticky="ew", pady=(0, self.px(8)))
        self.filter_var = tk.StringVar(value="all")
        self.chips: dict[str, ttk.Radiobutton] = {}
        for key, label in FILTERS:
            chip = ttk.Radiobutton(bar, text=label, value=key, variable=self.filter_var,
                                   style="Chip.TRadiobutton", command=self._rebuild_tree, takefocus=False)
            chip.pack(side="left", padx=(0, self.px(6)))
            self.chips[key] = chip
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(bar, textvariable=self.search_var, width=34)
        self.search_entry.pack(side="right")
        # testo segnaposto, nascosto quando si scrive
        self.search_hint = tk.Label(self.search_entry, text="Cerca titolo, artista, album, file…  (Ctrl+F)",
                                    background=P.surface, foreground=P.subtle, font=self.fonts["base"],
                                    cursor="xterm", borderwidth=0, padx=0, pady=0)
        self.search_hint.place(x=self.px(8), rely=0.5, anchor="w")
        self.search_hint.bind("<Button-1>", lambda e: self.search_entry.focus_set())
        self.search_var.trace_add("write", lambda *_: self._on_search_typing())
        self.search_entry.bind("<FocusIn>", lambda e: self._update_search_hint())
        self.search_entry.bind("<FocusOut>", lambda e: self._update_search_hint())
        self.search_entry.bind("<Escape>", lambda e: self.search_var.set(""))

        # --- tabella + dettagli (divisi da un separatore trascinabile) ------------------------
        self.paned = ttk.Panedwindow(area, orient="vertical")
        self.paned.grid(row=2, column=0, sticky="nsew")

        self.table_card = ttk.Frame(self.paned, style="Card.TFrame", padding=1)
        self.table_card.columnconfigure(0, weight=1)
        self.table_card.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(self.table_card, columns=tuple(COLUMNS), show="tree headings",
                                 selectmode="extended")
        self.tree.heading("#0", text=TREE_COLUMN[0], anchor="w", command=lambda: self._sort_by("#0"))
        self.tree.column("#0", width=self.px(TREE_COLUMN[1]), minwidth=self.px(160), stretch=True)
        for col, (title, width, anchor) in COLUMNS.items():
            self.tree.heading(col, text=title, anchor=anchor, command=lambda c=col: self._sort_by(c))
            self.tree.column(col, width=self.px(width), minwidth=self.px(min(width, 60)), anchor=anchor,
                             stretch=col in ("nuovo", "titolo", "artista", "album"))
        yscroll = ttk.Scrollbar(self.table_card, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(self.table_card, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=self._autohide(yscroll), xscrollcommand=self._autohide(xscroll))
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")

        self.tree.tag_configure("folder", font=self.fonts["bold"], background=P.surface_alt, foreground=P.text)
        self.tree.tag_configure("st_ok", foreground=P.muted)
        self.tree.tag_configure("st_warn", background=P.warn_soft, foreground=P.warn)
        self.tree.tag_configure("st_excluded", foreground=P.subtle, font=self.fonts["italic"])
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._update_details())
        self.tree.bind("<<TreeviewOpen>>", lambda e: self._on_folder_toggle(True))
        self.tree.bind("<<TreeviewClose>>", lambda e: self._on_folder_toggle(False))
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Return>", lambda e: self._edit_selected())
        self.tree.bind("<Delete>", lambda e: self._toggle_exclude())
        self.tree.bind("<Control-a>", lambda e: (self._select_all(), "break")[1])
        self.tree.bind("<Button-3>", self._on_right_click)

        # stato vuoto, sovrapposto alla tabella
        self.empty = ttk.Frame(self.table_card, style="Surface.TFrame")
        self.empty_inner = ttk.Frame(self.empty, style="Surface.TFrame")
        self.empty_inner.place(relx=0.5, rely=0.45, anchor="center")

        self.paned.add(self.table_card, weight=1)

        # il pannello dettagli compare solo quando c'e' un'anteprima da mostrare
        self.details_card = ttk.Frame(self.paned, style="Card.TFrame", padding=(self.px(16), self.px(12)))
        self._build_details(self.details_card)

        self._build_context_menu()

    @staticmethod
    def _autohide(scrollbar: ttk.Scrollbar):
        """Barra di scorrimento visibile solo quando il contenuto non entra."""
        def update(first, last):
            if float(first) <= 0.0 and float(last) >= 1.0:
                scrollbar.grid_remove()
            else:
                scrollbar.grid()
            scrollbar.set(first, last)
        return update

    def _set_details_visible(self, visible: bool):
        shown = str(self.details_card) in [str(p) for p in self.paned.panes()]
        if visible and not shown:
            self.paned.add(self.details_card, weight=0)
            self.after_idle(self._place_sash)
        elif not visible and shown:
            self.paned.forget(self.details_card)

    def _place_sash(self):
        """Altezza del pannello dettagli: quanto basta per il suo contenuto.
        Ridimensionando la finestra poi cambia solo l'altezza della tabella."""
        self.update_idletasks()
        total = self.paned.winfo_height()
        wanted = self.details_card.winfo_reqheight() + self.px(10)  # + spessore del divisore
        if total >= wanted + self.px(160):
            self.paned.sashpos(0, total - wanted)

    def _build_details(self, card: ttk.Frame):
        card.columnconfigure(0, weight=3)
        card.columnconfigure(1, weight=2)
        card.rowconfigure(1, weight=1)

        head = ttk.Frame(card, style="Surface.TFrame")
        head.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, self.px(8)))
        head.columnconfigure(0, weight=1)
        self.detail_title = ttk.Label(head, text="", style="DetailTitle.TLabel")
        self.detail_title.grid(row=0, column=0, sticky="w")
        actions = ttk.Frame(head, style="Surface.TFrame")
        actions.grid(row=0, column=1, sticky="e")
        self.edit_btn = ttk.Button(actions, text="Correggi…", style="Small.TButton", command=self._edit_selected)
        self.edit_btn.pack(side="left")
        Tooltip(self.edit_btn, "Correggi a mano titolo, artista, album, anno… (Invio o doppio clic)")
        self.exclude_btn = ttk.Button(actions, text="Escludi", style="Small.TButton", width=9,
                                      command=self._toggle_exclude)
        self.exclude_btn.pack(side="left", padx=(self.px(6), 0))
        Tooltip(self.exclude_btn, "I brani esclusi non vengono modificati (Canc)")
        self.reveal_btn = ttk.Button(actions, text="Apri cartella", style="Small.TButton", command=self._reveal_selected)
        self.reveal_btn.pack(side="left", padx=(self.px(6), 0))
        Tooltip(self.reveal_btn, "Mostra il file in Esplora risorse")

        self.compare = ttk.Treeview(card, columns=("campo", "attuale", "proposto", "fonte"), show="headings",
                                    height=7, selectmode="none", style="Compare.Treeview")
        for col, title, width in (("campo", "Campo", 110), ("attuale", "Attuale", 210),
                                  ("proposto", "Dopo l'applicazione", 230), ("fonte", "Fonte", 100)):
            self.compare.heading(col, text=title, anchor="w")
            self.compare.column(col, width=self.px(width), minwidth=self.px(60), anchor="w",
                                stretch=col in ("attuale", "proposto"))
        self.compare.tag_configure("diff", foreground=P.accent_pressed, font=self.fonts["bold"])
        self.compare.tag_configure("same", foreground=P.muted)
        self.compare.grid(row=1, column=0, sticky="nsew")

        self.info = tk.Text(card, wrap="word", height=8, width=40, relief="flat", borderwidth=0,
                            highlightthickness=0, background=P.surface, foreground=P.text,
                            font=self.fonts["base"], padx=0, pady=0, cursor="arrow")
        self.info.grid(row=1, column=1, sticky="nsew", padx=(self.px(20), 0))
        self.info.tag_configure("section", font=self.fonts["section"], foreground=P.muted,
                                spacing1=self.px(8), spacing3=self.px(2))
        self.info.tag_configure("first", spacing1=0)
        self.info.tag_configure("warn", foreground=P.warn)
        self.info.tag_configure("muted", foreground=P.muted)
        self.info.tag_configure("accent", foreground=P.accent_pressed)
        self.info.configure(state="disabled")

        # messaggio sovrapposto quando non c'e' selezione: il pannello mantiene
        # sempre la stessa altezza, cosi il divisore con la tabella non salta
        self.detail_placeholder = ttk.Label(card, text="Seleziona un brano o una cartella per vederne i dettagli.",
                                            style="CardMuted.TLabel", anchor="nw")

    def _build_context_menu(self):
        self.menu = tk.Menu(self, tearoff=False)
        self.menu.add_command(label="Correggi dati…", accelerator="Invio", command=self._edit_selected)
        self.menu.add_command(label="Escludi", accelerator="Canc", command=self._toggle_exclude)
        self.menu.add_command(label="Converti in un altro formato…", command=self._convert)
        self.menu.add_separator()
        self.menu.add_command(label="Mostra in Esplora risorse", command=self._reveal_selected)
        self.menu.add_command(label="Copia percorso", command=self._copy_path)
        self.menu.add_separator()
        self.menu.add_command(label="Espandi tutte le cartelle", command=lambda: self._set_all_open(True))
        self.menu.add_command(label="Comprimi tutte le cartelle", command=lambda: self._set_all_open(False))

    def _build_footer(self):
        bar = ttk.Frame(self, padding=(self.px(16), self.px(10), self.px(16), self.px(14)))
        bar.grid(row=3, column=0, sticky="ew")
        bar.columnconfigure(0, weight=1)
        self.status_var = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.status_var, style="Status.TLabel").grid(row=0, column=0, sticky="w")
        self.progress = ttk.Progressbar(bar, mode="determinate", length=self.px(220))
        self.progress.grid(row=0, column=1, padx=(self.px(12), self.px(12)))
        self.progress.grid_remove()
        self.stop_btn = ttk.Button(bar, text="Ferma", command=self._stop)
        self.stop_btn.grid(row=0, column=2, padx=(0, self.px(8)))
        self.stop_btn.grid_remove()
        Tooltip(self.stop_btn, "Interrompe l'operazione in corso (Esc)")
        self.convert_btn = ttk.Button(bar, text="Converti formato…", command=self._convert)
        self.convert_btn.grid(row=0, column=3, padx=(0, self.px(8)))
        Tooltip(self.convert_btn, "Crea una copia dei brani (selezionati o tutti) in MP3, AAC, Opus, OGG, "
                                  "FLAC, ALAC o WAV, con la qualità che preferisci. "
                                  "Gli originali non vengono modificati.")
        self.undo_btn = ttk.Button(bar, text="Annulla ultima modifica", command=self._undo)
        self.undo_btn.grid(row=0, column=4, padx=(0, self.px(8)))
        Tooltip(self.undo_btn, self._undo_tooltip)
        self.apply_btn = ttk.Button(bar, text="Applica modifiche", style="Accent.TButton", command=self._apply)
        self.apply_btn.grid(row=0, column=5)

    def _bind_shortcuts(self):
        self.bind("<Control-o>", lambda e: self._choose_folder())
        self.bind("<F5>", lambda e: self._analyze())
        self.bind("<Control-f>", lambda e: (self.search_entry.focus_set(), self.search_entry.select_range(0, "end")))
        self.bind("<Escape>", lambda e: self._stop() if self.busy_kind else None)
        self.bind("<F1>", lambda e: self._show_help())

    # ==========================================================================
    # configurazione persistente
    # ==========================================================================

    def _read_config(self) -> dict:
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _apply_config(self):
        self._loading = True  # i trace dei campi non devono risalvare la configurazione
        try:
            self._load_settings_from_config()
        finally:
            self._loading = False

    def _load_settings_from_config(self):
        cfg = self.config_data
        recent = [f for f in cfg.get("recent_folders", []) if isinstance(f, str)]
        if cfg.get("folder") and cfg["folder"] not in recent:
            recent.insert(0, cfg["folder"])
        self.folder_combo.configure(values=recent[:MAX_RECENT])
        self.folder_var.set(cfg.get("folder") or "")
        self.reorganize_var.set(bool(cfg.get("reorganize", False)))
        self.online_var.set(bool(cfg.get("online", True)))
        self.covers_var.set(bool(cfg.get("covers", False)))
        self.pattern_var.set(cfg.get("filename_pattern") or DEFAULT_FILENAME_PATTERN)
        self.folder_pattern_var.set(cfg.get("folder_pattern") or DEFAULT_FOLDER_PATTERN)
        self.folder_rename_var.set(bool(cfg.get("rename_folders", False)))
        convert = cfg.get("convert")
        self.convert_cfg = dict(convert) if isinstance(convert, dict) else {}
        self._update_examples()

        geometry = cfg.get("geometry")
        if isinstance(geometry, str) and geometry:
            self.geometry(geometry)
            if cfg.get("maximized"):
                self.state("zoomed")
        else:
            w, h = self.px(1360), self.px(940)
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            w, h = min(w, sw - self.px(40)), min(h, sh - self.px(80))
            self.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}")

    def _save_config(self):
        recent = list(self.folder_combo.cget("values") or ())
        data = {
            "folder": self.folder_var.get().strip(),
            "recent_folders": recent[:MAX_RECENT],
            "reorganize": self.reorganize_var.get(),
            "online": self.online_var.get(),
            "covers": self.covers_var.get(),
            "filename_pattern": self.pattern_var.get(),
            "folder_pattern": self.folder_pattern_var.get(),
            "rename_folders": self.folder_rename_var.get(),
            "convert": self.convert_cfg,
        }
        maximized = self.state() == "zoomed"
        data["maximized"] = maximized
        if not maximized and self.state() == "normal":
            data["geometry"] = self.geometry()
        else:
            data["geometry"] = self.config_data.get("geometry", "")
        self.config_data = data
        try:
            CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass

    def _remember_folder(self, folder: Path):
        text = str(folder)
        recent = [f for f in (self.folder_combo.cget("values") or ()) if os.path.normcase(f) != os.path.normcase(text)]
        self.folder_combo.configure(values=[text, *recent][:MAX_RECENT])

    # ==========================================================================
    # opzioni
    # ==========================================================================

    def _settings(self) -> dict:
        return {
            "reorganize": self.reorganize_var.get(),
            "online": self.online_var.get(),
            "download_covers": self.online_var.get() and self.covers_var.get(),
            "filename_pattern": self.pattern_var.get().strip() or DEFAULT_FILENAME_PATTERN,
            "folder_pattern": self.folder_pattern_var.get().strip() or DEFAULT_FOLDER_PATTERN,
            "rename_folders": self.folder_rename_var.get(),
        }

    def _pattern_errors(self) -> tuple[str | None, str | None]:
        file_err = pattern_error(self.pattern_var.get(), kind="file")
        folder_err = None
        if self.folder_rename_var.get() and not self.reorganize_var.get():
            folder_err = pattern_error(self.folder_pattern_var.get(), kind="folder")
        return file_err, folder_err

    def _update_examples(self):
        file_err = pattern_error(self.pattern_var.get(), kind="file")
        if file_err:
            self.filename_example.configure(text="⚠ " + file_err, style="CardError.TLabel")
            self.pattern_combo.configure(style="Invalid.TCombobox")
        else:
            example = expand_pattern(self.pattern_var.get(), track=1, artist="Artista", album="Album",
                                     title="Titolo", year=1999, disc=1, width=2)
            self.filename_example.configure(text=f"→  {example}.mp3", style="Example.TLabel")
            self.pattern_combo.configure(style="TCombobox")

        folder_err = pattern_error(self.folder_pattern_var.get(), kind="folder")
        if folder_err:
            self.folder_example.configure(text="⚠ " + folder_err, style="CardError.TLabel")
            self.folder_pattern_combo.configure(style="Invalid.TCombobox")
        else:
            example = expand_pattern(self.folder_pattern_var.get(), artist="Artista", album="Album",
                                     year=1999, disc=1, max_len=80)
            self.folder_example.configure(text=f"→  {example}\\", style="Example.TLabel")
            self.folder_pattern_combo.configure(style="TCombobox")

    def _on_pattern_typing(self):
        self._update_examples()
        if not getattr(self, "_loading", False):
            self._debounce("replan", 450, self._on_settings_change)

    def _on_settings_change(self, _event=None):
        self._update_examples()
        self._refresh_controls()
        self._save_config()
        plan = self.plan
        if plan is None or self.busy_kind is not None or any(self._pattern_errors()):
            return
        s = self._settings()
        unchanged = (s["filename_pattern"] == plan.filename_pattern
                     and s["folder_pattern"] == plan.folder_pattern
                     and s["reorganize"] == plan.reorganize
                     and (s["rename_folders"] and not s["reorganize"]) == plan.rename_folders)
        if not unchanged:
            self._replan()
            self._rebuild_tree()

    def _on_search_option_change(self):
        self._refresh_controls()
        self._save_config()
        self._update_notice()

    def _replan(self):
        """Ricalcola nomi e cartelle con le opzioni correnti, senza riscansionare nulla."""
        if self.plan is None:
            return
        s = self._settings()
        replan(self.plan, filename_pattern=s["filename_pattern"], folder_pattern=s["folder_pattern"],
               rename_folders=s["rename_folders"], reorganize=s["reorganize"])

    # ==========================================================================
    # azioni principali
    # ==========================================================================

    def _choose_folder(self):
        if self.busy_kind:
            return
        current = self.folder_var.get().strip()
        initial = current if current and Path(current).is_dir() else str(Path.home() / "Music")
        folder = filedialog.askdirectory(parent=self, initialdir=initial, title="Scegli la cartella della musica")
        if folder:
            self.folder_var.set(str(Path(folder)))
            self._update_notice()
            self.analyze_btn.focus_set()

    def _analyze(self, *, folder: Path | None = None):
        if self.busy_kind:
            return
        if folder is None:
            text = self.folder_var.get().strip().strip('"')
            if not text:
                messagebox.showinfo("Scegli una cartella", "Indica prima la cartella che contiene la musica.",
                                    parent=self)
                self._choose_folder()
                return
            folder = Path(text)
        if not folder.is_dir():
            messagebox.showwarning("Cartella non trovata",
                                   f"La cartella non esiste o non è raggiungibile:\n{folder}", parent=self)
            return
        file_err, folder_err = self._pattern_errors()
        if file_err or folder_err:
            messagebox.showwarning("Formato non valido",
                                   f"Correggi il formato dei nomi prima di analizzare:\n{file_err or folder_err}",
                                   parent=self)
            return
        if self.plan is None or os.path.normcase(str(self.plan.root)) != os.path.normcase(str(folder)):
            # cartella diversa: esclusioni e correzioni della precedente non valgono piu
            self.excluded_paths.clear()
            self.manual_by_path.clear()
            self.collapsed.clear()
        self._remember_folder(folder)
        self._save_config()
        settings = self._settings()
        self._start_worker("analyze", self._analyze_worker, folder, settings)

    def _stop(self):
        if self.busy_kind in ("analyze", "apply", "convert"):
            self.cancel_event.set()
            self.status_var.set("Interruzione in corso…")

    def _apply(self):
        if self.busy_kind or self.plan is None:
            return
        file_err, folder_err = self._pattern_errors()
        if file_err or folder_err:
            messagebox.showwarning("Formato non valido", file_err or folder_err, parent=self)
            return
        items = [it for it in self.plan.items if it.will_change]
        folders = self.plan.active_folder_renames
        if not items and not folders:
            messagebox.showinfo("Niente da fare", "Tutti i brani sono già a posto.", parent=self)
            return
        renames = sum(1 for it in items if it.renames)
        tags_only = sum(1 for it in items if not it.renames and (it.tag_changes or it.tags_to_clear))
        covers = sum(1 for it in items if it.cover_needed)
        excluded = sum(1 for it in self.plan.items if it.excluded and it.needs_change)
        lines = [f"Stai per modificare {_plural(len(items), 'brano', 'brani')}:", ""]
        if renames:
            lines.append(f"•  {_plural(renames, 'file rinominato o spostato', 'file rinominati o spostati')}")
        if tags_only:
            lines.append(f"•  {_plural(tags_only, 'file con solo i tag corretti', 'file con solo i tag corretti')}")
        if covers:
            lines.append(f"•  {_plural(covers, 'copertina da incorporare', 'copertine da incorporare')}")
        if folders:
            lines.append(f"•  {_plural(len(folders), 'cartella rinominata', 'cartelle rinominate')}")
        if excluded:
            lines.append(f"•  {_plural(excluded, 'brano escluso resta', 'brani esclusi restano')} com'è")
        lines += ["", "Potrai annullare tutto con «Annulla ultima modifica». Procedere?"]
        if not messagebox.askyesno("Applica modifiche", "\n".join(lines), parent=self):
            return
        self._start_worker("apply", self._apply_worker, self.plan)

    def _undo(self):
        if self.busy_kind:
            return
        info = journal_info()
        if not info:
            self._refresh_controls()
            return
        parts = [f"{_plural(info['files'], 'file', 'file')}"]
        if info["folders"]:
            parts.append(_plural(info["folders"], "cartella", "cartelle"))
        msg = (f"Annullare l'operazione del {info['timestamp']}?\n\n"
               f"{' e '.join(parts)} in «{info['root']}» torneranno esattamente com'erano "
               f"(nomi, posizione e tag).")
        if not messagebox.askyesno("Annulla ultima modifica", msg, parent=self):
            return
        self._start_worker("undo", self._undo_worker)

    def _undo_tooltip(self) -> str:
        info = journal_info()
        if not info:
            return "Nessuna operazione da annullare."
        text = f"Ultima operazione: {info['timestamp']}\n{_plural(info['files'], 'file', 'file')} in {info['root']}"
        if info["folders"]:
            text += f"\n{_plural(info['folders'], 'cartella rinominata', 'cartelle rinominate')}"
        return text

    def _convert(self):
        """Apre la finestra di conversione per i brani selezionati o tutta la cartella."""
        if self.busy_kind:
            return
        if self.plan is not None:
            root = self.plan.root
            all_files: list[Path] | None = [it.path for it in self.plan.items]
            selected = [it.path for it in self._selected_items()]
            if not all_files:
                messagebox.showinfo("Niente da convertire", "La cartella non contiene file audio.", parent=self)
                return
        else:
            text = self.folder_var.get().strip().strip('"')
            if not text or not Path(text).is_dir():
                messagebox.showinfo("Scegli una cartella",
                                    "Indica prima la cartella che contiene la musica da convertire.", parent=self)
                if not text:
                    self._choose_folder()
                return
            root, all_files, selected = Path(text), None, []

        ffmpeg = converter.find_ffmpeg(_app_dir())
        if ffmpeg is None:
            self._ffmpeg_missing()
            return
        formats = converter.supported_formats(ffmpeg)
        if not formats:
            messagebox.showerror("FFmpeg non utilizzabile",
                                 f"La versione di FFmpeg trovata non sa creare nessuno dei formati "
                                 f"supportati:\n{ffmpeg}", parent=self)
            return
        ConvertDialog(self, px=self.px, root=root, all_files=all_files, selected_files=selected,
                      formats=formats, config=self.convert_cfg, pending_changes=self._pending_count(),
                      on_start=lambda request: self._start_convert(request, ffmpeg))

    def _ffmpeg_missing(self):
        msg = ("Per convertire i file audio serve FFmpeg, un programma gratuito che non è installato "
               "su questo computer.\n\n"
               "Il modo più semplice: apri il Prompt dei comandi, scrivi\n\n"
               "      winget install Gyan.FFmpeg\n\n"
               "e riapri Music Organizer. In alternativa puoi copiare ffmpeg.exe nella cartella "
               "di Music Organizer.\n\n"
               "Vuoi aprire la pagina di download di FFmpeg?")
        if messagebox.askyesno("Serve FFmpeg", msg, parent=self):
            webbrowser.open(converter.FFMPEG_DOWNLOAD_URL)

    def _start_convert(self, request: ConvertRequest, ffmpeg: str):
        self._save_config()  # ricorda formato, qualita e destinazione scelti
        self._start_worker("convert", self._convert_worker, request, ffmpeg)

    def _show_help(self):
        HelpDialog(self, px=self.px, fonts=self.fonts)

    def _on_close(self):
        if self.busy_kind:
            if self.busy_kind in ("apply", "undo"):
                if not messagebox.askyesno(
                        "Operazione in corso",
                        "Sto ancora modificando i file. Vuoi interrompere e chiudere?\n\n"
                        "Chiudo appena il file corrente è completato: le modifiche già fatte "
                        "restano annullabili.", parent=self):
                    return
            elif self.busy_kind == "convert":
                if not messagebox.askyesno(
                        "Conversione in corso",
                        "Vuoi interrompere la conversione e chiudere?\n\n"
                        "I brani già convertiti restano; quelli in corso vengono scartati.", parent=self):
                    return
            self._closing = True
            self.cancel_event.set()
            self.status_var.set("Chiusura in corso…")
            return
        self._save_config()
        self.destroy()

    def report_callback_exception(self, exc, val, tb):
        text = "".join(traceback.format_exception(exc, val, tb))
        _log_exception("Errore nell'interfaccia", text)
        messagebox.showerror("Errore imprevisto", f"{val}\n\nDettagli salvati in:\n{LOG_PATH}", parent=self)

    # ==========================================================================
    # lavoro in background
    # ==========================================================================

    def _start_worker(self, kind: str, target, *args):
        if self.worker is not None and self.worker.is_alive():
            return
        self.cancel_event = threading.Event()
        self.busy_kind = kind
        self._refresh_controls()
        self.progress.configure(mode="indeterminate", value=0)
        self.progress.start(12)
        self.status_var.set({"analyze": "Analisi in corso…", "apply": "Applico le modifiche…",
                             "undo": "Annullamento in corso…", "convert": "Preparo la conversione…"}[kind])

        def run():
            try:
                target(*args)
            except Exception as exc:  # rete di sicurezza: l'interfaccia non deve restare bloccata
                _log_exception(f"Errore ({kind})", traceback.format_exc())
                self.queue.put(("error", kind, str(exc) or type(exc).__name__))

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def _progress_callback(self, done, total, message):
        self.queue.put(("progress", done, total, message))

    def _analyze_worker(self, folder: Path, settings: dict):
        try:
            plan = analyze(folder, progress=self._progress_callback, cancel_event=self.cancel_event, **settings)
            self.queue.put(("plan", plan, settings, folder))
        except CancelledError:
            self.queue.put(("cancelled",))

    def _apply_worker(self, plan: Plan):
        result = applier.apply(plan, progress=self._progress_callback, cancel_event=self.cancel_event)
        self.queue.put(("applied", result))

    def _undo_worker(self):
        result = applier.undo(progress=self._progress_callback)
        self.queue.put(("undone", result))

    def _convert_worker(self, request: ConvertRequest, ffmpeg: str):
        files = request.files
        if files is None:
            self._progress_callback(0, 0, "Cerco i file audio…")
            files = scan_audio(request.root)
        jobs = converter.plan_jobs(files, request.root, request.fmt, request.output_dir,
                                   overwrite=request.overwrite)
        result = converter.convert(jobs, request.fmt, request.quality, ffmpeg=ffmpeg,
                                   output_dir=request.output_dir, copy_compatible=request.copy_compatible,
                                   progress=self._progress_callback, cancel_event=self.cancel_event)
        self.queue.put(("converted", result, request))

    def _poll_queue(self):
        try:
            while True:
                msg = self.queue.get_nowait()
                self._handle_message(msg)
        except queue.Empty:
            pass
        if self._closing and (self.worker is None or not self.worker.is_alive()):
            self._save_config()
            self.destroy()
            return
        self.after(80, self._poll_queue)

    def _finish_worker(self):
        if self.worker is not None:
            self.worker.join(timeout=2)
        self.busy_kind = None
        self.progress.stop()
        self._refresh_controls()

    def _handle_message(self, msg):
        kind = msg[0]
        if kind == "progress":
            _, done, total, message = msg
            if total:
                if str(self.progress.cget("mode")) != "determinate":
                    self.progress.stop()
                    self.progress.configure(mode="determinate")
                self.progress.configure(maximum=max(total, 1), value=done)
                self.status_var.set(f"{message}  ({done}/{total})")
            else:
                self.status_var.set(message)
            return

        self._finish_worker()
        if self._closing:
            return
        if kind == "plan":
            _, plan, settings, folder = msg
            self._show_plan(plan, settings, folder)
        elif kind == "cancelled":
            self.status_var.set("Analisi interrotta.")
            if self.plan is None:
                self._show_empty_state()
        elif kind == "applied":
            self._on_applied(msg[1])
        elif kind == "undone":
            self._on_undone(msg[1])
        elif kind == "converted":
            self._on_converted(msg[1], msg[2])
        elif kind == "error":
            _, what, text = msg
            title = {"analyze": "Errore durante l'analisi", "apply": "Errore durante l'applicazione",
                     "undo": "Errore durante l'annullamento",
                     "convert": "Errore durante la conversione"}.get(what, "Errore")
            self.status_var.set(title + ".")
            messagebox.showerror(title, f"{text}\n\nDettagli salvati in:\n{LOG_PATH}", parent=self)

    def _on_applied(self, result: ApplyResult):
        self.manual_by_path.clear()
        lines = []
        if result.cancelled:
            lines.append("Operazione interrotta: è stata applicata solo una parte delle modifiche.")
            lines.append("")
        lines.append(f"•  {_plural(result.files_changed, 'brano modificato', 'brani modificati')}")
        if result.folders_renamed:
            lines.append(f"•  {_plural(result.folders_renamed, 'cartella rinominata', 'cartelle rinominate')}")
        if result.covers_saved:
            lines.append(f"•  {_plural(result.covers_saved, 'copertina scaricata', 'copertine scaricate')}")
        if result.errors:
            lines += ["", f"{_plural(len(result.errors), 'problema', 'problemi')}:"]
            lines += [f"   {e}" for e in result.errors[:8]]
            if len(result.errors) > 8:
                lines.append(f"   …e altri {len(result.errors) - 8}")
        lines += ["", "Puoi annullare tutto con «Annulla ultima modifica»."]
        show = messagebox.showwarning if (result.errors or result.cancelled) else messagebox.showinfo
        show("Modifiche applicate" if not result.cancelled else "Operazione interrotta", "\n".join(lines), parent=self)
        if self.plan is not None:
            self._analyze(folder=self.plan.root)

    def _on_undone(self, result: UndoResult):
        lines = [f"Ripristinati {_plural(result.restored, 'file', 'file')} allo stato originale."]
        if result.folders_restored:
            lines.append(f"Ripristinate {_plural(result.folders_restored, 'cartella', 'cartelle')}.")
        if result.errors:
            lines += ["", f"{_plural(len(result.errors), 'problema', 'problemi')}:"]
            lines += [f"   {e}" for e in result.errors[:8]]
            if len(result.errors) > 8:
                lines.append(f"   …e altri {len(result.errors) - 8}")
            lines += ["", "Il diario è stato conservato: puoi riprovare dopo aver risolto."]
        show = messagebox.showwarning if result.errors else messagebox.showinfo
        show("Modifiche annullate", "\n".join(lines), parent=self)
        if self.plan is not None:
            self._analyze(folder=self.plan.root)
        else:
            self.status_var.set("Modifiche annullate.")

    def _on_converted(self, result: ConvertResult, request: ConvertRequest):
        lines = []
        if result.cancelled:
            lines += ["Conversione interrotta: i brani già completati restano, quelli in corso "
                      "sono stati scartati.", ""]
        lines += [f"{request.fmt.label}, {request.quality.label}", ""]
        if result.converted:
            lines.append(f"•  {_plural(result.converted, 'brano convertito', 'brani convertiti')}")
        if result.copied:
            lines.append(f"•  {_plural(result.copied, 'brano copiato', 'brani copiati')} senza ricodifica "
                         f"(già in {request.fmt.short})")
        if result.skipped:
            lines.append(f"•  {_plural(len(result.skipped), 'brano saltato', 'brani saltati')}")
            if not result.created:
                lines += [f"   {s}" for s in result.skipped[:5]]
                if len(result.skipped) > 5:
                    lines.append(f"   …e altri {len(result.skipped) - 5}")
        if not (result.created or result.skipped or result.errors):
            lines.append("•  nessun brano convertito")
        if result.created and result.output_bytes:
            lines.append(f"•  spazio occupato: {format_size(result.output_bytes)} "
                         f"(originali: {format_size(result.source_bytes)})")
        if result.errors:
            lines += ["", f"{_plural(len(result.errors), 'problema', 'problemi')}:"]
            lines += [f"   {e}" for e in result.errors[:8]]
            if len(result.errors) > 8:
                lines.append(f"   …e altri {len(result.errors) - 8}")

        title = "Conversione interrotta" if result.cancelled else "Conversione completata"
        self.status_var.set(f"{title}: {_plural(result.created, 'brano creato', 'brani creati')}.")
        if request.output_dir is not None and result.created:
            lines += ["", f"I file sono in «{request.output_dir}».", "", "Vuoi aprire la cartella?"]
            if messagebox.askyesno(title, "\n".join(lines), parent=self,
                                   icon="warning" if result.errors or result.cancelled else "info"):
                try:
                    subprocess.Popen(["explorer", str(request.output_dir)])
                except OSError as exc:
                    messagebox.showerror("Impossibile aprire la cartella", str(exc), parent=self)
        else:
            show = messagebox.showwarning if (result.errors or result.cancelled) else messagebox.showinfo
            show(title, "\n".join(lines), parent=self)
        # i nuovi file accanto agli originali fanno ora parte della libreria analizzata
        if (request.output_dir is None and result.created and self.plan is not None
                and os.path.normcase(str(self.plan.root)) == os.path.normcase(str(request.root))):
            self._analyze(folder=self.plan.root)

    # ==========================================================================
    # stato dei controlli
    # ==========================================================================

    def _refresh_controls(self):
        busy = self.busy_kind is not None
        idle = ["!disabled"] if not busy else ["disabled"]
        for widget in (self.folder_combo, self.browse_btn, self.online_cb, self.pattern_combo,
                       self.reorganize_cb, self.convert_btn):
            widget.state(idle)
        self.covers_cb.state(["disabled"] if busy or not self.online_var.get() else ["!disabled"])
        reorganize = self.reorganize_var.get()
        self.folder_rename_cb.state(["disabled"] if busy or reorganize else ["!disabled"])
        self.folder_pattern_combo.state(
            ["disabled"] if busy or reorganize or not self.folder_rename_var.get() else ["!disabled"])

        # Analizza e' l'azione principale finche non c'e' un'anteprima, poi lo diventa Applica
        has_plan = self.plan is not None
        self.analyze_btn.state(idle)
        self.analyze_btn.configure(text="Rianalizza" if has_plan else "Analizza",
                                   style="TButton" if has_plan else "Accent.TButton")
        pending = self._pending_count()
        patterns_ok = not any(self._pattern_errors())
        self.apply_btn.state(["!disabled"] if not busy and pending and patterns_ok else ["disabled"])
        self.apply_btn.configure(text=f"Applica {pending} modifiche" if pending > 1 else
                                 ("Applica 1 modifica" if pending == 1 else "Applica modifiche"))
        self.undo_btn.state(["!disabled"] if not busy and applier.JOURNAL_PATH.exists() else ["disabled"])

        if busy:
            self.progress.grid()
            if self.busy_kind in ("analyze", "apply", "convert"):
                self.stop_btn.grid()
        else:
            self.progress.grid_remove()
            self.stop_btn.grid_remove()
        self._update_detail_buttons()

    def _pending_count(self) -> int:
        if self.plan is None:
            return 0
        return sum(1 for it in self.plan.items if it.will_change) + len(self.plan.active_folder_renames)

    def _update_notice(self):
        if self.plan is None and self.busy_kind is None:
            self._show_empty_state()  # il messaggio iniziale dipende dalla cartella scelta
        messages = []
        if self.plan is not None and self.busy_kind is None:
            text = self.folder_var.get().strip().strip('"')
            if text and os.path.normcase(text) != os.path.normcase(str(self.plan.root)):
                messages.append("Hai scelto un'altra cartella: l'anteprima mostra ancora quella precedente.")
                self.notice_btn.configure(text="Analizza la nuova cartella")
            elif self.analysis_settings and (
                    self.analysis_settings["online"] != self.online_var.get()
                    or self.analysis_settings["download_covers"] != (self.online_var.get() and self.covers_var.get())):
                messages.append("Le opzioni di ricerca online sono cambiate: rianalizza per applicarle.")
                self.notice_btn.configure(text="Rianalizza")
            show_button = bool(messages)
            messages += self.plan.notices
        else:
            show_button = False
        if messages:
            self.notice_label.configure(text="\n".join(messages))
            if show_button:
                self.notice_btn.grid()
            else:
                self.notice_btn.grid_remove()
            self.notice.grid(row=0, column=0, sticky="ew", pady=(0, self.px(10)))
        else:
            self.notice.grid_remove()

    def _on_search_typing(self):
        self._update_search_hint()
        self._debounce("search", 250, self._rebuild_tree)

    def _update_search_hint(self):
        if self.search_var.get() or self.focus_get() is self.search_entry:
            self.search_hint.place_forget()
        else:
            self.search_hint.place(x=self.px(8), rely=0.5, anchor="w")

    def _debounce(self, key: str, delay: int, func):
        if key in self._after_ids:
            self.after_cancel(self._after_ids[key])
        self._after_ids[key] = self.after(delay, lambda: (self._after_ids.pop(key, None), func()))

    # ==========================================================================
    # tabella
    # ==========================================================================

    @staticmethod
    def _status(item: PlanItem) -> str:
        if item.excluded:
            return "excluded"
        if item.warnings:
            return "warn"
        if item.needs_change:
            return "change"
        return "ok"

    def _show_plan(self, plan: Plan, settings: dict, folder: Path):
        # riapplica esclusioni e correzioni manuali fatte prima della nuova analisi
        for item in plan.items:
            key = str(item.path)
            item.excluded = key in self.excluded_paths
            item.manual = dict(self.manual_by_path.get(key, {}))
        self.plan = plan
        self.analysis_settings = {"online": settings["online"], "download_covers": settings["download_covers"]}
        self._replan()

        groups: dict[Path, list[int]] = {}
        for index, item in enumerate(plan.items):
            groups.setdefault(item.path.parent, []).append(index)
        self.groups = sorted(groups.items(), key=lambda kv: self._rel(kv[0]).casefold())
        self._rebuild_tree(keep_view=False)
        self._refresh_controls()
        self._update_notice()
        children = self.tree.get_children()
        if children:
            self.tree.focus(children[0])

    def _rel(self, path: Path) -> str:
        if self.plan is None:
            return str(path)
        try:
            rel = path.relative_to(self.plan.root)
        except ValueError:
            return str(path)
        return str(rel) if str(rel) != "." else "(cartella principale)"

    def _matches(self, item: PlanItem, query: str) -> bool:
        filt = self.filter_var.get()
        if filt != "all" and self._status(item) != filt:
            return False
        if not query:
            return True
        haystack = " ".join(str(v) for v in (
            item.path.name, item.new_name, self._rel(item.path.parent),
            item.components.get("title"), item.components.get("artist"),
            item.components.get("album"), item.components.get("albumartist"),
        ) if v).casefold()
        return all(word in haystack for word in query.split())

    def _sort_key(self, index: int):
        item = self.plan.items[index]
        col = self.sort_column
        comp = item.components
        if col == "#0":
            return item.path.name.casefold()
        if col == "stato":
            return STATUS_ORDER[self._status(item)]
        if col == "nuovo":
            return (item.new_name or "").casefold()
        if col in ("titolo", "artista", "album"):
            key = {"titolo": "title", "artista": "artist", "album": "album"}[col]
            return str(comp.get(key) or "￿").casefold()
        if col == "traccia":
            return comp.get("track") or 10**6
        if col == "anno":
            return comp.get("year") or 10**6
        return index

    def _sort_by(self, col: str):
        if self.sort_column == col:
            if self.sort_desc:
                self.sort_column, self.sort_desc = None, False  # terzo clic: ordine originale
            else:
                self.sort_desc = True
        else:
            self.sort_column, self.sort_desc = col, False
        for c in ("#0", *COLUMNS):
            title = TREE_COLUMN[0] if c == "#0" else COLUMNS[c][0]
            if c == self.sort_column:
                title += "  ▼" if self.sort_desc else "  ▲"
            self.tree.heading(c, text=title)
        self._rebuild_tree()

    def _counts(self) -> dict[str, int]:
        counts = {key: 0 for key, _ in FILTERS}
        if self.plan is None:
            return counts
        counts["all"] = len(self.plan.items)
        for item in self.plan.items:
            counts[self._status(item)] += 1
        return counts

    def _update_counts(self):
        counts = self._counts()
        for key, label in FILTERS:
            self.chips[key].configure(text=f"{label}  {counts[key]}" if self.plan is not None else label)
            self.chips[key].state(["disabled"] if self.plan is None else ["!disabled"])
        self._update_summary(counts)

    def _update_summary(self, counts: dict[str, int] | None = None):
        if self.plan is None or self.busy_kind:
            return
        counts = counts or self._counts()
        if not self.plan.items:
            self.status_var.set("Nessun file audio trovato nella cartella.")
            return
        parts = [f"{_plural(counts['all'], 'brano', 'brani')} in {_plural(len(self.groups), 'cartella', 'cartelle')}"]
        pending = sum(1 for it in self.plan.items if it.will_change)
        parts.append(f"{pending} da modificare")
        if counts["warn"]:
            parts.append(f"{counts['warn']} da controllare")
        folders = len(self.plan.active_folder_renames)
        if folders:
            parts.append(_plural(folders, "cartella da rinominare", "cartelle da rinominare"))
        if counts["excluded"]:
            parts.append(_plural(counts["excluded"], "escluso", "esclusi"))
        self.status_var.set("  ·  ".join(parts))

    def _file_row(self, index: int) -> dict:
        item = self.plan.items[index]
        comp = item.components
        status = self._status(item)
        if item.renames:
            if item.new_dir is not None and item.new_dir != item.path.parent:
                new = self._rel(item.new_path)
            else:
                new = item.new_name
        else:
            new = "= invariato"
        values = (
            STATUS_LABELS[status],
            new,
            comp.get("title") or "",
            comp.get("artist") or "",
            comp.get("album") or "",
            comp.get("track") or "",
            comp.get("year") or "",
        )
        tags = (f"st_{status}",) if status != "change" else ()
        return {"text": item.path.name, "values": values, "tags": tags, "image": self.icons[f"st_{status}"]}

    def _folder_rows_info(self) -> dict[Path, FolderRename]:
        return {fr.folder: fr for fr in (self.plan.active_folder_renames if self.plan else [])}

    def _folder_row(self, group: int, renames: dict[Path, FolderRename]) -> dict:
        folder, indices = self.groups[group]
        items = [self.plan.items[i] for i in indices]
        rename = renames.get(folder)
        new = f"→  {rename.new_name}" if rename else ""
        albums = {it.components.get("album") for it in items if it.components.get("album")}
        artists = {it.components.get("albumartist") or it.components.get("artist") for it in items} - {None}
        values = (_plural(len(items), "brano", "brani"), new, "",
                  artists.pop() if len(artists) == 1 else ("Vari artisti" if artists else ""),
                  albums.pop() if len(albums) == 1 else "", "", "")
        return {"text": self._rel(folder), "values": values, "tags": ("folder",), "image": self.icons["folder"]}

    def _rebuild_tree(self, keep_view: bool = True):
        if self.plan is None:
            return
        selection = set(self.tree.selection())
        focus = self.tree.focus()
        top = self.tree.yview()[0] if keep_view else 0.0
        self.tree.delete(*self.tree.get_children())

        query = self.search_var.get().strip().casefold()
        filt = self.filter_var.get()
        renames = self._folder_rows_info()
        shown = 0
        for group, (folder, indices) in enumerate(self.groups):
            visible = [i for i in indices if self._matches(self.plan.items[i], query)]
            folder_match = folder in renames and filt in ("all", "change") and (
                not query or query in self._rel(folder).casefold())
            if not visible and not folder_match:
                continue
            if self.sort_column:
                visible.sort(key=self._sort_key, reverse=self.sort_desc)
            fid = f"d{group}"
            self.tree.insert("", "end", iid=fid, open=str(folder) not in self.collapsed,
                             **self._folder_row(group, renames))
            for index in visible:
                self.tree.insert(fid, "end", iid=f"f{index}", **self._file_row(index))
            shown += len(visible)

        keep = [iid for iid in selection if self.tree.exists(iid)]
        if keep:
            self.tree.selection_set(keep)
        if focus and self.tree.exists(focus):
            self.tree.focus(focus)
        self.tree.update_idletasks()
        self.tree.yview_moveto(top)

        self._update_counts()
        self._set_details_visible(bool(self.plan.items))
        if not self.plan.items:
            self._show_empty_state("Nessun file audio trovato",
                                   "La cartella non contiene file MP3, FLAC, M4A, OGG, OPUS, WMA o WAV.")
        elif not self.tree.get_children():
            self._show_empty_state("Nessun brano corrisponde",
                                   "Cambia filtro o testo di ricerca per vedere altri brani.")
        else:
            self.empty.place_forget()
        self._update_details()

    def _update_rows(self):
        """Aggiorna i valori delle righe esistenti (dopo correzioni o esclusioni)."""
        renames = self._folder_rows_info()
        for fid in self.tree.get_children():
            self.tree.item(fid, **self._folder_row(int(fid[1:]), renames))
            for iid in self.tree.get_children(fid):
                self.tree.item(iid, **self._file_row(int(iid[1:])))
        self._update_counts()
        self._refresh_controls()
        self._update_details()

    def _show_empty_state(self, title: str | None = None, message: str | None = None):
        for child in self.empty_inner.winfo_children():
            child.destroy()
        inner = self.empty_inner
        ttk.Label(inner, text="♫", style="EmptyIcon.TLabel").pack()
        if title is None:
            ttk.Label(inner, text="Organizza la tua libreria musicale", style="EmptyTitle.TLabel").pack(
                pady=(self.px(4), self.px(14)))
            steps = ttk.Frame(inner, style="Surface.TFrame")
            steps.pack()
            for n, text in enumerate((
                    "Scegli la cartella della musica",
                    "Premi Analizza: nessun file viene toccato, vedi solo l'anteprima",
                    "Controlla, correggi se serve e premi Applica"), start=1):
                ttk.Label(steps, text=str(n), style="Step.TLabel", width=3).grid(
                    row=n, column=0, sticky="w", pady=self.px(3), ipady=self.px(2))
                ttk.Label(steps, text=text, style="Card.TLabel").grid(row=n, column=1, sticky="w",
                                                                      padx=(self.px(10), 0))
            folder = self.folder_var.get().strip().strip('"')
            if folder and Path(folder).is_dir():
                # cartella gia scelta: l'azione principale e' il pulsante Analizza in alto
                ttk.Label(inner, text=f"Premi Analizza (F5) per vedere l'anteprima di «{folder}».",
                          style="CardMuted.TLabel").pack(pady=(self.px(18), 0))
            else:
                ttk.Button(inner, text="Scegli la cartella…", style="Accent.TButton",
                           command=self._choose_folder).pack(pady=(self.px(18), 0))
            self._set_details_visible(False)
        else:
            ttk.Label(inner, text=title, style="EmptyTitle.TLabel").pack(pady=(self.px(4), self.px(4)))
            ttk.Label(inner, text=message or "", style="CardMuted.TLabel").pack()
        self.empty.place(x=1, y=1, relwidth=1, relheight=1, width=-2, height=-2)
        self.empty.lift()
        self._update_counts()
        self._update_details()

    def _on_folder_toggle(self, opened: bool):
        iid = self.tree.focus()
        if iid.startswith("d") and self.plan is not None:
            folder = str(self.groups[int(iid[1:])][0])
            if opened:
                self.collapsed.discard(folder)
            else:
                self.collapsed.add(folder)

    def _set_all_open(self, opened: bool):
        for fid in self.tree.get_children():
            self.tree.item(fid, open=opened)
            folder = str(self.groups[int(fid[1:])][0])
            if opened:
                self.collapsed.discard(folder)
            else:
                self.collapsed.add(folder)

    def _select_all(self):
        rows = [iid for fid in self.tree.get_children() for iid in self.tree.get_children(fid)]
        self.tree.selection_set(rows)

    def _on_double_click(self, event):
        region = self.tree.identify_region(event.x, event.y)
        iid = self.tree.identify_row(event.y)
        if region in ("heading", "separator") or not iid:
            return None
        if iid.startswith("f"):
            self.tree.selection_set(iid)
            self._edit_selected()
            return "break"
        return None  # cartella: doppio clic apre/chiude il gruppo

    def _on_right_click(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid or self.plan is None:
            return
        if iid not in self.tree.selection():
            self.tree.selection_set(iid)
            self.tree.focus(iid)
        items = self._selected_items()
        all_excluded = bool(items) and all(it.excluded for it in items)
        self.menu.entryconfigure(1, label="Includi di nuovo" if all_excluded else "Escludi")
        self.menu.tk_popup(event.x_root, event.y_root)

    # ==========================================================================
    # selezione, dettagli e azioni sui brani
    # ==========================================================================

    def _selected_indices(self) -> list[int]:
        if self.plan is None:
            return []
        seen: dict[int, None] = {}
        for iid in self.tree.selection():
            if iid.startswith("f"):
                seen[int(iid[1:])] = None
            elif iid.startswith("d"):
                for index in self.groups[int(iid[1:])][1]:
                    seen[index] = None
        return list(seen)

    def _selected_items(self) -> list[PlanItem]:
        return [self.plan.items[i] for i in self._selected_indices()] if self.plan else []

    def _edit_selected(self):
        items = self._selected_items()
        if not items or self.busy_kind:
            return
        EditDialog(self, items, px=self.px, on_save=self._after_items_changed, preview=self._preview_name)

    def _preview_name(self, item: PlanItem, values: dict) -> str:
        if self.plan is None or not values.get("title"):
            return "(titolo mancante: il file non verrà rinominato)"
        name = expand_pattern(self.plan.filename_pattern, track=values.get("track"), artist=values.get("artist"),
                              album=values.get("album"), title=values.get("title"), year=values.get("year"),
                              disc=item.components.get("disc"), width=max(2, len(str(values.get("track") or ""))))
        return name + item.path.suffix.lower()

    def _after_items_changed(self, items: list[PlanItem]):
        for item in items:
            key = str(item.path)
            if item.manual:
                self.manual_by_path[key] = dict(item.manual)
            else:
                self.manual_by_path.pop(key, None)
        self._replan()
        self._update_rows()

    def _toggle_exclude(self):
        items = self._selected_items()
        if not items or self.busy_kind:
            return
        exclude = not all(it.excluded for it in items)
        for item in items:
            item.excluded = exclude
            if exclude:
                self.excluded_paths.add(str(item.path))
            else:
                self.excluded_paths.discard(str(item.path))
        self._replan()  # le esclusioni cambiano gli avvisi sui nomi doppi e le rinomine cartella
        self._update_rows()

    def _reveal_selected(self):
        selection = self.tree.selection()
        if not selection or self.plan is None:
            return
        iid = selection[0]
        if iid.startswith("d"):
            target = self.groups[int(iid[1:])][0]
            args = ["explorer", str(target)]
        else:
            target = self.plan.items[int(iid[1:])].path
            args = ["explorer", "/select,", str(target)]
        try:
            subprocess.Popen(args)
        except OSError as exc:
            messagebox.showerror("Impossibile aprire la cartella", str(exc), parent=self)

    def _copy_path(self):
        selection = self.tree.selection()
        if not selection or self.plan is None:
            return
        paths = []
        for iid in selection:
            paths.append(str(self.groups[int(iid[1:])][0]) if iid.startswith("d")
                         else str(self.plan.items[int(iid[1:])].path))
        self.clipboard_clear()
        self.clipboard_append("\n".join(paths))
        self.status_var.set("Percorso copiato negli appunti.")

    def _update_detail_buttons(self):
        items = self._selected_items()
        enabled = bool(items) and self.busy_kind is None
        for btn in (self.edit_btn, self.exclude_btn):
            btn.state(["!disabled"] if enabled else ["disabled"])
        self.reveal_btn.state(["!disabled"] if self.tree.selection() else ["disabled"])
        all_excluded = bool(items) and all(it.excluded for it in items)
        self.exclude_btn.configure(text="Includi" if all_excluded else "Escludi")

    def _set_info(self, parts: list[tuple[str, str]]):
        self.info.configure(state="normal")
        self.info.delete("1.0", "end")
        for i, (text, tag) in enumerate(parts):
            tags = (tag, "first") if i == 0 and tag == "section" else (tag,)
            self.info.insert("end", text, tags)
        self.info.configure(state="disabled")

    def _update_details(self):
        self._update_detail_buttons()
        selection = self.tree.selection()
        self.compare.delete(*self.compare.get_children())
        if self.plan is None or not selection:
            self.detail_title.configure(text="Dettagli")
            self._set_info([])
            self.detail_placeholder.place(in_=self.compare, x=0, y=0, relwidth=1, relheight=1)
            self.detail_placeholder.lift()
            return
        self.detail_placeholder.place_forget()

        if len(selection) == 1 and selection[0].startswith("d"):
            self._show_folder_details(int(selection[0][1:]))
            return
        items = self._selected_items()
        if len(items) == 1:
            self._show_item_details(items[0])
        else:
            self._show_multi_details(items)

    def _fill_compare(self, rows):
        for label, current, proposed, source, changed in rows:
            self.compare.insert("", "end", values=(label, current, proposed, source),
                                tags=("diff",) if changed else ("same",))

    def _show_item_details(self, item: PlanItem):
        self.detail_title.configure(text=item.path.name)
        rows = []
        clear = set(item.tags_to_clear)
        changes = item.tag_changes
        for key, label in COMPARE_FIELDS:
            current = item.original.get(key)
            if key in clear:
                proposed, changed = "(cancellato)", True
            elif item.proposed.get(key) is not None:
                proposed, changed = item.proposed[key], key in changes
            else:
                proposed, changed = current, False
            source = SOURCE_LABELS.get(item.sources.get(key, ""), "") if changed else ""
            rows.append((label, "—" if current in (None, "") else current,
                         "—" if proposed in (None, "") else proposed, source or "", changed))
        self._fill_compare(rows)

        parts: list[tuple[str, str]] = [("POSIZIONE ATTUALE\n", "section"), (self._rel(item.path) + "\n", "")]
        parts.append(("DOPO L'APPLICAZIONE\n", "section"))
        if item.excluded:
            parts.append(("Escluso: il file non verrà modificato.\n", "muted"))
        elif item.renames:
            parts.append((self._rel(item.new_path) + "\n", "accent"))
        elif item.needs_change:
            parts.append(("Il nome resta uguale; cambiano solo i tag.\n", "muted"))
        else:
            parts.append(("Nessuna modifica: il file è già a posto.\n", "muted"))
        if item.warnings:
            parts.append(("DA CONTROLLARE\n", "section"))
            parts += [("⚠  " + w + "\n", "warn") for w in item.warnings]
        notes = []
        if item.cover_needed:
            notes.append("La copertina dell'album verrà scaricata e incorporata.")
        if item.confidence is not None:
            source = {"deezer": "Deezer", "musicbrainz": "MusicBrainz"}.get(item.lookup_source or "", "internet")
            notes.append(f"Dati completati da {source}, affidabilità {round(item.confidence * 100)}%.")
        if item.manual:
            names = dict(COMPARE_FIELDS)
            notes.append("Correzioni manuali: " + ", ".join(names.get(k, k).lower() for k in item.manual) + ".")
        if notes:
            parts.append(("NOTE\n", "section"))
            parts += [(n + "\n", "muted") for n in notes]
        self._set_info(parts)

    def _show_multi_details(self, items: list[PlanItem]):
        self.detail_title.configure(text=f"{len(items)} brani selezionati")
        rows = []
        for key, label in COMPARE_FIELDS:
            currents = {str(it.original.get(key) or "—") for it in items}
            proposed = {str(it.proposed.get(key) or it.original.get(key) or "—") for it in items}
            changed = any(key in it.tag_changes or key in it.tags_to_clear for it in items)
            rows.append((label,
                         currents.pop() if len(currents) == 1 else "(valori diversi)",
                         proposed.pop() if len(proposed) == 1 else "(valori diversi)", "", changed))
        self._fill_compare(rows)
        counts = {k: 0 for k in STATUS_LABELS}
        for it in items:
            counts[self._status(it)] += 1
        parts = [("RIEPILOGO\n", "section")]
        for key, label in STATUS_LABELS.items():
            if counts[key]:
                parts.append((f"{label}: {counts[key]}\n", "warn" if key == "warn" else ""))
        parts += [("\n", ""), ("Premi Correggi per cambiare album, artista, anno o genere "
                               "di tutti i brani selezionati in un colpo solo.\n", "muted")]
        self._set_info(parts)

    def _show_folder_details(self, group: int):
        folder, indices = self.groups[group]
        items = [self.plan.items[i] for i in indices]
        self.detail_title.configure(text=self._rel(folder))
        self._show_multi_details(items)
        self.detail_title.configure(text=self._rel(folder))
        rename = self._folder_rows_info().get(folder)
        parts = [("CARTELLA\n", "section"), (str(folder) + "\n", "")]
        if rename:
            parts += [("NUOVO NOME\n", "section"), (rename.new_name + "\n", "accent")]
        counts = {k: 0 for k in STATUS_LABELS}
        for it in items:
            counts[self._status(it)] += 1
        parts.append(("CONTENUTO\n", "section"))
        parts.append((_plural(len(items), "brano", "brani") + "\n", ""))
        for key, label in STATUS_LABELS.items():
            if counts[key]:
                parts.append((f"{label}: {counts[key]}\n", "warn" if key == "warn" else "muted"))
        self._set_info(parts)


def main():
    app = App()
    app.mainloop()

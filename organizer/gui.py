"""Finestra principale dell'app (tkinter, in italiano).

Flusso: scegli cartella -> Analizza -> anteprima nella tabella (con correzioni
manuali possibili con doppio clic) -> Applica -> eventualmente Annulla.

Formato dei nomi file e delle cartelle personalizzabile con pattern come
"{nn} - {titolo}" o "{artista} - {album} ({disco})".
"""

from __future__ import annotations

import json
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .applier import JOURNAL_PATH, apply, undo
from .filename_parser import expand_pattern
from .planner import (
    DEFAULT_FILENAME_PATTERN,
    DEFAULT_FOLDER_PATTERN,
    FILENAME_PRESETS,
    FOLDER_PRESETS,
    CancelledError,
    Plan,
    PlanItem,
    analyze,
    replan,
)

# come .exe (PyInstaller) la configurazione sta accanto all'eseguibile
if getattr(sys, "frozen", False):
    CONFIG_PATH = Path(sys.executable).resolve().parent / "config.json"
else:
    CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"

SOURCE_LABELS = {
    "tag": "Tag",
    "nome file": "Nome file",
    "nome cartella": "Cartella",
    "internet": "Internet",
    "manuale": "Manuale",
    "analisi": "Analisi",
}

COLUMNS = ("stato", "file", "nuovo", "titolo", "artista", "album", "traccia", "fonte", "conf")
COLUMN_HEADERS = {
    "stato": "Stato",
    "file": "File attuale",
    "nuovo": "Nuovo nome",
    "titolo": "Titolo",
    "artista": "Artista",
    "album": "Album",
    "traccia": "Nº",
    "fonte": "Fonte",
    "conf": "Conf.",
}
COLUMN_WIDTHS = {"stato": 110, "file": 270, "nuovo": 270, "titolo": 200, "artista": 140, "album": 170, "traccia": 40, "fonte": 120, "conf": 50}


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Music Organizer — Organizza libreria musicale")
        self.geometry("1180x720")
        self.minsize(900, 520)

        self.plan: Plan | None = None
        self.items: list[PlanItem] = []
        self.queue: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.cancel_event = threading.Event()

        self._build_widgets()
        self._load_config()
        self._update_examples()
        self.after(100, self._poll_queue)

    # --- costruzione interfaccia ------------------------------------------------

    def _build_widgets(self):
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")

        ttk.Label(top, text="Cartella musica:").pack(side="left")
        self.folder_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.folder_var).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(top, text="Scegli…", command=self._choose_folder).pack(side="left")

        options = ttk.Frame(self, padding=(8, 0))
        options.pack(fill="x")
        self.reorganize_var = tk.BooleanVar(value=False)
        self.online_var = tk.BooleanVar(value=True)
        self.covers_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(options, text="Riorganizza in Artista\\Album", variable=self.reorganize_var,
                        command=self._on_settings_change).pack(side="left")
        ttk.Checkbutton(options, text="Cerca dati mancanti online", variable=self.online_var).pack(side="left", padx=(16, 0))
        ttk.Checkbutton(options, text="Scarica copertine mancanti", variable=self.covers_var).pack(side="left", padx=(16, 0))

        # formato del nome file
        patterns = ttk.Frame(self, padding=(8, 4))
        patterns.pack(fill="x")
        ttk.Label(patterns, text="Nome file:").pack(side="left")
        self.pattern_var = tk.StringVar(value=DEFAULT_FILENAME_PATTERN)
        self.pattern_combo = ttk.Combobox(patterns, textvariable=self.pattern_var,
                                          values=FILENAME_PRESETS, width=34)
        self.pattern_combo.pack(side="left", padx=6)
        self.pattern_combo.bind("<<ComboboxSelected>>", self._on_settings_change)
        self.pattern_combo.bind("<Return>", self._on_settings_change)
        self.pattern_combo.bind("<FocusOut>", self._on_settings_change)
        self.filename_example = ttk.Label(patterns, text="", foreground="#5a6b8a")
        self.filename_example.pack(side="left", padx=(2, 0))

        # rinomina delle cartelle album
        folders_row = ttk.Frame(self, padding=(8, 0))
        folders_row.pack(fill="x")
        ttk.Label(folders_row, text="Cartelle:").pack(side="left")
        self.folder_rename_var = tk.BooleanVar(value=False)
        self.folder_rename_cb = ttk.Checkbutton(folders_row, text="Rinomina le cartelle album",
                                                variable=self.folder_rename_var,
                                                command=self._on_settings_change)
        self.folder_rename_cb.pack(side="left", padx=(4, 12))
        ttk.Label(folders_row, text="Formato:").pack(side="left")
        self.folder_pattern_var = tk.StringVar(value=DEFAULT_FOLDER_PATTERN)
        self.folder_pattern_combo = ttk.Combobox(folders_row, textvariable=self.folder_pattern_var,
                                                 values=FOLDER_PRESETS, width=26)
        self.folder_pattern_combo.pack(side="left", padx=6)
        self.folder_pattern_combo.bind("<<ComboboxSelected>>", self._on_settings_change)
        self.folder_pattern_combo.bind("<Return>", self._on_settings_change)
        self.folder_pattern_combo.bind("<FocusOut>", self._on_settings_change)
        self.folder_example = ttk.Label(folders_row, text="", foreground="#5a6b8a")
        self.folder_example.pack(side="left", padx=(2, 0))

        buttons = ttk.Frame(self, padding=8)
        buttons.pack(fill="x")
        self.analyze_btn = ttk.Button(buttons, text="🔍 Analizza", command=self._analyze)
        self.analyze_btn.pack(side="left")
        self.stop_btn = ttk.Button(buttons, text="⏹ Ferma", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        self.apply_btn = ttk.Button(buttons, text="✅ Applica modifiche", command=self._apply, state="disabled")
        self.apply_btn.pack(side="left", padx=(18, 0))
        self.undo_btn = ttk.Button(buttons, text="↩️ Annulla ultima modifica", command=self._undo)
        self.undo_btn.pack(side="left", padx=6)
        if not JOURNAL_PATH.exists():
            self.undo_btn.state(["disabled"])

        # tabella anteprima file
        table_frame = ttk.Frame(self, padding=(8, 0))
        table_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(table_frame, columns=COLUMNS, show="headings", selectmode="browse")
        for col in COLUMNS:
            self.tree.heading(col, text=COLUMN_HEADERS[col])
            self.tree.column(col, width=COLUMN_WIDTHS[col], anchor="w" if col not in ("traccia", "conf") else "center")
        yscroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        self.tree.tag_configure("ok", foreground="#6a7a6a")
        self.tree.tag_configure("warn", foreground="#8a6d00")
        self.tree.bind("<Double-1>", self._edit_row)

        # anteprima rinomina cartelle
        self.folder_frame = ttk.Frame(self, padding=(8, 4))
        self.folder_tree = ttk.Treeview(self.folder_frame, columns=("vecchio", "nuovo"), show="headings", height=4)
        self.folder_tree.heading("vecchio", text="Cartella attuale")
        self.folder_tree.heading("nuovo", text="Nuovo nome cartella")
        self.folder_tree.column("vecchio", width=430, anchor="w")
        self.folder_tree.column("nuovo", width=430, anchor="w")

        bottom = ttk.Frame(self, padding=8)
        bottom.pack(fill="x", side="bottom")
        self.status_var = tk.StringVar(value="Scegli la cartella della musica e premi Analizza.")
        ttk.Label(bottom, textvariable=self.status_var).pack(side="left")
        self.progress = ttk.Progressbar(bottom, mode="determinate", length=240)
        self.progress.pack(side="right")

    # --- configurazione persistente ---------------------------------------------

    def _load_config(self):
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if cfg.get("folder"):
                self.folder_var.set(cfg["folder"])
            self.reorganize_var.set(bool(cfg.get("reorganize", False)))
            self.online_var.set(bool(cfg.get("online", True)))
            self.covers_var.set(bool(cfg.get("covers", False)))
            self.pattern_var.set(cfg.get("filename_pattern") or DEFAULT_FILENAME_PATTERN)
            self.folder_pattern_var.set(cfg.get("folder_pattern") or DEFAULT_FOLDER_PATTERN)
            self.folder_rename_var.set(bool(cfg.get("rename_folders", False)))
        except Exception:
            pass
        self._update_folder_widgets_state()

    def _save_config(self):
        try:
            CONFIG_PATH.write_text(json.dumps({
                "folder": self.folder_var.get(),
                "reorganize": self.reorganize_var.get(),
                "online": self.online_var.get(),
                "covers": self.covers_var.get(),
                "filename_pattern": self.pattern_var.get(),
                "folder_pattern": self.folder_pattern_var.get(),
                "rename_folders": self.folder_rename_var.get(),
            }, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:
            pass

    # --- azioni utente -----------------------------------------------------------

    def _choose_folder(self):
        folder = filedialog.askdirectory(initialdir=self.folder_var.get() or str(Path.home()))
        if folder:
            self.folder_var.set(folder)

    def _update_examples(self):
        self.filename_example.config(text="es. " + expand_pattern(
            self.pattern_var.get(),
            track=1, artist="Artista", album="Album", title="Titolo", width=2,
        ) + ".mp3")
        self.folder_example.config(text="es. " + expand_pattern(
            self.folder_pattern_var.get(),
            artist="Artista", album="Album", disc=1, max_len=80,
        ) + "\\")

    def _update_folder_widgets_state(self):
        state = ["disabled"] if self.reorganize_var.get() else ["!disabled"]
        self.folder_rename_cb.state(state)
        self.folder_pattern_combo.config(state="disabled" if self.reorganize_var.get() else "normal")

    def _on_settings_change(self, event=None):
        self._update_examples()
        self._update_folder_widgets_state()
        self._save_config()
        if self.plan is None:
            return
        self._replan()
        self._show_plan(self.plan)

    def _replan(self):
        """Ricalcola nomi e cartelle con i pattern correnti, senza riscaicare nulla."""
        if self.plan is None:
            return
        replan(
            self.plan,
            filename_pattern=self.pattern_var.get().strip() or DEFAULT_FILENAME_PATTERN,
            folder_pattern=self.folder_pattern_var.get().strip() or DEFAULT_FOLDER_PATTERN,
            rename_folders=self.folder_rename_var.get(),
            reorganize=self.reorganize_var.get(),
        )

    def _analyze(self):
        folder = Path(self.folder_var.get().strip().strip('"'))
        if not folder.is_dir():
            messagebox.showwarning("Cartella non valida", "Scegli una cartella esistente che contiene i file musicali.")
            return
        self._save_config()
        self._start_worker(self._analyze_worker, folder)

    def _stop(self):
        self.cancel_event.set()

    def _apply(self):
        if not self.plan:
            return
        changed = [it for it in self.items if it.needs_change]
        folder_renames = [fr for fr in self.plan.folder_renames if fr.needs_change] if self.plan.rename_folders else []
        if not changed and not folder_renames:
            messagebox.showinfo("Niente da fare", "Nessun file o cartella necessita di modifiche.")
            return
        renames = sum(1 for it in changed if it.new_name != it.path.name or (it.new_dir and it.new_dir != it.path.parent))
        tags_only = len(changed) - renames
        msg = (
            f"Applicare le modifiche a {len(changed)} file?\n\n"
            f"• Rinomini/spostamenti file: {renames}\n"
            f"• Solo tag: {tags_only}"
        )
        if folder_renames:
            msg += f"\n• Cartelle da rinominare: {len(folder_renames)} (i file non audio dentro seguono la cartella)"
        msg += "\n\nPotrai annullare tutto con 'Annulla ultima modifica'."
        if not messagebox.askyesno("Conferma modifiche", msg):
            return
        self._start_worker(self._apply_worker, self.plan)

    def _undo(self):
        if not JOURNAL_PATH.exists():
            return
        if not messagebox.askyesno("Annulla modifiche", "Ripristinare file, cartelle e tag allo stato precedente all'ultima applicazione?"):
            return
        self._start_worker(self._undo_worker)

    def _edit_row(self, event):
        iid = self.tree.focus()
        if not iid or not self.plan:
            return
        index = int(iid)
        item = self.items[index]
        EditDialog(self, item, on_save=self._on_settings_change)

    # --- lavoro in background -----------------------------------------------------

    def _start_worker(self, target, *args):
        if self.worker is not None and self.worker.is_alive():
            return
        self.cancel_event = threading.Event()
        self._set_busy(True)
        self.worker = threading.Thread(target=target, args=args, daemon=True)
        self.worker.start()

    def _set_busy(self, busy: bool):
        self.analyze_btn.state(["disabled" if busy else "!disabled"])
        self.apply_btn.state(["disabled" if busy else "!disabled"])
        self.undo_btn.state(["disabled" if busy else "!disabled"])
        self.stop_btn.state(["!disabled" if busy else "disabled"])

    def _analyze_worker(self, folder: Path):
        try:
            def progress(done, total, message):
                self.queue.put(("progress", done, total, message))

            plan = analyze(
                folder,
                reorganize=self.reorganize_var.get(),
                online=self.online_var.get(),
                download_covers=self.covers_var.get(),
                filename_pattern=self.pattern_var.get().strip() or DEFAULT_FILENAME_PATTERN,
                folder_pattern=self.folder_pattern_var.get().strip() or DEFAULT_FOLDER_PATTERN,
                rename_folders=self.folder_rename_var.get(),
                progress=progress,
                cancel_event=self.cancel_event,
            )
            self.queue.put(("plan", plan))
        except CancelledError:
            self.queue.put(("cancelled",))
        except Exception as exc:
            self.queue.put(("error", f"Errore durante l'analisi: {exc}"))

    def _apply_worker(self, plan: Plan):
        try:
            def progress(done, total, message):
                self.queue.put(("progress", done, total, message))

            journal = apply(plan, progress=progress, cancel_event=self.cancel_event)
            self.queue.put(("applied", journal))
        except Exception as exc:
            self.queue.put(("error", f"Errore durante l'applicazione: {exc}"))

    def _undo_worker(self):
        try:
            def progress(done, total, message):
                self.queue.put(("progress", done, total, message))

            count = undo(progress=progress)
            self.queue.put(("undone", count))
        except Exception as exc:
            self.queue.put(("error", f"Errore durante l'annullamento: {exc}"))

    # --- coda messaggi -----------------------------------------------------------

    def _poll_queue(self):
        try:
            while True:
                msg = self.queue.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    _, done, total, message = msg
                    self.progress.config(mode="determinate")
                    if total:
                        self.progress.config(maximum=max(total, 1), value=done)
                    else:
                        self.progress.config(mode="indeterminate", value=0)
                    self.status_var.set(message)
                elif kind == "plan":
                    self._show_plan(msg[1])
                    self._set_busy(False)
                elif kind == "cancelled":
                    self.status_var.set("Analizza annullata.")
                    self._set_busy(False)
                elif kind == "applied":
                    self.undo_btn.state(["!disabled"])
                    messagebox.showinfo("Fatto", "Modifiche applicate e salvate nel diario.\nOra aggiorno l'anteprima…")
                    self._analyze()
                elif kind == "undone":
                    count = msg[1]
                    messagebox.showinfo("Annullato", f"Ripristinati {count} file allo stato originale.")
                    self._analyze()
                elif kind == "error":
                    messagebox.showerror("Errore", msg[1])
                    self._set_busy(False)
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    # --- tabella -------------------------------------------------------------------

    def _show_plan(self, plan: Plan):
        self.plan = plan
        self.items = plan.items
        self.tree.delete(*self.tree.get_children())
        for index, item in enumerate(plan.items):
            self._insert_row(index, item)
        self._refresh_folder_tree()
        self._update_status()
        self.apply_btn.state(["!disabled"])

    def _insert_row(self, index: int, item: PlanItem):
        values = (
            self._stato_label(item),
            item.path.name,
            item.new_name or "",
            item.components.get("title") or "",
            item.components.get("artist") or "",
            item.components.get("album") or "",
            item.components.get("track") or "",
            self._source_label(item),
            self._conf_label(item),
        )
        tag = self._row_tag(item)
        self.tree.insert("", "end", iid=str(index), values=values, tags=(tag,))

    def _refresh_folder_tree(self):
        self.folder_tree.delete(*self.folder_tree.get_children())
        renames = [fr for fr in (self.plan.folder_renames if self.plan else []) if fr.needs_change]
        if renames and not self.reorganize_var.get():
            for fr in renames:
                self.folder_tree.insert("", "end", values=(fr.folder.name, fr.new_name))
            self.folder_frame.pack(fill="x")
        else:
            self.folder_frame.pack_forget()

    def _update_status(self):
        if not self.items:
            self.status_var.set("Nessun file audio trovato nella cartella.")
            return
        changed = [it for it in self.items if it.needs_change]
        warnings = sum(1 for it in self.items if it.warnings)
        ok = len(self.items) - len(changed)
        text = f"{len(self.items)} file · {len(changed)} da modificare · {ok} già a posto"
        if warnings:
            text += f" · {warnings} da controllare"
        if self.plan and self.plan.rename_folders:
            folders = sum(1 for fr in self.plan.folder_renames if fr.needs_change)
            if folders:
                text += f" · {folders} cartelle da rinominare"
        self.status_var.set(text)

    @staticmethod
    def _stato_label(item: PlanItem) -> str:
        if item.warnings:
            return "⚠ controlla"
        if item.needs_change:
            return "✎ modifiche"
        return "✓ ok"

    @staticmethod
    def _row_tag(item: PlanItem) -> str:
        if item.warnings:
            return "warn"
        if item.needs_change:
            return "normal"
        return "ok"

    @staticmethod
    def _source_label(item: PlanItem) -> str:
        seen = []
        for key in ("title", "artist", "album", "track", "year", "genre", "albumartist"):
            source = item.sources.get(key)
            if source and source not in seen:
                seen.append(source)
        return " + ".join(SOURCE_LABELS.get(s, s) for s in seen) or "—"

    @staticmethod
    def _conf_label(item: PlanItem) -> str:
        if item.confidence is None:
            return ""
        return f"{round(item.confidence * 100)}%"


class EditDialog(tk.Toplevel):
    """Finestra per correggere manualmente i dati di un file prima di applicare."""

    FIELDS = (("title", "Titolo"), ("artist", "Artista"), ("album", "Album"), ("track", "Nº traccia"), ("year", "Anno"))

    def __init__(self, master: App, item: PlanItem, on_save):
        super().__init__(master)
        self.item = item
        self.on_save = on_save
        self.title(f"Correggi: {item.path.name}")
        self.resizable(False, False)
        self.grab_set()

        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)
        self.entries: dict[str, tk.StringVar] = {}
        for row, (key, label) in enumerate(self.FIELDS):
            ttk.Label(frame, text=f"{label}:").grid(row=row, column=0, sticky="e", pady=3, padx=(0, 8))
            value = item.manual.get(key, item.components.get(key, ""))
            var = tk.StringVar(value="" if value is None else str(value))
            self.entries[key] = var
            ttk.Entry(frame, textvariable=var, width=44).grid(row=row, column=1, pady=3)
        ttk.Label(frame, text="Lascia vuoto un campo per cancellarlo.", foreground="#777").grid(
            row=len(self.FIELDS), column=0, columnspan=2, sticky="w", pady=(4, 0))

        btns = ttk.Frame(frame)
        btns.grid(row=len(self.FIELDS) + 1, column=0, columnspan=2, pady=(8, 0))
        ttk.Button(btns, text="Salva", command=self._save).pack(side="left", padx=4)
        ttk.Button(btns, text="Annulla", command=self.destroy).pack(side="left")

        self.bind("<Return>", lambda e: self._save())
        self.bind("<Escape>", lambda e: self.destroy())

    def _save(self):
        manual = {}
        for key, var in self.entries.items():
            value = var.get().strip()
            if key in ("track", "year"):
                try:
                    value = int(value) if value else None
                except ValueError:
                    value = None
            elif not value:
                value = None
            manual[key] = value  # None = cancella il campo
        self.item.manual = manual
        self.destroy()
        self.on_save()


def main():
    app = App()
    app.mainloop()

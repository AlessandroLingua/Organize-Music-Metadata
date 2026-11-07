"""Finestra di conversione: quali brani, formato, qualita e destinazione."""

from __future__ import annotations

import os
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from .converter import (
    DEFAULT_FORMAT,
    OutputFormat,
    Quality,
    default_output_dir,
    output_dir_error,
    recycle_bin_available,
)
from .theme import P
from .widgets import Tooltip, center_on

# formati compressi con perdita: convertirli in un formato lossless non li migliora
_LOSSY_EXTS = {".mp3", ".ogg", ".opus", ".wma"}


@dataclass
class ConvertRequest:
    root: Path
    files: list[Path] | None        # None = tutti i file audio della cartella (scansione)
    fmt: OutputFormat
    quality: Quality
    output_dir: Path | None         # None = accanto agli originali o al loro posto
    copy_compatible: bool
    overwrite: bool
    replace: bool = False           # al posto degli originali (che vanno nel Cestino)


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


class ConvertDialog(tk.Toplevel):
    """Sceglie cosa convertire e come. `config` (dict) viene aggiornato con le
    scelte fatte, per riproporle la volta successiva."""

    def __init__(self, master: tk.Misc, *, px, root: Path, all_files: list[Path] | None,
                 selected_files: list[Path], formats: list[OutputFormat], config: dict,
                 pending_changes: int, on_start: Callable[[ConvertRequest], None]):
        super().__init__(master)
        self.withdraw()
        self.px = px
        self.root_dir = root
        self.all_files = all_files
        self.selected_files = selected_files
        self.formats = {f.label: f for f in formats}
        self.config_data = config
        # qualita scelta per ogni formato: salvata nella configurazione solo avviando
        qualities = config.get("qualities")
        self.qualities: dict[str, str] = dict(qualities) if isinstance(qualities, dict) else {}
        self.pending_changes = pending_changes
        self.on_start = on_start
        self.configure(background=P.surface)
        self.transient(master)
        self.resizable(False, False)
        self.title("Converti formato")

        body = ttk.Frame(self, style="Surface.TFrame", padding=px(20))
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        indent = px(16) + px(8)  # allinea al testo delle caselle e dei pulsanti di scelta

        ttk.Label(body, text="Converti i file audio", style="DetailTitle.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(body, text="Converte i brani nel formato e nella qualità che scegli, mantenendo "
                             "tag e copertine.",
                  style="CardMuted.TLabel", wraplength=px(520)).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(px(2), px(4)))

        def section(row: int, text: str):
            ttk.Label(body, text=text, style="CardSection.TLabel").grid(
                row=row, column=0, columnspan=3, sticky="w", pady=(px(14), px(4)))

        # --- brani --------------------------------------------------------------------
        section(2, "BRANI DA CONVERTIRE")
        name = root.name or str(root)
        self.scope_var = tk.StringVar(value="selected" if selected_files else "all")
        if selected_files:
            text = ("Il brano selezionato" if len(selected_files) == 1
                    else f"I {len(selected_files)} brani selezionati")
            ttk.Radiobutton(body, text=text, value="selected", variable=self.scope_var,
                            command=self._update).grid(row=3, column=0, columnspan=3, sticky="w")
        all_text = (f"Tutti i brani di «{name}» ({len(all_files)})" if all_files is not None
                    else f"Tutti i brani di «{name}» e delle sue sottocartelle")
        ttk.Radiobutton(body, text=all_text, value="all", variable=self.scope_var,
                        command=self._update).grid(row=4, column=0, columnspan=3, sticky="w")

        # --- formato e qualita -------------------------------------------------------------
        section(5, "FORMATO E QUALITÀ")
        saved_format = config.get("format", DEFAULT_FORMAT)
        fmt = next((f for f in formats if f.key == saved_format), None) or \
            next((f for f in formats if f.key == DEFAULT_FORMAT), formats[0])
        ttk.Label(body, text="Formato", style="Card.TLabel").grid(row=6, column=0, sticky="w",
                                                                  padx=(0, px(14)), pady=px(3))
        self.format_var = tk.StringVar(value=fmt.label)
        self.format_combo = ttk.Combobox(body, textvariable=self.format_var, values=list(self.formats),
                                         state="readonly", width=36)
        self.format_combo.grid(row=6, column=1, sticky="w", pady=px(3))
        self.format_combo.bind("<<ComboboxSelected>>", lambda e: self._on_format_change())
        self.format_desc = ttk.Label(body, text="", style="CardMuted.TLabel", wraplength=px(400))
        self.format_desc.grid(row=7, column=1, columnspan=2, sticky="w")

        ttk.Label(body, text="Qualità", style="Card.TLabel").grid(row=8, column=0, sticky="w",
                                                                  padx=(0, px(14)), pady=(px(8), px(3)))
        self.quality_var = tk.StringVar()
        self.quality_combo = ttk.Combobox(body, textvariable=self.quality_var, state="readonly", width=36)
        self.quality_combo.grid(row=8, column=1, sticky="w", pady=(px(8), px(3)))
        self.quality_combo.bind("<<ComboboxSelected>>", lambda e: self._remember_quality())

        # --- destinazione --------------------------------------------------------------------
        section(9, "DESTINAZIONE")
        self.can_replace = recycle_bin_available(root)
        mode = config.get("mode")
        if mode not in ("folder", "beside", "replace") or (mode == "replace" and not self.can_replace):
            mode = "folder"
        self.mode_var = tk.StringVar(value=mode)
        ttk.Radiobutton(body, text="In una cartella separata, con le stesse sottocartelle",
                        value="folder", variable=self.mode_var, command=self._update).grid(
            row=10, column=0, columnspan=3, sticky="w")
        dest = ttk.Frame(body, style="Surface.TFrame")
        dest.grid(row=11, column=0, columnspan=3, sticky="ew", padx=(indent, 0), pady=(px(2), px(4)))
        dest.columnconfigure(0, weight=1)
        self.dir_var = tk.StringVar()
        self.dir_entry = ttk.Entry(dest, textvariable=self.dir_var, width=52)
        self.dir_entry.grid(row=0, column=0, sticky="ew")
        self.browse_btn = ttk.Button(dest, text="Sfoglia…", style="Small.TButton", command=self._browse)
        self.browse_btn.grid(row=0, column=1, padx=(px(8), 0))
        ttk.Radiobutton(body, text="Accanto agli originali, nella stessa cartella",
                        value="beside", variable=self.mode_var, command=self._update).grid(
            row=12, column=0, columnspan=3, sticky="w")
        replace_rb = ttk.Radiobutton(body, text="Al posto degli originali, senza lasciare doppioni",
                                     value="replace", variable=self.mode_var, command=self._update)
        replace_rb.grid(row=13, column=0, columnspan=3, sticky="w")
        hint = ("Ogni originale va nel Cestino solo dopo che la sua conversione è riuscita."
                if self.can_replace else
                "Non disponibile: l'unità della musica non ha il Cestino (unità di rete o rimovibile), "
                "quindi gli originali andrebbero persi.")
        ttk.Label(body, text=hint, style="CardMuted.TLabel", wraplength=px(480)).grid(
            row=14, column=0, columnspan=3, sticky="w", padx=(indent, 0))
        if not self.can_replace:
            replace_rb.state(["disabled"])

        saved_dir = config.get("output_dirs", {}).get(self._root_key())
        self.custom_dir = bool(saved_dir)
        self._setting_dir = True
        self.dir_var.set(saved_dir or str(default_output_dir(root, fmt)))
        self._setting_dir = False
        self.dir_var.trace_add("write", lambda *_: self._on_dir_typing())

        # --- opzioni -------------------------------------------------------------------------
        section(15, "OPZIONI")
        self.copy_var = tk.BooleanVar(value=config.get("copy", True))
        self.copy_cb = ttk.Checkbutton(body, text="", variable=self.copy_var)
        self.copy_cb.grid(row=16, column=0, columnspan=3, sticky="w")
        Tooltip(self.copy_cb, "Ricodificare un file che è già nel formato scelto, con qualità uguale o "
                              "inferiore, non lo migliorerebbe e, per MP3, AAC, OGG e Opus, ne peggiorerebbe "
                              "la qualità. Nella cartella separata viene copiato così com'è; al posto degli "
                              "originali resta com'è.")
        self.overwrite_var = tk.BooleanVar(value=config.get("overwrite", False))
        self.overwrite_cb = ttk.Checkbutton(body, text="Sostituisci i file già presenti nella destinazione",
                                            variable=self.overwrite_var)
        self.overwrite_cb.grid(row=17, column=0, columnspan=3, sticky="w")
        Tooltip(self.overwrite_cb, "Se non è attivo, i file già convertiti vengono saltati: "
                                   "così puoi riprendere una conversione interrotta. "
                                   "Accanto agli originali nessun file viene mai sostituito.")

        self.note_label = ttk.Label(body, text="", style="CardWarn.TLabel", wraplength=px(520))
        self.note_label.grid(row=18, column=0, columnspan=3, sticky="w", pady=(px(12), 0))
        self.error_label = ttk.Label(body, text="", style="CardError.TLabel", wraplength=px(520))
        self.error_label.grid(row=19, column=0, columnspan=3, sticky="w")

        buttons = ttk.Frame(body, style="Surface.TFrame")
        buttons.grid(row=20, column=0, columnspan=3, sticky="ew", pady=(px(14), 0))
        self.start_btn = ttk.Button(buttons, text="Converti", style="Accent.TButton", command=self._start)
        self.start_btn.pack(side="right")
        ttk.Button(buttons, text="Annulla", command=self.destroy).pack(side="right", padx=(0, px(8)))

        self.bind("<Return>", lambda e: self._start())
        self.bind("<Escape>", lambda e: self.destroy())
        self._on_format_change()

        center_on(self, master)
        self.deiconify()
        self.wait_visibility()
        self.grab_set()
        self.format_combo.focus_set()

    # --- stato ----------------------------------------------------------------------------

    def _root_key(self) -> str:
        return os.path.normcase(str(self.root_dir))

    @property
    def fmt(self) -> OutputFormat:
        return self.formats[self.format_var.get()]

    def _quality(self) -> Quality:
        fmt = self.fmt
        return next((q for q in fmt.qualities if q.label == self.quality_var.get()), fmt.quality(None))

    def _files(self) -> list[Path] | None:
        return self.selected_files if self.scope_var.get() == "selected" else self.all_files

    def _on_format_change(self):
        fmt = self.fmt
        self.format_desc.configure(text=fmt.description)
        saved = self.qualities.get(fmt.key)
        self.quality_combo.configure(values=[q.label for q in fmt.qualities])
        self.quality_var.set(fmt.quality(saved).label)
        self.copy_cb.configure(text=f"Non ricodificare i file già in {fmt.short} "
                                    f"con qualità uguale o inferiore")
        if not self.custom_dir:
            self._setting_dir = True
            self.dir_var.set(str(default_output_dir(self.root_dir, fmt)))
            self._setting_dir = False
        self._update()

    def _remember_quality(self):
        self.qualities[self.fmt.key] = self._quality().key

    def _on_dir_typing(self):
        if not self._setting_dir:
            self.custom_dir = True
        self.error_label.configure(text="")

    def _browse(self):
        current = self.dir_var.get().strip()
        start = current if current and Path(current).is_dir() else str(self.root_dir.parent)
        folder = filedialog.askdirectory(parent=self, initialdir=start, mustexist=False,
                                         title="Dove salvare i file convertiti")
        if folder:
            self.dir_var.set(str(Path(folder)))

    def _update(self):
        mode = self.mode_var.get()
        folder_mode = mode == "folder"
        self.dir_entry.state(["!disabled"] if folder_mode else ["disabled"])
        self.browse_btn.state(["!disabled"] if folder_mode else ["disabled"])
        self.overwrite_cb.state(["!disabled"] if folder_mode else ["disabled"])
        self.copy_cb.state(["disabled"] if mode == "beside" else ["!disabled"])
        self.error_label.configure(text="")

        files = self._files()
        count = len(files) if files is not None else None
        verb = "Converti e sostituisci" if mode == "replace" else "Converti"
        self.start_btn.configure(text=verb if count is None else f"{verb} {_plural(count, 'brano', 'brani')}")

        notes = []
        fmt = self.fmt
        if fmt.lossless and files:
            lossy = sum(1 for f in files if f.suffix.lower() in _LOSSY_EXTS)
            if lossy:
                notes.append(f"{_plural(lossy, 'brano è', 'brani sono')} in un formato compresso "
                             f"(MP3, OGG…): convertirli in {fmt.short} occupa più spazio "
                             f"ma non ne migliora la qualità.")
        if mode == "beside":
            notes.append(f"I file che sono già {fmt.ext} vengono saltati; i nuovi file compariranno "
                         f"nella libreria, accanto agli originali.")
        elif mode == "replace":
            notes.append("Gli originali vanno nel Cestino di Windows: puoi recuperarli da lì "
                         "(«Annulla ultima modifica» non riguarda la conversione). Se converti moltissimi "
                         "file, il Cestino potrebbe non avere spazio per conservarli tutti.")
        if self.pending_changes:
            notes.append(f"Hai {_plural(self.pending_changes, 'modifica', 'modifiche')} non ancora "
                         f"applicat{'a' if self.pending_changes == 1 else 'e'}: i brani vengono convertiti "
                         f"con i nomi e i tag attuali. Applica prima le modifiche per convertirli già corretti.")
        self.note_label.configure(text="\n\n".join(notes))
        if notes:
            self.note_label.grid()
        else:
            self.note_label.grid_remove()

    def _start(self):
        output_dir = None
        mode = self.mode_var.get()
        if mode == "folder":
            error = output_dir_error(self.dir_var.get(), self.root_dir)
            if error:
                self.error_label.configure(text=error)
                self.dir_entry.focus_set()
                return
            output_dir = Path(self.dir_var.get().strip().strip('"'))
        elif mode == "replace":
            files = self._files()
            what = (f"i {len(files)} brani scelti" if files is not None and len(files) != 1
                    else "il brano scelto" if files is not None
                    else f"tutti i brani di «{self.root_dir.name or self.root_dir}»")
            if not messagebox.askyesno(
                    "Sostituisci gli originali",
                    f"Stai per convertire {what} in {self.fmt.short} ({self._quality().label}) "
                    f"e sostituire gli originali.\n\n"
                    f"Ogni originale viene spostato nel Cestino solo dopo che la sua conversione è "
                    f"riuscita: potrai recuperarlo da lì.\n\nProcedere?",
                    icon="warning", parent=self):
                return

        cfg = self.config_data
        cfg["format"] = self.fmt.key
        self._remember_quality()
        cfg["qualities"] = self.qualities
        cfg["mode"] = self.mode_var.get()
        cfg["copy"] = self.copy_var.get()
        cfg["overwrite"] = self.overwrite_var.get()
        dirs = {k: v for k, v in cfg.get("output_dirs", {}).items() if k != self._root_key()}
        if output_dir is not None and self.custom_dir:
            dirs = {self._root_key(): str(output_dir), **dirs}
        cfg["output_dirs"] = dict(list(dirs.items())[:8])

        request = ConvertRequest(
            root=self.root_dir, files=self._files(), fmt=self.fmt, quality=self._quality(),
            output_dir=output_dir, copy_compatible=self.copy_var.get() and mode != "beside",
            overwrite=self.overwrite_var.get() and output_dir is not None, replace=mode == "replace")
        self.destroy()
        self.on_start(request)

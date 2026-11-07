"""Finestra dei duplicati: gruppi di copie dello stesso brano, con la scelta
di quale copia tenere (automatica, per criterio, o a mano) e lo spostamento
delle altre nel Cestino."""

from __future__ import annotations

import os
import subprocess
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable

from . import duplicates as dup
from .converter import format_size
from .planner import PlanItem
from .theme import P
from .widgets import Tooltip, center_on

COLUMNS = {
    "azione": ("Azione", 150, "w"),
    "formato": ("Formato", 112, "w"),
    "dimensione": ("Dimensione", 90, "e"),
    "durata": ("Durata", 64, "center"),
    "album": ("Album", 170, "w"),
    "cartella": ("Cartella", 230, "w"),
}


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _duration(seconds: float | None) -> str:
    if not seconds:
        return ""
    minutes, secs = divmod(round(seconds), 60)
    return f"{minutes}:{secs:02d}"


class DuplicatesDialog(tk.Toplevel):
    def __init__(self, master: tk.Misc, *, px, fonts, icons, items: list[PlanItem], rel: Callable[[Path], str],
                 can_trash: bool, config: dict, on_remove: Callable[[list[Path]], None]):
        super().__init__(master)
        self.withdraw()
        self.px = px
        self.icons = icons
        self.all_items = items
        self.rel = rel
        self.can_trash = can_trash
        self.config_data = config
        self.on_remove = on_remove
        self.groups: list[dup.DuplicateGroup] = []
        self.configure(background=P.surface)
        self.transient(master)
        self.title("Trova duplicati")
        self.minsize(px(860), px(520))

        body = ttk.Frame(self, style="Surface.TFrame", padding=px(20))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)

        head = ttk.Frame(body, style="Surface.TFrame")
        head.grid(row=0, column=0, sticky="ew")
        ttk.Label(head, text="Brani doppi", style="DetailTitle.TLabel").pack(anchor="w")
        ttk.Label(head, text="Copie dello stesso brano (stesso artista, titolo e durata), anche in formati "
                             "diversi. Per ogni gruppo scegli quale copia tenere: le altre vanno nel Cestino.",
                  style="CardMuted.TLabel", wraplength=px(900)).pack(anchor="w", pady=(px(2), 0))

        # --- criterio ------------------------------------------------------------------
        opts = ttk.Frame(body, style="Surface.TFrame")
        opts.grid(row=1, column=0, sticky="ew", pady=(px(14), px(10)))
        ttk.Label(opts, text="Tieni", style="Card.TLabel").pack(side="left", padx=(0, px(10)))
        self.rule_var = tk.StringVar()
        self.rule_combo = ttk.Combobox(opts, textvariable=self.rule_var, state="readonly", width=52)
        self.rule_combo.pack(side="left")
        self.rule_combo.bind("<<ComboboxSelected>>", lambda e: self._on_rule_change())
        Tooltip(self.rule_combo, "Sceglie automaticamente la copia da tenere in ogni gruppo. "
                                 "Cambiando criterio le scelte vengono ricalcolate per tutti i gruppi; "
                                 "poi puoi correggere a mano i singoli gruppi.")
        self.across_var = tk.BooleanVar(value=bool(config.get("across_albums", False)))
        across = ttk.Checkbutton(opts, text="Cerca anche tra album diversi", variable=self.across_var,
                                 command=self._find)
        across.pack(side="left", padx=(px(24), 0))
        Tooltip(across, "Considera doppio anche lo stesso brano presente in album diversi, "
                        "per esempio in un album e in una raccolta di successi.")

        # --- tabella -------------------------------------------------------------------------
        card = ttk.Frame(body, style="Card.TFrame", padding=1)
        card.grid(row=2, column=0, sticky="nsew")
        card.columnconfigure(0, weight=1)
        card.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(card, columns=tuple(COLUMNS), show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="Brano / file", anchor="w")
        self.tree.column("#0", width=px(300), minwidth=px(160), stretch=True)
        for col, (title, width, anchor) in COLUMNS.items():
            self.tree.heading(col, text=title, anchor=anchor)
            self.tree.column(col, width=px(width), minwidth=px(50), anchor=anchor,
                             stretch=col in ("album", "cartella"))
        yscroll = ttk.Scrollbar(card, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=yscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        self.tree.tag_configure("group", font=fonts["bold"], background=P.surface_alt)
        self.tree.tag_configure("trash", foreground=P.subtle, font=fonts["italic"])
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._update_actions())
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Return>", lambda e: self._keep_only())
        self.tree.bind("<space>", lambda e: (self._toggle(), "break")[1])
        self.tree.bind("<Button-3>", self._on_right_click)
        self.empty = ttk.Label(card, text="", style="CardMuted.TLabel", anchor="center", justify="center")

        # --- azioni sul file selezionato ----------------------------------------------------------
        bar = ttk.Frame(body, style="Surface.TFrame")
        bar.grid(row=3, column=0, sticky="ew", pady=(px(10), 0))
        bar.columnconfigure(0, weight=1)
        self.selected_label = ttk.Label(bar, text="", style="CardMuted.TLabel")
        self.selected_label.grid(row=0, column=0, sticky="w")
        buttons = ttk.Frame(bar, style="Surface.TFrame")
        buttons.grid(row=1, column=0, sticky="e", pady=(px(6), 0))
        self.keep_only_btn = ttk.Button(buttons, text="Tieni solo questo", style="Small.TButton",
                                        command=self._keep_only)
        self.toggle_btn = ttk.Button(buttons, text="Tieni anche questo", style="Small.TButton", width=18,
                                     command=self._toggle)
        self.keep_all_btn = ttk.Button(buttons, text="Tieni tutto il gruppo", style="Small.TButton",
                                       command=self._keep_all)
        self.play_btn = ttk.Button(buttons, text="▶  Ascolta", style="Small.TButton", command=self._play)
        self.reveal_btn = ttk.Button(buttons, text="Apri cartella", style="Small.TButton", command=self._reveal)
        for i, btn in enumerate((self.keep_only_btn, self.toggle_btn, self.keep_all_btn,
                                 self.play_btn, self.reveal_btn)):
            btn.pack(side="left", padx=(px(6) if i else 0, 0))
        Tooltip(self.keep_only_btn, "Tiene questa copia e mette nel Cestino le altre del gruppo "
                                    "(Invio o doppio clic)")
        Tooltip(self.toggle_btn, "Tiene o mette nel Cestino solo questa copia (barra spaziatrice)")
        Tooltip(self.keep_all_btn, "Non elimina nessuna copia di questo brano")
        Tooltip(self.play_btn, "Apre il file nel lettore predefinito, per confrontare le copie")

        # --- piede ---------------------------------------------------------------------------
        foot = ttk.Frame(body, style="Surface.TFrame")
        foot.grid(row=4, column=0, sticky="ew", pady=(px(16), 0))
        foot.columnconfigure(0, weight=1)
        self.summary_label = ttk.Label(foot, text="", style="Card.TLabel", wraplength=px(560))
        self.summary_label.grid(row=0, column=0, sticky="w")
        ttk.Button(foot, text="Chiudi", command=self.destroy).grid(row=0, column=1, padx=(px(12), px(8)))
        self.remove_btn = ttk.Button(foot, text="Sposta nel Cestino", style="Accent.TButton",
                                     command=self._remove)
        self.remove_btn.grid(row=0, column=2)

        self.menu = tk.Menu(self, tearoff=False)
        self.menu.add_command(label="Tieni solo questo", command=self._keep_only)
        self.menu.add_command(label="Tieni anche questo", command=self._toggle)
        self.menu.add_command(label="Tieni tutto il gruppo", command=self._keep_all)
        self.menu.add_separator()
        self.menu.add_command(label="Ascolta", command=self._play)
        self.menu.add_command(label="Mostra in Esplora risorse", command=self._reveal)

        self.bind("<Escape>", lambda e: self.destroy())
        self._find()

        self.update_idletasks()
        w = min(px(1180), self.winfo_screenwidth() - px(80))
        h = min(px(700), self.winfo_screenheight() - px(120))
        self.geometry(f"{w}x{h}")
        center_on(self, master)
        self.geometry(f"{w}x{h}")
        self.deiconify()
        self.wait_visibility()
        self.grab_set()
        self.tree.focus_set()

    # --- dati -------------------------------------------------------------------------------

    @property
    def rule(self) -> str:
        return self._rule_keys.get(self.rule_var.get(), dup.RULE_BEST)

    def _find(self):
        """(Ri)cerca i gruppi con le opzioni correnti."""
        self.config_data["across_albums"] = self.across_var.get()
        saved = self.config_data.get("rule", dup.RULE_BEST)
        self.groups = dup.find_duplicates(self.all_items, across_albums=self.across_var.get())
        choices = dup.rule_choices(self.groups)
        self._rule_keys = {label: key for key, label in choices}
        labels = [label for _, label in choices]
        current = next((label for key, label in choices if key == saved), labels[0])
        self.rule_combo.configure(values=labels)
        self.rule_var.set(current)
        for group in self.groups:
            dup.apply_rule(group, self.rule)
        self._rebuild()

    def _on_rule_change(self):
        self.config_data["rule"] = self.rule
        for group in self.groups:
            dup.apply_rule(group, self.rule)
        self._refresh_rows()

    # --- tabella ---------------------------------------------------------------------------

    def _rebuild(self):
        self.tree.delete(*self.tree.get_children())
        for gi, group in enumerate(self.groups):
            self.tree.insert("", "end", iid=f"g{gi}", open=True, **self._group_row(group))
            for fi, item in enumerate(group.items):
                self.tree.insert(f"g{gi}", "end", iid=f"g{gi}:{fi}", **self._file_row(group, item))
        if self.groups:
            self.empty.place_forget()
            first = f"g0:{0}"
            self.tree.selection_set(first)
            self.tree.focus(first)
        else:
            self.empty.configure(text="Nessun brano doppio trovato.\n\n"
                                      + ("" if self.across_var.get() else
                                         "Prova ad attivare «Cerca anche tra album diversi»."))
            self.empty.place(relx=0.5, rely=0.45, anchor="center")
        self._update_summary()
        self._update_actions()

    def _group_row(self, group: dup.DuplicateGroup) -> dict:
        formats = sorted({it.audio.name or it.path.suffix.lstrip(".").upper() for it in group.items})
        removed = len(group.to_remove)
        action = _plural(len(group.items), "copia", "copie") + (f", {removed} nel Cestino" if removed else "")
        return {"text": group.title, "tags": ("group",),
                "values": (action, ", ".join(formats), "", _duration(group.items[0].audio.length), "", "")}

    def _file_row(self, group: dup.DuplicateGroup, item: PlanItem) -> dict:
        keep = item.path in group.keep
        values = ("Tieni" if keep else "Nel Cestino",
                  item.audio.label or item.path.suffix.lstrip(".").upper(),
                  format_size(group.size(item)),
                  _duration(item.audio.length),
                  item.components.get("album") or "",
                  self.rel(item.path.parent))
        return {"text": item.path.name, "values": values, "tags": () if keep else ("trash",),
                "image": self.icons["st_ok" if keep else "st_excluded"]}

    def _refresh_rows(self, groups: list[int] | None = None):
        for gi in (groups if groups is not None else range(len(self.groups))):
            group = self.groups[gi]
            self.tree.item(f"g{gi}", **self._group_row(group))
            for fi, item in enumerate(group.items):
                self.tree.item(f"g{gi}:{fi}", **self._file_row(group, item))
        self._update_summary()
        self._update_actions()

    def _paths_to_remove(self) -> list[Path]:
        return [it.path for g in self.groups for it in g.to_remove]

    def _update_summary(self):
        paths = self._paths_to_remove()
        size = sum(g.size(it) for g in self.groups for it in g.to_remove)
        if not self.groups:
            text = ""
        elif paths:
            text = (f"{_plural(len(self.groups), 'gruppo', 'gruppi')} di doppioni  ·  "
                    f"{_plural(len(paths), 'file va', 'file vanno')} nel Cestino  ·  "
                    f"{format_size(size)} liberati")
        else:
            text = f"{_plural(len(self.groups), 'gruppo', 'gruppi')} di doppioni  ·  tutte le copie vengono tenute"
        if self.groups and not self.can_trash:
            text = ("L'unità della musica non ha il Cestino (unità di rete o rimovibile): "
                    "elimina a mano le copie che non vuoi, i file andrebbero persi per sempre.")
            self.summary_label.configure(style="CardError.TLabel")
        self.summary_label.configure(text=text)
        self.remove_btn.configure(text=f"Sposta nel Cestino {_plural(len(paths), 'file', 'file')}"
                                  if paths else "Sposta nel Cestino")
        self.remove_btn.state(["!disabled"] if paths and self.can_trash else ["disabled"])

    # --- selezione e azioni -------------------------------------------------------------------

    def _selected(self) -> tuple[int, int | None] | None:
        sel = self.tree.selection()
        if not sel:
            return None
        gi, _, fi = sel[0][1:].partition(":")
        return int(gi), (int(fi) if fi else None)

    def _update_actions(self):
        sel = self._selected()
        file_selected = sel is not None and sel[1] is not None
        for btn in (self.keep_only_btn, self.toggle_btn, self.play_btn):
            btn.state(["!disabled"] if file_selected else ["disabled"])
        for btn in (self.keep_all_btn, self.reveal_btn):
            btn.state(["!disabled"] if sel is not None else ["disabled"])
        if not file_selected:
            self.selected_label.configure(text="")
            self.toggle_btn.configure(text="Tieni anche questo")
            return
        group = self.groups[sel[0]]
        item = group.items[sel[1]]
        keep = item.path in group.keep
        self.toggle_btn.configure(text="Metti nel Cestino" if keep else "Tieni anche questo")
        self.menu.entryconfigure(1, label="Metti nel Cestino" if keep else "Tieni anche questo")
        self.selected_label.configure(text=f"{item.audio.details}  ·  {self.rel(item.path)}")

    def _keep_only(self):
        sel = self._selected()
        if sel is None or sel[1] is None:
            return
        group = self.groups[sel[0]]
        group.keep = {group.items[sel[1]].path}
        self._refresh_rows([sel[0]])

    def _toggle(self):
        sel = self._selected()
        if sel is None or sel[1] is None:
            return
        group = self.groups[sel[0]]
        path = group.items[sel[1]].path
        if path in group.keep:
            if len(group.keep) == 1:
                self.bell()  # almeno una copia deve restare
                self.selected_label.configure(text="Almeno una copia del brano deve restare: "
                                                   "scegli prima quale tenere.")
                return
            group.keep.discard(path)
        else:
            group.keep.add(path)
        self._refresh_rows([sel[0]])

    def _keep_all(self):
        sel = self._selected()
        if sel is None:
            return
        group = self.groups[sel[0]]
        group.keep = {it.path for it in group.items}
        self._refresh_rows([sel[0]])

    def _current_path(self) -> Path | None:
        sel = self._selected()
        if sel is None:
            return None
        group = self.groups[sel[0]]
        return group.items[sel[1] if sel[1] is not None else 0].path

    def _play(self):
        path = self._current_path()
        if path is None:
            return
        try:
            os.startfile(str(path))  # lettore predefinito di Windows
        except (OSError, AttributeError) as exc:
            messagebox.showerror("Impossibile riprodurre il file", str(exc), parent=self)

    def _reveal(self):
        path = self._current_path()
        if path is None:
            return
        try:
            subprocess.Popen(["explorer", "/select,", str(path)])
        except OSError as exc:
            messagebox.showerror("Impossibile aprire la cartella", str(exc), parent=self)

    def _on_double_click(self, event):
        iid = self.tree.identify_row(event.y)
        if self.tree.identify_region(event.x, event.y) in ("heading", "separator") or not iid:
            return None
        if ":" in iid:
            self.tree.selection_set(iid)
            self._keep_only()
            return "break"
        return None  # gruppo: apre/chiude

    def _on_right_click(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        self.tree.selection_set(iid)
        self.tree.focus(iid)
        self._update_actions()
        is_file = ":" in iid
        for index in (0, 1, 4):
            self.menu.entryconfigure(index, state="normal" if is_file else "disabled")
        self.menu.tk_popup(event.x_root, event.y_root)

    def _remove(self):
        paths = self._paths_to_remove()
        if not paths or not self.can_trash:
            return
        size = sum(g.size(it) for g in self.groups for it in g.to_remove)
        groups = sum(1 for g in self.groups if g.to_remove)
        if not messagebox.askyesno(
                "Sposta nel Cestino",
                f"Spostare nel Cestino {_plural(len(paths), 'file doppio', 'file doppi')} "
                f"({format_size(size)}) da {_plural(groups, 'gruppo', 'gruppi')}?\n\n"
                f"Per ogni brano resta la copia segnata «Tieni». Potrai recuperare i file dal Cestino.",
                icon="warning", parent=self):
            return
        self.destroy()
        self.on_remove(paths)

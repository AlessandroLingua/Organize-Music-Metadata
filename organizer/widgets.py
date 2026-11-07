"""Componenti dell'interfaccia: suggerimenti (tooltip), dialogo di correzione
dei dati e finestra di aiuto."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from .planner import PATTERN_FIELDS, PlanItem
from .theme import P


def center_on(window: tk.Toplevel, parent: tk.Misc) -> None:
    """Posiziona una finestra al centro di quella principale."""
    window.update_idletasks()
    w, h = window.winfo_reqwidth(), window.winfo_reqheight()
    px_, py_ = parent.winfo_rootx(), parent.winfo_rooty()
    pw, ph = parent.winfo_width(), parent.winfo_height()
    x = max(0, px_ + (pw - w) // 2)
    y = max(0, py_ + (ph - h) // 3)
    window.geometry(f"+{x}+{y}")


class Tooltip:
    """Suggerimento che compare passando con il mouse su un widget.

    `text` puo essere una stringa o una funzione (testo calcolato al momento).
    """

    def __init__(self, widget: tk.Misc, text: str | Callable[[], str], *, delay: int = 450, wrap: int = 380):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.wrap = wrap
        self._after: str | None = None
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._cancel()
        self._after = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self._after:
            self.widget.after_cancel(self._after)
            self._after = None

    def _show(self):
        self._after = None
        text = self.text() if callable(self.text) else self.text
        if not text or self._tip is not None:
            return
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.attributes("-topmost", True)
        tk.Label(tip, text=text, justify="left", background=P.tooltip_bg, foreground="#FFFFFF",
                 font="TkTooltipFont", wraplength=self.wrap, padx=10, pady=6).pack()
        tip.update_idletasks()
        x = self.widget.winfo_pointerx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        max_x = self.widget.winfo_screenwidth() - tip.winfo_reqwidth() - 8
        max_y = self.widget.winfo_screenheight() - tip.winfo_reqheight() - 48
        if y > max_y:
            y = self.widget.winfo_rooty() - tip.winfo_reqheight() - 6
        tip.geometry(f"+{min(x, max_x)}+{max(0, y)}")
        self._tip = tip

    def _hide(self, _event=None):
        self._cancel()
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


class EditDialog(tk.Toplevel):
    """Correzione manuale dei dati di uno o piu brani prima di applicare.

    Vengono salvati solo i campi effettivamente modificati: con piu brani
    selezionati, un campo con valori diversi lasciato vuoto resta invariato.
    """

    SINGLE_FIELDS = (("title", "Titolo"), ("artist", "Artista"), ("album", "Album"),
                     ("albumartist", "Artista album"), ("track", "Nº traccia"),
                     ("year", "Anno"), ("genre", "Genere"))
    MULTI_FIELDS = (("artist", "Artista"), ("album", "Album"), ("albumartist", "Artista album"),
                    ("year", "Anno"), ("genre", "Genere"))

    def __init__(self, master: tk.Misc, items: list[PlanItem], *, px, on_save,
                 preview: Callable[[PlanItem, dict], str] | None = None):
        super().__init__(master)
        self.withdraw()
        self.items = items
        self.on_save = on_save
        self.preview = preview
        self.single = len(items) == 1
        self.configure(background=P.surface)
        self.transient(master)
        self.resizable(False, False)
        self.title("Correggi dati" if self.single else f"Correggi {len(items)} brani")

        body = ttk.Frame(self, style="Surface.TFrame", padding=px(20))
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)

        heading = "Correggi i dati del brano" if self.single else f"Correggi {len(items)} brani insieme"
        ttk.Label(body, text=heading, style="DetailTitle.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        subtitle = items[0].path.name if self.single else \
            "Le modifiche valgono per tutti i brani selezionati. I campi lasciati come sono non cambiano."
        ttk.Label(body, text=subtitle, style="CardMuted.TLabel", wraplength=px(440)).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(px(2), px(14)))

        self.vars: dict[str, tk.StringVar] = {}
        self.initial: dict[str, str] = {}
        first_entry = None
        fields = self.SINGLE_FIELDS if self.single else self.MULTI_FIELDS
        for row, (key, label) in enumerate(fields, start=2):
            values = {self._as_text(it.components.get(key)) for it in items}
            common = values.pop() if len(values) == 1 else ""
            self.initial[key] = common
            var = tk.StringVar(value=common)
            self.vars[key] = var
            ttk.Label(body, text=label, style="Card.TLabel").grid(row=row, column=0, sticky="w",
                                                                   padx=(0, px(14)), pady=px(3))
            width = 10 if key in ("track", "year") else 44
            entry = ttk.Entry(body, textvariable=var, width=width)
            entry.grid(row=row, column=1, sticky="w", pady=px(3))
            first_entry = first_entry or entry
            if not self.single and len(values) > 0:
                ttk.Label(body, text="valori diversi", style="CardMuted.TLabel").grid(
                    row=row, column=2, sticky="w", padx=(px(8), 0))
            var.trace_add("write", lambda *_: self._update_preview())

        row = 2 + len(fields)
        if self.single and preview:
            ttk.Label(body, text="NUOVO NOME FILE", style="CardSection.TLabel").grid(
                row=row, column=0, columnspan=3, sticky="w", pady=(px(14), 0))
            self.preview_label = ttk.Label(body, text="", style="Example.TLabel", wraplength=px(460))
            self.preview_label.grid(row=row + 1, column=0, columnspan=3, sticky="w")
            row += 2
        else:
            self.preview_label = None

        hint = "Svuota un campo per cancellare quel dato dal file." if self.single else \
            "Svuota un campo per cancellarlo da tutti i brani."
        ttk.Label(body, text=hint, style="CardMuted.TLabel").grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(px(12), 0))
        self.error_label = ttk.Label(body, text="", style="CardError.TLabel")
        self.error_label.grid(row=row + 1, column=0, columnspan=3, sticky="w")

        buttons = ttk.Frame(body, style="Surface.TFrame")
        buttons.grid(row=row + 2, column=0, columnspan=3, sticky="ew", pady=(px(14), 0))
        if any(it.manual for it in items):
            reset = ttk.Button(buttons, text="Ripristina dati automatici", command=self._reset)
            reset.pack(side="left")
            Tooltip(reset, "Elimina le correzioni manuali e torna ai dati trovati dall'analisi.")
        ttk.Button(buttons, text="Salva", style="Accent.TButton", command=self._save).pack(side="right")
        ttk.Button(buttons, text="Annulla", command=self.destroy).pack(side="right", padx=(0, px(8)))

        self.bind("<Return>", lambda e: self._save())
        self.bind("<Escape>", lambda e: self.destroy())
        self._update_preview()

        center_on(self, master)
        self.deiconify()
        self.wait_visibility()
        self.grab_set()
        if first_entry is not None:
            first_entry.focus_set()
            first_entry.select_range(0, "end")

    @staticmethod
    def _as_text(value) -> str:
        return "" if value is None else str(value)

    def _parse(self) -> dict | None:
        """Valori modificati dall'utente (None = cancella), o None se non validi."""
        changes = {}
        for key, var in self.vars.items():
            text = var.get().strip()
            if text == self.initial[key]:
                continue
            if key in ("track", "year") and text:
                if not text.isdigit():
                    label = "Il numero di traccia" if key == "track" else "L'anno"
                    self.error_label.config(text=f"{label} deve essere un numero.")
                    return None
                value = int(text)
                if key == "track" and not 1 <= value <= 999:
                    self.error_label.config(text="Il numero di traccia deve essere tra 1 e 999.")
                    return None
                if key == "year" and not 1000 <= value <= 2100:
                    self.error_label.config(text="Inserisci un anno a quattro cifre, es. 1995.")
                    return None
                changes[key] = value
            else:
                changes[key] = text or None
        self.error_label.config(text="")
        return changes

    def _update_preview(self):
        if self.preview_label is None or self.preview is None:
            return
        values = {}
        for key, var in self.vars.items():
            text = var.get().strip()
            if key in ("track", "year"):
                values[key] = int(text) if text.isdigit() else None
            else:
                values[key] = text or None
        name = self.preview(self.items[0], values)
        self.preview_label.config(text=name or "—")

    def _save(self):
        changes = self._parse()
        if changes is None:
            return
        for item in self.items:
            for key, value in changes.items():
                if value == item.base_components.get(key):
                    item.manual.pop(key, None)  # uguale al dato automatico: nessuna correzione
                else:
                    item.manual[key] = value
        self.destroy()
        self.on_save(self.items)

    def _reset(self):
        for item in self.items:
            item.manual.clear()
        self.destroy()
        self.on_save(self.items)


class HelpDialog(tk.Toplevel):
    """Breve guida: come funziona, formati dei nomi, stati e scorciatoie."""

    def __init__(self, master: tk.Misc, *, px, fonts):
        super().__init__(master)
        self.withdraw()
        self.title("Guida rapida")
        self.transient(master)
        self.configure(background=P.surface)
        self.resizable(False, True)

        body = ttk.Frame(self, style="Surface.TFrame")
        body.pack(fill="both", expand=True)
        text = tk.Text(body, width=74, height=20, wrap="word", relief="flat", borderwidth=0,
                       highlightthickness=0, background=P.surface, foreground=P.text,
                       font=fonts["base"], padx=px(24), pady=px(18), cursor="arrow",
                       spacing1=px(2), spacing3=px(2))
        scroll = ttk.Scrollbar(body, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)
        text.tag_configure("h", font=fonts["detail_title"], spacing1=px(12), spacing3=px(4))
        text.tag_configure("h1", font=fonts["title"], spacing3=px(6))
        text.tag_configure("muted", foreground=P.muted)
        text.tag_configure("code", font=fonts["mono"], foreground=P.accent_pressed, background=P.accent_soft)
        text.tag_configure("key", font=fonts["small_bold"], foreground=P.text, background=P.surface_alt)

        def add(*parts):
            for part in parts:
                if isinstance(part, tuple):
                    text.insert("end", part[0], part[1])
                else:
                    text.insert("end", part)

        add(("Music Organizer\n", "h1"),
            ("Corregge i metadati della tua musica e rinomina i file in modo uniforme.\n", "muted"))
        add(("Come funziona\n", "h"),
            "1.  Scegli la cartella della musica e premi ", ("Analizza", "key"),
            ". Nessun file viene toccato: viene solo preparata l'anteprima.\n",
            "2.  Controlla la tabella. I brani sono raggruppati per cartella; i filtri in alto "
            "mostrano solo quelli da modificare o da controllare.\n",
            "3.  Doppio clic su un brano (o ", ("Invio", "key"), ") per correggerlo. Seleziona più brani, "
            "o un'intera cartella, per cambiare album, artista o anno in un colpo solo.\n",
            "4.  Escludi i brani che non vuoi toccare con ", ("Canc", "key"), " o dal menu del tasto destro.\n",
            "5.  Premi ", ("Applica", "key"), ". Ogni operazione si può annullare con "
            "«Annulla ultima modifica».\n")
        text.tag_configure("dot_change", foreground=P.accent)
        text.tag_configure("dot_warn", foreground="#E39A12")
        text.tag_configure("dot_ok", foreground="#3BA272")
        text.tag_configure("dot_excluded", foreground=P.subtle)
        add(("Stati dei brani\n", "h"),
            ("●  ", "dot_change"), "Da modificare: nome o tag verranno corretti\n",
            ("●  ", "dot_warn"), "Da controllare: abbinamento online incerto, nome doppio o dati mancanti\n",
            ("●  ", "dot_ok"), "Già a posto: non verrà toccato\n",
            ("○  ", "dot_excluded"), "Escluso: hai scelto di non modificarlo\n")
        add(("Campi per i formati dei nomi\n", "h"))
        for token, desc in PATTERN_FIELDS.items():
            add((f" {token} ", "code"), f"  {desc}\n")
        add(("I campi mancanti spariscono da soli insieme ai separatori: "
             "\"{nn} - {titolo}\" senza numero traccia diventa solo il titolo.\n", "muted"))
        add(("Scorciatoie da tastiera\n", "h"))
        for keys, desc in (("Ctrl+O", "scegli la cartella"), ("F5", "analizza"), ("Ctrl+F", "cerca nella tabella"),
                           ("Invio", "correggi i brani selezionati"), ("Canc", "escludi / includi"),
                           ("Ctrl+A", "seleziona tutto"), ("Esc", "ferma l'operazione in corso")):
            add((f" {keys} ", "key"), f"  {desc}\n")
        text.configure(state="disabled")

        # altezza adatta al contenuto, senza superare lo schermo (oltre si scorre)
        text.update_idletasks()
        counted = text.count("1.0", "end", "ypixels")
        content = (counted[0] if isinstance(counted, tuple) else counted or 0) + 2 * px(18)
        linespace = fonts["base"].metrics("linespace")
        max_lines = (self.winfo_screenheight() - px(200)) // linespace
        text.configure(height=max(16, min(max_lines, content // linespace + 1)))

        bar = ttk.Frame(self, style="Surface.TFrame", padding=(px(24), 0, px(24), px(18)))
        bar.pack(fill="x")
        ttk.Button(bar, text="Chiudi", style="Accent.TButton", command=self.destroy).pack(side="right")
        self.bind("<Escape>", lambda e: self.destroy())
        center_on(self, master)
        self.deiconify()

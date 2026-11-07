## Contesto

La tua libreria musicale ha file con nomi disordinati (es. `02 - Gene Vincent - Be- Bop - A - Lula(1).mp3` con suffissi `(1)` da download duplicati) e tag mancanti o incompleti. Vuoi un'app che analizzi ogni file audio di una cartella e delle sue sottocartelle, ricavi i metadati corretti (dal nome file, dai tag esistenti o da internet), li scriva nel file, rinomini il file in modo uniforme e opzionalmente riorganizzi tutto in cartelle Artista\Album.

**Scelte confermate:** app GUI desktop (tkinter), interruttore per la riorganizzazione Artista/Album (default spento), nome file `NN - Artista - Album - Titolo.ext`.

## Setup ambiente (il PC non ha un Python funzionante)

1. `uv python install 3.13` → `uv init` + `pyproject.toml` con dipendenze: **mutagen** (lettura/scrittura tag MP3/FLAC/M4A/OGG/WMA), **requests** (ricerca online), **tkinter** è incluso nella libreria standard.
2. Avvio con doppio clic: `Avvia Music Organizer.bat` che lancia la GUI con `pythonw.exe` del venv (nessuna finestra nera di terminale).

## Struttura del progetto (cartella corrente)

```
Organize Music Metadata/
├─ main.py                    # avvia la GUI
├─ Avvia Music Organizer.bat  # doppio clic
├─ pyproject.toml
├─ organizer/
│  ├─ scanner.py              # scansione ricorsiva (.mp3 .flac .m4a .ogg .opus .wma)
│  ├─ filename_parser.py      # estrae traccia/artista/album/titolo dal nome file
│  ├─ metadata.py             # lettura/scrittura tag via mutagen (anche copertine)
│  ├─ lookup.py               # ricerca online: Deezer API (senza chiave) + MusicBrainz fallback
│  ├─ planner.py              # decide tag/nome/percorso nuovi per ogni file
│  ├─ applier.py              # applica le modifiche + diario di annullamento + rigenera .m3u
│  └─ gui.py                  # finestra tkinter in italiano
└─ tests/
   ├─ test_parser.py          # unit test del parser sui pattern reali dei tuoi screenshot
   └─ test_pipeline.py        # test end-to-end su file audio di fixture generati al volo
```

## Pipeline per ogni file

1. **Scan** ricorsivo della cartella scelta (ignora .log, .txt, .m3u ecc.).
2. **Lettura tag esistenti** (titolo, artista, album, albumartist, nº traccia, anno).
3. **Parser del nome file** — gestisce i pattern reali della tua libreria:
   - `01. - Keep Ya Head Up.flac` → traccia 1, titolo
   - `02 - Gene Vincent - Be- Bop - A - Lula(1).mp3` → traccia 2, artista Gene Vincent, titolo, e **rimuove i suffissi `(1)`**
   - `Artista - Titolo.mp3`, solo `Titolo.mp3`, e nomi già in formato `NN - Artista - Album - Titolo`
   - Pulizia di spazi doppi, punti finali, caratteri vietati su Windows
4. **Nome cartella** come indizio album: `2Pac - Greatest Hits [CD1]` → artista album = 2Pac, album = Greatest Hits, disco = CD1 (il nº disco finisce nel nome cartella: `Greatest Hits (CD1)`).
5. **Ricerca online solo per i campi mancanti** — mai sovrascrivere tag corretti esistenti:
   - Deezer (gratuita, senza chiave) poi MusicBrainz (per i brani più rari) come fallback
   - Verifica di somiglianza per evitare abbinamenti sbagliati; viene mostrato il punteggio di confidenza
6. **Compilation**: se una cartella contiene ≥3 artisti diversi (come "Greatest Hits Collection 50s") → albumartist = "Various Artists" e il file si chiama `NN - Artista - Album - Titolo` dove serve; negli album a artista singolo il formato resta comunque quello scelto `NN - Artista - Album - Titolo`.
7. **Piano finale per file**: nuovi tag, nuovo nome `NN - Artista - Album - Titolo.ext` (NN con zeri iniziali), nuovo percorso `Artista\Album\` se l'interruttore è attivo; gestione collisioni con suffisso ` (2)`.

## GUI (in italiano)

- Selezione cartella + pulsanti **Analizza** → **tabella anteprima** (file attuale → nuovo nome, titolo, artista, album, traccia, fonte del dato, confidenza) → **Applica** con barra di avanzamento
- Doppio clic su una riga per correggere manualmente artista/titolo prima di applicare
- Interruttori: **Riorganizza in Artista\Album** (spento di default), **Cerca online**, **Scarica copertine mancanti** (Da Deezer, salva cover.jpg e la incorpora nel file)
- **Annulla ultima modifica**: ogni applicazione scrive un diario JSON (percorsi originali + tag originali) che ripristina tutto
- Le playlist .m3u presenti nelle cartelle modificate vengono rigenerate con i nuovi nomi

## Verifica

1. Unit test del parser con i nomi reali dei tuoi screenshot (2Pac FLAC e compilation 50s) e casi limite.
2. Test end-to-end offline: genero MP3/FLAC minimi validi come fixture in una cartella temporanea, eseguo scan→piano→applica→annulla e verifico che tutto torni.
3. A app pronta: la punti sulla tua libreria reale in modalità anteprima (nessuna modifica finché non premi Applica) e controlliamo insieme il risultato su un album prima di applicare tutto.

**Fuori dallo scopo v1:** rilevamento duplicati per impronta audio, testi delle canzoni.
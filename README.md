# Music Organizer

App per organizzare la libreria musicale: analizza tutti i file audio di una
cartella e delle sue sottocartelle, corregge i metadati (dal nome file, dai tag
esistenti o cercandoli online) e rinomina i file in modo uniforme.

## Avvio

Doppio clic su **`Music Organizer.exe`** (autonomo: non serve installare nulla).

In alternativa:

- doppio clic su `Avvia Music Organizer.bat` (usa l'ambiente Python del progetto)
- da terminale: `uv run python main.py`

## Ricostruire l'eseguibile

Dopo modifiche al codice:

```
uv run python scripts/make_icon.py   # rigenera l'icona (opzionale)
uv run pyinstaller --noconsole --onefile --name "Music Organizer" --icon icon.ico --add-data "icon.ico;." --clean main.py
copy "dist\Music Organizer.exe" "Music Organizer.exe"
```

## Come funziona

1. **Scegli la cartella** della musica (le ultime usate sono nel menu a tendina)
   e premi **Analizza**: nessun file viene toccato, viene solo costruita l'anteprima.
2. **Controlla la tabella**: i brani sono raggruppati per cartella e ogni riga
   mostra file attuale, nuovo nome e tag proposti. Il pallino colorato indica lo stato:
   - viola **Da modificare** = nome o tag verranno corretti
   - arancione **Da controllare** = abbinamento online incerto, nome doppio o dati mancanti
   - verde **Già a posto** = non verrà toccato
   - grigio **Escluso** = hai scelto di non modificarlo

   I filtri in alto mostrano solo una categoria; la casella di ricerca filtra per
   titolo, artista, album, file o cartella; clic sulle intestazioni per ordinare.
   Il **pannello dettagli** in basso confronta, campo per campo, i tag attuali con
   quelli proposti, con la fonte di ogni dato (Tag / Nome file / Cartella /
   Internet), l'affidabilità dell'abbinamento online e gli eventuali avvisi.
3. **Correggi** (doppio clic, Invio o pulsante *Correggi…*): titolo, artista,
   album, artista album, traccia, anno e genere, con l'anteprima del nuovo nome.
   Selezionando più brani, o un'intera cartella, si cambiano album/artista/anno/genere
   di tutti in un colpo solo. Svuotare un campo cancella quel dato dal file.
4. **Escludi** (Canc o tasto destro) i brani che non vuoi toccare.
5. **Applica modifiche**: scrive i tag e rinomina i file, poi mostra un riepilogo
   con eventuali errori. Ogni operazione viene registrata in un diario: il pulsante
   **Annulla ultima modifica** ripristina file, nomi, cartelle e tag esattamente
   come prima.

### Scorciatoie

| Tasto | Azione |
|---|---|
| Ctrl+O | Scegli la cartella |
| F5 | Analizza |
| Ctrl+F | Cerca nella tabella |
| Invio / doppio clic | Correggi i brani selezionati |
| Canc | Escludi / includi |
| Esc | Ferma l'operazione in corso |
| F1 | Guida rapida |

## Opzioni

| Opzione | Effetto |
|---|---|
| Nome file: formato | Menu a tendina con i formati pronti (`{nn} - {artista} - {album} - {titolo}`, `{nn} - {titolo}`, `{titolo}`, …) oppure scrivi il tuo pattern. Campi disponibili: `{nn}` numero traccia, `{artista}`, `{album}`, `{titolo}`, `{anno}`, `{disco}`. I campi mancanti spariscono da soli. |
| Riorganizza in `Artista\Album` | Sposta i file in `Artista\Album\` (i CD multipli diventano `Album (CD1)`). Le compilation vanno sotto `Various Artists`. Disattivato di default. |
| Cartelle: Rinomina le cartelle album | Rinomina le cartelle in base ai metadati corretti (es. `2Pac - Greatest Hits [CD1]` → `2Pac - Greatest Hits (CD1)`). Anche le cartelle hanno un formato personalizzabile. I file non audio (.log, .txt, ecc.) restano dentro. Disattivato di default; non attivo insieme alla riorganizzazione. |
| Cerca dati mancanti online | Compila SOLO i campi mancanti (mai sovrascrivere dati corretti). Fonti: Deezer, poi MusicBrainz per i brani rari. Senza connessione l'analisi prosegue comunque e un avviso lo segnala. |
| Scarica copertine mancanti | Se l'album non ha copertina incorporata né `cover.jpg`, la scarica e la incorpora nei file (MP3/FLAC/M4A) e salva `cover.jpg` nella cartella. |

Cambiando formato o opzioni **dopo** l'analisi, l'anteprima si aggiorna all'istante
senza riscansionare la libreria né rifare le ricerche online.

## Formato dei nomi

Predefinito: `{nn} - {artista} - {album} - {titolo}` → `01 - 2Pac - Greatest Hits - Keep Ya Head Up.flac`.
Il numero di traccia viene omesso se sconosciuto. Nelle cartelle album multi-CD il
disco (`CD1`, `CD2`…) viene mantenuto anche se il formato non lo prevede, per non
fondere i dischi. La playlist `.m3u` eventualmente presente viene aggiornata.

## Sicurezza

- Nessuna modifica finché non premi **Applica modifiche**.
- Collisioni di nome gestite con suffisso ` (2)`, segnalate in anteprima.
- I formati dei nomi vengono validati: campi sconosciuti, caratteri non ammessi
  o formati senza `{titolo}` (file) / `{album}` (cartelle) bloccano l'applicazione.
- Chiudendo la finestra durante l'applicazione, l'app termina il file in corso
  e salva il diario, così quanto fatto resta annullabile.
- Le cartelle di sistema e i file non audio (.log, .txt, ecc.) vengono ignorati.
- Diario annullamento: `%LOCALAPPDATA%\OrganizeMusicMetadata\undo.json`
  (dopo un annullamento riuscito viene archiviato come `undo-annullato.json`).
- Errori imprevisti: `%LOCALAPPDATA%\OrganizeMusicMetadata\errori.log`.

## Formati supportati

MP3, FLAC, M4A/AAC, OGG, OPUS, WMA, WAV.
Copertine incorporabili: MP3, FLAC, M4A, WAV.

## Test

```
uv run pytest tests/
```

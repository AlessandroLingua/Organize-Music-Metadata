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
uv run pyinstaller --noconsole --onefile --name "Music Organizer" --icon icon.ico --clean main.py
copy "dist\Music Organizer.exe" "Music Organizer.exe"
```

## Come funziona

1. **Scegli la cartella** della musica e premi **Analizza**: nessun file viene
   toccato, viene solo costruita l'anteprima.
2. **Controlla la tabella**: ogni riga mostra il file attuale, il nuovo nome,
   i tag proposti, la fonte del dato (Tag / Nome file / Cartella / Internet) e
   la confidenza dell'eventuale abbinamento online.
   - righe verdi `✓ ok` = già a posto, non verranno toccate
   - righe `✎ modifiche` = verranno corrette
   - righe `⚠ controlla` = abbinamento online incerto o problemi: dai un'occhiata
3. **Doppio clic su una riga** per correggere manualmente titolo, artista,
   album, numero di traccia o anno prima di applicare.
4. **Applica modifiche**: scrive i tag e rinomina i file. Ogni operazione viene
   registrata in un diario: il pulsante **Annulla ultima modifica** ripristina
   file, nomi e tag esattamente come prima.

## Opzioni

| Opzione | Effetto |
|---|---|
| Nome file: formato | Menu a tendina con i formati pronti (`{nn} - {artista} - {album} - {titolo}`, `{nn} - {titolo}`, `{titolo}`, …) oppure scrivi il tuo pattern. Campi disponibili: `{nn}` numero traccia, `{artista}`, `{album}`, `{titolo}`, `{anno}`, `{disco}`. I campi mancanti spariscono da soli. |
| Riorganizza in `Artista\Album` | Sposta i file in `Artista\Album\` (i CD multipli diventano `Album (CD1)`). Le compilation vanno sotto `Various Artists`. Disattivato di default. |
| Cartelle: Rinomina le cartelle album | Rinomina le cartelle in base ai metadati corretti (es. `2Pac - Greatest Hits [CD1]` → `2Pac - Greatest Hits (CD1)`). Anche le cartelle hanno un formato personalizzabile. I file non audio (.log, .txt, ecc.) restano dentro. Disattivato di default; non attivo insieme alla riorganizzazione. |
| Cerca dati mancanti online | Compila SOLO i campi mancanti (mai sovrascrivere dati corretti). Fonti: Deezer, poi MusicBrainz per i brani rari. |
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
- Collisioni di nome gestite con suffisso ` (2)`.
- Le cartelle di sistema e i file non audio (.log, .txt, ecc.) vengono ignorati.
- Diario annullamento: `%LOCALAPPDATA%\OrganizeMusicMetadata\undo.json`.

## Formati supportati

MP3, FLAC, M4A/AAC, OGG, OPUS, WMA, WAV.
Copertine incorporabili: MP3, FLAC, M4A, WAV.

## Test

```
uv run pytest tests/
```

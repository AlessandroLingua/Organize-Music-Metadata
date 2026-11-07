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

## Conversione in altri formati

Il pulsante **Converti formato…** (in basso, o *Converti in un altro formato…* dal
tasto destro sui brani) converte i brani selezionati, o tutta la cartella, nel
formato e nella qualità che scegli. La colonna **Formato** della tabella mostra il
formato attuale di ogni brano (es. `FLAC 24/96`, `MP3 320 kbps`; ordinabile e
cercabile), e il pannello dettagli lo descrive per esteso con la durata.

| Formato | Qualità disponibili |
|---|---|
| MP3 | 320 / 256 / 192 / 128 kbps, VBR V0 (~245 kbps), VBR V2 (~190 kbps) |
| AAC (.m4a) | 320 / 256 / 192 / 128 kbps |
| Opus | 256 / 192 / 160 / 128 / 96 kbps |
| OGG Vorbis | Q10 (~500 kbps), Q8 (~256), Q6 (~192), Q4 (~128) |
| FLAC, ALAC (.m4a), WAV | Come l'originale · 16 bit / 44,1 kHz (qualità CD) · 24 bit fino a 48 kHz |

- **Destinazione**:
  - *cartella separata* con le stesse sottocartelle (proposta: `Musica (MP3)` accanto
    alla cartella della musica; ideale per telefono o autoradio);
  - *accanto agli originali*, che restano intatti;
  - *al posto degli originali, senza doppioni*: ogni originale viene spostato nel
    **Cestino** di Windows solo dopo che la sua conversione è riuscita (e si recupera
    da lì). Funziona anche con la stessa estensione, es. MP3 320 → MP3 128 sul posto.
    Non disponibile su unità di rete o rimovibili, che non hanno il Cestino.
    «Annulla ultima modifica» non riguarda la conversione.
- **Tag e copertine** vengono copiati nei nuovi file; se un brano non ha copertina
  incorporata viene usata quella della cartella (`cover.jpg`…), che viene copiata
  anche nella cartella di destinazione.
- I file **già nel formato scelto** con qualità uguale o inferiore non vengono
  ricodificati, perché ne uscirebbero peggiorati: nella cartella separata vengono
  copiati così come sono, al posto degli originali restano come sono (opzione attiva
  di default).
- I file già presenti nella destinazione vengono saltati, così una conversione
  interrotta si riprende da dove si era fermata (oppure attiva *Sostituisci i file già
  presenti*). Nella cartella della musica un file con lo stesso nome non viene mai
  sovrascritto: per esempio, se esistono sia `brano.flac` sia `brano.mp3`, il FLAC
  viene saltato invece di creare un `brano (2).mp3`.
- I file ad alta risoluzione vengono portati a 48/44,1 kHz nei formati compressi;
  da un MP3 i formati lossless non vanno oltre i 16 bit (non ci sarebbe nulla da guadagnare).
- Le conversioni vengono eseguite in parallelo su più core; **Ferma** (o Esc)
  interrompe scartando i file a metà.

La conversione usa **FFmpeg** (gratuito). L'app lo cerca accanto all'eseguibile,
nel PATH e nelle installazioni comuni (winget, Chocolatey, Scoop, `C:\ffmpeg`).
Se manca, il modo più semplice per installarlo è:

```
winget install Gyan.FFmpeg
```

oppure copiare `ffmpeg.exe` nella cartella di Music Organizer.

## Brani doppi

Il pulsante **Trova duplicati…** (in basso) raggruppa le copie dello stesso brano
presenti nella cartella analizzata, anche in formati diversi (es. lo stesso brano in
FLAC e in MP3, o un `Brano(1).mp3` scaricato due volte).

- **Quando due file sono lo stesso brano**: stesso artista e titolo (ignorando
  maiuscole, accenti, punteggiatura e i «feat.»), durata simile (±3 secondi) e stesso
  album, se indicato. Versioni diverse come *(Live)* o *(Remix)* restano separate.
  Con *Cerca anche tra album diversi* vengono trovati anche i brani ripetuti tra un
  album e una raccolta. Senza artista, i titoli vengono confrontati solo nella stessa
  cartella (per non unire tutte le «Intro»).
- **Quale copia tenere**: per ogni gruppo la scelta è automatica secondo il criterio
  selezionato:
  - *La qualità migliore*: lossless prima dei formati compressi; tra i lossless
    più bit e più kHz; tra i compressi il bitrate, tenendo conto che Opus/AAC/OGG
    rendono meglio dell'MP3 a parità di kbps. A parità di qualità vince la copia con i
    tag più completi e senza `(1)` o `copia` nel nome;
  - *Il file più leggero*, per risparmiare spazio;
  - *Il formato FLAC / MP3 / …, se c'è* (altrimenti la qualità migliore).
- **Scelta a mano**: doppio clic (o Invio) su una copia = *Tieni solo questa*;
  la barra spaziatrice tiene o scarta una singola copia (si possono tenere più copie,
  ma almeno una resta sempre); *Tieni tutto il gruppo* lo ignora. *▶ Ascolta* apre la
  copia nel lettore predefinito per confrontarle.
- **Sposta nel Cestino** sposta le copie scartate nel Cestino di Windows, da cui si
  possono recuperare. Non disponibile su unità di rete o rimovibili, che non hanno il
  Cestino. Se l'analisi non è ancora stata fatta, l'app la propone prima di cercare.

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
| Scarica copertine mancanti | Se l'album non ha copertina incorporata né `cover.jpg`, la scarica e la incorpora nei file (MP3/FLAC/M4A/OGG/Opus/WAV) e salva `cover.jpg` nella cartella. |

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

MP3, FLAC, M4A/AAC/ALAC, OGG, OPUS, WMA, WAV.
Copertine incorporabili: MP3, FLAC, M4A, OGG, OPUS, WAV.

## Test

```
uv run pytest tests/
```

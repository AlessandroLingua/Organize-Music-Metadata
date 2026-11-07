"""Ricerca dei metadati online: Deezer (gratuita, senza chiave) + MusicBrainz di riserva.

Restituisce solo abbinamenti plausibili, con un punteggio di confidenza 0..1;
il chiamante decide la soglia di accettazione.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import requests

from .filename_parser import similar

DEEZER_API = "https://api.deezer.com"
MB_API = "https://musicbrainz.org/ws/2"
USER_AGENT = "OrganizeMusicMetadata/1.0 (personal library organizer)"
TIMEOUT = 10
# dopo questi errori di rete consecutivi la ricerca online viene sospesa,
# cosi senza connessione l'analisi non resta bloccata sui timeout
MAX_NETWORK_FAILURES = 3
DEEZER_MIN_INTERVAL = 0.12  # Deezer accetta ~50 richieste ogni 5 secondi


@dataclass
class LookupResult:
    title: str
    artist: str
    album: str | None
    year: int | None
    track: int | None
    genre: str | None
    cover_url: str | None
    album_id: str | None
    confidence: float
    source: str  # "deezer" | "musicbrainz"


class LookupClient:
    """Client con cache: una stessa ricerca (o album) non viene ripetuta."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self._search_cache: dict[tuple, LookupResult | None] = {}
        self._album_cache: dict[str, dict] = {}
        self._last_mb_request = 0.0
        self._last_deezer_request = 0.0
        self._failures = 0
        self.offline = False  # True se la rete non risponde: niente piu ricerche

    def _network_ok(self) -> None:
        self._failures = 0

    def _network_failed(self) -> None:
        self._failures += 1
        if self._failures >= MAX_NETWORK_FAILURES:
            self.offline = True

    # -- Deezer ---------------------------------------------------------------

    def _deezer_get(self, url: str, params: dict | None = None) -> dict:
        """GET verso Deezer con rispetto del limite di richieste.

        Se Deezer segnala il superamento della quota (errore 4) attende e riprova
        una volta. Gli errori di rete vengono propagati al chiamante.
        """
        for attempt in range(2):
            elapsed = time.monotonic() - self._last_deezer_request
            if elapsed < DEEZER_MIN_INTERVAL:
                time.sleep(DEEZER_MIN_INTERVAL - elapsed)
            self._last_deezer_request = time.monotonic()
            resp = self.session.get(url, params=params, timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            error = data.get("error") if isinstance(data, dict) else None
            if isinstance(error, dict) and error.get("code") == 4 and attempt == 0:
                time.sleep(5)
                continue
            return data if isinstance(data, dict) else {}
        return {}

    def _deezer_search(self, query: str):
        data = self._deezer_get(f"{DEEZER_API}/search/track", {"q": query, "limit": 10})
        return data.get("data", []) or []

    def _score_deezer_item(self, item: dict, artist: str | None, title: str | None, album: str | None) -> float:
        res_artist = (item.get("artist") or {}).get("name") or ""
        res_title = item.get("title") or ""
        res_album = (item.get("album") or {}).get("title") or ""
        if artist and title:
            score = 0.55 * similar(res_artist, artist) + 0.45 * similar(res_title, title)
        elif title:
            score = 0.85 * similar(res_title, title)
        else:
            score = 0.3
        if album:
            score = 0.8 * score + 0.2 * similar(res_album, album)
        return score

    def _deezer_lookup(self, artist: str | None, title: str | None, album: str | None) -> LookupResult | None:
        query = " ".join(x for x in (artist, title) if x)
        if not query:
            return None
        items = self._deezer_search(query)
        best, best_score = None, 0.0
        for item in items:
            score = self._score_deezer_item(item, artist, title, album)
            if score > best_score:
                best, best_score = item, score
        if not best:
            return None

        album_id = str((best.get("album") or {}).get("id") or "") or None
        album_info = self.album_info(album_id) if album_id else {}
        return LookupResult(
            title=best.get("title") or "",
            artist=(best.get("artist") or {}).get("name") or "",
            album=(best.get("album") or {}).get("title") or album,
            year=album_info.get("year"),
            track=best.get("track_position") or None,
            genre=album_info.get("genre"),
            cover_url=album_info.get("cover_url"),
            album_id=album_id,
            confidence=best_score,
            source="deezer",
        )

    def album_info(self, album_id: str) -> dict:
        """Anno / genere / copertina di un album Deezer (con cache)."""
        if album_id in self._album_cache:
            return self._album_cache[album_id]
        info: dict = {}
        try:
            data = self._deezer_get(f"{DEEZER_API}/album/{album_id}")
            if data and not data.get("error"):
                year = None
                release = data.get("release_date") or ""
                if len(release) >= 4 and release[:4].isdigit():
                    year = int(release[:4])
                genres = (data.get("genres") or {}).get("data") or []
                info = {
                    "year": year,
                    "genre": genres[0].get("name") if genres else None,
                    "cover_url": data.get("cover_xl") or data.get("cover_big"),
                }
        except Exception:
            pass
        self._album_cache[album_id] = info
        return info

    # -- MusicBrainz (riserva, max ~1 richiesta al secondo) --------------------

    def _mb_rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_mb_request
        if elapsed < 1.05:
            time.sleep(1.05 - elapsed)
        self._last_mb_request = time.monotonic()

    def _mb_lookup(self, artist: str | None, title: str | None) -> LookupResult | None:
        """Ricerca su MusicBrainz. Gli errori di rete vengono propagati."""
        if not title:
            return None

        def quoted(value: str) -> str:
            # le virgolette interne romperebbero la query Lucene
            return '"' + value.replace("\\", " ").replace('"', " ") + '"'

        clauses = [f"recording:{quoted(title)}"]
        if artist:
            clauses.append(f"artist:{quoted(artist)}")
        self._mb_rate_limit()
        resp = self.session.get(
            f"{MB_API}/recording",
            params={"fmt": "json", "limit": 5, "query": " AND ".join(clauses)},
            timeout=TIMEOUT,
        )
        if resp.status_code >= 500 or resp.status_code == 429:
            raise requests.HTTPError(f"MusicBrainz HTTP {resp.status_code}")
        if not resp.ok:
            return None
        try:
            recordings = resp.json().get("recordings", []) or []
        except ValueError:
            return None

        best, best_score = None, 0.0
        for rec in recordings:
            res_title = rec.get("title") or ""
            credit = (rec.get("artist-credit") or [{}])[0]
            res_artist = (credit.get("artist") or credit).get("name") if isinstance(credit, dict) else str(credit)
            res_artist = res_artist or ""
            score = 0.55 * similar(res_artist, artist or "") + 0.45 * similar(res_title, title)
            if score > best_score:
                release_date = rec.get("first-release-date") or ""
                releases = rec.get("releases") or []
                best = LookupResult(
                    title=res_title,
                    artist=res_artist,
                    album=releases[0].get("title") if releases else None,
                    year=int(release_date[:4]) if len(release_date) >= 4 and release_date[:4].isdigit() else None,
                    track=None,
                    genre=None,
                    cover_url=None,
                    album_id=None,
                    confidence=score,
                    source="musicbrainz",
                )
                best_score = score
        return best

    # -- API pubblica -----------------------------------------------------------

    def lookup(self, artist: str | None, title: str | None, album: str | None = None) -> LookupResult | None:
        """Cerca un brano; restituisce il miglior abbinamento o None.

        Non solleva mai eccezioni: senza rete restituisce None e, dopo alcuni
        errori consecutivi, imposta `offline` e smette di provare.
        """
        if not self.enabled or self.offline or not (artist or title):
            return None
        key = (artist or "", title or "", album or "")
        if key in self._search_cache:
            return self._search_cache[key]

        result = None
        failed = False
        try:
            result = self._deezer_lookup(artist, title, album)
            self._network_ok()
        except requests.RequestException:
            failed = True
            self._network_failed()
        except ValueError:
            pass  # risposta non valida: si prova con MusicBrainz

        if (result is None or result.confidence < 0.6) and title and not self.offline:
            try:
                mb = self._mb_lookup(artist, title)
                self._network_ok()
                failed = False
                if mb and (result is None or mb.confidence > result.confidence):
                    result = mb
            except requests.RequestException:
                failed = True
                self._network_failed()
            except ValueError:
                pass

        if not failed:
            self._search_cache[key] = result  # i fallimenti di rete non vanno in cache
        return result

    def fetch_cover(self, url: str) -> bytes | None:
        """Scarica l'immagine di copertina dall'URL."""
        try:
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
            if len(resp.content) > 64:
                return resp.content
        except Exception:
            pass
        return None

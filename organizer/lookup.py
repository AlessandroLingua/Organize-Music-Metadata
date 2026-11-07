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

    # -- Deezer ---------------------------------------------------------------

    def _deezer_search(self, query: str):
        resp = self.session.get(
            f"{DEEZER_API}/search/track",
            params={"q": query, "limit": 10},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json().get("data", []) or []

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
            resp = self.session.get(f"{DEEZER_API}/album/{album_id}", timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict) and not data.get("error"):
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
        if not title:
            return None
        clauses = [f'recording:"{title}"']
        if artist:
            clauses.append(f'artist:"{artist}"')
        self._mb_rate_limit()
        try:
            resp = self.session.get(
                f"{MB_API}/recording",
                params={"fmt": "json", "limit": 5, "query": " AND ".join(clauses)},
                timeout=TIMEOUT,
            )
            resp.raise_for_status()
        except Exception:
            return None

        best, best_score = None, 0.0
        for rec in resp.json().get("recordings", []) or []:
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
        """Cerca un brano; restituisce il miglior abbinamento o None."""
        if not self.enabled or not (artist or title):
            return None
        key = (artist or "", title or "", album or "")
        if key in self._search_cache:
            return self._search_cache[key]
        result = self._deezer_lookup(artist, title, album)
        if (result is None or result.confidence < 0.6) and title:
            mb = self._mb_lookup(artist, title)
            if mb and (result is None or mb.confidence > result.confidence):
                result = mb
        self._search_cache[key] = result
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

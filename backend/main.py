import asyncio
import os
from datetime import datetime
from typing import List

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from schemas import Track, TrackSource

app = FastAPI(title="Full-Track Music Aggregator", version="0.1.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Environment / API keys (never exposed to clients)
JAMENDO_CLIENT_ID = os.getenv("JAMENDO_CLIENT_ID", "")
SOUNDCLOUD_CLIENT_ID = os.getenv("SOUNDCLOUD_CLIENT_ID", "")
AUDIOMACK_API_KEY = os.getenv("AUDIOMACK_API_KEY", "")

# Providers that are metadata-only (never used for playback)
METADATA_ONLY_PROVIDERS = {"spotify", "deezer", "youtube"}


# Helper scoring

def score_source(s: TrackSource) -> float:
    score = 0.0
    if s.streamable or s.playable:
        score += 100
    if s.cors_support:
        score += 10
    if s.bitrate:
        score += min(s.bitrate / 32, 10)
    if s.duration:
        score += 5
    if s.license and any(tag in (s.license or "").lower() for tag in ["cc", "creative", "public", "jamendo"]):
        score += 5
    if s.audiodownload_allowed or s.downloadable:
        score += 2
    return score


def normalize_and_filter_sources(sources: List[TrackSource], allow_metadata_only: bool = False) -> List[TrackSource]:
    filtered: List[TrackSource] = []
    for s in sources:
        provider = s.provider_name
        if provider in METADATA_ONLY_PROVIDERS and not allow_metadata_only:
            continue
        if not (s.stream_url or s.download_url):
            continue
        if (s.streamable is False) or (s.playable is False):
            continue
        if (s.streamable is None and s.playable is None) and not allow_metadata_only:
            continue
        filtered.append(s)
    return filtered


async def search_jamendo(query: str) -> List[Track]:
    if not JAMENDO_CLIENT_ID:
        return []
    url = "https://api.jamendo.com/v3.0/tracks"
    params = {
        "client_id": JAMENDO_CLIENT_ID,
        "format": "json",
        "limit": 10,
        "search": query,
        "include": "licenses+musicinfo+stats",
        "audioformat": "mp32",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        data = r.json()
    results = []
    for t in data.get("results", []):
        license_name = t.get("license") or (t.get("licenses", {}).get("cc", {}).get("name") if isinstance(t.get("licenses"), dict) else None)
        stream_url = t.get("audio")
        download_url = t.get("audiodownload") if t.get("audiodownload_allowed") else None
        source = TrackSource(
            provider_name="jamendo",
            source_id=str(t.get("id")),
            stream_url=stream_url,
            download_url=download_url,
            streamable=bool(stream_url),
            playable=bool(stream_url),
            audiodownload_allowed=bool(t.get("audiodownload_allowed")),
            zip_allowed=bool(t.get("zip_allowed")),
            downloadable=bool(download_url),
            license=license_name,
            bitrate=None,
            duration=t.get("duration") or None,
            cors_support=True,
        )
        track = Track(
            title=t.get("name"),
            artist=(t.get("artist_name") or None),
            duration=t.get("duration") or None,
            cover_url=(t.get("image") or None),
            sources=[source],
        )
        results.append(track)
    return results


async def search_soundcloud(query: str) -> List[Track]:
    if not SOUNDCLOUD_CLIENT_ID:
        return []
    url = "https://api-v2.soundcloud.com/search/tracks"
    params = {
        "q": query,
        "client_id": SOUNDCLOUD_CLIENT_ID,
        "limit": 10,
        "app_version": "1700000000",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        data = r.json()
    results: List[Track] = []
    for t in data.get("collection", []):
        if not (t.get("streamable") or t.get("media")):
            continue
        media = t.get("media", {})
        transcodings = media.get("transcodings", [])
        stream_url = None
        for tr in transcodings:
            if tr.get("format", {}).get("protocol") in {"progressive", "hls"} and tr.get("url"):
                stream_url = f"{tr['url']}?client_id={SOUNDCLOUD_CLIENT_ID}"
                break
        if not stream_url:
            continue
        source = TrackSource(
            provider_name="soundcloud",
            source_id=str(t.get("id")),
            stream_url=stream_url,
            download_url=None,
            streamable=True,
            playable=True,
            license=t.get("license"),
            duration=int(t.get("duration")/1000) if t.get("duration") else None,
            cors_support=True,
        )
        track = Track(
            title=t.get("title"),
            artist=(t.get("user", {}).get("username") or None),
            duration=int(t.get("duration")/1000) if t.get("duration") else None,
            cover_url=(t.get("artwork_url") or None),
            sources=[source],
        )
        results.append(track)
    return results


async def search_audiomack(query: str) -> List[Track]:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get("https://api.audiomack.com/v1/search", params={"query": query, "limit": 10})
        if r.status_code != 200:
            return []
        data = r.json()
    results: List[Track] = []
    for t in data.get("results", {}).get("songs", []):
        streaming = t.get("streaming") or {}
        stream_url = streaming.get("url")
        if not stream_url:
            continue
        source = TrackSource(
            provider_name="audiomack",
            source_id=str(t.get("id")),
            stream_url=stream_url,
            playable=True,
            streamable=True,
            license=t.get("license"),
            duration=t.get("duration"),
            cors_support=True,
        )
        track = Track(
            title=t.get("title"),
            artist=t.get("artist"),
            duration=t.get("duration"),
            cover_url=t.get("image"),
            sources=[source],
        )
        results.append(track)
    return results


async def search_internet_archive(query: str) -> List[Track]:
    params = {
        "q": f"{query} AND mediatype:(audio)",
        "fl[]": ["identifier", "title", "creator", "licenseurl"],
        "rows": 10,
        "output": "json",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get("https://archive.org/advancedsearch.php", params=params)
        if r.status_code != 200:
            return []
        data = r.json()
    results: List[Track] = []
    for doc in data.get("response", {}).get("docs", []):
        identifier = doc.get("identifier")
        if not identifier:
            continue
        stream_url = f"https://archive.org/download/{identifier}/{identifier}.mp3"
        source = TrackSource(
            provider_name="internet_archive",
            source_id=identifier,
            stream_url=stream_url,
            playable=True,
            streamable=True,
            license=doc.get("licenseurl"),
            cors_support=True,
        )
        track = Track(
            title=doc.get("title"),
            artist=doc.get("creator"),
            cover_url=None,
            sources=[source],
        )
        results.append(track)
    return results


class SearchResponse(BaseModel):
    results: List[Track]


@app.get("/health")
async def health():
    return {"ok": True, "ts": datetime.utcnow().isoformat()}


@app.get("/search", response_model=SearchResponse)
async def search_tracks(q: str = Query(..., min_length=2), allow_metadata_only: bool = False):
    provider_calls = [
        search_jamendo(q),
        search_soundcloud(q),
        search_audiomack(q),
        search_internet_archive(q),
    ]
    try:
        results_nested = await asyncio.gather(*provider_calls, return_exceptions=True)
    except Exception:
        results_nested = []
    merged: List[Track] = []
    for provider_results in results_nested:
        if isinstance(provider_results, Exception) or not provider_results:
            continue
        for tr in provider_results:
            tr.sources = normalize_and_filter_sources(tr.sources, allow_metadata_only=allow_metadata_only)
            if not tr.sources and not allow_metadata_only:
                continue
            if tr.sources:
                scores = [score_source(s) for s in tr.sources]
                best_idx = max(range(len(scores)), key=lambda i: scores[i])
                tr.best_source_index = best_idx
            merged.append(tr)
    return {"results": merged}


@app.get("/stream")
async def stream_proxy(url: str, provider: str):
    if provider in METADATA_ONLY_PROVIDERS:
        raise HTTPException(status_code=403, detail="Provider is metadata-only; playback not permitted")
    # For demo purposes we just return the original URL. In production, sign/stream.
    return {"proxied_url": url}


@app.get("/legal")
async def legal_note():
    return {
        "warning": (
            "Do not use YouTube, Spotify, or Deezer to serve full audio streams when their APIs or TOS forbid it. "
            "They commonly provide preview clips or enforce ad delivery. Use them for metadata only."
        )
    }

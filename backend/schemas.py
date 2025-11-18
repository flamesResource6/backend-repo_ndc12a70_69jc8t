from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from datetime import datetime

# Each Pydantic model maps to a MongoDB collection (lowercased class name)

class TrackSource(BaseModel):
    provider_name: Literal[
        "jamendo",
        "soundcloud",
        "audiomack",
        "internet_archive",
        "user_upload",
        "spotify",
        "deezer",
        "youtube",
        "other",
    ]
    source_id: Optional[str] = None
    stream_url: Optional[str] = None
    download_url: Optional[str] = None
    streamable: Optional[bool] = None
    playable: Optional[bool] = None
    audiodownload_allowed: Optional[bool] = None
    zip_allowed: Optional[bool] = None
    downloadable: Optional[bool] = None
    license: Optional[str] = None
    bitrate: Optional[int] = None
    duration: Optional[int] = None
    cors_support: Optional[bool] = None
    region: Optional[str] = None

class Track(BaseModel):
    title: str
    artist: Optional[str] = None
    album: Optional[str] = None
    duration: Optional[int] = None
    cover_url: Optional[str] = None
    sources: List[TrackSource] = Field(default_factory=list)
    best_source_index: Optional[int] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

class CreateDownloadRequest(BaseModel):
    track_id: str
    source_index: int

class AuditLog(BaseModel):
    action: Literal["stream_start", "stream_end", "download", "search"]
    track_title: Optional[str] = None
    provider_name: Optional[str] = None
    license: Optional[str] = None
    user_id: Optional[str] = None
    ts: datetime = Field(default_factory=datetime.utcnow)

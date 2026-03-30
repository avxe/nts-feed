from __future__ import annotations

from datetime import datetime
from typing import List

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# Association tables
track_artists = Table(
    "track_artists",
    Base.metadata,
    Column("track_id", ForeignKey("tracks.id", ondelete="CASCADE"), primary_key=True),
    Column("artist_id", ForeignKey("artists.id", ondelete="CASCADE"), primary_key=True, index=True),
)


class Show(Base):
    __tablename__ = "shows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False, server_default=text("''"))
    description: Mapped[str] = mapped_column(String(2048), nullable=False, server_default=text("''"))
    thumbnail: Mapped[str] = mapped_column(String(1024), nullable=False, server_default=text("''"))
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    auto_download: Mapped[bool] = mapped_column(Boolean, default=False)

    episodes: Mapped[List["Episode"]] = relationship('Episode', back_populates="show", cascade="all, delete-orphan")
    hosts: Mapped[List["ShowHost"]] = relationship('ShowHost', back_populates="show", cascade="all, delete-orphan")


class Host(Base):
    __tablename__ = "hosts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)

    show_links: Mapped[List[ShowHost]] = relationship(back_populates="host", cascade="all, delete-orphan")


class ShowHost(Base):
    __tablename__ = "show_hosts"
    __table_args__ = (UniqueConstraint("show_id", "host_id", name="uq_show_host"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    show_id: Mapped[int] = mapped_column(ForeignKey("shows.id", ondelete="CASCADE"))
    host_id: Mapped[int] = mapped_column(ForeignKey("hosts.id", ondelete="CASCADE"))

    show: Mapped["Show"] = relationship('Show', back_populates="hosts")
    host: Mapped["Host"] = relationship('Host', back_populates="show_links")


class Episode(Base):
    __tablename__ = "episodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    show_id: Mapped[int] = mapped_column(ForeignKey("shows.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    date: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    image_url: Mapped[str] = mapped_column(String(1024), nullable=False, server_default=text("''"))
    audio_url: Mapped[str] = mapped_column(String(1024), nullable=False, server_default=text("''"))

    show: Mapped["Show"] = relationship('Show', back_populates="episodes")
    tracks: Mapped[List["EpisodeTrack"]] = relationship('EpisodeTrack', back_populates="episode", cascade="all, delete-orphan")
    genres: Mapped[List["EpisodeGenre"]] = relationship('EpisodeGenre', back_populates="episode", cascade="all, delete-orphan")


class Genre(Base):
    __tablename__ = "genres"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(256), unique=True, nullable=False)


class Artist(Base):
    __tablename__ = "artists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    
    # MusicBrainz metadata
    mbid: Mapped[str] = mapped_column(String(36), nullable=True, index=True)  # MusicBrainz UUID
    disambiguation: Mapped[str] = mapped_column(String(512), nullable=True)  # e.g. "British electronic duo"
    mb_type: Mapped[str] = mapped_column(String(32), nullable=True)  # Person, Group, Orchestra, Choir, etc.
    country: Mapped[str] = mapped_column(String(2), nullable=True)  # ISO 3166-1 alpha-2 country code
    mb_fetched_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)  # When MB data was last fetched
    
    # Enrichment tracking - set whenever enrichment is attempted (regardless of success)
    # This prevents re-checking artists that were already checked but had no genres found
    enrichment_attempted_at: Mapped[datetime] = mapped_column(DateTime, nullable=True, index=True)
    
    aliases: Mapped[List["ArtistAlias"]] = relationship('ArtistAlias', back_populates="artist", cascade="all, delete-orphan")
    relationships_as_source: Mapped[List["ArtistRelationship"]] = relationship(
        'ArtistRelationship',
        back_populates="artist",
        cascade="all, delete-orphan",
        foreign_keys="ArtistRelationship.artist_id"
    )


class ArtistAlias(Base):
    __tablename__ = "artist_aliases"
    __table_args__ = (UniqueConstraint("artist_id", "name", name="uq_artist_alias_per_artist"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    artist_id: Mapped[int] = mapped_column(ForeignKey("artists.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(512), nullable=False)

    artist: Mapped["Artist"] = relationship('Artist', back_populates="aliases")


class ArtistGenre(Base):
    """Cached artist genre data from external APIs (Last.fm, Discogs, MusicBrainz).
    
    This table stores genre information for artists fetched from external APIs,
    enabling intelligent track-to-genre assignment based on artist affinity
    rather than just episode-level genre tags.
    """
    __tablename__ = "artist_genres"
    __table_args__ = (
        UniqueConstraint("artist_id", "genre", "source", name="uq_artist_genre_source"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    artist_id: Mapped[int] = mapped_column(ForeignKey("artists.id", ondelete="CASCADE"), index=True)
    genre: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    weight: Mapped[float] = mapped_column(Float, default=1.0)  # Confidence/relevance score (0.0-1.0)
    source: Mapped[str] = mapped_column(String(32), default="lastfm")  # lastfm, discogs, musicbrainz
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    artist: Mapped["Artist"] = relationship('Artist', backref="cached_genres")


class ArtistRelationship(Base):
    """Artist-to-artist relationships from MusicBrainz.
    
    Stores relationships like:
    - member of band (person → group)
    - collaboration (artist ↔ artist)  
    - supporting musician
    - producer
    - remix artist
    """
    __tablename__ = "artist_relationships"
    __table_args__ = (
        UniqueConstraint(
            "artist_id", "related_artist_mbid", "relationship_type", "direction",
            name="uq_artist_relationship"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    artist_id: Mapped[int] = mapped_column(ForeignKey("artists.id", ondelete="CASCADE"), index=True)
    
    # Related artist info - may or may not be in our database
    related_artist_id: Mapped[int] = mapped_column(
        ForeignKey("artists.id", ondelete="SET NULL"), nullable=True, index=True
    )
    related_artist_mbid: Mapped[str] = mapped_column(String(36), nullable=True, index=True)
    related_artist_name: Mapped[str] = mapped_column(String(512), nullable=False)
    
    # Relationship metadata
    relationship_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(16), default="forward")  # forward, backward
    attributes: Mapped[str] = mapped_column(JSON, nullable=True)  # e.g. {"begin": "1995", "end": "2003"}
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    # Relationships
    artist: Mapped["Artist"] = relationship(
        'Artist',
        back_populates="relationships_as_source",
        foreign_keys=[artist_id]
    )
    related_artist: Mapped["Artist"] = relationship('Artist', foreign_keys=[related_artist_id])


class EnrichmentJob(Base):
    """Tracks enrichment job state for pause/resume support.
    
    This allows long-running enrichment tasks to be:
    - Paused and resumed
    - Tracked with persistent progress
    - Viewed in the admin UI
    """
    __tablename__ = "enrichment_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    
    # Job configuration
    job_type: Mapped[str] = mapped_column(String(32), default="artist_enrichment")  # artist_enrichment, track_weights
    data_sources: Mapped[str] = mapped_column(JSON, default=list)  # ["lastfm", "musicbrainz"]
    
    # Status tracking
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)  # pending, running, paused, completed, failed
    
    # Progress counters
    total_items: Mapped[int] = mapped_column(Integer, default=0)
    processed_items: Mapped[int] = mapped_column(Integer, default=0)
    successful_items: Mapped[int] = mapped_column(Integer, default=0)
    failed_items: Mapped[int] = mapped_column(Integer, default=0)
    skipped_items: Mapped[int] = mapped_column(Integer, default=0)
    
    # Resume support
    last_processed_id: Mapped[int] = mapped_column(Integer, nullable=True)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    paused_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    
    # Error tracking
    error_message: Mapped[str] = mapped_column(String(2048), nullable=True)
    
    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "job_type": self.job_type,
            "data_sources": self.data_sources or [],
            "status": self.status,
            "total_items": self.total_items,
            "processed_items": self.processed_items,
            "successful_items": self.successful_items,
            "failed_items": self.failed_items,
            "skipped_items": self.skipped_items,
            "last_processed_id": self.last_processed_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "paused_at": self.paused_at.isoformat() if self.paused_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error_message": self.error_message,
            "progress_pct": round((self.processed_items / max(1, self.total_items)) * 100, 1),
        }


class Track(Base):
    __tablename__ = "tracks"
    __table_args__ = (
        UniqueConstraint("title_norm", "canonical_artist_set_hash", name="uq_track_norm_artistset"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title_original: Mapped[str] = mapped_column(String(512), nullable=False)
    title_norm: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    canonical_artist_set_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    youtube_video_id: Mapped[str] = mapped_column(String(32), nullable=True)
    youtube_video_url: Mapped[str] = mapped_column(String(1024), nullable=True)
    youtube_embed_url: Mapped[str] = mapped_column(String(1024), nullable=True)
    youtube_title: Mapped[str] = mapped_column(String(512), nullable=True)
    youtube_channel: Mapped[str] = mapped_column(String(512), nullable=True)
    youtube_thumbnail: Mapped[str] = mapped_column(String(1024), nullable=True)
    youtube_search_only: Mapped[bool] = mapped_column(Boolean, default=False)
    youtube_lookup_attempted_at: Mapped[datetime] = mapped_column(DateTime, nullable=True, index=True)

    artists: Mapped[List["Artist"]] = relationship('Artist', secondary=track_artists, backref="tracks")
    aliases: Mapped[List["TrackAlias"]] = relationship('TrackAlias', back_populates="track", cascade="all, delete-orphan")


class TrackAlias(Base):
    __tablename__ = "track_aliases"
    __table_args__ = (UniqueConstraint("track_id", "title", name="uq_track_alias_per_track"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(512), nullable=False)

    track: Mapped["Track"] = relationship('Track', back_populates="aliases")


class EpisodeTrack(Base):
    __tablename__ = "episode_tracks"
    __table_args__ = (
        Index('ix_episode_tracks_track_episode', 'track_id', 'episode_id'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id", ondelete="CASCADE"), index=True)
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"), index=True)
    track_order: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp: Mapped[str] = mapped_column(String(32), nullable=True)
    duration_sec: Mapped[float] = mapped_column(Float, nullable=True)
    notes: Mapped[str] = mapped_column(String(1024), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=True)

    episode: Mapped["Episode"] = relationship('Episode', back_populates="tracks")
    track: Mapped["Track"] = relationship('Track')


class EpisodeGenre(Base):
    __tablename__ = "episode_genres"
    __table_args__ = (
        UniqueConstraint("episode_id", "genre_id", name="uq_episode_genre"),
        Index('ix_episode_genres_episode_genre', 'episode_id', 'genre_id'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id", ondelete="CASCADE"), index=True)
    genre_id: Mapped[int] = mapped_column(ForeignKey("genres.id", ondelete="CASCADE"), index=True)

    episode: Mapped["Episode"] = relationship('Episode', back_populates="genres")
    genre: Mapped["Genre"] = relationship('Genre')


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(256), unique=True, nullable=False, index=True)


class TrackTag(Base):
    __tablename__ = "track_tags"
    __table_args__ = (UniqueConstraint("track_id", "tag_id", "source", name="uq_track_tag_source"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"), index=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id", ondelete="CASCADE"), index=True)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    source: Mapped[str] = mapped_column(String(16), default="episode")


class Mixtape(Base):
    __tablename__ = "mixtapes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    seed_track_ids: Mapped[List[int]] = mapped_column(JSON, nullable=False, default=list)
    seed_genres: Mapped[List[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    notes: Mapped[str] = mapped_column(String(1024), nullable=True)

    tracks: Mapped[List["MixtapeTrack"]] = relationship('MixtapeTrack', back_populates="mixtape", cascade="all, delete-orphan")


class MixtapeTrack(Base):
    __tablename__ = "mixtape_tracks"
    __table_args__ = (
        UniqueConstraint("mixtape_id", "position", name="uq_mixtape_track_position"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mixtape_id: Mapped[int] = mapped_column(ForeignKey("mixtapes.id", ondelete="CASCADE"), index=True)
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    mixtape: Mapped["Mixtape"] = relationship('Mixtape', back_populates="tracks")
    track: Mapped["Track"] = relationship('Track')


class LikedTrack(Base):
    """Tracks that the user has liked/favorited."""
    __tablename__ = "liked_tracks"
    __table_args__ = (
        UniqueConstraint("artist", "title", name="uq_liked_track_artist_title"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    artist: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    # Optional: link to the Track table if the track exists in DB
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id", ondelete="SET NULL"), nullable=True, index=True)
    # Episode context for where the track was liked from
    episode_url: Mapped[str] = mapped_column(String(1024), nullable=True)
    episode_title: Mapped[str] = mapped_column(String(512), nullable=True)
    show_title: Mapped[str] = mapped_column(String(512), nullable=True)
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    track: Mapped["Track"] = relationship('Track', foreign_keys=[track_id])
    playlist_items: Mapped[List["UserPlaylistTrack"]] = relationship('UserPlaylistTrack', back_populates="liked_track", cascade="all, delete-orphan")


class LikedEpisode(Base):
    """Episodes that the user has liked/favorited."""
    __tablename__ = "liked_episodes"
    __table_args__ = (
        UniqueConstraint("episode_url", name="uq_liked_episode_url"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    episode_url: Mapped[str] = mapped_column(String(1024), nullable=False, index=True)
    episode_title: Mapped[str] = mapped_column(String(512), nullable=False)
    show_title: Mapped[str] = mapped_column(String(512), nullable=True)
    show_url: Mapped[str] = mapped_column(String(1024), nullable=True)
    episode_date: Mapped[str] = mapped_column(String(64), nullable=True)
    image_url: Mapped[str] = mapped_column(String(1024), nullable=True)
    # Optional: link to the Episode table if it exists in DB
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id", ondelete="SET NULL"), nullable=True, index=True)
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    episode: Mapped["Episode"] = relationship('Episode', foreign_keys=[episode_id])


class UserPlaylist(Base):
    """User-created playlists for organizing liked tracks."""
    __tablename__ = "user_playlists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    tracks: Mapped[List["UserPlaylistTrack"]] = relationship('UserPlaylistTrack', back_populates="playlist", cascade="all, delete-orphan", order_by="UserPlaylistTrack.position")


class UserPlaylistTrack(Base):
    """Association between user playlists and liked tracks."""
    __tablename__ = "user_playlist_tracks"
    __table_args__ = (
        UniqueConstraint("playlist_id", "liked_track_id", name="uq_playlist_liked_track"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    playlist_id: Mapped[int] = mapped_column(ForeignKey("user_playlists.id", ondelete="CASCADE"), index=True)
    liked_track_id: Mapped[int] = mapped_column(ForeignKey("liked_tracks.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    playlist: Mapped["UserPlaylist"] = relationship('UserPlaylist', back_populates="tracks")
    liked_track: Mapped["LikedTrack"] = relationship('LikedTrack', back_populates="playlist_items")


class ListeningSession(Base):
    __tablename__ = "listening_sessions"
    __table_args__ = (
        UniqueConstraint("session_token", name="uq_listening_session_token"),
        Index("ix_listening_sessions_kind_last_event", "kind", "last_event_at"),
        Index("ix_listening_sessions_meaningful_last_event", "is_meaningful", "last_event_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_token: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    player: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    show_id: Mapped[int] = mapped_column(ForeignKey("shows.id", ondelete="SET NULL"), nullable=True, index=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id", ondelete="SET NULL"), nullable=True, index=True)
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id", ondelete="SET NULL"), nullable=True, index=True)

    show_url: Mapped[str] = mapped_column(String(1024), nullable=True)
    episode_url: Mapped[str] = mapped_column(String(1024), nullable=True)
    artist_name: Mapped[str] = mapped_column(String(512), nullable=True)
    track_title: Mapped[str] = mapped_column(String(512), nullable=True)

    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    last_event_at: Mapped[datetime] = mapped_column(DateTime, nullable=True, index=True)
    ended_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    listened_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=True)
    max_position_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    completion_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    is_meaningful: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_completed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    show: Mapped["Show"] = relationship("Show", foreign_keys=[show_id])
    episode: Mapped["Episode"] = relationship("Episode", foreign_keys=[episode_id])
    track: Mapped["Track"] = relationship("Track", foreign_keys=[track_id])


class EpisodeInboxState(Base):
    __tablename__ = "episode_inbox_state"
    __table_args__ = (
        UniqueConstraint("episode_url", name="uq_episode_inbox_state_episode_url"),
        Index("ix_episode_inbox_state_saved", "saved_for_later", "updated_at"),
        Index("ix_episode_inbox_state_snoozed", "snoozed_until"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id", ondelete="SET NULL"), nullable=True, index=True)
    episode_url: Mapped[str] = mapped_column(String(1024), nullable=False, index=True)
    saved_for_later: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    snoozed_until: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    dismissed_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    episode: Mapped["Episode"] = relationship("Episode", foreign_keys=[episode_id])

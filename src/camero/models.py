from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Site(Base):
    __tablename__ = "sites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)

    streamers: Mapped[list[Streamer]] = relationship(back_populates="site")  # type: ignore[name-defined]
    recordings: Mapped[list[Recording]] = relationship(back_populates="site")  # type: ignore[name-defined]


class Streamer(Base):
    __tablename__ = "streamers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id"), nullable=True)

    avatar_url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    info_json: Mapped[str | None] = mapped_column(String, nullable=True)

    site: Mapped[Site | None] = relationship(back_populates="streamers")
    recordings: Mapped[list[Recording]] = relationship(back_populates="streamer")

    __table_args__ = (UniqueConstraint("name", "site_id", name="uq_streamer_name_site"),)


class RecordingTag(Base):
    __tablename__ = "recording_tags"

    recording_id: Mapped[int] = mapped_column(ForeignKey("recordings.id"), primary_key=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id"), primary_key=True)


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)


class Recording(Base):
    __tablename__ = "recordings"

    # Prevent SQLite from reusing ids after deletes (new DBs only).
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    rel_path: Mapped[str] = mapped_column(String, unique=True, index=True)
    file_name: Mapped[str] = mapped_column(String(512), index=True)
    title: Mapped[str] = mapped_column(String(512))

    streamer_id: Mapped[int | None] = mapped_column(ForeignKey("streamers.id"), nullable=True)
    site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id"), nullable=True)

    recorded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)

    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Quick fingerprint of the underlying file for caching artifacts across DB rebuilds.
    # This is not a cryptographic guarantee; it's designed to be fast and stable enough.
    file_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    file_mtime: Mapped[float | None] = mapped_column(Float, nullable=True)
    live_preview_failed_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    live_preview_failure_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    auto_tag_attempted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    auto_tag_attempted_mtime: Mapped[float | None] = mapped_column(Float, nullable=True)

    video_codec: Mapped[str | None] = mapped_column(String(64), nullable=True)
    audio_codec: Mapped[str | None] = mapped_column(String(64), nullable=True)

    thumbnail_rel_path: Mapped[str | None] = mapped_column(String, nullable=True)

    streamer: Mapped[Streamer | None] = relationship(back_populates="recordings")
    site: Mapped[Site | None] = relationship(back_populates="recordings")

    tags: Mapped[list[Tag]] = relationship(secondary="recording_tags")


class RecordingOverride(Base):
    __tablename__ = "recording_overrides"

    # One row per recording. Flags allow distinguishing "no override" from
    # "override explicitly cleared".
    recording_id: Mapped[int] = mapped_column(ForeignKey("recordings.id"), primary_key=True)

    streamer_set: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    streamer_id: Mapped[int | None] = mapped_column(ForeignKey("streamers.id"), nullable=True)

    site_set: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    site_id: Mapped[int | None] = mapped_column(ForeignKey("sites.id"), nullable=True)

    recorded_at_set: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    recorded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

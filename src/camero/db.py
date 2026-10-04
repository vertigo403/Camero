from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import threading
import time
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from .models import Base


_ENGINE_CACHE: dict[str, Engine] = {}
_ENGINE_CACHE_LOCK = threading.Lock()


def _init_schema(engine: Engine) -> None:
    """Initialize DB schema in a concurrency-safe way.

    SQLite + SQLAlchemy's `create_all(checkfirst=True)` can still race when
    multiple threads/processes initialize the same DB at once:
    one sees "no table", another creates it, the first then fails with
    "table already exists".

    We serialize initialization within the process and retry a few times to
    tolerate cross-process races (e.g. multiple Uvicorn workers).
    """

    delays = (0.0, 0.05, 0.15, 0.30)
    last_exc: Exception | None = None
    for delay in delays:
        if delay:
            time.sleep(delay)
        try:
            Base.metadata.create_all(engine)
            return
        except OperationalError as exc:
            msg = str(exc).lower()
            if "already exists" in msg and "create table" in msg:
                last_exc = exc
                continue
            raise

    if last_exc is not None:
        # One last attempt after retries; if it still fails, bubble up.
        Base.metadata.create_all(engine)


def _configure_sqlite_pragmas(engine: Engine) -> None:
    # Apply per-connection pragmas. This is best-effort and should never
    # prevent app startup.

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection, _connection_record):  # type: ignore[no-redef]
        try:
            cursor = dbapi_connection.cursor()

            # Wait for locks instead of failing immediately.
            cursor.execute("PRAGMA busy_timeout=5000")

            # Improve writer concurrency (writers don't block readers as much).
            cursor.execute("PRAGMA journal_mode=WAL")

            # Reasonable defaults for a local app.
            cursor.execute("PRAGMA synchronous=NORMAL")

            cursor.close()
        except Exception:
            return


def _ensure_recordings_codec_columns(engine: Engine) -> None:
    """Best-effort schema migration for older SQLite DBs.

    SQLAlchemy's create_all() does not add columns to existing tables.
    This keeps local installs working across upgrades without a full
    migration framework.
    """

    try:
        with engine.begin() as conn:
            rows = conn.execute(text("PRAGMA table_info(recordings)")).fetchall()
            existing = {str(r[1]) for r in rows if len(r) > 1}

            if "video_codec" not in existing:
                conn.execute(text("ALTER TABLE recordings ADD COLUMN video_codec TEXT"))
            if "audio_codec" not in existing:
                conn.execute(text("ALTER TABLE recordings ADD COLUMN audio_codec TEXT"))

            # Quick file fingerprint used to cache thumbnails/previews across DB rebuilds.
            if "file_hash" not in existing:
                conn.execute(text("ALTER TABLE recordings ADD COLUMN file_hash TEXT"))
            if "file_mtime" not in existing:
                conn.execute(text("ALTER TABLE recordings ADD COLUMN file_mtime REAL"))
            if "live_preview_failed_hash" not in existing:
                conn.execute(text("ALTER TABLE recordings ADD COLUMN live_preview_failed_hash TEXT"))
            if "live_preview_failure_reason" not in existing:
                conn.execute(text("ALTER TABLE recordings ADD COLUMN live_preview_failure_reason TEXT"))
            if "auto_tag_attempted_at" not in existing:
                conn.execute(text("ALTER TABLE recordings ADD COLUMN auto_tag_attempted_at DATETIME"))
            if "auto_tag_attempted_mtime" not in existing:
                conn.execute(text("ALTER TABLE recordings ADD COLUMN auto_tag_attempted_mtime REAL"))
    except Exception:
        # Best-effort: do not block app startup if migration fails.
        return


def catalog_db_path(catalog_root: str) -> Path:
    root = Path(catalog_root)
    meta_dir = root / ".camero"
    meta_dir.mkdir(parents=True, exist_ok=True)
    return meta_dir / "camero.sqlite"


def catalog_thumbs_dir(catalog_root: str) -> Path:
    root = Path(catalog_root)
    meta_dir = root / ".camero" / "thumbs"
    meta_dir.mkdir(parents=True, exist_ok=True)
    return meta_dir


def get_engine(db_path: Path) -> Engine:
    key = str(db_path.resolve())
    engine = _ENGINE_CACHE.get(key)
    if engine is not None:
        return engine

    # Protect engine creation + schema init from concurrent requests.
    with _ENGINE_CACHE_LOCK:
        engine = _ENGINE_CACHE.get(key)
        if engine is not None:
            return engine

        engine = create_engine(
            f"sqlite:///{key}",
            connect_args={
                "check_same_thread": False,
                # sqlite3: wait up to N seconds when the DB is busy/locked.
                "timeout": 30,
            },
            pool_pre_ping=True,
            future=True,
        )
        _configure_sqlite_pragmas(engine)
        _init_schema(engine)
        _ensure_recordings_codec_columns(engine)
        _ENGINE_CACHE[key] = engine
        return engine


def get_session_for_catalog(catalog_root: str) -> sessionmaker[Session]:
    engine = get_engine(catalog_db_path(catalog_root))
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_session_for_db_path(db_path: Path) -> sessionmaker[Session]:
    engine = get_engine(db_path)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


@contextmanager
def session_scope(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

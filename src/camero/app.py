from __future__ import annotations

from pathlib import Path
from typing import Iterator
import mimetypes
import asyncio
import base64
import binascii
import hmac
import hashlib
import os
import sys
import concurrent.futures
import subprocess

from fastapi import Body, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from strawberry.fastapi import GraphQLRouter
from pydantic import BaseModel

from .assets import get_assets_dir
from .auto_scan_scheduler import start_auto_scan_scheduler, stop_auto_scan_scheduler
from .autotag_jobs import release_cached_tagger, sync_cached_tagger
from .db import get_session_for_catalog, get_session_for_db_path, session_scope
from .graphql_schema import schema
from .models import Recording
from .settings import load_settings
from .catalog_paths import resolve_recording_path
from .media import find_contact_sheet_path
from .reencode_jobs import get_reencode_output_path


def _guess_media_type(path: Path) -> str:
    ext = path.suffix.lower()
    # Prefer explicit mapping for common video types.
    if ext in {".mp4", ".m4v"}:
        return "video/mp4"
    if ext == ".webm":
        return "video/webm"
    if ext == ".mov":
        return "video/quicktime"

    # Fallback: system mimetypes.
    media_type, _ = mimetypes.guess_type(str(path))
    return media_type or "application/octet-stream"


def _auth_realm(expected_username: str, expected_password: str) -> str:
    raw = f"{expected_username}\0{expected_password}".encode("utf-8")
    fingerprint = hashlib.sha256(raw).hexdigest()[:12]
    return f"Camero-{fingerprint}"


def _unauthorized_response(*, expected_username: str, expected_password: str) -> Response:
    return Response(
        status_code=401,
        headers={"WWW-Authenticate": f'Basic realm="{_auth_realm(expected_username, expected_password)}"'},
    )


def _is_authorized_request(request: Request, *, expected_username: str, expected_password: str) -> bool:
    auth_header = (request.headers.get("authorization") or "").strip()
    if not auth_header:
        return False

    scheme, _, encoded = auth_header.partition(" ")
    if scheme.lower() != "basic" or not encoded:
        return False

    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return False

    username, sep, password = decoded.partition(":")
    if not sep:
        return False

    return hmac.compare_digest(username, expected_username) and hmac.compare_digest(password, expected_password)


def create_app() -> FastAPI:
    app = FastAPI(title="Camero", version="0.1.0")

    @app.middleware("http")
    async def require_network_auth(request: Request, call_next):
        settings = load_settings()
        if not getattr(settings, "auth_required", False):
            return await call_next(request)

        expected_username = str(getattr(settings, "auth_username", "") or "").strip()
        expected_password = str(getattr(settings, "auth_password", "") or "").strip()
        if not expected_username or not expected_password:
            return _unauthorized_response(
                expected_username=expected_username,
                expected_password=expected_password,
            )

        if not _is_authorized_request(
            request,
            expected_username=expected_username,
            expected_password=expected_password,
        ):
            return _unauthorized_response(
                expected_username=expected_username,
                expected_password=expected_password,
            )

        return await call_next(request)

    # Run folder picker dialogs on a dedicated
    # single thread and prevent re-entrancy.
    _folder_dialog_lock = asyncio.Lock()
    _tk_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="tk")

    def _site_logo_svg(site_name: str) -> str:
        raw = (site_name or "?").strip()
        parts = [p for p in raw.replace("_", " ").replace("-", " ").split() if p]
        if not parts:
            initials = "?"
        elif len(parts) == 1:
            initials = parts[0][:2].upper()
        else:
            initials = (parts[0][:1] + parts[1][:1]).upper()

        palette = [
            "#2563eb",  # blue
            "#7c3aed",  # violet
            "#db2777",  # pink
            "#ea580c",  # orange
            "#16a34a",  # green
            "#0891b2",  # cyan
            "#ca8a04",  # amber
            "#4f46e5",  # indigo
        ]
        h = 0
        for ch in raw:
            h = (h * 31 + ord(ch)) % 10_000
        bg = palette[h % len(palette)]

        title = raw.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = initials.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        return (
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
            "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"320\" height=\"180\" viewBox=\"0 0 320 180\">"
            f"<title>{title}</title>"
            f"<rect width=\"320\" height=\"180\" rx=\"18\" fill=\"{bg}\"/>"
            "<rect x=\"14\" y=\"14\" width=\"292\" height=\"152\" rx=\"14\" fill=\"rgba(0,0,0,0.12)\"/>"
            "<text x=\"160\" y=\"108\" text-anchor=\"middle\" "
            "font-family=\"ui-sans-serif,system-ui,Segoe UI,Roboto,Arial\" font-size=\"72\" "
            "font-weight=\"800\" fill=\"white\">"
            f"{text}</text>"
            "</svg>"
        )

    @app.on_event("startup")
    async def _silence_spurious_windows_resets() -> None:
        # On Windows, when the browser cancels a video Range request (206),
        # asyncio's Proactor transport may emit noisy ConnectionResetError tracebacks.
        # These are expected and safe to ignore for this local-only app.
        if not sys.platform.startswith("win"):
            return

        loop = asyncio.get_running_loop()
        previous = loop.get_exception_handler()

        def _handler(loop: asyncio.AbstractEventLoop, context: dict) -> None:
            exc = context.get("exception")
            msg = str(context.get("message") or "")
            if isinstance(exc, ConnectionResetError) and "_call_connection_lost" in msg:
                return

            if previous is not None:
                previous(loop, context)
            else:
                loop.default_exception_handler(context)

        loop.set_exception_handler(_handler)

    @app.on_event("shutdown")
    async def _shutdown_executor() -> None:
        release_cached_tagger(reason="app_shutdown")
        stop_auto_scan_scheduler()
        try:
            _tk_executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass

    @app.on_event("startup")
    async def _start_auto_scan_scheduler() -> None:
        start_auto_scan_scheduler()

    @app.on_event("startup")
    async def _warm_auto_tagger_if_enabled() -> None:
        try:
            sync_cached_tagger(load_settings())
        except Exception:
            pass

    base_dir = Path(__file__).parent
    web_dir = base_dir / "web"
    static_dir = web_dir / "static"
    assets_dir = get_assets_dir()

    settings = load_settings()

    def _get_settings():
        # Reload settings so UI changes apply without restart.
        return load_settings()

    def _get_session() -> Iterator:
        # GraphQL should be available even before a catalog is configured,
        # so the Settings page can call mutations.
        s = _get_settings()
        if s.catalog_root:
            session_factory = get_session_for_catalog(s.catalog_root)
        else:
            tmp_db = Path.cwd() / ".camero_tmp.sqlite"
            session_factory = get_session_for_db_path(tmp_db)
        with session_scope(session_factory) as session:
            yield session

    async def get_context(session=Depends(_get_session)) -> dict:
        s = _get_settings()
        return {"session": session, "settings": s}

    graphql_app = GraphQLRouter(schema, context_getter=get_context)
    app.include_router(graphql_app, prefix="/graphql")

    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    if assets_dir.exists() and assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/", response_class=HTMLResponse)
    def index():
        return (web_dir / "index.html").read_text(encoding="utf-8")

    @app.get("/site-logo/{site_name}.svg")
    def site_logo(site_name: str):
        svg = _site_logo_svg(site_name)
        return Response(content=svg, media_type="image/svg+xml")

    @app.get("/thumb/{recording_id}")
    def thumb(recording_id: int):
        s = _get_settings()
        if not s.catalog_root:
            raise HTTPException(status_code=400, detail="Catalog root is not configured")

        session_factory = get_session_for_catalog(s.catalog_root)
        with session_factory() as session:
            rec = session.get(Recording, recording_id)
            if not rec:
                raise HTTPException(status_code=404, detail="Recording not found")

            if not rec.thumbnail_rel_path:
                raise HTTPException(status_code=404, detail="Thumbnail not available")

            thumb_path = Path(s.catalog_root) / rec.thumbnail_rel_path
            if not thumb_path.exists():
                raise HTTPException(status_code=404, detail="Thumbnail not available")
            return FileResponse(
                str(thumb_path),
                media_type="image/jpeg",
                headers={"Cache-Control": "no-store"},
            )

    @app.get("/contact-sheet/{recording_id}")
    def contact_sheet(recording_id: int):
        s = _get_settings()
        if not s.catalog_root:
            raise HTTPException(status_code=400, detail="Catalog root is not configured")

        session_factory = get_session_for_catalog(s.catalog_root)
        with session_factory() as session:
            rec = session.get(Recording, recording_id)
            if not rec:
                raise HTTPException(status_code=404, detail="Recording not found")

            video_path = resolve_recording_path(s, rec.rel_path)
            if video_path is None:
                raise HTTPException(status_code=404, detail="File not found on disk")

            image_path = find_contact_sheet_path(video_path)
            if image_path is None:
                raise HTTPException(status_code=404, detail="Contact sheet not available")

            return FileResponse(
                str(image_path),
                media_type=_guess_media_type(image_path),
                headers={"Cache-Control": "no-store"},
            )

    @app.get("/streamer-avatar/{streamer_id}")
    def streamer_avatar(streamer_id: int):
        s = _get_settings()
        if not s.catalog_root:
            raise HTTPException(status_code=400, detail="Catalog root is not configured")

        sid = int(streamer_id)
        if sid <= 0:
            raise HTTPException(status_code=400, detail="Invalid streamer id")

        avatar_path = Path(s.catalog_root) / ".camero" / "avatars" / f"{sid}.jpg"
        if not avatar_path.exists() or avatar_path.stat().st_size <= 0:
            raise HTTPException(status_code=404, detail="Avatar not available")

        return FileResponse(
            str(avatar_path),
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/media/{recording_id}")
    def media(recording_id: int):
        s = _get_settings()
        if not s.catalog_root:
            raise HTTPException(status_code=400, detail="Catalog root is not configured")

        session_factory = get_session_for_catalog(s.catalog_root)
        with session_factory() as session:
            rec = session.get(Recording, recording_id)
            if not rec:
                raise HTTPException(status_code=404, detail="Recording not found")

            video_path = resolve_recording_path(s, rec.rel_path)
            if video_path is None:
                raise HTTPException(status_code=404, detail="File not found on disk")
            if not video_path.exists():
                raise HTTPException(status_code=404, detail="File not found on disk")

            media_type = _guess_media_type(video_path)
            return FileResponse(
                str(video_path),
                media_type=media_type,
                filename=rec.file_name,
            )

    @app.get("/media/{recording_id}/{file_name}")
    def media_named(recording_id: int, file_name: str):
        # Same file as /media/{id}, but with a filename in the URL.
        # This helps players infer media type from extension.
        _ = file_name  # best-effort; do not trust it for path resolution.

        s = _get_settings()
        if not s.catalog_root:
            raise HTTPException(status_code=400, detail="Catalog root is not configured")

        session_factory = get_session_for_catalog(s.catalog_root)
        with session_factory() as session:
            rec = session.get(Recording, recording_id)
            if not rec:
                raise HTTPException(status_code=404, detail="Recording not found")

            video_path = resolve_recording_path(s, rec.rel_path)
            if video_path is None:
                raise HTTPException(status_code=404, detail="File not found on disk")
            if not video_path.exists():
                raise HTTPException(status_code=404, detail="File not found on disk")

            media_type = _guess_media_type(video_path)
            return FileResponse(
                str(video_path),
                media_type=media_type,
                filename=rec.file_name,
            )

    @app.get("/transcoded/{recording_id}.mp4")
    def transcoded_media(recording_id: int):
        s = _get_settings()
        if not s.catalog_root:
            raise HTTPException(status_code=400, detail="Catalog root is not configured")

        out_path = Path(s.catalog_root) / ".camero" / "transcodes" / f"{recording_id}.mp4"
        if not out_path.exists() or out_path.stat().st_size <= 0:
            raise HTTPException(status_code=404, detail="Transcoded media not available")

        return FileResponse(str(out_path), media_type="video/mp4")

    @app.get("/remuxed/{recording_id}.mp4")
    def remuxed_media(recording_id: int):
        s = _get_settings()
        if not s.catalog_root:
            raise HTTPException(status_code=400, detail="Catalog root is not configured")

        out_path = Path(s.catalog_root) / ".camero" / "remux" / f"{recording_id}.mp4"
        if not out_path.exists() or out_path.stat().st_size <= 0:
            raise HTTPException(status_code=404, detail="Remuxed media not available")

        return FileResponse(str(out_path), media_type="video/mp4")

    @app.get("/reencoded/{recording_id}.{ext}")
    def reencoded_media(recording_id: int, ext: str):
        s = _get_settings()
        if not s.catalog_root:
            raise HTTPException(status_code=400, detail="Catalog root is not configured")

        safe_ext = (ext or "").strip().lower().lstrip(".")
        if safe_ext not in {"mp4", "mkv", "avi", "webm"}:
            raise HTTPException(status_code=400, detail="Unsupported container")

        out_path = Path(s.catalog_root) / ".camero" / "reencodes" / f"{recording_id}.{safe_ext}"
        if not out_path.exists() or out_path.stat().st_size <= 0:
            raise HTTPException(status_code=404, detail="Transcoded media not available")

        media_type = {
            "mp4": "video/mp4",
            "mkv": "video/x-matroska",
            "avi": "video/x-msvideo",
            "webm": "video/webm",
        }.get(safe_ext, "application/octet-stream")

        return FileResponse(str(out_path), media_type=media_type)

    @app.get("/reencoded/{job_id}/{recording_id}")
    def reencoded_media_for_job(job_id: str, recording_id: int):
        path = get_reencode_output_path((job_id or "").strip(), int(recording_id))
        if path is None or not path.exists() or path.stat().st_size <= 0:
            raise HTTPException(status_code=404, detail="Transcoded media not available")

        return FileResponse(str(path), media_type=_guess_media_type(path))

    @app.get("/preview/{recording_id}.mp4")
    def live_preview_media(recording_id: int):
        s = _get_settings()
        if not s.catalog_root:
            raise HTTPException(status_code=400, detail="Catalog root is not configured")

        # Previews are keyed by a quick file hash.
        session_factory = get_session_for_catalog(s.catalog_root)
        with session_factory() as session:
            rec = session.get(Recording, int(recording_id))
            if not rec:
                raise HTTPException(status_code=404, detail="Recording not found")

            fh = getattr(rec, "file_hash", None)
            if not isinstance(fh, str) or not fh.strip():
                raise HTTPException(status_code=404, detail="Animated preview not available")

            out_path = Path(s.catalog_root) / ".camero" / "previews" / f"{fh.strip().lower()}.mp4"
            try:
                if not out_path.exists() or out_path.stat().st_size <= 0:
                    raise HTTPException(status_code=404, detail="Animated preview not available")
            except HTTPException:
                raise
            except Exception:
                raise HTTPException(status_code=404, detail="Animated preview not available")

        return FileResponse(
            str(out_path),
            media_type="video/mp4",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.post("/api/dialog/select-folder")
    async def select_folder_dialog(payload: dict | None = Body(default=None)):
        """Open a native folder picker on the server machine.

        Note: Browsers cannot reliably return absolute local paths for security reasons,
        so we provide this local-only helper endpoint.
        """

        title = None
        if isinstance(payload, dict):
            title = payload.get("title")
        if not isinstance(title, str) or not title.strip():
            title = "Select folder"

        def _pick() -> str | None:
            try:
                import tkinter as tk
                from tkinter import filedialog
            except Exception:
                return None

            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            try:
                selected = filedialog.askdirectory(title=title)
            finally:
                try:
                    root.destroy()
                except Exception:
                    pass

            selected = (selected or "").strip()
            return selected or None

        try:
            try:
                await asyncio.wait_for(_folder_dialog_lock.acquire(), timeout=0.05)
            except TimeoutError:
                raise HTTPException(status_code=409, detail="Folder dialog is already open")

            loop = asyncio.get_running_loop()
            selected = await loop.run_in_executor(_tk_executor, _pick)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
        finally:
            if _folder_dialog_lock.locked():
                try:
                    _folder_dialog_lock.release()
                except Exception:
                    pass

        return {"path": selected}

    def _open_in_file_browser(*, file_path: Path) -> None:
        """Open OS file browser and (where supported) select the file."""

        if sys.platform.startswith("win"):
            # explorer /select,"C:\path\file.mp4"
            subprocess.Popen(["explorer", "/select,", str(file_path)])
            return

        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(file_path)])
            return

        # Linux / others: best-effort open the containing folder.
        folder = file_path.parent
        subprocess.Popen(["xdg-open", str(folder)])

    def _open_file_with_default_app(*, file_path: Path) -> None:
        """Open the file with the OS default handler (e.g. video player)."""

        if sys.platform.startswith("win"):
            # Opens with the default associated application.
            os.startfile(str(file_path))  # type: ignore[attr-defined]
            return

        if sys.platform == "darwin":
            subprocess.Popen(["open", str(file_path)])
            return

        subprocess.Popen(["xdg-open", str(file_path)])

    @app.post("/api/open-recording-folder")
    def open_recording_folder(
        recordingId: int | None = None,
        payload: dict | None = Body(default=None),
    ):
        s = _get_settings()
        if not s.catalog_root:
            raise HTTPException(status_code=400, detail="Catalog root is not configured")

        rid = recordingId
        if rid is None and isinstance(payload, dict):
            rid = payload.get("recordingId") or payload.get("recording_id") or payload.get("id")

        try:
            rid_int = int(rid) if rid is not None else None
        except Exception:
            rid_int = None

        if rid_int is None or rid_int <= 0:
            raise HTTPException(status_code=400, detail="Missing or invalid recordingId")

        session_factory = get_session_for_catalog(s.catalog_root)
        with session_factory() as session:
            rec = session.get(Recording, rid_int)
            if not rec:
                raise HTTPException(status_code=404, detail="Recording not found")

            video_path = resolve_recording_path(s, rec.rel_path)
            if video_path is None or not video_path.exists():
                raise HTTPException(status_code=404, detail="File not found on disk")

        try:
            _open_in_file_browser(file_path=video_path)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

        return {"ok": True, "message": "Opened folder"}

    @app.post("/api/open-recording-video")
    def open_recording_video(
        recordingId: int | None = None,
        payload: dict | None = Body(default=None),
    ):
        s = _get_settings()
        if not s.catalog_root:
            raise HTTPException(status_code=400, detail="Catalog root is not configured")

        rid = recordingId
        if rid is None and isinstance(payload, dict):
            rid = payload.get("recordingId") or payload.get("recording_id") or payload.get("id")

        try:
            rid_int = int(rid) if rid is not None else None
        except Exception:
            rid_int = None

        if rid_int is None or rid_int <= 0:
            raise HTTPException(status_code=400, detail="Missing or invalid recordingId")

        session_factory = get_session_for_catalog(s.catalog_root)
        with session_factory() as session:
            rec = session.get(Recording, rid_int)
            if not rec:
                raise HTTPException(status_code=404, detail="Recording not found")

            video_path = resolve_recording_path(s, rec.rel_path)
            if video_path is None or not video_path.exists():
                raise HTTPException(status_code=404, detail="File not found on disk")

        try:
            _open_file_with_default_app(file_path=video_path)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

        return {"ok": True, "message": "Opened video"}

    return app

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass
from contextlib import closing
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import select

from .catalog_paths import resolve_recording_path
from .media import check_ffmpeg_installed, find_contact_sheet_path, quick_file_hash
from .models import Recording
from .settings import DEFAULT_SEMANTIC_SEARCH_MIN_SIMILARITY, _default_ai_models_dir, resolve_ai_models_dir

MODEL_FILENAME = "embeddinggemma-2-text-vision-440m.litertlm"
MODEL_SHA256 = "92dcbea108899e5d6e30d919b0744f90d9967e80c67a4ab5503ac16d54f62eb0"
SEMANTIC_INDEX_FORMAT_VERSION = 3
MODEL_CANDIDATE_URLS = (
    "https://huggingface.co/litert-community/embeddinggemma-2-text-vision-440m-litert-lm/resolve/main/embeddinggemma-2-text-vision-440m.litertlm",
)
_MODEL_DOWNLOAD_LOCK = threading.Lock()

@dataclass(frozen=True)
class SemanticIndexResult:
    total: int
    indexed: int
    skipped: int
    failed: int


def default_ai_models_dir() -> Path:
    return _default_ai_models_dir().resolve()


def resolve_models_dir(value: str | Path | None = None) -> Path:
    return resolve_ai_models_dir(value)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_embeddinggemma_model(models_dir: str | Path | None = None) -> Path:
    model_dir = resolve_models_dir(models_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    model_path = model_dir / MODEL_FILENAME
    if model_path.exists() and model_path.is_file() and model_path.stat().st_size > 0:
        return model_path

    with _MODEL_DOWNLOAD_LOCK:
        if model_path.exists() and model_path.is_file() and model_path.stat().st_size > 0:
            return model_path

        lock_path = model_path.with_suffix(model_path.suffix + ".lock")
        deadline = time.monotonic() + 1800.0
        lock_fd: int | None = None
        while lock_fd is None:
            try:
                lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(lock_fd, f"{os.getpid()}\n".encode("ascii"))
            except FileExistsError:
                if model_path.exists() and model_path.is_file() and model_path.stat().st_size > 0:
                    return model_path
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        f"Timed out waiting for another Camero process to download {MODEL_FILENAME}."
                    )
                time.sleep(0.25)

        try:
            if model_path.exists() and model_path.is_file() and model_path.stat().st_size > 0:
                return model_path

            last_error: Exception | None = None
            for url in MODEL_CANDIDATE_URLS:
                temp_path = model_path.with_name(
                    f"{model_path.name}.{os.getpid()}.{threading.get_ident()}.download"
                )
                try:
                    urllib.request.urlretrieve(url, temp_path)
                    if _file_sha256(temp_path) != MODEL_SHA256:
                        raise RuntimeError("Downloaded EmbeddingGemma model checksum does not match the published SHA-256.")
                    temp_path.replace(model_path)
                    if model_path.exists() and model_path.is_file() and model_path.stat().st_size > 0:
                        return model_path
                except Exception as exc:  # pragma: no cover - network and filesystem failures
                    if model_path.exists() and model_path.is_file() and model_path.stat().st_size > 0:
                        return model_path
                    last_error = exc
                    try:
                        if temp_path.exists():
                            temp_path.unlink(missing_ok=True)
                    except OSError:
                        pass
            if last_error is not None:
                raise RuntimeError(f"Failed to auto-download EmbeddingGemma model to {model_dir}: {last_error}")
        finally:
            os.close(lock_fd)
            lock_path.unlink(missing_ok=True)
    raise RuntimeError(f"EmbeddingGemma model is missing and could not be downloaded to {model_dir}")


def _semantic_db_path(catalog_root: str | Path) -> Path:
    root = Path(catalog_root)
    return root / ".camero" / "semantic_search.sqlite"


def _semantic_index_format_path(database_path: Path) -> Path:
    return database_path.with_suffix(".format")


def _semantic_index_manifest_path(database_path: Path) -> Path:
    return database_path.with_suffix(".manifest.json")


def _load_index_manifest(database_path: Path) -> dict[str, str]:
    manifest_path = _semantic_index_manifest_path(database_path)
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(record_id): str(signature)
        for record_id, signature in raw.items()
        if isinstance(record_id, str) and isinstance(signature, str)
    }


def _save_index_manifest(database_path: Path, manifest: dict[str, str]) -> None:
    manifest_path = _semantic_index_manifest_path(database_path)
    temp_path = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    temp_path.replace(manifest_path)


def _ensure_current_index_format(database_path: Path) -> None:
    format_path = _semantic_index_format_path(database_path)
    try:
        current_version = int(format_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        current_version = None
    if current_version == SEMANTIC_INDEX_FORMAT_VERSION and database_path.exists():
        return

    database_path.unlink(missing_ok=True)
    _semantic_index_manifest_path(database_path).unlink(missing_ok=True)
    format_path.write_text(str(SEMANTIC_INDEX_FORMAT_VERSION), encoding="utf-8")


def _contact_sheet_path_for_recording(catalog_root: str | Path, recording_id: int, file_hash: str | None) -> Path:
    root = Path(catalog_root)
    return root / ".camero" / "semantic_contact_sheets" / f"{recording_id}_{(file_hash or 'unknown')[:20]}.jpg"


def ensure_contact_sheet_for_video(video_path: Path, *, catalog_root: str | Path | None = None, recording_id: int | None = None) -> Path | None:
    contact_path = find_contact_sheet_path(video_path)
    if contact_path is not None:
        return contact_path

    if catalog_root is None:
        return None

    file_hash = quick_file_hash(video_path)
    out_path = _contact_sheet_path_for_recording(catalog_root, recording_id or 0, file_hash)
    if out_path.exists() and out_path.is_file() and out_path.stat().st_size > 0:
        return out_path

    try:
        _, ffmpeg_path = check_ffmpeg_installed()
    except Exception:
        return None

    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            ffmpeg_path,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(video_path),
            "-vf",
            "select='eq(pict_type\\,I)',scale='min(320,iw)':-1,tile=6x5",
            "-vsync",
            "0",
            "-frames:v",
            "1",
            str(out_path),
        ]
        completed = subprocess.run(cmd, check=False, capture_output=True, text=True)
        if completed.returncode != 0:
            return None
        if out_path.exists() and out_path.stat().st_size > 0:
            return out_path
    except OSError:
        return None
    return None


def _semantic_search_searcher(model_path: Path, database_path: Path):
    try:
        from mediapipe.tasks.python import BaseOptions
        from mediapipe.tasks.python.core import mediapipe_c_bindings, mediapipe_c_utils
        from mediapipe.tasks.python.retrieval import semantic_retriever, universal_embedder
    except ImportError as exc:  # pragma: no cover - better surfaced to caller
        raise RuntimeError("Semantic search requires mediapipe to be installed.") from exc

    # GLOG_minloglevel does not control LiteRT LM's Abseil logging.
    # Its C API uses ERROR=4, and must be called before creating the engine.
    native_library = mediapipe_c_bindings.load_raw_library((
        mediapipe_c_utils.CFunction(
            "litert_lm_set_min_log_level", [ctypes.c_int], None
        ),
    ))
    native_library.litert_lm_set_min_log_level(4)

    embedder_options = universal_embedder.UniversalEmbedderOptions(
        base_options=BaseOptions(model_asset_path=str(model_path)),
        l2_normalize=True,
    )
    embedder = universal_embedder.UniversalEmbedder.create_from_options(embedder_options)
    retriever_options = semantic_retriever.SemanticRetrieverOptions(
        embedder=embedder,
        embedding_dimension=768,
        database_path=str(database_path),
    )
    retriever = semantic_retriever.SemanticRetriever.create_from_options(retriever_options)
    return embedder, retriever


def _recording_metadata(rec: Recording, rel_path: str) -> dict[str, str]:
    return {
        "recording_id": str(getattr(rec, "id", 0) or 0),
        "rel_path": rel_path,
        "title": str(getattr(rec, "title", "") or "").strip() or str(getattr(rec, "file_name", "") or "").strip(),
        "file_name": str(getattr(rec, "file_name", "") or "").strip(),
        "model_name": str(getattr(getattr(rec, "streamer", None), "name", "") or "").strip(),
        "site_name": str(
            getattr(getattr(rec, "site", None), "name", "")
            or getattr(getattr(getattr(rec, "streamer", None), "site", None), "name", "")
            or ""
        ).strip(),
    }


def _recording_index_signature(rec: Recording, rel_path: str) -> str:
    metadata = _recording_metadata(rec, rel_path)
    source = {
        "audio_codec": str(getattr(rec, "audio_codec", "") or ""),
        "duration_seconds": getattr(rec, "duration_seconds", None),
        "file_hash": str(getattr(rec, "file_hash", "") or ""),
        "file_mtime": getattr(rec, "file_mtime", None),
        "height": getattr(rec, "height", None),
        "metadata": metadata,
        "rel_path": rel_path,
        "size_bytes": getattr(rec, "size_bytes", None),
        "video_codec": str(getattr(rec, "video_codec", "") or ""),
        "width": getattr(rec, "width", None),
    }
    payload = json.dumps(source, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def index_semantic_recordings(
    *,
    session,
    catalog_root: str | Path,
    settings: Any | None,
    progress_cb: Callable[[int, int, int, int, str | None], None] | None = None,
    cancel_cb: Callable[[], bool] | None = None,
) -> SemanticIndexResult:
    root = Path(catalog_root)
    if not root.exists() or not root.is_dir():
        raise RuntimeError("Catalog root is not configured")
    database_path = _semantic_db_path(root)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    _ensure_current_index_format(database_path)
    all_items = session.scalars(select(Recording).order_by(Recording.id.desc())).all()
    manifest = _load_index_manifest(database_path)
    current_records: list[tuple[Recording, str, str, str]] = []
    for rec in all_items:
        rel_path = str(getattr(rec, "rel_path", "") or "").strip()
        if not rel_path:
            continue
        image_id = f"recording_{rec.id}"
        current_records.append((rec, rel_path, image_id, _recording_index_signature(rec, rel_path)))

    current_ids = {image_id for _, _, image_id, _ in current_records}
    pending = [
        item
        for item in current_records
        if manifest.get(item[2]) != item[3]
    ]
    removed_ids = set(manifest) - current_ids
    if not pending and not removed_ids:
        return SemanticIndexResult(total=len(all_items), indexed=0, skipped=len(all_items), failed=0)

    model_path = ensure_embeddinggemma_model(
        getattr(settings, "ai_models_dir", None) if settings is not None else None
    )

    try:
        embedder, retriever = _semantic_search_searcher(model_path, database_path)
        with embedder, retriever:
            indexed_ids = set(retriever.get_all_record_ids())
            for image_id in removed_ids:
                if image_id in indexed_ids:
                    retriever.delete_record(image_id)
                manifest.pop(image_id, None)

            indexed = 0
            skipped = len(all_items) - len(pending)
            failed = 0
            total = len(all_items)
            for position, (rec, rel_path, image_id, signature) in enumerate(pending, start=1):
                if cancel_cb is not None and cancel_cb():
                    break
                resolved = resolve_recording_path(settings, rel_path) if settings is not None else None
                if resolved is None or not resolved.exists() or not resolved.is_file():
                    failed += 1
                    if progress_cb is not None:
                        progress_cb(len(pending), position, indexed, failed, rel_path)
                    continue
                image_path = ensure_contact_sheet_for_video(
                    resolved,
                    catalog_root=root,
                    recording_id=int(getattr(rec, "id", 0) or 0),
                )
                if image_path is None:
                    failed += 1
                    if progress_cb is not None:
                        progress_cb(len(pending), position, indexed, failed, rel_path)
                    continue
                metadata = _recording_metadata(rec, rel_path)
                try:
                    if image_id not in indexed_ids:
                        retriever.insert_image(
                            image_id=image_id,
                            image_path=str(image_path),
                            metadata=metadata,
                        )
                        indexed_ids.add(image_id)
                    else:
                        retriever.delete_record(image_id)
                        retriever.insert_image(
                            image_id=image_id,
                            image_path=str(image_path),
                            metadata=metadata,
                        )
                    manifest[image_id] = signature
                    indexed += 1
                except RuntimeError:
                    failed += 1
                finally:
                    if progress_cb is not None:
                        progress_cb(len(pending), position, indexed, failed, rel_path)
            _save_index_manifest(database_path, manifest)
            return SemanticIndexResult(total=total, indexed=indexed, skipped=skipped, failed=failed)
    except Exception as exc:
        raise RuntimeError(f"Semantic indexing failed: {exc}") from exc


def search_semantic_recordings(
    *,
    catalog_root: str | Path,
    settings: Any | None,
    query: str,
    limit: int = 10,
    min_similarity: float | None = None,
) -> list[dict[str, Any]]:
    text = (query or "").strip()
    if not text:
        return []

    threshold = float(min_similarity if min_similarity is not None else getattr(settings, "semantic_search_min_similarity", DEFAULT_SEMANTIC_SEARCH_MIN_SIMILARITY) or DEFAULT_SEMANTIC_SEARCH_MIN_SIMILARITY)
    threshold = max(0.0, min(1.0, threshold))
    root = Path(catalog_root)
    database_path = _semantic_db_path(root)
    model_path = resolve_models_dir(
        getattr(settings, "ai_models_dir", None) if settings is not None else None
    ) / MODEL_FILENAME
    if not database_path.exists():
        raise RuntimeError("Semantic index is not available yet. Run a catalog scan first.")
    if not model_path.exists() or not model_path.is_file():
        raise RuntimeError("EmbeddingGemma model is not available yet. Run a catalog scan first.")

    try:
        embedder, retriever = _semantic_search_searcher(model_path, database_path)
        with embedder, retriever:
            limit_i = max(1, int(limit))
            result = retriever.retrieve(query=text, limit=limit_i, min_similarity=threshold)
            matches: list[dict[str, Any]] = []
            for record in (getattr(result, "records", []) or []):
                metadata = getattr(record, "metadata", {}) or {}
                rec_id = int(metadata.get("recording_id") or str(getattr(record, "id", "")).replace("recording_", "") or 0)
                if not rec_id:
                    continue
                score = float(getattr(record, "score", 0.0) or 0.0)
                if score < threshold:
                    continue
                matches.append(
                    {
                        "recording_id": rec_id,
                        "score": score,
                        "rel_path": str(metadata.get("rel_path") or ""),
                        "file_name": str(metadata.get("file_name") or ""),
                        "title": str(metadata.get("title") or ""),
                    }
                )
            return matches
    except Exception as exc:
        raise RuntimeError(f"Semantic search failed: {exc}") from exc


def semantic_recording_is_indexed(catalog_root: str | Path, recording_id: int) -> bool:
    database_path = _semantic_db_path(catalog_root)
    if not database_path.is_file():
        return False
    with closing(sqlite3.connect(database_path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        return connection.execute(
            "SELECT 1 FROM rag_vector_store WHERE record_id = ? AND embeddings IS NOT NULL LIMIT 1",
            (f"recording_{recording_id}",),
        ).fetchone() is not None


def search_similar_recordings(
    *,
    catalog_root: str | Path,
    recording_id: int,
    settings: Any | None,
    limit: int = 10,
    min_similarity: float | None = None,
) -> list[dict[str, Any]]:
    import numpy as np

    database_path = _semantic_db_path(catalog_root)
    if not database_path.is_file():
        raise RuntimeError("Semantic index is not available yet. Run a catalog scan first.")
    threshold = float(
        min_similarity if min_similarity is not None
        else getattr(settings, "semantic_search_min_similarity", DEFAULT_SEMANTIC_SEARCH_MIN_SIMILARITY)
    )
    threshold = max(0.0, min(1.0, threshold))

    def decode_embedding(blob: bytes):
        # MediaPipe's vector store serializes float32 vectors with a VF32 header.
        if not isinstance(blob, bytes) or blob[:4] != b"VF32" or len(blob) != 4 + 768 * 4:
            raise RuntimeError("Unsupported semantic embedding format. Rebuild the semantic index.")
        vector = np.frombuffer(blob, dtype="<f4", offset=4)
        norm = float(np.linalg.norm(vector))
        if not np.isfinite(vector).all() or not np.isfinite(norm) or norm <= 0:
            raise RuntimeError("Invalid semantic embedding. Rebuild the semantic index.")
        return vector / norm

    try:
        with closing(sqlite3.connect(database_path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
            source_id = f"recording_{recording_id}"
            source = connection.execute(
                "SELECT embeddings FROM rag_vector_store WHERE record_id = ?", (source_id,)
            ).fetchone()
            if source is None:
                raise RuntimeError("This video is not semantically indexed. Run a catalog scan first.")
            source_vector = decode_embedding(source[0])
            matches = []
            for record_id, blob, metadata_json in connection.execute(
                "SELECT record_id, embeddings, metadata FROM rag_vector_store WHERE record_id != ?",
                (source_id,),
            ):
                if not record_id.startswith("recording_"):
                    continue
                score = float(np.clip(np.dot(source_vector, decode_embedding(blob)), -1.0, 1.0))
                if score < threshold:
                    continue
                metadata = json.loads(metadata_json or "{}")
                matches.append({
                    "recording_id": int(record_id.removeprefix("recording_")),
                    "score": score,
                    "rel_path": str(metadata.get("rel_path") or ""),
                    "file_name": str(metadata.get("file_name") or ""),
                    "title": str(metadata.get("title") or ""),
                })
            matches.sort(key=lambda item: (-item["score"], item["recording_id"]))
            return matches[:max(1, int(limit))]
    except (sqlite3.Error, ValueError, TypeError) as exc:
        raise RuntimeError(f"Similar video search failed: {exc}") from exc

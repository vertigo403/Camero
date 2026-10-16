# Camero

Camero is a local catalog manager for recorded stream sessions.

## Features

- Choose a catalog root folder (the folder that contains your videos)
- Scan videos recursively and parse file names into:
  - streamer name
  - site name
  - recording date
- Browse recordings (paginated cards), sites, tags and configuration
- Search recordings semantically from the Recordings view using text descriptions
- UI uses GraphQL queries against the backend

## Run

Install dependencies (one time):

	python -m pip install -U pip
	python -m pip install fastapi uvicorn[standard] strawberry-graphql[fastapi] sqlalchemy pillow

Start Camero:

	python src/main.py

Camero starts an embedded HTTP server and opens:

	http://127.0.0.1:6969

You can change the backend host/IP and port from the Settings screen. For example,
use `0.0.0.0` to listen on all interfaces or set a specific external/local IP.

## External files

The ONNX models in `models/` and Windows copies of `ffmpeg.exe` and `ffprobe.exe`
are intentionally excluded from Git because GitHub does not accept files larger
than 100 MB. Obtain the required model files separately and place them in
`models/`. Install FFmpeg and make `ffmpeg` and `ffprobe` available on `PATH`.

## Notes

- Catalog data is stored in a SQLite database inside the catalog folder under:

	.camero/camero.sqlite

- Catalog configuration is stored inside the catalog folder under:

	.camero/settings.json

- Each catalog also stores a stable internal id at:

	.camero/catalog_id

- App-wide configuration only stores the "active catalog" pointer (so catalogs can live on removable drives).
	In development it defaults to `camero_settings.json` in the current working directory.
	In frozen (.exe) builds it defaults to `camero_settings.json` next to the executable.
	You can override it via `CAMERO_SETTINGS_PATH`.

- On Windows, the active catalog is referenced by a stable volume id (Volume GUID) + a path relative to the volume root,
	so it keeps working even if the same removable disk appears under a different drive letter.

- On Linux/macOS, the app uses the catalog's internal id (`.camero/catalog_id`) + a path relative to the mount root.
	It enumerates mount points using the Python package `psutil` (no external commands).

- Streamer "internet info" is currently a backend stub (as requested in specs).

### Semantic video search

The **Semantic search** control in the Recordings view indexes each video from its
contact-sheet image. If a contact sheet is missing, Camero creates one from at
most 30 key frames using FFmpeg. Each embedding retains the recording id, title,
model/streamer name, and site name as metadata.

Configure the model directory and the minimum similarity threshold in
**Settings → AI**. By default, the model directory is `models` next to the
frozen executable (or the repository's `models` directory during development).
EmbeddingGemma V2 is downloaded to that directory during catalog scanning. Search
only retrieves from the completed local index, so it never waits for video
indexing or model downloads. Later scans only reindex recordings whose media
fingerprint or indexed metadata changed.

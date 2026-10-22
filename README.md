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
  - Semantic indexing runs after automatic live preview generation, when enabled
    in Settings; otherwise, it runs immediately after the catalog file scan.
- Find similar videos with the search icon on recording cards in Recordings,
  streamer, site and tag views. Searches cover the entire catalog and open in
  Recordings. The icon is enabled only
  for semantically indexed videos and compares their stored embeddings directly,
  excluding the source video. Results use the AI Magic Search similarity threshold
  and are ordered by relevance; Clear returns to the normal recording list.
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

## Build executables

PyInstaller builds for the operating system and architecture where it runs;
it does not cross-compile. Use `build.bat` on Windows to generate
`dist/Camero.exe`. On Linux (or inside WSL), run these commands from the
repository directory:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
bash build_linux.sh
```

Install your distribution's Python venv/pip packages first if needed. Do not
reuse a Windows virtual environment in Linux; create a Linux environment in
a separate checkout when building on both systems.

The Linux script uses `.venv/bin/python` when available, otherwise `python3`,
and installs PyInstaller if it is missing. It generates the single-file
executable `dist_linux/Camero`, with intermediate files in `build/linux`, without
deleting existing Windows build outputs. Launch it with `./dist_linux/Camero`.
Python is not required on the destination machine.

Build on the oldest Linux distribution you intend to support: the executable
still depends on compatible system libraries (including glibc), and a build
from a newer distribution may not run on an older one. FFmpeg and the AI
models remain external resources as described below.

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
This directory is shared by **Auto-tagging** and **AI Magic Search**. Place
`model.onnx` and `selected_tags.csv` in it for auto-tagging; changing the directory
also switches the auto-tagging model on the next run. The minimum similarity
threshold, under **AI Magic Search**, applies only to semantic search.
Embeddings model is downloaded to that directory during catalog scanning. Search
only retrieves from the completed local index, so it never waits for video
indexing or model downloads. Later scans only reindex recordings whose media
fingerprint or indexed metadata changed.

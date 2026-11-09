# Camero Help

How Camero works and how to use it effectively.

## What Camero Is

Camero is a local catalog manager for recorded webcam stream sessions. It scans video folders, extracts metadata such as streamer, site, date and time, codecs, and duration, and lets you browse, tag, preview, and organize recordings from a web interface.

Camero runs on your computer and opens a browser UI. By default, it listens at `http://127.0.0.1:6969`. You can change the backend host or IP and port in Settings, including binding to an external IP when needed. No cloud services are required.

## Portable App and Removable Drives

Camero is designed to be portable: the catalog database and generated metadata are stored inside the catalog folder under `.camero/`. The catalog therefore travels with its recordings.

Camero also works with removable drives. On Windows, it can keep tracking the active catalog if the same drive appears with a different drive letter, using a stable volume identifier.

## Key Concepts

- **Catalog root:** The main folder that contains your recordings. Camero stores catalog metadata inside it under `.camero/`.
- **Catalog folders:** Optional additional folders to scan. Each can have its own filename pattern, which is useful for split libraries or multiple download locations.
- **Recordings:** Individual video files. You can filter, tag, move or copy, remux or transcode, and open them in an external player.
- **Streamers:** People or channels detected from filenames or set manually. Streamers can have a site and an avatar.
- **Sites:** Streaming platforms, used for filtering and organization.
- **Tags:** Custom labels you assign to recordings for quick filtering and organization.

## First Run Checklist

1. Open **Settings** (gear icon) and set your **Catalog root**.
2. Optionally add one or more **Catalog folders** if your recordings are in multiple locations.
3. Go to **Recordings** and click **Scan catalog** to populate the database.
4. Browse **Recordings** and start tagging and organizing your library.

## Scanning and Filename Parsing

Scanning reads files on disk and extracts metadata. Initial metadata such as streamer, site, date, and time is often parsed from filenames using a regular expression. The default pattern expects a name like:

```text
<streamer>_<site>_<YYYYMMDD>_<time>.<ext>
```

Camero also accepts this format without a site:

```text
<streamer>_<YYYY-MM-DD>_<HH-mm-ss>_<suffix>.<ext>
```

Streamer names may contain underscores in either format. If your naming scheme is different, adjust the filename regex in **Settings**. Camero accepts named groups such as `streamer`, `site`, `date`, and `time`.

To update entries that were already scanned after changing patterns, use **Rescan metadata** on selected recordings.

## Recordings Page

### Browsing

- Sort by date, title, streamer, duration, size, or resolution.
- Use **Filters** (funnel icon) for advanced filtering by size, duration, resolution, and codecs.
- Use pagination controls to navigate large libraries.

### Card Actions

- **Open containing folder** (folder icon).
- **Open in external player** (play icon) to use your default player.
- **Assign tags** (tag icon).
- **Create preview** to generate an animated thumbnail preview for hover playback.

Select multiple recordings using the checkbox overlay to run bulk actions such as delete, assign tags or sites, move or copy, rescan metadata, or start a transcode.

## AI Magic Search

AI Magic Search finds recordings by describing their visual content in natural language instead of matching exact filenames or tags. Camero compares your description with a local AI index of sampled video frames.

- **Prepare the index:** Run **Scan catalog** and wait for semantic indexing to finish. Camero creates missing visual representations and downloads the AI model automatically when needed; the initial download requires an internet connection. Later scans update changed recordings.
- **Search:** In **Recordings**, click **AI Magic Search**, enter a description such as `a blonde girl in the shower`, and press **Enter** or click **OK**.
- **Find similar recordings:** Click the **Find similar videos** sparkles icon on a recording card. This uses the recording's stored AI embeddings. It is enabled only for semantically indexed recordings, and results exclude the source video.
- **Results:** Search uses the completed local index and returns up to 100 matching videos. It does not download models or index videos while you search. AI tags are not required.
- **Adjust matching:** Open **Settings > AI** to choose the **AI models directory** and **Minimum similarity (0 to 1)**. A higher threshold is stricter; a lower threshold allows more matches.

If no recordings match, try a simpler description or lower the similarity threshold. If the index is not available yet, scan the catalog first. Search runs locally. Sampled frames may miss brief scenes, and matches are approximate rather than guaranteed.

## Recording Details

Open a recording to view it in the player modal.

- Playback happens inside the browser using an embedded player.
- Use **Left Arrow** or **Right Arrow** to skip -5 or +5 seconds while the modal is open.
- **Edit metadata** to set the streamer, site, or date if parsing did not detect it correctly.
- Set the current frame as the **streamer avatar** or **recording thumbnail**.
- Add or remove tags directly from the modal.

## Animated Previews and AI Controls

Animated previews are short MP4 clips used for hover playback on recording cards. Camero builds them from multiple one-second fragments sampled across the video.

Generating previews for a large catalog can take time. Create a preview for an individual recording, run **Generate previews** from the Recordings toolbar, or enable **Generate animated previews after scan** in Settings. You can configure the number of fragments in Settings: 5, 10, 15, or 20 (default: 10).

Other related controls:

- **AI tags** tags recordings that do not yet have tags.
- Use **AI Magic Search** to search by description. Click **Recordings** in the navigation to return to the normal list.
- Open **Settings > AI > Auto-tagging** to enable automatic tagging after scans and configure minimum confidence, scene change threshold, and frame interval.
- The **AI models directory** is shared by auto-tagging and AI Magic Search. Put `model.onnx` and `selected_tags.csv` in that directory for auto-tagging.
- **Minimum similarity** in the **AI Magic Search** panel only affects semantic search.
- Active filters show a red dot on the filter button. Filters reset when switching views and are not saved between application runs.

## Move and Copy

Bulk **Move** and **Copy** actions can send recordings to any folder on this computer. In the dialog, type a destination path or choose a folder using the native folder picker.

- **Move** to a folder inside the catalog keeps the recording in the catalog and relocates it.
- **Move** to a folder outside the catalog removes the recording from the catalog; this is treated as an export.
- **Copy** copies files to the destination and keeps the originals in the catalog.
- **Preserve folder structure** recreates the recording's folder structure inside the destination folder.

## Remux and Transcode

### Remux (Make Playable)

Some containers, such as `.mkv`, `.avi`, and `.ts`, are often not playable directly in browsers. Camero can remux a file so the browser can play it. Remuxing changes the container but keeps the audio and video streams as-is; it does not re-encode them.

### Transcode

Transcoding re-encodes a recording when you need a different codec or format, or want standardized outputs. It runs as a background job and shows progress. While a transcode is running, Camero can show a floating progress widget; restore the full dialog from that widget.

## Streamers

The **Streamers** view lists detected streamers. You can:

- Filter by site, search by name, and use A-Z shortcuts.
- Open a streamer to view their recordings and edit their site association.
- Fetch missing streamer information, if supported for the selected site.

## Sites

The **Sites** page shows an overview, including recording and model counts, and provides quick navigation to recordings or streamers filtered by that site.

## Tags

Tags are the main way to group and filter recordings across different sites and streamers.

- Create tags on the **Tags** page or assign them from the Recordings view.
- Rename or delete tags. Deleting a tag removes it from all recordings.
- Open a tag to browse recordings filtered by that tag.

## Data Safety

- **Delete** actions remove files from disk. Use them with care.
- **Move** and **Copy** can target any folder. Moving files outside the catalog removes those items from the catalog.
- Camero stores the catalog database and generated assets under `.camero/` inside the catalog root.

## Troubleshooting

- **"Catalog root is not configured":** Open Settings and set the catalog folder path.
- **Wrong streamer, site, or date:** Adjust the filename regex and use **Rescan metadata**, or edit the metadata from the recording modal.
- **Video will not play in the browser:** Use **Make playable (remux MP4)**, especially for MKV, AVI, or TS files.
- **No thumbnails or avatars:** Set them from the recording modal using the current player frame.
- **FFmpeg warning banner:** Camero requires FFmpeg and FFprobe. Install FFmpeg and add it to `PATH` (or place the executables next to the app), then click **Retry**.

*Source help last updated: 2026-01-17.*
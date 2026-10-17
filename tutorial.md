# Camero Tutorial (Quick Start + Features)

Updated: 17 October 2026.

Camero is a local catalog manager for recorded stream sessions. It scans your video folders, parses filenames into metadata (streamer, site, date/time), and lets you browse, tag, preview, and organize recordings from a web UI.

Recent features include **AI Magic Search**, **Find similar videos**, configurable
**AI auto-tagging**, a shared **AI models directory**, and **automatic catalog
scans**. AI inference and search run locally; the embedding model requires an
initial download.

## 1) First run and getting started

**Run the executable**

- Double-click Camero.exe (Windows) or Camero (Linux, MacOS).

**Open the UI in your browser**

- The app usually opens your browser automatically at startup.
  - If it does not, open the configured backend address manually. By default it is:


```
http://127.0.0.1:6969
```

You can change the backend host/IP and port in Settings. If you want other devices on
your network to connect, bind the backend to an external/local IP or to `0.0.0.0` and
open the matching port in your firewall.

Settings groups options into **Catalog**, **AI**, and **Network** tabs.

1. **Configure your catalog**

- On first run, the Settings panel opens automatically.
- Set the Catalog folder path (the folder that contains your recordings).
- Optionally add more folders if your library is split across multiple locations.

Short note: You can always reopen Settings later using the gear icon in the top-right.

2. **Define the filename templates (required for correct parsing)**

- Review each Catalog folder and set the filename template so it matches your real filenames (ℹ Read chapter 1.1 to understand how pattern templates work).
- Use the Test button to validate the template against real examples.

3. Select if you want to generate previews for the recordings.
4. Save the changes.

**Scan the catalog**

After the initial configuration, you can start parsing your collection:

- Go to Recordings.

- Click Scan catalog.

The scan progress area reports the current phase, including semantic indexing,
preview generation, and auto-tagging when applicable. Large catalogs can take
longer on their first scan.

### Automatic catalog scans

In **Settings > Catalog > Automatic catalog scan**, enable **Scan the catalog
automatically on a schedule** and set **Minutes between scans** (1 to 1440;
default 30). Save the settings and leave Camero running.

- Scheduling is disabled by default.
- Camero does not start a scheduled scan while another scan is running.
- After an active scan finishes, the scheduler waits the configured interval
  before starting the next one.
- This is an in-app scheduler, not a Windows scheduled task: it does not run while
  Camero is closed.

### 1.1) Catalog folders and filename templates (important)

⚠ Camero extracts metadata from filenames using a template . This is the most important step for a good first scan.

Default template:

```
{streamer}_{site?}{YYYYMMDD|YYYY-MM-DD}_{HHMMSS|HH-mm-ss}{wildcard}.{ext}
```

Key tokens you can use in templates:

- {site?}: optionally capture a site and its trailing underscore before a compact YYYYMMDD date. Before a dashed date, no site is captured, preserving underscores in streamer names.
- {YYYYMMDD|YYYY-MM-DD} and {HHMMSS|HH-mm-ss}: accept either of the two date and time formats.
- {streamer} and {site}: capture the streamer name and the site/platform name.
- {YYYYMMDD}, {YYYY-MM-DD}, {YYMMDD}: capture the recording date in those formats.
- {HHMMSS}, {HH-mm-ss}, {HH_mm}: capture the recording time in those formats.
- {ext}: capture the file extension (mp4, mkv, etc.).
- {wildcard} (anything, non-greedy): match extra text between tokens without being too greedy.
- {digit} (single digit): match one numeric digit (0-9).

Notes:

- The default template accepts both `LumaVox_Camturbate_20250112_213015.mp4` and `elliemint_2026-02-12_03-35-14_045.ts`. For the latter, the site remains unset. Existing saved templates and regexes are not replaced automatically; paste the new default template into Settings to update an existing folder.
- You can match against the relative path (not only the filename) using the Match against relative path checkbox. This is useful when streamer or site names are part of folder names.
- You can test a template directly in Settings using the Test button and a real filename.
- After changing a template, re-scan or use Rescan metadata on selected recordings to update existing entries.

### Example templates for common filename patterns

ℹ Names of sites and models in these examples are fictitious.

**Example A** (default style with underscores, format used by **Webcam Streams Recorder**):

Example of filename:
```
LumaVox_Camturbate_20250112_213015.mp4
```
Template

```
{streamer}_{site}_{YYYYMMDD}_{HHMMSS}.{ext}
```

**Example B** (default format used by **ctbrec**. No site in filename; You'll have to add site manually later):

Example of filename:
```
elliemint_2026-02-12_03-35-14_045.ts
```
Template:
```
{streamer}_{YYYY-MM-DD}_{HH-mm-ss}_{wildcard}.{ext}
```
Note: Since the filename does not include the site, you must set the site manually before streamer info fetching works.

**Example C** (spaces and dashes, date as YYYY-MM-DD):

Example of filename:
```
NovaJade - Stripchatt - 2025-01-12 21-30-15.mp4
```
Template:
```
{streamer} - {site} - {YYYY-MM-DD} {HH-mm-ss}.{ext}
```

**Example D** (date as DD-MM-YYYY, time with underscore, extra suffix):

Example of filename:
```
MiaXen_Camfour_12-01-2025_21_30_high.mp4
```
Template:
```
{streamer}_{site}_{DD-MM-YYYY}_{HH_mm}{wildcard}.{ext}
```

**Example** E (site in folder path):

Relative path + filename:
```
Camturbate/LumaVox/2025-01-12_21-30-15.mp4
```
Template (enable Match against relative path):
```
{site}/{streamer}/{YYYY-MM-DD}_{HH-mm-ss}.{ext}
```

**Example F** (two-digit year, compact time):

Example of filename:
```
StarKite_Stripchatt_250112_2130.mp4
```
Template:
```
{streamer}_{site}_{YYMMDD}_{HHmm}{wildcard}.{ext}
```

For advanced patterns, expand **Advanced: edit regex directly**. Leave the template
empty to use your own regular expression unchanged. The **Test** control is inside
this advanced section and uses Python's regex engine.

## 2) Recordings view (browse, filter, and manage)

The Recordings page is your main workspace. Each recording appears as a card with thumbnail, metadata, and quick actions.

What you can do:

- **Filter** by size, duration, resolution, codecs, or “missing preview” using the Filters panel.
- **Sort** by date, title, streamer, duration, size, or resolution.
- Select multiple recordings and run bulk actions (**delete**, **copy**, **move**, **tag**, **rescan metadata**, **transcode**).
- Use **Assign site** or **Auto-tag** on selected recordings.
- Open a recording to **view details** and play it in the embedded player.

Active filters are marked with a red dot on the Filters button. Filters reset
when switching views and are not saved between application runs. **Select all**
selects recordings matching the current filters, not just the visible page.

### 2.1) AI Magic Search (search by description)

AI Magic Search finds videos by visual content rather than only by filename or
manually assigned tags.

1. Run **Scan catalog** and allow semantic indexing to complete.
2. On the main **Recordings** page, click **AI Magic Search**.
3. Enter a short visual description, such as `a person wearing a red shirt`,
   and click **OK**.
4. Browse the matches, ordered by relevance (up to 100 results).
5. Use **Clear** in the search panel, or click **Recordings** in the navigation,
   to return to the normal list.

Semantic results use their own relevance ordering rather than the normal list's
sort order and advanced filters. Clear the search before returning to ordinary
filtering.

**How the index is built**

- During scanning, Camero uses a contact-sheet image for each video. If it is
  missing, Camero creates one with FFmpeg from at most 30 key frames.
- EmbeddingGemma V2 is downloaded automatically to the configured AI models
  directory during scanning. Allow time, disk space, and internet access for
  this initial setup.
- Later scans only reindex recordings whose media fingerprint or indexed
  metadata changed.
- Searching reads the completed local index. It does not download the model or
  start indexing videos while you wait for results.
- AI tags are not required. Tags and semantic embeddings serve different purposes.

In **Settings > AI > AI Magic Search**, adjust **Minimum similarity (0 to 1)**.
The default is **0.15**. Higher values are stricter; lower values allow more
matches. This threshold affects semantic search, not auto-tagging confidence.

Search is approximate: sampled frames can miss short scenes. If there are no
matches, try a simpler description, lower the threshold, or complete a catalog
scan first.

### 2.2) Find similar videos

On a recording card in **Recordings** or a streamer, site or tag view, click
**Find similar videos** to search using that recording's stored embeddings.
Searches cover the entire catalog; results are shown in **Recordings**, even
when the search starts from another view.

- The button is enabled only after the recording has been semantically indexed.
- Camero compares stored embeddings directly; you do not need to enter a text
  description or generate AI tags first.
- Results exclude the source recording and are ordered by relevance.
- When similar-video results finish loading, the page scrolls to the top.
- The same **Minimum similarity** setting used by AI Magic Search applies.
- Use **Clear** or return to **Recordings** to leave the similar-video results.

## 3) Recording details and metadata editing

Click a recording card to open the player modal. Here you can:

- **Play the video** in the browser.
- Use the left/right arrow keys to skip backward/forward by 5 seconds while the
  player modal is open.
- **Edit streamer, site, and recording date** if parsing was not correct.
- Set the current frame as the recording **thumbnail**.
- Set the current frame as the streamer **avatar**.
- **Add or remove tags**.



## 4) Animated previews (hover playback)

Camero can generate short animated previews that play when you hover over a card.

- Enable **Generate animated previews** after scan in Settings.
- Choose how many preview fragments to sample (5/10/15/20). More fragments = slower generation.
- You can also generate previews on demand from the Recordings toolbar or per recording.
- Use the round animated-preview toggle in the toolbar to turn card preview
  playback on or off. Stopping preview generation is separate from stopping
  a catalog scan; use the stop control shown for the active phase.



## 5) Move and Copy recordings

You can move or copy selected recordings to any folder on your computer.

- **Move** to a folder inside the catalog keeps the recording in the catalog (it is just relocated).
- Move to a folder outside the catalog removes it from the catalog (treat as **export**).
- Copy keeps the original recordings in place and adds a copy elsewhere.
- **Preserve folder structure** recreates the original folder hierarchy at the destination.



## 6) Remux vs Transcode

Some files are not browser-playable (e.g., MKV/AVI/TS). Camero offers two options:

- **Remux**: changes the container but keeps audio/video streams as-is (fast, no re-encode).
- **Transcode**: re-encodes to a standard format; runs as a background job with progress.

Tip: If the embedded player cannot play a file, try Make playable (remux) first.

For multiple recordings, select them and click **Transcode**. Background
transcoding shows overall and per-file progress. If you close the progress
dialog, use the floating progress widget to restore it.

**Data safety:** deleting recordings removes files from disk. Review destinations
before moving files, and keep a backup before choosing any option that deletes
originals after conversion.

## 7) Streamers and internet info

The Streamers view lists detected streamers and lets you:

- **Filter** by site and search by name.
- Use A-Z shortcuts to navigate the list.
- **Open a streamer** to see all recordings involving that streamer.
- **Fetch** missing streamer info (if supported for the selected site).

Camero stores streamer info locally and can show avatars if available.



## 8) Sites view

The Sites page provides an overview of platforms in your catalog. From here you can:

- See **how many recording**s and streamers exist per site.
- **Jump to recordings** or streamers filtered by a specific site.



## 9) Tags (organize your collection)

Tags are custom labels you assign to recordings, making it easy to group and filter across streamers or sites.

- **Create** and manage tags in the Tags view.
- **Assign** tags in bulk from the Recordings view or per recording in the modal.
- **Rename or delete** tags (deleting removes them from all recordings).
- Open a tag to browse recordings with that tag.

### 9.1) AI auto-tagging

Auto-tagging analyzes sampled video frames and assigns labels. It is separate
from AI Magic Search: you can use either feature without using the other.

**Prepare the model**

1. Open **Settings > AI** and set **AI models directory**.
2. Place the auto-tagging files `model.onnx` and `selected_tags.csv` in that
   directory. They must be obtained separately; the embedding-model download
   does not install them.
3. Save the settings. A directory change takes effect for auto-tagging on its
   next run.

The default directory is `models` next to the packaged executable, or the
repository's `models` directory during development. Both auto-tagging and
AI Magic Search use this directory, but they use different model files.

**Run auto-tagging**

- Click **AI tags** in the Recordings toolbar when available to process
  recordings that do not yet have tags.
- To target particular recordings, select them and click **Auto-tag**.
- For automatic processing, enable auto-tagging after scans in
  **Settings > AI > Auto-tagging**.
- Follow the progress indicator and use **Stop auto-tagging** to cancel the job.

**Tune the results**

| Setting | Default | Purpose |
| --- | --- | --- |
| Minimum confidence | 0.6 | Accepted range: 0.1 to 1.0. Higher values accept fewer, more confident labels. |
| Scene change threshold | 0.35 | Accepted range: 0.0 to 1.0. Controls scene-change detection when sampling frames. |
| Frame interval (seconds) | auto | Use Auto (scene detection), or sample every 10, 15, 30, or 60 seconds. |

Automatic tagging after scans is disabled by default. Review the resulting tags
and edit them manually when needed; AI labels are not guaranteed to be correct.


## 10) Data storage and portability

Camero keeps everything inside your catalog folder (so it's portable and can be used with external drives. Each catalog keeps its own information):

- Database: .camero/camero.sqlite
- Catalog settings: .camero/settings.json
- Catalog id: .camero/catalog_id

This makes catalogs portable (copy the folder and the metadata moves with it).

Keep the `.camero` directory when copying or backing up the catalog. Additional
catalog folders and an AI models directory outside the primary folder must be
copied or backed up separately.

On Windows, Camero tracks the active catalog using a stable volume identifier,
so a removable drive can keep working when its drive letter changes. On
Linux/macOS, catalog identification uses the catalog's internal id and mount
information.

App-wide `camero_settings.json` stores the active-catalog pointer. In development,
it defaults to the current working directory; in packaged builds, it is next to
the executable. `CAMERO_SETTINGS_PATH` can override this location.

### Network settings

In **Settings > Network**, configure the backend host/IP and port, and optional
username/password authentication. The default address is `127.0.0.1:6969` and
authentication is disabled by default. If you enable network access, configure
authentication and restrict access with your firewall; do not expose the service
directly to the public internet.

Host/IP and port changes take effect after restarting Camero.

## 11) Troubleshooting quick tips

- “Catalog root is not configured”: open Settings and set the catalog folder.
- Wrong streamer/site/date: adjust the template and Rescan metadata.
- Video won’t play: use Make playable (remux) or transcode.
- Missing previews: generate animated previews from the Recordings toolbar.
- FFmpeg warning: install FFmpeg and ensure it is on PATH (or place the executables next to the app), then click Retry.
- No semantic results: complete a catalog scan, try a simpler description, and
  check **Settings > AI > AI Magic Search > Minimum similarity**.
- Find similar videos is disabled: that recording has not been semantically
  indexed yet; scan the catalog and allow indexing to finish.
- First AI scan is slow or the embedding model is unavailable: check internet
  access for the initial download and write access/free space in the AI models
  directory. Searching alone will not download or build the index.
- Auto-tagging cannot find its model: check that `model.onnx` and
  `selected_tags.csv` are in the **AI models directory**, not only in an old
  model location.
- Changing auto-tagging confidence does not change semantic results: use
  **Minimum similarity** instead; these are independent settings.
- Scheduled scans do not run: leave Camero open, enable the schedule, save a
  valid catalog configuration, and wait for the interval after any active scan.

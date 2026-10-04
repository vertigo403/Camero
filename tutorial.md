# Camero Tutorial (Quick Start + Features)

Camero is a local catalog manager for recorded stream sessions. It scans your video folders, parses filenames into metadata (streamer, site, date/time), and lets you browse, tag, preview, and organize recordings from a web UI.

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

![image-20260216193404716](C:\Users\Enrique\AppData\Roaming\Typora\typora-user-images\image-20260216193404716.png)

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

Screenshot placeholder: Recordings page with the Scan catalog button and a few example cards.

### 1.1) Catalog folders and filename templates (important)

⚠ Camero extracts metadata from filenames using a template . This is the most important step for a good first scan.

Default template:

```
{streamer}_{site}_{YYYYMMDD}_{HHMMSS}{wildcard}.{ext}
```

Key tokens you can use in templates:

- {streamer} and {site}: capture the streamer name and the site/platform name.
- {YYYYMMDD}, {YYYY-MM-DD}, {YYMMDD}: capture the recording date in those formats.
- {HHMMSS}, {HH-mm-ss}, {HH_mm}: capture the recording time in those formats.
- {ext}: capture the file extension (mp4, mkv, etc.).
- {wildcard} (anything, non-greedy): match extra text between tokens without being too greedy.
- {digit} (single digit): match one numeric digit (0-9).

Notes:

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

Screenshot placeholder: Settings page with the template pills row, a test filename in the input, and a successful parsed output.

## 2) Recordings view (browse, filter, and manage)

The Recordings page is your main workspace. Each recording appears as a card with thumbnail, metadata, and quick actions.

What you can do:

- **Filter** by size, duration, resolution, codecs, or “missing preview” using the Filters panel.
- **Sort** by date, title, streamer, duration, size, or resolution.
- Select multiple recordings and run bulk actions (**delete**, **copy**, **move**, **tag**, **rescan metadata**, **transcode**).
- Open a recording to **view details** and play it in the embedded player.

![image-20260216193444110](C:\Users\Enrique\AppData\Roaming\Typora\typora-user-images\image-20260216193444110.png)



## 3) Recording details and metadata editing

Click a recording card to open the player modal. Here you can:

- **Play the video** in the browser.
- **Edit streamer, site, and recording date** if parsing was not correct.
- Set the current frame as the recording **thumbnail**.
- Set the current frame as the streamer **avatar**.
- **Add or remove tags**.



## 4) Animated previews (hover playback)

Camero can generate short animated previews that play when you hover over a card.

- Enable **Generate animated previews** after scan in Settings.
- Choose how many preview fragments to sample (5/10/15/20). More fragments = slower generation.
- You can also generate previews on demand from the Recordings toolbar or per recording.



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

![image-20260216193722899](C:\Users\Enrique\AppData\Roaming\Typora\typora-user-images\image-20260216193722899.png)

![image-20260216194119333](C:\Users\Enrique\AppData\Roaming\Typora\typora-user-images\image-20260216194119333.png)

## 7) Streamers and internet info

The Streamers view lists detected streamers and lets you:

- **Filter** by site and search by name.
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



## 10) Data storage and portability

Camero keeps everything inside your catalog folder (so it's portable and can be used with external drives. Each catalog keeps its own information):

- Database: .camero/camero.sqlite
- Catalog settings: .camero/settings.json
- Catalog id: .camero/catalog_id

This makes catalogs portable (copy the folder and the metadata moves with it).

## 11) Troubleshooting quick tips

- “Catalog root is not configured”: open Settings and set the catalog folder.
- Wrong streamer/site/date: adjust the template and Rescan metadata.
- Video won’t play: use Make playable (remux) or transcode.
- Missing previews: generate animated previews from the Recordings toolbar.
- FFmpeg warning: install FFmpeg and ensure it is on PATH (or place the executables next to the app), then click Retry.

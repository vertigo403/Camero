from __future__ import annotations


def stub_reencode_recordings(recording_ids: list[int]) -> None:
    for rid in recording_ids:
        print(f"Transcoding video {rid}", flush=True)


def stub_fetch_streamer_info(streamer_name: str, site_name: str) -> None:
    _ = site_name
    print(f"Fetching data for streamer {streamer_name}", flush=True)

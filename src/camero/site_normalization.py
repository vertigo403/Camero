from __future__ import annotations

from collections import defaultdict

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from .models import Recording, RecordingOverride, Site, Streamer


def canonical_site_name(name: str | None) -> str | None:
    """Return a canonical site name.

    Rules requested:
    - First character uppercase
    - Remaining characters lowercase

    Example: "CAM4" -> "Cam4", "cam4" -> "Cam4".
    """

    raw = (name or "").strip()
    if not raw:
        return None
    low = raw.lower()
    if len(low) == 1:
        return low.upper()
    return low[0].upper() + low[1:]


def _merge_streamers_for_site_ids(session: Session, *, target_site_id: int, site_ids: list[int]) -> None:
    """Move/merge streamers from the given site_ids into target_site_id.

    Handles collisions on (streamer.name, site_id) by merging streamers with the
    exact same name (case-sensitive) into a single row.
    """

    rows = session.execute(
        select(Streamer.id, Streamer.name, Streamer.site_id).where(Streamer.site_id.in_(site_ids))
    ).all()

    by_name: dict[str, dict[str, object]] = {}
    for sid, name, site_id in rows:
        if not name:
            continue
        n = str(name)
        if n not in by_name:
            by_name[n] = {"target": None, "others": []}

        if int(site_id or 0) == int(target_site_id) and by_name[n]["target"] is None:
            by_name[n]["target"] = int(sid)
        else:
            cast = by_name[n]["others"]
            assert isinstance(cast, list)
            cast.append(int(sid))

    # If we didn't have a target streamer on the canonical site, pick one.
    for n, g in by_name.items():
        if g["target"] is None:
            others = g["others"]
            assert isinstance(others, list)
            if not others:
                continue
            g["target"] = int(others.pop(0))

    # Merge duplicates by name.
    for _n, g in by_name.items():
        target_id = g.get("target")
        others = g.get("others")
        if not isinstance(target_id, int) or not isinstance(others, list):
            continue

        for other_id in list(others):
            session.execute(update(Recording).where(Recording.streamer_id == other_id).values(streamer_id=target_id))
            session.execute(
                update(RecordingOverride)
                .where(RecordingOverride.streamer_id == other_id)
                .values(streamer_id=target_id)
            )
            session.execute(delete(Streamer).where(Streamer.id == other_id))

    # Finally, move remaining streamers to the canonical site.
    session.execute(update(Streamer).where(Streamer.site_id.in_(site_ids)).values(site_id=target_site_id))


def _normalize_site_key(session: Session, *, key_lower: str) -> Site | None:
    rows = session.execute(
        select(Site.id, Site.name).where(func.lower(Site.name) == key_lower)
    ).all()

    if not rows:
        return None

    # Compute canonical name from the key itself.
    canonical = canonical_site_name(key_lower)
    if not canonical:
        return None

    # Prefer the row already using canonical name.
    target_id: int | None = None
    target_name: str | None = None
    for sid, name in rows:
        if str(name) == canonical:
            target_id = int(sid)
            target_name = str(name)
            break

    if target_id is None:
        # Otherwise pick the smallest id to keep stable references.
        sid_min, name_min = min(((int(sid), str(name)) for sid, name in rows), key=lambda x: x[0])
        target_id = sid_min
        target_name = name_min

    assert target_id is not None

    dup_ids = [int(sid) for sid, _name in rows if int(sid) != target_id]

    # Rename target to canonical form (safe because any existing canonical row
    # would have been selected as target already).
    if target_name != canonical:
        target = session.get(Site, target_id)
        if target is not None:
            target.name = canonical
            session.flush()

    if dup_ids:
        all_site_ids = [target_id] + dup_ids

        # Merge/move streamers first (so moving Streamer.site_id doesn't violate uq).
        _merge_streamers_for_site_ids(session, target_site_id=target_id, site_ids=all_site_ids)

        # Re-point recordings and overrides.
        session.execute(update(Recording).where(Recording.site_id.in_(dup_ids)).values(site_id=target_id))
        session.execute(update(RecordingOverride).where(RecordingOverride.site_id.in_(dup_ids)).values(site_id=target_id))

        # Remove duplicate site rows.
        session.execute(delete(Site).where(Site.id.in_(dup_ids)))
        session.flush()

    return session.get(Site, target_id)


def upsert_site_canonical(session: Session, name: str) -> Site:
    """Get or create a Site using canonical name, merging case variants.

    Guarantees that case variants like CAM4/cam4/Cam4 are treated as the same site.
    """

    canonical = canonical_site_name(name)
    if not canonical:
        # Keep existing behavior: empty site => should be handled by caller.
        raise ValueError("Site name is empty")

    key = canonical.lower()

    # If any variants exist, normalize/merge them and return.
    normalized = _normalize_site_key(session, key_lower=key)
    if normalized is not None:
        return normalized

    # Otherwise create new canonical row.
    site = Site(name=canonical)
    session.add(site)
    session.flush()
    return site


def normalize_all_sites(session: Session) -> int:
    """Normalize/merge all site names in the DB.

    Returns the number of site rows removed due to merges.
    """

    rows = session.execute(select(Site.id, Site.name)).all()
    groups: dict[str, list[int]] = defaultdict(list)
    for sid, name in rows:
        key = str(name or "").strip().lower()
        if not key:
            continue
        groups[key].append(int(sid))

    removed = 0
    for key_lower, ids in groups.items():
        # Best-effort: normalize/merge the group; if it succeeds, it should
        # collapse to a single row.
        try:
            _normalize_site_key(session, key_lower=key_lower)
            removed += max(0, len(ids) - 1)
        except Exception:
            # Ignore and continue; scan/UI should still work.
            continue

    return int(removed)

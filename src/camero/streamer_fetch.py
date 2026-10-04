from __future__ import annotations

import gzip
import http.cookiejar
import json
import logging
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlparse
import zlib

logger = logging.getLogger(__name__)

try:
    import brotli  # type: ignore[import-untyped]
except ImportError:
    try:
        import brotlicffi as brotli  # type: ignore[import-untyped]
    except ImportError:
        brotli = None

_cookie_jar = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(_cookie_jar),
)

_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
)


STREAMER_INFO_FIELDS = (
    "name",
    "gender",
    "age",
    "ethnicity",
    "country",
    "hair_color",
    "about",
    "url",
    "avatar_url",
)


@dataclass(frozen=True)
class StreamerInfo:
    payload: dict[str, Any]

    def normalized(self) -> dict[str, Any]:
        # Ensure all expected keys exist (optional fields can be null).
        out: dict[str, Any] = {k: None for k in STREAMER_INFO_FIELDS}
        for k, v in (self.payload or {}).items():
            if k in out:
                out[k] = v
        return out


def _decompress(raw: bytes, encoding: str | None) -> bytes:
    if not raw:
        return raw
    # Auto-detect gzip magic bytes (\x1f\x8b)
    if raw.startswith(b"\x1f\x8b"):
        try:
            return gzip.decompress(raw)
        except Exception:
            pass
    enc = (encoding or "").lower().strip()
    if "gzip" in enc:
        try:
            return gzip.decompress(raw)
        except Exception:
            pass
    elif "deflate" in enc:
        try:
            return zlib.decompress(raw)
        except Exception:
            try:
                return zlib.decompress(raw, -zlib.MAX_WBITS)
            except Exception:
                pass
    elif "br" in enc and brotli is not None:
        try:
            return brotli.decompress(raw)
        except Exception:
            pass
    return raw


def _http_get_json(
    url: str,
    *,
    timeout_s: float = 12.0,
    extra_headers: dict[str, str] | None = None,
    max_retries: int = 2,
) -> dict[str, Any] | None:
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""

    accept_encoding = "gzip, deflate, br" if brotli is not None else "gzip, deflate"

    base_headers = {
        "User-Agent": _DEFAULT_USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
        "Accept-Encoding": accept_encoding,
        "sec-ch-ua": '"Not(A:Brand";v="99", "Google Chrome";v="133", "Chromium";v="133"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
    }
    if origin:
        base_headers["Referer"] = f"{origin}/"

    headers = dict(base_headers)
    if extra_headers:
        lower_to_key = {k.lower(): k for k in headers}
        for k, v in extra_headers.items():
            if not k or v is None:
                continue
            k_str, v_str = str(k), str(v)
            existing_key = lower_to_key.get(k_str.lower())
            if existing_key and existing_key != k_str:
                del headers[existing_key]
            headers[k_str] = v_str
            lower_to_key[k_str.lower()] = k_str

    for attempt in range(max_retries + 1):
        req = urllib.request.Request(url, headers=headers, method="GET")
        raw: bytes | None = None
        content_encoding: str | None = None

        try:
            with _opener.open(req, timeout=timeout_s) as resp:
                status = getattr(resp, "status", 200)
                if status != 200:
                    if status in (429, 502, 503, 504) and attempt < max_retries:
                        time.sleep(0.5 * (2**attempt))
                        continue
                    return None
                content_encoding = resp.headers.get("Content-Encoding")
                raw = resp.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 502, 503, 504) and attempt < max_retries:
                retry_after = e.headers.get("Retry-After")
                delay = 0.5 * (2**attempt)
                if retry_after:
                    try:
                        delay = min(float(retry_after), 5.0)
                    except (ValueError, TypeError):
                        pass
                time.sleep(delay)
                continue
            logger.debug("_http_get_json HTTPError %s for %s", e.code, url)
            return None
        except (urllib.error.URLError, TimeoutError, ConnectionResetError, OSError) as e:
            if attempt < max_retries:
                time.sleep(0.5 * (2**attempt))
                continue
            logger.debug("_http_get_json network error %s for %s", e, url)
            return None
        except Exception as e:
            logger.debug("_http_get_json unexpected error %s for %s", e, url)
            return None

        if not raw:
            return None

        decompressed = _decompress(raw, content_encoding)

        # Detect Cloudflare challenge / HTML error page
        stripped = decompressed.lstrip()
        if stripped.startswith((b"<!DOCTYPE html", b"<html", b"<!doctype html")):
            logger.debug("_http_get_json received HTML challenge response for %s", url)
            return None

        try:
            data = json.loads(decompressed.decode("utf-8", errors="replace"))
            if isinstance(data, dict):
                return data
            return None
        except Exception:
            return None

    return None


def fetch_info_chaturbate(streamer_name: str) -> StreamerInfo | None:
    name = (streamer_name or "").strip()
    if not name:
        return None

    api_url = f"https://chaturbate.com/api/biocontext/{quote(name)}/"
    data = _http_get_json(api_url)
    if not isinstance(data, dict):
        return None

    #uniq = int(time.time() * 1000)  # timestamp en ms
    info: dict[str, Any] = {
        "name": name,
        "about": (data.get("about_me") or None),
        "age": (data.get("display_age") or None),
        "url": f"https://chaturbate.com/{name}/",
    }

    return StreamerInfo(payload=info)


def fetch_info_stripchat(streamer_name: str) -> StreamerInfo | None:
    name = (streamer_name or "").strip()
    if not name:
        return None

    api_modelid_url = f"https://stripchat.com/api/front/users/user-ids/{quote(name)}"

    data_id = _http_get_json(api_modelid_url)
    

    if not isinstance(data_id, dict):
        return None

    model_id = data_id.get("id", None)
    if not model_id:
        return None

    api_url = f"https://stripchat.com/api/front/v2/models/{model_id}/cam"
    print(f"api_url: {api_url}")

    data = _http_get_json(api_url)
    #print(f"data:\n {data}")
    if not isinstance(data, dict):
        return None

    def _split_camel(s: str) -> str:
        # "MiddleEastern" -> "Middle Eastern"
        return re.sub(r"(?<!^)([A-Z])", r" \1", s)

    def _stripchat_enum(v: object, prefix: str) -> str | None:
        if v is None:
            return None
        if not isinstance(v, str):
            return None
        raw = v.strip()
        if not raw:
            return None

        # Strip prefixes like "ethnicityWhite" / "hairColorBlack".
        p = (prefix or "").strip()
        if p and raw.lower().startswith(p.lower()):
            raw = raw[len(p) :].strip()
            if raw.startswith("_"):
                raw = raw[1:].strip()

        if not raw:
            return None

        if " " in raw:
            return raw

        cooked = _split_camel(raw).strip()
        if not cooked:
            return None
        # Title case for readability.
        return " ".join(w[:1].upper() + w[1:] if w else "" for w in cooked.split())

    user_container = data.get("user")
    if not isinstance(user_container, dict):
        return None

    # Some responses come as {"user": {"user": {...}}}; accept both.
    user = user_container.get("user")
    if not isinstance(user, dict):
        user = user_container

    avatar = user.get("previewUrlThumbBig") or user.get("avatarUrl") or None

    info: dict[str, Any] = {
        "name": name,
        "country": user.get("country") or None,
        "ethnicity": _stripchat_enum(user.get("ethnicity"), "ethnicity"),
        "hair_color": _stripchat_enum(user.get("hairColor"), "hairColor"),
        "about": user.get("description") or None,
        "avatar_url": avatar,
        "url": f"https://stripchat.com/{name}",
    }

    return StreamerInfo(payload=info)


def fetch_info_cam4(streamer_name: str) -> StreamerInfo | None:
    name = (streamer_name or "").strip()
    if not name:
        return None

    api_url = f"https://es.cam4.com/rest/v1.0/profile/{quote(name)}/info"
    data = _http_get_json(api_url)
    if not isinstance(data, dict):
        return None

    avatar = data.get("avatarUrl") or data.get("profileImageUrl") or None

    info: dict[str, Any] = {
        "name": name,
        "age": data.get("age") or None,
        "gender": data.get("gender") or None,
        "ethnicity": data.get("ethnicity") or None,
        "country": data.get("countryId") or None,
        "hair_color": data.get("hairColor") or None,
        "about": data.get("htmlBio") or None,
        "avatar_url": avatar,
        "url": f"https://cam4.com/{name}",
    }

    return StreamerInfo(payload=info)


def fetch_info_livejasmin(streamer_name: str) -> StreamerInfo | None:
    name = (streamer_name or "").strip()
    if not name:
        return None

    api_url = f"https://www.livejasmin.com/free/flash/get-performer-details/{quote(name)}"
    envelope = _http_get_json(api_url)
    if not isinstance(envelope, dict):
        return None

    data = envelope.get("data")
    if not isinstance(data, dict):
        return None

    about = data.get("bio") or None
    avatar = data.get("profile_picture_url") or None

    channel_path = data.get("channelsiteurl")
    channel_path = str(channel_path or "").strip()
    if channel_path and not channel_path.startswith("/"):
        channel_path = "/" + channel_path
    profile_url = f"https://livejasmin.com{channel_path}" if channel_path else f"https://livejasmin.com/{name}"

    persons = data.get("persons")
    if not isinstance(persons, list):
        persons = []

    def _collect_person_field(key: str) -> str | None:
        vals: list[str] = []
        for p in persons:
            if not isinstance(p, dict):
                continue
            raw = p.get(key)
            if raw is None:
                continue
            s = str(raw).strip()
            if not s or s.upper() == "N/A":
                continue
            vals.append(s)
        return ", ".join(vals) if vals else None

    info: dict[str, Any] = {
        "name": name,
        "about": about,
        "avatar_url": avatar,
        "url": profile_url,
        "age": _collect_person_field("age"),
        "ethnicity": _collect_person_field("ethnicity"),
        "hair_color": _collect_person_field("hair_color"),
        "gender": _collect_person_field("gender"),
    }

    return StreamerInfo(payload=info)


def fetch_info_bongacams(streamer_name: str) -> StreamerInfo | None:
    name = (streamer_name or "").strip()
    if not name:
        return None

    api_url = f"https://bongacams.com/get-member-chat-data?username={quote(name)}&withMiniProfile=1"
    data = _http_get_json(api_url, extra_headers={"X-Requested-With": "XMLHttpRequest"})
    if not isinstance(data, dict):
        return None

    result = data.get("result")
    if not isinstance(result, dict):
        return None

    mini = result.get("miniProfile")
    if not isinstance(mini, dict):
        return None

    avatar_src: str | None = None
    try:
        profile_photo = mini.get("profilePhoto")
        if isinstance(profile_photo, dict):
            image = profile_photo.get("image")
            if isinstance(image, dict):
                raw_src = image.get("src")
                if isinstance(raw_src, str) and raw_src.strip():
                    avatar_src = raw_src.strip()
    except Exception:
        avatar_src = None

    avatar_url: str | None = None
    if avatar_src:
        # Bongacams commonly returns protocol-relative URLs like "//...".
        if avatar_src.startswith("//"):
            avatar_url = "https:" + avatar_src
        elif avatar_src.startswith("http://") or avatar_src.startswith("https://"):
            avatar_url = avatar_src
        else:
            # Follow the request: always prefix with "https:".
            avatar_url = "https:" + avatar_src

        avatar_url = avatar_url.replace("_s.jpg", "_m.jpg")

    gender: str | None = None
    header = mini.get("header")
    if isinstance(header, dict):
        g = header.get("gender")
        if isinstance(g, str) and g.strip():
            gender = g.strip()

    info: dict[str, Any] = {
        "name": name,
        "gender": gender,
        "url": f"https://bongacams.com/{name}",
        "avatar_url": avatar_url,
    }

    return StreamerInfo(payload=info)


def fetch_info_amateurtv(streamer_name: str) -> StreamerInfo | None:
    name = (streamer_name or "").strip()
    if not name:
        return None

    api_url = f"https://www.amateur.tv/v3/readmodel/cache/show/{quote(name)}"
    data = _http_get_json(api_url)
    if not isinstance(data, dict):
        return None

    user = data.get("user")
    if not isinstance(user, dict):
        user = {}

    # Genre: example {"genre": {"type": "W"}}
    gender: str | None = None
    genre = user.get("genre")
    if isinstance(genre, dict):
        g = genre.get("type")
        if isinstance(g, str) and g.strip():
            gender = g.strip()

    # Age: example "age": [20]
    age: str | int | None = None
    raw_age = user.get("age")
    if isinstance(raw_age, list) and raw_age:
        v0 = raw_age[0]
        if isinstance(v0, (int, float)):
            age = int(v0)
        elif isinstance(v0, str) and v0.strip():
            age = v0.strip()
    elif isinstance(raw_age, (int, float)):
        age = int(raw_age)
    elif isinstance(raw_age, str) and raw_age.strip():
        age = raw_age.strip()

    avatar_url: str | None = None
    avatar = data.get("avatar")
    if isinstance(avatar, dict):
        a = avatar.get("big")
        if isinstance(a, str) and a.strip():
            avatar_url = a.strip()

    country: str | None = None
    c = data.get("country")
    if isinstance(c, str) and c.strip():
        country = c.strip()
    else:
        c2 = user.get("country")
        if isinstance(c2, str) and c2.strip():
            country = c2.strip()

    about: str | None = None
    ab = data.get("aboutMe")
    if isinstance(ab, str) and ab.strip():
        about = ab.strip()
    else:
        ab2 = user.get("aboutMe")
        if isinstance(ab2, str) and ab2.strip():
            about = ab2.strip()

    info: dict[str, Any] = {
        "name": name,
        "gender": gender,
        "age": age,
        "country": country,
        "about": about,
        "avatar_url": avatar_url,
        "url": f"https://www.amateur.tv/{name}",
    }

    return StreamerInfo(payload=info)


def fetch_streamer_info(site_name: str, streamer_name: str) -> StreamerInfo | None:
    site = (site_name or "").strip().lower()
    site_key = re.sub(r"\s+", "", site)
    if site == "chaturbate":
        return fetch_info_chaturbate(streamer_name)
    if site == "stripchat":
        return fetch_info_stripchat(streamer_name)
    if site == "cam4":
        return fetch_info_cam4(streamer_name)
    if site_key in {"livejasmin", "livejasmin.com"}:
        return fetch_info_livejasmin(streamer_name)
    if site_key == "bongacams":
        return fetch_info_bongacams(streamer_name)
    if site_key in {"amateurtv", "amateur.tv", "amateur"}:
        return fetch_info_amateurtv(streamer_name)

    # Unknown / unsupported for now.
    return None


def is_fetch_supported(site_name: str) -> bool:
    """Return True if `fetch_streamer_info` has an implementation for the given site."""

    site = (site_name or "").strip().lower()
    if not site or site == "unknown":
        return False

    site_key = re.sub(r"\s+", "", site)
    if site in {"chaturbate", "stripchat", "cam4"}:
        return True
    if site_key in {"livejasmin", "livejasmin.com"}:
        return True
    if site_key == "bongacams":
        return True
    if site_key in {"amateurtv", "amateur.tv", "amateur"}:
        return True
    return False

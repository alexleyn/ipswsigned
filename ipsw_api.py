"""
API wrappers for ipsw.me (release) and ipsw.dev (beta/dev) firmware data.
"""
import logging
import re
from typing import List, Optional, Union

import aiohttp
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

IPSW_ME_API = "https://api.ipsw.me/v4"
IPSW_DEV_SIGNING = "https://ipsw.dev/signing/"

TIMEOUT = aiohttp.ClientTimeout(total=30)
HEADERS = {"User-Agent": "IPSW-Monitor-Bot/1.0 (github.com/ipsw-monitor)"}

# Prefix → (display_name, emoji)
DEVICE_TYPES = [
    ("iPhone",         "iPhone",       "📱"),
    ("iPad",           "iPad",         "📱"),
    ("Mac",            "Mac",          "💻"),
    ("Watch",          "Apple Watch",  "⌚"),
    ("AppleTV",        "Apple TV",     "📺"),
    ("AudioAccessory", "HomePod",      "🔊"),
    ("iPod",           "iPod touch",   "🎵"),
]

_devices_cache: Optional[list] = None


async def _fetch_json(url: str, params: dict = None) -> Optional[Union[dict, list]]:
    try:
        async with aiohttp.ClientSession(headers=HEADERS, timeout=TIMEOUT) as session:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    return await resp.json(content_type=None)
    except Exception as e:
        logger.error("fetch_json %s: %s", url, e)
    return None


async def _fetch_text(url: str, params: dict = None) -> Optional[str]:
    try:
        async with aiohttp.ClientSession(headers=HEADERS, timeout=TIMEOUT) as session:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    return await resp.text()
    except Exception as e:
        logger.error("fetch_text %s: %s", url, e)
    return None


# ---------- device list ----------

async def get_all_devices() -> list:
    global _devices_cache
    if _devices_cache is not None:
        return _devices_cache
    data = await _fetch_json(f"{IPSW_ME_API}/devices")
    if data:
        _devices_cache = data
    return _devices_cache or []


async def get_devices_by_prefix(prefix: str) -> list:
    all_devices = await get_all_devices()
    filtered = [d for d in all_devices if d["identifier"].startswith(prefix)]

    def sort_key(d):
        ident = d["identifier"]
        tail = ident[len(prefix):]          # e.g. "13,1"
        parts = tail.lstrip(",").split(",")
        try:
            return (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
        except (ValueError, IndexError):
            return (0, 0)

    filtered.sort(key=sort_key, reverse=True)
    return filtered


def extract_generation(name: str, prefix: str) -> str:
    """Return the lineup/generation label for a device name.

    E.g. "iPhone 16 Pro Max" → "iPhone 16",
         "iPad Pro 12.9-inch (M4)" → "iPad Pro",
         "Apple Watch Series 10 (46mm)" → "Series 10"
    """
    if prefix == "iPhone":
        for suffix in [" Pro Max", " Pro", " Plus", " mini"]:
            if name.endswith(suffix):
                name = name[: -len(suffix)]
                break
        # Strip "(Nth generation)" for SE etc.
        name = re.sub(r"\s*\(\d[^)]*\)", "", name)
        return name.strip()

    if prefix == "iPad":
        m = re.match(r"(iPad(?:\s+(?:Pro|Air|mini))?)", name)
        return m.group(1) if m else name

    if prefix == "Mac":
        for line in ["MacBook Pro", "MacBook Air", "Mac mini", "Mac Pro", "Mac Studio", "iMac"]:
            if name.startswith(line):
                return line
        return name.split()[0] if name else name

    if prefix == "Watch":
        name = name.replace("Apple Watch ", "")
        name = re.sub(r"\s*\([^)]*\)", "", name).strip()   # remove (Nth gen) / (mm)
        name = re.sub(r"\s+\d+mm.*", "", name).strip()     # remove trailing size
        return name

    if prefix == "AppleTV":
        m = re.match(r"(Apple TV \d+K|Apple TV HD|Apple TV)", name)
        return m.group(1) if m else name

    if prefix == "AudioAccessory":
        m = re.match(r"(HomePod(?:\s+mini)?)", name)
        return m.group(1) if m else name

    return name


def group_devices_by_generation(devices: list, prefix: str) -> list:
    """Return [(generation_label, [device, ...]), ...] preserving newest-first order."""
    from collections import OrderedDict
    groups: "OrderedDict[str, list]" = __import__("collections").OrderedDict()
    for d in devices:
        gen = extract_generation(d["name"], prefix)
        groups.setdefault(gen, []).append(d)
    return list(groups.items())


async def resolve_device_name(identifier: str) -> str:
    """Return human-readable name for an identifier, e.g. 'iPhone 12 mini'."""
    all_devices = await get_all_devices()
    for d in all_devices:
        if d["identifier"] == identifier:
            return d["name"]
    return identifier


# ---------- firmwares ----------

def _normalize_fw(fw: dict) -> dict:
    return {
        "version": fw.get("version", ""),
        "buildid": fw.get("buildid", ""),
        "signed":  bool(fw.get("signed")),
    }


async def get_release_firmwares(identifier: str) -> tuple[list, list, str]:
    """
    Returns (signed_list, all_list, device_name).
    Each item: {version, buildid, signed}.
    """
    data = await _fetch_json(f"{IPSW_ME_API}/device/{identifier}", {"type": "ipsw"})
    if not data:
        return [], [], identifier
    device_name = data.get("name", identifier)
    all_fw = [_normalize_fw(f) for f in data.get("firmwares", [])]
    signed = [f for f in all_fw if f["signed"]]
    return signed, all_fw, device_name


async def get_dev_firmwares(identifier: str) -> tuple[list, list]:
    """
    Scrapes ipsw.dev for beta/dev firmware signing status.
    Returns (signed_list, all_list).
    """
    html = await _fetch_text(IPSW_DEV_SIGNING, {"identifier": identifier})
    if html:
        return _parse_ipsw_dev(html, identifier)
    return [], []


def _parse_ipsw_dev(html: str, identifier: str) -> tuple[list, list]:
    """
    Parse ipsw.dev signing page.
    Each <tr class="firmware"> has:
      - data-signed="true"/"false"
      - onclick="window.location = '/download/{id}/{buildid}';"
      - version inside .versions-info .font-semibold (older markup used <strong>)
    """
    soup = BeautifulSoup(html, "html.parser")
    results = []

    build_re = re.compile(r"/download/[^/]+/([^'\"]+)")

    for row in soup.select("tr.firmware"):
        signed = row.get("data-signed", "false").lower() == "true"

        onclick = row.get("onclick", "")
        m = build_re.search(onclick)
        buildid = m.group(1) if m else ""
        if not buildid:
            build_el = row.select_one(".build-id")
            if build_el:
                buildid = build_el.get_text(strip=True)

        version_el = row.select_one(".versions-info .font-semibold") or row.find("strong")
        version = version_el.get_text(strip=True) if version_el else ""

        if not version:
            continue

        results.append({"version": version, "buildid": buildid, "signed": signed})

    signed_list = [r for r in results if r["signed"]]
    return signed_list, results


# ---------- combined ----------

async def get_all_signed_firmwares(identifier: str):
    """
    Returns (release_signed, dev_signed, device_name).
    """
    release_signed, _, device_name = await get_release_firmwares(identifier)
    dev_signed, _ = await get_dev_firmwares(identifier)
    return release_signed, dev_signed, device_name


async def get_full_snapshot(identifier: str) -> tuple[list, list, str]:
    """
    Returns (release_all, dev_all, device_name) — full lists (signed + unsigned).
    Used for change detection.
    """
    _, release_all, device_name = await get_release_firmwares(identifier)
    _, dev_all = await get_dev_firmwares(identifier)
    return release_all, dev_all, device_name

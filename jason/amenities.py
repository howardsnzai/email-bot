"""Nearby-amenity lookup (Google Geocoding + Places) and amenity image
search (Google Custom Search JSON API, image mode).

Results are always surfaced to the agent as suggestions to confirm — never
asserted facts. That framing lives in Jason's prompt; this module just
fetches data.
"""

from __future__ import annotations

import httpx

from jason import config

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
PLACES_NEARBY_URL = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
CSE_URL = "https://www.googleapis.com/customsearch/v1"

# Marketable place types worth showing off in a listing video.
AMENITY_TYPES = [
    ("park", "park"),
    ("school", "school"),
    ("beach", "natural_feature"),
    ("cafe", "cafe"),
    ("shopping", "shopping_mall"),
    ("gym", "gym"),
]
SEARCH_RADIUS_M = 2000


def geocode(address: str) -> tuple[float, float] | None:
    r = httpx.get(
        GEOCODE_URL,
        params={"address": address, "key": config.GOOGLE_MAPS_API_KEY},
        timeout=15,
    )
    r.raise_for_status()
    results = r.json().get("results") or []
    if not results:
        return None
    loc = results[0]["geometry"]["location"]
    return loc["lat"], loc["lng"]


def lookup_amenities(address: str) -> dict:
    if not config.GOOGLE_MAPS_API_KEY:
        return {"error": "amenity lookup unavailable (no GOOGLE_MAPS_API_KEY configured)"}
    coords = geocode(address)
    if coords is None:
        return {"error": f"could not geocode address: {address!r}"}
    lat, lng = coords
    found: list[dict] = []
    seen: set[str] = set()
    for label, place_type in AMENITY_TYPES:
        r = httpx.get(
            PLACES_NEARBY_URL,
            params={
                "location": f"{lat},{lng}",
                "radius": SEARCH_RADIUS_M,
                "type": place_type,
                "key": config.GOOGLE_MAPS_API_KEY,
            },
            timeout=15,
        )
        r.raise_for_status()
        for place in (r.json().get("results") or [])[:3]:
            name = place.get("name")
            if not name or name in seen:
                continue
            seen.add(name)
            found.append(
                {
                    "name": name,
                    "category": label,
                    "rating": place.get("rating"),
                    "vicinity": place.get("vicinity"),
                }
            )
    return {"address": address, "suggestions": found}


def search_amenity_images(feature: str, address: str, max_results: int = 4) -> dict:
    """Google image search for a confirmed amenity near the property."""
    if not (config.GOOGLE_CSE_API_KEY and config.GOOGLE_CSE_ID):
        return {"error": "image search unavailable (GOOGLE_CSE_API_KEY / GOOGLE_CSE_ID not configured)"}
    query = f"{feature} near {address}"
    r = httpx.get(
        CSE_URL,
        params={
            "key": config.GOOGLE_CSE_API_KEY,
            "cx": config.GOOGLE_CSE_ID,
            "q": query,
            "searchType": "image",
            "num": max_results,
            "imgSize": "xlarge",
            "safe": "active",
        },
        timeout=15,
    )
    r.raise_for_status()
    items = r.json().get("items") or []
    return {
        "feature": feature,
        "images": [
            {
                "url": it.get("link"),
                "title": it.get("title"),
                "source": (it.get("image") or {}).get("contextLink"),
                "size": [
                    (it.get("image") or {}).get("width"),
                    (it.get("image") or {}).get("height"),
                ],
            }
            for it in items
        ],
    }

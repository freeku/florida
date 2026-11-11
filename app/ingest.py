"""Instagram-post -> tekst -> Claude-kandidaten -> Places-resolve -> POI-database."""
import base64
import functools
import json
import math
import os
import re

import httpx

import db

CATEGORIES = [c.strip() for c in os.getenv(
    "CATEGORIES",
    "Dingen om te doen,Restaurants,(Sport)bars,Stranden & natuur,Winkelen,Overig",
).split(",")]
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")
GOOGLE_KEY = os.getenv("GOOGLE_PLACES_KEY", "")
IG_USER = os.getenv("IG_USER", "")
IG_SESSION_FILE = os.getenv("IG_SESSION_FILE", "/data/ig-session")

MIAMI = (25.7617, -80.1918)
KEY_LARGO = (25.0865, -80.4473)
# Basispunten voor het afstandsfilter in de UI (naam -> lat, lng)
BASES = {
    "Miami": MIAMI,
    "Key Largo": KEY_LARGO,
    "Kennedy Space Center": (28.5729, -80.6490),
    "Cocoa Beach": (28.3200, -80.6076),
    "Indialantic (overnachting)": (28.0870, -80.5650),
    "Orlando": (28.5383, -81.3792),
}

SHORTCODE_RE = re.compile(r"instagram\.com/(?:[\w.]+/)?(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)")


def shortcodes_from_text(text: str):
    seen, out = set(), []
    for m in SHORTCODE_RE.finditer(text):
        sc = m.group(1)
        if sc not in seen:
            seen.add(sc)
            out.append(sc)
    return out


TT_VIDEO_RE = re.compile(r"tiktok\.com/(?:@[\w.\-]+/video|embed/v2|v)/(\d+)")
TT_SHORT_RE = re.compile(r"(?:(?:vm|vt)\.tiktok\.com/|tiktok\.com/t/)([A-Za-z0-9]+)")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}


def _resolve_tiktok_short(url: str) -> tuple[str, str] | None:
    """Volgt de redirect van een korte TikTok-link: (video-ID, volledige URL). None als dat niet lukt."""
    try:
        r = httpx.get(url, follow_redirects=True, timeout=8, headers=UA)
        m = TT_VIDEO_RE.search(str(r.url))
        return (m.group(1), str(r.url).split("?")[0]) if m else None
    except Exception as e:
        print(f"[ingest] short-link {url} niet op te lossen: {e}")
        return None


def refs_from_text(text: str) -> list[dict]:
    """Alle ondersteunde post-URL's in een tekst: [{key, url}]. Instagram: key=shortcode; TikTok: key='tt_<video-id>'."""
    out, seen = [], set()

    def add(key, url):
        if key not in seen:
            seen.add(key)
            out.append({"key": key, "url": url})
    for sc in shortcodes_from_text(text):
        add(sc, f"https://www.instagram.com/p/{sc}/")
    for m in re.finditer(r"https?://[^\s<>\"')]*tiktok\.com/[^\s<>\"')]*", text):
        u = m.group(0).rstrip(".,;")
        v = TT_VIDEO_RE.search(u)
        if v:
            add(f"tt_{v.group(1)}", u.split("?")[0])
            continue
        sh = TT_SHORT_RE.search(u)
        if sh:
            res = _resolve_tiktok_short(u)
            add(f"tt_{res[0]}" if res else f"tts_{sh.group(1)}", res[1] if res else u.split("?")[0])
    return out


def haversine_km(a, b):
    R = 6371.0
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    d = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return round(2 * R * math.asin(math.sqrt(d)), 1)


# ---------- 1. Instagram ophalen ----------
@functools.lru_cache(maxsize=8)
def _ig_post(shortcode: str):
    """Instaloader-Post (gecachet, zodat tekst + afbeeldingen één Instagram-request kosten)."""
    import instaloader
    L = instaloader.Instaloader(quiet=True, download_pictures=False, download_videos=False,
                                download_video_thumbnails=False, save_metadata=False)
    if IG_USER and os.path.exists(IG_SESSION_FILE):
        L.load_session_from_file(IG_USER, IG_SESSION_FILE)
    return instaloader.Post.from_shortcode(L.context, shortcode)


MAX_IMAGES = int(os.getenv("MAX_IMAGES", "10"))


def fetch_post_images(shortcode: str, limit: int = MAX_IMAGES) -> list[tuple[str, bytes]]:
    """Slides van een Instagram-post (carrousel of enkele foto) als [(media_type, bytes)]. Video's: alleen het omslagbeeld."""
    try:
        post = _ig_post(shortcode)
        if post.typename == "GraphSidecar":
            urls = [n.display_url for n in post.get_sidecar_nodes()][:limit]
        else:
            urls = [post.url]
        out = []
        for u in urls:
            r = httpx.get(u, timeout=20, headers=UA)
            r.raise_for_status()
            ct = r.headers.get("content-type", "image/jpeg").split(";")[0]
            if ct in ("image/jpeg", "image/png", "image/webp", "image/gif") and len(r.content) < 4_500_000:
                out.append((ct, r.content))
        return out
    except Exception as e:
        print(f"[ingest] afbeeldingen {shortcode} niet op te halen: {e}")
        return []


def fetch_post_text(shortcode: str) -> str | None:
    """Caption + locatietag via Instaloader. Geeft None als ophalen mislukt (-> needs_text)."""
    try:
        post = _ig_post(shortcode)
        parts = [f"Account: @{post.owner_username}", f"Caption: {post.caption or ''}"]
        if post.location:
            parts.append(f"Locatietag: {post.location.name} ({post.location.lat}, {post.location.lng})")
        return "\n".join(parts)
    except Exception as e:  # login-wall, rate limit, verwijderd, ...
        print(f"[ingest] fetch {shortcode} mislukt: {e}")
        return None


def fetch_tiktok_text(url: str) -> str | None:
    """Caption + account via TikTok oEmbed (zonder login). Veel TikToks noemen de plek alleen in beeld/geluid -> dan needs_text."""
    try:
        r = httpx.get("https://www.tiktok.com/oembed", params={"url": url}, timeout=15, headers=UA)
        r.raise_for_status()
        j = r.json()
        if not (j.get("title") or "").strip():
            return None
        return f"Account: @{j.get('author_unique_id') or j.get('author_name', '')}\nCaption: {j['title']}"
    except Exception as e:
        print(f"[ingest] tiktok oembed {url} mislukt: {e}")
        return None


# ---------- 2. Claude parse ----------
SYSTEM = f"""Je haalt concrete, bezoekbare plekken uit social-mediaposts (Instagram of TikTok) over Miami/Florida (reis van een gezin met twee jonge kinderen).
Geef UITSLUITEND een JSON-array. Elk item:
{{"name": str, "city": str, "category": één van {CATEGORIES}, "confidence": 0..1, "evidence": korte letterlijke aanwijzing uit de tekst}}
Regels: alleen specifieke plekken (geen 'Miami' of 'het strand' in het algemeen); zet de stad/regio erbij als die uit de tekst blijkt;
neem een plek niet op als je de naam moet verzinnen; lege array als er niets concreets staat."""


def _extract_json(msg) -> list[dict]:
    raw = "".join(b.text for b in msg.content if b.type == "text").strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip()
    data = json.loads(raw)
    return data if isinstance(data, list) else []


def parse_images_with_claude(images: list[tuple[str, bytes]], text: str, url: str) -> list[dict]:
    """Voor posts waarvan de tips in de beelden staan (carrousel met tekst-slides): Claude leest de afbeeldingen."""
    import anthropic
    client = anthropic.Anthropic()
    content = []
    for i, (mt, data) in enumerate(images, 1):
        content.append({"type": "text", "text": f"Slide {i}:"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": mt, "data": base64.b64encode(data).decode()}})
    content.append({"type": "text", "text": f"Post: {url}\n\n{text}\n\nDe tips staan (mede) in de afbeeldingen hierboven. Lees de tekst in beeld en haal de plekken eruit; "
                                            "geef bij 'evidence' kort aan op welke slide en wat er staat."})
    msg = client.messages.create(model=CLAUDE_MODEL, max_tokens=3000, system=SYSTEM, messages=[{"role": "user", "content": content}])
    return _extract_json(msg)


def parse_with_claude(text: str, url: str) -> list[dict]:
    import anthropic
    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=CLAUDE_MODEL, max_tokens=2000, system=SYSTEM,
        messages=[{"role": "user", "content": f"Post: {url}\n\n{text}"}],
    )
    return _extract_json(msg)


# ---------- 3. Resolve naar echte plek ----------
def resolve_place(name: str, city: str) -> dict | None:
    q = ", ".join(x for x in (name, city, "Florida") if x)
    if GOOGLE_KEY:
        r = httpx.post(
            "https://places.googleapis.com/v1/places:searchText",
            headers={"X-Goog-Api-Key": GOOGLE_KEY,
                     "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress,places.location,places.googleMapsUri"},
            json={"textQuery": q, "maxResultCount": 1,
                  "locationBias": {"circle": {"center": {"latitude": MIAMI[0], "longitude": MIAMI[1]}, "radius": 50000.0}}},
            timeout=20)
        r.raise_for_status()
        pl = (r.json().get("places") or [None])[0]
        if not pl:
            return None
        return {"place_id": pl["id"], "name": pl["displayName"]["text"], "address": pl.get("formattedAddress"),
                "lat": pl["location"]["latitude"], "lng": pl["location"]["longitude"], "gmaps_url": pl.get("googleMapsUri")}
    # Fallback zonder Google-key: OpenStreetMap Nominatim (minder precies voor POI's)
    r = httpx.get("https://nominatim.openstreetmap.org/search",
                  params={"q": q, "format": "jsonv2", "limit": 1, "addressdetails": 0},
                  headers={"User-Agent": "florida-poi/0.1 (private trip planner)"}, timeout=20)
    r.raise_for_status()
    hit = (r.json() or [None])[0]
    if not hit:
        return None
    return {"place_id": f"osm:{hit['osm_type']}{hit['osm_id']}", "name": hit["display_name"].split(",")[0],
            "address": hit["display_name"], "lat": float(hit["lat"]), "lng": float(hit["lon"]),
            "gmaps_url": f"https://www.openstreetmap.org/{hit['osm_type']}/{hit['osm_id']}"}


# Verblijven (getoond als huis-icoon op de kaart)
STAYS = [
    {"name": "Hampton Inn & Suites Orlando Airport", "query": "Hampton Inn & Suites Orlando Airport at Gateway Village, 5460 Gateway Village Cir, Orlando, FL 32812", "dates": "20–21 dec"},
    {"name": "BLEU Beach Resort", "query": "BLEU Beach Resort, 501 N Miramar Ave, Indialantic, FL 32903", "dates": "21–22 dec"},
    {"name": "Kenmore Village Hotel", "query": "Kenmore Village Hotel, 1050 Washington Ave, Miami Beach, FL 33139", "dates": "22–24 dec"},
    {"name": "Reefhouse Resort", "query": "Reefhouse Resort, 103800 Overseas Hwy, Key Largo, FL 33037", "dates": "24–26 dec"},
    {"name": "Airbnb Miami", "query": "3450 SW 23rd Terrace, Miami, FL 33145", "dates": "26 dec – 1 jan"},
]


def _norm(s: str) -> str:
    s = re.sub(r"[^a-z0-9 ]", "", s.lower())
    return re.sub(r"\b(the|de|restaurant|bar|cafe|café)\b", "", s).strip()


def find_near_duplicate(place: dict, max_km: float = 0.25):
    """Zelfde plek onder een ander place_id: <250 m weg én vergelijkbare naam."""
    from difflib import SequenceMatcher
    n = _norm(place["name"])
    for p in db.rows("SELECT id,name,lat,lng FROM pois"):
        if haversine_km((p["lat"], p["lng"]), (place["lat"], place["lng"])) > max_km:
            continue
        m = _norm(p["name"])
        if n and m and (n in m or m in n or SequenceMatcher(None, n, m).ratio() > 0.75):
            return {"id": p["id"]}
    return None


def upsert_poi(shortcode: str, cand: dict, place: dict):
    pt = (place["lat"], place["lng"])
    cat = cand.get("category") if cand.get("category") in CATEGORIES else "Overig"
    existing = db.one("SELECT id FROM pois WHERE place_id=?", (place["place_id"],)) or find_near_duplicate(place)
    if existing:
        poi_id = existing["id"]
    else:
        poi_id = db.run(
            "INSERT INTO pois(place_id,name,address,lat,lng,category,gmaps_url,km_miami,km_keylargo) VALUES(?,?,?,?,?,?,?,?,?)",
            (place["place_id"], place["name"], place["address"], place["lat"], place["lng"], cat,
             place["gmaps_url"], haversine_km(pt, MIAMI), haversine_km(pt, KEY_LARGO)))
    db.run("INSERT OR REPLACE INTO poi_sources(poi_id,shortcode,confidence,evidence) VALUES(?,?,?,?)",
           (poi_id, shortcode, cand.get("confidence"), cand.get("evidence")))
    return poi_id


# ---------- pipeline ----------
def process(shortcode: str):
    post = db.one("SELECT * FROM posts WHERE shortcode=?", (shortcode,))
    if not post:
        return
    text = post["caption"] or (fetch_tiktok_text(post["url"]) if shortcode.startswith("tt") else fetch_post_text(shortcode))
    if not text:
        db.run("UPDATE posts SET status='needs_text', error=NULL WHERE shortcode=?", (shortcode,))
        return
    db.run("UPDATE posts SET caption=? WHERE shortcode=?", (text, shortcode))
    try:
        cands = parse_with_claude(text, post["url"])
        if not cands and not shortcode.startswith("tt"):  # niets in de caption: tips zitten waarschijnlijk in de beelden
            imgs = fetch_post_images(shortcode)
            if imgs:
                cands = parse_images_with_claude(imgs, text, post["url"])
        result = []
        for c in cands:
            place = resolve_place(c.get("name", ""), c.get("city", ""))
            entry = {**c, "resolved": bool(place)}
            if place:
                entry["poi_id"] = upsert_poi(shortcode, c, place)
            result.append(entry)
        db.run("UPDATE posts SET status='parsed', error=NULL, parsed=? WHERE shortcode=?", (db.jdump(result), shortcode))
    except Exception as e:
        db.run("UPDATE posts SET status='error', error=? WHERE shortcode=?", (str(e)[:500], shortcode))

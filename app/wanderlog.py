"""Sync van goedgekeurde POI's naar lijsten in een Wanderlog-trip.

Onofficieel: gebruikt Wanderlogs interne web-API met je sessiecookie (connect.sid).
Lezen/zoeken via REST, schrijven via ShareDB (JSON0-ops) over een WebSocket.
Protocol en blokvorm zijn overgenomen uit de MIT-gelicenseerde wanderlog-mcp
(shaikhspeare/wanderlog-mcp, ZawYePhyo/wanderlog_mcp).

Env:
  WANDERLOG_COOKIE    waarde van de cookie 'connect.sid' (s%3A...) van wanderlog.com
  WANDERLOG_TRIP_KEY  de code uit de trip-URL (wanderlog.com/plan/<key>/...)
  WANDERLOG_LISTS     optioneel JSON: categorie in de app -> lijstnaam in Wanderlog
"""
import asyncio
import json
import os
import random
import uuid
from urllib.parse import quote

import httpx
from websockets.asyncio.client import connect

import db

BASE = os.getenv("WANDERLOG_BASE_URL", "https://wanderlog.com")
WS_BASE = os.getenv("WANDERLOG_WS_BASE_URL", "wss://wanderlog.com")
UA = os.getenv("WANDERLOG_USER_AGENT",
               "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36")
DEFAULT_LISTS = {"Dingen om te doen": "Places to visit", "Winkelen": "Shoppen"}


class WanderlogError(Exception):
    pass


def _cookie() -> str:
    raw = os.getenv("WANDERLOG_COOKIE", "").strip()
    if not raw:
        raise WanderlogError("WANDERLOG_COOKIE is niet gezet")
    if "connect.sid=" in raw:
        return raw
    if raw.startswith(("s%3A", "s:")):
        return f"connect.sid={raw}"
    raise WanderlogError("WANDERLOG_COOKIE ziet er niet uit als een connect.sid-waarde (verwacht 's%3A…')")


def _trip_key() -> str:
    k = os.getenv("WANDERLOG_TRIP_KEY", "").strip()
    if not k:
        raise WanderlogError("WANDERLOG_TRIP_KEY is niet gezet (de code uit de trip-URL)")
    return k


def list_map() -> dict:
    m = dict(DEFAULT_LISTS)
    try:
        m.update(json.loads(os.getenv("WANDERLOG_LISTS", "") or "{}"))
    except ValueError:
        raise WanderlogError("WANDERLOG_LISTS is geen geldige JSON")
    return m


class Rest:
    def __init__(self, client: httpx.AsyncClient, cookie: str):
        self.c, self.cookie = client, cookie

    async def get(self, path: str):
        r = await self.c.get(BASE + path, headers={
            "Accept": "application/json, text/plain, */*", "Accept-Language": "en",
            "Cookie": self.cookie, "Origin": BASE, "Referer": BASE + "/", "User-Agent": UA})
        if r.status_code in (401, 403):
            raise WanderlogError("Wanderlog weigert de sessie: haal een nieuwe cookie op (connect.sid)")
        if r.status_code >= 400:
            raise WanderlogError(f"Wanderlog gaf {r.status_code} op {path.split('?')[0]}")
        return r.json()

    async def user_id(self) -> int:
        u = (await self.get("/api/user")).get("user") or {}
        if not isinstance(u.get("id"), int):
            raise WanderlogError("Geen gebruiker bij deze sessie: cookie ongeldig of verlopen")
        return u["id"]

    async def place_detail(self, place_id: str):
        env = await self.get(f"/api/placesAPI/getPlaceDetails/v2?placeId={quote(place_id)}&language=en")
        return env.get("data")

    async def autocomplete(self, name: str, lat: float, lng: float):
        req = {"input": name, "sessiontoken": str(uuid.uuid4()),
               "location": {"latitude": lat, "longitude": lng}, "radius": 2000, "language": "en"}
        env = await self.get("/api/placesAPI/autocomplete/v2?request=" +
                             quote(json.dumps(req, separators=(",", ":"))))
        return env.get("data") or []


class TripSession:
    """ShareDB (JSON0) client voor één trip."""

    def __init__(self, key: str, cookie: str):
        self.key, self.cookie = key, cookie
        self.ws = None
        self.session = None
        self.version = 0
        self.snapshot = None
        self.seq = 0

    async def _recv(self, timeout: float = 15.0) -> dict:
        raw = await asyncio.wait_for(self.ws.recv(), timeout)
        return json.loads(raw)

    async def open(self):
        url = f"{WS_BASE}/api/tripPlans/wsOverall/{quote(self.key)}?clientSchemaVersion=2"
        self.ws = await connect(url, additional_headers={"Cookie": self.cookie, "Origin": BASE, "User-Agent": UA},
                                open_timeout=15)
        await self.ws.send(json.dumps({"a": "hs", "id": None, "protocol": 1, "protocolMinor": 2}))
        while True:
            f = await self._recv()
            if f.get("error"):
                raise WanderlogError(f"Wanderlog websocket: {f['error']}")
            if f.get("a") == "init":
                self.session = f.get("id")
            elif f.get("a") == "hs":
                self.session = self.session or f.get("id")
                break
        await self.ws.send(json.dumps({"a": "s", "c": "TripPlans", "d": self.key}))
        while True:
            f = await self._recv()
            if f.get("error"):
                raise WanderlogError(f"Wanderlog websocket: {f['error']}")
            if f.get("a") == "s":
                data = f.get("data")
                if not data:
                    raise WanderlogError("Trip niet gevonden of geen toegang (klopt WANDERLOG_TRIP_KEY?)")
                self.snapshot, self.version = data["data"], data["v"]
                return
            if f.get("a") == "op" and f.get("op"):
                self.version = f["v"] + 1

    async def submit(self, ops: list):
        self.seq += 1
        seq = self.seq
        await self.ws.send(json.dumps({"a": "op", "c": "TripPlans", "d": self.key, "v": self.version,
                                       "seq": seq, "x": {}, "op": ops}))
        while True:
            f = await self._recv(10.0)
            if f.get("error"):
                err = f["error"]
                raise WanderlogError(f"Wanderlog weigerde de wijziging: {err.get('message', err) if isinstance(err, dict) else err}")
            if f.get("a") == "op":
                if f.get("src") == self.session and f.get("seq") == seq:
                    self.version = f["v"] + 1
                    return
                if f.get("op"):  # wijziging van een andere client
                    self.version = f["v"] + 1

    async def close(self):
        if self.ws:
            await self.ws.close()


def find_list(snapshot: dict, heading: str):
    """Sectie van type placeList met deze kopnaam (hoofdletterongevoelig). 'Places to visit' mag ook kopje '' hebben."""
    want = heading.strip().lower()
    for i, s in enumerate(snapshot["itinerary"]["sections"]):
        if s.get("mode") != "placeList":
            continue
        h = (s.get("heading") or "").strip().lower()
        if h == want or (want == "places to visit" and h == "" and s.get("type") == "normal"):
            return i, s
    return None


def list_headings(snapshot: dict) -> list:
    return [(s.get("heading") or "(naamloos)") for s in snapshot["itinerary"]["sections"] if s.get("mode") == "placeList"]


def existing_place_ids(snapshot: dict) -> set:
    ids = set()
    for s in snapshot["itinerary"]["sections"]:
        for b in s.get("blocks", []):
            if b.get("type") == "place":
                pl = b.get("place") or {}
                for k in ("place_id", "placeId"):
                    if pl.get(k):
                        ids.add(pl[k])
    return ids


def build_place_block(detail: dict, user_id: int) -> dict:
    return {"id": random.randint(0, 999_999_999), "type": "place", "place": detail,
            "text": {"ops": [{"insert": "\n"}]}, "addedBy": {"type": "user", "userId": user_id},
            "imageSize": "small", "upvotedBy": [], "travelMode": None, "attachments": []}


async def _open():
    cookie, key = _cookie(), _trip_key()
    client = httpx.AsyncClient(timeout=20)
    rest = Rest(client, cookie)
    user_id = await rest.user_id()
    trip = TripSession(key, cookie)
    await trip.open()
    return client, rest, user_id, trip


async def check() -> dict:
    client, rest, user_id, trip = await _open()
    try:
        have = list_headings(trip.snapshot)
        needed = sorted(set(list_map().get(c, c) for c in
                            [r["category"] for r in db.rows("SELECT DISTINCT category FROM pois")]))
        return {"ok": True, "user_id": user_id, "trip": trip.snapshot.get("title"),
                "lijsten_in_wanderlog": have,
                "ontbrekende_lijsten": [n for n in needed if not find_list(trip.snapshot, n)],
                "mapping": list_map()}
    finally:
        await trip.close()
        await client.aclose()


async def sync(dry_run: bool = True, ids: list | None = None) -> list:
    sql = "SELECT * FROM pois WHERE status='keep' AND synced=0"
    pois = db.rows(sql + " ORDER BY category,name")
    if ids:
        pois = [p for p in pois if p["id"] in set(ids)]
    if not pois:
        return []
    client, rest, user_id, trip = await _open()
    mapping, results = list_map(), []
    try:
        known = existing_place_ids(trip.snapshot)
        for p in pois:
            target = mapping.get(p["category"], p["category"])
            res = {"id": p["id"], "name": p["name"], "lijst": target}
            found = find_list(trip.snapshot, target)
            if not found:
                results.append({**res, "status": "fout", "detail": f"lijst '{target}' bestaat niet in de trip"})
                continue
            idx, section = found
            try:
                detail = None
                if not p["place_id"].startswith("osm:"):
                    try:
                        detail = await rest.place_detail(p["place_id"])
                    except WanderlogError:
                        detail = None
                if not detail:  # OSM-id of onbekend bij Wanderlog: zoek op naam rond de coördinaten
                    preds = await rest.autocomplete(p["name"], p["lat"], p["lng"])
                    if preds:
                        detail = await rest.place_detail(preds[0]["place_id"])
                if not detail:
                    results.append({**res, "status": "fout", "detail": "plek niet gevonden bij Wanderlog"})
                    continue
                pid = detail.get("place_id") or detail.get("placeId")
                if pid and pid in known:
                    if not dry_run:
                        db.run("UPDATE pois SET synced=1 WHERE id=?", (p["id"],))
                    results.append({**res, "status": "stond al in de trip"})
                    continue
                if dry_run:
                    results.append({**res, "status": "proefrun", "detail": f"zou '{detail.get('name')}' toevoegen"})
                    continue
                block = build_place_block(detail, user_id)
                await trip.submit([{"p": ["itinerary", "sections", idx, "blocks", len(section["blocks"])], "li": block}])
                section["blocks"].append(block)
                if pid:
                    known.add(pid)
                db.run("UPDATE pois SET synced=1 WHERE id=?", (p["id"],))
                results.append({**res, "status": "toegevoegd", "detail": detail.get("name")})
                await asyncio.sleep(0.3)
            except WanderlogError as e:
                results.append({**res, "status": "fout", "detail": str(e)})
        return results
    finally:
        await trip.close()
        await client.aclose()

"""Test van wanderlog.py tegen een nagebootste Wanderlog (REST via MockTransport + ShareDB-websocket).
Run: cd app && python -I ../tests/test_wanderlog_fake.py"""
import asyncio, json, os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
os.environ.update(DB_PATH=tempfile.mkdtemp() + "/t.db", WANDERLOG_COOKIE="s%3Atest", WANDERLOG_TRIP_KEY="abc123")
import httpx
from websockets.asyncio.server import serve
import db, wanderlog

db.init()
for i, (pid, name, cat) in enumerate([("g-venetian", "Venetian Pool", "Dingen om te doen"), ("g-joes", "Joe's Stone Crab", "Restaurants"),
                                       ("g-bar", "Some Bar", "(Sport)bars"), ("g-dupe", "Already There", "Restaurants"), ("g-missing", "Nowhere", "Winkelen")], 1):
    db.run("INSERT INTO pois(place_id,name,address,lat,lng,category,status) VALUES(?,?,?,?,?,?,'keep')", (pid, name, "x", 25.7, -80.2, cat))

SNAP = {"title": "Florida", "itinerary": {"sections": [
    {"type": "normal", "mode": "placeList", "heading": "Places to visit", "blocks": []},
    {"type": "normal", "mode": "placeList", "heading": "Restaurants", "blocks": [{"type": "place", "place": {"place_id": "g-dupe"}}]},
    {"type": "normal", "mode": "placeList", "heading": "(Sport)bars", "blocks": []},
    {"type": "normal", "mode": "dayPlan", "heading": "", "date": "2026-12-20", "blocks": []}]}}
ops_seen = []

async def handler(ws):
    await ws.send(json.dumps({"a": "init", "protocol": 1, "id": "sess1"}))
    async for raw in ws:
        m = json.loads(raw)
        if m["a"] == "hs": await ws.send(json.dumps({"a": "hs", "protocol": 1, "id": "sess1"}))
        elif m["a"] == "s": await ws.send(json.dumps({"a": "s", "c": "TripPlans", "d": m["d"], "data": {"v": 7, "data": SNAP}}))
        elif m["a"] == "op":
            assert m["v"] == 7 + len(ops_seen), (m["v"], len(ops_seen))
            ops_seen.append(m["op"])
            await ws.send(json.dumps({"a": "op", "c": "TripPlans", "d": m["d"], "v": m["v"], "src": "sess1", "seq": m["seq"]}))

def rest(req: httpx.Request):
    p = req.url.path
    assert "s%3Atest" in req.headers["cookie"]
    if p == "/api/user": return httpx.Response(200, json={"user": {"id": 42}})
    if p == "/api/placesAPI/getPlaceDetails/v2":
        pid = req.url.params["placeId"]
        return httpx.Response(200, json={"data": {"place_id": pid, "name": pid.upper()}})
    return httpx.Response(404)

async def main():
    real = httpx.AsyncClient
    wanderlog.httpx.AsyncClient = lambda **kw: real(transport=httpx.MockTransport(rest))
    async with serve(handler, "127.0.0.1", 0) as server:
        wanderlog.WS_BASE = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
        chk = await wanderlog.check()
        print("check:", chk)
        assert chk["ontbrekende_lijsten"] == ["Shoppen"] and chk["user_id"] == 42
        dry = await wanderlog.sync(dry_run=True)
        assert not ops_seen and all(r["status"] in ("proefrun", "stond al in de trip", "fout") for r in dry)
        res = await wanderlog.sync(dry_run=False)
        for r in res: print(r)
        by = {r["name"]: r for r in res}
        assert by["Venetian Pool"]["status"] == "toegevoegd" and by["Venetian Pool"]["lijst"] == "Places to visit"
        assert by["Joe's Stone Crab"]["status"] == "toegevoegd" and by["Some Bar"]["status"] == "toegevoegd"
        assert by["Already There"]["status"] == "stond al in de trip"
        assert by["Nowhere"]["status"] == "fout" and "Shoppen" in by["Nowhere"]["detail"]
        # volgorde = categorie alfabetisch: (Sport)bars, Dingen om te doen, Restaurants
        assert [o[0]["p"] for o in ops_seen] == [["itinerary", "sections", 2, "blocks", 0], ["itinerary", "sections", 0, "blocks", 0], ["itinerary", "sections", 1, "blocks", 1]]
        assert ops_seen[0][0]["li"]["addedBy"]["userId"] == 42
        assert (await wanderlog.sync(dry_run=False))[0]["status"] == "fout"  # alleen 'Nowhere' blijft over
        print("ALLES OK")
asyncio.run(main())

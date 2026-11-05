"""Test van wanderlog.py tegen een nagebootste Wanderlog (REST via MockTransport + ShareDB-websocket met echte JSON0 li/ld).
Run: cd app && python -I ../tests/test_wanderlog_fake.py"""
import asyncio, copy, json, os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
os.environ.update(DB_PATH=tempfile.mkdtemp() + "/t.db", WANDERLOG_COOKIE="s%3Atest", WANDERLOG_TRIP_KEY="abc123")
import httpx
from websockets.asyncio.server import serve
import db, wanderlog

db.init()
for pid, name, cat in [("g-venetian", "Venetian Pool", "Dingen om te doen"), ("g-joes", "Joe's Stone Crab", "Restaurants"),
                       ("g-bar", "Some Bar", "(Sport)bars"), ("g-dupe", "Already There", "Restaurants"), ("g-missing", "Nowhere", "Winkelen")]:
    db.run("INSERT INTO pois(place_id,name,address,lat,lng,category,status) VALUES(?,?,?,?,?,?,'keep')", (pid, name, "x", 25.7, -80.2, cat))

def sec(h, blocks=None, **kw): return {"type": "normal", "mode": "placeList", "heading": h, "blocks": blocks or [], **kw}
SERVER = {"v": 7, "snap": {"title": "Florida", "itinerary": {"sections": [
    sec("Places to visit"), sec("Restaurants", [{"id": 1, "type": "place", "place": {"place_id": "g-dupe", "name": "Already There"}}]),
    sec("(Sport)bars"), {"type": "normal", "mode": "dayPlan", "heading": "", "date": "2026-12-20", "blocks": []}]}}}
frames = []

def apply(ops):
    for o in ops:
        lst = SERVER["snap"]
        for k in o["p"][:-1]: lst = lst[k]
        i = o["p"][-1]
        if "ld" in o:
            assert lst[i] == o["ld"], "ld wijkt af van serverstand"; lst.pop(i)
        if "li" in o: lst.insert(i, o["li"])

async def handler(ws):
    await ws.send(json.dumps({"a": "init", "protocol": 1, "id": "sess1"}))
    async for raw in ws:
        m = json.loads(raw)
        if m["a"] == "hs": await ws.send(json.dumps({"a": "hs", "protocol": 1, "id": "sess1"}))
        elif m["a"] == "s": await ws.send(json.dumps({"a": "s", "c": "TripPlans", "d": m["d"], "data": {"v": SERVER["v"], "data": copy.deepcopy(SERVER["snap"])}}))
        elif m["a"] == "op":
            assert m["v"] == SERVER["v"], (m["v"], SERVER["v"])
            apply(m["op"]); frames.append(m["op"]); SERVER["v"] += 1
            await ws.send(json.dumps({"a": "op", "c": "TripPlans", "d": m["d"], "v": m["v"], "src": "sess1", "seq": m["seq"]}))

def rest(req: httpx.Request):
    p = req.url.path
    if p == "/api/user": return httpx.Response(200, json={"user": {"id": 42}})
    if p == "/api/placesAPI/getPlaceDetails/v2":
        pid = req.url.params["placeId"]
        return httpx.Response(200, json={"data": {"place_id": pid, "name": pid.upper()}})
    return httpx.Response(404)

def headings_of(name):
    for s in SERVER["snap"]["itinerary"]["sections"]:
        for b in s["blocks"]:
            if (b["place"].get("name") or "").lower() == name.lower(): return s["heading"]

def row(name): return db.one("SELECT * FROM pois WHERE name=?", (name,))

async def main():
    real = httpx.AsyncClient
    wanderlog.httpx.AsyncClient = lambda **kw: real(transport=httpx.MockTransport(rest))
    async with serve(handler, "127.0.0.1", 0) as server:
        wanderlog.WS_BASE = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
        chk = await wanderlog.check()
        assert chk["ontbrekende_lijsten"] == ["Shoppen"] and chk["user_id"] == 42

        # 1. toevoegen (proefrun schrijft niets)
        dry = await wanderlog.sync(dry_run=True); assert not frames
        res = {r["name"]: r for r in await wanderlog.sync(dry_run=False)}
        assert res["Venetian Pool"]["status"] == "toegevoegd" and res["Already There"]["status"] == "stond al in de trip"
        assert res["Nowhere"]["status"] == "fout" and "Shoppen" in res["Nowhere"]["detail"]
        assert headings_of("G-JOES") == "Restaurants" and headings_of("G-BAR") == "(Sport)bars" and headings_of("G-VENETIAN") == "Places to visit"
        assert row("Joe's Stone Crab")["wl_block_id"] and row("Joe's Stone Crab")["wl_list"] == "Restaurants"
        assert row("Already There")["wl_block_id"] == 1  # al aanwezig: koppeling alsnog vastgelegd
        n = len(frames)

        # 2. niets veranderd -> niets te doen (idempotent)
        assert [r for r in await wanderlog.sync(dry_run=False) if r["name"] != "Nowhere"] == [] and len(frames) == n

        # 3. categorie gewijzigd -> verplaatsen (blok-id blijft gelijk)
        bid = row("Joe's Stone Crab")["wl_block_id"]
        db.run("UPDATE pois SET category='(Sport)bars' WHERE name=?", ("Joe's Stone Crab",))
        d = [r for r in await wanderlog.sync(dry_run=True) if r["name"] == "Joe's Stone Crab"]
        assert d[0]["status"] == "proefrun" and headings_of("G-JOES") == "Restaurants"
        m = [r for r in await wanderlog.sync(dry_run=False) if r["name"] == "Joe's Stone Crab"]
        assert m[0]["status"] == "verplaatst" and headings_of("G-JOES") == "(Sport)bars"
        assert row("Joe's Stone Crab")["wl_block_id"] == bid and row("Joe's Stone Crab")["wl_list"] == "(Sport)bars"

        # 4. oudere POI zonder opgeslagen koppeling wordt toch gevonden (op place_id/naam)
        db.run("UPDATE pois SET wl_block_id=NULL, wl_list=NULL, category='Restaurants' WHERE name=?", ("Venetian Pool",))
        m = [r for r in await wanderlog.sync(dry_run=False) if r["name"] == "Venetian Pool"]
        assert m[0]["status"] == "verplaatst" and headings_of("G-VENETIAN") == "Restaurants"

        # 5. door jou naar een dag gesleept -> met rust laten (ook bij verplaatsen)
        sections = SERVER["snap"]["itinerary"]["sections"]
        blk = next(b for b in sections[1]["blocks"] if b["place"]["name"] == "G-VENETIAN")
        sections[1]["blocks"].remove(blk); sections[3]["blocks"].append(blk); SERVER["v"] += 1
        db.run("UPDATE pois SET category='(Sport)bars' WHERE name=?", ("Venetian Pool",))
        assert [r for r in await wanderlog.sync(dry_run=False) if r["name"] == "Venetian Pool"] == []
        assert any(b["place"]["name"] == "G-VENETIAN" for b in sections[3]["blocks"])

        # 6. uitgevinkt -> verwijderen (alleen als aangekondigd), dag-blok blijft staan
        db.run("UPDATE pois SET status='new' WHERE name IN (?,?)", ("Some Bar", "Venetian Pool"))
        assert [r["status"] for r in await wanderlog.remove(dry_run=True)].count("proefrun") == 1 and headings_of("G-BAR") == "(Sport)bars"
        out = {r["name"]: r for r in await wanderlog.remove(dry_run=False)}
        assert out["Some Bar"]["status"] == "verwijderd" and headings_of("G-BAR") is None
        assert out["Venetian Pool"]["status"] == "laat staan" and any(b["place"]["name"] == "G-VENETIAN" for b in sections[3]["blocks"])
        assert row("Some Bar")["synced"] == 0 and row("Some Bar")["wl_block_id"] is None
        assert headings_of("G-JOES") == "(Sport)bars"  # gehouden POI blijft staan
        print("ALLES OK")
asyncio.run(main())

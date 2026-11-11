# Test: caption leeg -> afbeeldingen via Claude-vision -> POI's. Netwerk/Claude gemockt. Run: python3 tests/test_images.py
import os, sys, tempfile
os.environ["DB_PATH"] = tempfile.mkdtemp() + "/t.db"
sys.path.insert(0, "/home/claude/florida/app")
import db, ingest
db.init()
calls = []
ingest.fetch_post_text = lambda sc: "Account: @x\nCaption: Top tips voor Miami! Swipe ➡️"
ingest.parse_with_claude = lambda t, u: []                       # caption bevat geen plekken
ingest.fetch_post_images = lambda sc: [("image/jpeg", b"x"), ("image/jpeg", b"y")]
ingest.parse_images_with_claude = lambda imgs, t, u: (calls.append(len(imgs)) or [{"name": "Zak the Baker", "city": "Miami", "category": "Restaurants", "confidence": .9, "evidence": "slide 3: Zak the Baker"}])
ingest.resolve_place = lambda n, c: {"place_id": "g1", "name": n, "address": "a", "lat": 25.8, "lng": -80.2, "gmaps_url": None}
db.run("INSERT INTO posts(shortcode,url) VALUES(?,?)", ("IMG1", "https://www.instagram.com/p/IMG1/"))
ingest.process("IMG1")
p = db.one("SELECT * FROM posts WHERE shortcode='IMG1'")
assert p["status"] == "parsed" and calls == [2], (p["status"], calls)
assert db.one("SELECT name FROM pois WHERE place_id='g1'")["name"] == "Zak the Baker"
# caption levert wel plekken op -> geen afbeeldingen nodig
calls.clear(); ingest.parse_with_claude = lambda t, u: [{"name": "Vizcaya", "city": "Miami", "category": "Dingen om te doen", "confidence": .9, "evidence": "e"}]
ingest.resolve_place = lambda n, c: {"place_id": "g2", "name": n, "address": "a", "lat": 25.7, "lng": -80.2, "gmaps_url": None}
db.run("INSERT INTO posts(shortcode,url) VALUES(?,?)", ("IMG2", "https://www.instagram.com/p/IMG2/"))
ingest.process("IMG2"); assert calls == []
print("IMAGES-TEST OK")

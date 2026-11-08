# Headless mobiele UI-test (iPhone-achtig viewport). Run: python3 tests/ui_mobile_test.py  (leaflet 1.9.4 uitgepakt in /tmp/lf/package)
import os, sys, subprocess, time, tempfile, sqlite3
db = tempfile.mkdtemp() + "/poi.db"
srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "main:app", "--port", "8152"], cwd="/home/claude/florida/app", env={**os.environ, "DB_PATH": db}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(3)
c = sqlite3.connect(db)
rows = [("p1","Venetian Pool","2701 De Soto Blvd, Coral Gables, FL",25.7623,-80.2725,"Dingen om te doen"),("p2","Joe's Stone Crab","11 Washington Ave, Miami Beach",25.7694,-80.1347,"Restaurants"),
        ("p3","Vizcaya Museum and Gardens","3251 S Miami Ave, Miami",25.7444,-80.2106,"Dingen om te doen"),("p4","Ver weg","Tampa",27.95,-82.46,"Overig")]
for r in rows: c.execute("INSERT INTO pois(place_id,name,address,lat,lng,category) VALUES(?,?,?,?,?,?)", r)
for i, sc in enumerate(["AAA","BBB","CCC","DDD"], 1):
    c.execute("INSERT INTO posts(shortcode,url,status) VALUES(?,?,?)", (sc, f"https://www.instagram.com/p/{sc}/", "parsed"))
    c.execute("INSERT INTO poi_sources VALUES(?,?,?,?)", (i, sc, .9, f"bewijs voor poi {i}"))
c.commit()
from playwright.sync_api import sync_playwright
LF = "/tmp/lf/package/dist/"
def status(): return {r[0]: r[1] for r in sqlite3.connect(db).execute("SELECT name,status FROM pois")}
try:
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, device_scale_factor=2)
        pg = ctx.new_page(); errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.route("**/unpkg.com/leaflet@1.9.4/dist/leaflet.js", lambda r: r.fulfill(path=LF + "leaflet.js", content_type="application/javascript"))
        pg.route("**/unpkg.com/leaflet@1.9.4/dist/leaflet.css", lambda r: r.fulfill(path=LF + "leaflet.css", content_type="text/css"))
        pg.route("**/tile.openstreetmap.org/**", lambda r: r.abort())
        pg.route("**/instagram.com/**", lambda r: r.fulfill(body="<html>embed</html>", content_type="text/html"))
        pg.goto("http://127.0.0.1:8152/"); pg.wait_for_selector("#sheetTitle")
        pg.wait_for_function("document.querySelector('#sheetTitle').textContent.includes('plekken')")
        pg.screenshot(path="/tmp/m1.png")
        assert pg.locator("#fab").is_visible() and pg.locator("#addBox").is_hidden() and pg.locator(".filters").is_hidden()
        assert pg.evaluate("document.body.dataset.sheet") == "peek"
        # FAB -> modal -> toevoegen
        posted = []
        pg.route("**/api/posts", lambda r: (posted.append(r.request.post_data), r.fulfill(json={"new": 1, "duplicates": 0}, content_type="application/json")) if r.request.method == "POST" else r.continue_())
        pg.tap("#fab"); assert pg.locator("#addModal").is_visible()
        pg.screenshot(path="/tmp/m2.png")
        pg.fill("#mUrls", "https://www.instagram.com/p/ZZZ/"); pg.tap("#mAdd"); pg.wait_for_timeout(300)
        assert posted and "ZZZ" in posted[0] and "1 nieuw" in pg.locator("#mMsg").inner_text()
        pg.wait_for_timeout(1700); assert not pg.locator("#addModal").is_visible()
        # sheet openen, filters tonen
        pg.tap("#grab"); assert pg.evaluate("document.body.dataset.sheet") == "half" and pg.locator(".poi").count() == 3
        pg.tap("#filtersBtn"); assert pg.locator(".filters").is_visible()
        pg.screenshot(path="/tmp/m3.png")
        pg.tap("#filtersBtn")
        # POI kiezen -> detail, lijst verborgen
        pg.locator(".poi", has_text="Joe's").tap(); pg.wait_for_timeout(300)
        assert pg.locator("#detail").is_visible() and pg.locator("#list").is_hidden() and "plekken" in pg.locator("#sheetTitle").inner_text()
        pg.screenshot(path="/tmp/m4.png")
        pg.locator("#detail button.keep").tap(); pg.wait_for_timeout(300); assert status()["Joe's Stone Crab"] == "keep"
        pg.locator("#closeDetail").tap(); assert pg.locator("#list").is_visible()
        # kaart tikken -> sheet naar peek
        pg.tap("#map", position={"x": 200, "y": 120}); pg.wait_for_timeout(200)
        assert pg.evaluate("document.body.dataset.sheet") == "peek", pg.evaluate("document.body.dataset.sheet")
        assert not errs, errs
        print("MOBILE-TEST OK"); b.close()
finally:
    srv.terminate()

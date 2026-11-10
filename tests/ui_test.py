# Headless UI-test (Playwright + lokale Leaflet). Run: python3 tests/ui_test.py  (leaflet 1.9.4 uitgepakt in /tmp/lf/package)
import os, sys, subprocess, time, tempfile, sqlite3, json
db = tempfile.mkdtemp() + "/poi.db"
env = {**os.environ, "DB_PATH": db}
srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "main:app", "--port", "8150"], cwd="/home/claude/florida/app", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(3)
c = sqlite3.connect(db)
rows = [("p1","Venetian Pool","Coral Gables",25.7623,-80.2725,"Dingen om te doen"),("p2","Joe's Stone Crab","Miami Beach",25.7694,-80.1347,"Restaurants"),
        ("p3","Vizcaya","Miami",25.7444,-80.2106,"Dingen om te doen"),("p4","Ver weg","Tampa",27.95,-82.46,"Overig")]
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
        b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1500, "height": 900})
        errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.route("**/unpkg.com/leaflet@1.9.4/dist/leaflet.js", lambda r: r.fulfill(path=LF + "leaflet.js", content_type="application/javascript"))
        pg.route("**/unpkg.com/leaflet@1.9.4/dist/leaflet.css", lambda r: r.fulfill(path=LF + "leaflet.css", content_type="text/css"))
        pg.route("**/tile.openstreetmap.org/**", lambda r: r.abort())
        pg.route("**/instagram.com/**", lambda r: r.fulfill(body="<html>embed</html>", content_type="text/html"))
        def mclick():  # klik op de eerste marker die echt in beeld staat (kaart pant/zoomt bij selectie)
            loc = pg.locator("path.leaflet-interactive")
            for i in range(loc.count()):
                bb = loc.nth(i).bounding_box()
                if bb and 430 < bb["x"] < 1450 and 10 < bb["y"] < 880:
                    loc.nth(i).click(force=True); return
            raise AssertionError("geen zichtbare marker")
        pg.goto("http://127.0.0.1:8150/"); pg.wait_for_selector(".poi")
        assert pg.locator("#fBase").input_value() == "__any__"  # standaard: alle locaties
        pg.fill("#fKm", "9999")  # Tampa ook binnen filter
        n = lambda: pg.locator(".poi").count()
        assert n() == 4, n()
        assert pg.locator("#detail").is_hidden()
        # klik op lijst -> selectie + detail + embed
        pg.locator(".poi", has_text="Joe's").click()
        assert pg.locator("#detail").is_visible() and "Joe's Stone Crab" in pg.locator("#detail").inner_text()
        assert pg.locator(".poi.sel").count() == 1 and pg.locator("#detail iframe").count() == 1
        assert "bewijs voor poi 2" in pg.locator("#detail").inner_text()
        # klik op marker -> selectie van die POI
        pg.wait_for_timeout(600)  # pan-animatie afwachten
        mclick()
        sel = pg.locator(".poi.sel").inner_text(); print("marker-klik selecteert:", sel.split("\n")[0])
        assert pg.locator(".poi.sel").count() == 1
        # afwijzen via paneel: selectie blijft op dezelfde POI (blijft zichtbaar), pas bij doorlopen verdwijnt hij
        pg.locator(".poi", has_text="Venetian").click()
        before = n(); pg.locator("#detail button.rej").click(); pg.wait_for_timeout(400)
        assert status()["Venetian Pool"] == "reject", status()
        assert n() == before and "Venetian" in pg.locator(".poi.sel").inner_text() and "Herstel" in pg.locator("#detail button.rej").inner_text()
        pg.keyboard.press("ArrowDown"); pg.wait_for_timeout(300)
        nxt = pg.locator(".poi.sel").inner_text().split("\n")[0]; print("na doorlopen geselecteerd:", nxt)
        assert n() == before - 1 and "Venetian" not in nxt and pg.locator(".poi.sel").count() == 1
        # sneltoetsen op de nu geselecteerde POI
        pg.keyboard.press("k"); pg.wait_for_timeout(400)
        assert list(status().values()).count("keep") == 1 and pg.locator(".poi.sel").count() == 1
        pg.keyboard.press("k"); pg.wait_for_timeout(300)  # terugdraaien
        pg.keyboard.press("x"); pg.wait_for_timeout(400)
        assert list(status().values()).count("reject") == 2, status()
        # afgewezen bekijken + herstellen
        pg.select_option("#fStatus", "reject"); assert n() == 2
        pg.locator(".poi", has_text="Venetian").click(); pg.locator("#detail button.rej").click(); pg.wait_for_timeout(300)
        assert status()["Venetian Pool"] == "new"
        # posts-overzicht: uitklappen toont dezelfde embed, POI-link selecteert de POI
        pg.locator("#posts summary").first.click(); pg.locator("#posts .postsBody > details").first.locator("summary").click(); pg.wait_for_timeout(200)
        assert pg.locator("#posts .pEmbed iframe").count() == 1 and "/embed" in pg.locator("#posts .pEmbed iframe").get_attribute("src")
        pg.locator("#posts summary").first.click()  # paneel weer dicht
        # embed uit
        pg.select_option("#fStatus", "active")
        pg.locator(".poi").first.click(); pg.locator("#detail label input").uncheck(); assert pg.locator("#detail iframe").count() == 0
        # basisfilter: standaard 'Alle locaties'; Tampa (ver van alle basispunten) valt af, '(geen filter)' toont alles
        pg.select_option("#fStatus", ""); pg.select_option("#fBase", "__any__"); pg.fill("#fKm", "80")
        assert pg.locator("#fBase").input_value() == "__any__" and n() == 3, n()
        assert "km van" in pg.locator(".poi").first.inner_text()
        pg.select_option("#fBase", ""); assert n() == 4
        pg.select_option("#fBase", "__any__"); pg.fill("#fKm", "9999"); pg.select_option("#fStatus", "active")
        # deselecteren: Esc, ×-knop, klik op lege kaart; markerklik selecteert (en deselecteert niet)
        pg.locator(".poi").first.click(); assert pg.locator("#detail").is_visible()
        pg.keyboard.press("Escape"); assert pg.locator("#detail").is_hidden() and pg.locator(".poi.sel").count() == 0
        pg.locator(".poi").first.click(); pg.locator("#closeDetail").click(); assert pg.locator("#detail").is_hidden()
        mclick(); assert pg.locator("#detail").is_visible(), "markerklik deselecteert ten onrechte"
        pg.mouse.click(1400, 150); pg.wait_for_timeout(200); assert pg.locator("#detail").is_hidden() and pg.locator(".poi.sel").count() == 0
        assert not errs, errs
        pg.screenshot(path="/tmp/ui.png"); print("UI-TEST OK", status()); b.close()
finally:
    srv.terminate()

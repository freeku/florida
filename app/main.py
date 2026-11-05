from typing import Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db
import ingest

app = FastAPI(title="florida-poi")
db.init()


class UrlsIn(BaseModel):
    text: str  # één of meer URL's, vrij plakbaar (ook hele notitie)


class TextIn(BaseModel):
    text: str


class PoiPatch(BaseModel):
    status: Optional[str] = None
    category: Optional[str] = None
    note: Optional[str] = None
    synced: Optional[bool] = None


class IdsIn(BaseModel):
    ids: list[int]


@app.get("/api/config")
def config():
    return {"categories": ingest.CATEGORIES,
            "bases": {"Miami": ingest.MIAMI, "Key Largo": ingest.KEY_LARGO}}


@app.get("/api/pois")
def pois():
    items = db.rows("SELECT * FROM pois ORDER BY category, name")
    src = db.rows("SELECT poi_id, shortcode, confidence, evidence FROM poi_sources")
    by = {}
    for s in src:
        by.setdefault(s["poi_id"], []).append(
            {**s, "url": f"https://www.instagram.com/p/{s['shortcode']}/"})
    for p in items:
        p["sources"] = by.get(p["id"], [])
    return items


@app.patch("/api/pois/{poi_id}")
def patch_poi(poi_id: int, body: PoiPatch):
    if body.status and body.status not in ("new", "keep", "reject"):
        raise HTTPException(400, "ongeldige status")
    if body.category and body.category not in ingest.CATEGORIES:
        raise HTTPException(400, "onbekende categorie")
    fields, args = [], []
    for k in ("status", "category", "note"):
        v = getattr(body, k)
        if v is not None:
            fields.append(f"{k}=?")
            args.append(v)
    if body.synced is not None:
        fields.append("synced=?")
        args.append(int(body.synced))
    if not fields:
        return {"ok": True}
    db.run(f"UPDATE pois SET {', '.join(fields)} WHERE id=?", (*args, poi_id))
    return {"ok": True}


@app.post("/api/posts")
def add_posts(body: UrlsIn, bg: BackgroundTasks):
    codes = ingest.shortcodes_from_text(body.text)
    new = []
    for sc in codes:
        if not db.one("SELECT 1 AS x FROM posts WHERE shortcode=?", (sc,)):
            db.run("INSERT INTO posts(shortcode,url) VALUES(?,?)", (sc, f"https://www.instagram.com/p/{sc}/"))
            new.append(sc)
            bg.add_task(ingest.process, sc)
    return {"found": len(codes), "new": len(new), "duplicates": len(codes) - len(new)}


@app.get("/api/posts")
def list_posts():
    return db.rows("SELECT shortcode,url,status,error,parsed,substr(caption,1,300) AS caption FROM posts ORDER BY created DESC")


@app.post("/api/posts/{shortcode}/text")
def set_text(shortcode: str, body: TextIn, bg: BackgroundTasks):
    """Handmatige tekst (caption/ondertitels/wat je in de video ziet) voor posts die niet automatisch ophaalbaar zijn."""
    if not db.one("SELECT 1 AS x FROM posts WHERE shortcode=?", (shortcode,)):
        raise HTTPException(404)
    db.run("UPDATE posts SET caption=?, status='queued' WHERE shortcode=?", (body.text, shortcode))
    bg.add_task(ingest.process, shortcode)
    return {"ok": True}


@app.post("/api/posts/retry-failed")
def retry_failed(bg: BackgroundTasks):
    """Zet alle posts met status error opnieuw in de wachtrij (bijv. na een key-fix)."""
    codes = [r["shortcode"] for r in db.rows("SELECT shortcode FROM posts WHERE status='error'")]
    for sc in codes:
        db.run("UPDATE posts SET status='queued', error=NULL WHERE shortcode=?", (sc,))
        bg.add_task(ingest.process, sc)
    return {"retrying": len(codes)}


@app.post("/api/posts/{shortcode}/retry")
def retry(shortcode: str, bg: BackgroundTasks):
    db.run("UPDATE posts SET status='queued', error=NULL WHERE shortcode=?", (shortcode,))
    bg.add_task(ingest.process, shortcode)
    return {"ok": True}


# --- voor de Wanderlog-sync (via Claude + wanderlog-mcp, of n8n) ---
@app.get("/api/export/approved")
def export_approved():
    return db.rows(
        "SELECT id,name,address,lat,lng,category,gmaps_url,note FROM pois WHERE status='keep' AND synced=0 ORDER BY category,name")


@app.post("/api/export/mark-synced")
def mark_synced(body: IdsIn):
    for i in body.ids:
        db.run("UPDATE pois SET synced=1 WHERE id=?", (i,))
    return {"ok": True, "count": len(body.ids)}


@app.get("/")
def index():
    return FileResponse("static/index.html")


app.mount("/static", StaticFiles(directory="static"), name="static")

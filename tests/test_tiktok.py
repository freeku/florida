# Unit-test TikTok-ondersteuning (netwerk gemockt). Run: python3 tests/test_tiktok.py
import sys, types
sys.path.insert(0, "/home/claude/florida/app")
import httpx, ingest

class R:  # minimale httpx-respons
    def __init__(s, url="", js=None): s.url, s._js = url, js
    def raise_for_status(s): pass
    def json(s): return s._js
def fake_get(url, **kw):
    if "oembed" in url: return R(js={"title": "Beste taco's in Miami #florida", "author_unique_id": "foodie", "author_name": "Foodie"})
    return R(url="https://www.tiktok.com/@foodie/video/7300000000000000099?_r=1")  # redirect van korte link
httpx.get = fake_get

t = """https://www.instagram.com/reel/ABC123/?igsh=x
https://www.tiktok.com/@miami.eats/video/7111111111111111111?is_from_webapp=1
https://vm.tiktok.com/ZMabc123/ en https://www.tiktok.com/t/ZTxyz9/
https://www.tiktok.com/@miami.eats/video/7111111111111111111"""
refs = ingest.refs_from_text(t)
print(refs)
keys = [r["key"] for r in refs]
assert keys == ["ABC123", "tt_7111111111111111111", "tt_7300000000000000099"], keys  # dubbele video + twee korte links naar dezelfde video ontdubbeld
assert refs[2]["url"] == "https://www.tiktok.com/@foodie/video/7300000000000000099"
assert refs[1]["url"] == "https://www.tiktok.com/@miami.eats/video/7111111111111111111"
txt = ingest.fetch_tiktok_text(refs[1]["url"]); assert "@foodie" in txt and "taco" in txt, txt
httpx.get = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("geen netwerk"))
assert ingest.refs_from_text("https://vm.tiktok.com/ZMkapot/")[0]["key"] == "tts_ZMkapot"
assert ingest.fetch_tiktok_text("x") is None
print("TIKTOK-TEST OK")

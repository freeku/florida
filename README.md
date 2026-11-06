# florida

Florida POI-kaart: Instagram-URL's → Claude-parse → Google Places → reviewkaart → export naar Wanderlog.

## Deploy
Push naar `main` → GitHub Action bouwt `ghcr.io/freeku/florida:latest` (linux/amd64) → Watchtower op de NAS pullt en herstart de container. Geen inbound toegang tot de NAS nodig.

Eenmalig op de NAS:
1. GHCR-pull: `docker login ghcr.io` met een PAT (alleen `read:packages`), of maak het package public.
2. Stack uit `docker-compose.yml` (Container Manager/Portainer of `docker compose up -d`), env uit `.env.example`.
3. Watchtower (alleen containers met het label):
   `docker run -d --name watchtower --restart unless-stopped -v /var/run/docker.sock:/var/run/docker.sock -v ~/.docker/config.json:/config.json containrrr/watchtower --label-enable --interval 300`

UI: `http://<nas>:8140` (geen eigen login: alleen LAN of achter reverse proxy met auth). Data (SQLite) staat in `/volume1/docker/florida-poi`.

## Gebruik
- Plak URL's (of de hele Joplin-notitie, zie `seed-urls.txt`) in het veld bovenaan → dedup op shortcode → parse op de achtergrond.
- Posts met status `needs_text`/`error` onderaan: plak caption of noem de plek uit de video → "Parse".
- Review: klik op een marker of een naam in de lijst → het detailpaneel toont de POI, de bewijsregel uit de post en de Instagram-post zelf (embed, uit te zetten). **Houden** en **Afwijzen** springen door naar de volgende POI; sneltoetsen: ↑/↓ navigeren, `k` houden, `x` afwijzen.
- Filter op status (standaard: alles behalve afgewezen), categorie en afstand tot een basispunt; een afgewezen POI blijft terug te vinden via het statusfilter en met **Herstel**.
- Dubbele plekken (zelfde `place_id`, of <250 m + vergelijkbare naam) worden één POI met meerdere bronposts.

## Sync naar Wanderlog (rechtstreeks, zonder MCP)
Onofficieel: gebruikt Wanderlogs interne web-API met je sessiecookie. Instellen in de stack-variabelen:
- `WANDERLOG_COOKIE`: waarde van de cookie `connect.sid` (begint met `s%3A`). Chrome: wanderlog.com openen, ingelogd → DevTools → Application → Cookies → `connect.sid` → Value kopiëren. Verloopt na enkele weken; bij een fout "sessie geweigerd" opnieuw ophalen.
- `WANDERLOG_TRIP_KEY`: de code in de trip-URL (`wanderlog.com/plan/<key>/…`).
- `WANDERLOG_LISTS` (optioneel, JSON): categorie in de app → lijstnaam in Wanderlog. Standaard: `Dingen om te doen` → `Places to visit`, `Winkelen` → `Shoppen`; de overige namen moeten gelijk zijn.

Na het syncen houdt de app per POI het Wanderlog-blok bij. Verander je daarna de categorie, dan **verplaatst** de volgende sync het blok naar de juiste lijst (notities blijven behouden). Vink je een gesyncte POI uit of wijs je hem af, dan blijft hij in Wanderlog staan tot je op **Verwijder uitgevinkte uit Wanderlog** klikt (met proefrun en bevestiging). Staat een plek niet meer in een lijst (bijv. naar een dag gesleept), dan blijft hij onaangeroerd.

Knoppen in de UI: **controleer** (cookie, trip en ontbrekende lijsten; schrijft niets), **proefrun** (laat zien wat er zou gebeuren), **sync** (voegt toe en zet de POI op `gesynced`). Plekken die al in de trip staan worden overgeslagen. Tests: `cd app && python -I ../tests/test_wanderlog_fake.py` (nagebootste Wanderlog; de echte API is niet getest).

## API voor de Wanderlog-sync
- `GET /api/export/approved` → `keep` + nog niet gesynced
- `POST /api/export/mark-synced` `{"ids":[...]}`

Sync loopt via Claude Desktop + wanderlog-mcp (onofficieel, cookie-auth). Zet `CATEGORIES` gelijk aan de lijstnamen in Wanderlog.

## Env
`ANTHROPIC_API_KEY` (verplicht), `GOOGLE_PLACES_KEY` (aanbevolen), `CLAUDE_MODEL`, `IG_USER` + `/data/ig-session` (optioneel, Instaloader).

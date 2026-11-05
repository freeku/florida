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
- Kaart: filter op status, categorie en afstand tot Miami/Key Largo; vink aan = `keep`; "Afwijzen" dempt de POI.
- Dubbele plekken (zelfde `place_id`, of <250 m + vergelijkbare naam) worden één POI met meerdere bronposts.

## API voor de Wanderlog-sync
- `GET /api/export/approved` → `keep` + nog niet gesynced
- `POST /api/export/mark-synced` `{"ids":[...]}`

Sync loopt via Claude Desktop + wanderlog-mcp (onofficieel, cookie-auth). Zet `CATEGORIES` gelijk aan de lijstnamen in Wanderlog.

## Env
`ANTHROPIC_API_KEY` (verplicht), `GOOGLE_PLACES_KEY` (aanbevolen), `CLAUDE_MODEL`, `IG_USER` + `/data/ig-session` (optioneel, Instaloader).

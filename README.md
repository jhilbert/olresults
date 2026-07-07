# olresults — Orienteering Results Database

A database and static website collecting orienteering competition results,
built to render detailed per-runner profiles: for every race the category,
rank, number of starters, classified finishers, time behind the winner and
percentage behind.

## Architecture

Fully static. Data is ingested from source systems into raw JSON snapshots,
normalized, compiled into a SQLite database at build time, and queried
client-side in the browser via sql.js. Hosted on GitHub Pages, refreshed by
a scheduled GitHub Actions workflow.

```
ingest/    source adapters (ANNE API, SportSoftware HTML/PDF parsers)
data/
  raw/     verbatim snapshots from sources (provenance)
  normalized/  parsed legacy results in the common JSON shape
build/     raw + normalized JSON -> site/data/results.db (SQLite)
site/      static frontend (sql.js), deployed to GitHub Pages
```

## Data sources

| Tier | Source | Coverage | Quality |
|---|---|---|---|
| 1 | ANNE API (`anne-api.oefol.at/v1`) structured results | ~322 events, growing | splits, stable person ids |
| 2 | SportSoftware HTML attachments | ~775 files | full result lists |
| 3 | SportSoftware PDF attachments | ~1,065 files | full result lists |
| 4 | External links (SPORTident Center, club sites) | ~504 links | varies |

ANNE (anne.orienteeringaustria.at) is the entry & results system of the
Austrian Orienteering Federation (ÖFOL). Its public API is documented at
<https://anne-api.oefol.at/v1/docs/>. Requests are made politely (low
concurrency, identifying User-Agent) and raw responses are cached in git so
sources are only hit for new or changed events.

## Usage

```
python3 ingest/anne_sync.py            # sync events + structured results
python3 ingest/parse_sportsoftware_html.py  # parse tier-2 HTML attachments
python3 build/build_db.py              # build site/data/results.db
cd site && python3 -m http.server 8643 # local preview
```

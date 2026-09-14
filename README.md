# 🏡 Universal Real Estate Hunter & Intelligence Platform

[![Python Version](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg)](https://www.python.org/)
[![Docker Ready](https://img.shields.io/badge/docker-ready-2496ED.svg?logo=docker&logoColor=white)](https://www.docker.com/)
[![License: PolyForm Noncommercial](https://img.shields.io/badge/License-PolyForm_Noncommercial_1.0.0-blue.svg)](https://polyformproject.org/licenses/noncommercial/1.0.0)
[![Code Style: Clean](https://img.shields.io/badge/code%20style-pydantic%20%7C%20sqlalchemy%202.0-emerald.svg)](https://docs.pydantic.dev/)

An advanced, asynchronous real estate monitoring, analytical pipeline, and market intelligence platform. Designed to continuously track, qualify, deduplicate, and analyze property listings across major portals (**Otodom**, **OLX**, **Nieruchomości-online**, **Morizon**) with official cadastral data integration (**Polish National Geoportal / GUGiK**), Air Quality intelligence (CAMS Copernicus + GIOŚ), AI due-diligence analysis (LLM), interactive map CRM, and real-time Discord/Telegram notifications.

**100% City-Agnostic & Universal Across Poland** — seamlessly monitor houses, apartments, and land plots in **any Polish city, metropolitan area, or commune** (Warsaw, Kraków, Wrocław, Poznań, Trójmiasto, Katowice, Łódź, Szczecin, Lublin, Rzeszów, and beyond). Dynamic URL generators, nationwide GUGiK spatial auditing (all 380+ powiats), and multi-profile management make the platform completely universal.

> 🇵🇱 *Polska wersja dokumentacji jest dostępna w pliku [README.pl.md](README.pl.md).*

---

## ✨ Key Features

### 🌍 1. Universal Multi-City & Multi-Profile Architecture
- **City-Agnostic Engine:** Operates in any Polish city or municipality without hardcoded locations or regional locks.
- **Dynamic Portal URL Generation:** Search queries for Otodom, OLX, Nieruchomości-online, and Morizon are synthesized on-the-fly from city names, distance radius, categories (houses, flats, plots), price limits, and room counts.
- **Independent Search Profiles:** Run multiple concurrent search criteria (e.g. *Flats in Warsaw*, *Houses around Kraków*, *Plots in Wrocław*), each with dedicated filters, notifications, and portal toggles.
- **Nationwide Spatial Coverage:** Cadastral parcels (ULDK), MPZP zoning, ISOK flood risks, SOPO landslides, and GESUT/KIUT utilities work across all 380+ counties in Poland.
- **Dynamic Agglomeration Matrix:** Commute distance calculations automatically resolve to the target metropolitan center using nationwide centroids.

### 🌐 2. High-Performance Multi-Portal Scraping
- **Anti-Bot Bypass:** Uses `curl_cffi` with Chrome TLS fingerprint impersonation (`impersonate="chrome120"`) to seamlessly pass Cloudflare and DataDome protections without paid proxies.
- **Direct Next.js Hydration Extraction:** Extracts pristine structured JSON from `__NEXT_DATA__` and `__PRERENDERED_STATE__` script tags, minimizing brittle HTML DOM parsing.
- **Detail Page Refresh Control:** Intelligent cache layer skipping recently scraped listings while tracking price drops and listing updates.
- **Per-portal limits:** Independent page limits and delays for Otodom, OLX, Nieruchomości-online, and Morizon.

### 🗺️ 3. Official Cadastral & Geospatial Intelligence (GUGiK Geoportal)
- **Automatic Parcel Identification:** Queries the Polish National ULDK API (`GetParcelByXY`) to resolve cadastral parcel ID (`TERYT`), commune, precinct, and parcel number from GPS coordinates.
- **Accurate Surface Area Calculation:** Downloads exact boundary polygons in `EPSG:2180` and computes true legal land area in m² (handling enclaves and multi-polygons).
- **Industrial & Commercial Risk Audit:** Scans surrounding parcels within 120 meters in 8 cardinal directions via KIEG WMS (`GetFeatureInfo`) for zoning risks:
  - `Ba` – Industrial / production facilities
  - `Bi` – Commercial / warehousing complexes
  - `Tk` – Railway grounds
- **Automated Score Penalty & Alerts:** Automatically subtracts 25 points from listings near industrial risks, alerts in cons, and provides direct 1-click deep links to the National Geoportal.

### 🧠 4. Two-Stage Qualification & Semantic Engine
- **Stage 1 (Hard Rules):** Enforces strict numerical thresholds (price, price/m², living area, plot area, rooms, floor, construction year, owner/market type) and whitelist/blacklist locations.
- **Stage 2 (Semantic NLP & Heuristics):** Analyzes listing titles and full descriptions:
  - Detects **Finish Condition**: *do zamieszkania / pod klucz*, *do wykończenia*, *deweloperski*, *surowy zamknięty*, *surowy otwarty*, *do remontu*.
  - Flags **3D Visualizations & Renderings** (alerting on listings lacking real photos).
  - Detects **Utilities**: municipal sewerage vs. septic tank (`szambo`) vs. biological treatment plant, heating type (heat pump, gas, district, solid fuel, electric), and fiber optic internet.
  - Verifies road access (asphalt vs. dirt), parking, segment type, and terrain hazards.
- **Cross-Agency Deduplication (`property_fingerprint`):** Matches properties listed simultaneously by multiple real estate agencies using fuzzy spatial, pricing, and dimensional signatures.
- **Price History Tracking:** Tracks historical price drops with percentage changes and timestamps.

### 🤖 5. AI Due Diligence (Optional LLM Analysis)
Powered by **OpenRouter**, **OpenAI**, or a local **Ollama** instance. For every listing the LLM returns structured JSON containing:
- **TL;DR Summary** — max 2 concrete sentences: location, area, price (and zł/m²), actual finish state, and the single main risk/advantage. No marketing fluff.
- **Interest Verdict** (`worth_interest` + `verdict`) — ✅ worth contacting / ❌ skip, justified with concrete numbers from the listing.
- **Finish Condition & State Note** — precise classification plus a one-sentence note of what is done and what is missing (e.g. *"Instalacje i okna wykonane; do zrobienia: wylewki, tynki, wykończenie."*).
- **Visualisation Detection** — flags listings based on renders / exemplary photos instead of real photos.
- **Questions for the Agent** — 3–5 sharp questions targeting information gaps in this specific listing.
- **Contact Extraction** — phone number and contact person.
- **Hidden Costs, Legal Risks, Portal-vs-Description Discrepancies, Pros & Cons.**

### 📊 6. Interactive Live Web Dashboard & CRM
- **Modern Dark UI:** High-density, professional design built on a strict neutral palette and tabular figures.
- **Interactive Leaflet Map:** Custom color-coded pins, clustering, and viewport synchronization.
- **Personal CRM:** Mark listings as ★ Favorites, 📅 Scheduled Visits, or ✕ Rejected. Add private notes from property tours.
- **Multi-Profile Search Manager:** Switch between multiple profiles (e.g. *Flats in Warsaw*, *Houses in Kraków*, *Plots in Wrocław*) directly from the browser.
- **AI Due Diligence Modal:** TL;DR, interest verdict badge, questions for the agent, contact card with copy-ready SMS, all-in budget calculator, and price-drop history.
- **Live Configuration:** Edit search profiles, thresholds, whitelist/blacklist, and scheduler settings without restarting — with one-click scraping start.
- **Database Reset:** Safe reset of listings (per profile or entire database) with confirmation.
- **Photo Galleries & Lightbox:** Inline thumbnail carousels and full-screen image viewer.

### ⏱️ 7. Smart Scheduler & Quiet Hours
- Configurable continuous background monitoring (e.g. every 15–20 minutes during daytime).
- Automatic **Night Mode / Quiet Hours** (e.g. reducing checks to every 60 minutes between 22:00 and 07:00).
- Dynamic interval adjustments from web UI without restarting the container.

---

## 🏗️ Architecture

```text
Universal-Real-Estate-Hunter/
├── config/                    # Pydantic BaseSettings & environment loader
│   └── settings.py            # Thresholds, default whitelist, database URLs, LLM settings
├── src/
│   ├── models/                # Pydantic v2 schemas and enums
│   ├── scrapers/              # Multi-portal scrapers (Otodom, OLX, Nieruchomości-online, Morizon)
│   ├── filters/               # Qualification engine (Stage 1 + Stage 2 + LLM + fingerprint)
│   ├── storage/               # Async SQLAlchemy 2.0 models, repository & automatic SQLite migrations
│   ├── services/
│   │   ├── geoportal.py       # GUGiK ULDK & KIEG WMS spatial audit service
│   │   ├── geocoder.py        # Nominatim caching geocoder
│   │   ├── pipeline.py        # Orchestrator running scraping, audit, storage, and alerts
│   │   ├── config_manager.py  # Multi-profile & scheduler JSON configuration manager
│   │   ├── live_dashboard.py  # aiohttp async web server & REST API
│   │   ├── discord_notifier.py# Rich Discord embed notifier
│   │   ├── telegram_notifier.py # HTML Telegram bot notifier
│   │   ├── progress.py        # Live scraping progress tracker
│   │   └── terminal_view.py   # Rich terminal listing table (CLI `view` command)
│   ├── scheduler/             # Periodic runner with day/night adaptive loop
│   └── version.py             # Version (managed by semantic-release)
├── tests/                     # Automated pytest suite
├── Dockerfile                 # Production container image
├── docker-compose.yml         # Multi-service setup (Dashboard + Scraper Daemon)
├── search_config.json         # Live profiles, portal limits, and scheduler configuration
├── main.py                    # Unified CLI entrypoint
├── pyproject.toml             # Project metadata, ruff, pytest, bandit, semantic-release
├── .pre-commit-config.yaml    # Pre-commit hooks (ruff, security, hygiene)
└── listings.db                # SQLite database (persisted locally or in Docker volume)
```

---

## 🚀 Quickstart with Docker (Recommended)

### 1. Clone the repository
```bash
git clone https://github.com/p-sternik/Universal-Real-Estate-Hunter.git
cd Universal-Real-Estate-Hunter
```

### 2. Configure environment variables
Copy the example environment file and add your Discord/Telegram credentials and (optionally) an LLM API key:
```bash
cp .env.example .env
```

### 3. Launch with Docker Compose
```bash
docker compose up -d
```

The system will start three containers out-of-the-box (zero configuration needed):
1. **`estate_hunter_db`**: High-performance PostgreSQL 16 database with health check and persistent storage volume (eliminates SQLite write contention / file lock issues). Existing SQLite data (`listings.db`) is automatically migrated on first run.
2. **`estate_hunter_dashboard`**: Live Web Dashboard accessible at **`http://localhost:8080`**.
3. **`estate_hunter_scraper`**: Continuous background scraper monitoring active search profiles.

### 4. Check logs
```bash
docker compose logs -f scraper
```

---

## 💻 Local Installation (Without Docker)

### Prerequisites
- Python 3.11, 3.12, or 3.13

### Option A: pip
```bash
git clone https://github.com/p-sternik/Universal-Real-Estate-Hunter.git
cd Universal-Real-Estate-Hunter

# Create and activate virtual environment
python -m venv venv
# Linux / macOS:
source venv/bin/activate
# Windows PowerShell:
.\venv\Scripts\Activate.ps1

pip install --upgrade pip
pip install .
```

### Option B: uv (recommended for development)
```bash
uv sync
uv run pre-commit install
```

### Initialize Database
```bash
python main.py init-db
```

---

## 🛠️ CLI Usage & Commands

The application provides a unified CLI via [`main.py`](main.py):

| Command | Description | Example |
| :--- | :--- | :--- |
| `run` | Start continuous monitoring daemon | `python main.py run` or `python main.py run --profile "Mieszkania Warszawa" --interval 15` |
| `once` | Run a single scraping and qualification pass | `python main.py once` or `python main.py once --profile "Domy Kraków"` |
| `dashboard` | Launch real-time web UI CRM & Leaflet map | `python main.py dashboard --port 8080` (`server` is an alias) |
| `geoportal` | Audit saved listings with GUGiK Geoportal | `python main.py geoportal --limit 50` (`--all` to include non-qualified) |
| `view` | View qualified listings in formatted terminal table | `python main.py view --status QUALIFIED --limit 15` |
| `reindex` | Re-evaluate all database listings with latest filters | `python main.py reindex` |
| `geocode` | Backfill missing GPS coordinates via Nominatim | `python main.py geocode` |
| `test-webhook` | Send a test notification to Discord | `python main.py test-webhook` |
| `test-filter` | Run a synthetic demonstration of the filtering engine | `python main.py test-filter` |
| `init-db` | Initialize database tables | `python main.py init-db` |

City and radius overrides work for `run` and `once`:
```bash
python main.py once --city Kraków --radius 20
```

---

## ⚙️ Configuration & Customization

### 1. Environment Variables (`.env`)
```ini
# Database (SQLite by default, PostgreSQL supported)
DATABASE_URL=sqlite+aiosqlite:///listings.db

# Notifications
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
TELEGRAM_CHAT_ID=-100123456789

# Scheduler is owned by search_config.json (editable in the dashboard)

# Stage 1 thresholds fallback
MAX_PRICE=1300000.0
MIN_AREA_HOME=90.0
MAX_AREA_HOME=145.0
MIN_AREA_PLOT=250.0

# LLM Analysis (optional — see "AI Due Diligence")
USE_LLM_ANALYSIS=false
OPENROUTER_API_KEY=
OPENROUTER_MODEL=nex-agi/nex-n2.5-mini:free
LLM_MAX_CALLS_PER_MINUTE=15
# Alternatives:
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini
OPENAI_BASE_URL=
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1:8b

# Network & Scraping
PROXY_URL=
REQUEST_TIMEOUT_SECONDS=25
MAX_RETRIES=3
FETCH_DETAILS=true
```

### 2. Search Profiles & Scheduler (`search_config.json`)
You can configure multiple independent search profiles (Houses, Apartments, Plots) across any Polish city via the Web Dashboard or by editing `search_config.json`.
This file is personal and ignored by git — on first run it is created automatically from `search_config.example.json`:

```json
{
  "profiles": [
    {
      "id": "warszawa_mieszkania",
      "name": "Mieszkania Warszawa",
      "enabled": true,
      "category": "mieszkanie",
      "city": "Warszawa",
      "distance_radius": 5,
      "min_price": 500000,
      "max_price": 1200000,
      "min_area_home": 45,
      "max_area_home": 85,
      "min_rooms": 2,
      "max_rooms": 4,
      "allowed_finish_conditions": ["do zamieszkania"],
      "allow_visualisations": false,
      "whitelist_areas": ["Mokotów", "Ursynów", "Wola"],
      "blacklist_keywords": [],
      "enabled_portals": null
    },
    {
      "id": "krakow_domy",
      "name": "Domy Kraków",
      "enabled": true,
      "category": "dom",
      "city": "Kraków",
      "distance_radius": 15,
      "min_price": 700000,
      "max_price": 1600000,
      "min_area_home": 100,
      "max_area_home": 180,
      "min_area_plot": 400,
      "allowed_finish_conditions": ["all"],
      "allow_visualisations": true,
      "building_types": ["wolnostojący", "bliźniak", "szeregowiec"],
      "whitelist_areas": [],
      "blacklist_keywords": [],
      "enabled_portals": null
    }
  ],
  "scrapers": {
    "otodom": {"enabled": true, "max_pages": 5, "delay_seconds": 1.0},
    "olx": {"enabled": true, "max_pages": 2, "delay_seconds": 1.0},
    "nieruchomosci_online": {"enabled": true, "max_pages": 3, "delay_seconds": 1.0},
    "morizon": {"enabled": true, "max_pages": 2, "delay_seconds": 1.0}
  },
  "scheduler": {
    "interval_minutes": 20,
    "night_mode": true,
    "night_interval_minutes": 60,
    "quiet_hours_start": "22:00",
    "quiet_hours_end": "07:00"
  },
  "llm_analysis_enabled": true
}
```

---

## 🤖 AI Due Diligence

Enable LLM analysis by setting `USE_LLM_ANALYSIS=true` (or `llm_analysis_enabled` in `search_config.json`) and providing one of the supported backends:

| Backend | Configuration | Notes |
| :--- | :--- | :--- |
| **OpenRouter** (default) | `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` | Free models supported (e.g. `nex-agi/nex-n2.5-mini:free`); paid models allow higher throughput |
| **OpenAI-compatible API** | `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_BASE_URL` | Any OpenAI-compatible endpoint |
| **Ollama** (local) | `OLLAMA_BASE_URL`, `OLLAMA_MODEL` | Fully local, no API key required |

**Cost optimization:**
- Listings whose description has not changed are not re-sent to the LLM (results are preserved in the database).
- `LLM_MAX_CALLS_PER_MINUTE` throttles requests to stay within provider limits (free OpenRouter tier = 20 req/min, set 15).
- Automatic retry with exponential backoff on rate limits (429), and fallback chain OpenRouter → OpenAI → Ollama.

**What you get per listing:** concrete TL;DR, ✅/❌ interest verdict with numeric justification, precise finish state + note, visualisation detection, 3–5 sharp questions for the agent, contact extraction, hidden costs, legal risks, and portal-vs-description discrepancies.

---

## 🗺️ How Geoportal Spatial Audit Works

When an offer qualifies, the system performs a zero-cost spatial audit against the official Polish National Spatial Infrastructure (GUGiK):

1. **Parcel Resolution:** ULDK API identifies the cadastral plot ID (e.g. `181609_2.0001.2643/7`).
2. **True Legal Area:** Geometrical calculation computes the polygon surface in square meters directly from official land borders.
3. **Zoning Hazard Scan:** KIEG WMS evaluates land-use contours in a 120m radius. If an industrial (`Ba`), commercial warehouse (`Bi`), or railway (`Tk`) contour is detected, the listing is flagged with a warning and penalized by 25 points in the qualification score.

---

## 🧪 Development & Automated Tests

Run the test suite with `pytest`:
```bash
uv run pytest          # or: pytest
uv run pytest -m "not integration"   # unit tests only (also run pre-push)
```

**Quality tooling** (enforced by pre-commit hooks):
- `ruff` — linting and formatting
- `bandit` — security linting
- `gitleaks` — secret scanning
- file hygiene: trailing whitespace, EOF newline, LF line endings, JSON/YAML/TOML validation

```bash
uv run pre-commit install
uv run pre-commit run --all-files
```

Releases are versioned automatically with `python-semantic-release` (conventional commits); see [`CHANGELOG.md`](CHANGELOG.md).

---

## 🔒 Security & Open Source Practices

- **Never commit `.env` or `listings.db`**: Both are explicitly excluded in `.gitignore` and `.dockerignore`.
- **Stateless Containers**: In Docker, data is mounted to `/app/data/listings.db` to ensure persistent storage across restarts and upgrades.
- **Secret scanning**: `gitleaks` runs on every commit to prevent accidental credential leaks.

---

## 📄 License

This project is licensed under the [PolyForm Noncommercial License 1.0.0](LICENSE) — free for personal, educational, and noncommercial use. Commercial use is prohibited without prior written permission from the author.

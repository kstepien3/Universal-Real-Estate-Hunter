# 🏡 Universal Real Estate Hunter & Intelligence Platform

[![Python Version](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue.svg)](https://www.python.org/)
[![Docker Ready](https://img.shields.io/badge/docker-ready-2496ED.svg?logo=docker&logoColor=white)](https://www.docker.com/)
[![License: PolyForm Noncommercial](https://img.shields.io/badge/License-PolyForm_Noncommercial_1.0.0-blue.svg)](https://polyformproject.org/licenses/noncommercial/1.0.0)

Zaawansowana, asynchroniczna platforma monitorowania, analityki i wywiadu rynkowego dla nieruchomości. Ciągle śledzi, kwalifikuje, deduplikuje i analizuje oferty z głównych portali (**Otodom**, **OLX**, **Nieruchomości-online**, **Morizon**), integruje oficjalne dane ewidencyjne (**Geoportal Krajowy / GUGiK**), jakość powietrza i smog (CAMS Copernicus + GIOŚ), analizę due diligence opartą na LLM, interaktywny CRM z mapą oraz powiadomienia Discord/Telegram w czasie rzeczywistym.

**100% Uniwersalna i Agnostyczna dla dowolnego miasta w Polsce** — monitoruj domy, mieszkania i działki w **dowolnym polskim mieście, aglomeracji lub gminie** (Warszawa, Kraków, Wrocław, Poznań, Trójmiasto, Katowice/Śląsk, Łódź, Szczecin, Lublin, Rzeszów i każde inne). Dynamiczne generatory zapytań do portali, ogólnokrajowa integracja z bazami GUGiK (wszystkie 380+ powiatów) oraz system niezależnych profili czynią platformę w pełni uniwersalną.

> 🇬🇧 *English documentation is available in [README.md](README.md).*

---

## ✨ Kluczowe funkcje

### 🌍 1. W pełni uniwersalna architektura wieloprofilowa i wielomiejska
- **Agnostyczność lokalizacyjna:** System nie posiada żadnych zahardkodowanych miast ani blokad regionalnych — działa w dowolnej polskiej miejscowości.
- **Dynamiczne generowanie linków portali:** Adresy zapytań dla Otodom, OLX, Nieruchomości-online i Morizon są syntetyzowane w locie na podstawie nazwy miasta, promienia poszukiwań (+km), kategorii (domy, mieszkania, działki), cen i liczby pokoi.
- **Wieloprofilowość wyszukiwania:** Prowadź równoległy monitoring wielu różnych rynków jednocześnie (np. *Mieszkania Warszawa*, *Domy Kraków*, *Działki Wrocław*), każdy z odrębnymi filtrami, webhookami i portalami.
- **Ogólnokrajowe pokrycie przestrzenne:** Integracja z ewidencją gruntów (ULDK TERYT), planami MPZP, strefami zalewowymi ISOK, osuwiskami SOPO oraz sieciami uzbrojenia terenu GESUT/KIUT obejmuje całą Polskę (380+ powiatów).
- **Dynamiczna macierz dojazdów:** Czasy i odległości dojazdu wyliczane są dynamicznie względem centrum wybranej metropolii w oparciu o ogólnopolskie centroidy.

### 🌐 2. Wydajny scraping wielu portali
- **Omijanie anty-botów:** `curl_cffi` z impersonacją odcisku TLS przeglądarki Chrome (`impersonate="chrome120"`) przechodzi przez zabezpieczenia Cloudflare i DataDome bez płatnych proxy.
- **Bezpośrednia ekstrakcja hydracji Next.js:** strukturalny JSON z tagów `__NEXT_DATA__` i `__PRERENDERED_STATE__` zamiast kruchych selektorów DOM.
- **Inteligentny odświeżanie szczegółów:** warstwa cache pomija niedawno pobrane oferty, śledząc jednocześnie spadki cen i zmiany ogłoszeń.
- **Niezależne limity per portal:** osobne limity stron i opóźnienia dla Otodom, OLX, Nieruchomości-online i Morizon.

### 🗺️ 3. Oficjalne dane katastralne i geoprzestrzenne (Geoportal GUGiK)
- **Automatyczna identyfikacja działki:** zapytania do krajowego API ULDK (`GetParcelByXY`) ustalają numer działki ewidencyjnej (`TERYT`), gminę, obręb i numer z współrzędnych GPS.
- **Dokładna powierzchnia:** pobieranie granic działki w `EPSG:2180` i obliczanie rzeczywistej powierzchni prawnej w m² (z obsługą enklaw i multipoligonów).
- **Audyt ryzyka przemysłowego/handlowego:** skan otoczenia w promieniu 120 metrów w 8 kierunkach przez KIEG WMS (`GetFeatureInfo`):
  - `Ba` – tereny produkcyjne / przemysłowe
  - `Bi` – kompleksy handlowe / magazynowe
  - `Tk` – tereny kolejowe
- **Automatyczna kara punktowa i alerty:** −25 punktów za sąsiedztwo ryzyk przemysłowych, wpis w „minusach" i linki 1-klik do Geoportalu Krajowego.

### 🧠 4. Dwuetapowy silnik kwalifikacji i analizy semantycznej
- **Etap I (twarde reguły):** ścisłe progi numeryczne (cena, cena/m², metraż domu, działka, liczba pokoi, piętro, rok budowy, typ właściciela/rynku) oraz whitelist/blacklist lokalizacji.
- **Etap II (NLP semantyczne i heurystyki):** analiza tytułów i pełnych opisów:
  - Wykrywanie **stanu wykończenia**: *do zamieszkania / pod klucz*, *do wykończenia*, *deweloperski*, *surowy zamknięty*, *surowy otwarty*, *do remontu*.
  - Oznaczanie **wizualizacji 3D i zdjęć poglądowych** (ostrzeżenie przy braku realnych zdjęć).
  - Wykrywanie **mediów**: kanalizacja miejska vs. szambo vs. przydomowa oczyszczalnia, typ ogrzewania (pompa ciepła, gaz, miejskie, paliwo stałe, elektryczne) i światłowód.
  - Weryfikacja dojazdu (asfalt vs. polna), parkingu, typu segmentu i ryzyk terenowych.
- **Deduplikacja między agencjami (`property_fingerprint`):** dopasowywanie tej samej nieruchomości wystawionej przez kilka agencji na podstawie rozmytych sygnatur przestrzennych, cenowych i wymiarowych.
- **Historia cen:** śledzenie spadków cen z procentami i znacznikami czasu.

### 🤖 5. AI Due Diligence (opcjonalna analiza LLM)
Wspierane backendy: **OpenRouter**, **OpenAI** lub lokalny **Ollama**. Dla każdej oferty LLM zwraca strukturalny JSON:
- **Podsumowanie TL;DR** — maks. 2 konkretne zdania: lokalizacja, metraż, cena (i zł/m²), faktyczny stan wykończenia oraz główne ryzyko/atut. Bez marketingowej wody.
- **Werdykt** (`worth_interest` + `verdict`) — ✅ warto się zainteresować / ❌ pominąć, uzasadniony konkretnymi liczbami z ogłoszenia.
- **Stan wykończenia + notatka** — precyzyjna klasyfikacja plus jedno zdanie, co jest zrobione, a czego brakuje (np. *„Wykonane instalacje i okna; do zrobienia: wylewki, tynki, wykończenie."*).
- **Wykrywanie wizualizacji** — oznaczanie ofert opartych na renderach / zdjęciach przykładowej aranżacji zamiast realnych zdjęć.
- **Pytania do agenta** — 3–5 konkretnych pytań o luki informacyjne w danym ogłoszeniu.
- **Ekstrakcja kontaktu** — numer telefonu i osoba kontaktowa.
- **Ukryte koszty, ryzyka prawne, rozbieżności portal vs. opis, zalety i wady.**

### 📊 6. Interaktywny Live Dashboard i CRM
- **Nowoczesny ciemny UI:** profesjonalny design na neutralnej palecie kolorów i cyfrach tabelarycznych.
- **Interaktywna mapa Leaflet:** kolorowe pinezki, klasteryzacja i synchronizacja widoku.
- **Osobisty CRM:** oznaczanie ofert jako ⭐ Ulubione, 📅 Do obejrzenia, ✕ Odrzucone oraz prywatne notatki z oględzin.
- **Zarządzanie wieloma profilami:** przełączanie profili (np. *Mieszkania Warszawa*, *Domy Kraków*, *Działki Wrocław*) prosto z przeglądarki.
- **Modal AI Due Diligence:** TL;DR, badge werdyktu, pytania do agenta, karta kontaktu z gotowym SMS-em do skopiowania, kalkulator kosztów zakupu all-in i historia spadków cen.
- **Konfiguracja na żywo:** edycja profili, progów, whitelist/blacklist i harmonogramu bez restartu — z jednoklikowym startem scrapingu.
- **Reset bazy danych:** bezpieczne czyszczenie ofert (per profil lub całej bazy) z potwierdzeniem.
- **Galerie zdjęć i Lightbox:** karuzele miniatur i pełnoekranowy podgląd.

### ⏱️ 7. Inteligentny harmonogram i godziny nocne
- Konfigurowalne ciągłe monitorowanie w tle (np. co 15–20 minut w ciągu dnia).
- Automatyczny **tryb nocny** (np. co 60 minut między 22:00 a 07:00).
- Dynamiczna zmiana interwałów z poziomu panelu web bez restartu kontenera.

---

## 🏗️ Architektura Projektu

```text
Universal-Real-Estate-Hunter/
├── config/                    # Pydantic BaseSettings i ładowanie zmiennych środowiskowych
│   └── settings.py            # Progi, domyślna whitelist, adresy baz danych, ustawienia LLM
├── src/
│   ├── models/                # Schematy Pydantic v2 i enums
│   ├── scrapers/              # Scrapery portali (Otodom, OLX, Nieruchomości-online, Morizon)
│   ├── filters/               # Silnik kwalifikacji (Etap I + Etap II + LLM + fingerprint)
│   ├── storage/               # Modele SQLAlchemy 2.0 async, repozytorium, automatyczne migracje SQLite
│   ├── services/
│   │   ├── geoportal.py       # Audyt przestrzenny GUGiK ULDK & KIEG WMS
│   │   ├── geocoder.py        # Geokoder Nominatim z cache
│   │   ├── pipeline.py        # Orkiestrator cyklu (scraping → audyt → zapis → powiadomienia)
│   │   ├── config_manager.py  # Konfiguracja profili i harmonogramu (search_config.json)
│   │   ├── live_dashboard.py  # Asynchroniczny serwer aiohttp i REST API
│   │   ├── discord_notifier.py# Powiadomienia Discord Embed
│   │   ├── telegram_notifier.py # Powiadomienia bota Telegram (HTML)
│   │   ├── progress.py        # Śledzenie postępu scrapingu na żywo
│   │   └── report_generator.py# Generator statycznego raportu HTML
│   ├── scheduler/             # Cykliczny runner z trybem dzień/noc
│   └── version.py             # Wersja (zarządzana przez semantic-release)
├── tests/                     # Zestaw testów pytest
├── Dockerfile                 # Obraz produkcyjny
├── docker-compose.yml         # Zestaw usług (Dashboard + Daemon Scrapera)
├── search_config.json         # Profile wyszukiwania, limity portali i harmonogram
├── main.py                    # Główny punkt wejściowy CLI
├── pyproject.toml             # Metadane projektu, ruff, pytest, bandit, semantic-release
├── requirements.txt           # Zależności pip
├── .pre-commit-config.yaml    # Hooki pre-commit (ruff, bezpieczeństwo, higiena plików)
└── listings.db                # Baza SQLite (lokalnie lub w wolumenie Dockera)
```

---

## 🚀 Szybki start z Dockerem (zalecany)

### 1. Sklonuj repozytorium
```bash
git clone https://github.com/p-sternik/Universal-Real-Estate-Hunter.git
cd Universal-Real-Estate-Hunter
```

### 2. Skonfiguruj zmienne środowiskowe
Skopiuj plik przykładowy i uzupełnij dane Discorda/Telegrama oraz (opcjonalnie) klucz LLM:
```bash
cp .env.example .env
```

### 3. Uruchom przez Docker Compose
```bash
docker compose up -d
```

System uruchomi trzy kontenery bez konieczności jakiejkolwiek konfiguracji (zero-config):
1. **`estate_hunter_db`**: wydajna baza PostgreSQL 16 z automatycznym health checkiem i trwałym wolumenem danych (całkowicie eliminuje błędy blokowania bazy SQLite). W przypadku aktualizacji, dotychczasowe dane z bazy SQLite (`listings.db`) są automatycznie migrowane przy pierwszym uruchomieniu.
2. **`estate_hunter_dashboard`**: Live Web Dashboard pod adresem **`http://localhost:8080`**.
3. **`estate_hunter_scraper`**: ciągły background worker monitorujący aktywne profile wyszukiwania.

### 4. Sprawdź logi
```bash
docker compose logs -f scraper
```

---

## 💻 Instalacja lokalna (bez Dockera)

### Wymagania
- Python 3.11, 3.12 lub 3.13

### Wariant A: pip
```bash
git clone https://github.com/p-sternik/Universal-Real-Estate-Hunter.git
cd Universal-Real-Estate-Hunter

# Utwórz i aktywuj środowisko wirtualne
python -m venv venv
# Linux / macOS:
source venv/bin/activate
# Windows PowerShell:
.\venv\Scripts\Activate.ps1

pip install --upgrade pip
pip install -r requirements.txt
```

### Wariant B: uv (zalecany do developmentu)
```bash
uv sync
uv run pre-commit install
```

### Inicjalizacja bazy danych
```bash
python main.py init-db
```

---

## 🛠️ Komendy CLI

Aplikacja udostępnia ujednolicone CLI w [`main.py`](main.py):

| Komenda | Opis | Przykład |
| :--- | :--- | :--- |
| `run` | Start ciągłego demona monitorującego | `python main.py run` lub `python main.py run --profile "Mieszkania Warszawa" --interval 15` |
| `once` | Pojedynczy przebieg scrapingu i kwalifikacji | `python main.py once` lub `python main.py once --profile "Domy Kraków"` |
| `dashboard` | Uruchomienie Live Dashboard z CRM i mapą Leaflet | `python main.py dashboard --port 8080` (alias: `server`) |
| `geoportal` | Audyt zapisanych ofert w Geoportalu GUGiK | `python main.py geoportal --limit 50` (`--all` — z niezakwalifikowanymi) |
| `report` | Generowanie statycznego raportu HTML | `python main.py report` (otwiera w przeglądarce) |
| `view` | Podgląd zakwalifikowanych ofert w terminalu | `python main.py view --status QUALIFIED --limit 15` |
| `reindex` | Ponowna ocena wszystkich ofert w bazie najnowszymi filtrami | `python main.py reindex` |
| `geocode` | Uzupełnienie brakujących współrzędnych GPS przez Nominatim | `python main.py geocode` |
| `test-webhook` | Testowe powiadomienie na Discord | `python main.py test-webhook` |
| `test-filter` | Demonstracja silnika filtrów na syntetycznych scenariuszach | `python main.py test-filter` |
| `init-db` | Inicjalizacja tabel bazy danych | `python main.py init-db` |

Nadpisanie miasta i promienia działa dla `run` i `once`:
```bash
python main.py once --city Kraków --radius 20
```

---

## ⚙️ Konfiguracja

### 1. Zmienne środowiskowe (`.env`)
```ini
# Baza danych (domyślnie SQLite, wspierany PostgreSQL)
DATABASE_URL=sqlite+aiosqlite:///listings.db

# Powiadomienia
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
TELEGRAM_CHAT_ID=-100123456789

# Harmonogram jest w search_config.json (edycja z dashboardu)

# Fallback progów Etapu I
MAX_PRICE=1300000.0
MIN_AREA_HOME=90.0
MAX_AREA_HOME=145.0
MIN_AREA_PLOT=250.0

# Analiza LLM (opcjonalna — patrz sekcja „AI Due Diligence")
USE_LLM_ANALYSIS=false
OPENROUTER_API_KEY=
OPENROUTER_MODEL=nex-agi/nex-n2.5-mini:free
LLM_MAX_CALLS_PER_MINUTE=15
# Alternatywy:
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini
OPENAI_BASE_URL=
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1:8b

# Sieć i scraping
PROXY_URL=
REQUEST_TIMEOUT_SECONDS=25
MAX_RETRIES=3
FETCH_DETAILS=true
```

### 2. Profile wyszukiwania i harmonogram (`search_config.json`)
Wiele niezależnych profili (Domy, Mieszkania, Działki) w dowolnym polskim mieście konfiguruje się z poziomu Web Dashboardu lub edytując `search_config.json`:

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

Włącz analizę LLM ustawiając `USE_LLM_ANALYSIS=true` (lub `llm_analysis_enabled` w `search_config.json`) i konfigurując jeden z backendów:

| Backend | Konfiguracja | Uwagi |
| :--- | :--- | :--- |
| **OpenRouter** (domyślny) | `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` | Wspiera darmowe modele (np. `nex-agi/nex-n2.5-mini:free`); modele płatne pozwalają na wyższy limit zapytań |
| **API zgodne z OpenAI** | `OPENAI_API_KEY`, `OPENAI_MODEL`, `OPENAI_BASE_URL` | Dowolny endpoint zgodny z OpenAI |
| **Ollama** (lokalnie) | `OLLAMA_BASE_URL`, `OLLAMA_MODEL` | W pełni lokalnie, bez kluczy API |

**Optymalizacja kosztów:**
- Oferty z niezmienionym opisem nie są ponownie wysyłane do LLM (wyniki są zachowane w bazie).
- `LLM_MAX_CALLS_PER_MINUTE` ogranicza liczbę zapytań do limitów dostawcy (darmowy OpenRouter = 20 zapytań/min, ustaw 15).
- Automatyczne retry z wykładniczym backoff przy limitach (429) oraz łańcuch awaryjny OpenRouter → OpenAI → Ollama.

**Co dostajesz dla każdej oferty:** konkretne TL;DR, werdykt ✅/❌ z uzasadnieniem liczbowym, precyzyjny stan wykończenia z notatką, wykrycie wizualizacji, 3–5 konkretnych pytań do agenta, dane kontaktowe, ukryte koszty, ryzyka prawne i rozbieżności portal vs. opis.

---

## 🗺️ Jak działa audyt przestrzenny Geoportalu

Gdy oferta przejdzie kwalifikację, system wykonuje bezpłatny audyt przestrzenny w oparciu o oficjalną Krajową Infrastrukturę Informacji Przestrzennej (GUGiK):

1. **Identyfikacja działki:** API ULDK ustala numer działki ewidencyjnej (np. `181609_2.0001.2643/7`).
2. **Rzeczywista powierzchnia prawna:** obliczenia geometryczne na oficjalnych granicach działki w m².
3. **Skan zagrożeń strefowych:** KIEG WMS sprawdza użytkowanie terenu w promieniu 120 m. Wykrycie strefy przemysłowej (`Ba`), magazynowej/handlowej (`Bi`) lub kolejowej (`Tk`) oznacza ofertę ostrzeżeniem i karą −25 punktów w score.

---

## 🧪 Rozwój i testy automatyczne

Uruchomienie testów:
```bash
uv run pytest          # lub: pytest
uv run pytest -m "not integration"   # tylko testy jednostkowe (uruchamiane też pre-push)
```

**Narzędzia jakości** (egzekwowane hookami pre-commit):
- `ruff` — lint i formatowanie
- `bandit` — lint bezpieczeństwa
- `gitleaks` — skanowanie sekretów
- higiena plików: końcowe spacje, nowa linia na końcu pliku, zakończenia linii LF, walidacja JSON/YAML/TOML

```bash
uv run pre-commit install
uv run pre-commit run --all-files
```

Wersje są zarządzane automatycznie przez `python-semantic-release` (conventional commits); zobacz [`CHANGELOG.md`](CHANGELOG.md).

---

## 🔒 Bezpieczeństwo i dobre praktyki

- **Nigdy nie commituj `.env` ani `listings.db`**: oba pliki są wykluczone w `.gitignore` i `.dockerignore`.
- **Kontenery bezstanowe:** w Dockerze dane są montowane w `/app/data/listings.db`, co zapewnia trwałość między restartami i aktualizacjami.
- **Skanowanie sekretów:** `gitleaks` działa przy każdym commicie, zapobiegając wyciekowi kluczy.

---

## 📄 Licencja

Projekt jest licencjonowany na warunkach [PolyForm Noncommercial License 1.0.0](LICENSE) — bezpłatny do użytku osobistego, edukacyjnego i niekomercyjnego. Wykorzystanie komercyjne bez uprzedniej pisemnej zgody autora jest zabronione.

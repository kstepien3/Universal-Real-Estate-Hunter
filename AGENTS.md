# AGENTS.md — Universal Real Estate Hunter

Operational and architectural guide for AI coding agents working on Universal Real Estate Hunter.

---

## 1. Ground Truth Hierarchy

Real estate portal metadata is **unverified, untrusted claim data**. Listing descriptions written by sellers and agents are **forensic evidence of physical reality**.

| Layer | Priority | Trust Level | Description |
| :--- | :--- | :--- | :--- |
| **Physical Ground Truth** | 1 (Highest) | Verified | Physical reality described in listing text (rooms, fixtures, materials, timeline). |
| **Official Spatial / Geoportal** | 2 | High | GUGiK/ULDK parcel IDs, ISOK flood zones, MPZP zoning plans, GESUT infrastructure. |
| **Portal Metadata Tags** | 3 (Lowest) | Untrusted | Dropdown selections on Otodom, Morizon, OLX (often stale defaults or copy-paste relics). |

### Conflict Resolution Principle
- When text evidence contradicts a portal tag, **the text evidence wins**.
- Record every contradiction in `discrepancies` so the buyer is informed of the misleading portal claim.

---

## 2. Finish Condition Taxonomy & Resolution

The finish condition is governed by the **Living Quarters Principle**: readiness of primary residential interior spaces (kitchen, bathrooms, living room, bedrooms, finished floors, operational installations).

### Taxonomy Enums

1. **`pod_klucz` (`DO_ZAMIESZKANIA`)**:
   - **Target state**: Ready for immediate move-in without interior construction work.
   - **Positive physical evidence**: Installed/equipped kitchen with appliances, fully tiled/furnished bathrooms with fixtures, laid finished floors (parkiet, panele, gres), painted walls, heating operational, or property currently inhabited.
   - **Ancillary Elements Rule**: If the interior living quarters are turnkey, but minor outdoor/cosmetic works remain (e.g. *taras do wykończenia*, *niezagospodarowany ogród*, *brak kostki brukowej*, *poddasze do adaptacji*), the property **MUST REMAIN `pod_klucz`**. Record unfinished exterior items in `finish_note` (e.g. `"Wnętrze mieszkalne w pełni wykończone; do zrobienia taras i ogród"`). Do **not** downgrade to `do_wykonczenia`.
   - **False-Friend Traps**: Ignore past-tense history (*"kupiony w stanie deweloperskim i wykończony"*) and mentions of other units (*"dostępne inne segmenty do wykończenia"*). Focus exclusively on the subject unit.

2. **`do_wykonczenia` (`DO_WYKONCZENIA`)**:
   - **Target state**: Building is erected, but interior living quarters require major trades before anyone can move in (bare screeds/plasters, missing bathroom tiles/plumbing, no kitchen, raw floors).
   - NOT a developer primary-market sale with a future delivery date.

3. **`deweloperski` (`DEWELOPERSKI`)**:
   - **Target state**: Primary market sale by a developer/builder, typically standard developer finish (*stan deweloperski*), or actively under construction with a planned completion quarter (*"oddanie IV kwartał 2025"*).

4. **`surowy_zamkniety` / `surowy_otwarty` (`SUROWY_ZAMKNIETY` / `SUROWY_OTWARTY`)**:
   - Explicitly labeled as stan surowy (SSZ / SSO).

5. **`do_remontu` (`DO_REMONTU`)**:
   - Previously inhabited building requiring renovation or modernization.

---

## 3. Filtering & Qualification Architecture

The pipeline processes listings in 3 cascading stages:

1. **Stage 1: Hard Rules** (`src/filters/stage1_hard_rules.py`): Zero-cost fast reject. Filters out-of-budget, wrong building types, blacklisted terms, or non-matching whitelist locations.
2. **Stage 2: Semantic Regex** (`src/filters/stage2_semantic.py`): Fast regex parser extracting preliminary utilities, road conditions, visualisations, and segment subtypes.
3. **Stage 3: LLM Enrichment** (`src/filters/llm_analyzer.py` & `src/filters/__init__.py`): Forensic semantic audit via LLM (`analyze_description`). Applies the Ground Truth Hierarchy, overrides portal tags, evaluates legal risks, hidden costs, and drafts agent questions.

---

## 4. Verification & Quality Gates

Every code change touching scrapers, models, filters, or web interface must satisfy:

1. **Test Suite**: `pytest` (all tests must pass).
2. **Type Checking**: `mypy src/ tests/` (zero errors).
3. **Linter**: `ruff check src/ tests/` (zero warnings/errors).
4. **Container Rebuild** (when services or dependencies change): `docker compose build && docker compose up -d`

---

## Agent skills

### Issue tracker

GitHub issues via `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Canonical roles: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context (`CONTEXT.md` + `docs/adr/`). See `docs/agents/domain.md`.

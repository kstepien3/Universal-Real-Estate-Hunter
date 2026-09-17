# Changelog

All notable changes to this project will be documented in this file.

The changelog is maintained automatically by the release workflow.

<!-- version list -->

## v1.14.2 (2026-09-17)

### Code Style

- **ui**: Unify due diligence drawer tables, typography, and spacing
  ([`8264df1`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/8264df1f051645fca201576e3a16ce2395c914d8))

### Refactoring

- **filters,storage**: Deduplicate persistence and streamline filter models
  ([`b7256e8`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/b7256e8c59b7989f96afe6f560104ad9d91dda30))

- **pipeline**: Unify spatial audit and consolidate notification dispatch
  ([`3552123`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/3552123f33b914b4ef75762d05b09a825992edfb))


## v1.14.1 (2026-09-17)

### Bug Fixes

- **ci**: Checkout release tag in publish job to fix container version mismatch
  ([`8bb69f1`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/8bb69f1c49ac84ce0609941f7437a4bfd31392ec))


## v1.14.0 (2026-09-16)

### Bug Fixes

- **commute**: Resolve profile city and transit POI in commute audit
  ([`3f1d4ef`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/3f1d4ef71d4be956efcf3972d35cbb0eb4f221e3))

### Features

- **due-diligence**: Add solar potential, POI walkability, geology audit and structured risks
  ([`79a1ae6`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/79a1ae6244c6cd50811f9ba6b5889692ab93e4c2))


## v1.13.1 (2026-09-16)

### Bug Fixes

- **ui**: Enlarge split-view card photos to 500px max-height
  ([`c382e16`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/c382e160c5194142988a0e59bc7f8b6a0f421dda))


## v1.13.0 (2026-09-16)

### Features

- **dashboard**: Server-side thumbnails, valuation cache, map clustering, overview tab and update
  check
  ([`56bb4ee`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/56bb4eef027e97ecdd4d316482cbd2d3acf5a28f))


## v1.12.1 (2026-09-15)

### Performance Improvements

- **dashboard**: Leaner listings API, first-party image proxy, lazy map init
  ([`d586fdf`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/d586fdfe669cc3bf883f5b65b77fe2918a12c245))


## v1.12.0 (2026-09-15)

### Features

- **ui**: Mobile-first overhaul for iphone 15 and responsive polish
  ([`db6953b`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/db6953bef65a9d3789d588c8ff7167a0ba17a236))


## v1.11.1 (2026-09-14)

### Refactoring

- **ui**: Optimize split view density, analytical map pins and card layout
  ([`c5de872`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/c5de872062fae59b4e619c87ff3e6865dbd0e5eb))


## v1.11.0 (2026-09-14)

### Bug Fixes

- **ai**: Ensure ollama preset normalizes url and does not inherit stale lmstudio port 1234
  ([`0a1d24a`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/0a1d24a9bf5b3eeebecf873212c3f6f16292dc3d))

- **ai**: Only probe and display configured LLM provider during test connection
  ([`418c90d`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/418c90d2b1a1fed8eb084827db997cfffd652ac7))

### Features

- Implement re-listing detection & extract due-diligence dashboard module
  ([`4a27d2f`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/4a27d2f3ddbfb5b8b75139c8efeb0e7a746de3e6))

- **ai**: Add hardware-aware Ollama recommendations, Bielik support, and tok/s diagnostics
  ([`52f9de3`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/52f9de3612cd6ff6afda2ef0852af7e8e188ff5b))

- **ai**: Support local OpenAI-compatible servers (LM Studio, vLLM, Docker Model Runner)
  ([`3e8b930`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/3e8b930dfb8e6346b6286e26e7bfdbdee206f1af))

- **ai**: Unify local LLM configuration with presets and add cloud timeout
  ([`0536771`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/053677175f9516f6925bce1eed360c7eecc6b9a2))

### Refactoring

- **ai**: Remove container-skewed hardware profiling in favor of real server metrics
  ([`be233eb`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/be233eb7ca0950632f867eeb402e5a1ac190896b))

- **ui**: Remove unnecessary local LLM API key field from modal
  ([`9ca1ce8`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/9ca1ce83d4eff82413b813c13341af5fdeb2e50e))


## v1.10.3 (2026-09-14)

### Refactoring

- Apply KISS and YAGNI cleanups across pipeline, storage and dashboard
  ([`30a4298`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/30a4298ccd271e91f12220534b82bf48b3916764))


## v1.10.2 (2026-09-14)

### Chores

- Update uv, streamline Docker/CI, drop redundant python-dotenv
  ([`111c97b`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/111c97bdf91987627f9dc11bc01c8b36faabd0f4))

### Refactoring

- Dedupe spatial field mapping and notification predicate
  ([`d65c6e0`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/d65c6e024912c06512c6c7717012eeb492db1ce5))


## v1.10.1 (2026-09-14)

### Bug Fixes

- **commute**: Stop fabricating a city-center anchor for unknown cities
  ([`20dade4`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/20dade426524545bb5a8279633c4caf3d16da082))


## v1.10.0 (2026-09-14)

### Bug Fixes

- **air-quality**: Fetch all GIOŚ stations nationwide with pagination size=500 instead of default 20
  ([`c68aa19`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/c68aa192086bfe6571735d81391dfd7e2b6bc15d))

- **db**: Add schema migrations for PostgreSQL and improve dashboard loading state
  ([`f31885d`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/f31885dc2063a75cff293236c17937c44899b7ac))

- **db**: Handle duplicate keys gracefully with session.merge during sqlite to postgres migration
  ([`39fe7d3`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/39fe7d3b6843fe35d0aa39851501299ce8b385e4))

- **db**: Use DateTime(timezone=True) and TIMESTAMPTZ for asyncpg PostgreSQL compatibility
  ([`fc7d989`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/fc7d989b2396cee197f69cb8d49e63b9dd7f143b))

### Code Style

- Apply pre-commit formatters and linters (ruff, mypy)
  ([`a16cc8d`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/a16cc8d8f4f1951bb5f481c1e2a27857218d9dcf))

### Features

- Generalize from Rzeszów to nationwide multi-city support
  ([`b5977c1`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/b5977c1cdb42652a640fa6435ac86cc95cde9c00))

- **air-quality**: Implement air quality & smog risk intelligence (CAMS + GIOŚ)
  ([`f6ac130`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/f6ac1304eb429be1949c22eb01e9f6de01ea27ca))

### Refactoring

- Trim dead compatibility wrapper and shrink test closures
  ([`0e90254`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/0e902549e1869d62c16371fc23c0c419dc4b09ee))


## v1.9.3 (2026-09-11)

### Bug Fixes

- **dashboard**: Remove card height constraint in split view
  ([`3c3b872`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/3c3b872819970d53f8cc65be64de1e33be65dca2))


## v1.9.2 (2026-09-11)

### Bug Fixes

- **docker,progress**: Optimize image size, remove dead deps, and fix multi-process scrape status
  sync
  ([`24690f2`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/24690f2eda28b68b033304683ba6777488d057c8))


## v1.9.1 (2026-09-11)

### Bug Fixes

- **logs**: Separate rejected and error log filters without duplication
  ([`e98d503`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/e98d503b7ffcf6e8cc9ab84a374ab09ed8e2d070))


## v1.9.0 (2026-09-11)

### Bug Fixes

- **dashboard**: Resolve profile selector dropdown z-index and clipping
  ([`0c878db`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/0c878db1ed1ef17ee1afb81cfbacdcbaa659597f))

- **db**: Ensure init_db is called on dashboard and scheduler startup
  ([`a2ee699`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/a2ee699211fd996418637e60802cf1ecf25ed3ed))

- **progress**: Prevent test progress logs from leaking into shared status file
  ([`95c72e4`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/95c72e44dbedc15fa3de041e40607cd0ec9b88ee))

### Features

- Migrate default docker database to postgresql with auto-migration from sqlite
  ([`6bd5ff3`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/6bd5ff3df9f9d4d2759bf0d1200ad60923c5111e))

### Refactoring

- **storage**: Shrink sqlite to postgres auto-migration logic
  ([`4926b93`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/4926b932b9e1abb9e11f74ef8d768b9f4afdfa72))


## v1.8.1 (2026-09-11)

### Bug Fixes

- **scheduler**: Prevent daemon exit and docker restart-loop when scheduler is disabled
  ([`afbf890`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/afbf89049091b8c0e67c63f535112c8922c8ed88))


## v1.8.0 (2026-09-11)

### Features

- Refactor settings modal for mobile RWD, bottom sheet layout, and touch ergonomics
  ([`9c0dd7a`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/9c0dd7aa4e113a205a7abc44032e123c5136bfaa))


## v1.7.0 (2026-09-11)

### Features

- Safety hard-rejects, configurable CAPEX, spatial cache and dashboard UX
  ([`4c6903d`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/4c6903d037e6760e5197f99c305a3f0f4ca7fbbf))


## v1.6.4 (2026-09-11)

### Bug Fixes

- Enforce profile isolation, dynamic city analytics, and multi-provider llm configuration
  ([`cc74ac6`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/cc74ac61dad781ba771cebf8aaa1c2fcb9cd133c))


## v1.6.3 (2026-09-10)

### Bug Fixes

- Resolve sqlite readonly database and lock contention across containers
  ([`1dd0e73`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/1dd0e738aa66342bbeabd62d260952951dbb6abb))


## v1.6.2 (2026-09-10)

### Bug Fixes

- Build and push multi-arch docker images for amd64 and arm64
  ([`8902d60`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/8902d60b7317a3320bcb0e34ec46ea4a707784a3))


## v1.6.1 (2026-09-10)

### Bug Fixes

- Resolve sqlite database locked error and add rich progress logging with live filter
  ([`50e9df2`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/50e9df2c14c001a8d61eab195a94565c98db1253))


## v1.6.0 (2026-09-10)

### Features

- Auto-backfill spatial due diligence for existing listings during sync
  ([`47a1d56`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/47a1d56825015347f2c62e6f5c71990fd399790b))


## v1.5.0 (2026-09-10)

### Features

- Implement 5 advanced spatial checks (SIDUSIS FTTH, parcel OBB geometry, NMT slope, PKA
  walkability, high-voltage lines)
  ([`c7d9897`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/c7d98971597fc815778039265b4d0f35157d7af1))


## v1.4.0 (2026-09-10)

### Features

- Implement Tier 1 geospatial due diligence and spatial audit integration
  ([`4b070d8`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/4b070d88ca0eec005c388a084c0639fecef8a716))


## v1.3.0 (2026-09-10)

### Code Style

- Apply pre-commit lint and format fixes
  ([`18bd44a`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/18bd44a15a8b27749897ca76e18d760377f822ff))

### Features

- Negotiation intelligence, scrape cancellation, and dashboard static split
  ([`295fc61`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/295fc6189a19f0f614925c5e7b73306cac8d5c84))


## v1.2.0 (2026-09-10)

### Documentation

- **license**: Update license to PolyForm Noncommercial 1.0.0
  ([`3db9089`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/3db90890d0da53054cdce7ca8e84b3678918a595))

### Features

- Integrate MPZP zoning and ISOK flood risk audit with LLM due-diligence
  ([`94f145c`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/94f145c40441626ffb32fc05751b4b40e2ed3341))


## v1.1.0 (2026-09-10)

### Features

- Overhaul LLM due-diligence prompt and add dashboard DB reset
  ([`bdc3657`](https://github.com/p-sternik/Universal-Real-Estate-Hunter/commit/bdc36570e569a117a2c108c7ed6dcde3ddd59b09))


## v1.0.0 (2026-09-10)

- Initial Release

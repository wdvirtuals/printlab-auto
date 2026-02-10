# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PrintLab Auto is a Telegram-controlled 3D print automation system for Bambu Lab X1C printers. Users search for 3D models via Telegram, and the system downloads and sends them to the printer via MQTT/FTPS.

## Commands

```bash
# Install dependencies
pip install -e ".[dev]"

# Install with REST API support (FastAPI + uvicorn)
pip install -e ".[dev,api]"

# Run the application
printlab

# Run all tests (excluding live-endpoint search tests)
pytest --ignore=tests/test_search.py

# Run all tests including live search
pytest

# Run a single test file
pytest tests/test_agent.py

# Run a specific test
pytest tests/test_agent.py::test_parse_query_basic
```

**Note:** Use `python3` (not `python`) on this machine — macOS system Python 3.9.6.

## Architecture

```
src/printlab/
├── main.py           # Entry point, loads .env config, runs async Telegram polling
├── agent/claude.py   # ClaudeAgent - NLP query parsing and result ranking via Claude API
├── bot/handlers.py   # PrintLabBot - Telegram commands and inline keyboard callbacks
├── bridge/           # Native library bridge for Bambu Lab auth
│   ├── __init__.py   # Python module: auto-compiles bridge, extracts JWT token via ctypes
│   └── bambu_bridge.cpp  # C++ wrapper: translates std::string ↔ const char* for dylib
├── printer/x1c.py    # X1CClient - MQTT/FTPS communication with Bambu Lab X1C
├── slicer/slicer.py  # Slicer - auto-slices STL to 3MF (supports Orca/Bambu/PrusaSlicer)
└── search/           # Pluggable search providers
    ├── base.py       # SearchProvider ABC and Model dataclass
    ├── makerworld.py # MakerWorld API (disabled — auth issues)
    ├── cults3d.py    # Cults3D web scraping
    ├── thingiverse.py# Thingiverse API
    ├── printables.py # Printables GraphQL API
    └── websearch.py  # DuckDuckGo-based multi-site search
```

Uses `hatchling` build system with `src/` layout — imports use `printlab.*` (e.g., `from printlab.search.base import Model`).

### REST API (OpenClaw Integration)

An optional FastAPI server at `src/printlab/api/server.py` exposes printer and search functions as HTTP endpoints. Enabled by setting `API_PORT` in `.env` — it runs alongside the Telegram bot in the same process, sharing the same `X1CClient` and `ThingiverseSearch` instances via `create_app()`.

- Auth: `API_KEY` env var, sent as `Authorization: Bearer <key>`
- Endpoints: `/api/health`, `/api/search`, `/api/status`, `/api/print`, `/api/trays`, `/api/pause`, `/api/resume`, `/api/stop`
- Requires `pip install -e ".[api]"` for FastAPI/uvicorn dependencies
- OpenClaw skill definition: `openclaw/SKILL.md`
- ACP offerings and registration: `openclaw/acp/`

**Key flows:**
1. User sends message → `ClaudeAgent.parse_query()` extracts search terms
2. Parallel search across providers → `ClaudeAgent.rank_results()` ranks by relevance
3. User selects model via inline keyboard → download via provider
4. If STL file → auto-slice to 3MF using template injection + Orca/Bambu CLI
5. Upload to printer via FTPS (port 990) → start print via MQTT (port 8883)

**Currently active provider:** Only `ThingiverseSearch` is wired into `PrintLabBot.search_providers`. MakerWorld was removed due to auth issues. Thingiverse requires zero config — it uses an anonymous bearer token from their public JS bundle. Other providers (Cults3D, Printables, WebSearch) exist but aren't used by default.

**Communication protocols:**
- Printer MQTT: port 8883 with TLS (self-signed certs, `CERT_NONE`)
- Printer FTPS: port 990, uploads to `/cache/` folder
- MQTT topics: `device/{serial}/report` (subscribe), `device/{serial}/request` (publish)
- Downloads stored in `~/.printlab/downloads`

## Thingiverse API Details

The Thingiverse provider is the only active search backend. Key implementation details:
- **Token:** Anonymous bearer token `56edfc79ecf25922b98202dd79a291aa` from their public JS bundle (`app.bundle.js` module 88673). May rotate when Thingiverse updates their bundle.
- **API proxy:** Must use `www.thingiverse.com/api/` — the direct `api.thingiverse.com` endpoint is blocked by Cloudflare.
- **Search:** `GET /api/search/{query}` with `type=things&sort=popular` params
- **File URLs:** `/api/things/{id}/files` is blocked by Cloudflare. Instead, use `zip_data.files` from the `/api/things/{id}` detail response, which provides direct CDN URLs (`cdn.thingiverse.com/assets/...`) that need no auth.

## Configuration

Copy `.env.example` to `.env` and set:
- `TELEGRAM_BOT_TOKEN` - from @BotFather
- `ANTHROPIC_API_KEY` - Claude API key
- `X1C_IP`, `X1C_ACCESS_CODE`, `X1C_SERIAL` - printer credentials
- `ALLOWED_USERS` (optional) - comma-separated Telegram user IDs
- `BED_TYPE` (optional) - bed type for slicing, e.g. `cool_plate` (default: `auto`)
- `API_PORT` (optional) - set to enable REST API (e.g. `8000`)
- `API_KEY` (optional) - auth key for REST API

**MakerWorld auth:** No env vars needed. The token is read automatically from BambuStudio or OrcaSlicer. Just log into either app normally. Requires Xcode Command Line Tools for first-run bridge compilation (`xcode-select --install`).

## Code Patterns

- All I/O is async (MQTT, HTTP, Telegram). Uses `httpx.AsyncClient` for HTTP.
- `ClaudeAgent` uses `claude-sonnet-4-20250514` for both query parsing and result ranking.
- Search providers inherit from `SearchProvider` ABC with `search()` and `get_download_url()` methods. Each provider must also implement `close()` for cleanup (called by `PrintLabBot.shutdown()`, though not declared abstract in the base class).
- Per-user session state (`self.sessions`) tracks multi-step workflows in bot handlers, keyed by `(chat_id, user_id)` tuples.
- Inline keyboard callbacks use colon-delimited prefixes: `select:{idx}`, `confirm:{idx}`, `cancel`, `back`, `more`
- `send_print_job()` returns `True`/`False` or string error codes (`no_slicer`, `wrong_slicer`, `no_template`, `slice_failed`) — the bot handler maps each to a user-facing message
- STL slicing uses a template-based approach: user must place a pre-configured `template.3mf` at `~/.printlab/template.3mf`, then the slicer injects STL geometry into it
- Telegram messages use MarkdownV2 — the `_esc()` helper escapes special characters in external data

## Testing

- pytest with `asyncio_mode = "auto"` (configured in `pyproject.toml`)
- Agent tests mock `AsyncAnthropic` via `unittest.mock.patch`
- API tests mock printer/search — pure unit tests
- Printer tests are pure unit tests on `PrinterStatus` parsing/display
- Search tests (`tests/test_search.py`) hit live Thingiverse endpoints — skip with `--ignore=tests/test_search.py` when offline or to avoid flakiness

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PrintLab Auto is a 3D print automation system for Bambu Lab X1C printers with two interfaces:

1. **PrintLabBot** — Telegram bot with inline keyboard workflows for searching and printing 3D models
2. **PrintClaw** — Autonomous conversational agent (also Telegram) powered by Claude tool calling, with shell access, file manipulation, skill system, and ACP seller integration

Both share the same REST API backend that wraps printer and search functionality.

## Commands

```bash
# Set up Python 3.12 venv (required — system Python is 3.9.6)
/opt/homebrew/bin/python3.12 -m venv .venv
source .venv/bin/activate

# Install all dependencies
pip install -e ".[dev,api]"
pip install virtuals-acp  # ACP seller SDK

# Run the original Telegram bot
.venv/bin/python -m printlab.main

# Run PrintClaw (conversational agent + ACP seller)
.venv/bin/python -m printlab.printclaw.main

# Run tests (excluding live-endpoint search tests)
.venv/bin/pytest tests --ignore=tests/test_search.py

# Run all tests including live Thingiverse search
.venv/bin/pytest tests

# Run a single test
.venv/bin/pytest tests/test_agent.py::test_parse_query_basic
```

## Architecture

```
src/printlab/
├── main.py              # Original entry point: Telegram bot + optional REST API
├── agent/claude.py      # ClaudeAgent — NLP query parsing and result ranking
├── bot/handlers.py      # PrintLabBot — Telegram inline keyboard workflows
├── api/server.py        # FastAPI REST API wrapping printer + search
├── printer/x1c.py       # X1CClient — MQTT/FTPS communication with printer
├── slicer/slicer.py     # STL→3MF slicing via Orca/Bambu/PrusaSlicer CLI
├── search/              # Pluggable search providers (only Thingiverse active)
│   ├── base.py          # SearchProvider ABC and Model dataclass
│   └── thingiverse.py   # Active provider — anonymous token, zero config
├── bridge/              # Native C++ bridge for Bambu Lab auth (MakerWorld)
└── printclaw/           # Autonomous agent with ACP seller
    ├── main.py          # Entry point: embedded REST API + TG bot + ACP seller
    ├── bot.py           # Claude tool-calling conversation loop (14 tools)
    ├── api_client.py    # Async httpx client for PrintLab REST API
    └── acp_seller.py    # Virtuals ACP seller via WebSocket
```

Uses `hatchling` build system with `src/` layout — imports use `printlab.*`.

### Two Entry Points

- **`printlab.main`** — Runs PrintLabBot (inline keyboards) + optional REST API. The original bot.
- **`printlab.printclaw.main`** — Runs PrintClaw (conversational agent) with its own embedded REST API + ACP seller. Self-contained — does not require `printlab.main` to be running separately.

### PrintClaw Tools (14 total)

The conversational agent has these Claude tool-calling tools:
- **Printer**: `search_models`, `get_printer_status`, `print_model`, `get_filaments`, `pause_print`, `resume_print`, `stop_print`
- **System**: `run_command` (shell), `read_file`, `write_file`, `list_directory`
- **Skills**: `install_skill` (from GitHub repos), `list_skills`, `remove_skill`

Skills are SKILL.md files stored in `~/.printclaw/skills/`. They're parsed (YAML frontmatter + markdown body) and appended to the system prompt on startup.

### ACP Seller Integration

`printclaw/acp_seller.py` wraps the `virtuals-acp` SDK to listen for incoming jobs via WebSocket. The SDK uses synchronous `python-socketio` with threaded callbacks — bridged to async via `asyncio.run_coroutine_threadsafe()`. Jobs are routed by offering name substring match ("search" / "print" / "status") and fulfilled by calling the REST API. Gracefully skipped if `VIRTUALS_*` env vars are missing.

### REST API

FastAPI server at `api/server.py` — shared by both entry points. `create_app()` accepts pre-existing printer/search objects so the Telegram bot and API share instances.

- Auth: `Authorization: Bearer {API_KEY}` (skipped if no `API_KEY` set)
- Endpoints: `/api/health`, `/api/search`, `/api/status`, `/api/print`, `/api/trays`, `/api/pause`, `/api/resume`, `/api/stop`
- ACP offerings defined in `openclaw/acp/offerings.json` (3 offerings with JSON schemas)

### Key Flows

1. User message → `ClaudeAgent.parse_query()` extracts search terms → parallel search → `ClaudeAgent.rank_results()` ranks by relevance
2. User selects model → download → if STL, auto-slice to 3MF via template injection → upload via FTPS (port 990) → start print via MQTT (port 8883)
3. `send_print_job()` returns `True`/`False` or string error codes (`no_slicer`, `wrong_slicer`, `no_template`, `slice_failed`, `storage_error`, `upload_failed`)

## Thingiverse API Details

Only active search provider. Key gotchas:
- **Token:** Anonymous bearer `56edfc79ecf25922b98202dd79a291aa` from public JS bundle. May rotate.
- **API proxy:** Must use `www.thingiverse.com/api/` — `api.thingiverse.com` blocked by Cloudflare.
- **File URLs:** `/api/things/{id}/files` blocked — use `zip_data.files` from thing detail instead. CDN URLs need no auth.

## Configuration

Copy `.env.example` to `.env` and set:
- `TELEGRAM_BOT_TOKEN`, `ANTHROPIC_API_KEY` — required for PrintLabBot
- `X1C_IP`, `X1C_ACCESS_CODE`, `X1C_SERIAL` — printer credentials
- `BED_TYPE` (optional) — e.g. `cool_plate` (default: `auto`)
- `API_PORT`, `API_KEY` (optional) — REST API
- `PRINTCLAW_TG_TOKEN` — separate Telegram bot token for PrintClaw
- `PRINTCLAW_OWNER_CHAT_ID` — owner's chat ID for ACP event notifications
- `VIRTUALS_AGENT_WALLET_ADDRESS`, `VIRTUALS_WALLET_PRIVATE_KEY`, `VIRTUALS_ENTITY_ID` — ACP seller credentials (all optional)

## Code Patterns

- All I/O is async. Uses `httpx.AsyncClient` for HTTP.
- `ClaudeAgent` uses `claude-sonnet-4-20250514` for query parsing and ranking.
- Search providers inherit `SearchProvider` ABC with `search()`, `get_download_url()`, and `close()` methods.
- PrintLabBot sessions keyed by `(chat_id, user_id)` tuples. Callbacks use colon-delimited prefixes: `select:{idx}`, `confirm:{idx}`, `tray:{idx}:{tray}`, `cancel`, `back`, `more`.
- PrintClaw conversation history is in-memory per chat_id, capped at 30 messages.
- Telegram messages use MarkdownV2 — `_esc()` helper escapes special chars.
- STL slicing requires `~/.printlab/template.3mf` — slicer injects geometry into it.

## Testing

- pytest with `asyncio_mode = "auto"` (in `pyproject.toml`)
- Agent tests mock `AsyncAnthropic` via `unittest.mock.patch`
- API tests mock printer/search — pure unit tests
- Printer tests are pure unit tests on `PrinterStatus` parsing
- Search tests (`tests/test_search.py`) hit live Thingiverse — skip with `--ignore=tests/test_search.py`

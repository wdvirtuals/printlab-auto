"""PrintLab REST API — FastAPI wrapper for printer and search functions."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Header
from pydantic import BaseModel

from ..printer.x1c import X1CClient, PrinterStatus, AmsTray
from ..search.thingiverse import ThingiverseSearch
from ..search.base import Model


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------

class SearchRequest(BaseModel):
    query: str
    limit: int = 5


class PrintRequest(BaseModel):
    model_id: Optional[str] = None
    model_url: Optional[str] = None
    filename: Optional[str] = None
    ams_mapping: Optional[list[int]] = None


# ---------------------------------------------------------------------------
# Shared state (populated during lifespan)
# ---------------------------------------------------------------------------

_printer: Optional[X1CClient] = None
_search: Optional[ThingiverseSearch] = None
_api_key: Optional[str] = None


def _get_printer() -> X1CClient:
    if _printer is None:
        raise HTTPException(503, "Printer client not initialized")
    return _printer


def _get_search() -> ThingiverseSearch:
    if _search is None:
        raise HTTPException(503, "Search provider not initialized")
    return _search


async def _check_auth(authorization: Optional[str] = Header(None)):
    if not _api_key:
        return  # no key configured = open access
    if not authorization or authorization != f"Bearer {_api_key}":
        raise HTTPException(401, "Invalid or missing API key")


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _printer, _search, _api_key
    _api_key = os.getenv("API_KEY")

    # Printer client — uses env vars already loaded by main.py
    ip = os.getenv("X1C_IP", "")
    access_code = os.getenv("X1C_ACCESS_CODE", "")
    serial = os.getenv("X1C_SERIAL", "")
    bed_type = os.getenv("BED_TYPE", "auto")

    if ip and access_code and serial:
        _printer = X1CClient(ip, access_code, serial, bed_type=bed_type)
        connected = await _printer.connect()
        print(f"API: Printer {'connected' if connected else 'connection failed'}")

    _search = ThingiverseSearch()

    yield

    # Cleanup
    if _printer:
        await _printer.disconnect()
    if _search:
        await _search.close()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="PrintLab API", version="0.1.0", lifespan=lifespan)


def create_app(
    printer: Optional[X1CClient] = None,
    search: Optional[ThingiverseSearch] = None,
    api_key: Optional[str] = None,
) -> FastAPI:
    """Create app with pre-existing instances (for shared use with Telegram bot)."""
    global _printer, _search, _api_key
    _printer = printer
    _search = search
    _api_key = api_key
    return app


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

ERROR_MESSAGES = {
    "no_slicer": "No slicer installed — install OrcaSlicer or BambuStudio",
    "wrong_slicer": "PrusaSlicer cannot produce Bambu-compatible files",
    "no_template": "Slicing template missing — see setup instructions",
    "slice_failed": "Slicing failed",
    "storage_error": "Printer storage not writable — check SD card",
    "upload_failed": "FTPS upload to printer failed",
}


@app.get("/api/health", dependencies=[Depends(_check_auth)])
async def health():
    printer = _printer
    return {
        "status": "ok",
        "printer_connected": printer.is_connected if printer else False,
    }


@app.post("/api/search", dependencies=[Depends(_check_auth)])
async def search(req: SearchRequest, search_prov: ThingiverseSearch = Depends(_get_search)):
    models = await search_prov.search(req.query, limit=req.limit)
    return {
        "models": [asdict(m) for m in models],
    }


@app.get("/api/status", dependencies=[Depends(_check_auth)])
async def status(printer: X1CClient = Depends(_get_printer)):
    st = await printer.get_status()
    if st is None:
        raise HTTPException(503, "Could not reach printer")
    return asdict(st)


@app.post("/api/print", dependencies=[Depends(_check_auth)])
async def print_model(
    req: PrintRequest,
    printer: X1CClient = Depends(_get_printer),
    search_prov: ThingiverseSearch = Depends(_get_search),
):
    # Resolve download URL
    download_url = req.model_url
    filename = req.filename

    if not download_url and req.model_id:
        # Look up download URL from Thingiverse
        model = Model(
            id=req.model_id, name="", url="", thumbnail=None,
            author="", provider="Thingiverse",
        )
        download_url = await search_prov.get_download_url(model)
        if not download_url:
            raise HTTPException(404, f"No downloadable file for model {req.model_id}")
        if model.file_count > 1:
            raise HTTPException(
                400,
                f"Multi-part model ({model.file_count} files) — only single-file models supported",
            )

    if not download_url:
        raise HTTPException(400, "Provide model_url or model_id")

    if not filename:
        filename = download_url.split("/")[-1] or f"model_{req.model_id}"

    # Download
    file_path = await printer.download_model(download_url, filename)
    if not file_path:
        raise HTTPException(502, "Failed to download model file")

    # Print
    result = await printer.send_print_job(file_path, ams_mapping=req.ams_mapping)

    if result is True:
        return {"success": True}
    elif isinstance(result, str):
        msg = ERROR_MESSAGES.get(result, result)
        raise HTTPException(422, {"error": result, "message": msg})
    else:
        raise HTTPException(500, "Print job failed")


@app.get("/api/trays", dependencies=[Depends(_check_auth)])
async def trays(printer: X1CClient = Depends(_get_printer)):
    tray_list = await printer.get_ams_trays()
    return {"trays": [asdict(t) for t in tray_list]}


@app.post("/api/pause", dependencies=[Depends(_check_auth)])
async def pause(printer: X1CClient = Depends(_get_printer)):
    await printer.pause()
    return {"ok": True}


@app.post("/api/resume", dependencies=[Depends(_check_auth)])
async def resume(printer: X1CClient = Depends(_get_printer)):
    await printer.resume()
    return {"ok": True}


@app.post("/api/stop", dependencies=[Depends(_check_auth)])
async def stop(printer: X1CClient = Depends(_get_printer)):
    await printer.stop()
    return {"ok": True}

"""Tests for PrintLab REST API."""

import pytest
from dataclasses import asdict
from unittest.mock import AsyncMock, MagicMock, patch

# FastAPI test client needs httpx
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from printlab.api.server import app, create_app
from printlab.printer.x1c import PrinterStatus, AmsTray
from printlab.search.base import Model


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_printer():
    printer = AsyncMock()
    printer.is_connected = True
    printer.get_status = AsyncMock(return_value=PrinterStatus(
        state="IDLE",
        progress=0,
        current_layer=0,
        total_layers=0,
        remaining_time=0,
        bed_temp=25.0,
        nozzle_temp=25.0,
    ))
    printer.get_ams_trays = AsyncMock(return_value=[
        AmsTray(ams_id=0, tray_id=0, color="FF0000FF", material="PLA"),
        AmsTray(ams_id=0, tray_id=1, color="00FF00FF", material="PETG"),
    ])
    printer.send_print_job = AsyncMock(return_value=True)
    printer.download_model = AsyncMock(return_value=MagicMock())
    printer.pause = AsyncMock()
    printer.resume = AsyncMock()
    printer.stop = AsyncMock()
    return printer


@pytest.fixture
def mock_search():
    search = AsyncMock()
    search.search = AsyncMock(return_value=[
        Model(
            id="123",
            name="Test Dragon",
            url="https://www.thingiverse.com/thing:123",
            thumbnail="https://cdn.thingiverse.com/thumb.jpg",
            author="TestUser",
            provider="Thingiverse",
            downloads=100,
            likes=50,
        )
    ])
    search.get_download_url = AsyncMock(return_value="https://cdn.thingiverse.com/test.stl")
    return search


@pytest.fixture
def client(mock_printer, mock_search):
    create_app(printer=mock_printer, search=mock_search, api_key="test-key")
    return TestClient(app)


@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer test-key"}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_health(client, auth_headers):
    resp = client.get("/api/health", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["printer_connected"] is True


def test_health_no_auth(client):
    resp = client.get("/api/health")
    assert resp.status_code == 401


def test_health_bad_auth(client):
    resp = client.get("/api/health", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


def test_search(client, auth_headers, mock_search):
    resp = client.post(
        "/api/search",
        json={"query": "dragon", "limit": 5},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["models"]) == 1
    assert data["models"][0]["name"] == "Test Dragon"
    mock_search.search.assert_called_once_with("dragon", limit=5)


def test_status(client, auth_headers, mock_printer):
    resp = client.get("/api/status", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["state"] == "IDLE"
    assert data["bed_temp"] == 25.0


def test_trays(client, auth_headers, mock_printer):
    resp = client.get("/api/trays", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["trays"]) == 2
    assert data["trays"][0]["material"] == "PLA"
    assert data["trays"][1]["material"] == "PETG"


def test_print_by_id(client, auth_headers, mock_printer, mock_search):
    resp = client.post(
        "/api/print",
        json={"model_id": "123"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    mock_search.get_download_url.assert_called_once()
    mock_printer.download_model.assert_called_once()
    mock_printer.send_print_job.assert_called_once()


def test_print_by_url(client, auth_headers, mock_printer):
    resp = client.post(
        "/api/print",
        json={"model_url": "https://example.com/model.stl", "filename": "test.stl"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    mock_printer.download_model.assert_called_once_with(
        "https://example.com/model.stl", "test.stl"
    )


def test_print_missing_params(client, auth_headers):
    resp = client.post(
        "/api/print",
        json={},
        headers=auth_headers,
    )
    assert resp.status_code == 400


def test_print_error_code(client, auth_headers, mock_printer, mock_search):
    mock_printer.send_print_job = AsyncMock(return_value="no_slicer")
    resp = client.post(
        "/api/print",
        json={"model_id": "123"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_pause(client, auth_headers, mock_printer):
    resp = client.post("/api/pause", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    mock_printer.pause.assert_called_once()


def test_resume(client, auth_headers, mock_printer):
    resp = client.post("/api/resume", headers=auth_headers)
    assert resp.status_code == 200
    mock_printer.resume.assert_called_once()


def test_stop(client, auth_headers, mock_printer):
    resp = client.post("/api/stop", headers=auth_headers)
    assert resp.status_code == 200
    mock_printer.stop.assert_called_once()

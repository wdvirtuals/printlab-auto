"""Tests for printer module."""

import pytest
from printlab.printer.x1c import PrinterStatus, AmsTray, X1CClient


def test_printer_status_idle():
    """Test PrinterStatus parsing for idle state."""
    data = {
        "print": {
            "gcode_state": "IDLE",
            "mc_percent": 0,
            "layer_num": 0,
            "total_layer_num": 0,
            "mc_remaining_time": 0,
            "bed_temper": 25.5,
            "nozzle_temper": 28.0,
        }
    }

    status = PrinterStatus.from_mqtt(data)
    assert status.state == "IDLE"
    assert status.progress == 0
    assert status.bed_temp == 25.5
    assert status.nozzle_temp == 28.0


def test_printer_status_printing():
    """Test PrinterStatus parsing for printing state."""
    data = {
        "print": {
            "gcode_state": "RUNNING",
            "mc_percent": 45,
            "layer_num": 120,
            "total_layer_num": 300,
            "mc_remaining_time": 95,  # minutes
            "bed_temper": 60.0,
            "nozzle_temper": 220.0,
        }
    }

    status = PrinterStatus.from_mqtt(data)
    assert status.state == "PRINTING"
    assert status.progress == 45
    assert status.current_layer == 120
    assert status.total_layers == 300
    assert status.remaining_time == 95


def test_printer_status_display_idle():
    """Test display text for idle printer."""
    status = PrinterStatus(
        state="IDLE",
        progress=0,
        current_layer=0,
        total_layers=0,
        remaining_time=0,
        bed_temp=25.0,
        nozzle_temp=28.0,
    )

    text = status.display_text()
    assert "idle" in text.lower()
    assert "25" in text
    assert "28" in text


def test_printer_status_display_printing():
    """Test display text for printing state."""
    status = PrinterStatus(
        state="PRINTING",
        progress=50,
        current_layer=150,
        total_layers=300,
        remaining_time=65,  # 1h 5m
        bed_temp=60.0,
        nozzle_temp=220.0,
    )

    text = status.display_text()
    assert "50%" in text
    assert "150/300" in text
    assert "1h" in text


# --- AmsTray tests ---


def test_ams_tray_global_index():
    """Test global index calculation for AMS trays."""
    tray = AmsTray(ams_id=0, tray_id=2, color="FF0000FF", material="PLA")
    assert tray.global_index == 2

    tray2 = AmsTray(ams_id=1, tray_id=3, color="00FF00FF", material="PETG")
    assert tray2.global_index == 7  # 1*4 + 3


def test_ams_tray_color_emoji():
    """Test color emoji mapping for common filament colors."""
    assert AmsTray(0, 0, "FF0000FF", "PLA").color_emoji == "\U0001f534"  # red
    assert AmsTray(0, 0, "00FF00FF", "PLA").color_emoji == "\U0001f7e2"  # green
    assert AmsTray(0, 0, "057748FF", "PLA").color_emoji == "\U0001f7e2"  # dark green
    assert AmsTray(0, 0, "0000FFFF", "PLA").color_emoji == "\U0001f535"  # blue
    assert AmsTray(0, 0, "FFFFFFFF", "PLA").color_emoji == "\u26aa"     # white
    assert AmsTray(0, 0, "161616FF", "PLA").color_emoji == "\u26ab"     # black
    assert AmsTray(0, 0, "0EE2A0FF", "PLA").color_emoji == "\U0001f7e2"  # mint → green


def test_ams_tray_display_text():
    """Test display text formatting."""
    tray = AmsTray(ams_id=0, tray_id=1, color="FF0000FF", material="PLA")
    text = tray.display_text()
    assert "PLA" in text
    assert "Slot 1-2" in text  # 1-based: ams 0 → 1, tray 1 → 2


def test_parse_ams_real_data():
    """Test AMS parsing with real printer data."""
    ams_data = {
        "ams": [
            {
                "id": "0",
                "humidity": "1",
                "temp": "27.9",
                "tray": [
                    {"id": "0", "tray_color": "FFFFFFFF", "tray_type": "PLA"},
                    {"id": "1", "tray_color": "0EE2A0FF", "tray_type": "PLA"},
                    {"id": "2", "tray_color": "057748FF", "tray_type": "PLA"},
                    {"id": "3", "tray_color": "161616FF", "tray_type": "PETG"},
                ],
            }
        ],
    }

    trays = X1CClient._parse_ams(ams_data)
    assert len(trays) == 4
    assert trays[0].material == "PLA"
    assert trays[0].color == "FFFFFFFF"
    assert trays[0].global_index == 0
    assert trays[3].material == "PETG"
    assert trays[3].global_index == 3


def test_parse_ams_empty_trays():
    """Test that empty trays are skipped."""
    ams_data = {
        "ams": [
            {
                "id": "0",
                "tray": [
                    {"id": "0", "tray_color": "FF0000FF", "tray_type": "PLA"},
                    {"id": "1", "tray_color": "", "tray_type": ""},
                    {"id": "2", "tray_color": "00FF00FF", "tray_type": "PETG"},
                ],
            }
        ],
    }

    trays = X1CClient._parse_ams(ams_data)
    assert len(trays) == 2
    assert trays[0].tray_id == 0
    assert trays[1].tray_id == 2


def test_parse_ams_no_data():
    """Test parsing with no AMS units."""
    assert X1CClient._parse_ams({}) == []
    assert X1CClient._parse_ams({"ams": []}) == []


def test_stale_failed_state_detected_as_idle():
    """Test that stale FAILED state with no active stage is treated as IDLE."""
    data = {
        "print": {
            "gcode_state": "FAILED",
            "fail_reason": 50348044,
            "stg_cur": -1,
            "bed_target_temper": 0.0,
            "nozzle_target_temper": 0.0,
            "mc_percent": 0,
            "layer_num": 0,
            "total_layer_num": 0,
            "mc_remaining_time": 0,
            "bed_temper": 25.0,
            "nozzle_temper": 29.0,
        }
    }
    status = PrinterStatus.from_mqtt(data)
    assert status.state == "IDLE"

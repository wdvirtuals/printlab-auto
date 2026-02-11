"""Bambu Lab X1C printer client using MQTT."""

from __future__ import annotations

import ftplib
import json
import socket
import ssl
import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Callable

import paho.mqtt.client as mqtt
import httpx

from ..slicer import Slicer, SlicerNotFoundError


class _ImplicitFTPS(ftplib.FTP_TLS):
    """Implicit FTPS (port 990) with TLS session reuse.

    Bambu Lab printers use vsftpd which requires:
    1. Implicit TLS (SSL on connect, not AUTH TLS upgrade)
    2. TLS session reuse for data connections
    """

    def connect(self, host, port=990, timeout=120):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock = socket.create_connection((host, port), timeout)
        self.af = self.sock.family
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        self.sock = ctx.wrap_socket(self.sock, server_hostname=host)
        self.file = self.sock.makefile("r", encoding=self.encoding)
        self.welcome = self.getresp()
        return self.welcome

    def ntransfercmd(self, cmd, rest=None):
        # Reuse the control connection's TLS session for data connections
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.sock.context.wrap_socket(
                conn, server_hostname=self.host,
                session=self.sock.session,
            )
        # Ensure data socket has generous timeout for large file transfers
        conn.settimeout(300)
        return conn, size


@dataclass
class AmsTray:
    """A single AMS tray (spool slot)."""

    ams_id: int   # AMS unit index (0 for most setups)
    tray_id: int  # Tray index within the unit (0-3)
    color: str    # Hex RGBA, e.g. "FF0000FF"
    material: str  # "PLA", "PETG", "ABS", etc.

    @property
    def global_index(self) -> int:
        """Global tray index for ams_mapping (unit * 4 + slot)."""
        return self.ams_id * 4 + self.tray_id

    @property
    def color_hex(self) -> str:
        """RGB portion of the color (strip alpha)."""
        return self.color[:6] if len(self.color) >= 6 else self.color

    @property
    def color_emoji(self) -> str:
        """Approximate emoji circle for this color."""
        if len(self.color) < 6:
            return "\u26aa"
        r = int(self.color[0:2], 16)
        g = int(self.color[2:4], 16)
        b = int(self.color[4:6], 16)
        brightness = (r + g + b) / 3
        if brightness > 220:
            return "\u26aa"       # white
        if brightness < 35:
            return "\u26ab"       # black
        if r > 180 and g < 80 and b < 80:
            return "\U0001f534"   # red
        if g > r and g > b:
            return "\U0001f7e2"   # green (dominant green channel)
        if b > r and b > g:
            return "\U0001f535"   # blue (dominant blue channel)
        if r > 200 and g > 200 and b < 80:
            return "\U0001f7e1"   # yellow
        if r > 200 and g > 100 and b < 80:
            return "\U0001f7e0"   # orange
        if r > 100 and b > 100 and g < 80:
            return "\U0001f7e3"   # purple
        if r > 100 and g > 50 and b < 50:
            return "\U0001f7e4"   # brown
        return "\u26aa"

    def display_text(self) -> str:
        """Format for Telegram display."""
        return f"{self.color_emoji} {self.material} (Slot {self.ams_id + 1}-{self.tray_id + 1})"


@dataclass
class PrinterStatus:
    """Current printer status."""

    state: str  # IDLE, PRINTING, PAUSED, ERROR
    progress: int  # 0-100
    current_layer: int
    total_layers: int
    remaining_time: int  # minutes
    bed_temp: float
    nozzle_temp: float
    error_message: Optional[str] = None

    @classmethod
    def from_mqtt(cls, data: dict) -> "PrinterStatus":
        """Parse status from MQTT message."""
        print_info = data.get("print", {})

        # Determine state
        gcode_state = str(print_info.get("gcode_state", "IDLE"))
        state_map = {
            "IDLE": "IDLE",
            "PREPARE": "PREPARING",
            "RUNNING": "PRINTING",
            "PAUSE": "PAUSED",
            "FINISH": "FINISHED",
            "FINISHED": "FINISHED",
            "FAILED": "ERROR",
        }
        state = state_map.get(gcode_state.upper(), "IDLE")

        # Detect stale FAILED state: printer is physically idle but MQTT
        # still reports FAILED from a previous print. Signs of stale state:
        # stg_cur=-1 (no active stage) and no heating targets.
        if state == "ERROR":
            stg_cur = print_info.get("stg_cur", 0)
            bed_target = float(print_info.get("bed_target_temper", 0) or 0)
            nozzle_target = float(print_info.get("nozzle_target_temper", 0) or 0)
            if stg_cur == -1 and bed_target == 0 and nozzle_target == 0:
                state = "IDLE"

        # Handle numeric error codes in fail_reason
        fail_reason = print_info.get("fail_reason")
        if fail_reason and isinstance(fail_reason, int):
            fail_reason = f"Error code: {fail_reason}"

        return cls(
            state=state,
            progress=int(print_info.get("mc_percent", 0) or 0),
            current_layer=int(print_info.get("layer_num", 0) or 0),
            total_layers=int(print_info.get("total_layer_num", 0) or 0),
            remaining_time=int(print_info.get("mc_remaining_time", 0) or 0),
            bed_temp=float(print_info.get("bed_temper", 0) or 0),
            nozzle_temp=float(print_info.get("nozzle_temper", 0) or 0),
            error_message=fail_reason,
        )

    def display_text(self) -> str:
        """Format status for display."""
        temps = f"🛏️ Bed: {self.bed_temp:.0f}°C | 🔥 Nozzle: {self.nozzle_temp:.0f}°C"

        if self.state == "IDLE":
            return f"🟢 Printer idle\n{temps}"

        if self.state == "FINISHED":
            return f"✅ Print finished\n{temps}"

        if self.state == "PREPARING":
            return f"⏳ Preparing...\n{temps}"

        if self.state == "PAUSED":
            return f"⏸️ Paused at {self.progress}%\n{temps}"

        if self.state == "PRINTING":
            hours, mins = divmod(self.remaining_time, 60)
            time_str = f"{hours}h {mins}m" if hours else f"{mins}m"
            return (
                f"🖨️ Printing: {self.progress}%\n"
                f"📄 Layer {self.current_layer}/{self.total_layers}\n"
                f"⏱️ {time_str} remaining\n"
                f"{temps}"
            )

        if self.state == "ERROR":
            return (
                f"🔴 Previous print failed ({self.error_message or 'Unknown error'})\n"
                f"{temps}\n\n"
                f"Clear the error on the printer touchscreen, then try /status again."
            )

        return f"📊 Status: {self.state}\n{temps}"


class X1CClient:
    """MQTT client for Bambu Lab X1C printer."""

    DOWNLOAD_DIR = Path.home() / ".printlab" / "downloads"

    def __init__(self, ip: str, access_code: str, serial: str, bed_type: str = "auto"):
        self.ip = ip
        self.access_code = access_code
        self.serial = serial
        self.port = 8883
        self.bed_type = bed_type

        self._client: Optional[mqtt.Client] = None
        self._connected = False
        self._status: Optional[PrinterStatus] = None
        self._ams_trays: list[AmsTray] = []
        self._nozzle_type: Optional[str] = None
        self._status_callbacks: list[Callable[[PrinterStatus], None]] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # Ensure download directory exists
        self.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

        # Initialize slicer for STL conversion
        self.slicer = Slicer(bed_type=bed_type)

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        """Handle MQTT connection."""
        if rc == 0:
            self._connected = True
            # Subscribe to printer status
            topic = f"device/{self.serial}/report"
            client.subscribe(topic)
        else:
            self._connected = False

    def _on_disconnect(self, client, userdata, flags, rc, properties=None):
        """Handle MQTT disconnection."""
        self._connected = False

    def _on_message(self, client, userdata, msg):
        """Handle incoming MQTT messages."""
        try:
            data = json.loads(msg.payload.decode())
            if "print" in data:
                self._status = PrinterStatus.from_mqtt(data)
                # Capture nozzle type from printer
                reported_nozzle = data["print"].get("nozzle_type")
                if reported_nozzle:
                    self._nozzle_type = reported_nozzle
                # Parse AMS data if present
                ams_data = data["print"].get("ams")
                if ams_data and "ams" in ams_data:
                    self._ams_trays = self._parse_ams(ams_data)
                # Notify callbacks
                for callback in self._status_callbacks:
                    if self._loop:
                        self._loop.call_soon_threadsafe(callback, self._status)
        except Exception as e:
            print(f"Error parsing MQTT message: {e}")

    @staticmethod
    def _parse_ams(ams_data: dict) -> list[AmsTray]:
        """Parse AMS tray data from MQTT message."""
        trays = []
        for unit in ams_data.get("ams", []):
            ams_id = int(unit.get("id", 0))
            for tray_data in unit.get("tray", []):
                tray_color = tray_data.get("tray_color", "")
                tray_type = tray_data.get("tray_type", "")
                if not tray_color or not tray_type:
                    continue
                trays.append(AmsTray(
                    ams_id=ams_id,
                    tray_id=int(tray_data.get("id", 0)),
                    color=tray_color,
                    material=tray_type,
                ))
        return trays

    async def get_ams_trays(self) -> list[AmsTray]:
        """Get available AMS trays. Triggers a status refresh if needed."""
        if not self._ams_trays:
            await self.get_status()
        return self._ams_trays

    async def connect(self) -> bool:
        """Connect to the printer."""
        self._loop = asyncio.get_event_loop()

        # Clean up any previous client before reconnecting
        if self._client is not None:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                pass
            self._client = None
            self._connected = False

        # Create MQTT client
        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"printlab_{self.serial}",
            protocol=mqtt.MQTTv311,
        )

        # Set credentials
        self._client.username_pw_set("bblp", self.access_code)

        # Configure TLS (X1C uses self-signed certs)
        self._client.tls_set(cert_reqs=ssl.CERT_NONE)
        self._client.tls_insecure_set(True)

        # Set callbacks
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

        # Connect
        try:
            self._client.connect(self.ip, self.port)
            self._client.loop_start()

            # Wait for connection
            for _ in range(50):  # 5 second timeout
                if self._connected:
                    return True
                await asyncio.sleep(0.1)

            return False

        except Exception as e:
            print(f"MQTT connection error: {e}")
            return False

    async def disconnect(self):
        """Disconnect from the printer."""
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
            self._connected = False

    def on_status_update(self, callback: Callable[[PrinterStatus], None]):
        """Register a callback for status updates."""
        self._status_callbacks.append(callback)

    async def get_status(self) -> Optional[PrinterStatus]:
        """Get current printer status."""
        if not self._connected:
            print("MQTT not connected, attempting reconnect...")
            if not await self.connect():
                return None

        # Clear stale status so we know when a fresh one arrives
        prev_status = self._status
        self._status = None

        # Request fresh status
        self._publish_command({"pushing": {"command": "pushall"}})

        # Wait up to 5 seconds for a print status response
        for _ in range(50):
            if self._status is not None:
                return self._status
            await asyncio.sleep(0.1)

        # If no fresh status arrived, restore previous (better than None)
        if self._status is None:
            self._status = prev_status
        return self._status

    def _publish_command(self, command: dict):
        """Publish a command to the printer."""
        if self._client and self._connected:
            topic = f"device/{self.serial}/request"
            payload = json.dumps(command)
            self._client.publish(topic, payload)

    async def send_print_job(self, file_path: Path, ams_mapping: list[int] | None = None) -> bool | str:
        """Send a print job to the printer.

        Accepts .3mf files directly, or .stl files which will be sliced first.

        Args:
            file_path: Path to the .3mf or .stl file

        Returns:
            True if job was sent successfully, False on error,
            "no_slicer" if STL needs slicing but no slicer available
        """
        if not file_path.exists():
            print(f"File not found: {file_path}")
            return False

        # Handle STL files - need to slice first
        if file_path.suffix.lower() == ".stl":
            if not self.slicer.is_available:
                print("No slicer available for STL file")
                return "no_slicer"

            if not self.slicer.supports_bambu:
                print(f"Slicer {self.slicer.slicer_type} does not support Bambu printers")
                return "wrong_slicer"

            if not self.slicer.has_template:
                print("No slicing template found")
                return "no_template"

            print(f"Slicing STL file: {file_path}")
            sliced_path = await self.slicer.slice_stl(file_path)
            if not sliced_path:
                print("Slicing failed")
                return "slice_failed"
            file_path = sliced_path

        if not file_path.suffix.lower() == ".3mf":
            print(f"Unsupported file type: {file_path.suffix}")
            return False

        try:
            # Upload file via implicit FTPS (port 990, TLS on connect)
            ftp = _ImplicitFTPS()
            ftp.connect(self.ip, 990)
            ftp.login("bblp", self.access_code)
            ftp.prot_p()

            # Upload to cache folder
            remote_path = f"/cache/{file_path.name}"
            with open(file_path, "rb") as f:
                ftp.storbinary(f"STOR {remote_path}", f)

            try:
                ftp.quit()
            except EOFError:
                pass  # Printer may close connection abruptly

        except Exception as e:
            print(f"FTPS upload error: {e}")
            if "553" in str(e):
                return "storage_error"
            return "upload_failed"

        try:
            # Start the print — all fields required by newer firmware
            self._sequence_id = getattr(self, "_sequence_id", 0) + 1
            self._publish_command(
                {
                    "print": {
                        "sequence_id": str(self._sequence_id),
                        "command": "project_file",
                        "param": "Metadata/plate_1.gcode",
                        "project_id": "0",
                        "profile_id": "0",
                        "task_id": "0",
                        "subtask_id": "0",
                        "subtask_name": file_path.stem,
                        "file": "",
                        "url": f"ftp:///cache/{file_path.name}",
                        "md5": "",
                        "timelapse": False,
                        "bed_type": self.bed_type,
                        "bed_levelling": True,
                        "flow_cali": True,
                        "vibration_cali": True,
                        "layer_inspect": False,
                        "use_ams": True,
                        "ams_mapping": ams_mapping if ams_mapping is not None else [0],
                    }
                }
            )

            # Auto-resume if printer pauses for firmware warnings (e.g. nozzle
            # type mismatch after firmware updates).  Wait a few seconds for the
            # print to start, then check if it landed in PAUSE and send resume.
            for _ in range(30):  # up to 6 seconds
                await asyncio.sleep(0.2)
                if self._status and self._status.state == "PRINTING":
                    break
            if self._status and self._status.state == "PAUSED":
                print("Auto-resuming paused print (firmware warning dismissed)")
                await asyncio.sleep(2)
                self._sequence_id += 1
                self._publish_command(
                    {
                        "print": {
                            "sequence_id": str(self._sequence_id),
                            "command": "resume",
                            "param": "",
                        }
                    }
                )

            return True

        except Exception as e:
            print(f"Error starting print: {e}")
            return False

    async def download_model(self, url: str, filename: str) -> Optional[Path]:
        """Download a model file from a URL.

        Args:
            url: URL to download from
            filename: Name to save the file as

        Returns:
            Path to downloaded file or None on failure
        """
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(url, follow_redirects=True)
                response.raise_for_status()

                # Ensure proper extension
                if not filename.endswith((".3mf", ".stl")):
                    content_type = response.headers.get("content-type", "")
                    if "3mf" in content_type or url.endswith(".3mf"):
                        filename += ".3mf"
                    else:
                        filename += ".stl"

                file_path = self.DOWNLOAD_DIR / filename
                file_path.write_bytes(response.content)

                return file_path

        except Exception as e:
            print(f"Error downloading model: {e}")
            return None

    async def pause(self):
        """Pause the current print."""
        self._publish_command({"print": {"command": "pause"}})

    async def resume(self):
        """Resume a paused print."""
        self._publish_command({"print": {"command": "resume"}})

    async def stop(self):
        """Stop/cancel the current print."""
        self._publish_command({"print": {"command": "stop"}})

    @property
    def is_connected(self) -> bool:
        """Check if connected to printer."""
        return self._connected

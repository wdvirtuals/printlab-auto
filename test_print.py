#!/usr/bin/env python3
"""Test print job submission to Bambu Lab X1C.

Usage:
    python3 test_print.py                    # Upload + print test file
    python3 test_print.py upload             # Upload only (no print)
    python3 test_print.py print              # Print already-uploaded file
    python3 test_print.py /path/to/file.3mf  # Use specific file
"""

import ftplib
import json
import os
import socket
import ssl
import sys
import time

from dotenv import load_dotenv

load_dotenv()

IP = os.getenv("X1C_IP", "")
CODE = os.getenv("X1C_ACCESS_CODE", "")
SERIAL = os.getenv("X1C_SERIAL", "")

# Default test file
DEFAULT_FILE = os.path.expanduser(
    "~/.printlab/downloads/Razor_blade_holder_scraper_handle.3mf"
)


class ImplicitFTPS(ftplib.FTP_TLS):
    """Implicit FTPS with TLS session reuse for Bambu printers."""

    def connect(self, host, port=990, timeout=30):
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
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.sock.context.wrap_socket(
                conn, server_hostname=self.host,
                session=self.sock.session,
            )
        return conn, size


def upload_file(local_path: str) -> str:
    """Upload file to printer via FTPS. Returns remote filename."""
    filename = os.path.basename(local_path)
    # Sanitize filename — remove special chars that might cause issues
    safe_name = filename.replace("#", "").replace(" ", "_")

    print(f"\n📤 Uploading {safe_name} to printer...")
    ftp = ImplicitFTPS()
    ftp.connect(IP, 990)
    ftp.login("bblp", CODE)
    ftp.prot_p()

    remote_path = f"/cache/{safe_name}"
    with open(local_path, "rb") as f:
        ftp.storbinary(f"STOR {remote_path}", f)

    # Verify upload
    files = ftp.nlst("/cache/")
    if f"/cache/{safe_name}" in files:
        print(f"   ✅ Uploaded: /cache/{safe_name}")
    else:
        print(f"   ⚠️  File not found in listing, but upload didn't error")

    try:
        ftp.quit()
    except EOFError:
        pass

    return safe_name


def send_print_command(filename: str, url_format: str):
    """Send MQTT print command with given URL format."""
    import paho.mqtt.client as mqtt

    url = url_format.format(filename=filename)
    print(f"\n🖨️  Sending print command with url: {url}")

    connected = {"done": False}
    response_data = {"received": False, "data": None}

    def on_connect(client, userdata, flags, rc, properties=None):
        connected["done"] = True
        client.subscribe(f"device/{SERIAL}/report")

        seq_id = str(int(time.time()) % 10000)
        cmd = {
            "print": {
                "sequence_id": seq_id,
                "command": "project_file",
                "param": "Metadata/plate_1.gcode",
                "project_id": "0",
                "profile_id": "0",
                "task_id": "0",
                "subtask_id": "0",
                "subtask_name": filename.replace(".3mf", ""),
                "file": "",
                "url": url,
                "md5": "",
                "timelapse": False,
                "bed_type": "auto",
                "bed_levelling": True,
                "flow_cali": True,
                "vibration_cali": True,
                "layer_inspect": False,
                "use_ams": True,
                "ams_mapping": "",
            }
        }
        payload = json.dumps(cmd)
        client.publish(f"device/{SERIAL}/request", payload)
        print(f"   Command sent (seq={seq_id})")

    def on_message(client, userdata, msg):
        data = json.loads(msg.payload.decode())
        if "print" in data:
            p = data["print"]
            state = p.get("gcode_state", "")
            if state:
                print(f"   Printer state: {state}")
                if state == "PREPARE":
                    print("   ✅ Printer is preparing the print!")
                    response_data["received"] = True
                elif state == "RUNNING":
                    print("   ✅ Printer is printing!")
                    response_data["received"] = True
                elif state == "FAILED":
                    fail = p.get("fail_reason", "?")
                    print(f"   ❌ Print failed: {fail}")
                    response_data["received"] = True
            # Check for command response
            if "command" in p and p["command"] != "push_status":
                print(f"   Response: {json.dumps(p, indent=2)[:500]}")
                response_data["received"] = True

    c = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"test_print_{int(time.time())}",
        protocol=mqtt.MQTTv311,
    )
    c.username_pw_set("bblp", CODE)
    c.tls_set(cert_reqs=ssl.CERT_NONE)
    c.tls_insecure_set(True)
    c.on_connect = on_connect
    c.on_message = on_message
    c.connect(IP, 8883)
    c.loop_start()

    # Wait for response
    for _ in range(100):  # 10 seconds
        if response_data["received"]:
            break
        time.sleep(0.1)

    if not response_data["received"]:
        print("   ⏳ No state change detected in 10 seconds")

    c.loop_stop()
    c.disconnect()


def main():
    args = sys.argv[1:]

    # Determine mode and file
    mode = "both"  # upload + print
    file_path = DEFAULT_FILE

    for arg in args:
        if arg == "upload":
            mode = "upload"
        elif arg == "print":
            mode = "print"
        elif os.path.exists(arg):
            file_path = arg

    if not os.path.exists(file_path) and mode != "print":
        print(f"❌ File not found: {file_path}")
        print(f"Available .3mf files:")
        import glob
        for f in glob.glob(os.path.expanduser("~/.printlab/downloads/*.3mf")):
            print(f"  {f}")
        sys.exit(1)

    print("🔧 PrintLab Print Job Tester")
    print(f"   Printer: {IP}")
    print(f"   File:    {os.path.basename(file_path)}")
    print(f"   Mode:    {mode}")

    if mode in ("both", "upload"):
        filename = upload_file(file_path)
    else:
        filename = os.path.basename(file_path)
        # Sanitize same as upload
        filename = filename.replace("#", "").replace(" ", "_")

    if mode in ("both", "print"):
        # Try different URL formats to find one the printer accepts
        url_formats = [
            ("ftp:///cache/{filename}", "ftp:// scheme"),
            ("file:///cache/{filename}", "file:// scheme"),
            ("/cache/{filename}", "bare path"),
            ("file:///sdcard/cache/{filename}", "file:// with /sdcard"),
        ]

        if mode == "both":
            # Try just the first format (most likely to work based on community)
            fmt, desc = url_formats[0]
            print(f"\n--- Trying: {desc} ---")
            send_print_command(filename, fmt)
        else:
            # In print-only mode, try all formats
            for fmt, desc in url_formats:
                print(f"\n--- Trying: {desc} ---")
                send_print_command(filename, fmt)
                time.sleep(3)

    print("\n✅ Done")


if __name__ == "__main__":
    main()

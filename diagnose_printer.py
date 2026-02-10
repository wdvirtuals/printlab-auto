#!/usr/bin/env python3
"""Diagnose Bambu Lab X1C printer connectivity."""

import os
import socket
import ssl
import subprocess
import sys
import time

from dotenv import load_dotenv

load_dotenv()

IP = os.getenv("X1C_IP", "")
ACCESS_CODE = os.getenv("X1C_ACCESS_CODE", "")
SERIAL = os.getenv("X1C_SERIAL", "")
MQTT_PORT = 8883
FTPS_PORT = 990


def header(msg):
    print(f"\n{'='*50}")
    print(f"  {msg}")
    print(f"{'='*50}")


def test_ping():
    """Test basic network reachability."""
    header("1. PING TEST")
    print(f"   Target: {IP}")
    try:
        result = subprocess.run(
            ["ping", "-c", "3", "-W", "2", IP],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            # Extract round-trip time
            for line in result.stdout.splitlines():
                if "avg" in line or "round-trip" in line:
                    print(f"   {line.strip()}")
            print("   ✅ Printer is reachable on the network")
            return True
        else:
            print("   ❌ FAILED — Printer not reachable")
            print("   → Check that the printer is powered on")
            print(f"   → Verify the IP is correct (yours: {IP})")
            print("   → Ensure you're on the same WiFi network")
            return False
    except subprocess.TimeoutExpired:
        print("   ❌ FAILED — Ping timed out")
        return False
    except Exception as e:
        print(f"   ❌ Error: {e}")
        return False


def test_tcp_port(port, name):
    """Test if a TCP port is open."""
    print(f"\n   Port {port} ({name})...", end=" ")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    try:
        result = sock.connect_ex((IP, port))
        if result == 0:
            print("✅ OPEN")
            return True
        else:
            print(f"❌ CLOSED (errno {result})")
            return False
    except socket.timeout:
        print("❌ TIMEOUT")
        return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False
    finally:
        sock.close()


def test_ports():
    """Test MQTT and FTPS ports."""
    header("2. PORT SCAN")
    mqtt_ok = test_tcp_port(MQTT_PORT, "MQTT/TLS")
    ftps_ok = test_tcp_port(FTPS_PORT, "FTPS")

    if not mqtt_ok:
        print("\n   → Port 8883 closed. Possible causes:")
        print("     • LAN Only Mode is not enabled on the printer")
        print("     • Firewall blocking the connection")
        print("     • Wrong IP address")
    if not ftps_ok:
        print("\n   → Port 990 closed (needed for file upload)")

    return mqtt_ok


def test_tls():
    """Test TLS handshake on MQTT port."""
    header("3. TLS HANDSHAKE")
    print(f"   Connecting to {IP}:{MQTT_PORT} with TLS...")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        sock = socket.create_connection((IP, MQTT_PORT), timeout=5)
        tls_sock = ctx.wrap_socket(sock, server_hostname=IP)
        cipher = tls_sock.cipher()
        print(f"   Cipher: {cipher[0]}")
        print(f"   TLS version: {cipher[1]}")
        tls_sock.close()
        print("   ✅ TLS handshake successful")
        return True
    except ssl.SSLError as e:
        print(f"   ❌ TLS Error: {e}")
        return False
    except socket.timeout:
        print("   ❌ Connection timed out")
        return False
    except Exception as e:
        print(f"   ❌ Error: {e}")
        return False


def test_mqtt():
    """Test MQTT authentication."""
    header("4. MQTT AUTHENTICATION")
    print(f"   User: bblp")
    print(f"   Access code: {ACCESS_CODE[:4]}****")
    print(f"   Serial: {SERIAL}")

    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        print("   ⚠️  paho-mqtt not installed — skipping MQTT test")
        print("   Run: pip install paho-mqtt")
        return None

    connected_event = {"result": None, "done": False}

    def on_connect(client, userdata, flags, rc, properties=None):
        connected_event["result"] = rc
        connected_event["done"] = True

    def on_disconnect(client, userdata, flags, rc, properties=None):
        if not connected_event["done"]:
            connected_event["result"] = rc
            connected_event["done"] = True

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"printlab_diag_{int(time.time())}",
        protocol=mqtt.MQTTv311,
    )
    client.username_pw_set("bblp", ACCESS_CODE)
    client.tls_set(cert_reqs=ssl.CERT_NONE)
    client.tls_insecure_set(True)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect

    try:
        client.connect(IP, MQTT_PORT)
        client.loop_start()

        # Wait up to 5 seconds
        for _ in range(50):
            if connected_event["done"]:
                break
            time.sleep(0.1)

        client.loop_stop()
        client.disconnect()

        rc = connected_event["result"]
        if rc is None:
            print("   ❌ No response from MQTT broker (timed out)")
            print("   → Printer may not have MQTT enabled")
            print("   → Enable LAN Only Mode: Settings > WLAN on touchscreen")
            return False

        # paho-mqtt v2 returns ReasonCode objects
        rc_str = str(rc)
        is_success = rc_str == "Success" or rc == 0

        if is_success:
            print("   ✅ Connected successfully!")
            return True

        print(f"   ❌ Connection refused: {rc_str}")
        if "auth" in rc_str.lower() or "password" in rc_str.lower() or "not" in rc_str.lower():
            print("   → Check access code on printer: Settings > General > Access Code")
        return False

    except ConnectionRefusedError:
        print("   ❌ Connection refused")
        print("   → MQTT service not running or LAN access disabled")
        return False
    except socket.timeout:
        print("   ❌ Connection timed out")
        return False
    except Exception as e:
        print(f"   ❌ Error: {e}")
        return False


def test_mqtt_status():
    """Try to get a status push from the printer."""
    header("5. MQTT STATUS REQUEST")
    print("   Subscribing and requesting status push...")

    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        print("   ⚠️  Skipped (paho-mqtt not installed)")
        return None

    import json

    status_data = {"received": False, "payload": None}

    def on_connect(client, userdata, flags, rc, properties=None):
        if rc == 0:
            topic = f"device/{SERIAL}/report"
            client.subscribe(topic)
            # Request status push
            req_topic = f"device/{SERIAL}/request"
            client.publish(req_topic, json.dumps({"pushing": {"command": "pushall"}}))

    def on_message(client, userdata, msg):
        status_data["received"] = True
        try:
            status_data["payload"] = json.loads(msg.payload.decode())
        except Exception:
            status_data["payload"] = msg.payload[:200]

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"printlab_diag_status_{int(time.time())}",
        protocol=mqtt.MQTTv311,
    )
    client.username_pw_set("bblp", ACCESS_CODE)
    client.tls_set(cert_reqs=ssl.CERT_NONE)
    client.tls_insecure_set(True)
    client.on_connect = on_connect
    client.on_message = on_message

    try:
        client.connect(IP, MQTT_PORT)
        client.loop_start()

        # Wait up to 5 seconds for a status message
        for _ in range(50):
            if status_data["received"]:
                break
            time.sleep(0.1)

        client.loop_stop()
        client.disconnect()

        if status_data["received"]:
            payload = status_data["payload"]
            if isinstance(payload, dict) and "print" in payload:
                p = payload["print"]
                state = p.get("gcode_state", "?")
                bed = p.get("bed_temper", "?")
                nozzle = p.get("nozzle_temper", "?")
                print(f"   State: {state}")
                print(f"   Bed: {bed}°C | Nozzle: {nozzle}°C")
            else:
                print(f"   Received data (keys: {list(payload.keys()) if isinstance(payload, dict) else '?'})")
            print("   ✅ Printer is responding with status data!")
            return True
        else:
            print("   ❌ No status response within 5 seconds")
            print("   → Connected OK, but printer didn't reply to pushall")
            return False

    except Exception as e:
        print(f"   ❌ Error: {e}")
        return False


def main():
    print("🔧 PrintLab Printer Diagnostic")
    print(f"   Printer IP:     {IP}")
    print(f"   Serial:         {SERIAL}")
    print(f"   Access Code:    {ACCESS_CODE[:4]}****")

    if not IP:
        print("\n❌ X1C_IP not set in .env file!")
        sys.exit(1)

    # Run tests in sequence — each depends on the previous
    if not test_ping():
        print("\n" + "="*50)
        print("  DIAGNOSIS: Network unreachable")
        print("="*50)
        print("  Fix: Check printer IP and WiFi network")
        return

    if not test_ports():
        print("\n" + "="*50)
        print("  DIAGNOSIS: MQTT port closed")
        print("="*50)
        print("  Fix: Enable LAN Only Mode on the printer")
        print("       Settings > WLAN > LAN Only Mode")
        return

    if not test_tls():
        print("\n" + "="*50)
        print("  DIAGNOSIS: TLS handshake failed")
        print("="*50)
        print("  Fix: This is unusual — try restarting the printer")
        return

    mqtt_ok = test_mqtt()
    if mqtt_ok is False:
        print("\n" + "="*50)
        print("  DIAGNOSIS: MQTT auth failed")
        print("="*50)
        print("  Fix: Check access code on printer touchscreen")
        print("       Settings > General > Access Code")
        return
    elif mqtt_ok is None:
        return

    # If MQTT connected, try getting status
    test_mqtt_status()

    print("\n" + "="*50)
    print("  ALL TESTS PASSED")
    print("="*50)
    print("  Your printer connection should work with PrintLab!")


if __name__ == "__main__":
    main()

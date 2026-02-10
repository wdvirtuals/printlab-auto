"""Bridge to libbambu_networking.dylib for reading auth tokens.

Auto-compiles a thin C++ bridge on first use, then loads it via ctypes
to decrypt BambuStudio/OrcaSlicer config and extract the JWT token.
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

# Paths to check for the Bambu networking dylib and config
_APP_CONFIGS = [
    {
        "name": "BambuStudio",
        "config_dir": Path.home() / "Library" / "Application Support" / "BambuStudio",
        "dylib": Path.home() / "Library" / "Application Support" / "BambuStudio" / "plugins" / "libbambu_networking.dylib",
        "cert_dir": Path("/Applications/BambuStudio.app/Contents/Resources/cert"),
    },
    {
        "name": "OrcaSlicer",
        "config_dir": Path.home() / "Library" / "Application Support" / "OrcaSlicer",
        "dylib": Path.home() / "Library" / "Application Support" / "OrcaSlicer" / "plugins" / "libbambu_networking.dylib",
        "cert_dir": Path("/Applications/OrcaSlicer.app/Contents/Resources/cert"),
    },
]

_BRIDGE_DIR = Path(__file__).parent
_BRIDGE_CPP = _BRIDGE_DIR / "bambu_bridge.cpp"
_BRIDGE_DYLIB = _BRIDGE_DIR / "bambu_bridge.dylib"


def _compile_bridge() -> bool:
    """Compile the C++ bridge if not already built or if source is newer."""
    if not _BRIDGE_CPP.exists():
        return False

    # Skip if already compiled and up-to-date
    if _BRIDGE_DYLIB.exists():
        if _BRIDGE_DYLIB.stat().st_mtime >= _BRIDGE_CPP.stat().st_mtime:
            return True

    try:
        result = subprocess.run(
            [
                "clang++",
                "-shared",
                "-std=c++17",
                "-O2",
                "-o", str(_BRIDGE_DYLIB),
                str(_BRIDGE_CPP),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            print(f"Bridge compile error: {result.stderr}")
            return False
        return True
    except FileNotFoundError:
        print("Bridge: clang++ not found. Install Xcode Command Line Tools: xcode-select --install")
        return False
    except Exception as e:
        print(f"Bridge: compile failed: {e}")
        return False


def _find_app_config() -> Optional[dict]:
    """Find the first available BambuStudio/OrcaSlicer installation with a valid config."""
    for app in _APP_CONFIGS:
        conf_file = app["config_dir"] / "BambuNetworkEngine.conf"
        if app["dylib"].exists() and conf_file.exists():
            return app
    return None


def extract_token() -> Optional[str]:
    """Extract the Bambu Lab auth token from a local BambuStudio/OrcaSlicer installation.

    Returns the JWT token string, or None if extraction fails.
    """
    app = _find_app_config()
    if not app:
        return None

    if not _compile_bridge():
        return None

    bridge = None
    try:
        bridge = ctypes.CDLL(str(_BRIDGE_DYLIB))

        # Set up function signatures
        bridge.bridge_load.argtypes = [ctypes.c_char_p]
        bridge.bridge_load.restype = ctypes.c_int

        bridge.bridge_init.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        bridge.bridge_init.restype = ctypes.c_int

        bridge.bridge_is_user_login.argtypes = []
        bridge.bridge_is_user_login.restype = ctypes.c_int

        bridge.bridge_build_login_info.argtypes = []
        bridge.bridge_build_login_info.restype = ctypes.c_char_p

        bridge.bridge_cleanup.argtypes = []
        bridge.bridge_cleanup.restype = None

        # Load the Bambu networking dylib
        rc = bridge.bridge_load(str(app["dylib"]).encode())
        if rc != 0:
            print(f"Bridge: failed to load {app['dylib']}")
            return None

        # Initialize with config and cert paths
        rc = bridge.bridge_init(
            str(app["config_dir"]).encode(),
            str(app["cert_dir"]).encode(),
        )
        if rc != 0:
            print(f"Bridge: failed to initialize agent for {app['name']}")
            return None

        # Check if user is logged in
        login_status = bridge.bridge_is_user_login()
        if login_status != 1:
            print(f"Bridge: not logged in to {app['name']}")
            return None

        # Get login info JSON
        info_ptr = bridge.bridge_build_login_info()
        if not info_ptr:
            print(f"Bridge: failed to get login info from {app['name']}")
            return None

        info_str = info_ptr.decode("utf-8")

        # Parse the JSON to extract the token
        try:
            info = json.loads(info_str)
        except json.JSONDecodeError:
            print("Bridge: login info is not valid JSON")
            return None

        token = info.get("token")
        if not token:
            print("Bridge: no token in login info")
            return None

        print(f"Bridge: got auth token from {app['name']}")
        return token

    except OSError as e:
        print(f"Bridge: failed to load bridge library: {e}")
        return None
    except Exception as e:
        print(f"Bridge: error extracting token: {e}")
        return None
    finally:
        if bridge:
            try:
                bridge.bridge_cleanup()
            except Exception:
                pass

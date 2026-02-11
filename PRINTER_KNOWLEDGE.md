# Bambu Lab X1C Printer — Agent Knowledge Base

This document captures hard-won knowledge about programmatically controlling a Bambu Lab X1C 3D printer. It is intended for AI agents or developers integrating with the printer via MQTT, FTPS, and the OrcaSlicer CLI.

## Communication Protocols

### MQTT (Port 8883, TLS)
- **Broker**: Printer's local IP, port 8883 with self-signed TLS certificate
- **Credentials**: Username `bblp`, password is the printer's access code
- **Topics**:
  - `device/{serial}/request` — send commands
  - `device/{serial}/report` — receive status updates
- **Status refresh**: Send `{"pushing": {"command": "pushall"}}` to get a full status dump

### FTPS (Port 990, Implicit TLS)
- Implicit TLS (SSL on connect, not AUTH TLS upgrade)
- Credentials: Username `bblp`, password is access code
- **TLS session reuse required** for data connections (vsftpd requirement)
- Upload to `/cache/` folder — printer reads 3MF files from here
- Generous timeouts needed: connect=120s, data socket=300s

### Print Workflow
1. Slice STL to 3MF (via OrcaSlicer CLI + template injection)
2. Upload 3MF to `/cache/{filename}` via FTPS
3. Send MQTT `project_file` command referencing `ftp:///cache/{filename}`
4. Monitor `gcode_state` in MQTT reports

## MQTT Commands

### Start a Print
```json
{
    "print": {
        "sequence_id": "1",
        "command": "project_file",
        "param": "Metadata/plate_1.gcode",
        "project_id": "0",
        "profile_id": "0",
        "task_id": "0",
        "subtask_id": "0",
        "subtask_name": "model_name",
        "file": "",
        "url": "ftp:///cache/model.3mf",
        "md5": "",
        "timelapse": false,
        "bed_type": "auto",
        "bed_levelling": true,
        "flow_cali": true,
        "vibration_cali": true,
        "layer_inspect": false,
        "use_ams": true,
        "ams_mapping": [0]
    }
}
```

### Pause / Resume / Stop
```json
{"print": {"sequence_id": "2", "command": "pause", "param": ""}}
{"print": {"sequence_id": "3", "command": "resume", "param": ""}}
{"print": {"sequence_id": "4", "command": "stop", "param": ""}}
```

### Change Nozzle Type (set_accessories)
```json
{
    "system": {
        "sequence_id": "5",
        "command": "set_accessories",
        "accessory_type": "nozzle",
        "nozzle_diameter": 0.4,
        "nozzle_type": "hardened_steel"
    }
}
```
Note: This command returns `"result": "success"` but may not actually change the reported nozzle type on newer firmware. See "Nozzle Type Mismatch" below.

## Printer States (`gcode_state`)

| State | Meaning |
|-------|---------|
| `IDLE` | Ready, no active job |
| `PREPARE` | Print job accepted, preparing |
| `RUNNING` | Actively printing |
| `PAUSE` | Paused (user action or firmware warning) |
| `FINISH` | Print completed |
| `FAILED` | Print failed or was stopped |

## Known Issues & Solutions

### 1. AMS Mapping Must Be `[0]`, Not Empty String
**Symptom**: Print starts but shows "AMS mapping error" on touchscreen.
**Cause**: Older code sent `ams_mapping: ""` — firmware rejects this.
**Fix**: Always send `ams_mapping: [0]` (array with tray index) as default.

### 2. Nozzle Type Mismatch (Firmware 01.11+)
**Symptom**: Print pauses immediately with "The current nozzle setting does not match the slicing file" warning.

**Root Cause**: Firmware 01.11.00.00 changed nozzle type identifiers. The printer now reports `nozzle_type: "HX01"` via MQTT instead of `"hardened_steel"`. OrcaSlicer 2.3.1 only outputs `hardened_steel`, `stainless_steel`, or `undefine` in sliced 3MF files. The firmware does a string comparison and shows a warning dialog when they don't match.

**What does NOT work**:
- Patching the 3MF `nozzle_type` to `HX01` — OrcaSlicer doesn't recognize this value; even manual post-slice patching still triggers the warning
- Patching to `stainless_steel` — printer rejects the file entirely (stays IDLE)
- Removing `nozzle_type` from the 3MF — printer rejects the file
- Patching to `undefine` — still triggers the PAUSE warning
- MQTT `set_accessories` to change printer nozzle to `hardened_steel` — returns success but printer still reports `HX01`
- There is **no bypass flag** in the `project_file` MQTT command for nozzle validation

**What WORKS — Auto-Resume**:
The print actually starts (state goes to RUNNING/PREPARE) then immediately pauses for the warning dialog. Sending a `resume` command via MQTT auto-dismisses the warning and the print continues normally. This is the implemented solution:

```python
# After sending project_file, poll for state changes
for _ in range(30):  # up to 6 seconds
    await asyncio.sleep(0.2)
    if status.state == "PRINTING":
        break
if status.state == "PAUSED":
    # Auto-dismiss the firmware warning
    publish({"print": {"command": "resume", "param": ""}})
```

### 3. FTPS Data Socket Needs TLS Session Reuse
**Symptom**: `ssl.SSLError` or `ValueError: server_hostname cannot be empty` during file upload.
**Fix**: Override `ntransfercmd()` in the FTP_TLS subclass to reuse the control socket's TLS session:
```python
def ntransfercmd(self, cmd, rest=None):
    conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
    if self._prot_p:
        conn = self.sock.context.wrap_socket(
            conn, server_hostname=self.host,
            session=self.sock.session,
        )
    return conn, size
```

### 4. MQTT Disconnects Between Operations
**Symptom**: Status checks or commands fail after a period of inactivity.
**Fix**: Clean up the previous MQTT client before reconnecting — call `loop_stop()` and `disconnect()` on the old client, set it to `None`, then create a fresh one.

## 3MF File Structure (After OrcaSlicer Slicing)

```
model.3mf (ZIP archive)
├── [Content_Types].xml
├── _rels/.rels
├── 3D/
│   ├── 3dmodel.model              # 3D geometry (vertices/triangles)
│   ├── Objects/*.model             # Object mesh data
│   └── _rels/3dmodel.model.rels
└── Metadata/
    ├── plate_1.gcode               # Sliced G-code (the actual print instructions)
    ├── plate_1.gcode.md5
    ├── plate_1.json                # Plate config: bed_type, nozzle_diameter, bbox
    ├── project_settings.config     # JSON: all slicer settings including nozzle_type
    ├── model_settings.config       # XML: object placement and plate layout
    ├── slice_info.config           # XML: slice metadata, nozzle_diameters
    └── _rels/model_settings.config.rels
```

**Key fields in `project_settings.config`** (JSON):
- `nozzle_type`: `"hardened_steel"` / `"stainless_steel"` — validated by firmware
- `nozzle_diameter`: `["0.4"]`
- `nozzle_hrc`: `"0"` (hardness, not validated)
- `nozzle_volume`: `"107"` (flow rate, not validated)
- `curr_bed_type`: `"Cool Plate"` / `"High Temp Plate"` etc.

**Nozzle type also appears in G-code header comments** (line ~288):
```
; nozzle_type = hardened_steel
```

## Slicing with OrcaSlicer CLI

```bash
# Template-based slicing: inject STL geometry into a pre-configured 3MF template
OrcaSlicer --slice 0 --export-3mf output.3mf input.3mf
```

The template (`~/.printlab/template.3mf`) contains all printer/filament/quality settings. The workflow:
1. Parse STL vertices and triangles
2. Build 3MF model XML from geometry
3. Inject into template (replace `3D/3dmodel.model`)
4. Run OrcaSlicer CLI to re-slice with template settings
5. Post-process: patch `bed_type` in output to match printer config

## Thingiverse API (Model Search)

- **Token**: Anonymous bearer `56edfc79ecf25922b98202dd79a291aa` (from public JS bundle, may rotate)
- **Base URL**: `https://www.thingiverse.com/api/` (NOT `api.thingiverse.com` — blocked by Cloudflare)
- **Search**: `GET /api/search/{query}` with `Authorization: Bearer {token}`
- **Thing detail**: `GET /api/things/{id}` — includes `zip_data.files` array with CDN download URLs
- **File list endpoint** (`/api/things/{id}/files`) is blocked by Cloudflare — use `zip_data.files` instead
- **CDN URLs** (`cdn.thingiverse.com/assets/...`) work without auth headers

## Environment & Dependencies

- Python 3.12 (via Homebrew) in `.venv`
- Key packages: `paho-mqtt`, `httpx`, `python-telegram-bot`, `anthropic`, `fastapi`, `uvicorn`
- OrcaSlicer 2.3.1 installed at `/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer`
- System Python 3.9.6 is too old for `virtuals-acp` SDK (needs 3.10+)

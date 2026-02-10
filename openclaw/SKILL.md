# PrintLab 3D Printing Skill

You can control a Bambu Lab X1C 3D printer through the PrintLab API. The API runs locally and provides search, print, and status capabilities.

**Base URL:** `http://localhost:8000`
**Auth:** All requests require `Authorization: Bearer $PRINTLAB_API_KEY` header.

---

## Actions

### 1. Search for 3D Models

Find printable 3D models on Thingiverse.

```bash
curl -s -X POST http://localhost:8000/api/search \
  -H "Authorization: Bearer $PRINTLAB_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query": "dragon figurine", "limit": 5}'
```

**Response:**
```json
{
  "models": [
    {
      "id": "6428358",
      "name": "ARTICULATED poison DRAGON",
      "url": "https://www.thingiverse.com/thing:6428358",
      "thumbnail": "https://cdn.thingiverse.com/...",
      "author": "McGybeer",
      "provider": "Thingiverse",
      "downloads": 1200,
      "likes": 11000,
      "download_url": null,
      "file_count": 0
    }
  ]
}
```

### 2. Print a Model

Print a model by its Thingiverse ID or direct download URL. The system automatically downloads the file, slices STL to 3MF if needed, uploads to the printer via FTPS, and starts the print.

**By model ID** (looks up download URL from Thingiverse):
```bash
curl -s -X POST http://localhost:8000/api/print \
  -H "Authorization: Bearer $PRINTLAB_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model_id": "6428358"}'
```

**By direct URL:**
```bash
curl -s -X POST http://localhost:8000/api/print \
  -H "Authorization: Bearer $PRINTLAB_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model_url": "https://cdn.thingiverse.com/assets/...", "filename": "dragon.stl"}'
```

**With filament selection** (AMS tray index):
```bash
curl -s -X POST http://localhost:8000/api/print \
  -H "Authorization: Bearer $PRINTLAB_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model_id": "6428358", "ams_mapping": [2]}'
```

**Success:** `{"success": true}`

**Error responses** (HTTP 422):
```json
{"detail": {"error": "no_slicer", "message": "No slicer installed"}}
{"detail": {"error": "slice_failed", "message": "Slicing failed"}}
{"detail": {"error": "upload_failed", "message": "FTPS upload to printer failed"}}
```

### 3. Check Printer Status

```bash
curl -s http://localhost:8000/api/status \
  -H "Authorization: Bearer $PRINTLAB_API_KEY"
```

**Response:**
```json
{
  "state": "PRINTING",
  "progress": 42,
  "current_layer": 87,
  "total_layers": 207,
  "remaining_time": 340,
  "bed_temp": 55.0,
  "nozzle_temp": 220.0,
  "error_message": null
}
```

States: `IDLE`, `PRINTING`, `PAUSED`, `FINISHED`, `PREPARING`, `ERROR`

### 4. List Available Filaments (AMS Trays)

```bash
curl -s http://localhost:8000/api/trays \
  -H "Authorization: Bearer $PRINTLAB_API_KEY"
```

**Response:**
```json
{
  "trays": [
    {"ams_id": 0, "tray_id": 0, "color": "FF0000FF", "material": "PLA"},
    {"ams_id": 0, "tray_id": 1, "color": "00FF00FF", "material": "PLA"},
    {"ams_id": 0, "tray_id": 2, "color": "FFFFFFFF", "material": "PETG"}
  ]
}
```

Use `ams_id * 4 + tray_id` as the value for `ams_mapping` when printing.

### 5. Printer Controls

```bash
# Pause current print
curl -s -X POST http://localhost:8000/api/pause \
  -H "Authorization: Bearer $PRINTLAB_API_KEY"

# Resume paused print
curl -s -X POST http://localhost:8000/api/resume \
  -H "Authorization: Bearer $PRINTLAB_API_KEY"

# Stop/cancel current print
curl -s -X POST http://localhost:8000/api/stop \
  -H "Authorization: Bearer $PRINTLAB_API_KEY"
```

### 6. Health Check

```bash
curl -s http://localhost:8000/api/health \
  -H "Authorization: Bearer $PRINTLAB_API_KEY"
```

**Response:** `{"status": "ok", "printer_connected": true}`

---

## Workflow Example

To fulfill a "print a dragon figurine" request:

1. **Search** for models: `POST /api/search` with `{"query": "dragon figurine", "limit": 5}`
2. **Check printer** is ready: `GET /api/status` — verify state is `IDLE`
3. **Check filaments** (optional): `GET /api/trays` — see what colors are loaded
4. **Print** the best result: `POST /api/print` with `{"model_id": "<id from search>"}`
5. **Monitor** progress: `GET /api/status` — poll until state is `FINISHED`

## Important Notes

- Only single-file models can be printed. Multi-part models (file_count > 1) will be rejected.
- STL files are automatically sliced to 3MF using OrcaSlicer before printing.
- Print times range from minutes to many hours depending on model size.
- The printer must be idle to start a new print.
- `remaining_time` in status is in minutes.

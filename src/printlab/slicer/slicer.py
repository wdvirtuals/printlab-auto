"""Slicer integration for STL to 3MF conversion."""

from __future__ import annotations

import asyncio
import shutil
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional


class SlicerNotFoundError(Exception):
    """Raised when no slicer is available."""
    pass


    # Map config bed_type values to gcode/3MF display names
BED_TYPE_NAMES = {
    "cool_plate": "Cool Plate",
    "eng_plate": "Engineering Plate",
    "hot_plate": "High Temp Plate",
    "textured_plate": "Textured PEI Plate",
}


class Slicer:
    """Handles slicing STL files to 3MF for Bambu Lab X1C."""

    TEMPLATE_PATH = Path.home() / ".printlab" / "template.3mf"

    # Slicer paths to check (in order of preference)
    SLICER_PATHS = {
        "orca": [
            "/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer",
            shutil.which("orca-slicer"),
        ],
        "bambu": [
            "/Applications/BambuStudio.app/Contents/MacOS/BambuStudio",
            shutil.which("bambu-studio"),
        ],
        "prusa": [
            "/Applications/PrusaSlicer.app/Contents/MacOS/PrusaSlicer",
            "/Applications/Original Prusa Drivers/PrusaSlicer.app/Contents/MacOS/PrusaSlicer",
            shutil.which("prusa-slicer"),
            shutil.which("prusaslicer"),
        ],
    }

    def __init__(self, bed_type: str = "auto"):
        self.slicer_type: Optional[str] = None
        self.slicer_path: Optional[Path] = None
        self.bed_type = bed_type
        self._detect_slicer()

    def _detect_slicer(self) -> None:
        """Detect available slicer."""
        for slicer_type, paths in self.SLICER_PATHS.items():
            for path in paths:
                if path and Path(path).exists():
                    self.slicer_type = slicer_type
                    self.slicer_path = Path(path)
                    print(f"Found slicer: {slicer_type} at {path}")
                    return

    @property
    def is_available(self) -> bool:
        """Check if a slicer is available."""
        return self.slicer_path is not None

    @property
    def supports_bambu(self) -> bool:
        """Check if the detected slicer supports Bambu printers."""
        return self.slicer_type in ("orca", "bambu")

    @property
    def has_template(self) -> bool:
        """Check if a slicing template exists."""
        return self.TEMPLATE_PATH.exists()

    def get_install_instructions(self) -> str:
        """Get instructions for installing a slicer."""
        return (
            "No compatible slicer found.\n\n"
            "For Bambu Lab printers, install:\n"
            "• *Orca Slicer* (recommended):\n"
            "  https://github.com/SoftFever/OrcaSlicer/releases\n\n"
            "• *Bambu Studio*:\n"
            "  https://bambulab.com/en/download/studio"
        )

    def get_template_instructions(self) -> str:
        """Get instructions for creating a slicing template."""
        return (
            "Slicing template not found.\n\n"
            "To create a template:\n"
            "1. Open Orca Slicer\n"
            "2. Load any STL file\n"
            "3. Configure your preferred settings\n"
            "4. Click Slice\n"
            "5. Export as ~/.printlab/template.3mf"
        )

    def _parse_stl(self, stl_path: Path) -> tuple[list, list]:
        """Parse an STL file and return vertices and triangles.

        Returns:
            Tuple of (vertices list, triangles list)
        """
        vertices = []
        triangles = []
        vertex_map = {}

        with open(stl_path, 'rb') as f:
            # Check if binary or ASCII
            header = f.read(80)
            f.seek(0)

            # Try ASCII first
            try:
                content = f.read().decode('ascii')
                if 'solid' in content.lower() and 'facet' in content.lower():
                    return self._parse_ascii_stl(content)
            except:
                pass

            # Binary STL
            f.seek(80)  # Skip header
            num_triangles = int.from_bytes(f.read(4), 'little')

            for _ in range(num_triangles):
                f.read(12)  # Skip normal
                tri_verts = []
                for _ in range(3):
                    x = self._read_float(f)
                    y = self._read_float(f)
                    z = self._read_float(f)
                    v_key = (round(x, 6), round(y, 6), round(z, 6))
                    if v_key not in vertex_map:
                        vertex_map[v_key] = len(vertices)
                        vertices.append(v_key)
                    tri_verts.append(vertex_map[v_key])
                triangles.append(tuple(tri_verts))
                f.read(2)  # Skip attribute

        return vertices, triangles

    def _read_float(self, f) -> float:
        import struct
        return struct.unpack('<f', f.read(4))[0]

    def _parse_ascii_stl(self, content: str) -> tuple[list, list]:
        """Parse ASCII STL content."""
        import re
        vertices = []
        triangles = []
        vertex_map = {}

        vertex_pattern = re.compile(r'vertex\s+([-\d.e+]+)\s+([-\d.e+]+)\s+([-\d.e+]+)', re.I)

        current_tri = []
        for match in vertex_pattern.finditer(content):
            x, y, z = float(match.group(1)), float(match.group(2)), float(match.group(3))
            v_key = (round(x, 6), round(y, 6), round(z, 6))
            if v_key not in vertex_map:
                vertex_map[v_key] = len(vertices)
                vertices.append(v_key)
            current_tri.append(vertex_map[v_key])
            if len(current_tri) == 3:
                triangles.append(tuple(current_tri))
                current_tri = []

        return vertices, triangles

    def _create_3mf_from_template(self, stl_path: Path, output_path: Path) -> Optional[Path]:
        """Create a 3MF file by injecting STL model into template."""
        import tempfile

        if not self.has_template:
            print("No template.3mf found")
            return None

        vertices, triangles = self._parse_stl(stl_path)
        if not vertices or not triangles:
            print("Failed to parse STL")
            return None

        # Build 3MF model XML
        model_xml = self._build_3mf_model(vertices, triangles, stl_path.stem)

        # Extract template, replace model, re-zip
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            # Extract template
            with zipfile.ZipFile(self.TEMPLATE_PATH, 'r') as zf:
                zf.extractall(tmppath)

            # Replace model
            model_file = tmppath / "3D" / "3dmodel.model"
            model_file.write_text(model_xml)

            # Re-zip
            with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for file in tmppath.rglob('*'):
                    if file.is_file():
                        zf.write(file, file.relative_to(tmppath))

        return output_path if output_path.exists() else None

    def _build_3mf_model(self, vertices: list, triangles: list, name: str) -> str:
        """Build 3MF model XML from vertices and triangles."""
        vert_lines = '\n'.join(
            f'     <vertex x="{v[0]}" y="{v[1]}" z="{v[2]}"/>'
            for v in vertices
        )
        tri_lines = '\n'.join(
            f'     <triangle v1="{t[0]}" v2="{t[1]}" v3="{t[2]}"/>'
            for t in triangles
        )

        # Calculate center for positioning (place on bed)
        min_z = min(v[2] for v in vertices)
        z_offset = -min_z if min_z < 0 else 0

        return f'''<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" xmlns:BambuStudio="http://schemas.bambulab.com/package/2021">
 <metadata name="Application">PrintLab-Auto</metadata>
 <metadata name="BambuStudio:3mfVersion">1</metadata>
 <resources>
  <object id="1" type="model">
   <mesh>
    <vertices>
{vert_lines}
    </vertices>
    <triangles>
{tri_lines}
    </triangles>
   </mesh>
  </object>
  <object id="2" type="model">
   <components>
    <component objectid="1" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>
   </components>
  </object>
 </resources>
 <build>
  <item objectid="2" transform="1 0 0 0 1 0 0 0 1 128 128 {z_offset}" printable="1"/>
 </build>
</model>'''

    async def slice_stl(
        self,
        stl_path: Path,
        output_dir: Optional[Path] = None,
    ) -> Optional[Path]:
        """Slice an STL file to 3MF.

        Args:
            stl_path: Path to the STL file
            output_dir: Directory for output file (defaults to same as input)

        Returns:
            Path to the sliced 3MF file, or None on failure
        """
        if not self.is_available:
            raise SlicerNotFoundError(self.get_install_instructions())

        if not stl_path.exists():
            print(f"STL file not found: {stl_path}")
            return None

        output_dir = output_dir or stl_path.parent
        output_path = output_dir / f"{stl_path.stem}.3mf"

        try:
            if self.slicer_type == "orca":
                return await self._slice_with_orca(stl_path, output_path)
            elif self.slicer_type == "bambu":
                return await self._slice_with_bambu(stl_path, output_path)
            elif self.slicer_type == "prusa":
                return await self._slice_with_prusa(stl_path, output_path)
        except Exception as e:
            print(f"Slicing error: {e}")
            return None

        return None

    async def _slice_with_orca(self, stl_path: Path, output_path: Path) -> Optional[Path]:
        """Slice using Orca Slicer with template-based approach."""
        if not self.has_template:
            print(self.get_template_instructions())
            return None

        # Create 3MF with STL model injected into template
        temp_3mf = output_path.with_suffix('.temp.3mf')
        if not self._create_3mf_from_template(stl_path, temp_3mf):
            print("Failed to create 3MF from template")
            return None

        # Slice the 3MF
        cmd = [
            str(self.slicer_path),
            "--slice", "0",
            "--export-3mf", str(output_path),
            str(temp_3mf),
        ]
        result = await self._run_slicer(cmd, output_path)

        # Clean up temp file
        if temp_3mf.exists():
            temp_3mf.unlink()

        return result

    async def _slice_with_bambu(self, stl_path: Path, output_path: Path) -> Optional[Path]:
        """Slice using Bambu Studio with template-based approach."""
        if not self.has_template:
            print(self.get_template_instructions())
            return None

        # Create 3MF with STL model injected into template
        temp_3mf = output_path.with_suffix('.temp.3mf')
        if not self._create_3mf_from_template(stl_path, temp_3mf):
            print("Failed to create 3MF from template")
            return None

        # Slice the 3MF
        cmd = [
            str(self.slicer_path),
            "--slice", "0",
            "--export-3mf", str(output_path),
            str(temp_3mf),
        ]
        result = await self._run_slicer(cmd, output_path)

        # Clean up temp file
        if temp_3mf.exists():
            temp_3mf.unlink()

        return result

    async def _slice_with_prusa(self, stl_path: Path, output_path: Path) -> Optional[Path]:
        """Slice using PrusaSlicer.

        Note: PrusaSlicer cannot generate Bambu-compatible sliced 3MF files.
        For Bambu printers, use Orca Slicer or Bambu Studio instead.
        This will export a 3MF with the model but without Bambu-specific G-code.
        """
        # PrusaSlicer CLI - export as 3MF (model only, not sliced for Bambu)
        cmd = [
            str(self.slicer_path),
            "--export-3mf",
            "-o", str(output_path),
            str(stl_path),
        ]
        return await self._run_slicer(cmd, output_path)

    def _patch_bed_type(self, path_3mf: Path) -> None:
        """Patch the bed_type inside a sliced 3MF to match config.

        Modifies plate_1.json, project_settings.config, and gcode metadata
        so the printer doesn't show a plate mismatch warning.
        """
        if self.bed_type == "auto":
            return
        target_name = BED_TYPE_NAMES.get(self.bed_type)
        if not target_name:
            return

        import json as json_mod
        temp_path = path_3mf.with_suffix('.patched.3mf')
        patched = False

        with zipfile.ZipFile(path_3mf, 'r') as zin:
            with zipfile.ZipFile(temp_path, 'w', zipfile.ZIP_DEFLATED) as zout:
                for item in zin.namelist():
                    data = zin.read(item)

                    if item.endswith('plate_1.json'):
                        try:
                            j = json_mod.loads(data)
                            if j.get('bed_type') != self.bed_type:
                                j['bed_type'] = self.bed_type
                                data = json_mod.dumps(j).encode()
                                patched = True
                        except Exception:
                            pass

                    elif item.endswith('project_settings.config'):
                        text = data.decode('utf-8')
                        for old_name in BED_TYPE_NAMES.values():
                            if old_name != target_name and f'"curr_bed_type": "{old_name}"' in text:
                                text = text.replace(
                                    f'"curr_bed_type": "{old_name}"',
                                    f'"curr_bed_type": "{target_name}"',
                                )
                                patched = True
                        data = text.encode('utf-8')

                    elif item.endswith('.gcode'):
                        text = data.decode('utf-8', errors='ignore')
                        for old_name in BED_TYPE_NAMES.values():
                            if old_name != target_name and f'; curr_bed_type = {old_name}' in text:
                                text = text.replace(
                                    f'; curr_bed_type = {old_name}',
                                    f'; curr_bed_type = {target_name}',
                                )
                                patched = True
                        data = text.encode('utf-8')

                    zout.writestr(item, data)

        if patched:
            import os
            os.replace(temp_path, path_3mf)
            print(f"Patched bed_type → {self.bed_type} ({target_name})")
        else:
            temp_path.unlink(missing_ok=True)

    def _patch_nozzle_type(self, path_3mf: Path, nozzle_type: str) -> None:
        """Patch the nozzle_type inside a sliced 3MF to match the printer.

        After firmware updates, Bambu printers may report nozzle types using
        product codes (e.g. 'HX01') instead of generic names ('hardened_steel').
        This patches project_settings.config AND gcode comment headers so the
        printer doesn't reject the file.
        """
        if not nozzle_type:
            return

        import json as json_mod
        import re
        temp_path = path_3mf.with_suffix('.nozzle_patched.3mf')
        patched = False

        with zipfile.ZipFile(path_3mf, 'r') as zin:
            with zipfile.ZipFile(temp_path, 'w', zipfile.ZIP_DEFLATED) as zout:
                for item in zin.namelist():
                    data = zin.read(item)

                    if item.endswith('project_settings.config'):
                        try:
                            j = json_mod.loads(data)
                            old_type = j.get('nozzle_type')
                            if old_type and old_type != nozzle_type:
                                j['nozzle_type'] = nozzle_type
                                data = json_mod.dumps(j, indent=4).encode()
                                patched = True
                        except Exception:
                            pass

                    elif item.endswith('.gcode'):
                        text = data.decode('utf-8', errors='ignore')
                        new_text = re.sub(
                            r'^(; nozzle_type = ).+$',
                            rf'\g<1>{nozzle_type}',
                            text,
                            flags=re.MULTILINE,
                        )
                        if new_text != text:
                            data = new_text.encode('utf-8')
                            patched = True

                    zout.writestr(item, data)

        if patched:
            import os
            os.replace(temp_path, path_3mf)
            print(f"Patched nozzle_type → {nozzle_type}")
        else:
            temp_path.unlink(missing_ok=True)

    async def _run_slicer(self, cmd: list[str], output_path: Path) -> Optional[Path]:
        """Run slicer command and return output path if successful."""
        print(f"Running slicer: {' '.join(cmd)}")

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            print(f"Slicer failed (exit {process.returncode})")
            if stderr:
                print(f"Stderr: {stderr.decode()}")
            if stdout:
                print(f"Stdout: {stdout.decode()}")
            return None

        if output_path.exists():
            print(f"Sliced successfully: {output_path}")
            self._patch_bed_type(output_path)
            return output_path
        else:
            print(f"Slicer completed but output file not found: {output_path}")
            return None

"""Floor plan importers. Each returns Room fields {"plan": {...}, "openings": [...]} (see agentic_sim_world_gen.scene.plan).

    json_plan.load_plan(path)   agentic-sim-world-gen floor plan JSON (also what Claude writes when tracing an image / PDF)
    dxf.load_dxf(path)          AutoCAD DXF (ezdxf): wall lines + door / window blocks
    ifc.load_ifc(path)          IFC (ifcopenshell): IfcWall / IfcDoor / IfcWindow / IfcSpace

`load_any(path)` picks one by file extension.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def load_any(path: str | Path, **kw: Any) -> dict[str, Any]:
    p = Path(path).expanduser()
    ext = p.suffix.lower()
    if ext == ".json":
        from agentic_sim_world_gen.plan.json_plan import load_plan

        return load_plan(p, **kw)
    if ext == ".dxf":
        from agentic_sim_world_gen.plan.dxf import load_dxf

        return load_dxf(p, **kw)
    if ext == ".ifc":
        from agentic_sim_world_gen.plan.ifc import load_ifc

        return load_ifc(p, **kw)
    raise ValueError(
        f"unsupported floor plan '{p.name}': use .json, .dxf or .ifc. For an image or PDF, trace it into "
        "floor plan JSON first (see the world-gen skill)"
    )

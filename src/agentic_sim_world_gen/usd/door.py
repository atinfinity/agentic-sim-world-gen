"""Automatic sliding doors (Opening.style = "auto_sliding").

Two glass panels hang on the outside face of the wall, one on each half of the opening. Each panel
is a rigid body on a prismatic joint to the world with a linear drive; positive travel opens it
(the left panel slides toward the wall start, the right one toward the wall end). A header box
covers the top of the panels and their travel.

The trigger is an OmniGraph action graph authored as plain USD (no Python, no Kit needed to write
it): every playback tick, three PhysX box overlap queries look at the inside, the doorway and the
outside. If any of them touches a collider, both drive targets are set to the open travel, else 0.
Isaac Sim runs it while the timeline plays; omni.physx.graph must be enabled (it is in the full
app; check_scene enables it when the stage has doors).

    /World/Room/Doors/<id>            Xform, customData agentic_sim_world_gen:door {opening, wall, travel, sensor}
        Header, Panel_L, Panel_R      panels: Xform rigid body (Collider + Glass + Frame_*)
        Joint_L, Joint_R              PhysicsPrismaticJoint + PhysicsDriveAPI:linear
        Sensor                        OmniGraph
"""

from __future__ import annotations

import math

from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade

from agentic_sim_world_gen.layout.room import RoomGeometry, Wall
from agentic_sim_world_gen.scene.plan import Opening

PANEL_T = 0.04  # collider thickness [m]
GLASS_T = 0.012
FRAME_T = 0.045
STILE = 0.05  # frame bar width
FACE_GAP = 0.015  # wall face -> panel
OVERLAP = 0.05  # each panel covers the wall this far past the opening edge
MID_GAP = 0.002  # between the closed panels
FLOOR_GAP = 0.01
HEADER_H = 0.15
MASS = 20.0  # kg per panel
STIFFNESS = 1000.0  # N/m
DAMPING = 300.0  # N s/m (about critical for 20 kg)
SENSE_Z = (0.03, 1.4)  # sensor boxes above the sill [m]: robots and people, not the floor
TARGET = "drive:linear:physics:targetPosition"


def _quat_z(deg: float) -> Gf.Quatf:
    r = math.radians(deg) / 2
    return Gf.Quatf(math.cos(r), 0.0, 0.0, math.sin(r))


def _cube(stage: Usd.Stage, path: str, center, size, yaw_deg: float = 0.0, collider: bool = False) -> UsdGeom.Cube:
    c = UsdGeom.Cube.Define(stage, path)
    c.CreateSizeAttr(1.0)
    xf = UsdGeom.Xformable(c)
    xf.AddTranslateOp().Set(Gf.Vec3d(*center))
    if yaw_deg:
        xf.AddRotateZOp().Set(float(yaw_deg))
    xf.AddScaleOp().Set(Gf.Vec3f(*size))
    c.CreateExtentAttr([Gf.Vec3f(-0.5, -0.5, -0.5), Gf.Vec3f(0.5, 0.5, 0.5)])
    if collider:
        UsdPhysics.CollisionAPI.Apply(c.GetPrim())
    return c


def _material(stage: Usd.Stage, path: str, color, opacity: float, roughness: float, metallic: float) -> UsdShade.Material:
    mat = UsdShade.Material.Define(stage, path)
    ps = UsdShade.Shader.Define(stage, f"{path}/PreviewSurface")
    ps.CreateIdAttr("UsdPreviewSurface")
    ps.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color))
    ps.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(roughness)
    ps.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(metallic)
    ps.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(opacity)
    if opacity < 1:
        ps.CreateInput("ior", Sdf.ValueTypeNames.Float).Set(1.5)
    mat.CreateSurfaceOutput().ConnectToSource(ps.ConnectableAPI(), "surface")
    return mat


def _bind(prim: Usd.Prim, mat: UsdShade.Material) -> None:
    UsdShade.MaterialBindingAPI.Apply(prim).Bind(mat)


def door_frame(geom: RoomGeometry, w: Wall, o: Opening) -> dict:
    """Geometry of an auto door in world coordinates (also used by tests and the preview)."""
    u0, u1 = w.interval(o)
    sill, top = o.resolved(w.height)
    out = -geom.inner_side(w, (u0 + u1) / 2)  # panels on the outside face
    n_panel = out * (w.thickness / 2 + FACE_GAP + PANEL_T / 2)
    width = u1 - u0
    pw = width / 2 + OVERLAP - MID_GAP / 2
    travel = width / 2 + 0.02
    return {"u0": u0, "u1": u1, "sill": sill, "top": top, "out": out, "n_panel": n_panel,
            "panel_w": pw, "travel": round(travel, 4), "yaw": w.yaw_deg}


def build_doors(stage: Usd.Stage, geom: RoomGeometry, root: str, label) -> list[str]:
    """Author every auto_sliding door of the room. Returns the door prim paths."""
    doors = [(w, o) for w in geom.walls for o in w.openings if o.style == "auto_sliding"]
    if not doors:
        return []
    UsdGeom.Xform.Define(stage, f"{root}/Doors")
    glass = _material(stage, f"{root}/Looks/DoorGlass", (0.78, 0.86, 0.9), 0.25, 0.05, 0.0)
    alu = _material(stage, f"{root}/Looks/DoorFrame", (0.72, 0.73, 0.75), 1.0, 0.35, 0.8)
    paths = []
    for w, o in doors:
        f = door_frame(geom, w, o)
        base = f"{root}/Doors/{o.id}"
        dp = UsdGeom.Xform.Define(stage, base).GetPrim()
        dp.SetCustomDataByKey("agentic_sim_world_gen:door", {"opening": o.id, "wall": w.id, "travel": f["travel"],
                                                 "sensor": o.sensor, "style": o.style})
        label(dp, "door")
        yaw, mid = f["yaw"], (f["u0"] + f["u1"]) / 2
        z0, z1 = f["sill"] + FLOOR_GAP, f["top"] - 0.005
        zc, ph = (z0 + z1) / 2, z1 - z0
        # header: over the closed panels and their full travel
        hl = f["u1"] - f["u0"] + 2 * (f["travel"] + OVERLAP) + 0.1
        hx, hy = w.at(mid, f["n_panel"] + f["out"] * 0.01)
        hdr = _cube(stage, f"{base}/Header", (hx, hy, f["top"] + HEADER_H / 2 - 0.005), (hl, PANEL_T + 0.05, HEADER_H),
                    yaw, collider=True)
        _bind(hdr.GetPrim(), alu)
        for side, sgn in (("L", -1.0), ("R", 1.0)):
            uc = mid + sgn * (MID_GAP / 2 + f["panel_w"] / 2)
            px, py = w.at(uc, f["n_panel"])
            panel = UsdGeom.Xform.Define(stage, f"{base}/Panel_{side}")
            xf = UsdGeom.Xformable(panel)
            xf.AddTranslateOp().Set(Gf.Vec3d(px, py, zc))
            xf.AddRotateZOp().Set(float(yaw))
            pp = panel.GetPrim()
            UsdPhysics.RigidBodyAPI.Apply(pp)
            UsdPhysics.MassAPI.Apply(pp).CreateMassAttr(MASS)
            col = _cube(stage, f"{base}/Panel_{side}/Collider", (0, 0, 0), (f["panel_w"], PANEL_T, ph), collider=True)
            col.CreateVisibilityAttr(UsdGeom.Tokens.invisible)
            g = _cube(stage, f"{base}/Panel_{side}/Glass", (0, 0, 0), (f["panel_w"] - STILE, GLASS_T, ph - STILE))
            _bind(g.GetPrim(), glass)
            hw, hh = f["panel_w"] / 2, ph / 2
            for name, c, s in (("Frame_B", (0, 0, -hh + STILE), (2 * hw, FRAME_T, 2 * STILE)),
                               ("Frame_T", (0, 0, hh - STILE / 2), (2 * hw, FRAME_T, STILE)),
                               ("Frame_0", (-hw + STILE / 2, 0, 0), (STILE, FRAME_T, ph)),
                               ("Frame_1", (hw - STILE / 2, 0, 0), (STILE, FRAME_T, ph))):
                fr = _cube(stage, f"{base}/Panel_{side}/{name}", c, s)
                _bind(fr.GetPrim(), alu)
            # joint frame 0 (world) at the closed panel; its X axis points the way the panel opens
            phi = yaw if sgn > 0 else yaw + 180.0
            j = UsdPhysics.PrismaticJoint.Define(stage, f"{base}/Joint_{side}")
            j.CreateBody1Rel().SetTargets([pp.GetPath()])
            j.CreateAxisAttr(UsdPhysics.Tokens.x)
            j.CreateLocalPos0Attr(Gf.Vec3f(px, py, zc))
            j.CreateLocalRot0Attr(_quat_z(phi))
            j.CreateLocalPos1Attr(Gf.Vec3f(0, 0, 0))
            j.CreateLocalRot1Attr(_quat_z(phi - yaw))
            j.CreateLowerLimitAttr(0.0)
            j.CreateUpperLimitAttr(f["travel"])
            d = UsdPhysics.DriveAPI.Apply(j.GetPrim(), "linear")
            d.CreateTypeAttr("force")
            d.CreateStiffnessAttr(STIFFNESS)
            d.CreateDampingAttr(DAMPING)
            d.CreateTargetPositionAttr(0.0)
        _sensor_graph(stage, f"{base}/Sensor", w, o, f)
        paths.append(base)
    return paths


def sensor_boxes(w: Wall, o: Opening, f: dict) -> list[tuple[tuple[float, float, float], tuple[float, float, float]]]:
    """(center, full size) of the inside, doorway and outside boxes, in the wall frame turned by yaw.
    They keep clear of the floor, the wall parts and the panels."""
    mid = (f["u0"] + f["u1"]) / 2
    width = f["u1"] - f["u0"]
    za, zb = f["sill"] + SENSE_Z[0], min(f["sill"] + SENSE_Z[1], f["top"] - 0.05)
    zc, h = (za + zb) / 2, zb - za
    t = w.thickness
    near_in = t / 2 + 0.05
    near_out = t / 2 + FACE_GAP + PANEL_T + 0.05
    out = []
    for n0, n1, wd in ((-f["out"] * near_in, -f["out"] * (near_in + o.sensor), width + 0.4),
                       (-f["out"] * (t / 2 - 0.01), f["out"] * (t / 2 - 0.01), width - 0.04),
                       (f["out"] * near_out, f["out"] * (near_out + o.sensor), width + 0.4)):
        x, y = w.at(mid, (n0 + n1) / 2)
        out.append(((x, y, zc), (wd, abs(n1 - n0), h)))
    return out


def _node(stage: Usd.Stage, path: str, node_type: str, version: int) -> Usd.Prim:
    p = stage.DefinePrim(path, "OmniGraphNode")
    p.CreateAttribute("node:type", Sdf.ValueTypeNames.Token, custom=False).Set(node_type)
    p.CreateAttribute("node:typeVersion", Sdf.ValueTypeNames.Int, custom=False).Set(version)
    return p


def _exec_out(p: Usd.Prim, name: str) -> None:
    a = p.CreateAttribute(name, Sdf.ValueTypeNames.UInt)
    a.SetMetadata("customData", {"isExecution": True})


def _connect(p: Usd.Prim, name: str, src: str, typ=Sdf.ValueTypeNames.Token) -> None:
    a = p.CreateAttribute(name, typ)
    if typ == Sdf.ValueTypeNames.UInt:
        a.SetMetadata("customData", {"isExecution": True})
    a.AddConnection(Sdf.Path(src))


def _sensor_graph(stage: Usd.Stage, g: str, w: Wall, o: Opening, f: dict) -> None:
    """Tick -> 3 x OverlapBoxAny -> Or -> SelectIf(travel, 0) -> write both drive targets.
    Raw USD as Isaac Sim 6.1's og.Controller writes it (fileFormatVersion 1.9)."""
    gp = stage.DefinePrim(g, "OmniGraph")
    for name, typ, val in (("evaluator:type", Sdf.ValueTypeNames.Token, "execution"),
                           ("evaluationMode", Sdf.ValueTypeNames.Token, "Automatic"),
                           ("fabricCacheBacking", Sdf.ValueTypeNames.Token, "StageWithoutHistory"),
                           ("fileFormatVersion", Sdf.ValueTypeNames.Int2, Gf.Vec2i(1, 9)),
                           ("pipelineStage", Sdf.ValueTypeNames.Token, "pipelineStageSimulation")):
        gp.CreateAttribute(name, typ, custom=False).Set(val)
    tick = _node(stage, f"{g}/tick", "omni.graph.action.OnPlaybackTick", 2)
    _exec_out(tick, "outputs:tick")
    rot = _node(stage, f"{g}/rot", "omni.graph.nodes.ConstantDouble3", 1)
    rot.CreateAttribute("inputs:value", Sdf.ValueTypeNames.Double3).Set(Gf.Vec3d(0, 0, f["yaw"]))
    prev = f"{g}/tick.outputs:tick"
    for i, (c, s) in enumerate(sensor_boxes(w, o, f)):
        pos = _node(stage, f"{g}/pos{i}", "omni.graph.nodes.ConstantDouble3", 1)
        pos.CreateAttribute("inputs:value", Sdf.ValueTypeNames.Double3).Set(Gf.Vec3d(*c))
        dim = _node(stage, f"{g}/dim{i}", "omni.graph.nodes.ConstantDouble3", 1)
        dim.CreateAttribute("inputs:value", Sdf.ValueTypeNames.Double3).Set(Gf.Vec3d(*s))
        ov = _node(stage, f"{g}/overlap{i}", "omni.physx.graph.SceneQueryOverlapBoxAny", 1)
        _connect(ov, "inputs:execIn", prev, Sdf.ValueTypeNames.UInt)
        _connect(ov, "inputs:position", f"{g}/pos{i}.inputs:value")
        _connect(ov, "inputs:dimensions", f"{g}/dim{i}.inputs:value")
        _connect(ov, "inputs:rotation", f"{g}/rot.inputs:value")
        _exec_out(ov, "outputs:execOut")
        ov.CreateAttribute("outputs:overlap", Sdf.ValueTypeNames.Bool)
        prev = f"{g}/overlap{i}.outputs:execOut"
    or1 = _node(stage, f"{g}/or01", "omni.graph.nodes.BooleanOr", 2)
    _connect(or1, "inputs:a", f"{g}/overlap0.outputs:overlap")
    _connect(or1, "inputs:b", f"{g}/overlap1.outputs:overlap")
    or1.CreateAttribute("outputs:result", Sdf.ValueTypeNames.Token)
    or2 = _node(stage, f"{g}/or012", "omni.graph.nodes.BooleanOr", 2)
    _connect(or2, "inputs:a", f"{g}/or01.outputs:result")
    _connect(or2, "inputs:b", f"{g}/overlap2.outputs:overlap")
    or2.CreateAttribute("outputs:result", Sdf.ValueTypeNames.Token)
    for name, v in (("open", f["travel"]), ("shut", 0.0)):
        _node(stage, f"{g}/{name}", "omni.graph.nodes.ConstantFloat", 1).CreateAttribute(
            "inputs:value", Sdf.ValueTypeNames.Float).Set(float(v))
    sel = _node(stage, f"{g}/select", "omni.graph.nodes.SelectIf", 1)
    _connect(sel, "inputs:condition", f"{g}/or012.outputs:result")
    _connect(sel, "inputs:ifTrue", f"{g}/open.inputs:value")
    _connect(sel, "inputs:ifFalse", f"{g}/shut.inputs:value")
    sel.CreateAttribute("outputs:result", Sdf.ValueTypeNames.Token)
    door = g.rsplit("/", 1)[0]
    for side in ("L", "R"):
        wr = _node(stage, f"{g}/write{side}", "omni.graph.nodes.WritePrimAttribute", 3)
        _connect(wr, "inputs:execIn", prev, Sdf.ValueTypeNames.UInt)
        wr.CreateAttribute("inputs:name", Sdf.ValueTypeNames.Token).Set(TARGET)
        wr.CreateAttribute("inputs:primPath", Sdf.ValueTypeNames.Token).Set(f"{door}/Joint_{side}")
        wr.CreateAttribute("inputs:usePath", Sdf.ValueTypeNames.Bool).Set(True)
        wr.CreateAttribute("inputs:usdWriteBack", Sdf.ValueTypeNames.Bool).Set(True)
        _connect(wr, "inputs:value", f"{g}/select.outputs:result")
        _exec_out(wr, "outputs:execOut")
        prev = f"{g}/write{side}.outputs:execOut"

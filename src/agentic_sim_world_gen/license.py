"""License report for the assets a scene uses (attribution text + warnings)."""

from __future__ import annotations

import re
from typing import Any

from agentic_sim_world_gen.inputs import SOURCED_KINDS, screen_source
from agentic_sim_world_gen.origin import label as origin_label
from agentic_sim_world_gen.origin import origin
from agentic_sim_world_gen.scene.looks import LIGHTING, SKIES, sky_url
from agentic_sim_world_gen.scene.spec import ResolvedScene

# SPDX id -> (human name, attribution required, note); unknown ids are flagged for manual review
KNOWN = {
    "CC0-1.0": ("CC0 1.0", False, ""),
    "CC-BY-4.0": ("CC BY 4.0", True, ""),
    "CC-BY-3.0": ("CC BY 3.0", True, ""),
    "CC-BY-SA-4.0": ("CC BY-SA 4.0", True, "share-alike: derived assets must use the same license"),
    "CC-BY-NC-4.0": ("CC BY-NC 4.0", True, "non-commercial only"),
    "CC-BY-NC-SA-4.0": ("CC BY-NC-SA 4.0", True, "non-commercial only, share-alike"),
    "MIT": ("MIT", True, ""),
    "Apache-2.0": ("Apache 2.0", True, ""),
    "NVIDIA-Isaac-Sim": ("NVIDIA Isaac Sim asset license", False,
                         "Isaac Sim bundled asset: use with Isaac Sim; check its license before redistributing"),
    # MDL materials bundled with Isaac Sim (agentic_sim_world_gen.materials). vMaterials: the .mdl header grants use free of
    # charge solely with NVIDIA's MDL, keeping the copyright notice; Base: no separate license, ships with the
    # Isaac Sim / Omniverse assets
    "NVIDIA-vMaterials": ("NVIDIA vMaterials license", True,
                          "use only with NVIDIA MDL; keep NVIDIA's copyright notice; check before redistributing the files"),
    "NVIDIA-Omniverse-Materials": ("NVIDIA Omniverse materials (Isaac Sim / Omniverse license)", False,
                                   "Isaac Sim bundled material: use with Isaac Sim / Omniverse; check before redistributing"),
}
_ALIASES = {"CC0": "CC0-1.0", "PUBLIC-DOMAIN": "CC0-1.0",
            "NVIDIA-ISAAC-SIM-ASSETS-(ISAAC-SIM-LICENSE)": "NVIDIA-Isaac-Sim"}


def normalize(lic: str | None) -> str:
    """'CC-BY 4.0', 'cc by 4.0', 'CC-BY-4.0' -> 'CC-BY-4.0'. Empty -> 'UNKNOWN'."""
    if not lic or not lic.strip():
        return "UNKNOWN"
    s = re.sub(r"[\s_]+", "-", lic.strip().upper())
    s = s.replace("ATTRIBUTION", "BY")
    s = _ALIASES.get(s, s)
    for k in KNOWN:
        if k.upper() == s.upper():
            return k
    return lic.strip()


_DETECT = [  # (pattern in a LICENSE file, SPDX id); first match wins, so specific ones come first
    (r"SPDX-License-Identifier:\s*([\w.+-]+)", None),
    (r"Attribution-NonCommercial-ShareAlike 4\.0", "CC-BY-NC-SA-4.0"),
    (r"Attribution-NonCommercial 4\.0", "CC-BY-NC-4.0"),
    (r"Attribution-ShareAlike 4\.0", "CC-BY-SA-4.0"),
    (r"Attribution 4\.0", "CC-BY-4.0"),
    (r"Attribution 3\.0", "CC-BY-3.0"),
    (r"\bCC0\b|Public Domain Dedication", "CC0-1.0"),
    (r"MIT License|Permission is hereby granted, free of charge", "MIT"),
    (r"Apache License,?\s+Version 2\.0", "Apache-2.0"),
]


def detect(text: str) -> str | None:
    """SPDX id from the text of a LICENSE file, or None if not recognized."""
    for pat, spdx in _DETECT:
        m = re.search(pat, text, re.I)
        if m:
            return normalize(m.group(1)) if spdx is None else spdx
    return None


def source_page(uri: str) -> str:
    """Hugging Face dataset page for a resolve URL; the model's page for "polyhaven:<id>"; otherwise the URI itself."""
    if uri.startswith("polyhaven:"):
        from agentic_sim_world_gen import polyhaven_models

        return polyhaven_models.PAGE.format(name=uri.removeprefix("polyhaven:"))
    m = re.match(r"https://huggingface\.co/datasets/([^/]+/[^/]+)/", uri)
    return f"https://huggingface.co/datasets/{m.group(1)}" if m else uri


def _holder(uri: str) -> str:
    m = re.match(r"https://huggingface\.co/datasets/([^/]+)/", uri)
    if m:
        return m.group(1)
    if "omniverse-content-production" in uri or "/Isaac/" in uri:
        return "NVIDIA"
    return "unknown"


def report(scene: ResolvedScene) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    warnings = []
    origin_warnings = []  # the asset cannot be fetched again at this version (reproducibility, not licensing)
    for p in scene.placements:
        a = p.asset
        if a is None:
            continue
        o = scene.obj(p.object_id)
        lic = normalize(a.license)
        page = source_page(a.uri)
        holder = a.author or _holder(a.uri)
        name, needs_attr, note = KNOWN.get(lic, (lic, True, "unknown license: check before sharing"))
        org = origin(a.uri, a.source, a.id)
        item = {
            "object_id": o.id,
            "label": o.label,
            "asset": a.name,
            "source": page,
            "holder": holder,
            "uri": a.uri,
            "origin": org,
            "version": origin_label(org),
            "attribution": f'"{a.name}" by {holder} ({page}), {name}' if needs_attr else None,
        }
        if a.source == "polyhaven":  # CC0 asks for nothing, but the author and the version are kept
            from agentic_sim_world_gen import polyhaven_models

            item["credit"] = polyhaven_models.credit(a.name)
        groups.setdefault(lic, []).append(item)
        if note:
            warnings.append(f"{o.id} ({a.name}): {note}")
        if org.get("note") and not org["from"].startswith("Isaac Sim"):  # Isaac: release-level, shown as a badge
            origin_warnings.append(f"{o.id} ({a.name}): {org['note']}")
    credits: dict[tuple[str, str], dict[str, Any]] = {}
    for p in scene.placements:
        if p.asset is None:
            continue
        label = scene.obj(p.object_id).label or p.object_id
        for c in p.asset.credits:
            key = (str(c.get("title")), str(c.get("url") or ""))
            item = credits.setdefault(key, {
                "title": c.get("title"), "author": c.get("author"), "url": c.get("url"), "license": c.get("license"),
                "license_url": c.get("license_url"), "subject": c.get("subject"), "used_by": []})
            if label not in item["used_by"]:
                item["used_by"].append(label)
    from agentic_sim_world_gen.inputs import frame_note

    inputs = []
    for i in scene.spec.inputs:
        lic = normalize(i.license)
        name, needs_attr, _ = KNOWN.get(lic, (lic, True, ""))
        title = i.title or (i.path and i.path.rsplit("/", 1)[-1]) or i.id
        title += f" ({note})" if (note := frame_note(i)) else ""  # a video frame: which video, when
        inputs.append({"id": i.id, "kind": i.kind, "title": title, "author": i.author, "license": lic,
                       "source": i.url or i.path,
                       "credit": f'"{title}" by {i.author or "unknown"} ({i.url or i.path}), {name}'})
        if not i.url and i.kind in SOURCED_KINDS:
            warnings.append(f"input {i.id}: source URL not recorded (set url, or put <stem>.source.json next to the file)")
        if lic == "UNKNOWN" or (lic not in KNOWN):
            warnings.append(f"input {i.id}: license {lic!r} — make sure you may use this {i.kind}")
    screens = []
    for p in scene.placements:
        o = scene.obj(p.object_id)
        if o.screen is None or p.asset is None:
            continue
        for f in sorted(set(o.screen.images([x["material"] for x in p.asset.screens]).values())):
            src = screen_source(o.screen, f)
            lic = normalize(src["license"])
            name, _, _ = KNOWN.get(lic, (lic, True, ""))
            title = src["title"] or f.rsplit("/", 1)[-1]
            screens.append({"object_id": o.id, "label": o.label, "image": f, "title": title, "author": src["author"],
                            "license": lic, "url": src["url"],
                            "credit": f'"{title}" by {src["author"] or "unknown"} ({src["url"] or f}), {name}'})
            if lic == "UNKNOWN" or lic not in KNOWN:
                warnings.append(f"screen image {f.rsplit('/', 1)[-1]} on {o.id}: license {lic!r} — make sure you may "
                                "show it (set license, or put <stem>.source.json next to the image)")
    environment = []
    room = scene.spec.room
    if room.site is not None:  # the outside around the room: its sky
        sky = room.site.sky if room.site.sky is not None else LIGHTING[room.site.lighting].get("sky")
    else:
        sky = room.sky if room.sky is not None else LIGHTING[room.lighting].get("sky")
    if sky and sky != "none":
        url = sky_url(sky)
        bundled = sky in SKIES
        lic = "NVIDIA-Isaac-Sim" if bundled else "UNKNOWN"
        environment.append({"id": "sky", "uri": url, "license": lic,
                            "credit": f"sky {sky!r}: {url}" + (" (Isaac Sim bundled HDRI)" if bundled else "")})
        if not bundled:
            warnings.append(f"sky image {url}: license not recorded — make sure you may use it")
    materials = _materials(scene, warnings)
    sources: dict[str, dict[str, Any]] = {}
    for items in groups.values():
        for i in items:
            g = sources.setdefault(i["origin"]["from"], {"from": i["origin"]["from"], "versions": {}, "count": 0})
            g["count"] += 1
            v = g["versions"].setdefault(origin_label(i["origin"], with_from=False), [])
            v.append(i["label"] or i["object_id"])
    for m in materials:  # CC0 PBR textures: where they were downloaded from, and which version
        if o := m.get("origin"):
            g = sources.setdefault(o["from"], {"from": o["from"], "versions": {}, "count": 0})
            g["count"] += 1
            ver = f"{m['id']} {o.get('version') or 'unversioned'} — {', '.join(o.get('download') or [o.get('url') or ''])}"
            g["versions"].setdefault(ver, []).append(m["surface"])
    return {
        "sources": list(sources.values()),
        "inputs": inputs,
        "screens": screens,
        "environment": environment,
        "materials": materials,
        "licenses": {k: len(v) for k, v in groups.items()},
        "attribution_required": any(i["attribution"] for v in groups.values() for i in v),
        "warnings": warnings,
        "origin_warnings": origin_warnings,
        "assets": groups,
        "credits": list(credits.values()),
    }


def _materials(scene: ResolvedScene, warnings: list[str]) -> list[dict[str, Any]]:
    """The MDL materials of the room surfaces (floor, walls incl. accent walls and trim, ceiling, outdoor areas / buildings) with their licenses."""
    from agentic_sim_world_gen import materials, pbr
    from agentic_sim_world_gen.scene.looks import resolve_surface
    from agentic_sim_world_gen.scene.walls import default_value, overrides

    room = scene.spec.room
    rect = getattr(room, "plan", None) is None
    surfs = [("floor", room.floor), ("walls", default_value(room.walls))]
    surfs += [(f"wall {k}", v) for k, v in overrides(room.walls, rect).items()]
    surfs += [(k, t.surface) for k, t in (("wainscot", getattr(room, "wainscot", None)),
                                          ("baseboard", getattr(room, "baseboard", None))) if t is not None]
    if room.ceiling and room.ceiling is not True:
        surfs.append(("ceiling", room.ceiling))
    from agentic_sim_world_gen.scene.zone_floors import zone_floors

    surfs += [(f"floor {z['id']}", z["value"]) for z in zone_floors(room)]
    surfs += [(f"floor area {a.id}", a.floor) for a in getattr(room, "floor_areas", None) or [] if a.floor is not None]
    surfs += [(f"column {c.id}", c.surface) for c in getattr(room, "columns", None) or [] if c.surface is not None]
    surfs += [(f"area {a.id}", a.surface) for a in getattr(room, "areas", None) or [] if getattr(a, "surface", None)]
    surfs += [(f"building {b.id}", b.surface) for b in getattr(room, "buildings", None) or [] if getattr(b, "surface", None)]
    if scene.spec.sdg is not None:  # SDG material randomization candidates (built into the USD too)
        from agentic_sim_world_gen.sdg import material_candidates

        mats, _ = material_candidates(room, scene.spec.sdg)
        surfs += [(f"sdg {t}", c["surface"]) for t, cands in mats.items() for c in cands
                  if c["path"] and c["surface"].get("mdl")]
    out = []
    for where, surf in surfs:
        try:
            flat = resolve_surface(surf, "concrete")
        except ValueError:
            continue
        pbr_ref = next((flat[k] for k in ("texture", "normal", "roughness_texture")
                        if str(flat.get(k) or "").startswith(pbr.REF)), None)
        if pbr_ref:  # CC0 PBR textures (ambientCG / Poly Haven)
            m = pbr.manifest_of(pbr_ref)
            files = m.get("files") or {}
            out.append({"surface": where, "id": m["id"], "library": m["library"], "license": m.get("license") or pbr.LICENSE,
                        "credit": pbr.credit(m),
                        "origin": {"from": f"{m['library']} (CC0 PBR)", "url": m.get("url"), "download": m.get("download"),
                                   "version": m.get("version"), "files": files, "fetched": m.get("fetched")}})
            if not files:
                warnings.append(f"{where} material {m['id']}: textures not downloaded yet (they are fetched when the "
                                "scene is composed)")
            continue
        if not flat.get("mdl"):
            continue
        r = materials.by_mdl(flat["mdl"], flat.get("mdl_name"))
        if r is None:
            lic = "UNKNOWN"
            warnings.append(f"{where} material {flat['mdl']}: license not recorded — make sure you may use it")
            credit = f"{flat.get('mdl_name') or ''} {flat['mdl']}".strip()
        else:
            lic = r["license"]
            credit = (f"\"{r.get('title') or r['name']}\" ({r['id']}), NVIDIA "
                      + ("vMaterials 2" if r["library"] == "vMaterials_2" else "Omniverse Base materials")
                      + f", {flat['mdl']}")
        out.append({"surface": where, "mdl": flat["mdl"], "mdl_name": flat.get("mdl_name"),
                    "id": r and r["id"], "license": lic, "credit": credit})
    return out


# how each input kind is named in LICENSES.md
INPUT_KINDS = {"reference": "reference image for the look only / 参考画像"}


def to_markdown(scene: ResolvedScene, r: dict[str, Any]) -> str:
    lines = [f"# Asset licenses — {scene.spec.name}", ""]
    if r["warnings"]:
        lines += ["## Warnings", "", *[f"- {w}" for w in r["warnings"]], ""]
    if r.get("inputs"):
        lines += ["## Inputs (photos / reference images / drawings the scene was made from)", ""]
        lines += [f"- {i['id']} ({INPUT_KINDS.get(i['kind'], i['kind'])}): {i['credit']}" for i in r["inputs"]]
        lines.append("")
    if r.get("screens"):
        lines += ["## Screen images (shown on signage instead of the asset's own)", ""]
        lines += [f"- {i['label'] or i['object_id']}: {i['credit']}" for i in r["screens"]]
        lines.append("")
    if r.get("environment"):
        lines += ["## Environment (sky)", ""]
        lines += [f"- {e['credit']} — {KNOWN.get(e['license'], (e['license'],))[0]}" for e in r["environment"]]
        lines.append("")
    if r.get("materials"):
        lines += ["## Materials (floor / walls / ceiling: MDL, CC0 PBR textures)", ""]
        lines += [f"- {m['surface']}: {m['credit']} — {KNOWN.get(m['license'], (m['license'],))[0]}" for m in r["materials"]]
        lines.append("")
    if r.get("sources"):
        lines += ["## Sources (where each asset came from, and which version)", ""]
        lines += [f"- warning: {w}" for w in r.get("origin_warnings") or []]
        for g in r["sources"]:
            lines.append(f"- {g['from']} ({g['count']})")
            for ver, used in g["versions"].items():
                names = ", ".join(used[:5]) + (f" and {len(used) - 5} more" if len(used) > 5 else "")
                lines.append(f"  - {ver}: {names}")
        lines.append("")
    for lic, items in r["assets"].items():
        name = KNOWN.get(lic, (lic,))[0]
        lines += [f"## {name} ({len(items)})", ""]
        for i in items:
            lines.append(f"- {i['label'] or i['object_id']}: {i['attribution'] or i.get('credit') or i['asset'] + ' — ' + i['source']}")
            o = i.get("origin") or {}
            sha = f", sha256 {o['sha256']}" if o.get("sha256") else ""
            lines.append(f"  - version: {i['version']} — {o.get('url') or i['uri']}{sha}")
        lines.append("")
    if r.get("credits"):
        lines += ["## Material used in the assets (texture photos, fonts)", ""]
        for c in r["credits"]:
            src = f" ({c['url']})" if c.get("url") else ""
            lic = c.get("license") or "license not recorded"
            if c.get("license_url"):
                lic = f"[{lic}]({c['license_url']})"
            used = c["used_by"]
            names = ", ".join(used[:5]) + (f" and {len(used) - 5} more" if len(used) > 5 else "")
            lines.append(f'- "{c["title"]}" by {c.get("author") or "unknown"}{src}, {lic} — used by {names}')
        lines.append("")
    return "\n".join(lines)

# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2,<2", "httpx>=0.27"]
# ///
"""MCP server for QIDI printers: QIDI Studio CLI slicing + Moonraker. Config via env, see README."""
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
from functools import lru_cache
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP, Image

STUDIO = os.environ.get("QIDI_STUDIO", "/Applications/QIDIStudio.app/Contents/MacOS/QIDIStudio")
APPDIR = Path(os.environ.get("QIDI_STUDIO_DATA", Path.home() / "Library/Application Support/QIDIStudio")).expanduser()
USER = APPDIR / "user/default"
if os.environ.get("QIDI_PRESETS"):
    SYSTEM_ROOTS = [Path(os.environ["QIDI_PRESETS"]).expanduser()]
else:
    _sys = APPDIR / "system"
    SYSTEM_ROOTS = sorted(d for d in _sys.iterdir() if d.is_dir()) if _sys.is_dir() else []
PRESET_ROOTS = [USER, *SYSTEM_ROOTS]

DENSITY = {"PLA": 1.24, "PETG": 1.27, "ABS": 1.04, "ASA": 1.07, "TPU": 1.21, "PA": 1.14, "PC": 1.20, "PET": 1.30}
FILAMENT_D = 1.75

# Support setups. Tuned for single-material PLA/PETG on a 0.4 nozzle, no soluble interface.
_EASY = {
    "enable_support": "1",
    "support_type": "normal(auto)",
    "support_style": "grid",
    "support_threshold_angle": "35",       # PLA holds ~55° overhangs, no need to support them
    "support_top_z_distance": "0.25",      # bigger air gap = peels off clean
    "support_bottom_z_distance": "0.25",
    "support_object_xy_distance": "0.5",
    "support_interface_top_layers": "2",
    "support_interface_bottom_layers": "0",
    "support_interface_spacing": "0.5",
    "support_interface_pattern": "rectilinear",
    "support_base_pattern": "rectilinear",
    "support_base_pattern_spacing": "3",
    "support_remove_small_overhang": "1",
    "bridge_no_support": "1",
}
SUPPORTS = {
    "none": {"enable_support": "0"},
    "easy": _EASY,
    "buildplate": {**_EASY, "support_on_build_plate_only": "1"},
    "clean": {**_EASY, "support_style": "snug", "support_threshold_angle": "30",
              "support_top_z_distance": "0.18", "support_object_xy_distance": "0.35",
              "support_interface_top_layers": "3", "support_interface_spacing": "0.25"},
    "tall": {**_EASY, "support_threshold_angle": "30", "support_base_pattern_spacing": "2",
             "support_interface_bottom_layers": "2", "support_speed": "80",
             "brim_type": "outer_only", "brim_width": "5"},
    "painted": {**_EASY, "support_type": "normal(manual)"},
    "tree": {"enable_support": "1", "support_type": "tree(auto)", "support_style": "tree_hybrid",
             "support_threshold_angle": "35", "support_top_z_distance": "0.2",
             "support_object_xy_distance": "0.5", "tree_support_wall_count": "1",
             "tree_support_branch_diameter": "3"},
}
BRIM = {"auto": "auto_brim", "none": "no_brim", "outer": "outer_only", "inner": "inner_only",
        "both": "outer_and_inner"}

COLOR_NAMES = {
    "black": (0, 0, 0), "white": (255, 255, 255), "gray": (128, 128, 128), "light gray": (200, 200, 200),
    "dark gray": (64, 64, 64), "red": (220, 30, 30), "dark red": (128, 0, 0), "pink": (255, 113, 116),
    "light pink": (255, 182, 193), "magenta": (230, 0, 170), "purple": (128, 0, 160), "lavender": (190, 160, 230),
    "blue": (20, 60, 220), "sky blue": (30, 144, 255), "navy": (0, 0, 110), "light blue": (130, 190, 240),
    "cyan": (0, 200, 220), "teal": (0, 128, 128), "green": (30, 160, 50), "dark green": (0, 90, 30),
    "lime": (160, 230, 40), "olive": (128, 128, 0), "yellow": (250, 220, 0), "gold": (212, 175, 55),
    "orange": (250, 130, 0), "brown": (120, 70, 30), "beige": (230, 210, 170), "silver": (175, 180, 185),
}

mcp = FastMCP("qidi")


def _color_name(hex_str: str) -> str:
    h = hex_str.lstrip("#")[:6]
    try:
        rgb = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return "?"
    return min(COLOR_NAMES, key=lambda n: sum((a - b) ** 2 for a, b in zip(rgb, COLOR_NAMES[n])))


def _fmt_time(s: float) -> str:
    s = int(s)
    return f"{s // 3600}h {s % 3600 // 60}m"


# ---------- presets ----------

@lru_cache(maxsize=1)
def _index() -> dict[str, list[dict]]:
    """All presets by kind: [{name, path, compat, user}]. User presets come first."""
    idx: dict[str, list[dict]] = {"machine": [], "process": [], "filament": []}
    raw: dict[str, dict] = {}
    for root in PRESET_ROOTS:
        for kind in idx:
            d = root / kind
            if not d.is_dir():
                continue
            for f in sorted(d.glob("*.json")):
                try:
                    j = json.loads(f.read_text())
                except Exception:
                    continue
                name = j.get("name", f.stem)
                if j.get("type") == "machine_model":   # model description, not a printer preset
                    continue
                raw.setdefault(f"{kind}:{name}", j)
                if j.get("instantiation", "true") == "false":   # base presets, not selectable
                    continue
                idx[kind].append({"name": name, "stem": f.stem, "path": f, "json": j, "user": root == USER})

    def compat(kind, j, depth=0):
        if j.get("compatible_printers") or depth > 5 or not j.get("inherits"):
            return j.get("compatible_printers") or []
        return compat(kind, raw.get(f"{kind}:{j['inherits']}", {}), depth + 1)

    for kind, items in idx.items():
        for it in items:
            it["compat"] = compat(kind, it.pop("json"))
    return idx


def _preset(kind: str, name: str) -> Path:
    for it in _index()[kind]:
        if name in (it["name"], it["stem"]):
            return it["path"]
    raise ValueError(f"{kind} preset not found: {name!r}. Use list_presets('{kind}').")


def _studio_selection() -> dict:
    """What's currently selected in the QIDI Studio app."""
    try:
        p = json.loads((APPDIR / "QIDIStudio.conf").read_text()).get("presets", {})
        fil = p.get("filaments") or []
        return {"machine": p.get("machine"), "process": p.get("process"), "filament": fil[0] if fil else None}
    except Exception:
        return {}


def _resolve_machine(name: str) -> str:
    names = [it["name"] for it in _index()["machine"]]
    stems = {it["stem"]: it["name"] for it in _index()["machine"]}
    if name in names:
        return name
    if name in stems:
        return stems[name]
    for cand in (f"{name} 0.4 nozzle", name.replace("Qidi ", "") + " 0.4 nozzle"):
        if cand in names:
            return cand
    low = name.lower().replace("-", "").replace(" ", "").removeprefix("qidi")
    flat = lambda n: n.lower().replace("-", "").replace(" ", "")
    hits = [n for n in names if flat(n).startswith(low)]
    if not hits:
        hits = [n for n in names if flat(n).startswith("x" + low)]
    if hits:
        return sorted(hits, key=lambda n: ("0.4" not in n, n))[0]
    raise ValueError(f"unknown printer model {name!r}. Known: {', '.join(sorted({n.rsplit(' ', 2)[0] for n in names}))}")


def _pick_process(machine: str, quality: str = "0.20mm Standard") -> str:
    items = [it["name"] for it in _index()["process"] if machine in it["compat"]]
    if not items:
        raise ValueError(f"no process presets compatible with {machine!r}")
    for pref in (quality, "0.20mm Standard", "0.20mm"):
        hit = [n for n in items if n.startswith(pref)]
        if hit:
            return sorted(hit, key=len)[0]
    return items[0]


def _pick_filament(machine: str, material: str = "PLA") -> str:
    items = [it for it in _index()["filament"] if machine in it["compat"]]
    mat = material.upper()
    for test in (lambda n: n.upper().startswith(f"QIDI {mat} BASIC"),
                 lambda n: n.upper().startswith(f"GENERIC {mat} @") or n.upper().startswith(f"GENERIC {mat}@"),
                 lambda n: n.upper().startswith(f"QIDI {mat} "),
                 lambda n: re.search(rf"\b{re.escape(mat)}\b", n.upper())):
        hit = [it for it in items if test(it["name"])]
        if hit:
            hit.sort(key=lambda it: (not it["user"], len(it["name"])))
            return hit[0]["name"]
    raise ValueError(f"no {material} filament preset compatible with {machine!r}, see list_presets('filament', '{material}')")


@mcp.tool()
def list_presets(kind: str = "process", contains: str = "", printer_model: str = "") -> str:
    """List preset names. kind: machine | process | filament. Filter by substring and/or printer model
    (e.g. printer_model="X-Plus 3" shows only presets that work on it)."""
    machine = _resolve_machine(printer_model) if printer_model else None
    names = sorted({it["name"] for it in _index()[kind]
                    if contains.lower() in it["name"].lower()
                    and (machine is None or kind == "machine" or machine in it["compat"])})
    if kind == "machine" and machine:
        names = [n for n in names if n.startswith(machine.rsplit(" ", 2)[0])]
    return "\n".join(names) or "(none)"


# ---------- printers ----------

def _printers() -> dict[str, dict]:
    """Known printers: QIDI_PRINTERS / QIDI_HOST env, plus physical printers saved in QIDI Studio."""
    out: dict[str, dict] = {}
    for part in filter(None, os.environ.get("QIDI_PRINTERS", "").split(",")):
        name, _, rest = part.strip().partition("=")
        host, _, model = rest.partition(":")
        out[name] = {"host": host.strip(), "model": model.strip() or None, "source": "env"}
    if os.environ.get("QIDI_HOST"):
        out.setdefault("default", {"host": os.environ["QIDI_HOST"].strip(),
                                   "model": os.environ.get("QIDI_MACHINE"), "source": "env"})
    pdir = USER / "physical_printer"
    if pdir.is_dir():
        for f in sorted(pdir.glob("*.json")):
            try:
                j = json.loads(f.read_text())
            except Exception:
                continue
            host = (j.get("print_host") or "").replace("http://", "").strip("/")
            if host and not any(p["host"] == host for p in out.values()):
                out[j.get("name", f.stem)] = {"host": host, "model": j.get("preset_name"), "source": "QIDI Studio"}
    return out


def _printer(printer: str = "") -> tuple[str, dict]:
    ps = _printers()
    if not ps:
        raise RuntimeError("no printer configured: set QIDI_HOST=<ip> (or add the printer in QIDI Studio)")
    if printer:
        for name, p in ps.items():
            if printer in (name, p["host"]):
                return name, p
        raise ValueError(f"unknown printer {printer!r}. Known: {', '.join(ps)}")
    name = "default" if "default" in ps else next(iter(ps))
    return name, ps[name]


def _req(method: str, path: str, timeout: float, printer: str = "", **params):
    _, p = _printer(printer)
    try:
        r = httpx.request(method, f"http://{p['host']}{path}", params=params, timeout=timeout)
    except (httpx.ConnectError, httpx.ConnectTimeout):
        raise RuntimeError(f"printer at {p['host']} is not reachable (off, or dropped off wifi?)") from None
    r.raise_for_status()
    return r.json().get("result")


def _get(path: str, printer: str = "", **params):
    return _req("GET", path, 10, printer, **params)


def _post(path: str, printer: str = "", **params):
    return _req("POST", path, 30, printer, **params)


def _gcode(script: str, printer: str = ""):
    return _post("/printer/gcode/script", printer, script=script)


def _query(*objects: str, printer: str = "") -> dict:
    return _get("/printer/objects/query", printer, **{o: None for o in objects})["status"]


def _objects(printer: str = "") -> list[str]:
    return _get("/printer/objects/list", printer)["objects"]


def _is_printing(printer: str = "") -> bool:
    return _query("print_stats", printer=printer)["print_stats"].get("state") in ("printing", "paused")


@mcp.tool()
def list_printers() -> str:
    """Printers this server knows about, with model, address and whether they're online right now."""
    lines = []
    for name, p in _printers().items():
        try:
            r = httpx.get(f"http://{p['host']}/printer/objects/query?print_stats", timeout=3)
            state = r.json()["result"]["status"]["print_stats"].get("state", "?")
        except Exception:
            state = "offline"
        lines.append(f"{name}: {p.get('model') or '?'} at {p['host']} - {state} (from {p['source']})")
    return "\n".join(lines) or "no printers configured"


# ---------- slicing ----------

@mcp.tool()
def slice(
    model_paths: list[str],
    supports: str = "none",
    material: str = "",
    infill: int | None = None,
    walls: int | None = None,
    layer_height: float | None = None,
    brim: str | None = None,
    copies: int = 1,
    scale: float = 1.0,
    rotate_z: float = 0.0,
    auto_orient: bool = False,
    settings: dict[str, str] | None = None,
    printer: str = "",
    machine: str = "",
    process: str = "",
    filament: str = "",
    output_dir: str = "",
) -> str:
    """Slice STL/3MF files with QIDI Studio. Nothing is sent to the printer.

    Which printer: `printer` (a name from list_printers) or `machine` (model or preset, e.g. "X-Plus 3",
    "Q1 Pro 0.6 nozzle"). If neither is given, whatever is selected in QIDI Studio is used.
    material: PLA / PETG / ABS / ASA / TPU / PA... picks a matching filament preset for that printer.
    process / filament: exact preset names if you want something specific.

    supports - pick by the part:
      none        no supports (default)
      easy        normal grid supports, wide air gaps, peel off by hand. Good default when a part needs support.
      buildplate  like easy but only from the bed, never on top of the part. Least material.
      clean       snug supports, small gap, 3 interface layers. Nicer underside, harder to remove.
      tall        like easy but denser base + brim, for tall/thin support towers that could snap or tip over.
      painted     only where supports are painted/enforced in a 3MF (normal(manual)).
      tree        tree supports (hybrid). Less contact on organic shapes, but more stringy and fragile.
    infill: sparse infill %, walls: wall loops, layer_height: mm, brim: auto|none|outer|inner|both.
    copies: copies of each model, scale: 1.02 = +2%, rotate_z: degrees.
    settings: raw QIDI Studio process keys applied last, e.g. {"top_shell_layers": "5"}.

    Returns gcode path, .gcode.3mf path (has the plate preview the printer screen shows),
    print time, filament grams and slicer warnings."""
    if supports not in SUPPORTS:
        raise ValueError(f"supports must be one of: {', '.join(SUPPORTS)}")
    paths = [str(Path(p).expanduser().resolve()) for p in model_paths]
    for p in paths:
        if not Path(p).is_file():
            raise ValueError(f"model not found: {p}")
    paths = [p for p in paths for _ in range(max(1, copies))]

    sel = _studio_selection()
    if machine:
        machine = _resolve_machine(machine)
    elif printer:
        model = _printer(printer)[1].get("model")
        if not model:
            raise ValueError(f"don't know the model of printer {printer!r}, pass machine=")
        machine = _resolve_machine(model)
    elif os.environ.get("QIDI_MACHINE"):
        machine = _resolve_machine(os.environ["QIDI_MACHINE"])
    elif sel.get("machine"):
        machine = _resolve_machine(sel["machine"])
    else:
        raise ValueError("no printer model: pass machine= or select a printer in QIDI Studio")
    same_as_app = sel.get("machine") and _resolve_machine(sel["machine"]) == machine

    if not process:
        process = os.environ.get("QIDI_PROCESS") or (sel.get("process") if same_as_app else None) \
            or _pick_process(machine)
    if not filament:
        if material:
            filament = _pick_filament(machine, material)
        else:
            filament = os.environ.get("QIDI_FILAMENT") or (sel.get("filament") if same_as_app else None) \
                or _pick_filament(machine, "PLA")

    over = dict(SUPPORTS[supports])
    if infill is not None:
        over["sparse_infill_density"] = f"{infill}%"
    if walls is not None:
        over["wall_loops"] = str(walls)
    if layer_height is not None:
        over["layer_height"] = str(layer_height)
    if brim is not None:
        if brim not in BRIM:
            raise ValueError(f"brim must be one of: {', '.join(BRIM)}")
        over["brim_type"] = BRIM[brim]
    over.update({k: str(v) for k, v in (settings or {}).items()})

    work = Path(tempfile.mkdtemp(prefix="qidi-slice-"))
    proc = json.loads(_preset("process", process).read_text())
    proc.update(over)
    proc.update({"name": f"{process} (mcp)", "from": "User", "inherits": process})
    proc.pop("setting_id", None)
    proc.pop("instantiation", None)
    (work / "process.json").write_text(json.dumps(proc, indent=1))

    stem = Path(paths[0]).stem
    out = Path(output_dir).expanduser() if output_dir else Path(paths[0]).parent / "gcode"
    out.mkdir(parents=True, exist_ok=True)
    # some QIDI system presets ship without "from", which the CLI rejects; copy them and fill it in
    for kind, name in (("machine", machine), ("filament", filament)):
        src = _preset(kind, name)
        j = json.loads(src.read_text())
        j.setdefault("from", "User" if USER in src.parents else "system")
        (work / f"{kind}.json").write_text(json.dumps(j, indent=1))
    cmd = [
        STUDIO,
        "--load-settings", f"{work / 'machine.json'};{work / 'process.json'}",
        "--load-filaments", str(work / "filament.json"),
        "--slice", "0",
        "--arrange", "1",
        "--orient", "1" if auto_orient else "0",
        "--outputdir", str(work),
        "--export-3mf", f"{stem}.gcode.3mf",
    ]
    if scale != 1.0:
        cmd += ["--scale", str(scale)]
    if rotate_z:
        cmd += ["--rotate", str(rotate_z)]
    r = subprocess.run(cmd + paths, capture_output=True, text=True, timeout=900)
    res_file = work / "result.json"
    if not res_file.exists():
        tail = "\n".join((r.stdout + r.stderr).splitlines()[-15:])
        raise RuntimeError(f"slice failed (exit {r.returncode}):\n{tail}")
    res = json.loads(res_file.read_text())
    if res.get("return_code", 1) != 0:
        errs = [l for l in (r.stdout + r.stderr).splitlines() if "[error]" in l][-5:]
        raise RuntimeError(f"slice failed: {res.get('error_string')}\n" + "\n".join(errs))

    lines, total_s, grams = [], 0.0, 0.0
    mat = next((k for k in sorted(DENSITY, key=len, reverse=True) if k in filament.upper()), "PLA")
    plates = res.get("sliced_plates", [])
    for plate in plates:
        pid = plate.get("id", 1)
        dst = out / (f"{stem}.gcode" if len(plates) == 1 else f"{stem}_plate{pid}.gcode")
        shutil.move(work / f"plate_{pid}.gcode", dst)
        m = re.search(r"; filament used \[cm3\] = ([\d.]+)", dst.read_text(errors="ignore"))
        g = float(m.group(1)) * DENSITY[mat] if m else 0.0
        t = float(plate.get("total_predication", 0))
        total_s += t
        grams += g
        lines.append(f"plate {pid}: {dst}  ({_fmt_time(t)}, ~{g:.1f} g {mat}, {len(plate.get('objects', []))} objects)")
        if plate.get("warning_message"):
            lines.append(f"  warning: {plate['warning_message']}")
    g3mf = out / f"{stem}.gcode.3mf"
    shutil.move(work / f"{stem}.gcode.3mf", g3mf)
    shutil.rmtree(work, ignore_errors=True)
    lines.append(f"for printer (with preview): {g3mf}")
    lines.append(f"total {_fmt_time(total_s)}, ~{grams:.1f} g | supports={supports} | "
                 f"{machine} / {process} / {filament}")
    shown = {k: v for k, v in over.items() if k not in SUPPORTS[supports]}
    if shown:
        lines.append(f"overrides: {shown}")
    return "\n".join(lines)


# ---------- printer tools ----------

@mcp.tool()
def printer_status(printer: str = "") -> str:
    """Printer state, temps, current job, progress, layer and time left."""
    q = _query("print_stats", "extruder", "heater_bed", "virtual_sdcard", printer=printer)
    ps, ex, bed = q.get("print_stats", {}), q.get("extruder", {}), q.get("heater_bed", {})
    prog = q.get("virtual_sdcard", {}).get("progress", 0) or 0
    name, p = _printer(printer)
    lines = [f"{name} ({p.get('model') or '?'}): {ps.get('state')} | file: {ps.get('filename') or '-'} | {prog * 100:.1f}%",
             f"nozzle {ex.get('temperature', 0):.0f}/{ex.get('target', 0):.0f}°C, "
             f"bed {bed.get('temperature', 0):.0f}/{bed.get('target', 0):.0f}°C"]
    objs = _objects(printer)
    for obj in ("heater_generic chamber", "temperature_sensor chamber", "temperature_sensor Chamber"):
        if obj in objs:
            c = _query(obj, printer=printer).get(obj, {})
            tgt = c.get("target")
            lines[-1] += f", chamber {c.get('temperature', 0):.0f}" + (f"/{tgt:.0f}" if tgt is not None else "") + "°C"
            break

    info = ps.get("info") or {}
    if info.get("total_layer"):
        lines.append(f"layer {info.get('current_layer')}/{info['total_layer']}")
    done = ps.get("print_duration") or 0
    if ps.get("state") in ("printing", "paused") and done:
        left = None
        try:
            est = _get("/server/files/metadata", printer, filename=ps["filename"]).get("estimated_time")
            if est:
                left = max(est - done, 0)
        except Exception:
            pass
        if left is None and prog > 0.02:
            left = done / prog - done
        line = f"printing for {_fmt_time(done)}"
        if left is not None:
            eta = time.strftime("%H:%M", time.localtime(time.time() + left))
            line += f", ~{_fmt_time(left)} left, done around {eta}"
        lines.append(line)
    if ps.get("message"):
        lines.append(f"message: {ps['message']}")
    return "\n".join(lines)


@mcp.tool()
def camera_snapshot(printer: str = "") -> Image:
    """Take a photo from the printer camera. Use it to check the first layer, spaghetti, warping, etc."""
    _, p = _printer(printer)
    urls = []
    try:
        for cam in _get("/server/webcams/list", printer).get("webcams", []):
            if cam.get("snapshot_url"):
                urls.append(cam["snapshot_url"])
    except Exception:
        pass
    urls += ["/webcam/?action=snapshot", ":8080/?action=snapshot"]
    last = None
    for u in urls:
        url = u if u.startswith("http") else f"http://{p['host']}{u}"
        try:
            r = httpx.get(url, timeout=10)
            ctype = r.headers.get("content-type", "")
            if r.status_code == 200 and ctype.startswith("image"):
                return Image(data=r.content, format="png" if "png" in ctype else "jpeg")
            last = f"{url}: {r.status_code} {ctype}"
        except Exception as e:
            last = f"{url}: {e}"
    raise RuntimeError(f"no camera snapshot found ({last}). Not every QIDI model has a camera.")


@mcp.tool()
def filament_box_status(printer: str = "") -> str:
    """QIDI Box: what's in each slot (material, color, temps), which slot is loaded, box humidity/temp, drying.
    On printers without a Box, shows the filament runout sensor."""
    objs = _objects(printer)
    sensors = [o for o in objs if o.startswith("filament_switch_sensor") or o.startswith("filament_motion_sensor")]
    lines = []
    if "box_extras" in objs:
        bx = _query("box_extras", printer=printer)["box_extras"]
        loaded = bx.get("loaded_slot") or "-"
        lines.append(f"loaded: {loaded}")
        for b_i, box in enumerate(bx.get("boxes", [])):
            if not box.get("is_online", 1):
                continue
            target = box.get("box_target_temp") or 0
            lines.append(f"box {b_i + 1}: {box.get('aht20_temp', 0):.0f}°C, {box.get('aht20_humidity', 0)}% RH"
                         + (f", heating to {target:.0f}°C" if target else "")
                         + (" drying" if box.get("dry_state") else ""))
            info = {f["index"]: f for f in box.get("filament_info", [])}
            for i in range(4):
                slot = b_i * 4 + i
                f = info.get(i)
                mark = " <- loaded" if loaded == f"slot{slot}" else ""
                if f and box.get(f"box_inserted_{i}"):
                    color = (f.get("color_rgb_str") or ["?"])[0]
                    lines.append(f"  slot{slot}: {_color_name(color)} {f.get('filament_str', '')} "
                                 f"({f.get('vendor_str', '')}, {color}), {f.get('min_temp')}-{f.get('max_temp')}°C{mark}")
                else:
                    lines.append(f"  slot{slot}: empty{mark}")
    else:
        lines.append("no QIDI Box on this printer")
    if sensors:
        st = _query(*sensors, printer=printer)
        for s in sensors:
            v = st.get(s, {})
            if v.get("enabled", True):
                lines.append(f"{s.split(' ', 1)[-1]}: {'filament' if v.get('filament_detected') else 'EMPTY'}")
    return "\n".join(lines)


@mcp.tool()
def heat(nozzle: float | None = None, bed: float | None = None, chamber: float | None = None,
         force: bool = False, printer: str = "") -> str:
    """Set target temps (°C, 0 = off). Physical action: only when the user asks.
    Refuses while a print is running unless force=True."""
    if _is_printing(printer) and not force:
        raise RuntimeError("printer is printing, changing temps can ruin the job. Pass force=True if the user insists.")
    cmds = []
    if nozzle is not None:
        cmds.append(f"M104 S{nozzle:.0f}")
    if bed is not None:
        cmds.append(f"M140 S{bed:.0f}")
    if chamber is not None:
        if "heater_generic chamber" not in _objects(printer):
            raise RuntimeError("this printer has no heated chamber")
        cmds.append(f"SET_HEATER_TEMPERATURE HEATER=chamber TARGET={chamber:.0f}")
    if not cmds:
        return "nothing to set"
    _gcode("\n".join(cmds), printer)
    return "sent: " + "; ".join(cmds)


@mcp.tool()
def cooldown(printer: str = "") -> str:
    """Turn off nozzle, bed and chamber heaters. Refuses while printing."""
    if _is_printing(printer):
        raise RuntimeError("printer is printing, use cancel_print or pause_print instead")
    cmds = ["M104 S0", "M140 S0"]
    if "heater_generic chamber" in _objects(printer):
        cmds.append("SET_HEATER_TEMPERATURE HEATER=chamber TARGET=0")
    _gcode("\n".join(cmds), printer)
    return "heaters off"


@mcp.tool()
def list_macros(printer: str = "") -> str:
    """Klipper macros available on the printer (hidden _macros skipped)."""
    names = sorted(o.split(" ", 1)[1] for o in _objects(printer)
                   if o.startswith("gcode_macro ") and not o.split(" ", 1)[1].startswith("_"))
    return "\n".join(names) or "(none)"


@mcp.tool()
def run_macro(name: str, args: str = "", force: bool = False, printer: str = "") -> str:
    """Run a Klipper macro or G28/BED_MESH_CALIBRATE. Moves hardware: only when the user asks.
    Refuses while printing unless force=True."""
    if f"gcode_macro {name}" not in _objects(printer) and name.upper() not in ("G28", "BED_MESH_CALIBRATE"):
        raise ValueError(f"unknown macro {name!r}, see list_macros")
    if _is_printing(printer) and not force:
        raise RuntimeError("printer is printing. Pass force=True if the user insists.")
    _gcode(f"{name} {args}".strip(), printer)
    return f"ran {name} {args}".strip()


@mcp.tool()
def print_history(limit: int = 10, printer: str = "") -> str:
    """Recent print jobs: file, result, duration, filament. Plus lifetime totals."""
    jobs = _get("/server/history/list", printer, limit=limit, order="desc").get("jobs", [])
    area = math.pi * (FILAMENT_D / 2) ** 2 / 1000  # cm3 per mm of filament
    lines = []
    for j in jobs:
        when = time.strftime("%m-%d %H:%M", time.localtime(j.get("start_time", 0)))
        g = (j.get("filament_used") or 0) * area * DENSITY["PLA"]
        lines.append(f"{when}  {j.get('status', '?'):10} {_fmt_time(j.get('print_duration') or 0):>7}  "
                     f"~{g:.0f} g  {j.get('filename')}")
    try:
        t = _get("/server/history/totals", printer)["job_totals"]
        kg = t.get("total_filament_used", 0) * area * DENSITY["PLA"] / 1000
        lines.append(f"\ntotals: {int(t.get('total_jobs') or 0)} jobs, {_fmt_time(t.get('total_print_time', 0))} "
                     f"printing, ~{kg:.2f} kg filament (counted as PLA)")
    except Exception:
        pass
    return "\n".join(lines) or "(no history)"


@mcp.tool()
def list_printer_files(limit: int = 20, printer: str = "") -> str:
    """Files stored on the printer, newest first."""
    files = sorted(_get("/server/files/list", printer, root="gcodes"), key=lambda f: f.get("modified", 0), reverse=True)
    return "\n".join(f"{f['path']}  ({f['size'] / 1e6:.1f} MB)" for f in files[:limit]) or "(none)"


@mcp.tool()
def upload_file(path: str, start_print: bool = False, printer: str = "") -> str:
    """Upload a .gcode or .gcode.3mf to the printer (.gcode.3mf shows a preview on the printer screen).
    Set start_print=True only if the user asked to start printing."""
    _, pr = _printer(printer)
    p = Path(path).expanduser()
    if not p.is_file() or not (p.name.endswith(".gcode") or p.name.endswith(".gcode.3mf")):
        raise ValueError("need an existing .gcode or .gcode.3mf file (use slice first)")
    try:
        with p.open("rb") as fh:
            r = httpx.post(f"http://{pr['host']}/server/files/upload", files={"file": (p.name, fh)},
                           data={"print": "true" if start_print else "false"}, timeout=300)
    except (httpx.ConnectError, httpx.ConnectTimeout):
        raise RuntimeError(f"printer at {pr['host']} is not reachable") from None
    r.raise_for_status()
    return f"uploaded {p.name}" + (" and print STARTED" if start_print else " (not started)")


@mcp.tool()
def start_print(filename: str, printer: str = "") -> str:
    """Start printing a file that is already on the printer. Only call when the user asks."""
    _post("/printer/print/start", printer, filename=filename)
    return f"print started: {filename}"


@mcp.tool()
def pause_print(printer: str = "") -> str:
    """Pause the current print."""
    _post("/printer/print/pause", printer)
    return "paused"


@mcp.tool()
def resume_print(printer: str = "") -> str:
    """Resume a paused print."""
    _post("/printer/print/resume", printer)
    return "resumed"


@mcp.tool()
def cancel_print(printer: str = "") -> str:
    """Cancel the current print. Can't be undone, only call when the user asks."""
    _post("/printer/print/cancel", printer)
    return "cancelled"


if __name__ == "__main__":
    mcp.run()

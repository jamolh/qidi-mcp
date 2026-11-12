# qidi-mcp

MCP server for QIDI printers. Slices models through the QIDI Studio CLI and talks to the printer over Moonraker, so you can do things like "slice this in PETG with easy supports, upload it and show me the first layer" from Claude.

## Supported printers

All the Klipper based QIDI printers that QIDI Studio supports:

```
Q1 Pro, Q2, Q2C
X-Smart 3, X-Plus 3, X-Max 3
X-Plus 4, X-Max 4
```

Slicing is tested on all of them. Printer control is tested on a Q2; the rest run the same Moonraker API so it should just work, but if something's off on your model open an issue. Camera, heated chamber and QIDI Box tools check what the printer actually has.

Older Marlin QIDIs (X-Plus / X-Max gen 1-2, X-Pro, i-Fast etc.) aren't supported. They use QIDI Print and a different network protocol.

## Tools

Slicing:

- `slice` - slice STL/3MF for any of the models above. Supports profile, material, infill, walls, layer height, brim, copies, scale, rotation, or any raw process key. Gives back a .gcode and a .gcode.3mf (same thing with the plate preview, shows up on the printer screen), print time, grams and slicer warnings.
- `list_presets` - machine / process / filament presets, optionally only the ones that fit a given model

Printer:

- `list_printers` - every printer it knows about and whether it's online
- `printer_status` - state, temps, layer, progress, time left and ETA
- `camera_snapshot` - photo from the printer camera
- `filament_box_status` - QIDI Box: material and color per slot, loaded slot, humidity, drying. Runout sensor on printers without a Box
- `print_history` - recent jobs and lifetime totals
- `list_printer_files`, `upload_file`
- `start_print`, `pause_print`, `resume_print`, `cancel_print`
- `heat`, `cooldown` - preheat or turn heaters off
- `list_macros`, `run_macro` - Klipper macros (bed mesh, input shaper, whatever your config has)

Every printer tool takes an optional `printer` argument when you have more than one.

Upload doesn't start a print unless you pass `start_print=true`. `heat` and `run_macro` refuse to run while a print is going unless `force=true`.

## Which presets get used

If you don't say anything, `slice` uses whatever printer, process and filament are selected in QIDI Studio right now. Otherwise:

- `machine="X-Plus 3"` (or `"Q1 Pro 0.6 nozzle"`) or `printer="<name>"` picks the printer
- `material="PETG"` picks a matching filament for that printer (QIDI Basic first, then Generic)
- process defaults to `0.20mm Standard` for that printer
- `process=` / `filament=` take exact preset names if you want something specific

## Supports

`slice(supports=...)` takes one of these. Numbers are for PLA/PETG with a 0.4 nozzle and no separate interface material.

```
none        no supports (default)
easy        normal grid, 35° threshold, 0.25 mm top gap, 0.5 mm side gap, sparse 2 layer interface.
            Comes off by hand. Use this unless there's a reason not to.
buildplate  easy, but only grows from the bed, never from the part itself
clean       snug style, 0.18 mm gap, dense 3 layer interface. Better looking underside, harder to remove
tall        easy + denser base and a brim, for tall skinny supports that tend to snap or fall over
painted     normal(manual), only where supports are painted/enforced in the 3MF
tree        tree_hybrid. Fine for organic shapes, I find the normal ones more reliable
```

Rule of thumb: flat overhangs and bridges over ~10 mm get `easy`, anything where the support would sit on a finished surface gets `buildplate`, and downloaded 3MF projects with painted supports get `painted`.

## Setup

You need [uv](https://docs.astral.sh/uv/) and QIDI Studio. Dependencies are declared inline in `server.py`, uv installs them on first run.

Check the printer is reachable first:

```
curl http://<printer-ip>/printer/info
```

Claude Code:

```
git clone https://github.com/jamolh/qidi-mcp ~/.claude/mcp/qidi-mcp
claude mcp add qidi --scope user -- uv run --quiet --script ~/.claude/mcp/qidi-mcp/server.py
```

If your printer is already added in QIDI Studio (Device tab), that's it, the address is read from there. Otherwise add `-e QIDI_HOST=<printer-ip>`.

Claude Desktop or other clients:

```json
{
  "mcpServers": {
    "qidi": {
      "command": "uv",
      "args": ["run", "--quiet", "--script", "/path/to/qidi-mcp/server.py"],
      "env": { "QIDI_HOST": "192.168.1.50" }
    }
  }
}
```

## Config

All optional:

```
QIDI_HOST          printer ip, if it isn't saved in QIDI Studio
QIDI_PRINTERS      several printers: "q2=192.168.1.50:Q2,big=192.168.1.51:X-Max 3"
QIDI_MACHINE       default model/preset instead of QIDI Studio's current one
QIDI_PROCESS       default process preset
QIDI_FILAMENT      default filament preset
QIDI_STUDIO        QIDI Studio binary (default /Applications/QIDIStudio.app/Contents/MacOS/QIDIStudio)
QIDI_STUDIO_DATA   QIDI Studio data dir (default ~/Library/Application Support/QIDIStudio)
QIDI_PRESETS       system presets dir (default: every series under QIDI_STUDIO_DATA/system)
```

On Linux/Windows set `QIDI_STUDIO` and `QIDI_STUDIO_DATA`, the defaults are mac paths.

## Notes

- Overrides are applied by writing a temporary process preset that inherits from the one you picked, so anything you can set in QIDI Studio's process tab can go into `settings`.
- A few QIDI system presets (e.g. Q1 Pro filaments) are missing the `from` field and the CLI refuses them. The server works on temp copies and fills it in.
- `copies` passes the same file several times and lets the slicer arrange them. The CLI's own `--repetitions` doesn't work when slicing all plates.
- The CLI reports 0 g filament weight, so weight is calculated from the volume in the gcode and a density table (PLA 1.24, PETG 1.27, etc).
- If you get `No module named 'mcp.server.fastmcp'`, you're on mcp 2.x. Run it with `uv run --script` so the `mcp<2` pin is used.
- Moonraker on QIDI printers doesn't require auth on the LAN. Keep the printer off the public internet.

## License

MIT

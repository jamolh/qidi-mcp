<h1 align="center">qidi-mcp</h1>

<h3 align="center">Let your AI assistant slice, send and watch prints on your QIDI printer.</h3>

<p align="center">
  <a href="https://github.com/jamolh/qidi-mcp/blob/main/LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-555"></a>
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-3776ab">
  <img alt="Klipper" src="https://img.shields.io/badge/printers-Klipper%20QIDI-e85d2a">
</p>

qidi-mcp connects an AI app on your computer to QIDI Studio and to your printer. Tell the assistant what you want in plain words. It slices the model with the right supports and settings, tells you the print time and how much filament it takes, sends the job, and keeps an eye on it while it prints. It uses the printers and presets you already have in QIDI Studio.

**1. Tell it what you want**

```
You:  Slice bracket.stl in PETG, supports that come off easily, 4 walls.
      How long will it take?
```

**2. It slices with the settings that fit**

```
AI:   Sliced for Q2 0.4 nozzle / 0.20mm Standard / QIDI PETG Basic.
      Supports: easy (35° threshold, 0.25 mm top gap, sparse interface)
      Walls: 4
      Time: 2h 14m, about 31 g of PETG.
      Slot 2 in your QIDI Box has white PETG loaded. Send it?
```

**3. It sends the job and watches the print**

```
You:  Yes, start it and show me the first layer.
AI:   Printing, layer 3 of 142, nozzle 250 °C, bed 80 °C.
      Done around 16:40. Here's the camera: [photo]
```

## What you need

- **An AI app on your computer that can use MCP**, such as Claude Code, Claude Desktop, Codex, Cursor or VS Code. MCP is how these apps connect to other programs, and qidi-mcp is the connector for QIDI. Browser chats and phone apps can't use it, because it runs on your computer.
- **QIDI Studio** on macOS, Windows or Linux.
- **A Klipper-based QIDI printer** on the same network: Q1 Pro, Q2, Q2C, X-Smart 3, X-Plus 3, X-Max 3, Plus4 or Max4. Slicing works even without a printer.
- **About five minutes.** qidi-mcp is free. Your AI app uses whatever plan you already have.

Older Marlin QIDIs (X-Plus / X-Max gen 1-2, X-Pro, i-Fast and so on) aren't supported. They use QIDI Print and a different network protocol.

## Get started

**1. Install uv.** It's a small free tool that runs the connector and installs what it needs on first run: `curl -LsSf https://astral.sh/uv/install.sh | sh` on macOS and Linux, or `irm https://astral.sh/uv/install.ps1 | iex` in PowerShell on Windows.

**2. Get the code.**

```
git clone https://github.com/jamolh/qidi-mcp ~/.claude/mcp/qidi-mcp
```

**3. Add the connector to your AI app.**

- **Claude Code:**
  ```
  claude mcp add qidi --scope user -- uv run --quiet --script ~/.claude/mcp/qidi-mcp/server.py
  ```
- **Codex:**
  ```
  codex mcp add qidi -- uv run --quiet --script ~/.claude/mcp/qidi-mcp/server.py
  ```
- **Claude Desktop, Cursor and other apps:** add this to the app's MCP settings:
  ```json
  {
    "mcpServers": {
      "qidi": {
        "command": "uv",
        "args": ["run", "--quiet", "--script", "/full/path/to/qidi-mcp/server.py"]
      }
    }
  }
  ```
  VS Code reads the same entry from `.vscode/mcp.json` under `servers` instead of `mcpServers`.

If your printer is already added in QIDI Studio (Device tab), that's all. The address is read from there. If not, add `-e QIDI_HOST=<printer-ip>` to the command, or `"env": { "QIDI_HOST": "<printer-ip>" }` to the JSON.

**4. Check it works.** Ask *"What's my QIDI printer doing right now?"* The answer should show the printer state and temperatures. If it can't connect, open `http://<printer-ip>/printer/info` in a browser. If that doesn't load either, the printer isn't reachable from this computer.

## What it can do

| | |
|---|---|
| **Slice** | STL or 3MF for any of the models above. Supports, material, infill, walls, layer height, brim, copies, scale, rotation, or any setting from QIDI Studio's process tab |
| **Report** | Print time, grams of filament and slicer warnings, before anything is printed |
| **Send and print** | Upload the job and start, pause, resume or cancel it |
| **Watch** | State, layer, progress, temperatures, time left and finish time, plus a photo from the printer camera |
| **QIDI Box** | Which filament and color is in each slot, which one is loaded, humidity and drying |
| **Heat and calibrate** | Preheat, cool down, run any Klipper macro: bed mesh, input shaper, nozzle cleaning |
| **History** | Recent jobs and lifetime totals |
| **Several printers** | Every tool takes a printer name when you have more than one |

Every tool is listed in [Tools](#tools) below.

## Supports

Ask for supports by what you need, and the assistant picks one of these. Numbers are for PLA/PETG with a 0.4 nozzle.

| Profile | When to use it |
|---|---|
| `easy` | The default when supports are needed. Normal grid, 35° threshold, 0.25 mm top gap, sparse interface. Comes off by hand |
| `buildplate` | Like `easy`, but only grows from the bed, never from the part. For parts where the surface matters |
| `clean` | Tighter gap and dense interface. Nicer underside, harder to remove |
| `tall` | Denser base plus a brim, for tall thin supports that tend to snap or fall over |
| `painted` | Only where supports are painted in the 3MF. For downloaded projects |
| `tree` | Organic shapes. In practice the regular ones are more reliable |
| `none` | No supports |

## You stay in charge

- **Nothing prints by surprise.** Uploading a file doesn't start it unless you ask.
- **No heating during a print.** Heating and macros refuse to run while a print is going, unless you say so explicitly.
- **Your presets stay as they are.** Changes go into a temporary preset that inherits from yours. QIDI Studio's own presets are never edited.
- **It stays on your network.** The connector talks to QIDI Studio on your computer and to your printer over the local network. QIDI printers don't ask for a password on the LAN, so keep yours off the public internet.

## Privacy

qidi-mcp has no backend and collects nothing. It talks to QIDI Studio and to your printer, and to nothing else. Your conversation, tool replies included, goes to whichever model your AI app uses.

## Tools

Slicing: `slice`, `list_presets`

Printer: `list_printers`, `printer_status`, `camera_snapshot`, `filament_box_status`, `print_history`, `list_printer_files`, `upload_file`, `start_print`, `pause_print`, `resume_print`, `cancel_print`, `heat`, `cooldown`, `list_macros`, `run_macro`

When you don't name a printer, process or filament, `slice` uses whatever is selected in QIDI Studio right now. `material="PETG"` picks a matching filament for the printer, QIDI Basic first, then Generic. Printer models can be named the way they're sold (`Max4`, `Plus 3`) or the way QIDI Studio names them (`X-Max 4`, `X-Plus 3`).

## Config

All optional, set as environment variables:

```
QIDI_HOST          printer ip, if it isn't saved in QIDI Studio
QIDI_PRINTERS      several printers: "q2=192.168.1.50:Q2,big=192.168.1.51:Max4"
QIDI_MACHINE       default model or preset instead of QIDI Studio's current one
QIDI_PROCESS       default process preset
QIDI_FILAMENT      default filament preset
QIDI_STUDIO        QIDI Studio binary (default /Applications/QIDIStudio.app/Contents/MacOS/QIDIStudio)
QIDI_STUDIO_DATA   QIDI Studio data folder (default ~/Library/Application Support/QIDIStudio)
QIDI_PRESETS       system presets folder (default: every series under QIDI_STUDIO_DATA/system)
```

On Windows and Linux, set `QIDI_STUDIO` and `QIDI_STUDIO_DATA`. The defaults are macOS paths.

## Troubleshooting

- **`No module named 'mcp.server.fastmcp'`**: you're on mcp 2.x. Run it with `uv run --script` as shown above, so the right version is used.
- **"printer is not reachable"**: the printer is off or dropped off Wi-Fi. Check `http://<printer-ip>/printer/info`.
- **Filament weight shows 0 in QIDI Studio's CLI output**: known CLI quirk. qidi-mcp works the weight out from the volume and a density table instead.
- **A QIDI preset is called "invalid"**: some QIDI system presets miss a field the CLI wants. qidi-mcp works on temporary copies and fills it in.

[Report a problem](https://github.com/jamolh/qidi-mcp/issues)

## License

Free and open source under MIT.

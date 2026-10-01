# Millumin tools

Two helpers for prepping and running a Millumin show (60 fps by default):

1. **`content_check/`**: a content intake checker. It scans the client's folder, flags anything that will cause problems in Millumin, writes a report and a starter cue sheet, and can batch-convert video to HAP or ProRes.
2. **`millumin_mcp/`**: an MCP server, so Claude Code can drive Millumin over OSC ("launch column 4", "fade to black", "show the test card"). It can send every command to a **main and a backup Mac** at the same time.

Everything runs on your Mac. Millumin itself is Mac-only.

## Setup (once)

```bash
brew install ffmpeg python          # ffmpeg/ffprobe do the media work
git clone <this repo> && cd learn/millumin_tools
python3 -m pip install -r requirements.txt   # only needed for the MCP server
```

## 1. Content checker

```bash
# Check only. Writes ClientContent_report/ next to the folder.
python3 content_check/check_content.py ~/Desktop/ClientContent --canvas 1920x1080

# Check and convert every video to HAP (HAP Alpha when it has transparency),
# conformed to 60 fps and fitted to the canvas. Writes ClientContent_ShowMedia/.
python3 content_check/check_content.py ~/Desktop/ClientContent --canvas 1920x1080 \
    --convert auto --conform-fps --fit-canvas

# Then re-check the converted folder. Everything should read OK/INFO.
python3 content_check/check_content.py ~/Desktop/ClientContent_ShowMedia --canvas 1920x1080
```

| Option | Meaning |
|---|---|
| `--fps 60` | Show frame rate (default 60) |
| `--canvas WxH` | Output resolution: projector native, or the LED processor map |
| `--convert` | `auto`, `hap`, `hap_q` (better quality, ~2x size), `hap_alpha`, `prores` (422 HQ), `prores4444` (with alpha) |
| `--conform-fps` | Re-time converted clips to `--fps`. 30 fps becomes 60 cleanly; 25/24 fps will still judder, so ask for a re-render |
| `--fit-canvas` | Scale and pad converted clips to `--canvas` (transparent padding for alpha clips) |

**What it flags:** H.264/H.265 "delivery" codecs, frame rates that don't divide evenly into 60 (judder), 59.94 vs 60, variable frame rate (phone and screen recordings), wrong resolution or aspect ratio, sizes not divisible by 4 (HAP), interlaced or HDR video, phone rotation metadata, alpha channels, PowerPoint/Keynote/PDF decks, HEIC/SVG/GIF, non-48 kHz or compressed audio, fonts, image sequences, and filenames like "copy" or "draft".

**Outputs:** `report.md` (includes a list of questions to send the client), `report.csv`, and `cue_sheet.csv` (cue #, column, file, duration, on-end behaviour, notes; fill it in with the client).

Exit code: `1` if anything is an ERROR (e.g. a deck that still needs exporting), otherwise `0`.

## 2. Millumin MCP server (Claude Code controls Millumin)

**In Millumin:** turn on OSC input (Interactions / OSC settings, default UDP port **5000**). If you want feedback (which cue is live), also set Millumin's OSC output to the Mac running Claude Code, e.g. port 5001.

**Register it with Claude Code** on your Mac:

```bash
# Single machine:
claude mcp add millumin -e MILLUMIN_HOSTS=127.0.0.1:5000 -e MILLUMIN_FEEDBACK_PORT=5001 \
  -- python3 "$PWD/millumin_mcp/server.py"

# Main + backup Macs (every command goes to both, so they stay in sync):
claude mcp add millumin -e MILLUMIN_HOSTS=10.0.0.11:5000,10.0.0.12:5000 \
  -- python3 "$PWD/millumin_mcp/server.py"
```

Then ask Claude things like: *"show the test card on both machines"*, *"launch column 3"*, *"fade the video master to black over 3 seconds"*, *"set the lower third text to 'Jane Doe, CEO'"*.

| Tool | OSC sent |
|---|---|
| `launch_column`, `stop_column`, `next_column`, `previous_column` | `/action/launchColumn` etc. |
| `select_board` | `/action/selectBoard` |
| `transport`, `go_to_time`, `go_to_timeline_segment` | `/action/play`, `/action/goToTime`, ... |
| `set_master`, `fade_master_video` | `/masterVideo`, `/masterAudio`, `/masterDMX` |
| `set_layer_opacity`, `layer_media`, `set_layer_text`, `set_layer_media_time` | `/index:N/...`, `/layer:Name/...`, `/selectedLayer/...` |
| `test_card`, `fullscreen`, `save_project`, `ping` | `/action/displayTestCard`, ... |
| `send_osc` | any raw address |
| `status` | shows targets and recent feedback from Millumin |

Addresses come from the [official Millumin OSC docs](https://github.com/anome/millumin-dev-kit/wiki/OSC-documentation). Older Millumin versions put `/millumin` in front of addresses; if commands do nothing, set `-e MILLUMIN_OSC_PREFIX=/millumin`.

> **Use it for prep and programming, not for calling the live show.** OSC is UDP: nothing confirms a message arrived. During the show, cues should come from you (keyboard, Stream Deck or MIDI) or from the show-control system. Always test on the real network before doors open.

## Running the tests

```bash
python3 -m unittest discover millumin_tools/tests -v
```

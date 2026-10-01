#!/usr/bin/env python3
"""MCP server that lets Claude control Millumin over OSC.

Millumin listens for OSC on UDP 5000 by default (enable it in Millumin's
OSC / Interactions settings). Addresses follow the official docs:
https://github.com/anome/millumin-dev-kit/wiki/OSC-documentation

Environment:
    MILLUMIN_HOSTS           Comma-separated host[:port] list. Every command goes to
                             ALL of them, so main + backup Macs stay in sync.
                             Default 127.0.0.1:5000.
    MILLUMIN_OSC_PREFIX      Prefix for incoming addresses. Default "" (Millumin 4/5).
                             Older versions used "/millumin".
    MILLUMIN_FEEDBACK_PORT   Optional UDP port to listen for Millumin's OSC feedback
                             (point Millumin's OSC output at this machine/port).
"""

from __future__ import annotations

import os
import time
from typing import Literal

from mcp.server.fastmcp import FastMCP

from osc import FeedbackListener, OscSender, parse_hosts

HOSTS = parse_hosts(os.environ.get("MILLUMIN_HOSTS", "127.0.0.1:5000"))
PREFIX = os.environ.get("MILLUMIN_OSC_PREFIX", "").rstrip("/")
FEEDBACK_PORT = os.environ.get("MILLUMIN_FEEDBACK_PORT")

sender = OscSender(HOSTS)
feedback = FeedbackListener(int(FEEDBACK_PORT)) if FEEDBACK_PORT else None

mcp = FastMCP(
    "millumin",
    instructions=(
        "Controls Millumin (a Mac media server used for live shows) over OSC. "
        "Commands go to every configured Mac at once (main + backup). "
        "Things the user can see on stage, like launching columns or changing masters, "
        "should only be done when the user asks for them; never guess cue numbers during a show. "
        "Column and layer indexes are 1-based, as shown in Millumin."
    ),
)


def _send(path: str, *args) -> str:
    address = f"{PREFIX}{path}"
    targets = sender.send(address, *args)
    shown = " ".join(repr(a) for a in args)
    return f"Sent {address} {shown}".rstrip() + f" -> {', '.join(targets)}"


def _target(layer: int | str) -> str:
    """Layer by 1-based index, by name, or 'selected'."""
    if isinstance(layer, int) or (isinstance(layer, str) and layer.isdigit()):
        return f"/index:{int(layer)}"
    if layer.lower() in ("selected", "selectedlayer"):
        return "/selectedLayer"
    return f"/layer:{layer}"


# ------------------------------------------------------------------ cues / dashboard

@mcp.tool()
def launch_column(column: int | str) -> str:
    """Launch a Dashboard column (cue) by 1-based index or by name."""
    return _send("/action/launchColumn", int(column) if str(column).isdigit() else column)


@mcp.tool()
def stop_column() -> str:
    """Stop the currently launched column."""
    return _send("/action/stopColumn")


@mcp.tool()
def next_column() -> str:
    """Launch the next column (the 'GO' button)."""
    return _send("/action/launchNextColumn")


@mcp.tool()
def previous_column() -> str:
    """Launch the previous column."""
    return _send("/action/launchPreviousColumn")


@mcp.tool()
def select_board(board: int | str) -> str:
    """Switch to another board/dashboard by 1-based index or name."""
    return _send("/action/selectBoard", int(board) if str(board).isdigit() else board)


# ------------------------------------------------------------------ transport / timeline

@mcp.tool()
def transport(action: Literal["play", "pause", "playOrPause"]) -> str:
    """Play/pause the current timeline or composition."""
    return _send(f"/action/{action}")


@mcp.tool()
def go_to_time(time_code: str) -> str:
    """Jump the timeline. Accepts seconds ('10'), relative seconds ('-10'), or 'HH:MM:SS.mmm'."""
    try:
        return _send("/action/goToTime", float(time_code))
    except ValueError:
        return _send("/action/goToTime", time_code)


@mcp.tool()
def go_to_timeline_segment(name: str) -> str:
    """Jump to a named timeline segment/marker."""
    return _send("/action/goToTimelineSegment", name)


# ------------------------------------------------------------------ masters / layers

@mcp.tool()
def set_master(kind: Literal["video", "audio", "dmx"], level: float) -> str:
    """Set the video/audio/DMX master (0.0-1.0). Video master 0 = blackout."""
    level = max(0.0, min(1.0, float(level)))
    return _send(f"/master{kind.capitalize() if kind != 'dmx' else 'DMX'}", level)


@mcp.tool()
def fade_master_video(to_level: float, seconds: float = 2.0, from_level: float = 1.0) -> str:
    """Fade the video master smoothly (e.g. to_level=0 to fade to black)."""
    to_level = max(0.0, min(1.0, float(to_level)))
    steps = max(1, int(seconds * 30))
    for n in range(1, steps + 1):
        sender.send(f"{PREFIX}/masterVideo", from_level + (to_level - from_level) * n / steps)
        time.sleep(seconds / steps)
    return f"Faded masterVideo {from_level} -> {to_level} over {seconds}s on {len(HOSTS)} machine(s)"


@mcp.tool()
def set_layer_opacity(layer: int | str, opacity: float) -> str:
    """Set a layer's opacity (0.0-1.0). layer = 1-based index, layer name, or 'selected'."""
    return _send(f"{_target(layer)}/opacity", max(0.0, min(1.0, float(opacity))))


@mcp.tool()
def layer_media(layer: int | str, action: Literal["startMedia", "pauseMedia", "startOrPauseMedia", "stopMedia"]) -> str:
    """Start/pause/stop the media on a layer."""
    return _send(f"{_target(layer)}/{action}")


@mcp.tool()
def set_layer_text(layer: int | str, text: str) -> str:
    """Change the text of a text layer (e.g. a speaker name lower third)."""
    return _send(f"{_target(layer)}/media/text", text)


@mcp.tool()
def set_layer_media_time(layer: int | str, seconds: float) -> str:
    """Seek the media on a layer to a time in seconds."""
    return _send(f"{_target(layer)}/media/time", float(seconds))


# ------------------------------------------------------------------ setup helpers

@mcp.tool()
def test_card(show: bool) -> str:
    """Show or hide Millumin's test card on the outputs (for lining up projectors/LED)."""
    return _send("/action/displayTestCard" if show else "/action/hideTestCard")


@mcp.tool()
def fullscreen(enter: bool) -> str:
    """Enter or exit fullscreen output."""
    return _send("/action/enterFullscreen" if enter else "/action/exitFullscreen")


@mcp.tool()
def save_project() -> str:
    """Save the open Millumin project on every machine."""
    return _send("/action/saveProject")


@mcp.tool()
def ping() -> str:
    """Send /ping to every Millumin machine (check feedback/status for replies)."""
    return _send("/ping")


@mcp.tool()
def send_osc(address: str, args: list[int | float | str] | None = None) -> str:
    """Send any raw OSC message, e.g. address='/selectedLayer/effect1/amount', args=[0.5].
    The MILLUMIN_OSC_PREFIX is NOT added here."""
    return f"Sent {address} {args or ''} -> {', '.join(sender.send(address, *(args or [])))}"


@mcp.tool()
def status(last: int = 20) -> str:
    """Show configuration and the latest OSC feedback Millumin sent back (needs MILLUMIN_FEEDBACK_PORT)."""
    lines = [f"Targets: {', '.join(f'{h}:{p}' for h, p in HOSTS)}", f"Address prefix: {PREFIX or '(none)'}"]
    if not feedback:
        lines.append("Feedback: off (set MILLUMIN_FEEDBACK_PORT and point Millumin's OSC output at this machine)")
        return "\n".join(lines)
    lines.append(f"Feedback: listening on UDP {feedback.port}, {len(feedback.messages)} message(s) received")
    for key in ("/millumin/board/launchedColumn", "/millumin/board/stoppedColumn"):
        if key in feedback.state:
            t, args = feedback.state[key]
            lines.append(f"{key.rsplit('/', 1)[-1]}: {args} ({time.time() - t:.0f}s ago)")
    for t, host, address, args in list(feedback.messages)[-last:]:
        lines.append(f"{time.strftime('%H:%M:%S', time.localtime(t))} {host} {address} {args}")
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()

#!/usr/bin/env python3
"""Millumin content intake checker.

Scan a folder of client content, flag anything that will cause trouble in
Millumin (codecs, frame rates, resolution, alpha, decks, fonts...), write a
report + starter cue sheet, and optionally batch-transcode to HAP or ProRes.

Requires ffmpeg/ffprobe on PATH (macOS: `brew install ffmpeg`).

Examples:
    python3 check_content.py ~/Desktop/ClientContent
    python3 check_content.py ~/Desktop/ClientContent --canvas 1920x1080
    python3 check_content.py ~/Desktop/ClientContent --convert hap --conform-fps
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

VIDEO_EXT = {".mov", ".mp4", ".m4v", ".avi", ".mkv", ".webm", ".mxf", ".mpg", ".mpeg", ".wmv", ".flv", ".3gp"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".psd", ".tga", ".exr", ".webp"}
PHOTO_CONVERT_EXT = {".heic", ".heif", ".svg", ".ai", ".eps"}
AUDIO_EXT = {".wav", ".aif", ".aiff", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}
DECK_EXT = {".ppt", ".pptx", ".key", ".pdf", ".odp"}
FONT_EXT = {".ttf", ".otf", ".ttc", ".woff", ".woff2"}
IGNORE_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}

# Long-GOP / delivery codecs: fine for web, bad for scrubbing and stacking layers.
DELIVERY_CODECS = {"h264", "hevc", "vp8", "vp9", "av1", "mpeg4", "mpeg2video", "mpeg1video", "wmv3", "flv1", "msmpeg4v3"}
PLAYBACK_CODECS = {"hap", "prores", "dnxhd", "png", "qtrle", "rawvideo", "hapq", "hapalpha"}
HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}

ERROR, WARN, INFO = "ERROR", "WARN", "INFO"
SEVERITY_RANK = {ERROR: 0, WARN: 1, INFO: 2}


@dataclass
class Item:
    path: Path
    rel: str
    kind: str
    codec: str = ""
    width: int = 0
    height: int = 0
    fps: float = 0.0
    duration: float = 0.0
    alpha: bool = False
    audio: str = ""
    issues: list[tuple[str, str]] = field(default_factory=list)

    def flag(self, severity: str, msg: str) -> None:
        self.issues.append((severity, msg))

    @property
    def worst(self) -> str:
        if not self.issues:
            return "OK"
        return min((s for s, _ in self.issues), key=SEVERITY_RANK.__getitem__)


# --------------------------------------------------------------------------- probing

def ffprobe(path: Path) -> dict:
    cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return {}
    if out.returncode != 0:
        return {}
    return json.loads(out.stdout or "{}")


def parse_rate(rate: str | None) -> float:
    if not rate or rate in ("0/0", "0"):
        return 0.0
    try:
        return float(Fraction(rate))
    except (ValueError, ZeroDivisionError):
        return 0.0


def has_alpha(stream: dict) -> bool:
    pix = stream.get("pix_fmt", "") or ""
    tag = (stream.get("codec_tag_string") or "").lower()
    if tag in ("hap5", "hapa", "hapm"):
        return True
    if stream.get("codec_name") == "prores" and stream.get("profile", "").startswith("4444") and "a" in pix[:4]:
        return True
    return bool(re.match(r"^(yuva|rgba|bgra|argb|abgr|ya|gbrap|pal8)", pix))


def rotation_of(stream: dict) -> int:
    rot = stream.get("tags", {}).get("rotate")
    if rot:
        return int(float(rot))
    for sd in stream.get("side_data_list", []) or []:
        if "rotation" in sd:
            return int(float(sd["rotation"]))
    return 0


def kind_of(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in VIDEO_EXT:
        return "video"
    if ext in IMAGE_EXT:
        return "image"
    if ext in PHOTO_CONVERT_EXT:
        return "image-convert"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in DECK_EXT:
        return "deck"
    if ext in FONT_EXT:
        return "font"
    return "other"


# --------------------------------------------------------------------------- checks

def check_fps(item: Item, fps: float, avg_fps: float, show_fps: float) -> None:
    if fps <= 0:
        return
    if avg_fps > 0 and abs(fps - avg_fps) > 0.05 * fps and item.duration > 1:
        item.flag(WARN, f"Looks variable-frame-rate ({fps:.3f} nominal vs {avg_fps:.3f} avg) - typical of phone/screen recordings; conform to constant {show_fps:g} fps")
    if abs(fps - show_fps) < 0.01:
        return
    ratio = show_fps / fps
    if abs(show_fps - 60) < 0.01 and abs(fps - 59.94) < 0.01:
        item.flag(WARN, "59.94 fps vs 60 fps show: will drop a frame every ~16 s; ask whether the system runs 60 or 59.94")
    elif abs(ratio - round(ratio)) < 0.001 and round(ratio) >= 1:
        item.flag(INFO, f"{fps:g} fps plays cleanly at {show_fps:g} (each frame shown {round(ratio)}x)")
    else:
        item.flag(WARN, f"{fps:.3f} fps does not divide evenly into {show_fps:g} fps -> visible judder on pans/motion; ask for a {show_fps:g} fps (or {show_fps/2:g}) render")


def check_size(item: Item, canvas: tuple[int, int] | None) -> None:
    w, h = item.width, item.height
    if not w or not h:
        return
    if canvas:
        cw, ch = canvas
        if (w, h) != (cw, ch):
            if abs(w / h - cw / ch) > 0.01:
                item.flag(WARN, f"Aspect {w}x{h} ({w/h:.3f}) doesn't match canvas {cw}x{ch} ({cw/ch:.3f}) -> will letterbox/crop/stretch")
            elif w < cw:
                item.flag(WARN, f"{w}x{h} is smaller than canvas {cw}x{ch} -> will be upscaled (soft)")
            else:
                item.flag(INFO, f"{w}x{h} is larger than canvas {cw}x{ch} -> wasted GPU/disk; consider scaling down")
    if w > 8192 or h > 8192:
        item.flag(WARN, f"Very large ({w}x{h}); may exceed GPU texture limits")


def check_video(item: Item, info: dict, show_fps: float, canvas) -> None:
    streams = info.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = info.get("format", {})
    item.duration = float(fmt.get("duration") or 0)
    if not v:
        item.flag(ERROR, "No video stream found (corrupt or audio-only?)")
        return
    item.codec = v.get("codec_name", "?")
    tag = (v.get("codec_tag_string") or "").lower()
    if item.codec == "hap":
        item.codec = {"hapy": "hap_q", "hap5": "hap_alpha", "hapm": "hap_q_alpha"}.get(tag, "hap")
    if item.codec == "prores" and v.get("profile"):
        item.codec = f"prores ({v['profile']})"
    item.width, item.height = int(v.get("width") or 0), int(v.get("height") or 0)
    fps = parse_rate(v.get("r_frame_rate"))
    avg = parse_rate(v.get("avg_frame_rate"))
    item.fps = round(avg or fps, 3)
    item.alpha = has_alpha(v)

    base = v.get("codec_name", "")
    if base in DELIVERY_CODECS:
        item.flag(WARN, f"{base.upper()} is a delivery codec: stutters when scrubbing/stacking layers -> transcode to HAP or ProRes")
    elif base not in PLAYBACK_CODECS:
        item.flag(WARN, f"Unusual codec '{base}' -> transcode to HAP or ProRes")

    check_fps(item, fps, avg, show_fps)
    check_size(item, canvas)

    if item.width % 4 or item.height % 4:
        item.flag(INFO, f"{item.width}x{item.height} not divisible by 4; HAP conversion will pad it")
    if v.get("field_order") not in (None, "progressive", "unknown"):
        item.flag(WARN, f"Interlaced ({v['field_order']}) -> combing on screen; deinterlace")
    if v.get("color_transfer") in HDR_TRANSFERS:
        item.flag(WARN, "HDR content (PQ/HLG) -> will look washed out/dark on SDR outputs; ask for an SDR Rec.709 version")
    if rotation_of(v):
        item.flag(WARN, f"Has {rotation_of(v)} deg rotation metadata (phone video) -> may show sideways; bake rotation in")
    if item.duration and item.duration < 0.5:
        item.flag(INFO, "Very short clip (<0.5 s)")

    if a:
        sr = int(a.get("sample_rate") or 0)
        item.audio = f"{a.get('codec_name')} {sr/1000:g}k {a.get('channels')}ch"
        if sr and sr != 48000:
            item.flag(INFO, f"Audio at {sr} Hz (48 kHz is the AV standard)")
    elif item.duration > 0:
        item.audio = "none"


def check_image(item: Item, info: dict, canvas) -> None:
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if not v:
        item.flag(WARN, "Could not read image")
        return
    item.codec = v.get("codec_name", "?")
    item.width, item.height = int(v.get("width") or 0), int(v.get("height") or 0)
    item.alpha = has_alpha(v)
    check_size(item, canvas)
    if item.path.suffix.lower() == ".gif":
        item.flag(WARN, "Animated GIF -> convert to a video file")
    if item.path.suffix.lower() == ".psd":
        item.flag(INFO, "PSD: confirm the flattened result is what the client expects; PNG is safer")


def check_audio(item: Item, info: dict) -> None:
    a = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    item.duration = float(info.get("format", {}).get("duration") or 0)
    if not a:
        item.flag(ERROR, "No audio stream found")
        return
    sr = int(a.get("sample_rate") or 0)
    item.codec = a.get("codec_name", "?")
    item.audio = f"{sr/1000:g}k {a.get('channels')}ch"
    if item.codec in ("mp3", "aac", "vorbis", "opus"):
        item.flag(INFO, "Compressed audio; WAV/AIFF 48 kHz preferred for shows")
    if sr and sr != 48000:
        item.flag(INFO, f"{sr} Hz (48 kHz is the AV standard)")


def check_name(item: Item) -> None:
    if re.search(r"[^\w.\- ()]", item.path.name):
        item.flag(INFO, "Filename has special characters; rename to avoid relink issues")
    if re.search(r"(?i)\b(copy|draft|old|wip)\b", item.path.stem.replace("_", " ")):
        item.flag(INFO, "Name suggests a draft/copy; confirm this is the final version")


def scan(root: Path, show_fps: float, canvas) -> list[Item]:
    items: list[Item] = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        if path.name in IGNORE_NAMES or path.name.startswith("._"):
            continue
        item = Item(path=path, rel=str(path.relative_to(root)), kind=kind_of(path))
        check_name(item)
        if item.kind == "video":
            check_video(item, ffprobe(path), show_fps, canvas)
        elif item.kind == "image":
            check_image(item, ffprobe(path), canvas)
        elif item.kind == "image-convert":
            item.flag(WARN, f"{path.suffix.upper()[1:]} isn't reliably supported -> export as PNG")
        elif item.kind == "audio":
            check_audio(item, ffprobe(path))
        elif item.kind == "deck":
            item.flag(ERROR, "Presentation file: Millumin can't run decks -> export each slide to PNG (or builds to video) at canvas resolution; ask for fonts")
        elif item.kind == "font":
            item.flag(INFO, "Font: install on BOTH show Macs before opening the project")
        else:
            item.flag(INFO, "Unknown file type; ask the client what it's for")
        items.append(item)
    find_sequences(items)
    return items


def find_sequences(items: list[Item]) -> None:
    """Flag numbered image sequences (e.g. frame_0001.png...) so they're treated as one clip."""
    groups: dict[tuple[str, str], list[Item]] = {}
    for it in items:
        if it.kind != "image":
            continue
        m = re.match(r"^(.*?)(\d{3,})$", it.path.stem)
        if m:
            groups.setdefault((str(it.path.parent / m.group(1)), it.path.suffix.lower()), []).append(it)
    for (prefix, ext), members in groups.items():
        if len(members) >= 10:
            members[0].flag(INFO, f"Image sequence of {len(members)} frames ({Path(prefix).name}####{ext}) -> import as a sequence or convert to a single HAP/ProRes clip")


# --------------------------------------------------------------------------- reports

def fmt_dur(sec: float) -> str:
    if not sec:
        return ""
    m, s = divmod(sec, 60)
    return f"{int(m)}:{s:05.2f}"


def write_reports(items: list[Item], out_dir: Path, show_fps: float, canvas) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(out_dir / "report.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["status", "file", "type", "codec", "resolution", "fps", "duration", "alpha", "audio", "issues"])
        for it in items:
            w.writerow([it.worst, it.rel, it.kind, it.codec, f"{it.width}x{it.height}" if it.width else "",
                        it.fps or "", fmt_dur(it.duration), "yes" if it.alpha else "", it.audio,
                        " | ".join(f"{s}: {m}" for s, m in it.issues)])

    with open(out_dir / "cue_sheet.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["cue", "dashboard_column", "layer", "file", "type", "duration", "on_end (loop/stop/hold/next)", "transition_in", "audio", "notes"])
        n = 0
        for it in items:
            if it.kind in ("video", "image", "audio"):
                n += 1
                w.writerow([n, n, "", it.rel, it.kind, fmt_dur(it.duration), "hold" if it.kind == "image" else "",
                            "", it.audio, ""])

    counts = {k: sum(1 for it in items if it.worst == k) for k in (ERROR, WARN, INFO, "OK")}
    lines = [
        "# Content intake report", "",
        f"Show frame rate: **{show_fps:g} fps**  ",
        f"Canvas: **{canvas[0]}x{canvas[1]}**  " if canvas else "Canvas: _not set (use --canvas WxH)_  ",
        f"Files: {len(items)} | Errors: {counts[ERROR]} | Warnings: {counts[WARN]} | Info: {counts[INFO]} | OK: {counts['OK']}", "",
        "| Status | File | Type | Codec | Res | FPS | Dur | Alpha | Audio |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for it in sorted(items, key=lambda i: (SEVERITY_RANK.get(i.worst, 3), i.rel)):
        lines.append(f"| {it.worst} | {it.rel} | {it.kind} | {it.codec} | {f'{it.width}x{it.height}' if it.width else ''} | "
                     f"{it.fps or ''} | {fmt_dur(it.duration)} | {'yes' if it.alpha else ''} | {it.audio} |")
    lines += ["", "## Issues", ""]
    for it in sorted(items, key=lambda i: (SEVERITY_RANK.get(i.worst, 3), i.rel)):
        if it.issues:
            lines.append(f"**{it.rel}**")
            lines += [f"- {s}: {m}" for s, m in sorted(it.issues, key=lambda x: SEVERITY_RANK[x[0]])]
            lines.append("")
    lines += ["## Questions for the client", ""]
    lines += [f"- {q}" for q in client_questions(items, show_fps, canvas)]
    (out_dir / "report.md").write_text("\n".join(lines) + "\n")


def client_questions(items: list[Item], show_fps: float, canvas) -> list[str]:
    qs = []
    if not canvas:
        qs.append("What is the exact pixel resolution of each output (projector native / LED processor map)?")
    bad_fps = sorted({it.fps for it in items if it.kind == "video" and it.fps and any("judder" in m for _, m in it.issues)})
    if bad_fps:
        qs.append(f"Can you re-render the {', '.join(f'{f:g}' for f in bad_fps)} fps clips at {show_fps:g} fps?")
    if any(it.kind == "deck" for it in items):
        qs.append("Can you export the presentation(s) as PNG per slide (and builds/animations as video)? Which fonts are used?")
    if any(it.kind == "video" and it.codec.startswith(("h264", "hevc")) for it in items):
        qs.append("Do you have ProRes/high-bitrate masters of the MP4 files?")
    if any(it.kind == "video" and it.audio and it.audio != "none" for it in items):
        qs.append("Which clips should play audio, and where does audio go (HDMI embedded or audio interface to FOH)?")
    qs.append("Is this the final content? What's the cutoff for changes, and how are on-site changes handled?")
    return qs


def print_summary(items: list[Item]) -> None:
    color = {ERROR: "\033[31m", WARN: "\033[33m", INFO: "\033[36m", "OK": "\033[32m"} if sys.stdout.isatty() else {}
    reset = "\033[0m" if color else ""
    for it in items:
        c = color.get(it.worst, "")
        dims = f"{it.width}x{it.height}" if it.width else ""
        print(f"{c}{it.worst:5}{reset} {it.rel:50.50} {it.kind:6} {it.codec[:16]:16} {dims:>10} {str(it.fps or ''):>7} {fmt_dur(it.duration):>9}")
        for s, m in it.issues:
            if s != INFO or it.worst == INFO:
                print(f"        - {s}: {m}")


# --------------------------------------------------------------------------- conversion

def build_ffmpeg_cmd(it: Item, dest: Path, target: str, show_fps: float, conform_fps: bool, canvas) -> list[str]:
    if target == "auto":
        target = "hap_alpha" if it.alpha else "hap"
    vf = []
    if conform_fps:
        vf.append(f"fps={show_fps:g}")
    if canvas:
        cw, ch = canvas
        pad_color = "black@0" if target in ("hap_alpha", "prores4444") else "black"
        vf.append(f"scale={cw}:{ch}:force_original_aspect_ratio=decrease,pad={cw}:{ch}:(ow-iw)/2:(oh-ih)/2:color={pad_color}")
    if target.startswith("hap"):
        vf.append("pad=ceil(iw/4)*4:ceil(ih/4)*4")

    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(it.path)]
    if vf:
        cmd += ["-vf", ",".join(vf)]
    if target.startswith("hap"):
        cmd += ["-c:v", "hap", "-format", target, "-chunks", "4"]
        if target == "hap_alpha":
            cmd += ["-pix_fmt", "rgba"]
    elif target == "prores":
        cmd += ["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le", "-vendor", "apl0"]
    elif target == "prores4444":
        cmd += ["-c:v", "prores_ks", "-profile:v", "4", "-pix_fmt", "yuva444p10le", "-alpha_bits", "16", "-vendor", "apl0"]
    cmd += ["-c:a", "pcm_s16le", "-ar", "48000", "-map", "0:v:0", "-map", "0:a?", str(dest)]
    return cmd


def convert(items: list[Item], dest_root: Path, target: str, show_fps: float, conform_fps: bool, canvas, force: bool) -> int:
    failures = 0
    for it in items:
        if it.kind != "video":
            continue
        dest = dest_root / Path(it.rel).with_suffix(".mov")
        if dest.exists() and not force:
            print(f"skip   {it.rel} (exists; use --force)")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        cmd = build_ffmpeg_cmd(it, dest, target, show_fps, conform_fps, canvas)
        print(f"encode {it.rel} -> {dest.relative_to(dest_root)}")
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            failures += 1
            print(f"  FAILED: {res.stderr.strip()[-400:]}")
    return failures


# --------------------------------------------------------------------------- main

def parse_canvas(s: str | None):
    if not s:
        return None
    m = re.fullmatch(r"(\d+)[xX](\d+)", s)
    if not m:
        raise argparse.ArgumentTypeError("canvas must look like 1920x1080")
    return int(m.group(1)), int(m.group(2))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Check client content for Millumin and optionally transcode it.")
    p.add_argument("folder", type=Path, help="Folder of client content")
    p.add_argument("--fps", type=float, default=60.0, help="Show frame rate (default 60)")
    p.add_argument("--canvas", type=parse_canvas, help="Output canvas resolution, e.g. 1920x1080 or 3840x1080")
    p.add_argument("--out", type=Path, help="Where to write reports (default: <folder>_report)")
    p.add_argument("--convert", choices=["auto", "hap", "hap_q", "hap_alpha", "prores", "prores4444"],
                   help="Transcode every video. auto = HAP, or HAP Alpha when the source has transparency")
    p.add_argument("--dest", type=Path, help="Where converted media goes (default: <folder>_ShowMedia)")
    p.add_argument("--conform-fps", action="store_true", help="Also conform converted clips to --fps")
    p.add_argument("--fit-canvas", action="store_true", help="Also scale/pad converted clips to --canvas")
    p.add_argument("--force", action="store_true", help="Overwrite existing converted files")
    args = p.parse_args(argv)

    for tool in ("ffprobe", "ffmpeg"):
        if not shutil.which(tool):
            print(f"{tool} not found. On a Mac: brew install ffmpeg", file=sys.stderr)
            return 2
    root = args.folder.expanduser().resolve()
    if not root.is_dir():
        print(f"Not a folder: {root}", file=sys.stderr)
        return 2
    if args.fit_canvas and not args.canvas:
        p.error("--fit-canvas needs --canvas")

    items = scan(root, args.fps, args.canvas)
    out_dir = (args.out or root.with_name(root.name + "_report")).expanduser()
    print_summary(items)
    write_reports(items, out_dir, args.fps, args.canvas)
    print(f"\nReports: {out_dir}/report.md, report.csv, cue_sheet.csv")

    if args.convert:
        dest = (args.dest or root.with_name(root.name + "_ShowMedia")).expanduser()
        failures = convert(items, dest, args.convert, args.fps, args.conform_fps,
                           args.canvas if args.fit_canvas else None, args.force)
        print(f"\nConverted media: {dest}" + (f"  ({failures} failed)" if failures else ""))
        if failures:
            return 1
        print("Copy non-video files (images, audio, fonts) yourself, then re-run this checker on the ShowMedia folder.")
    return 1 if any(it.worst == ERROR for it in items) else 0


if __name__ == "__main__":
    sys.exit(main())

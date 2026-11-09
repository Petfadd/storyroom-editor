#!/usr/bin/env python3
"""Turn an uploaded video into a contact sheet of frames an AI agent can actually read.

Agents cannot play a video. They *can* read images. This script uses the same
FFmpeg binary the Storyroom Editor already depends on (``imageio-ffmpeg``), so it
adds no new requirement and never writes into ``projects/`` or touches project
state.

Usage
-----
    python tools/watch_video.py path/to/clip.mp4
    python tools/watch_video.py path/to/clip.mp4 --frames 48 --cols 6 --scale 400
    python tools/watch_video.py folder/with/clips --extend      # long clips: 2 passes
    python tools/watch_video.py path/to/clip.mp4 --no-peaks --json report.json

Outputs (unless ``--out`` overrides the directory), all inside
``video_review/<filename>_<timestamp>/``:

    metadata.json   duration, fps, resolution, codecs, bitrate, file size
    sheet_1.jpg     chronological contact sheet, each tile stamped "MM:SS.mmm #n"
    sheet_2.jpg     (only with --extend) reuse of the same timestamp from later in the clip
    peaks.jpg       visually busiest frames (montage / cut density)
    audio.png       waveform + loudness, so silence and clipping are visible
    audio.json      loudness stats computed from the waveform
    README.txt      how to read the folder

Run it from the repository root; ``video_review/`` is git-ignored.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
import time
from array import array
from pathlib import Path

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".mpg", ".mpeg", ".wmv", ".flv", ".ts"}
REPO_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
# FFmpeg / FFprobe discovery
# --------------------------------------------------------------------------- #
def _find_tools() -> tuple[str, str]:
    """Return (ffmpeg, ffprobe). Raises a friendly error when FFmpeg is absent."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        try:
            import imageio_ffmpeg  # the same dependency the editor uses

            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:  # pragma: no cover - depends on install
            ffmpeg = None
    if not ffmpeg:
        sys.exit(
            "FFmpeg was not found.\n"
            "Fix with:  python -m pip install imageio-ffmpeg\n"
            "or install FFmpeg system-wide (https://ffmpeg.org/download.html)."
        )

    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        # imageio-ffmpeg ships ffmpeg only; look for a sibling ffprobe first.
        sibling = Path(ffmpeg).with_name("ffprobe")
        ffprobe = str(sibling) if sibling.exists() else ""
    return ffmpeg, ffprobe


FFMPEG, FFPROBE = _find_tools()


def run(args: list[str], capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        [FFMPEG, "-hide_banner", "-nostdin", "-y", *args],
        capture_output=capture,
        text=True,
        errors="replace",
    )


def ffprobe_json(path: Path) -> dict:
    """Metadata via ffprobe when available, otherwise a light ffmpeg fallback."""
    if FFPROBE:
        proc = subprocess.run(
            [FFPROBE, "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, errors="replace",
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return json.loads(proc.stdout)
        print(f"  ! ffprobe failed on {path.name}: {proc.stderr.strip()[:200]}")

    # Fallback: parse the banner ffmpeg prints when probing the file.
    info = run(["-i", str(path)])
    text = info.stderr
    duration = 0.0
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.?\d*)", text)
    if m:
        h, mi, s = m.groups()
        duration = int(h) * 3600 + int(mi) * 60 + float(s)

    def block(kind: str) -> dict:
        m = re.search(rf"Stream #\S+.*?: {kind}: ([^\n]+)", text)
        if not m:
            return {}
        body = m.group(1)
        codec = body.split(",")[0].split(" ")[0].strip()
        out = {"codec_name": codec}
        res = re.search(r"(\d{2,5})x(\d{2,5})", body)
        if res:
            out["width"], out["height"] = int(res.group(1)), int(res.group(2))
        fps = re.search(r"(\d+(?:\.\d+)?) fps", body)
        if fps:
            out["r_frame_rate"] = f"{fps.group(1)}/1"
        ch = re.search(r"(\d+) channels", body)
        if ch:
            out["channels"] = int(ch.group(1))
        sr = re.search(r"(\d+) Hz", body)
        if sr:
            out["sample_rate"] = int(sr.group(1))
        return out

    streams = []
    v, a = block("Video"), block("Audio")
    if v:
        streams.append({"codec_type": "video", **v})
    if a:
        streams.append({"codec_type": "audio", **a})
    size = re.search(r"bitrate: (\d+) kb/s", text)
    return {
        "format": {"duration": duration, "size": path.stat().st_size,
                   "bit_rate": int(size.group(1)) * 1000 if size else 0,
                   "format_name": "unknown (ffprobe not installed)"},
        "streams": streams,
    }


# --------------------------------------------------------------------------- #
# Metadata helpers
# --------------------------------------------------------------------------- #
def _ratio(value: str) -> float:
    try:
        if "/" in value:
            num, den = value.split("/", 1)
            return float(num) / float(den) if float(den) else 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _stream(probe: dict, kind: str) -> dict:
    for s in probe.get("streams", []):
        if s.get("codec_type") == kind:
            return s
    return {}


def summarize(path: Path, probe: dict) -> dict:
    fmt = probe.get("format", {})
    v, a = _stream(probe, "video"), _stream(probe, "audio")
    duration = float(fmt.get("duration") or v.get("duration") or 0.0)
    fps = _ratio(v.get("avg_frame_rate") or "") or _ratio(v.get("r_frame_rate") or "")
    size = int(fmt.get("size") or path.stat().st_size)
    rotation = 0
    for sd in v.get("side_data_list") or []:
        if "rotation" in sd:
            try:
                rotation = int(float(sd["rotation"]))
            except (TypeError, ValueError):
                pass
    return {
        "file": path.name,
        "path": str(path),
        "duration_seconds": round(duration, 3),
        "duration_hms": time.strftime("%H:%M:%S", time.gmtime(duration)) if duration else "unknown",
        "size_bytes": size,
        "size_mb": round(size / (1024 * 1024), 2),
        "bitrate_kbps": round(int(fmt.get("bit_rate") or 0) / 1000, 1),
        "container": fmt.get("format_name"),
        "video": {
            "codec": v.get("codec_name"),
            "width": v.get("width"),
            "height": v.get("height"),
            "fps": round(fps, 3),
            "frames": round(duration * fps) if duration and fps else None,
            "pix_fmt": v.get("pix_fmt"),
            "rotation": rotation,
        },
        "audio": (
            {
                "codec": a.get("codec_name"),
                "channels": a.get("channels"),
                "sample_rate": int(a.get("sample_rate") or 0),
                "bitrate_kbps": round(int(a.get("bit_rate") or 0) / 1000, 1),
            }
            if a else None
        ),
    }


def resolution(meta: dict) -> str:
    v = meta.get("video") or {}
    w, h = v.get("width"), v.get("height")
    return f"{w}x{h}" if w and h else "resolution unknown"


def is_portrait(meta: dict) -> bool:
    v = meta["video"]
    w, h = v.get("width") or 0, v.get("height") or 0
    if v.get("rotation") in (90, 270, -90):
        w, h = h, w
    return h > w > 0


# --------------------------------------------------------------------------- #
# Contact sheets
# --------------------------------------------------------------------------- #
def even_times(duration: float, count: int, offset: float = 0.0) -> list[float]:
    """Evenly spaced sample times, nudged off the very start and end."""
    if duration <= 0 or count <= 0:
        return [0.0]
    step = duration / count
    start = min(max(offset, 0.0), max(step * 0.5, 0.0))
    return [round(min(start + step * i + step * 0.5, max(duration - 0.05, 0.0)), 3)
            for i in range(count)]


def stamp(seconds: float) -> str:
    m, s = divmod(max(seconds, 0.0), 60)
    return f"{int(m):02d}:{s:06.3f}"


def grab_frame(video: Path, when: float, out: Path, tile_w: int, portrait: bool) -> bool:
    """Extract one frame near ``when`` as PNG. Fast keyframe seek, accurate decode."""
    scale = f"scale={tile_w}:-2:flags=lanczos" if not portrait else f"scale=-2:{tile_w}:flags=lanczos"
    proc = run(["-ss", f"{when:.3f}", "-i", str(video), "-frames:v", "1",
                "-vf", f"{scale},setsar=1,format=rgb24", str(out)])
    return proc.returncode == 0 and out.exists() and out.stat().st_size > 0


def build_sheet(video: Path, times: list[float], out: Path, cols: int, tile_w: int,
                meta: dict, scratch: Path, max_width: int = 2400) -> Path:
    """Compose a labelled contact sheet. Pillow is already a project dependency."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        sys.exit("Pillow is required for contact sheets:  python -m pip install Pillow")

    portrait = is_portrait(meta)
    count = len(times)
    cols = max(1, min(cols, count, max_width // max(tile_w, 1)))
    rows = math.ceil(count / cols)
    label_h = max(int(tile_w * 0.09), 18)
    pad = 6

    tiles: list[tuple[Image.Image, float, int]] = []
    for i, t in enumerate(times):
        png = scratch / f"f{i:03d}.png"
        if grab_frame(video, t, png, tile_w, portrait):
            try:
                tiles.append((Image.open(png).convert("RGB"), t, i + 1))
            except Exception:
                pass
        else:
            print(f"  ! no frame at {stamp(t)}")
    if not tiles:
        raise RuntimeError(
            "no frames could be extracted - the file may be corrupt, truncated, or an "
            "unsupported container; try re-saving it as MP4 (H.264) first"
        )

    cw = max(im.width for im, _, _ in tiles)
    ch = max(im.height for im, _, _ in tiles) + label_h
    canvas = Image.new("RGB", (cols * cw + pad * (cols + 1),
                               rows * ch + pad * (rows + 1)), (18, 18, 20))
    draw = ImageDraw.Draw(canvas)
    for idx, (im, t, num) in enumerate(tiles):
        r, c = divmod(idx, cols)
        x = pad + c * (cw + pad)
        y = pad + r * (ch + pad)
        canvas.paste(im, (x + (cw - im.width) // 2, y))
        bar_y = y + im.height
        draw.rectangle([x, bar_y, x + cw, bar_y + label_h], fill=(0, 0, 0))
        draw.text((x + 6, bar_y + label_h // 2), f"{stamp(t)}  #{num}",
                  fill=(240, 240, 240), anchor="lm")

    canvas.save(out, quality=88, optimize=True)
    return out


def pick_peaks(video: Path, duration: float, sample_fps: float = 1.0) -> list[float]:
    """Times of visually 'busiest' frames, judged by ffmpeg's scene-change score."""
    proc = run(["-i", str(video), "-an",
                "-vf", f"fps={sample_fps},select='gt(scene,0.0)',metadata=print",
                "-f", "null", "-"])
    scores: list[tuple[float, float]] = []
    when = 0.0
    for line in proc.stderr.splitlines():
        line = line.strip()
        m = re.search(r"pts_time:([0-9.]+)", line)          # "[Parsed_metadata_2 @ ...]" prefix
        if m:
            when = float(m.group(1))
            continue
        m = re.search(r"lavfi\.scene_score=([0-9.]+)", line)  # same prefix
        if m:
            scores.append((float(m.group(1)), when))
    if not scores:
        return []
    scores.sort(reverse=True)
    chosen: list[float] = []
    min_gap = max(duration / 60, 0.5)
    for _, when in scores:
        if all(abs(when - c) >= min_gap for c in chosen):
            chosen.append(when)
    return sorted(chosen)


# --------------------------------------------------------------------------- #
# Audio waveform + loudness
# --------------------------------------------------------------------------- #
def analyse_audio(video: Path, meta: dict, out_dir: Path, width: int = 1200) -> dict | None:
    if not meta.get("audio"):
        return None
    raw = out_dir / "audio.raw"
    proc = run(["-i", str(video), "-vn", "-ac", "1", "-ar", "8000",
                "-f", "s16le", str(raw)])
    if proc.returncode != 0 or not raw.exists() or raw.stat().st_size == 0:
        print("  ! could not extract audio for the waveform")
        return None

    pcm = array("h")
    pcm.frombytes(raw.read_bytes())
    raw.unlink(missing_ok=True)
    if not pcm:
        return None

    total = len(pcm)
    peak = max(max(pcm), -min(pcm)) or 1
    peak_dbfs = 20 * math.log10(peak / 32768) if peak else -120.0
    rms = math.sqrt(sum(s * s for s in pcm) / total)
    rms_dbfs = 20 * math.log10(rms / 32768) if rms else -120.0
    silent = sum(1 for s in pcm if abs(s) < 60)
    stats = {
        "peak_dbfs": round(peak_dbfs, 1),
        "rms_dbfs": round(rms_dbfs, 1),
        "silence_percent": round(100 * silent / total, 1),
        "duration_seconds": round((meta.get("duration_seconds") or 0), 2),
        "samples": total,
        "sample_rate": 8000,
    }

    # Downsample to per-column min/max so the waveform survives as a small PNG.
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        stats["waveform_png"] = None
        stats["note"] = "install Pillow to render audio.png"
        return stats

    cols = min(width, max(240, total // 200))
    per = max(total // cols, 1)
    wave_h, ruler_h = 200, 22
    mid = wave_h // 2
    img = Image.new("RGB", (cols + 2, wave_h + ruler_h), (16, 16, 16))
    draw = ImageDraw.Draw(img)
    for c in range(cols):
        chunk = pcm[c * per:(c + 1) * per]
        if not chunk:
            continue
        lo = min(chunk) / peak * (wave_h / 2 - 2)
        hi = max(chunk) / peak * (wave_h / 2 - 2)
        draw.line([(c + 1, mid - hi), (c + 1, mid - lo)], fill=(120, 210, 160))
    # Reference lines drawn on top of the waveform: mid scale, -6 dB and -12 dB of peak.
    for frac in (1.0, 0.5):
        y = int(mid - (wave_h / 2 - 2) * frac)
        draw.line([(0, y), (cols + 2, y)], fill=(30, 30, 30))
        draw.line([(0, wave_h - y), (cols + 2, wave_h - y)], fill=(30, 30, 30))
    draw.line([(0, mid), (cols + 2, mid)], fill=(70, 70, 70))

    duration = stats.get("duration_seconds") or (total / 8000)
    for i in range(11):
        x = int(i / 10 * cols)
        draw.line([(x, wave_h), (x, wave_h + 5)], fill=(160, 160, 160))
        draw.text((x + 3, wave_h + 6), stamp(duration * i / 10)[:5], fill=(170, 170, 170))

    stats["waveform_png"] = "audio.png"
    stats["waveform_note"] = ("flat line = silence, tall green = loud, boxy top = clipped; "
                              "grey lines mark -6 dB and -12 dB of full scale")
    img.save(out_dir / "audio.png")
    return stats


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def process(video: Path, out_root: Path, frames: int, cols: int, tile_w: int,
            extend: bool, peaks: bool) -> dict:
    probe = ffprobe_json(video)
    meta = summarize(video, probe)
    out_dir = out_root / f"{video.stem}_{time.strftime('%Y%m%d_%H%M%S')}"
    scratch = out_dir / ".frames"
    scratch.mkdir(parents=True, exist_ok=True)

    duration = meta["duration_seconds"]
    print(f"\n== {video.name} ==")
    print(f"   {meta['duration_hms']}  ·  {resolution(meta)}"
          f"  ·  {meta['video']['fps']} fps  ·  {meta['size_mb']} MB"
          f"  ·  audio: {'yes' if meta['audio'] else 'NO'}")

    times = even_times(duration, frames)
    build_sheet(video, times, out_dir / "sheet_1.jpg", cols, tile_w, meta, scratch)
    meta["sheets"] = {"sheet_1.jpg": [round(t, 3) for t in times]}

    # Long clips: a second pass offset by half a step catches what pass 1 skipped.
    if extend and duration > 120:
        step = duration / len(times)
        later = [round(min(t + step / 2, duration - 0.05), 3) for t in times]
        try:
            build_sheet(video, later, out_dir / "sheet_2.jpg", cols, tile_w, meta, scratch)
            meta["sheets"]["sheet_2.jpg"] = later
        except RuntimeError as exc:
            print(f"  ! skipped sheet_2.jpg: {exc}")

    if peaks:
        peak_times = pick_peaks(video, duration)
        if peak_times:
            subset = peak_times[:frames]
            try:
                build_sheet(video, subset, out_dir / "peaks.jpg", cols, tile_w, meta, scratch)
                meta["peaks_jpg"] = subset
                meta["peaks_note"] = "highest scene-change scores = cuts, camera moves, montage density"
            except RuntimeError as exc:
                print(f"  ! skipped peaks.jpg: {exc}")
        else:
            meta["peaks_note"] = "no scene-change data (single static shot or filter unavailable)"

    audio = analyse_audio(video, meta, out_dir)
    if audio:
        meta["audio_analysis"] = audio
        (out_dir / "audio.json").write_text(json.dumps(audio, indent=2), encoding="utf-8")
        print(f"   audio peak {audio['peak_dbfs']} dBFS · rms {audio['rms_dbfs']} dBFS"
              f" · silence {audio['silence_percent']}%")

    shutil.rmtree(scratch, ignore_errors=True)
    (out_dir / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    (out_dir / "README.txt").write_text(how_to_read(meta, out_dir), encoding="utf-8")
    print(f"   -> {out_dir}")
    return meta


def how_to_read(meta: dict, out_dir: Path) -> str:
    lines = [
        f"How to read this folder: {out_dir.name}",
        "",
        f"Source file : {meta['file']}",
        f"Duration    : {meta['duration_hms']} ({meta['duration_seconds']}s)",
        f"Picture     : {resolution(meta)} @ {meta['video']['fps']} fps",
        f"Audio       : {meta['audio'] or 'none'}",
        "",
        "sheet_1.jpg  chronological frames, each labelled MM:SS.mmm #index.",
        "sheet_2.jpg  optional second pass (timestamps shifted by half a step) for long clips.",
        "peaks.jpg    frames with the strongest scene change - the busiest moments.",
        "audio.png    waveform; flat = silence, symmetric maxima = clipping.",
        "audio.json   peak/RMS loudness and silence share, measured from PCM.",
        "metadata.json full machine-readable summary, including the sampled timestamps.",
        "",
        "Ask for the frame numbers you want and a full-resolution still can be pulled",
        "with:  ffmpeg -ss <MM:SS.mmm> -i <video> -frames:v 1 still.png",
    ]
    return "\n".join(lines) + "\n"


def gather(target: Path) -> list[Path]:
    if target.is_file():
        if target.suffix.lower() not in VIDEO_EXTS and not target.suffix.lower() == ".gif":
            sys.exit(
                f"{target.name} is not a video file.\n"
                f"Supported: {', '.join(sorted(VIDEO_EXTS))}\n"
                "Animated GIFs also work: pass them one at a time."
            )
        return [target]
    if target.is_dir():
        return sorted(p for p in target.rglob("*")
                      if p.is_file() and p.suffix.lower() in VIDEO_EXTS)
    sys.exit(f"No such video or folder: {target}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Turn an uploaded video into contact sheets, waveform and metadata an "
                    "AI agent can read. No new dependencies beyond the editor's own FFmpeg.",
    )
    ap.add_argument("target", help="video file, or a folder that contains videos")
    ap.add_argument("--frames", type=int, default=30,
                    help="frames per contact sheet (default 30)")
    ap.add_argument("--cols", type=int, default=5, help="columns in the sheet (default 5)")
    ap.add_argument("--scale", type=int, default=420, help="tile width in px (default 420)")
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "video_review",
                    help="output folder (default: <repo>/video_review)")
    ap.add_argument("--extend", action="store_true",
                    help="add a second sheet for clips longer than 2 minutes")
    ap.add_argument("--no-peaks", action="store_true", help="skip peaks.jpg")
    ap.add_argument("--json", type=Path, help="also write all metadata to this JSON file")
    args = ap.parse_args()

    target = Path(args.target).expanduser()
    videos = gather(target)
    if not videos:
        sys.exit(f"No video files found in {target}")
    if len(videos) > 1:
        print(f"Found {len(videos)} video(s) in {target}")

    args.out.mkdir(parents=True, exist_ok=True)
    results = []
    for video in videos:
        try:
            results.append(process(video, args.out, max(4, args.frames), max(1, args.cols),
                                   max(120, args.scale), args.extend, not args.no_peaks))
        except Exception as exc:  # keep going through a batch
            print(f"  ! {video.name}: {exc}")

    if args.json and results:
        args.json.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"\nmetadata written to {args.json}")

    print(f"\nDone: {len(results)}/{len(videos)} video(s) reviewable in {args.out}")
    print("Open the sheet_*.jpg files to watch the clip frame by frame.")
    return 0 if results else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""What this computer can actually do, and how hard it is working.

Everything here is measured on the machine the app is running on. Nothing is assumed
from the user's description of their hardware: a driver that is missing, a GPU whose
encoder is not compiled into the bundled ffmpeg, or a laptop on battery all change the
answer, so the answer is read from the system each time rather than hard-coded.

The two questions this module answers:

1. May we start another heavy job right now? (`admit`)
2. Which encoder should we use for this piece of work? (`encoder_for`)

Both are deliberately conservative. A machine that is asked to do less than it can is
merely slow; a machine that is oversubscribed stops responding, and an editor that
stops responding is worse than one that renders slowly.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# psutil is the only reliable cross-platform source for CPU and memory, but the app
# must still run without it, so every call that needs it degrades to a safe default.
try:
    import psutil
except Exception:                                        # pragma: no cover
    psutil = None

# Never use the last of the memory. Below this the operating system starts paging and
# every operation, including the one being admitted, becomes slow.
MIN_FREE_MEMORY_MB = 900

# Stop starting new heavy work above this, and let the queue drain.
HEAVY_LOAD_PERCENT = 88

# Sampling hardware capabilities involves running ffmpeg, which costs a few hundred
# milliseconds. The answer changes only when drivers change, so it is cached for the
# life of the process.
_CAPABILITIES: dict | None = None


@dataclass
class Encoder:
    """One way of encoding video on this machine."""
    name: str                     # ffmpeg encoder name
    kind: str                     # 'software' | 'hardware'
    vendor: str = ''              # 'intel' | 'amd' | 'nvidia' | ''
    quality: int = 0              # relative quality at equal bitrate; higher is better

    def as_dict(self) -> dict:
        return {'name': self.name, 'kind': self.kind, 'vendor': self.vendor,
                'quality': self.quality}


@dataclass
class Snapshot:
    """A reading of what the machine is doing, and what is left over."""
    cpu_percent: float
    memory_percent: float
    memory_available_mb: float
    disk_free_gb: float | None
    load_percent: float
    pressure: str                 # 'idle' | 'busy' | 'strained'
    detail: str = ''

    def as_dict(self) -> dict:
        return {'cpu_percent': round(self.cpu_percent, 1),
                'memory_percent': round(self.memory_percent, 1),
                'memory_available_mb': round(self.memory_available_mb),
                'disk_free_gb': None if self.disk_free_gb is None else round(self.disk_free_gb, 1),
                'load_percent': round(self.load_percent, 1),
                'pressure': self.pressure, 'detail': self.detail}


def _usable_cores() -> int:
    """How many cores to plan with. Leaves one for the operating system and server."""
    try:
        total = os.cpu_count() or 2
    except Exception:                                    # pragma: no cover
        total = 2
    return max(1, total - 1)


def snapshot(cache_dir: Path | None = None) -> Snapshot:
    """One reading of CPU, memory and disk.

    `psutil.cpu_percent` is deliberately called without a blocking interval so this is
    cheap enough to call from a status endpoint every second.
    """
    if psutil is None:                                   # pragma: no cover
        return Snapshot(0.0, 0.0, 4096.0, None, 0.0, 'idle',
                        'System monitoring is off because psutil is not installed.')
    try:
        cpu = float(psutil.cpu_percent(interval=None))
        memory = psutil.virtual_memory()
        available_mb = memory.available / (1024 * 1024)
        memory_percent = float(memory.percent)
    except Exception:                                    # pragma: no cover
        return Snapshot(0.0, 0.0, 4096.0, None, 0.0, 'idle', 'System readings failed.')

    free_gb = None
    try:
        target = Path(cache_dir) if cache_dir else Path.home()
        while not target.exists() and target != target.parent:
            target = target.parent
        free_gb = shutil.disk_usage(str(target)).free / (1024 ** 3)
    except Exception:
        pass

    # Pressure is the worse of the two pressures, because either one alone can make the
    # editor unusable: a full CPU stalls the server, a full memory stalls everything.
    load = max(cpu, memory_percent)
    if available_mb < MIN_FREE_MEMORY_MB or load >= HEAVY_LOAD_PERCENT:
        pressure = 'strained'
    elif load >= 60:
        pressure = 'busy'
    else:
        pressure = 'idle'
    detail = (f'CPU {cpu:.0f}%, memory {memory_percent:.0f}% '
              f'({available_mb:.0f} MB free)')
    if free_gb is not None:
        detail += f', disk {free_gb:.1f} GB free'
    return Snapshot(cpu, memory_percent, available_mb, free_gb, load, pressure, detail)


def admit(weight: str = 'heavy') -> tuple[bool, str]:
    """May another job of this weight start now?

    `light` work is always allowed: it is the work that keeps the editor usable, and
    refusing it would make the machine feel broken in order to protect throughput.
    """
    state = snapshot()
    if weight == 'light':
        return True, ''
    if state.pressure == 'strained':
        return False, f'The computer is busy ({state.detail}).'
    if state.memory_available_mb < MIN_FREE_MEMORY_MB:
        return False, 'There is not enough free memory to start another heavy job.'
    return True, ''


def _ffmpeg_encoders(ffmpeg: str) -> set[str]:
    try:
        out = subprocess.run([ffmpeg, '-hide_banner', '-encoders'],
                             capture_output=True, text=True, timeout=30).stdout
    except Exception:                                    # pragma: no cover
        return set()
    names = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 6:
            names.add(parts[1])
    return names


def _try_encoder(ffmpeg: str, encoder: str) -> bool:
    """Does this encoder actually run here?

    Being listed by ffmpeg is not the same as working. A build can contain
    h264_nvenc on a machine with no NVIDIA driver, and the failure appears only when it
    is used. This runs one real one-frame encode, which is the only honest test.
    """
    cmd = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-f', 'lavfi',
           '-i', 'color=c=black:s=320x240:d=0.1', '-c:v', encoder, '-f', 'null', '-']
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
        return result.returncode == 0
    except Exception:
        return False


# Quality ranking at an equal bitrate, on ordinary judgement rather than a benchmark:
# hardware encoders trade compression efficiency for speed, so the software encoder is
# always the quality reference and is used for anything that is being delivered.
_SOFTWARE = Encoder('libx264', 'software', '', quality=100)


def capabilities(ffmpeg: str | None = None, refresh: bool = False) -> dict:
    """Which encoders exist, which of them work, and what is recommended for what."""
    global _CAPABILITIES
    if _CAPABILITIES is not None and not refresh:
        return _CAPABILITIES
    if ffmpeg is None:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    listed = _ffmpeg_encoders(ffmpeg)

    candidates = [
        # (name, vendor, quality, usable for what)
        ('h264_qsv', 'intel', 72),      # Intel Quick Sync, present on the i7's iGPU
        ('h264_amf', 'amd', 70),        # AMD AMF, the likely discrete GPU here
        ('h264_nvenc', 'nvidia', 78),
        ('h264_vaapi', 'generic', 68),
    ]
    working: list[Encoder] = []
    for name, vendor, quality in candidates:
        if name not in listed:
            continue
        if _try_encoder(ffmpeg, name):
            working.append(Encoder(name, 'hardware', vendor, quality))

    _CAPABILITIES = {
        'ffmpeg': ffmpeg,
        'software': _SOFTWARE.as_dict(),
        'hardware': [e.as_dict() for e in working],
        # Proxies are disposable and must be quick, so the fastest working encoder wins.
        'proxy_encoder': (max(working, key=lambda e: e.quality).name
                          if working else 'libx264'),
        # Delivery keeps the software encoder. A hardware encoder at the same bitrate
        # is visibly worse, and the brief says not to trade final quality for speed.
        'export_encoder': 'libx264',
        'hardware_available': bool(working),
        'cores': os.cpu_count() or 2,
        'planning_cores': _usable_cores(),
        'platform': sys.platform,
    }
    return _CAPABILITIES


def reset_capabilities() -> None:
    """Forget the cached answer. Used by tests and by the settings page."""
    global _CAPABILITIES
    _CAPABILITIES = None


def encoder_for(purpose: str) -> list[str]:
    """ffmpeg encoder arguments for a purpose: 'proxy', 'preview' or 'export'.

    Only disposable work takes the hardware path. Anything delivered to the user is
    encoded with the software encoder, because the difference is visible.
    """
    caps = capabilities()
    if purpose in ('proxy', 'preview') and caps['hardware_available']:
        return ['-c:v', caps['proxy_encoder']]
    return ['-c:v', caps['export_encoder']]


def thread_count() -> int:
    """x264 threads to give a background job.

    Deliberately fewer than the machine has. Background work that takes every core
    makes the editor stop responding, and a render the user cannot watch is a render
    they will assume has frozen.
    """
    return max(1, min(8, _usable_cores()))


def suitable_cache_root(preferred: Path | None) -> tuple[Path, str]:
    """Where caches should live, and why.

    Temporary files, proxies and caches are large and are read constantly while
    scrubbing, so they belong on a local disk rather than a network share. There is no
    way to prove a volume is solid state from Python, so the answer reports what it can
    see: the free space, and whether the choice is the default.
    """
    if preferred:
        return Path(preferred), 'Using the cache location you selected.'
    if sys.platform == 'win32':
        base = Path(os.environ.get('LOCALAPPDATA') or Path.home())
        return base / 'Storyroom' / 'cache', (
            'Using the local application data folder, which is on your system drive. '
            'Choose another location if that drive is not an SSD.')
    return Path.home() / '.storyroom' / 'cache', 'Using the home folder.'

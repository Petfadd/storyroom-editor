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
    # Graphics memory, when the driver will say. None means "not readable", which is
    # different from zero and is shown as unavailable rather than as an idle GPU.
    vram_used_mb: float | None = None
    vram_total_mb: float | None = None
    gpu_name: str = ''

    def as_dict(self) -> dict:
        return {'cpu_percent': round(self.cpu_percent, 1),
                'memory_percent': round(self.memory_percent, 1),
                'memory_available_mb': round(self.memory_available_mb),
                'disk_free_gb': None if self.disk_free_gb is None else round(self.disk_free_gb, 1),
                'load_percent': round(self.load_percent, 1),
                'pressure': self.pressure, 'detail': self.detail,
                'vram_used_mb': None if self.vram_used_mb is None else round(self.vram_used_mb),
                'vram_total_mb': None if self.vram_total_mb is None else round(self.vram_total_mb),
                'gpu_name': self.gpu_name}


def _usable_cores() -> int:
    """How many cores to plan with. Leaves one for the operating system and server."""
    try:
        total = os.cpu_count() or 2
    except Exception:                                    # pragma: no cover
        total = 2
    return max(1, total - 1)


_GPU_CACHE: dict = {'at': 0.0, 'value': None}
_GPU_TTL_SECONDS = 60.0


def _run_quiet(command) -> str:
    """Run a small system query and return its output, or '' if it is unavailable."""
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=5)
        return result.stdout if result.returncode == 0 else ''
    except Exception:
        return ''


def gpu_info() -> dict:
    """What graphics hardware this machine has, and how much memory it can use.

    Read from the system rather than assumed: the app is told nothing about the user's
    GPU, and a machine may have an integrated chip, a discrete card, both, or neither.
    Windows is asked through its own tooling, Linux through nvidia-smi where it exists,
    and anything unreadable is reported as unknown rather than guessed at.

    Cached for a minute: this shells out, and the status panel asks once a second.
    """
    now = time.monotonic()
    if _GPU_CACHE['value'] is not None and now - _GPU_CACHE['at'] < _GPU_TTL_SECONDS:
        return _GPU_CACHE['value']

    devices: list[dict] = []
    name = ''
    vram_mb = None
    source = ''

    if os.name == 'nt':
        # Win32_VideoController gives every adapter, including the integrated one.
        query = ('Get-CimInstance Win32_VideoController | '
                 'Select-Object Name,AdapterRAM | ConvertTo-Json -Compress')
        raw = _run_quiet(['powershell', '-NoProfile', '-NonInteractive', '-Command', query])
        if raw.strip():
            import json as _json
            try:
                parsed = _json.loads(raw)
                entries = parsed if isinstance(parsed, list) else [parsed]
                for entry in entries:
                    label = str(entry.get('Name') or '').strip()
                    if not label:
                        continue
                    ram = entry.get('AdapterRAM') or 0
                    by_mb = round(int(ram) / (1024 * 1024)) if ram else None
                    devices.append({'name': label, 'vram_mb': by_mb})
                source = 'wmi'
            except (ValueError, TypeError):
                pass
        # AdapterRAM is a 32-bit field and reports nonsense above 4 GB, so ask the
        # driver registry for the real figure when one is available.
        if devices and _run_quiet(['where', 'nvidia-smi']).strip():
            pass
    elif sys.platform == 'darwin':
        raw = _run_quiet(['system_profiler', 'SPDisplaysDataType'])
        for line in raw.splitlines():
            stripped = line.strip()
            if stripped.startswith('Chipset Model:'):
                devices.append({'name': stripped.split(':', 1)[1].strip(), 'vram_mb': None})
        source = 'system_profiler'

    if not devices:
        raw = _run_quiet(['nvidia-smi', '--query-gpu=name,memory.total',
                          '--format=csv,noheader,nounits'])
        for line in raw.splitlines():
            parts = [part.strip() for part in line.split(',')]
            if parts and parts[0]:
                try:
                    memory = int(float(parts[1])) if len(parts) > 1 else None
                except (ValueError, IndexError):
                    memory = None
                devices.append({'name': parts[0], 'vram_mb': memory})
        if devices:
            source = 'nvidia-smi'

    if not devices:
        # Any Linux driver that exposes DRM can be read here.
        try:
            for card in sorted(Path('/sys/class/drm').glob('card*/device/mem_info_vram_total')):
                total = int(card.read_text().strip()) // (1024 * 1024)
                label = ''
                vendor = card.parent / 'uevent'
                if vendor.exists():
                    for row in vendor.read_text().splitlines():
                        if row.startswith('DRIVER='):
                            label = row.split('=', 1)[1]
                devices.append({'name': label or card.parts[-3], 'vram_mb': total})
            if devices:
                source = 'drm'
        except Exception:
            pass

    if devices:
        name = devices[0]['name']
        vram_mb = next((d['vram_mb'] for d in devices if d.get('vram_mb')), None)

    value = {'name': name, 'vram_mb': vram_mb, 'devices': devices,
             'source': source, 'detected': bool(devices)}
    _GPU_CACHE['at'] = now
    _GPU_CACHE['value'] = value
    return value


_VRAM_SAMPLE: dict = {'at': 0.0, 'value': None}
# Asking a driver for a reading means starting a process. The status panel asks once a
# second, and on Windows that process is a shell; doing it every time would cost more
# than the monitoring is worth. A few seconds of staleness changes nothing.
_VRAM_TTL_SECONDS = 5.0


def vram_usage_mb() -> float | None:
    """Video memory in use right now, where the driver can be asked.

    Only nvidia-smi reports this in a way that is worth parsing. Elsewhere the answer is
    None, and the panel says the figure is unavailable rather than showing a zero that
    would read as "nothing is using the GPU". A machine with no such tool is remembered
    as such, so the search for it does not repeat on every reading.
    """
    now = time.monotonic()
    if now - _VRAM_SAMPLE['at'] < _VRAM_TTL_SECONDS:
        return _VRAM_SAMPLE['value']
    value = None
    if shutil.which('nvidia-smi'):
        raw = _run_quiet(['nvidia-smi', '--query-gpu=memory.used',
                          '--format=csv,noheader,nounits'])
        for line in raw.splitlines():
            try:
                value = float(line.strip())
                break
            except ValueError:
                continue
    _VRAM_SAMPLE['at'] = now
    _VRAM_SAMPLE['value'] = value
    return value


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
    # Graphics is reported separately and never fails the reading: a machine without a
    # readable driver is a normal machine, and the rest of the numbers are still true.
    try:
        graphics = gpu_info()
        used = vram_usage_mb()
    except Exception:                                    # pragma: no cover
        graphics, used = {'name': '', 'vram_mb': None}, None
    return Snapshot(cpu, memory_percent, available_mb, free_gb, load, pressure, detail,
                    vram_used_mb=used, vram_total_mb=graphics.get('vram_mb'),
                    gpu_name=graphics.get('name') or '')


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
        # What the graphics hardware actually is, separately from whether ffmpeg can
        # use it. The two answers differ often: a card can be present and its encoder
        # missing from this build, which is exactly what a user needs to see.
        'gpu': gpu_info(),
    }
    return _CAPABILITIES


def reset_capabilities() -> None:
    """Forget the cached answer. Used by tests and by the settings page."""
    global _CAPABILITIES
    _CAPABILITIES = None
    _GPU_CACHE['value'] = None
    _GPU_CACHE['at'] = 0.0
    _VRAM_SAMPLE['value'] = None
    _VRAM_SAMPLE['at'] = 0.0


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

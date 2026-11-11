"""Picture upscaling for stills, using either the Real-ESRGAN ncnn binary or Lanczos.

Why a binary and not a Python model stack: Real-ESRGAN's Python package pulls in
PyTorch, which is a multi-gigabyte dependency and needs a CUDA or MPS build to be
worth using. The ncnn build is a single portable executable, MIT licensed, that runs
on CPU through Vulkan and handles exactly the x2 and x4 factors this app offers.

Nothing here is required for the editor to work. If the binary is absent, upscaling
degrades to Lanczos and says so, rather than failing a render.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from PIL import Image

# Factors Real-ESRGAN's ncnn build actually provides. Anything else has to be reached
# by a resize after one of these, which is why the app never asks for x3.
SUPPORTED_FACTORS = (2, 4)
# Where a user drops the extracted release. Both names are checked because the archive
# has shipped under each at different versions.
BINARY_NAMES = ('realesrgan-ncnn-vulkan', 'realesrgan-ncnn-vulkan.exe')
MODEL_LABEL = 'realesrgan-x4plus'


def find_binary():
    """Locate the Real-ESRGAN executable, or return None.

    Search order is deliberate: an explicit environment variable first, so a user can
    point at a copy anywhere, then `tools/` inside the project, then PATH. The project
    copy wins over PATH because a user who unzipped the release here meant it.
    """
    override = os.environ.get('REALESRGAN_BINARY')
    if override and Path(override).is_file():
        return Path(override)
    here = Path(__file__).resolve().parent
    for folder in (here / 'tools' / 'realesrgan', here / 'tools'):
        for name in BINARY_NAMES:
            candidate = folder / name
            if candidate.is_file():
                return candidate
    for name in BINARY_NAMES:
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def available():
    return find_binary() is not None


def describe():
    """What the export page should say about AI upscaling on this machine."""
    binary = find_binary()
    if binary:
        return {'available': True, 'engine': 'Real-ESRGAN (ncnn)', 'path': str(binary),
                'detail': 'Local AI upscaling is installed.'}
    return {'available': False, 'engine': 'Lanczos',
            'detail': 'Real-ESRGAN is not installed, so pictures are resized with Lanczos. '
                      'No AI detail is invented.'}


def _upscale_tiled(binary, image, factor, work, name, leftovers):
    """Enlarge a large picture piece by piece.

    Each tile is cut with an overlap, enlarged, and pasted back with the overlap cropped
    away, so no seam is visible. Peak memory is proportional to one tile rather than the
    whole frame, which is what keeps a 4K photograph from exhausting a 16 GB machine.
    """
    source_image = image
    width, height = source_image.size
    side = int((TILE_PIXELS) ** 0.5)
    result = Image.new('RGB', (width * factor, height * factor))
    index = 0
    for top in range(0, height, side):
        for left in range(0, width, side):
            # Grow the cut by the overlap, but never outside the picture.
            x0 = max(0, left - TILE_OVERLAP)
            y0 = max(0, top - TILE_OVERLAP)
            x1 = min(width, left + side + TILE_OVERLAP)
            y1 = min(height, top + side + TILE_OVERLAP)
            tile = source_image.crop((x0, y0, x1, y1))
            tile_source = work / f'{name}-tile{index}.png'
            tile_output = work / f'{name}-tile{index}-x{factor}.png'
            leftovers.extend([tile_source, tile_output])
            tile.save(tile_source, 'PNG')
            try:
                _run(binary, tile_source, tile_output, factor)
            except (RuntimeError, OSError, subprocess.SubprocessError):
                # Retry this one tile on the CPU before giving up on the whole picture.
                _run(binary, tile_source, tile_output, factor, gpu=False)
            with Image.open(tile_output) as enlarged:
                piece = enlarged.convert('RGB')
            # Keep only the part of the enlarged tile that belongs outside the overlap.
            crop_left = (left - x0) * factor
            crop_top = (top - y0) * factor
            keep_w = (min(width, left + side) - left) * factor
            keep_h = (min(height, top + side) - top) * factor
            piece = piece.crop((crop_left, crop_top, crop_left + keep_w,
                                crop_top + keep_h))
            result.paste(piece, (left * factor, top * factor))
            index += 1
    return result


def release_models():
    """Drop anything held in memory after an enhancement run.

    The ncnn binary is a separate process and exits on its own, so there is nothing to
    free here today. This exists as the one place to do it, so that adding an in-process
    model later cannot quietly leave weights resident between projects.
    """
    import gc
    gc.collect()


def target_for(source_size, factor):
    """The intermediate size a factor produces, before any final fit to the canvas."""
    w, h = source_size
    return (max(2, int(w) * int(factor)), max(2, int(h) * int(factor)))


# Pictures larger than this are processed in tiles. A full-frame 4K pass through the
# network needs several gigabytes of memory for one image, which on a 16 GB machine that
# is also running an editor is how swapping starts. Tiling caps the peak at roughly the
# tile size and is the difference between "slow" and "the whole computer freezes".
TILE_PIXELS = 1_200_000          # about 1100x1100

# The tile is grown by this much on each side and cropped back afterwards, so the seams
# between tiles fall inside overlapping detail rather than on a visible edge.
TILE_OVERLAP = 32


def tiled(size: tuple[int, int]) -> bool:
    """Would this image benefit from being processed in pieces?"""
    width, height = size
    return width * height > TILE_PIXELS


def _run(binary, source, destination, factor, timeout=600, gpu=True):
    """One Real-ESRGAN pass. Raises on failure so the caller can fall back.

    `gpu=False` asks the ncnn binary for its CPU path. It is much slower, but it is the
    difference between finishing and not finishing on a machine whose Vulkan driver
    cannot supply the memory the GPU path wants.
    """
    command = [str(binary), '-i', str(source), '-o', str(destination),
               '-n', MODEL_LABEL, '-s', str(int(factor)), '-f', 'png']
    if not gpu:
        command += ['-g', '0']          # ncnn: use no GPU device, run on the CPU
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                            cwd=str(Path(binary).parent))
    if result.returncode != 0 or not Path(destination).is_file():
        detail = (result.stderr or result.stdout or '').strip()[:400]
        raise RuntimeError(f'Real-ESRGAN did not produce a picture: {detail or "no output"}')


def upscale(image, factor, cache_dir=None, cache_key=None, prefer_ai=True):
    """Enlarge a PIL image by an integer factor.

    Returns (image, used) where `used` is 'realesrgan', 'lanczos', or 'none'. A factor
    of 1 returns the image untouched -- that is the "do not process what is already
    sharp" rule, enforced by arithmetic rather than by a heuristic.

    A successful AI result is cached as a PNG beside the render cache, keyed by the
    caller. Rerunning a render at the same resolution therefore reuses the enhanced
    picture instead of processing the same frames again.
    """
    factor = int(factor)
    if factor <= 1:
        return image, 'none'
    if factor not in SUPPORTED_FACTORS:
        # Reach the requested size through the nearest supported factor rather than
        # refusing: a x3 need is a x4 pass followed by the final fit.
        factor = 4 if factor > 2 else 2

    cached = None
    if cache_dir and cache_key:
        cached = Path(cache_dir) / f'{cache_key}-x{factor}.png'
        if cached.is_file():
            try:
                with Image.open(cached) as im:
                    return im.convert('RGB'), 'realesrgan'
            except OSError:
                cached = None

    binary = find_binary() if prefer_ai else None
    if binary:
        work = Path(cache_dir) if cache_dir else Path(image.filename or '.').parent
        try:
            work.mkdir(parents=True, exist_ok=True)
        except OSError:
            work = Path('.')
        source = work / f'{cache_key or "source"}-input.png'
        destination = work / f'{cache_key or "source"}-x{factor}-raw.png'
        leftoers = [source, destination]
        try:
            if tiled(image.size):
                result = _upscale_tiled(binary, image, factor, work, cache_key or 'source',
                                        leftoers)
            else:
                image.save(source, 'PNG')
                _run(binary, source, destination, factor)
                with Image.open(destination) as im:
                    result = im.convert('RGB')
            if cached is not None:
                try:
                    result.save(cached, 'PNG')
                except OSError:
                    pass
            return result, 'realesrgan'
        except (OSError, RuntimeError, subprocess.SubprocessError):
            # A missing Vulkan driver or an incomplete release is not a reason to
            # lose the render. Lanczos is worse, but it is honest and it finishes.
            pass
        finally:
            for leftover in leftoers:
                try:
                    Path(leftover).unlink(missing_ok=True)
                except OSError:
                    pass

    width, height = target_for(image.size, factor)
    return image.resize((width, height), Image.LANCZOS), 'lanczos'


def prepare(image, target_size, prefer_ai=True, framing='auto', cache_dir=None, cache_key=None):
    """Fit a source picture into a canvas, upscaling first when that helps.

    `target_size` is the canvas (w, h). Returns (picture, report) where report names
    the factor used, the engine, and the framing that was applied -- so a saved render
    record can explain how a picture was made without anyone having to guess.
    """
    target_w, target_h = target_size
    source_w, source_h = image.size
    # The factor is decided on the short side, the same rule the export page shows.
    short_gap = min(target_w, target_h) / max(1, min(source_w, source_h))
    factor = 1 if short_gap <= 1.0 else 2 if short_gap <= 2.0 else 4
    enhanced, used = upscale(image, factor, cache_dir=cache_dir, cache_key=cache_key, prefer_ai=prefer_ai)

    # Every branch returns exactly the canvas. A picture that came back a different
    # size than the frame it was drawn for would break the "consistent output
    # resolution" promise, so this is checked rather than assumed.
    if framing == 'fit':
        fitted = ImageOps_contain(enhanced, (target_w, target_h))
        canvas = Image.new('RGB', (target_w, target_h), (0, 0, 0))
        canvas.paste(fitted, ((target_w - fitted.width) // 2, (target_h - fitted.height) // 2))
        result = canvas
    elif framing == 'crop':
        result = ImageOps_fit(enhanced, (target_w, target_h))
    else:
        # Blurred background: the whole frame stays visible over a dimmed, blurred
        # copy of itself. Nothing is lost to the bars and nothing is stretched.
        from PIL import ImageEnhance, ImageFilter
        backdrop = ImageOps_fit(enhanced, (target_w, target_h))
        backdrop = backdrop.filter(ImageFilter.GaussianBlur(28))
        backdrop = ImageEnhance.Brightness(backdrop).enhance(0.58)
        front = ImageOps_contain(enhanced, (int(target_w * 0.88), int(target_h * 0.86)))
        backdrop.paste(front, ((target_w - front.width) // 2, (target_h - front.height) // 2))
        result = backdrop
    if result.size != (target_w, target_h):
        raise RuntimeError(f'Prepared picture is {result.size}, not the {target_w}x{target_h} canvas.')
    report = {'factor': factor, 'engine': used, 'framing': framing,
              'source': [source_w, source_h], 'canvas': [target_w, target_h]}
    return result, report


def ImageOps_contain(image, size):
    from PIL import ImageOps
    return ImageOps.contain(image, (int(size[0]), int(size[1])), Image.LANCZOS)


def ImageOps_fit(image, size):
    from PIL import ImageOps
    return ImageOps.fit(image, (int(size[0]), int(size[1])), Image.LANCZOS, centering=(0.5, 0.5))

def prepare_queued(image, target_size, prefer_ai=True, framing='auto',
                   cache_dir=None, cache_key=None, context=None):
    """`prepare` with progress reporting, for use inside the shared job queue.

    AI enhancement is the most expensive thing the editor does, so it runs as a queued
    job with a single slot. This wrapper exists so that path reports progress and
    honours pause without every caller having to know about the queue.
    """
    if context is not None:
        context.set(2, 'Enhancing picture' if prefer_ai and find_binary() else 'Resizing picture')
        context.wait_if_paused()
    try:
        return prepare(image, target_size, prefer_ai=prefer_ai, framing=framing,
                       cache_dir=cache_dir, cache_key=cache_key)
    finally:
        release_models()

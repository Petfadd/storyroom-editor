"""Turn a downloaded photograph into a silent hold clip a documentary Beat can use.

A documentary Beat names a video and a source range; it has no Photograph field, and
the documentary renderer, frame audit and preview all refuse an asset whose kind is
not `video`. A photograph therefore cannot be named directly by a Beat. What a Beat
*can* name is a silent video that happens to hold a still picture. This script makes
that file, so a face can be on screen while the narrator says the name.

One clip per person, long enough to cover every mention of that person:

    python tools/photo_to_clip.py photos/ --out clips/ --seconds 90

then in each Beat that mentions the person, name that one clip with a different,
non-overlapping source window per mention:

    Beat: 012
    Full source: Maliyah_Bass_hold.mp4
    Source in: 00:00:00,000
    Source out: 00:00:03,500
    Footage audio: MUTED (narration only, 0% source level)

    Beat: 019
    Full source: Maliyah_Bass_hold.mp4
    Source in: 00:00:20,000      <- same person, same clip, its own window
    Source out: 00:00:23,500
    Footage audio: MUTED (narration only, 0% source level)

Why a window per mention and not the same window twice: two Beats that reuse one
source interval are an unapproved overlap, and the builder refuses them. Separate
windows are accepted without any approval step, and because every frame of a hold is
identical the audience sees the same photograph both times.

Nothing here identifies anyone in a photograph or checks rights; both stay a manual
review step. FFmpeg is the one this app already bundles - never install one.
"""
import argparse
import subprocess
import tempfile
import sys
from fractions import Fraction
from pathlib import Path

SUFFIXES = {'.jpg', '.jpeg', '.png', '.webp'}
# The documentary output rate. A source that is already at this rate is never
# resampled, so the hold clip carries no frame-conversion surprise.
OUTPUT_RATE = Fraction(30000, 1001)
# Every frame of a hold is the same picture, so quality costs nothing per second.
# CRF 20 keeps a 90 s 1080p hold near a few hundred kilobytes.
CRF = '20'


def ffmpeg():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def probe_frames(path, rate):
    """Frame count and duration actually decoded, not what the header claims."""
    import av
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        frames = stream.frames or 0
        return frames, frames / float(rate)


def output_size(width, height):
    """Fill the 1080-high frame without stretching.

    A downloaded news photograph is usually small - 326x415, 225x225 - and a face
    that keeps its native size inside a 1920x1080 frame is a postage stamp in the
    middle of the screen. The image is enlarged to fill the height, which is what a
    documentary does with a family photograph: the source files here are stills, so
    there is no motion to smear and lanczos keeps the edges clean. The height is the
    limit, never the width, because a portrait must stay portrait.
    """
    scale = 1080 / height
    return round(width * scale / 2) * 2, round(height * scale / 2) * 2


def background_graph(target, style):
    """Filters that put the photograph on a 1920x1080 canvas without stretching it.

    `blur` fills the sides of a portrait still with an enlarged, blurred copy of the
    same picture - the standard documentary treatment for a mugshot or a family
    photograph, and it keeps the frame from looking like a slide in a black box.
    `black` is the plain letterbox the app's own renderer produces, kept because a
    production may already be matching that look.
    """
    fit = f'scale={target[0]}:{target[1]}:flags=lanczos'
    # A 16:9 photograph needs no background: the fitted copy already fills the frame.
    if style == 'black' or (target[0], target[1]) == (1920, 1080):
        return f'[0:v]{fit},pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[out]'
    # Underneath: the same picture, filled to the frame, blurred hard and darkened so
    # it never competes with the face. `crop` is centre-weighted, so the middle of the
    # photograph - where a face is - stays behind the sharp copy rather than a corner.
    # The foreground is padded to the full canvas *before* the overlay follows. A 4:3
    # photograph fitted into a 16:9 canvas is 1620x1215, which is not 1920 wide, so
    # overlaying it unpadded at (W-w)/2 leaves the blurred layer showing at both sides
    # as a softer band - it looked like black bars once the blur was this subtle.
    # The foreground is padded to the canvas with *transparent* pixels, not black. An
    # opaque pad covers the blurred layer completely and the frame comes back with the
    # plain black bars this treatment exists to remove. Padding to full size first is
    # still necessary: a 4:3 still is 1620 wide, and overlaying it unpadded at
    # (W-w)/2 leaves the blurred layer showing through as a visibly softer band.
    return ('[0:v]split=2[bgsrc][fgsrc];'
            '[bgsrc]scale=1920:1080:force_original_aspect_ratio=increase,'
            'crop=1920:1080,gblur=sigma=45,eq=brightness=-0.05:saturation=0.75[bg];'
            f'[fgsrc]{fit},format=rgba,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=black@0[fg];'
            '[bg][fg]overlay=0:0:format=auto,setsar=1[out]')


def convert(photo, out, seconds, rate=OUTPUT_RATE, style='blur'):
    """One silent, still frame-perfect clip, composited into a 1920x1080 frame.

    The picture is never stretched and never cropped: a portrait keeps the whole
    face, which is the reason the photograph is on screen at all.

    The frame is composited to a single PNG first, and only that finished frame is
    looped into the encoder. Blurring a 1920x1080 canvas is expensive, and doing it
    per frame made a 90-second hold take 70 seconds; every frame of a hold is the
    same picture, so it is built once and repeated. The still is a lossless PNG, so
    the copy inside the clip is exactly the composite a reviewer would see.

    One extra frame is always encoded beyond the requested length. A Beat's source
    OUT is an *exclusive* frame index, so a window that ends exactly at the clip's
    last frame asks for a frame that does not exist and the build refuses it with
    "source ended after N frames, before the exclusive OUT frame N+1". The spare
    frame is the same picture, so it costs nothing and makes the last usable
    timestamp safe instead of one frame short.
    """
    from PIL import Image
    with Image.open(photo) as im:
        width, height = im.size
    target = output_size(width, height)
    # A single upscale of the original keeps the detail the downloaded file has.
    graph = background_graph(target, style)
    spare = 1 / float(rate)
    with tempfile.TemporaryDirectory() as work:
        still = Path(work) / 'frame.png'
        frame = subprocess.run([ffmpeg(), '-y', '-hide_banner', '-loglevel', 'error',
                                '-i', str(photo), '-filter_complex', graph,
                                '-map', '[out]', '-frames:v', '1', str(still)],
                               capture_output=True, text=True)
        if frame.returncode != 0:
            raise SystemExit(f'{photo.name}: FFmpeg could not prepare the frame.\n{frame.stderr.strip()[:600]}')
        cmd = [ffmpeg(), '-y', '-hide_banner', '-loglevel', 'error',
               '-loop', '1', '-i', str(still),
               # A silent stereo track, because every source this app accepts must have
               # audio, and because a Beat with MUTED footage still expects a stream.
               '-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=48000',
               '-t', f'{seconds + spare:.6f}', '-r', str(rate),
               '-c:v', 'libx264', '-crf', CRF, '-preset', 'ultrafast', '-tune', 'stillimage',
               '-pix_fmt', 'yuv420p',
               '-c:a', 'aac', '-b:a', '96k', '-shortest',
               str(out)]
        result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f'{photo.name}: FFmpeg failed.\n{result.stderr.strip()[:600]}')
    return probe_frames(out, rate)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('photos', help='A photograph, or a folder of them.')
    parser.add_argument('--out', required=True, help='Folder for the clips.')
    parser.add_argument('--seconds', type=float, default=90.0,
                        help='Length of each hold (default 90). Long enough for every '
                             'mention of one person; frames are all identical, so the '
                             'only cost is disk - about 500 KB for 90 s.')
    parser.add_argument('--background', choices=('blur', 'black'), default='blur',
                        help='How a photograph that is not 16:9 fills the frame. '
                             '"blur" (default) fills the sides with a blurred copy of '
                             'the same picture; "black" leaves the plain letterbox the '
                             'app\'s own renderer uses.')
    args = parser.parse_args()
    source = Path(args.photos)
    photos = sorted(p for p in ([source] if source.is_file() else source.iterdir())
                    if p.suffix.lower() in SUFFIXES)
    if not photos:
        raise SystemExit(f'No JPG, PNG or WebP photographs found in {source}.')
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f'Holding each photograph for {args.seconds:g}s at {float(OUTPUT_RATE):.2f} fps.')
    print('Each mention in a Beat takes its own source window inside its clip.\n')
    failures = 0
    for photo in photos:
        # `_hold` marks the file as a made hold, so it is never mistaken for an
        # original download while reviewing rights.
        out = out_dir / f'{photo.stem}_hold.mp4'
        try:
            frames, duration = convert(photo, out, args.seconds, style=args.background)
        except SystemExit as error:
            print(f'  FAILED  {photo.name}: {error}')
            failures += 1
            continue
        windows = int(args.seconds // 3)
        print(f'  {photo.name} -> {out.name}  {frames} frames, {duration:.2f}s')
        print(f'     up to about {windows} separate 3 s mentions: '
              f'{", ".join(f"00:00:{i * 3:02d},000" for i in range(min(windows, 5)))} ...')
    if failures:
        raise SystemExit(f'{failures} photograph(s) could not be converted.')
    print('\nDone. Upload the clips with the footage, then name one in each Beat that mentions the person.')
    return 0


if __name__ == '__main__':
    sys.exit(main())

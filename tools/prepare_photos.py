"""Prepare downloaded photographs of people for a documentary, before the hold clip.

Downloads are rarely clean portraits. Three things turn up constantly and each one
puts the wrong picture on screen or breaks the build:

  * a blurred pillarbox baked into the file (a news site's own "vertical video"
    treatment), so the picture arrives with fake black bars that our own blurred
    background then frames twice;
  * two faces side by side - a mugshot pair, a mother-and-child split - where the
    script needs ONE person, and cutting to it shows somebody the narration did not
    name;
  * a watermark or channel ident across the corner.

This tool does not guess who anybody is. It shows you what is in the folder, lets you
name each file for the person it shows, cuts the useful region out, and writes a clean
JPG per person ready for tools/photo_to_clip.py.

    python tools/prepare_photos.py photos/originals --out photos/prepared --review
    python tools/prepare_photos.py photos/originals --out photos/prepared \
        --crop "MALIYAH BASS.jpeg=206,0,532,415:Sahara Ervin 1.jpg=0,0,960,1080"

The `--crop` value is `box` per file, where box is `left,top,right,bottom` in the
source image's own pixels. `--review` writes a labelled contact sheet of every result
so a person can confirm the face before it is encoded into a clip.
"""
import argparse
import subprocess
import sys
from pathlib import Path

SUFFIXES = {'.jpg', '.jpeg', '.png', '.webp'}


def parse_crops(values):
    """`file.jpg=L,T,R,B` pairs, repeated or separated by `;`."""
    crops = {}
    for value in values or []:
        for part in value.split(';'):
            part = part.strip()
            if not part:
                continue
            if '=' not in part:
                raise SystemExit(f'--crop needs NAME=L,T,R,B, got {part!r}')
            name, box = part.rsplit('=', 1)
            try:
                left, top, right, bottom = (int(n) for n in box.split(','))
            except ValueError:
                raise SystemExit(f'--crop box must be four integers for {name!r}')
            if right <= left or bottom <= top:
                raise SystemExit(f'--crop box is empty for {name!r}')
            crops[name.strip()] = (left, top, right, bottom)
    return crops


def prepare(source, out_dir, crops):
    from PIL import Image
    source, out_dir = Path(source), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in (source.iterdir() if source.is_dir() else [source])
                   if p.suffix.lower() in SUFFIXES)
    if not files:
        raise SystemExit(f'No JPG, PNG or WebP files in {source}.')
    written = []
    for photo in files:
        box = crops.get(photo.name)
        with Image.open(photo) as im:
            frame = im.convert('RGB')
            if box:
                left, top, right, bottom = box
                if right > frame.width or bottom > frame.height:
                    raise SystemExit(f'{photo.name}: crop {box} is outside {frame.size}.')
                frame = frame.crop(box)
            # Name the output after the person, not the download. `MALIYAH BASS.jpeg`
            # and `Maliyah Rosalie Bass.jpeg` are two files of one child; a script that
            # names a clip per file would cut between them for no reason.
            target = out_dir / (photo.stem + '.jpg')
            frame.save(target, quality=95, subsampling=0)
            written.append((photo.name, target, box, frame.size))
            print(f'  {photo.name} -> {target.name}  {frame.width}x{frame.height}'
                  + (f'  cropped {left},{top},{right},{bottom}' if box else ''))
    return written


def contact_sheet(written, out_dir):
    """One labelled row so a human can check every face before encoding."""
    from PIL import Image, ImageDraw
    if not written:
        return None
    tile_h = 320
    tiles = []
    for source, target, *_ in written:
        with Image.open(target) as im:
            copy = im.copy()
        copy.thumbnail((420, tile_h))
        tiles.append((target.name, copy))
    width = sum(t.width for _, t in tiles) + 12 * (len(tiles) + 1)
    height = tile_h + 46
    sheet = Image.new('RGB', (width, height), (24, 24, 24))
    draw = ImageDraw.Draw(sheet)
    x = 12
    for name, tile in tiles:
        sheet.paste(tile, (x, 10))
        draw.text((x + 2, tile.height + 16), name[:44], fill=(240, 240, 120))
        x += tile.width + 12
    path = out_dir / '_contact_sheet.jpg'
    sheet.save(path, quality=90)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('photos', help='A photograph, or a folder of downloads.')
    parser.add_argument('--out', required=True, help='Folder for the prepared JPGs.')
    parser.add_argument('--crop', action='append', metavar='NAME=L,T,R,B',
                        help='Cut one file to a region. Repeat, or join with ";".')
    parser.add_argument('--review', action='store_true',
                        help='Write _contact_sheet.jpg of every result into --out.')
    args = parser.parse_args()
    crops = parse_crops(args.crop)
    written = prepare(args.photos, args.out, crops)
    print(f'\n{len(written)} photograph(s) prepared in {args.out}.')
    print('Check each face with --review, then:')
    print(f'  python tools/photo_to_clip.py {args.out} --out clips --seconds 90')
    if args.review:
        sheet = contact_sheet(written, Path(args.out))
        if sheet:
            print(f'\nContact sheet: {sheet}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

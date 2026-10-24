"""Turn the viewer's ``-DemoCapture`` output into the README images.

    python viewer/tools/make_demo_media.py <capture folder> [--out docs/images]

Stills are resized to a sensible width and the ``viewer-demo_###.png`` frame
sequence becomes ``viewer-demo.gif``. Needs Pillow, which matplotlib installs.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

STILLS = ["viewer-orbit", "viewer-ballcam", "viewer-topdown"]
SEQUENCE = "viewer-demo"


def resize(image: Image.Image, width: int) -> Image.Image:
    if image.width <= width:
        return image
    return image.resize((width, round(image.height * width / image.width)), Image.Resampling.LANCZOS)


def make_gif(frames: list[Path], out: Path, width: int, fps: float) -> None:
    images = [resize(Image.open(f).convert("RGB"), width) for f in frames]
    # One palette shared by every frame, built from a sample of them, so colors
    # don't flicker from frame to frame.
    sample = images[:: max(1, len(images) // 8)]
    strip = Image.new("RGB", (width, sum(i.height for i in sample)))
    y = 0
    for image in sample:
        strip.paste(image, (0, y))
        y += image.height
    palette = strip.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
    frames_p = [i.quantize(palette=palette, dither=Image.Dither.NONE) for i in images]
    frames_p[0].save(out, save_all=True, append_images=frames_p[1:], duration=round(1000 / fps), loop=0, optimize=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("capture", type=Path, help="Folder passed to -DemoCapture")
    parser.add_argument("--out", type=Path, default=Path("docs/images"))
    parser.add_argument("--still-width", type=int, default=1600)
    parser.add_argument("--gif-width", type=int, default=800)
    parser.add_argument("--fps", type=float, default=15.0, help="Must match ARLDemoCapture::SequenceFps")
    args = parser.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    for name in STILLS:
        source = args.capture / f"{name}.png"
        if source.exists():
            target = args.out / f"{name}.png"
            resize(Image.open(source).convert("RGB"), args.still_width).save(target, optimize=True)
            print(f"wrote {target} ({target.stat().st_size / 1e6:.1f} MB)")
        else:
            print(f"missing {source}")

    frames = sorted(args.capture.glob(f"{SEQUENCE}_*.png"))
    if frames:
        target = args.out / f"{SEQUENCE}.gif"
        make_gif(frames, target, args.gif_width, args.fps)
        print(f"wrote {target} from {len(frames)} frames ({target.stat().st_size / 1e6:.1f} MB)")
    else:
        print(f"no {SEQUENCE}_###.png frames in {args.capture}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

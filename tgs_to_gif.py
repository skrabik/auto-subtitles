from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError as exc:
    raise SystemExit("Install dependencies first: python -m pip install pillow rlottie-python") from exc

try:
    from rlottie_python import LottieAnimation
except ImportError as exc:
    raise SystemExit("Install dependencies first: python -m pip install pillow rlottie-python") from exc


DEFAULT_FPS = 30


def gif_ready_frame(frame: Image.Image) -> Image.Image:
    """Convert RGBA to a GIF palette frame with transparent index 255."""
    rgba = frame.convert("RGBA")
    alpha = rgba.getchannel("A")
    paletted = rgba.convert("RGB").convert("P", palette=Image.ADAPTIVE, colors=255)
    transparent_mask = Image.eval(alpha, lambda value: 255 if value <= 128 else 0)
    paletted.paste(255, mask=transparent_mask)
    paletted.info["transparency"] = 255
    return paletted


def output_size(animation: LottieAnimation, requested_size: int | None) -> tuple[int, int]:
    width, height = animation.lottie_animation_get_size()
    if requested_size is None:
        return int(width), int(height)

    longest_side = max(width, height)
    if longest_side <= 0:
        raise ValueError("Animation has invalid viewport size.")

    scale = requested_size / longest_side
    return max(1, round(width * scale)), max(1, round(height * scale))


def convert_tgs_to_gif(
    source: Path,
    destination: Path,
    fps: int,
    size: int | None,
    overwrite: bool,
) -> None:
    if destination.exists() and not overwrite:
        print(f"skip: {destination} already exists")
        return

    with LottieAnimation.from_tgs(str(source)) as animation:
        width, height = output_size(animation, size)
        total_frames = int(animation.lottie_animation_get_totalframe())
        source_fps = float(animation.lottie_animation_get_framerate()) or fps

        if total_frames <= 0:
            raise ValueError("Animation has no frames.")

        frame_step = max(1, math.ceil(source_fps / fps))
        duration_ms = max(10, round(1000 * frame_step / source_fps))

        frames = [
            gif_ready_frame(
                animation.render_pillow_frame(
                    frame_num=frame_number,
                    width=width,
                    height=height,
                )
            )
            for frame_number in range(0, total_frames, frame_step)
        ]

    destination.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        destination,
        format="GIF",
        append_images=frames[1:],
        save_all=True,
        duration=duration_ms,
        loop=0,
        transparency=255,
        disposal=2,
        optimize=False,
    )
    print(f"ok: {source.name} -> {destination}")


def find_tgs_files(input_path: Path, recursive: bool) -> list[Path]:
    if input_path.is_file():
        return [input_path] if input_path.suffix.lower() == ".tgs" else []

    pattern = "**/*.tgs" if recursive else "*.tgs"
    return sorted(input_path.glob(pattern))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert Telegram .tgs stickers to animated GIF files.")
    parser.add_argument("input", type=Path, help="Path to a .tgs file or a folder with .tgs files.")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output .gif file or folder. Defaults to a 'gif' folder inside the input folder.",
    )
    parser.add_argument("--fps", type=int, default=DEFAULT_FPS, help=f"Target GIF FPS. Default: {DEFAULT_FPS}.")
    parser.add_argument(
        "--size",
        type=int,
        default=None,
        help="Resize longest side to this many pixels. Defaults to the sticker's original size.",
    )
    parser.add_argument("--recursive", action="store_true", help="Convert .tgs files in nested folders too.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing GIF files.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = args.input.expanduser().resolve()

    if args.fps <= 0:
        print("--fps must be greater than 0", file=sys.stderr)
        return 2

    if args.size is not None and args.size <= 0:
        print("--size must be greater than 0", file=sys.stderr)
        return 2

    if not input_path.exists():
        print(f"Input path does not exist: {input_path}", file=sys.stderr)
        return 2

    sources = find_tgs_files(input_path, args.recursive)
    if not sources:
        print(f"No .tgs files found in: {input_path}", file=sys.stderr)
        return 1

    if input_path.is_file():
        output_path = args.output or input_path.with_suffix(".gif")
        if len(sources) != 1:
            print("Expected a single .tgs file.", file=sys.stderr)
            return 2
        destinations = [(sources[0], output_path.expanduser().resolve())]
    else:
        output_dir = (args.output or (input_path / "gif")).expanduser().resolve()
        destinations = [(source, output_dir / f"{source.stem}.gif") for source in sources]

    failures = 0
    for source, destination in destinations:
        try:
            convert_tgs_to_gif(source, destination, args.fps, args.size, args.overwrite)
        except Exception as exc:
            failures += 1
            print(f"error: {source}: {exc}", file=sys.stderr)

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

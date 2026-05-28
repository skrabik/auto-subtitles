from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920


@dataclass(frozen=True)
class Caption:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class SubtitleStyle:
    font_size: int = 114
    outline: int = 3
    shadow: int = 1
    margin_v: int = 370
    primary_color: tuple[int, int, int] = (255, 255, 255)
    outline_color: tuple[int, int, int] = (0, 0, 0)


def run_command(command: list[str], cwd: Path | None = None) -> None:
    print("+ " + " ".join(command))
    try:
        subprocess.run(command, cwd=cwd, check=True)
    except FileNotFoundError as exc:
        missing = command[0]
        raise SystemExit(
            f"Command '{missing}' was not found. Install it and make sure it is in PATH."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"Command failed with exit code {exc.returncode}: {command}") from exc


def require_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise SystemExit(
            "ffmpeg was not found in PATH. Install ffmpeg first: https://ffmpeg.org/download.html"
        )


def extract_audio(video_path: Path, audio_path: Path) -> None:
    run_command(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(audio_path),
        ]
    )


def transcribe_audio(
    audio_path: Path,
    model_size: str,
    language: str | None,
    device: str,
    compute_type: str,
    max_words: int,
) -> list[Caption]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise SystemExit(
            "Missing dependency 'faster-whisper'. Install dependencies with: "
            "pip install -r requirements.txt"
        ) from exc

    model = WhisperModel(model_size, device=device, compute_type=compute_type)
    segments, _info = model.transcribe(
        str(audio_path),
        language=language,
        vad_filter=True,
        beam_size=5,
    )

    captions: list[Caption] = []
    for segment in segments:
        text = " ".join(segment.text.strip().split())
        if text:
            captions.extend(split_caption(segment.start, segment.end, text, max_words=max_words))

    return captions


def split_caption(start: float, end: float, text: str, max_words: int = 2) -> list[Caption]:
    """Keep captions punchy for vertical social-video layouts."""
    words = text.split()
    if len(words) <= max_words:
        return [Caption(start, end, text)]

    chunks = [words[index : index + max_words] for index in range(0, len(words), max_words)]

    duration = max(end - start, 0.1)
    total_words = sum(len(chunk) for chunk in chunks)
    result: list[Caption] = []
    cursor = start

    for index, chunk in enumerate(chunks):
        if index == len(chunks) - 1:
            chunk_end = end
        else:
            chunk_duration = duration * (len(chunk) / total_words)
            chunk_end = min(end, cursor + chunk_duration)
        result.append(Caption(cursor, chunk_end, " ".join(chunk)))
        cursor = chunk_end

    return result


def create_ass_subtitles(
    captions: list[Caption], ass_path: Path, subtitle_style: SubtitleStyle
) -> None:
    try:
        import pysubs2
    except ImportError as exc:
        raise SystemExit(
            "Missing dependency 'pysubs2'. Install dependencies with: "
            "pip install -r requirements.txt"
        ) from exc

    subs = pysubs2.SSAFile()
    subs.info["PlayResX"] = str(TARGET_WIDTH)
    subs.info["PlayResY"] = str(TARGET_HEIGHT)

    style = pysubs2.SSAStyle()
    style.fontname = "Arial"
    style.fontsize = subtitle_style.font_size
    style.primarycolor = pysubs2.Color(*subtitle_style.primary_color)
    style.outlinecolor = pysubs2.Color(*subtitle_style.outline_color)
    style.backcolor = pysubs2.Color(0, 0, 0, 90)
    style.bold = True
    style.outline = subtitle_style.outline
    style.shadow = subtitle_style.shadow
    style.alignment = 2
    style.marginl = 90
    style.marginr = 90
    style.marginv = subtitle_style.margin_v
    subs.styles["Default"] = style

    for caption in captions:
        subs.append(
            pysubs2.SSAEvent(
                start=pysubs2.make_time(s=caption.start),
                end=pysubs2.make_time(s=caption.end),
                text=caption.text,
                style="Default",
            )
        )

    subs.save(str(ass_path))


def render_vertical_video(video_path: Path, output_path: Path, workdir: Path) -> None:
    # The ASS file is referenced by a simple relative name to avoid Windows path escaping issues.
    video_filter = (
        f"scale={TARGET_WIDTH}:{TARGET_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={TARGET_WIDTH}:{TARGET_HEIGHT},setsar=1,subtitles=filename='captions.ass'"
    )

    run_command(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-vf",
            video_filter,
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(output_path),
        ],
        cwd=workdir,
    )


def default_output_path(input_path: Path) -> Path:
    return input_path.with_name(f"{input_path.stem}_vertical_subtitled.mp4")


def generate_captions(
    input_path: Path,
    model_size: str = "small",
    language: str | None = None,
    device: str = "cpu",
    compute_type: str = "int8",
    max_words: int = 2,
    progress_callback: Callable[[int, str], None] | None = None,
) -> list[Caption]:
    def report(progress: int, message: str) -> None:
        if progress_callback is not None:
            progress_callback(progress, message)

    if not input_path.exists():
        raise SystemExit(f"Input video does not exist: {input_path}")

    report(5, "Checking ffmpeg...")
    require_ffmpeg()

    with tempfile.TemporaryDirectory(prefix="auto_subtitle_vertical_") as temp_dir:
        workdir = Path(temp_dir)
        audio_path = workdir / "audio.wav"

        print("Extracting audio...")
        report(15, "Extracting audio...")
        extract_audio(input_path, audio_path)

        print("Transcribing audio...")
        report(35, "Generating subtitles...")
        captions = transcribe_audio(
            audio_path=audio_path,
            model_size=model_size,
            language=language,
            device=device,
            compute_type=compute_type,
            max_words=max_words,
        )

        if not captions:
            raise SystemExit("No speech was detected, so subtitles were not generated.")

    report(70, f"Generated {len(captions)} captions.")
    return captions


def render_video_with_captions(
    input_path: Path,
    output_path: Path,
    captions: list[Caption],
    subtitle_style: SubtitleStyle | None = None,
    keep_subtitles: bool = False,
    progress_callback: Callable[[int, str], None] | None = None,
) -> Path:
    def report(progress: int, message: str) -> None:
        if progress_callback is not None:
            progress_callback(progress, message)

    if not input_path.exists():
        raise SystemExit(f"Input video does not exist: {input_path}")

    if not captions:
        raise SystemExit("No subtitles were provided.")

    report(70, f"Creating subtitles ({len(captions)} captions)...")
    require_ffmpeg()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    subtitle_style = subtitle_style or SubtitleStyle()

    with tempfile.TemporaryDirectory(prefix="auto_subtitle_vertical_") as temp_dir:
        workdir = Path(temp_dir)
        ass_path = workdir / "captions.ass"

        print(f"Creating subtitles ({len(captions)} captions)...")
        create_ass_subtitles(captions, ass_path, subtitle_style=subtitle_style)

        print("Rendering vertical video...")
        report(85, "Rendering vertical video...")
        render_vertical_video(input_path, output_path, workdir)

        if keep_subtitles:
            subtitles_output = output_path.with_suffix(".ass")
            shutil.copy2(ass_path, subtitles_output)
            print(f"Saved subtitles: {subtitles_output}")

    report(100, "Video is ready.")
    return output_path


def process_video(
    input_path: Path,
    output_path: Path,
    model_size: str = "small",
    language: str | None = None,
    device: str = "cpu",
    compute_type: str = "int8",
    max_words: int = 2,
    subtitle_style: SubtitleStyle | None = None,
    keep_subtitles: bool = False,
    progress_callback: Callable[[int, str], None] | None = None,
) -> Path:
    captions = generate_captions(
        input_path=input_path,
        model_size=model_size,
        language=language,
        device=device,
        compute_type=compute_type,
        max_words=max_words,
        progress_callback=progress_callback,
    )
    return render_video_with_captions(
        input_path=input_path,
        output_path=output_path,
        captions=captions,
        subtitle_style=subtitle_style,
        keep_subtitles=keep_subtitles,
        progress_callback=progress_callback,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a vertical 9:16 video with automatic burned-in subtitles."
    )
    parser.add_argument("input", type=Path, help="Path to the source video file.")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output .mp4 path. Defaults to '<input>_vertical_subtitled.mp4'.",
    )
    parser.add_argument(
        "--model",
        default="small",
        help="faster-whisper model size: tiny, base, small, medium, large-v3. Default: small.",
    )
    parser.add_argument(
        "--language",
        default=None,
        help="Speech language, for example 'ru' or 'en'. If omitted, Whisper auto-detects it.",
    )
    parser.add_argument(
        "--device",
        default="cpu",
        choices=["cpu", "cuda", "auto"],
        help="Device for transcription. Use 'cuda' if you have a working NVIDIA GPU setup.",
    )
    parser.add_argument(
        "--compute-type",
        default="int8",
        help="faster-whisper compute type. Good CPU default: int8. Good CUDA default: float16.",
    )
    parser.add_argument(
        "--keep-subtitles",
        action="store_true",
        help="Also save the generated .ass subtitle file next to the output video.",
    )
    parser.add_argument(
        "--max-words",
        type=int,
        default=2,
        help="Maximum words per subtitle. Default: 2.",
    )
    parser.add_argument(
        "--font-size",
        type=int,
        default=SubtitleStyle.font_size,
        help=f"Subtitle font size. Default: {SubtitleStyle.font_size}.",
    )
    parser.add_argument(
        "--subtitle-y",
        type=int,
        default=SubtitleStyle.margin_v,
        help=(
            "Subtitle vertical offset from the bottom. "
            f"Higher value moves subtitles up. Default: {SubtitleStyle.margin_v}."
        ),
    )
    parser.add_argument(
        "--outline",
        type=int,
        default=SubtitleStyle.outline,
        help=f"Subtitle outline thickness. Default: {SubtitleStyle.outline}.",
    )
    parser.add_argument(
        "--shadow",
        type=int,
        default=SubtitleStyle.shadow,
        help=f"Subtitle shadow size. Default: {SubtitleStyle.shadow}.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = args.input.expanduser().resolve()
    output_path = (args.output or default_output_path(input_path)).expanduser().resolve()

    process_video(
        input_path=input_path,
        output_path=output_path,
        model_size=args.model,
        language=args.language,
        device=args.device,
        compute_type=args.compute_type,
        max_words=args.max_words,
        subtitle_style=SubtitleStyle(
            font_size=args.font_size,
            outline=args.outline,
            shadow=args.shadow,
            margin_v=args.subtitle_y,
        ),
        keep_subtitles=args.keep_subtitles,
    )

    print(f"Done: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

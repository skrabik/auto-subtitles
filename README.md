# Reel Generator

Python script for turning a video into a vertical 9:16 clip with automatic burned-in subtitles.

## Requirements

1. Install Python 3.10+.
2. Install `ffmpeg` and make sure it is available in `PATH`.
3. Install Python dependencies:

```bash
pip install -r requirements.txt
```

## Web UI

Start the local web interface:

```bash
python web_app.py
```

Open:

```text
http://127.0.0.1:8000
```

The app binds to `127.0.0.1`, so it is intended for local-only use on your computer.

## CLI Usage

Basic usage:

```bash
python auto_subtitle_vertical.py input.mp4
```

With Russian language hint and custom output:

```bash
python auto_subtitle_vertical.py input.mp4 --language ru -o output.mp4
```

If you have an NVIDIA GPU configured for CUDA:

```bash
python auto_subtitle_vertical.py input.mp4 --device cuda --compute-type float16
```

To keep the generated `.ass` subtitle file:

```bash
python auto_subtitle_vertical.py input.mp4 --keep-subtitles
```

Customize subtitle style:

```bash
python auto_subtitle_vertical.py input.mp4 --max-words 2 --font-size 114 --subtitle-y 370 --outline 3
```

The default output is `1080x1920` MP4 with large centered subtitles in the lower part of the video.

from __future__ import annotations

import re
import shutil
import threading
import uuid
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from auto_subtitle_vertical import (
    Caption,
    SubtitleStyle,
    generate_captions,
    render_video_with_captions,
)


BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "data" / "uploads"
OUTPUT_DIR = BASE_DIR / "data" / "outputs"
ALLOWED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Local Reel Generator")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
JOBS: dict[str, dict[str, object]] = {}
JOBS_LOCK = threading.Lock()


def safe_stem(filename: str) -> str:
    stem = Path(filename).stem or "video"
    stem = re.sub(r"[^a-zA-Z0-9_-]+", "_", stem).strip("_")
    return stem[:80] or "video"


def update_job(job_id: str, **values: object) -> None:
    with JOBS_LOCK:
        JOBS.setdefault(job_id, {}).update(values)


def get_job(job_id: str) -> dict[str, object] | None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        return dict(job) if job is not None else None


def public_job(job: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in job.items() if not key.startswith("_")}


def caption_to_dict(caption: Caption) -> dict[str, object]:
    return {
        "start": round(caption.start, 3),
        "end": round(caption.end, 3),
        "text": caption.text,
    }


def captions_from_payload(payload: object) -> list[Caption]:
    if not isinstance(payload, list):
        raise ValueError("Captions payload must be a list.")

    captions: list[Caption] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise ValueError(f"Caption #{index + 1} is invalid.")

        text = str(item.get("text", "")).strip()
        if not text:
            continue

        start = float(item.get("start", 0))
        end = float(item.get("end", 0))
        if end <= start:
            raise ValueError(f"Caption #{index + 1} has invalid timing.")

        captions.append(Caption(start=start, end=end, text=text))

    if not captions:
        raise ValueError("At least one subtitle line is required.")

    return captions


def build_subtitle_style(
    subtitle_preset: str,
    font_size: int,
    subtitle_y: int,
    outline: int,
    shadow: int,
) -> SubtitleStyle:
    if subtitle_preset == "black_plain":
        return SubtitleStyle(
            font_size=font_size,
            outline=0,
            shadow=0,
            margin_v=subtitle_y,
            primary_color=(0, 0, 0),
        )

    return SubtitleStyle(
        font_size=font_size,
        outline=outline,
        shadow=shadow,
        margin_v=subtitle_y,
    )


def run_generation_job(
    job_id: str,
    source_path: Path,
    output_path: Path,
    output_name: str,
    model: str,
    language_value: str | None,
    device: str,
    compute_type: str,
    max_words: int,
    subtitle_style: SubtitleStyle,
) -> None:
    def report(progress: int, message: str) -> None:
        update_job(job_id, progress=progress, message=message)

    update_job(job_id, status="running", progress=1, message="Starting...")
    try:
        captions = generate_captions(
            input_path=source_path,
            model_size=model,
            language=language_value,
            device=device,
            compute_type=compute_type,
            max_words=max(1, max_words),
            progress_callback=report,
        )
        render_video_with_captions(
            input_path=source_path,
            output_path=output_path,
            captions=captions,
            subtitle_style=subtitle_style,
            progress_callback=report,
        )
    except SystemExit as exc:
        update_job(job_id, status="error", progress=100, message=str(exc), error=str(exc))
    except Exception as exc:
        message = f"Generation failed: {exc}"
        update_job(job_id, status="error", progress=100, message=message, error=message)
    else:
        update_job(
            job_id,
            status="completed",
            progress=100,
            message="Video is ready.",
            captions=[caption_to_dict(caption) for caption in captions],
            _source_path=source_path,
            _subtitle_style=subtitle_style,
            _render_count=0,
            result={
                "filename": output_name,
                "download_url": f"/download/{output_name}",
            },
        )


def run_rerender_job(job_id: str, captions: list[Caption]) -> None:
    job = get_job(job_id)
    if job is None:
        return

    source_path = job.get("_source_path")
    subtitle_style = job.get("_subtitle_style")
    if not isinstance(source_path, Path) or not isinstance(subtitle_style, SubtitleStyle):
        update_job(job_id, status="error", progress=100, message="Original job data is missing.")
        return

    render_count = int(job.get("_render_count", 0)) + 1
    output_name = f"{job_id}_edited_{render_count}_vertical_subtitled.mp4"
    output_path = OUTPUT_DIR / output_name

    def report(progress: int, message: str) -> None:
        update_job(job_id, progress=progress, message=message)

    update_job(job_id, status="rerendering", progress=65, message="Rendering edited subtitles...")
    try:
        render_video_with_captions(
            input_path=source_path,
            output_path=output_path,
            captions=captions,
            subtitle_style=subtitle_style,
            progress_callback=report,
        )
    except SystemExit as exc:
        update_job(job_id, status="error", progress=100, message=str(exc), error=str(exc))
    except Exception as exc:
        message = f"Re-render failed: {exc}"
        update_job(job_id, status="error", progress=100, message=message, error=message)
    else:
        update_job(
            job_id,
            status="completed",
            progress=100,
            message="Edited video is ready.",
            captions=[caption_to_dict(caption) for caption in captions],
            _render_count=render_count,
            result={
                "filename": output_name,
                "download_url": f"/download/{output_name}",
            },
        )


@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/generate")
async def generate(
    video: UploadFile = File(...),
    language: str = Form("ru"),
    model: str = Form("small"),
    device: str = Form("cpu"),
    compute_type: str = Form("int8"),
    max_words: int = Form(2),
    subtitle_preset: str = Form("white_outline"),
    font_size: int = Form(114),
    subtitle_y: int = Form(370),
    outline: int = Form(3),
    shadow: int = Form(1),
):
    source_name = video.filename or "video.mp4"
    source_ext = Path(source_name).suffix.lower()
    if source_ext not in ALLOWED_EXTENSIONS:
        return JSONResponse(
            {"error": "Unsupported video format. Use MP4, MOV, MKV, WEBM, or AVI."},
            status_code=400,
        )

    job_id = uuid.uuid4().hex
    source_path = UPLOAD_DIR / f"{job_id}_{safe_stem(source_name)}{source_ext}"
    output_name = f"{job_id}_{safe_stem(source_name)}_vertical_subtitled.mp4"
    output_path = OUTPUT_DIR / output_name

    with source_path.open("wb") as file:
        shutil.copyfileobj(video.file, file)

    language_value = language.strip() or None
    subtitle_style = build_subtitle_style(subtitle_preset, font_size, subtitle_y, outline, shadow)

    update_job(job_id, status="queued", progress=0, message="Queued...")
    thread = threading.Thread(
        target=run_generation_job,
        args=(
            job_id,
            source_path,
            output_path,
            output_name,
            model,
            language_value,
            device,
            compute_type,
            max_words,
            subtitle_style,
        ),
        daemon=True,
    )
    thread.start()

    return {"job_id": job_id}


@app.get("/status/{job_id}")
async def status(job_id: str):
    job = get_job(job_id)
    if job is None:
        return JSONResponse({"error": "Job not found."}, status_code=404)
    return public_job(job)


@app.post("/rerender/{job_id}")
async def rerender(job_id: str, request: Request):
    job = get_job(job_id)
    if job is None:
        return JSONResponse({"error": "Job not found."}, status_code=404)
    if job.get("status") in {"running", "rerendering", "queued"}:
        return JSONResponse({"error": "Job is still running."}, status_code=409)

    try:
        payload = await request.json()
        captions = captions_from_payload(payload.get("captions") if isinstance(payload, dict) else None)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    update_job(job_id, status="queued", progress=60, message="Queued edited render...")
    thread = threading.Thread(target=run_rerender_job, args=(job_id, captions), daemon=True)
    thread.start()

    return {"job_id": job_id}


@app.get("/download/{filename}")
async def download(filename: str):
    output_path = OUTPUT_DIR / Path(filename).name
    return FileResponse(
        output_path,
        media_type="video/mp4",
        filename=output_path.name,
    )


if __name__ == "__main__":
    uvicorn.run("web_app:app", host="127.0.0.1", port=8000, reload=False)

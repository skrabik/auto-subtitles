from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from auto_subtitle_vertical import SubtitleStyle, process_video


BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "data" / "uploads"
OUTPUT_DIR = BASE_DIR / "data" / "outputs"
ALLOWED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Local Reel Generator")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def safe_stem(filename: str) -> str:
    stem = Path(filename).stem or "video"
    stem = re.sub(r"[^a-zA-Z0-9_-]+", "_", stem).strip("_")
    return stem[:80] or "video"


@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/generate")
async def generate(
    request: Request,
    video: UploadFile = File(...),
    language: str = Form("ru"),
    model: str = Form("small"),
    device: str = Form("cpu"),
    compute_type: str = Form("int8"),
    max_words: int = Form(2),
    font_size: int = Form(114),
    subtitle_y: int = Form(370),
    outline: int = Form(3),
    shadow: int = Form(1),
):
    source_name = video.filename or "video.mp4"
    source_ext = Path(source_name).suffix.lower()
    if source_ext not in ALLOWED_EXTENSIONS:
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "error": "Unsupported video format. Use MP4, MOV, MKV, WEBM, or AVI.",
            },
            status_code=400,
        )

    job_id = uuid.uuid4().hex
    source_path = UPLOAD_DIR / f"{job_id}_{safe_stem(source_name)}{source_ext}"
    output_name = f"{job_id}_{safe_stem(source_name)}_vertical_subtitled.mp4"
    output_path = OUTPUT_DIR / output_name

    with source_path.open("wb") as file:
        shutil.copyfileobj(video.file, file)

    language_value = language.strip() or None

    try:
        await run_in_threadpool(
            process_video,
            input_path=source_path,
            output_path=output_path,
            model_size=model,
            language=language_value,
            device=device,
            compute_type=compute_type,
            max_words=max(1, max_words),
            subtitle_style=SubtitleStyle(
                font_size=font_size,
                outline=outline,
                shadow=shadow,
                margin_v=subtitle_y,
            ),
        )
    except SystemExit as exc:
        return templates.TemplateResponse(
            "index.html",
            {"request": request, "error": str(exc)},
            status_code=500,
        )
    except Exception as exc:
        return templates.TemplateResponse(
            "index.html",
            {"request": request, "error": f"Generation failed: {exc}"},
            status_code=500,
        )

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "result": {
                "filename": output_name,
                "download_url": f"/download/{output_name}",
            },
        },
    )


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

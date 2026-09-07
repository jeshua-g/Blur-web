#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import queue
import re
import secrets
import shutil
import subprocess
import threading
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles

DATA = Path(os.environ.get("DATA_DIR", "data"))
BLUR_BIN = os.environ.get("BLUR_BIN", "blur-cli")
PASSWORD = os.environ.get("BLUR_WEB_PASSWORD", "")
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "2048"))
ALLOWED = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"}
WEIGHTING = {
    "equal",
    "gaussian_sym",
    "vegas",
    "pyramid",
    "gaussian",
    "ascending",
    "descending",
    "gaussian_reverse",
}
METHODS = {"svp", "rife"}
DEDUP = {"svp", "rife", "old"}
PRESETS = {"h264", "h265", "av1", "vp9"}

DATA.mkdir(parents=True, exist_ok=True)
jobs: queue.Queue[str] = queue.Queue()
# ponytail: one worker; a pool if the VPS ever has spare cores and a GPU
security = HTTPBasic(auto_error=False)
app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")


def job_dir(job_id: str) -> Path:
    return DATA / job_id


def read_status(job_id: str) -> dict:
    path = job_dir(job_id) / "status.json"
    if not path.is_file():
        raise FileNotFoundError(job_id)
    return json.loads(path.read_text())


def write_status(job_id: str, **fields) -> dict:
    path = job_dir(job_id) / "status.json"
    data = json.loads(path.read_text()) if path.is_file() else {}
    data.update(fields)
    path.write_text(json.dumps(data))
    return data


def safe_name(name: str) -> str:
    name = Path(name or "").name
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    return name or "video.mp4"


def as_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ("1", "true", "on", "yes")
    return default


def clamp(value, lo, hi, default, cast=float):
    try:
        n = cast(value)
    except (TypeError, ValueError):
        n = default
    return cast(max(lo, min(hi, n)))


def pick(value, allowed: set[str], default: str) -> str:
    s = str(value or default).strip().lower()
    return s if s in allowed else default


def blur_cfg(raw: dict | None = None) -> str:
    s = raw if isinstance(raw, dict) else {}
    amount = clamp(s.get("blur_amount", 1), 0, 4, 1.0)
    out_fps = clamp(s.get("blur_output_fps", 60), 1, 240, 60, int)
    gamma = clamp(s.get("blur_gamma", 1), 0.1, 4, 1.0)
    quality = clamp(s.get("quality", 18), 0, 51, 18, int)
    interp_fps = str(s.get("interpolated_fps") or "5x").strip() or "5x"
    pre_fps = str(s.get("pre_interpolated_fps") or "360").strip() or "360"
    weighting = pick(s.get("blur_weighting"), WEIGHTING, "equal")
    method = pick(s.get("interpolation_method"), METHODS, "svp")
    dedup_method = pick(s.get("deduplicate_method"), DEDUP, "svp")
    preset = pick(s.get("encode_preset"), PRESETS, "h264")
    blur_on = as_bool(s.get("blur", True), True)
    interpolate = as_bool(s.get("interpolate"), False)
    pre = as_bool(s.get("pre_interpolate"), False)
    dedup = as_bool(s.get("deduplicate", True), True)
    detailed = as_bool(s.get("detailed_filenames"), False)
    gpu_dec = as_bool(s.get("gpu_decoding"), False)
    gpu_interp = as_bool(s.get("gpu_interpolation"), False)
    gpu_enc = as_bool(s.get("gpu_encoding"), False)
    timescale = as_bool(s.get("timescale"), False)
    in_ts = clamp(s.get("input_timescale", 1), 0.1, 10, 1.0)
    out_ts = clamp(s.get("output_timescale", 1), 0.1, 10, 1.0)
    pitch = as_bool(s.get("output_timescale_audio_pitch"), False)
    filters = as_bool(s.get("filters"), False)
    brightness = clamp(s.get("brightness", 1), 0, 4, 1.0)
    saturation = clamp(s.get("saturation", 1), 0, 4, 1.0)
    contrast = clamp(s.get("contrast", 1), 0, 4, 1.0)
    yn = lambda v: "true" if v else "false"
    return f"""[blur v2.45]

- blur
blur: {yn(blur_on)}
blur amount: {amount}
blur output fps: {out_fps}
blur weighting: {weighting}
blur gamma: {gamma}

- interpolation
interpolate: {yn(interpolate)}
interpolated fps: {interp_fps}
interpolation method: {method}

- pre-interpolation
pre-interpolate: {yn(pre)}
pre-interpolated fps: {pre_fps}

- deduplication
deduplicate: {yn(dedup)}
deduplicate method: {dedup_method}

- rendering
encode preset: {preset}
quality: {quality}
preview: false
detailed filenames: {yn(detailed)}
copy dates: false

- gpu acceleration
gpu decoding: {yn(gpu_dec)}
gpu interpolation: {yn(gpu_interp)}
gpu encoding: {yn(gpu_enc)}

- timescale
timescale: {yn(timescale)}
input timescale: {in_ts}
output timescale: {out_ts}
adjust timescaled audio pitch: {yn(pitch)}

- filters
filters: {yn(filters)}
brightness: {brightness}
saturation: {saturation}
contrast: {contrast}
"""


def parse_config(raw: str) -> dict:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        data = {}
    return data if isinstance(data, dict) else {}


def fail(status: int, code: str, message: str) -> None:
    raise HTTPException(status, {"code": code, "message": message})


def classify_render(text: str, returncode: int | None = None) -> tuple[str, str]:
    t = (text or "").lower()
    if "not a valid video" in t or "unreadable" in t:
        return "unreadable_video", "that file isn’t a readable video"
    if "was not found" in t or "wrong path" in t:
        return "input_missing", "the uploaded file went missing on the server"
    if "failed to initialise" in t:
        return "blur_init_failed", "blur couldn’t start — vapoursynth or plugins are missing"
    if "no attribute with the name bs" in t or "libavutil.so.60" in t:
        return "blur_init_failed", "blur couldn’t load its video plugins"
    if "failed to render" in t:
        return "render_failed", "blur failed to render that video"
    if "rife" in t and ("error" in t or "fail" in t):
        return "rife_failed", "rife interpolation failed — switch to svp or turn interpolate off"
    if "svp" in t and ("error" in t or "fail" in t):
        return "svp_failed", "svp interpolation failed — try rife or turn interpolate off"
    if "permission" in t:
        return "permission_denied", "the server couldn’t write the output file"
    if returncode:
        return "render_failed", f"render failed (exit {returncode})"
    return "output_missing", "blur finished but didn’t write an output file"


def require_auth(credentials: HTTPBasicCredentials | None = Depends(security)):
    if not PASSWORD:
        return
    if credentials is None or not secrets.compare_digest(credentials.password, PASSWORD):
        raise HTTPException(
            401,
            {"code": "password_required", "message": "password required"},
            headers={"WWW-Authenticate": 'Basic realm="blur-web"'},
        )


def run_job(job_id: str) -> None:
    d = job_dir(job_id)
    status = read_status(job_id)
    src = Path(status["input"])
    dst = d / f"blurred{src.suffix}"
    log = d / "log.txt"
    write_status(job_id, status="running", log="")
    cmd = [BLUR_BIN, "-i", str(src), "-o", str(dst), "-c", str(d / "blur.cfg"), "-v"]
    try:
        proc = subprocess.run(
            cmd,
            cwd=d,
            capture_output=True,
            text=True,
            timeout=6 * 60 * 60,
        )
        text = (proc.stdout or "") + (proc.stderr or "")
        log.write_text(text)
        if proc.returncode != 0 or not dst.is_file() or "failed to render" in text.lower():
            code, message = classify_render(text, proc.returncode)
            write_status(job_id, status="error", code=code, error=message, log=text[-4000:])
            return
        src.unlink(missing_ok=True)
        write_status(job_id, status="done", output=str(dst), log=text[-4000:])
    except FileNotFoundError:
        write_status(
            job_id,
            status="error",
            code="blur_missing",
            error="blur-cli isn’t installed in this container",
        )
    except subprocess.TimeoutExpired:
        write_status(job_id, status="error", code="render_timeout", error="render timed out after 6 hours")
    except Exception as exc:
        write_status(job_id, status="error", code="render_crash", error=str(exc)[:240])


def worker() -> None:
    while True:
        run_job(jobs.get())


threading.Thread(target=worker, daemon=True).start()


@app.get("/")
def index(_: None = Depends(require_auth)):
    return FileResponse("static/index.html")


@app.post("/api/jobs")
async def create_job(
    _: None = Depends(require_auth),
    file: UploadFile = File(...),
    config: str = Form("{}"),
):
    settings = parse_config(config)
    name = safe_name(file.filename or "video.mp4")
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED:
        fail(400, "unsupported_type", f"{ext or 'that file'} isn’t a supported video")
    job_id = secrets.token_hex(8)
    d = job_dir(job_id)
    d.mkdir(parents=True)
    dest = d / f"input{ext}"
    limit = MAX_UPLOAD_MB * 1024 * 1024
    written = 0
    try:
        with dest.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > limit:
                    dest.unlink(missing_ok=True)
                    shutil.rmtree(d, ignore_errors=True)
                    fail(413, "file_too_large", f"that video is larger than {MAX_UPLOAD_MB} MB")
                out.write(chunk)
    finally:
        await file.close()
    if written == 0:
        shutil.rmtree(d, ignore_errors=True)
        fail(400, "empty_file", "that file is empty")
    (d / "blur.cfg").write_text(blur_cfg(settings))
    write_status(job_id, id=job_id, status="queued", filename=name, input=str(dest), config=settings)
    jobs.put(job_id)
    return {"id": job_id}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, _: None = Depends(require_auth)):
    try:
        return read_status(job_id)
    except FileNotFoundError:
        fail(404, "job_not_found", "that job is gone")


@app.get("/api/jobs/{job_id}/download")
def download(job_id: str, _: None = Depends(require_auth)):
    try:
        status = read_status(job_id)
    except FileNotFoundError:
        fail(404, "job_not_found", "that job is gone")
    if status.get("status") != "done":
        fail(409, "not_ready", "that render isn’t finished yet")
    path = Path(status["output"])
    if not path.is_file():
        fail(404, "output_missing", "the finished file is gone from the server")
    original = Path(status.get("filename") or "video.mp4")
    return FileResponse(path, filename=f"{original.stem}-blurred{path.suffix}", media_type="video/mp4")

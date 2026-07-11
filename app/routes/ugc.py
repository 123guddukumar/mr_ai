import os
import secrets
import logging
import aiofiles
from typing import Optional, List, Dict
from fastapi import APIRouter, Depends, HTTPException, Header, UploadFile, File, BackgroundTasks
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.clients import validate_client_token
from app.core.models import UgcJob
from app.services.ugc_service import run_ugc_pipeline

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_VIDEO_SIZE_MB = 100

# ── Auth dependency ───────────────────────────────────────────────────────────

def _require_client(
    x_app_token: Optional[str] = Header(None, alias="X-App-Token"),
    db: Session = Depends(get_db),
) -> dict:
    if not x_app_token:
        raise HTTPException(401, "Missing X-App-Token header.")
    record = validate_client_token(x_app_token, db=db)
    if not record:
        raise HTTPException(401, "Invalid or expired token.")
    return record


# ── Schemas ───────────────────────────────────────────────────────────────────

class UgcProcessRequest(BaseModel):
    caption: bool = Field(default=True)
    zoom: bool = Field(default=True)
    broll: bool = Field(default=True)
    broll_source: Optional[str] = Field(default="pollinations", description="'pollinations' (AI image), 'pexels' (stock video), or 'meta_ai' (pre-uploaded images)")
    music: bool = Field(default=True)
    sfx: bool = Field(default=True)
    silence: bool = Field(default=True)
    jumpcut: bool = Field(default=True)
    facetrack: bool = Field(default=True)
    background: bool = Field(default=False)
    background_style: Optional[str] = Field(default="premium_abstract_dark_studio_background_neon_lights_orange_vertical_aesthetic")
    dress_color_shift: bool = Field(default=False)
    dress_color: Optional[str] = Field(default="Blue")
    video_quality: Optional[str] = Field(default="1080p")
    bgm_mood: Optional[str] = Field(default="Corporate")
    viral: bool = Field(default=True)
    logo: bool = Field(default=False)
    subtitle_style: Optional[str] = Field(default="default", description="Subtitle styling style name: 'default', 'important_large', 'neon_bounce', 'minimal_white', 'bold_yellow', 'split_top_bottom', 'two_line_slide_right_left', 'two_line_slide_left_right', 'two_line_slide_top_bottom', 'two_line_zoom_in'")


class BrollUploadIndex(BaseModel):
    index: int = Field(..., description="B-roll index (0-based)")
    start: float = Field(..., description="B-roll start time in seconds")
    end: float = Field(..., description="B-roll end time in seconds")
    prompt: str = Field(default="", description="Original prompt used for this B-roll")


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/ugc/upload", tags=["UGC Creator"])
async def upload_ugc_video(
    file: UploadFile = File(...),
    source: Optional[str] = None,
    client: dict = Depends(_require_client),
    db: Session = Depends(get_db),
):
    """Uploads a local video file for UGC editing."""
    filename = file.filename or "video.mp4"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in [".mp4", ".mov", ".avi", ".mkv"]:
        raise HTTPException(400, "Only video files (.mp4, .mov, .avi, .mkv) are accepted.")

    job_id = secrets.token_hex(8)
    work_dir = os.path.join(os.getcwd(), "uploads", "ugc", job_id)
    os.makedirs(work_dir, exist_ok=True)
    
    video_path = os.path.join(work_dir, f"original{ext}")

    # Write file asynchronously
    size = 0
    try:
        async with aiofiles.open(video_path, "wb") as f:
            while content := await file.read(1024 * 1024):
                size += len(content)
                if size > MAX_VIDEO_SIZE_MB * 1024 * 1024:
                    raise HTTPException(400, f"Video size exceeds {MAX_VIDEO_SIZE_MB}MB limit.")
                await f.write(content)
    except HTTPException as he:
        # Clean up directory
        try:
            os.remove(video_path)
            os.rmdir(work_dir)
        except:
            pass
        raise he
    except Exception as e:
        logger.error(f"Error saving uploaded UGC video: {e}")
        try:
            os.remove(video_path)
            os.rmdir(work_dir)
        except:
            pass
        raise HTTPException(500, f"Error saving file: {str(e)}")

    if size == 0:
        try:
            os.remove(video_path)
            os.rmdir(work_dir)
        except:
            pass
        raise HTTPException(400, "Uploaded video file is empty.")

    # Create UgcJob database record
    try:
        import json
        is_api_val = (source != "dashboard")
        job_metadata = {"is_api": is_api_val}
        job = UgcJob(
            job_id=job_id,
            client_id=client["client_id"],
            filename=filename,
            status="pending",
            progress=0,
            original_video_path=video_path,
            metadata_json=json.dumps(job_metadata)
        )
        db.add(job)
        db.commit()
    except Exception as e:
        logger.error(f"Database error creating UGC job: {e}")
        try:
            os.remove(video_path)
            os.rmdir(work_dir)
        except:
            pass
        db.rollback()
        raise HTTPException(500, "Failed to create UGC database job.")

    return {
        "success": True,
        "job_id": job_id,
        "filename": filename,
        "message": "Video uploaded successfully. Configure options and click Process to start."
    }


@router.post("/ugc/process/{job_id}", tags=["UGC Creator"])
async def process_ugc_video(
    job_id: str,
    req: UgcProcessRequest,
    background_tasks: BackgroundTasks,
    client: dict = Depends(_require_client),
    db: Session = Depends(get_db),
):
    """Initiates background video enhancement processing."""
    job = db.query(UgcJob).filter(
        UgcJob.job_id == job_id,
        UgcJob.client_id == client["client_id"]
    ).first()
    if not job:
        raise HTTPException(404, "UGC Job not found.")

    if job.status in ["transcribing", "processing"]:
        raise HTTPException(400, "Job is already being processed.")

    # Update job state in DB to transcribing & save metadata settings
    job.status = "transcribing"
    job.progress = 5
    
    # Preserve existing metadata (like meta_brolls) and merge request parameters
    existing_meta = json_dumps_parse(job.metadata_json) if job.metadata_json else {}
    existing_meta.update(req.dict())
    job.metadata_json = json_dumps(existing_meta)
    
    db.commit()

    # Add task to background workers
    background_tasks.add_task(
        run_ugc_pipeline,
        job_id=job_id,
        client_id=client["client_id"],
        video_path=job.original_video_path,
        filename=job.filename,
        features=req.dict()
    )

    return {
        "success": True,
        "job_id": job_id,
        "status": "transcribing",
        "message": "UGC video processing started in the background. Check status for progress updates."
    }


@router.post("/ugc/upload-broll/{job_id}", tags=["UGC Creator"])
async def upload_broll_image(
    job_id: str,
    index: int,
    start: float,
    end: float,
    prompt: str = "",
    local_url: Optional[str] = None,
    file: Optional[UploadFile] = File(None),
    client: dict = Depends(_require_client),
    db: Session = Depends(get_db),
):
    """Accepts a pre-generated B-roll image or video (e.g. from Meta AI) for a pending UGC job."""
    import json
    import shutil
    import httpx
    
    job = db.query(UgcJob).filter(
        UgcJob.job_id == job_id,
        UgcJob.client_id == client["client_id"]
    ).first()
    if not job:
        raise HTTPException(404, "UGC Job not found.")

    work_dir = os.path.join(os.getcwd(), "uploads", "ugc", job_id)
    os.makedirs(work_dir, exist_ok=True)

    img_path = None
    if file:
        # Save uploaded file
        ext = os.path.splitext(file.filename or "broll.jpg")[1].lower() or ".jpg"
        img_path = os.path.join(work_dir, f"broll_meta_{index}{ext}")
        async with aiofiles.open(img_path, "wb") as f:
            content = await file.read()
            await f.write(content)
    elif local_url:
        # Resolve path
        filename_from_url = os.path.basename(local_url.split("?")[0])
        src_path = os.path.join(os.getcwd(), "uploads", "social", filename_from_url)
        
        # Determine extension from URL
        ext = os.path.splitext(filename_from_url)[1].lower() or ".mp4"
        img_path = os.path.join(work_dir, f"broll_meta_{index}{ext}")
        
        if os.path.exists(src_path):
            shutil.copy(src_path, img_path)
            logger.info(f"Copied local B-roll asset from {src_path} to {img_path}")
        else:
            # Maybe it's a relative URL or absolute remote URL
            url_to_fetch = local_url
            if local_url.startswith("/"):
                url_to_fetch = f"http://127.0.0.1:8000{local_url}"
            
            try:
                async with httpx.AsyncClient() as client_http:
                    dl_res = await client_http.get(url_to_fetch, timeout=60.0)
                    if dl_res.status_code == 200:
                        with open(img_path, "wb") as f:
                            f.write(dl_res.content)
                        logger.info(f"Downloaded B-roll from {url_to_fetch} to {img_path}")
            except Exception as dl_err:
                logger.error(f"Failed downloading B-roll from URL {url_to_fetch}: {dl_err}")

    if not img_path or not os.path.exists(img_path):
        raise HTTPException(400, "No B-roll file was provided or resolved.")

    # Store mapping in job metadata
    try:
        meta = json.loads(job.metadata_json) if job.metadata_json else {}
    except Exception:
        meta = {}

    meta_brolls = meta.get("meta_brolls", [])
    # Remove existing broll at this index if present to avoid duplicates
    meta_brolls = [mb for mb in meta_brolls if mb.get("index") != index]
    
    meta_brolls.append({
        "index": index,
        "start": start,
        "end": end,
        "prompt": prompt,
        "path": img_path
    })
    meta["meta_brolls"] = meta_brolls
    job.metadata_json = json.dumps(meta)
    db.commit()

    logger.info(f"B-roll #{index} registered for job {job_id}: {img_path}")
    return {"success": True, "index": index, "path": img_path}


@router.post("/ugc/upload-logo/{job_id}", tags=["UGC Creator"])
async def upload_logo_image(
    job_id: str,
    file: UploadFile = File(...),
    client: dict = Depends(_require_client),
    db: Session = Depends(get_db),
):
    """Uploads a brand logo (PNG/JPG) to be overlaid on the final UGC video."""
    import json

    job = db.query(UgcJob).filter(
        UgcJob.job_id == job_id,
        UgcJob.client_id == client["client_id"]
    ).first()
    if not job:
        raise HTTPException(404, "UGC Job not found.")

    work_dir = os.path.join(os.getcwd(), "uploads", "ugc", job_id)
    os.makedirs(work_dir, exist_ok=True)

    ext = os.path.splitext(file.filename or "logo.png")[1].lower() or ".png"
    if ext not in [".png", ".jpg", ".jpeg", ".webp"]:
        raise HTTPException(400, "Logo must be a PNG, JPG, or WebP image.")

    logo_path = os.path.join(work_dir, f"brand_logo{ext}")
    async with aiofiles.open(logo_path, "wb") as f:
        content = await file.read()
        await f.write(content)

    # Store logo path in job metadata
    try:
        meta = json.loads(job.metadata_json) if job.metadata_json else {}
    except Exception:
        meta = {}
    meta["logo_path"] = logo_path
    job.metadata_json = json.dumps(meta)
    db.commit()

    logger.info(f"Brand logo uploaded for job {job_id}: {logo_path}")
    return {"success": True, "path": logo_path}


@router.get("/ugc/analyze-broll/{job_id}", tags=["UGC Creator"])
async def analyze_broll_prompts(
    job_id: str,
    client: dict = Depends(_require_client),
    db: Session = Depends(get_db),
):
    """
    Runs Whisper transcription + LLM Stage 3 analysis on an uploaded video
    and returns the suggested B-roll prompts (with timestamps).
    Called by the frontend when 'Meta AI Extension' B-roll source is selected,
    to get prompts before triggering the extension.
    """
    import asyncio
    import re
    from app.services.llm import generate_simple_response
    
    job = db.query(UgcJob).filter(
        UgcJob.job_id == job_id,
        UgcJob.client_id == client["client_id"]
    ).first()
    if not job:
        raise HTTPException(404, "UGC Job not found.")
    
    video_path = job.original_video_path
    if not video_path or not os.path.exists(video_path):
        raise HTTPException(400, "Video file not found on server.")
    
    work_dir = os.path.join(os.getcwd(), "uploads", "ugc", job_id)
    os.makedirs(work_dir, exist_ok=True)
    
    try:
        # Extract audio
        audio_path = os.path.join(work_dir, "analysis_audio.mp3")
        import subprocess
        extract_cmd = ["ffmpeg", "-y", "-i", video_path, "-vn", "-acodec", "libmp3lame", "-ar", "16000", audio_path]
        subprocess.run(extract_cmd, capture_output=True)
        
        # Transcribe with Whisper
        import whisper
        model = whisper.load_model("base")
        trans_res = model.transcribe(
            audio_path,
            fp16=False,
            word_timestamps=True,
            initial_prompt="English and Hindi spoken mixed speech. Transcribe English words in English script and Hindi words in Devanagari (Hindi) script. Hinglish transcription."
        )
        segments = trans_res.get("segments", [])
        
        if not segments:
            return {"brolls": [], "message": "No speech detected in video."}
        
        # Build transcript snippet
        clean_lines = [f"[{round(s['start'], 2)}s - {round(s['end'], 2)}s]: {s['text']}" for s in segments[:80]]
        transcript_snippet = "\n".join(clean_lines)
        
        # LLM analysis for B-roll prompts
        system_prompt = "You are a professional video editor. Output strictly raw JSON only."
        user_prompt = f"""
Transcript:
{transcript_snippet}

Suggest 2-3 B-roll image overlays based on keywords spoken.
Output format (raw JSON, no markdown):
{{
  "brolls": [
    {{"start": 5.2, "end": 8.0, "prompt": "cinematic photorealistic illustration..."}}
  ]
}}
"""
        llm_response = await generate_simple_response(user_prompt, system_prompt)
        cleaned = re.sub(r'^```json\s*|\s*```$', '', llm_response.strip(), flags=re.MULTILINE)
        plan = json_dumps_parse(cleaned)
        brolls = plan.get("brolls", []) if plan else []
        
        return {"brolls": brolls, "transcript_lines": len(segments)}
        
    except Exception as e:
        logger.error(f"analyze-broll error: {e}")
        raise HTTPException(500, f"Analysis failed: {str(e)}")


@router.get("/ugc/status/{job_id}", tags=["UGC Creator"])
@router.get("/ugc/job/{job_id}", tags=["UGC Creator"])
async def get_ugc_job_status(
    job_id: str,
    client: dict = Depends(_require_client),
    db: Session = Depends(get_db),
):
    """Returns the current status of the UGC video enhancement job, including edited version history."""
    import json, glob
    from datetime import datetime
    from app.core.config import settings

    job = db.query(UgcJob).filter(
        UgcJob.job_id == job_id,
        UgcJob.client_id == client["client_id"]
    ).first()
    if not job:
        raise HTTPException(404, "UGC Job not found.")

    result = job.to_dict()

    # Scan for edited version files (result_edited_1.mp4, result_edited_2.mp4, etc.)
    work_dir = os.path.join(str(settings.BASE_DIR), "static", "ugc", job_id)
    edited_versions = []
    try:
        pattern = os.path.join(work_dir, "result_edited_*.mp4")
        for fpath in sorted(glob.glob(pattern)):
            fname = os.path.basename(fpath)
            fstat = os.stat(fpath)
            # Extract version number from filename
            ver = fname.replace("result_edited_", "").replace(".mp4", "")
            edited_versions.append({
                "version": ver,
                "filename": fname,
                "url": f"/static/ugc/{job_id}/{fname}",
                "size_mb": round(fstat.st_size / (1024 * 1024), 2),
                "saved_at": datetime.fromtimestamp(fstat.st_mtime).isoformat(),
            })
    except Exception as e:
        logger.warning(f"Could not scan edited versions for {job_id}: {e}")

    result["edited_versions"] = edited_versions
    return result



@router.get("/ugc/jobs", tags=["UGC Creator"])
async def list_ugc_jobs(
    is_api: Optional[bool] = None,
    client: dict = Depends(_require_client),
    db: Session = Depends(get_db),
):
    """Returns all UGC video jobs run by the authenticated client."""
    jobs = db.query(UgcJob).filter(
        UgcJob.client_id == client["client_id"]
    ).order_by(UgcJob.created_at.desc()).all()

    if is_api is not None:
        jobs = [j for j in jobs if j.settings.get("is_api", False) == is_api]

    return [j.to_dict() for j in jobs]


# ── Subtitle Editor Schemas ───────────────────────────────────────────────────

class SubtitleSegment(BaseModel):
    start: float = Field(..., description="Start time in seconds")
    end: float = Field(..., description="End time in seconds")
    text: str = Field(..., description="Subtitle line text")


class SubtitleEditRequest(BaseModel):
    segments: List[SubtitleSegment] = Field(..., description="Edited subtitle segments")
    subtitle_style: Optional[str] = Field(default="default", description="Subtitle styling style name: 'default', 'important_large', 'neon_bounce', 'minimal_white', 'bold_yellow', 'split_top_bottom', 'two_line_slide_right_left', 'two_line_slide_left_right', 'two_line_slide_top_bottom', 'two_line_zoom_in'")


# ── Subtitle Editor Endpoints ─────────────────────────────────────────────────

@router.get("/ugc/subtitles/{job_id}", tags=["UGC Creator"])
async def get_ugc_subtitles(
    job_id: str,
    client: dict = Depends(_require_client),
    db: Session = Depends(get_db),
):
    """Returns transcript as editable subtitle segments (start, end, text)."""
    import json

    job = db.query(UgcJob).filter(
        UgcJob.job_id == job_id,
        UgcJob.client_id == client["client_id"]
    ).first()
    if not job:
        raise HTTPException(404, "UGC Job not found.")

    try:
        raw_segments = json.loads(job.transcript_json or "[]")
    except Exception:
        raw_segments = []

    if not raw_segments:
        raise HTTPException(404, "No transcript found for this job. The job may not have been transcribed yet.")

    # Build grouped subtitle lines (max 4 words per line, ≤1.8s) — same logic as the pipeline
    all_words = []
    for seg in raw_segments:
        for w in seg.get("words", []):
            if w.get("word", "").strip():
                all_words.append({
                    "word": w["word"].strip(),
                    "start": float(w["start"]),
                    "end": float(w["end"])
                })

    grouped_lines = []
    current_words = []
    for w in all_words:
        if not current_words:
            current_words.append(w)
        else:
            dur = w["end"] - current_words[0]["start"]
            if len(current_words) >= 4 or dur > 1.8:
                grouped_lines.append(current_words)
                current_words = [w]
            else:
                current_words.append(w)
    if current_words:
        grouped_lines.append(current_words)

    segments_out = []
    for line in grouped_lines:
        text = " ".join(w["word"].upper() for w in line)
        segments_out.append({
            "start": round(line[0]["start"], 3),
            "end": round(line[-1]["end"], 3),
            "text": text
        })

    return {"job_id": job_id, "segments": segments_out}


@router.post("/ugc/rerender-subtitles/{job_id}", tags=["UGC Creator"])
async def rerender_ugc_subtitles(
    job_id: str,
    req: SubtitleEditRequest,
    background_tasks: BackgroundTasks,
    client: dict = Depends(_require_client),
    db: Session = Depends(get_db),
):
    """Regenerates the subtitle .ass file from edited segments and re-burns onto the final video."""
    import json, subprocess
    from app.core.config import settings

    job = db.query(UgcJob).filter(
        UgcJob.job_id == job_id,
        UgcJob.client_id == client["client_id"]
    ).first()
    if not job:
        raise HTTPException(404, "UGC Job not found.")

    if job.status not in ("completed", "failed"):
        raise HTTPException(400, "Job must be completed before editing subtitles.")

    # Construct the local path based directly on job_id instead of parsing potentially remote URL
    local_video_path = os.path.join(str(settings.BASE_DIR), "static", "ugc", job_id, "result.mp4")

    if not os.path.exists(local_video_path):
        raise HTTPException(404, f"Result video not found on disk at: {local_video_path}. Cannot re-render.")

    # Run subtitle re-render in background
    background_tasks.add_task(
        _rerender_subtitles_task, job_id, req.segments, local_video_path, req.subtitle_style or "default", db
    )

    return {"success": True, "message": "Subtitle re-render started. Check job status for completion."}


async def _rerender_subtitles_task(job_id: str, segments: list, result_video_path: str, subtitle_style: str, db):
    """Background task: regenerate ASS + re-burn captions onto result video."""
    import json, subprocess, asyncio
    from app.core.config import settings
    from app.core.database import get_session_local

    SessionLocal = get_session_local()
    db2 = SessionLocal()
    try:
        job = db2.query(UgcJob).filter(UgcJob.job_id == job_id).first()
        if not job:
            return

        # Mark as processing
        job.status = "processing"
        job.progress = 80
        job.error_message = None
        db2.commit()

        # Paths
        output_folder = os.path.join(str(settings.BASE_DIR), "static", "ugc", job_id)
        subs_ass_path = os.path.join(output_folder, "subtitles_edited.ass")

        # 1. Generate new .ass file from edited segments
        def fmt_ass_time(s: float) -> str:
            h = int(s // 3600)
            m = int((s % 3600) // 60)
            sec = s % 60
            return f"{h}:{m:02d}:{sec:05.2f}"

        font_name = "Impact" if subtitle_style in ["two_line_slide_right_left", "two_line_slide_left_right", "two_line_slide_top_bottom", "two_line_zoom_in"] else "Arial"
        outline_val = 5 if font_name == "Impact" else 6

        with open(subs_ass_path, "w", encoding="utf-8") as sf:
            sf.write("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n")
            sf.write("[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n")
            sf.write(f"Style: Default,{font_name},82,&H00FFFFFF,&H0000FFFF,&H00000000,&H00000000,-1,0,0,0,100,100,1,0,1,{outline_val},2,2,30,30,420,1\n\n")
            sf.write("[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")

            for seg in segments:
                words = seg.text.strip().split()
                if not words:
                    continue

                if subtitle_style == "important_large":
                    # Find longest word index
                    word_lengths = [len(w.strip(",.?!;:\"'")) for w in words]
                    longest_idx = word_lengths.index(max(word_lengths)) if word_lengths else 0
                    
                    parts = []
                    for idx, w in enumerate(words):
                        if idx == longest_idx:
                            # Yellow, large word
                            parts.append(f"{{\\fs115\\c&H0000FFFF&\\fscx100\\fscy100\\t(0,80,\\fscx114\\fscy114)\\t(80,160,\\fscx100\\fscy100)}}{w}{{\\r}}")
                        else:
                            # White, small word
                            parts.append(f"{{\\fs72\\c&H00FFFFFF&}}{w}{{\\r}}")
                    line_text = " ".join(parts)
                
                elif subtitle_style == "neon_bounce":
                    # Highlight first word in neon green
                    highlighted = (
                        f"{{\\c&H0000FF00&\\fscx100\\fscy100\\t(0,80,\\fscx114\\fscy114)\\t(80,160,\\fscx100\\fscy100)}}{words[0]}{{\\c&H00FFFFFF&}}"
                    )
                    rest = " ".join(words[1:])
                    line_text = (highlighted + " " + rest).strip() if rest else highlighted
                
                elif subtitle_style == "minimal_white":
                    # Clean white text, no color change or scale bounce
                    line_text = seg.text.strip()
                
                elif subtitle_style == "bold_yellow":
                    # Entire line in bold yellow
                    line_text = f"{{\\c&H0000FFFF&}}{seg.text.strip()}"
                
                elif subtitle_style in ["two_line_slide_right_left", "two_line_slide_left_right", "two_line_slide_top_bottom", "two_line_zoom_in"]:
                    mid = max(1, len(words) // 2)
                    top_words = words[:mid]
                    bottom_words = words[mid:]

                    word_lengths = [len(w.strip(',.?!;:"')) for w in words]
                    longest_idx = word_lengths.index(max(word_lengths)) if word_lengths else 0

                    # Build top-line ASS parts (all active - static render for re-render)
                    top_parts = []
                    for k, w in enumerate(top_words):
                        imp = (k == longest_idx)
                        sz = 115 if imp else 100
                        clr = "&H0000FFFF&" if imp else "&H00FFFFFF&"
                        top_parts.append("{\\c" + clr + "\\fs" + str(sz) + "}" + w + "{\\r}")
                    top_text = " ".join(top_parts)

                    # Build bottom-line ASS parts
                    bottom_parts = []
                    for k, w in enumerate(bottom_words):
                        actual_k = mid + k
                        imp = (actual_k == longest_idx)
                        sz = 115 if imp else 100
                        clr = "&H0000FFFF&" if imp else "&H00FFFFFF&"
                        bottom_parts.append("{\\c" + clr + "\\fs" + str(sz) + "}" + w + "{\\r}")
                    bottom_text = " ".join(bottom_parts)

                    # Sequential timing: split segment time evenly between Line 1 and Line 2
                    seg_mid = seg.start + (seg.end - seg.start) / 2

                    # Entrance animation for Line 1 (appears at seg.start)
                    if subtitle_style == "two_line_slide_right_left":
                        anim1 = "\\move(1480,1420,540,1420,0,200)"
                        anim2 = "\\move(-400,1550,540,1550,0,200)"
                    elif subtitle_style == "two_line_slide_left_right":
                        anim1 = "\\move(-400,1420,540,1420,0,200)"
                        anim2 = "\\move(1480,1550,540,1550,0,200)"
                    elif subtitle_style == "two_line_slide_top_bottom":
                        anim1 = "\\move(540,-100,540,1420,0,200)"
                        anim2 = "\\move(540,2020,540,1550,0,200)"
                    elif subtitle_style == "two_line_zoom_in":
                        anim1 = "\\pos(540,1420)\\fscx0\\fscy0\\t(0,200,\\fscx100\\fscy100)"
                        anim2 = "\\pos(540,1550)\\fscx0\\fscy0\\t(0,200,\\fscx100\\fscy100)"

                    # LINE 1: shows seg.start → midpoint with entrance animation
                    if top_text:
                        sf.write("Dialogue: 0," + fmt_ass_time(seg.start) + "," + fmt_ass_time(seg_mid) + ",Default,,0,0,0,,{" + anim1 + "}" + top_text + "\n")
                    # LINE 2: shows midpoint → seg.end with its own entrance animation
                    if bottom_text:
                        sf.write("Dialogue: 0," + fmt_ass_time(seg_mid) + "," + fmt_ass_time(seg.end) + ",Default,,0,0,0,,{" + anim2 + "}" + bottom_text + "\n")
                    continue
                
                elif subtitle_style == "split_top_bottom":
                    mid = max(1, len(words) // 2)
                    top_words = words[:mid]
                    bottom_words = words[mid:]
                    
                    # Find important word index in the entire list of words
                    word_lengths = [len(w.strip(",.?!;:\"'")) for w in words]
                    longest_idx = word_lengths.index(max(word_lengths)) if word_lengths else 0
                    
                    # Top part (highlight first word of top part in yellow)
                    top_parts = []
                    for idx, w in enumerate(top_words):
                        if idx == 0:
                            top_parts.append(f"{{\\c&H0000FFFF&\\fscx100\\fscy100\\t(0,80,\\fscx114\\fscy114)\\t(80,160,\\fscx100\\fscy100)}}{w}{{\\c&H00FFFFFF&}}")
                        else:
                            top_parts.append(w)
                            
                    # Bottom part
                    bottom_parts = []
                    for idx, w in enumerate(bottom_words):
                        actual_idx = mid + idx
                        if actual_idx == longest_idx:
                            # Yellow, large word
                            bottom_parts.append(f"{{\\fs115\\c&H0000FFFF&\\fscx100\\fscy100\\t(0,80,\\fscx114\\fscy114)\\t(80,160,\\fscx100\\fscy100)}}{w}{{\\r}}")
                        else:
                            # White, small word
                            bottom_parts.append(f"{{\\fs72\\c&H00FFFFFF&}}{w}{{\\r}}")
                            
                    top_text = " ".join(top_parts)
                    bottom_text = " ".join(bottom_parts)
                    
                    if top_text:
                        sf.write(f"Dialogue: 0,{fmt_ass_time(seg.start)},{fmt_ass_time(seg.end)},Default,,0,0,0,,{{\\an8\\fs72}}{top_text}\n")
                    if bottom_text:
                        sf.write(f"Dialogue: 0,{fmt_ass_time(seg.start)},{fmt_ass_time(seg.end)},Default,,0,0,0,,{{\\an2}}{bottom_text}\n")
                    continue
                
                else:  # "default"
                    # Highlight first word yellow, rest white
                    highlighted = (
                        f"{{\\c&H0000FFFF&\\fscx100\\fscy100\\t(0,80,\\fscx114\\fscy114)\\t(80,160,\\fscx100\\fscy100)}}{words[0]}{{\\c&H00FFFFFF&}}"
                    )
                    rest = " ".join(words[1:])
                    line_text = (highlighted + " " + rest).strip() if rest else highlighted
                
                sf.write(f"Dialogue: 0,{fmt_ass_time(seg.start)},{fmt_ass_time(seg.end)},Default,,0,0,0,,{line_text}\n")

        # 2. Strip existing subs from result video and re-burn new ones
        reframed_path = os.path.join(output_folder, "reframed.mp4")
        input_base_video = reframed_path if os.path.exists(reframed_path) else result_video_path
        
        # Use result video without audio as base, then re-composite audio
        safe_ass = subs_ass_path.replace("\\", "/").replace(":", "\\:")

        # Temp output file
        new_result_path = os.path.join(output_folder, "result_edited.mp4")

        # FFmpeg: burn new subtitles onto the clean base video
        render_cmd = [
            "ffmpeg", "-y",
            "-i", input_base_video,
            "-vf", f"ass='{safe_ass}'",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "copy",
            new_result_path
        ]
        proc = subprocess.run(render_cmd, capture_output=True, text=True)

        if proc.returncode != 0:
            job.status = "failed"
            job.error_message = f"Subtitle re-render failed: {proc.stderr[:500]}"
            db2.commit()
            return

        # Check if original was an R2 remote URL
        is_r2 = False
        original_db_path = job.result_video_path
        if original_db_path and original_db_path.startswith("http"):
            is_r2 = True

        # Backup old result with version tracking (history backup)
        if os.path.exists(result_video_path):
            backup_idx = 1
            while True:
                backup_path = result_video_path.replace("result.mp4", f"result_edited_{backup_idx}.mp4")
                if not os.path.exists(backup_path):
                    break
                backup_idx += 1
            import shutil
            shutil.copy(result_video_path, backup_path)

        # Replace old result with new
        import shutil
        shutil.move(new_result_path, result_video_path)

        if is_r2:
            try:
                from app.services.r2_storage import upload_to_r2
                r2_key = f"ugc/{job_id}/result.mp4"
                new_url = upload_to_r2(result_video_path, r2_key)
                if new_url:
                    job.result_video_path = new_url
            except Exception as r2_err:
                logger.error(f"Failed to upload re-rendered video to R2: {r2_err}")

        job.status = "completed"
        job.progress = 100
        db2.commit()

    except Exception as e:
        try:
            job.status = "failed"
            job.error_message = f"Subtitle re-render error: {str(e)[:500]}"
            db2.commit()
        except Exception:
            pass
    finally:
        db2.close()


# Helper for dict to string
def json_dumps(d: dict) -> str:
    try:
        import json
        return json.dumps(d)
    except:
        return "{}"

# Helper to safely parse JSON string
def json_dumps_parse(s: str) -> dict:
    try:
        import json
        return json.loads(s)
    except:
        return {}


# ── Video Editor Routes ────────────────────────────────────────────────────────

@router.get("/ugc/brolls/{job_id}", tags=["UGC Creator"])
async def get_broll_list(
    job_id: str,
    client=Depends(_require_client),
    db: Session = Depends(get_db),
):
    """Return the list of B-roll assets from job metadata for the editor."""
    import json
    job = db.query(UgcJob).filter(UgcJob.job_id == job_id).first()
    if not job:
        raise HTTPException(404, "Job not found.")
    from app.core.config import settings
    meta = json.loads(job.metadata_json) if job.metadata_json else {}
    brolls = meta.get("broll_assets", [])

    if not brolls:
        # Auto-initialize default timeline B-rolls if empty so that the editor displays segments to modify
        duration = 15.0
        try:
            if job.transcript_json:
                import json as _json
                transcript = _json.loads(job.transcript_json)
                if transcript and isinstance(transcript, list):
                    duration = float(transcript[-1].get("end", 15.0))
        except Exception:
            pass

        # Create B-rolls spaced out on the timeline
        default_brolls = []
        t = 2.0
        idx = 0
        while t + 3.0 <= duration:
            img_path = os.path.join(str(settings.BASE_DIR), "static", "ugc", job_id, f"broll_{idx}.jpg")
            default_brolls.append({
                "index": idx,
                "start": t,
                "end": t + 3.0,
                "path": img_path,
                "prompt": f"aesthetic cinematic B-roll scene {idx + 1}",
                "keyword": "scene"
            })
            t += 6.0
            idx += 1

        if default_brolls:
            meta["broll_assets"] = default_brolls
            job.metadata_json = json.dumps(meta)
            db.commit()
            brolls = default_brolls
    result = []
    for ba in brolls:
        path = ba.get("path", "")
        rel = ""
        try:
            from app.core.config import settings
            rel_path = os.path.relpath(path, str(settings.BASE_DIR)).replace("\\", "/")
            rel = f"/{rel_path}"
        except Exception:
            pass
        result.append({
            "index": ba.get("index", 0),
            "start": ba.get("start", 0),
            "end": ba.get("end", 0),
            "prompt": ba.get("prompt", ""),
            "keyword": ba.get("keyword", ""),
            "thumbnail_url": rel,
            "exists": os.path.exists(path)
        })
    features = meta.get("features", {})
    return {
        "job_id": job_id,
        "brolls": result,
        "bgm_mood": features.get("bgm_mood", "Corporate"),
        "edit_plan": meta.get("edit_plan", {})
    }


@router.post("/ugc/brolls/{job_id}/replace", tags=["UGC Creator"])
async def replace_broll(
    job_id: str,
    index: int,
    file: Optional[UploadFile] = File(None),
    prompt: Optional[str] = None,
    target_path: Optional[str] = None,
    start: Optional[float] = None,
    end: Optional[float] = None,
    client=Depends(_require_client),
    db: Session = Depends(get_db),
):
    """Replace a B-roll by uploading a new file, re-generating via Pollinations, or referencing a generated asset path."""
    import json
    from app.core.config import settings

    job = db.query(UgcJob).filter(UgcJob.job_id == job_id).first()
    if not job:
        raise HTTPException(404, "Job not found.")

    meta = json.loads(job.metadata_json) if job.metadata_json else {}
    brolls = meta.get("broll_assets", [])
    work_dir = os.path.join(str(settings.BASE_DIR), "static", "ugc", job_id)
    img_path = os.path.join(work_dir, f"broll_{index}.jpg")

    if file:
        content = await file.read()
        async with aiofiles.open(img_path, "wb") as f:
            await f.write(content)
        new_prompt = f"custom_upload_index_{index}"
    elif target_path:
        # Use preexisting static asset path
        clean_target = target_path.lstrip("/")
        local_target_path = os.path.join(str(settings.BASE_DIR), clean_target)
        if os.path.exists(local_target_path):
            img_path = local_target_path
            new_prompt = f"referenced_asset_{os.path.basename(clean_target)}"
        else:
            raise HTTPException(400, f"Referenced asset not found: {local_target_path}")
    elif prompt:
        import httpx
        from urllib.parse import quote
        import secrets as _secrets
        enhanced = f"{prompt}, photorealistic, 8k, cinematic color grading, masterpiece, ultra-detailed"
        encoded = quote(enhanced)
        url = f"https://image.pollinations.ai/prompt/{encoded}?width=1080&height=1920&nologo=true&model=flux&seed={_secrets.token_hex(4)}"
        async with httpx.AsyncClient(timeout=90) as hc:
            resp = await hc.get(url)
            if resp.status_code == 200:
                async with aiofiles.open(img_path, "wb") as f:
                    await f.write(resp.content)
        new_prompt = enhanced
    else:
        raise HTTPException(400, "Provide either a file upload, a prompt, or target_path.")

    updated = False
    for ba in brolls:
        if ba.get("index") == index:
            ba["path"] = img_path
            ba["prompt"] = new_prompt
            if start is not None:
                ba["start"] = start
            if end is not None:
                ba["end"] = end
            updated = True
            break
    if not updated:
        brolls.append({
            "index": index,
            "path": img_path,
            "prompt": new_prompt,
            "start": start if start is not None else 0.0,
            "end": end if end is not None else 3.0
        })

    meta["broll_assets"] = brolls
    job.metadata_json = json.dumps(meta)
    db.commit()
    return {"success": True, "message": f"B-roll #{index} replaced.", "path": img_path}


class UgcSettingsUpdateRequest(BaseModel):
    logo_position: Optional[str] = None
    running_tap_text: Optional[str] = None
    intro_path: Optional[str] = None
    outro_path: Optional[str] = None
    watermark_path: Optional[str] = None
    logo_path: Optional[str] = None
    use_intro: Optional[bool] = None
    use_outro: Optional[bool] = None
    use_watermark: Optional[bool] = None
    use_logo: Optional[bool] = None
    use_running_tap: Optional[bool] = None


@router.post("/ugc/assets/{job_id}/upload", tags=["UGC Creator"])
async def upload_ugc_asset(
    job_id: str,
    asset_type: str, # intro, outro, watermark, logo
    file: UploadFile = File(...),
    client=Depends(_require_client),
    db: Session = Depends(get_db)
):
    import json, os
    from app.core.config import settings
    
    job = db.query(UgcJob).filter(UgcJob.job_id == job_id).first()
    if not job:
        raise HTTPException(404, "Job not found.")
        
    work_dir = os.path.join(str(settings.BASE_DIR), "static", "ugc", job_id)
    os.makedirs(work_dir, exist_ok=True)
    
    # Determine extension
    _, ext = os.path.splitext(file.filename)
    if not ext:
        ext = ".mp4" if asset_type in ("intro", "outro") else ".png"
        
    dest_filename = f"{asset_type}{ext.lower()}"
    dest_path = os.path.join(work_dir, dest_filename)
    
    # Write file
    content = await file.read()
    with open(dest_path, "wb") as f:
        f.write(content)
        
    # Update metadata_json
    meta = json.loads(job.metadata_json) if job.metadata_json else {}
    meta[f"{asset_type}_path"] = dest_path
    meta[f"use_{asset_type}"] = True  # Automatically enable it upon upload!
    
    # Also save as logo_path if logo
    if asset_type == "logo":
        meta["logo_path"] = dest_path
        meta["use_logo"] = True
        
    job.metadata_json = json.dumps(meta)
    db.commit()
    
    web_url = f"/static/ugc/{job_id}/{dest_filename}"
    return {"success": True, "message": f"Asset {asset_type} uploaded successfully.", "url": web_url, "path": dest_path}


@router.post("/ugc/settings/{job_id}/update", tags=["UGC Creator"])
async def update_ugc_settings(
    job_id: str,
    req: UgcSettingsUpdateRequest,
    client=Depends(_require_client),
    db: Session = Depends(get_db)
):
    import json
    job = db.query(UgcJob).filter(UgcJob.job_id == job_id).first()
    if not job:
        raise HTTPException(404, "Job not found.")
        
    meta = json.loads(job.metadata_json) if job.metadata_json else {}
    
    if req.logo_position is not None:
        meta["logo_position"] = req.logo_position
    if req.running_tap_text is not None:
        meta["running_tap_text"] = req.running_tap_text
        
    if req.intro_path is not None:
        meta["intro_path"] = req.intro_path if req.intro_path != "" else None
    if req.outro_path is not None:
        meta["outro_path"] = req.outro_path if req.outro_path != "" else None
    if req.watermark_path is not None:
        meta["watermark_path"] = req.watermark_path if req.watermark_path != "" else None
    if req.logo_path is not None:
        meta["logo_path"] = req.logo_path if req.logo_path != "" else None

    # Sync booleans
    if req.use_intro is not None:
        meta["use_intro"] = req.use_intro
    if req.use_outro is not None:
        meta["use_outro"] = req.use_outro
    if req.use_watermark is not None:
        meta["use_watermark"] = req.use_watermark
    if req.use_logo is not None:
        meta["use_logo"] = req.use_logo
    if req.use_running_tap is not None:
        meta["use_running_tap"] = req.use_running_tap
        
    job.metadata_json = json.dumps(meta)
    db.commit()
    return {"success": True, "message": "UGC settings updated.", "metadata": meta}


class BrollGenerateRequest(BaseModel):
    prompt: str
    source: str  # pollinations, pexels, meta_ai


@router.post("/ugc/generate/broll/{job_id}", tags=["UGC Creator"])
async def generate_ugc_broll_item(
    job_id: str,
    req: BrollGenerateRequest,
    client=Depends(_require_client),
    db: Session = Depends(get_db)
):
    import json, os, secrets
    from app.core.config import settings
    from app.services.video_engine import search_pexels_videos
    from app.services.ugc_service import download_file_async

    job = db.query(UgcJob).filter(UgcJob.job_id == job_id).first()
    if not job:
        raise HTTPException(404, "Job not found.")

    work_dir = os.path.join(str(settings.BASE_DIR), "static", "ugc", job_id)
    os.makedirs(work_dir, exist_ok=True)

    random_id = secrets.token_hex(4)

    if req.source == "pollinations":
        import urllib.parse
        encoded_prompt = urllib.parse.quote(req.prompt)
        img_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1080&height=1920&nologo=true&model=flux&seed={secrets.token_hex(4)}"
        
        dest_filename = f"gen_pollinations_{random_id}.png"
        dest_path = os.path.join(work_dir, dest_filename)
        
        try:
            await download_file_async(img_url, dest_path)
            web_url = f"/static/ugc/{job_id}/{dest_filename}"
            return {"success": True, "url": web_url, "type": "image"}
        except Exception as e:
            raise HTTPException(500, f"Pollinations AI failed: {e}")

    elif req.source == "pexels":
        try:
            video_urls = await search_pexels_videos(req.prompt, count=1)
            if not video_urls:
                words = req.prompt.split()
                if len(words) > 3:
                    video_urls = await search_pexels_videos(" ".join(words[:3]), count=1)
            
            if not video_urls:
                raise HTTPException(404, "No matching stock videos found on Pexels.")
                
            video_url = video_urls[0]
            dest_filename = f"gen_pexels_{random_id}.mp4"
            dest_path = os.path.join(work_dir, dest_filename)
            
            await download_file_async(video_url, dest_path)
            web_url = f"/static/ugc/{job_id}/{dest_filename}"
            return {"success": True, "url": web_url, "type": "video"}
        except Exception as e:
            raise HTTPException(500, f"Pexels Video search/download failed: {e}")

    elif req.source == "meta_ai":
        return {"success": True, "waiting_for_extension": True, "random_id": random_id, "prompt": req.prompt}

    else:
        raise HTTPException(400, f"Unknown source: {req.source}")


@router.get("/ugc/generate/broll/list/{job_id}", tags=["UGC Creator"])
async def list_generated_brolls(
    job_id: str,
    client=Depends(_require_client),
    db: Session = Depends(get_db)
):
    import os
    from app.core.config import settings
    work_dir = os.path.join(str(settings.BASE_DIR), "static", "ugc", job_id)
    assets = []
    
    # 1. Scan job static outputs directory
    if os.path.exists(work_dir):
        for f in os.listdir(work_dir):
            if f.startswith("gen_") or f.startswith("single_gen_"):
                ext = f.split(".")[-1].lower()
                media_type = "video" if ext in ["mp4", "mkv", "mov", "avi"] else "image"
                
                source = "pollinations"
                if "pexels" in f:
                    source = "pexels"
                elif "meta_ai" in f or "single_gen" in f:
                    source = "meta_ai"
                    
                web_url = f"/static/ugc/{job_id}/{f}"
                assets.append({
                    "url": web_url,
                    "source": source,
                    "type": media_type
                })
                
    # 2. Scan uploads/social directory for extension-downloaded files
    social_uploads = os.path.join(os.getcwd(), "uploads", "social")
    if os.path.exists(social_uploads):
        for f in os.listdir(social_uploads):
            if f.startswith("single_gen_"):
                ext = f.split(".")[-1].lower()
                media_type = "video" if ext in ["mp4"] else "image"
                web_url = f"/uploads/social/{f}"
                # Deduplicate if already present
                if not any(a["url"] == web_url for a in assets):
                    assets.append({
                        "url": web_url,
                        "source": "meta_ai",
                        "type": media_type
                    })
                    
    return {"success": True, "assets": assets}


@router.post("/ugc/generate/broll/delete-file", tags=["UGC Creator"])
async def delete_generated_broll_file(
    job_id: str,
    url: str,
    client=Depends(_require_client),
    db: Session = Depends(get_db)
):
    import os, urllib.parse
    from app.core.config import settings
    
    parsed_url = urllib.parse.unquote(url)
    
    if parsed_url.startswith(f"/static/ugc/{job_id}/"):
        filename = parsed_url.split("/")[-1]
        if not all(c.isalnum() or c in "._-" for c in filename):
            raise HTTPException(400, "Invalid filename")
            
        file_path = os.path.join(str(settings.BASE_DIR), "static", "ugc", job_id, filename)
        if os.path.exists(file_path):
            os.remove(file_path)
            return {"success": True, "message": "File deleted."}
            
    elif parsed_url.startswith("/uploads/social/"):
        filename = parsed_url.split("/")[-1]
        if not all(c.isalnum() or c in "._-" for c in filename):
            raise HTTPException(400, "Invalid filename")
            
        file_path = os.path.join(os.getcwd(), "uploads", "social", filename)
        if os.path.exists(file_path):
            os.remove(file_path)
            return {"success": True, "message": "File deleted."}
            
    raise HTTPException(404, "File not found or invalid path.")


@router.get("/ugc/jobs/{job_id}/download-bundle", tags=["UGC Creator"])
async def download_ugc_job_bundle(
    job_id: str,
    token: Optional[str] = None,
    db: Session = Depends(get_db)
):
    import io, zipfile, json, os
    from fastapi.responses import StreamingResponse
    from app.core.config import settings
    from app.core.clients import validate_client_token
    
    if not token:
        raise HTTPException(401, "Token required")
    record = validate_client_token(token, db=db)
    if not record:
        raise HTTPException(401, "Invalid token")
        
    job = db.query(UgcJob).filter(UgcJob.job_id == job_id).first()
    if not job:
        raise HTTPException(404, "Job not found.")
        
    static_dir = os.path.join(str(settings.BASE_DIR), "static", "ugc", job_id)
    
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        # 1. Original Video
        if job.original_video_path and os.path.exists(job.original_video_path):
            ext = os.path.basename(job.original_video_path).split(".")[-1]
            zip_file.write(job.original_video_path, f"original_video.{ext}")
            
        # 2. Final Video
        result_path = job.result_video_path
        if result_path and os.path.exists(result_path):
            ext = os.path.basename(result_path).split(".")[-1]
            zip_file.write(result_path, f"final_video.{ext}")
            
        # 3. Viral Short Video
        if job.viral_video_url:
            viral_filename = os.path.basename(job.viral_video_url)
            local_viral_path = os.path.join(static_dir, viral_filename)
            if os.path.exists(local_viral_path):
                zip_file.write(local_viral_path, f"viral_short.mp4")
                
        # 4. Brand Logo
        meta = json.loads(job.metadata_json) if job.metadata_json else {}
        logo_path = meta.get("logo_path")
        if not logo_path and job.settings:
            logo_path = job.settings.get("logo_path")
        if logo_path and os.path.exists(logo_path):
            ext = os.path.basename(logo_path).split(".")[-1]
            zip_file.write(logo_path, f"brand_logo.{ext}")
            
        # 5. Transcript (JSON and text format)
        if job.transcript_json:
            try:
                transcript_data = json.loads(job.transcript_json)
                zip_file.writestr("transcript.json", json.dumps(transcript_data, indent=2))
                lines = []
                for s in transcript_data:
                    lines.append(f"[{s.get('start', 0.0):.1f}s - {s.get('end', 0.0):.1f}s] {s.get('text', '')}")
                zip_file.writestr("transcript.txt", "\n".join(lines))
            except Exception:
                pass
                
        # 6. B-roll assets from metadata timeline
        brolls = meta.get("broll_assets", [])
        for ba in brolls:
            path = ba.get("path")
            if path and os.path.exists(path):
                ext = os.path.basename(path).split(".")[-1]
                zip_file.write(path, f"brolls/broll_segment_{ba.get('index', 0)}.{ext}")
                
        # 7. Scan and bundle any other static generated assets in job folder
        if os.path.exists(static_dir):
            for f in os.listdir(static_dir):
                if f.startswith("gen_") or f.startswith("single_gen_") or f.startswith("broll_"):
                    file_path = os.path.join(static_dir, f)
                    if os.path.isfile(file_path):
                        archive_name = f"brolls/{f}"
                        if archive_name not in zip_file.namelist():
                            zip_file.write(file_path, archive_name)
                            
    zip_buffer.seek(0)
    
    safe_filename = f"bundle_{job_id}.zip"
    headers = {
        "Content-Disposition": f"attachment; filename={safe_filename}"
    }
    return StreamingResponse(zip_buffer, media_type="application/zip", headers=headers)


class BrollDeleteRequest(BaseModel):
    index: int = Field(..., description="B-roll index to delete")


@router.post("/ugc/brolls/{job_id}/delete", tags=["UGC Creator"])
async def delete_broll(
    job_id: str,
    req: BrollDeleteRequest,
    client=Depends(_require_client),
    db: Session = Depends(get_db),
):
    """Remove a B-roll entry from the job metadata."""
    import json
    job = db.query(UgcJob).filter(UgcJob.job_id == job_id).first()
    if not job:
        raise HTTPException(404, "Job not found.")
    meta = json.loads(job.metadata_json) if job.metadata_json else {}
    brolls = meta.get("broll_assets", [])
    original_count = len(brolls)
    brolls = [ba for ba in brolls if ba.get("index") != req.index]
    meta["broll_assets"] = brolls
    job.metadata_json = json.dumps(meta)
    db.commit()
    return {"success": True, "message": f"B-roll #{req.index} deleted.", "remaining": len(brolls)}


@router.post("/ugc/music/{job_id}/replace", tags=["UGC Creator"])
async def replace_music(
    job_id: str,
    mood: Optional[str] = None,
    file: Optional[UploadFile] = File(None),
    client=Depends(_require_client),
    db: Session = Depends(get_db),
):
    """Replace BGM by mood or custom file upload."""
    import json
    from app.core.config import settings

    job = db.query(UgcJob).filter(UgcJob.job_id == job_id).first()
    if not job:
        raise HTTPException(404, "Job not found.")
    meta = json.loads(job.metadata_json) if job.metadata_json else {}
    work_dir = os.path.join(str(settings.BASE_DIR), "static", "ugc", job_id)

    if file:
        bgm_path = os.path.join(work_dir, "custom_bgm.mp3")
        content = await file.read()
        async with aiofiles.open(bgm_path, "wb") as f:
            await f.write(content)
        features = meta.get("features", {})
        features["custom_bgm_path"] = bgm_path
        meta["features"] = features
    elif mood:
        features = meta.get("features", {})
        features["bgm_mood"] = mood
        features.pop("custom_bgm_path", None)
        meta["features"] = features
    else:
        raise HTTPException(400, "Provide mood or music file.")

    job.metadata_json = json.dumps(meta)
    db.commit()
    return {"success": True, "message": "Music updated.", "mood": mood or "custom"}


@router.post("/ugc/rerender/{job_id}", tags=["UGC Creator"])
async def fast_rerender(
    job_id: str,
    background_tasks: BackgroundTasks,
    client=Depends(_require_client),
    db: Session = Depends(get_db),
):
    """Fast re-render using existing reframed.mp4 + updated B-roll/music metadata. Skips all AI stages."""
    import json
    job = db.query(UgcJob).filter(UgcJob.job_id == job_id).first()
    if not job:
        raise HTTPException(404, "Job not found.")
    if job.status not in ("completed", "error"):
        raise HTTPException(400, "Job must be completed before re-rendering.")

    job.status = "processing"
    job.progress = 50
    job.error_message = None
    db.commit()

    background_tasks.add_task(_fast_rerender_task, job_id)
    return {"success": True, "message": "Fast re-render started."}


def overlay_brolls_on_video(clean_video_path: str, output_video_path: str, broll_assets: list):
    """Dynamically overlays B-roll images and videos on top of a clean reframed video."""
    import cv2, os, numpy as np
    
    cap = cv2.VideoCapture(clean_video_path)
    if not cap.isOpened():
        raise Exception(f"Could not open clean video for reading: {clean_video_path}")
        
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    
    # Use mp4v fourcc to write temporary silent reframed file
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter(output_video_path, fourcc, fps, (w, h))
    
    frame_idx = 0
    broll_cap = None
    active_broll_cap_path = None
    
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break
                
            frame_time = frame_idx / fps
            frame_idx += 1
            
            # Find if any B-roll matches current playback timestamp
            active_broll = None
            for br in broll_assets:
                if br.get("start", 0.0) <= frame_time <= br.get("end", 0.0):
                    active_broll = br
                    break
                    
            if active_broll and active_broll.get("path") and os.path.exists(active_broll["path"]):
                b_path = active_broll["path"]
                broll_img = None
                
                # Check if B-roll is a video
                if b_path.lower().endswith((".mp4", ".mov", ".avi", ".mkv")):
                    try:
                        if active_broll_cap_path != b_path:
                            if broll_cap is not None:
                                broll_cap.release()
                            broll_cap = cv2.VideoCapture(b_path)
                            active_broll_cap_path = b_path
                            
                        if broll_cap is not None and broll_cap.isOpened():
                            b_fps = broll_cap.get(cv2.CAP_PROP_FPS) or 30.0
                            b_total = int(broll_cap.get(cv2.CAP_PROP_FRAME_COUNT))
                            elapsed = frame_time - active_broll.get("start", 0.0)
                            b_frame_idx = int(elapsed * b_fps)
                            if b_total > 0:
                                b_frame_idx = b_frame_idx % b_total
                                
                            broll_cap.set(cv2.CAP_PROP_POS_FRAMES, b_frame_idx)
                            ret_v, broll_frame = broll_cap.read()
                            if ret_v and broll_frame is not None:
                                broll_img = broll_frame
                    except Exception:
                        pass
                
                # Fallback to image reading if it's an image or video frame read failed
                if broll_img is None:
                    try:
                        broll_img = cv2.imread(b_path)
                    except Exception:
                        pass
                        
                if broll_img is not None:
                    # Apply Ken Burns Zoom effect
                    broll_progress = 0.0
                    broll_dur = active_broll.get("end", 1.0) - active_broll.get("start", 0.0)
                    if broll_dur > 0:
                        broll_progress = (frame_time - active_broll.get("start", 0.0)) / broll_dur
                        
                    scale = 1.0 + 0.05 * broll_progress  # zoom 100% to 105%
                    bh, bw = broll_img.shape[:2]
                    sz_h, sz_w = int(bh / scale), int(bw / scale)
                    sy, sx = (bh - sz_h) // 2, (bw - sz_w) // 2
                    broll_img = broll_img[sy:sy+sz_h, sx:sx+sz_w]
                    broll_resized = cv2.resize(broll_img, (w, h))
                    
                    # ── Circular Face Bubble on B-roll ──
                    try:
                        # Extract a square from the center of the clean speaker frame
                        face_square_size = min(w, h)
                        y_center = h // 2
                        x_center = w // 2
                        half_s = face_square_size // 2
                        
                        square_speaker = frame[max(0, y_center - half_s): min(h, y_center + half_s), max(0, x_center - half_s): min(w, x_center + half_s)]
                        
                        # Size of the bubble on screen (38% of canvas width)
                        bubble_size = int(w * 0.38)
                        bubble_img = cv2.resize(square_speaker, (bubble_size, bubble_size))
                        
                        # Create circular mask
                        mask = np.zeros((bubble_size, bubble_size), dtype=np.uint8)
                        cv2.circle(mask, (bubble_size // 2, bubble_size // 2), (bubble_size // 2) - 2, 255, -1)
                        
                        # Draw white border
                        cv2.circle(bubble_img, (bubble_size // 2, bubble_size // 2), (bubble_size // 2) - 2, (255, 255, 255), 5)
                        
                        # Position coordinates: bottom-right (clear of subtitles)
                        px = w - bubble_size - 40
                        py = h - bubble_size - 220
                        
                        for c in range(3):
                            broll_resized[py:py+bubble_size, px:px+bubble_size, c] = np.where(
                                mask == 255,
                                bubble_img[:, :, c],
                                broll_resized[py:py+bubble_size, px:px+bubble_size, c]
                            )
                    except Exception:
                        pass
                        
                    out.write(broll_resized)
                    continue
                    
            out.write(frame)
    finally:
        cap.release()
        out.release()
        if broll_cap is not None:
            broll_cap.release()
def has_audio(video_path: str) -> bool:
    import subprocess
    cmd = ["ffprobe", "-show_streams", "-select_streams", "a", "-loglevel", "error", video_path]
    res = subprocess.run(cmd, capture_output=True, text=True)
    return len(res.stdout.strip()) > 0


def standardize_video(input_path: str, output_path: str):
    import subprocess
    if has_audio(input_path):
        filter_str = "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(1080-iw)/2:(1920-ih)/2,fps=30,setsar=1[v];[0:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[a]"
        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-filter_complex", filter_str,
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            output_path
        ]
    else:
        filter_str = "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(1080-iw)/2:(1920-ih)/2,fps=30,setsar=1[v]"
        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-filter_complex", filter_str,
            "-map", "[v]", "-map", "1:a",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest",
            output_path
        ]
    subprocess.run(cmd, capture_output=True)


def concat_videos(video_list: list, output_path: str):
    import subprocess, os
    temp_txt = output_path + "_concat.txt"
    with open(temp_txt, "w") as f:
        for v in video_list:
            v_clean = v.replace("\\", "/")
            f.write(f"file '{v_clean}'\n")
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", temp_txt,
        "-c", "copy", output_path
    ]
    subprocess.run(cmd, capture_output=True)
    if os.path.exists(temp_txt):
        os.remove(temp_txt)


async def _fast_rerender_task(job_id: str):
    """Re-run FFmpeg subtitle burn + audio mix using updated metadata and custom B-roll overlay."""
    import json, subprocess, asyncio, shutil
    from app.core.config import settings
    from app.core.database import get_session_local
    from app.services.ugc_service import MOOD_MUSIC_URLS, download_file_async

    SessionLocal = get_session_local()
    db2 = SessionLocal()
    try:
        job = db2.query(UgcJob).filter(UgcJob.job_id == job_id).first()
        if not job:
            return

        meta = json.loads(job.metadata_json) if job.metadata_json else {}
        # Safely extract features
        features = meta.get("features", {}) or {}
        for k, v in meta.items():
            if k not in features and not isinstance(v, (dict, list)):
                features[k] = v

        work_dir = os.path.join(str(settings.BASE_DIR), "static", "ugc", job_id)
        uploads_work_dir = os.path.join(str(settings.BASE_DIR), "uploads", "ugc", job_id)
        
        # Output silent, audio, and subtitle output files
        reframed_silent_path = os.path.join(work_dir, "reframed_silent.mp4")
        reframed_path = os.path.join(work_dir, "reframed.mp4")
        clean_video_path = os.path.join(work_dir, "reframed_clean.mp4")

        # Copy reframed_clean.mp4 from uploads directory if it is missing in static outputs directory
        if not os.path.exists(clean_video_path):
            uploads_clean = os.path.join(uploads_work_dir, "reframed_clean.mp4")
            if os.path.exists(uploads_clean):
                shutil.copy(uploads_clean, clean_video_path)

        # 1. Dynamically overlay B-rolls on top of reframed_clean.mp4 to write reframed_silent.mp4
        if os.path.exists(clean_video_path):
            logger.info(f"Rebuilding reframed_silent.mp4 with updated B-rolls for job {job_id}...")
            broll_assets = meta.get("broll_assets", [])
            overlay_brolls_on_video(clean_video_path, reframed_silent_path, broll_assets)
        else:
            logger.warning(f"reframed_clean.mp4 not found for job {job_id}. Skipping dynamic B-roll rebuild.")
            # Fallback: if reframed_clean.mp4 is missing but reframed_silent.mp4 exists, use it
            if not os.path.exists(reframed_silent_path) and os.path.exists(reframed_path):
                # We can use reframed_path as silent fallback using ffmpeg strip
                subprocess.run(["ffmpeg", "-y", "-i", reframed_path, "-an", "-c:v", "copy", reframed_silent_path], capture_output=True)

        job.progress = 60
        db2.commit()

        # 2. Find and resolve Background Music
        bgm_path = features.get("custom_bgm_path")
        user_mood = features.get("bgm_mood")
        
        if not bgm_path or not os.path.exists(str(bgm_path)):
            if user_mood and user_mood != "none":
                bgm_url = MOOD_MUSIC_URLS.get(user_mood)
                if bgm_url:
                    bgm_path = os.path.join(work_dir, "bgm.mp3")
                    try:
                        logger.info(f"Downloading mood BGM track: {bgm_url}")
                        await download_file_async(bgm_url, bgm_path)
                    except Exception as e:
                        logger.error(f"Failed to download BGM mood music: {e}")
                        bgm_path = None
            else:
                bgm_path = os.path.join(work_dir, "bgm.mp3")
                if not os.path.exists(bgm_path):
                    bgm_path = None

        # 3. Construct premium final audio mix (Trimmed Voice, Auto-ducked BGM, SFX)
        voice_path = os.path.join(uploads_work_dir, "trimmed_audio.mp3")
        if not os.path.exists(voice_path):
            voice_path = os.path.join(uploads_work_dir, "extracted_audio.mp3")
            
        final_audio_path = os.path.join(work_dir, "final_audio.mp3")
        
        if os.path.exists(voice_path):
            mix_cmd = ["ffmpeg", "-y", "-i", voice_path]
            mix_filter = []
            mix_inputs = ["[0:a]volume=1.4[voice];"]
            current_input_idx = 1
            
            if bgm_path and os.path.exists(bgm_path) and features.get("music", True) and user_mood != "none":
                mix_cmd.extend(["-i", bgm_path])
                bgm_input_tag = f"[{current_input_idx}:a]"
                current_input_idx += 1
                mix_filter.append(f"{bgm_input_tag}volume=0.08[bgm_base];")
                mix_filter.append(f"[bgm_base][0:a]sidechaincompress=threshold=0.03:ratio=4:attack=100:release=400[bgm_ducked];")
                mix_inputs.append("[bgm_ducked]")
                
            mixed_sfx_path = os.path.join(uploads_work_dir, "mixed_sfx.wav")
            if features.get("sfx") and os.path.exists(mixed_sfx_path):
                mix_cmd.extend(["-i", mixed_sfx_path])
                sfx_input_tag = f"[{current_input_idx}:a]"
                current_input_idx += 1
                mix_inputs.append(sfx_input_tag)
                
            num_mix = len(mix_inputs) - 1
            if num_mix == 0:
                mix_filter.append("[0:a]volume=1.4[a_mix]")
            elif num_mix == 1 and "[bgm_ducked]" in mix_inputs:
                mix_filter.append("[voice][bgm_ducked]amix=inputs=2:duration=first[a_mix]")
            elif num_mix == 1:
                mix_filter.append(f"[voice]{sfx_input_tag}amix=inputs=2:duration=first[a_mix]")
            else:
                mix_filter.append(f"[voice][bgm_ducked]{sfx_input_tag}amix=inputs=3:duration=first[a_mix]")
                
            mix_cmd.extend(["-filter_complex", "".join(mix_filter), "-map", "[a_mix]", final_audio_path])
            subprocess.run(mix_cmd, capture_output=True)
        else:
            uploads_final_audio = os.path.join(uploads_work_dir, "final_audio.mp3")
            if os.path.exists(uploads_final_audio):
                shutil.copy(uploads_final_audio, final_audio_path)

        # 4. Mux reframed_silent.mp4 + final_audio.mp3 -> reframed.mp4
        if os.path.exists(reframed_silent_path) and os.path.exists(final_audio_path):
            mux_cmd = [
                "ffmpeg", "-y",
                "-i", reframed_silent_path,
                "-i", final_audio_path,
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-shortest",
                reframed_path
            ]
            subprocess.run(mux_cmd, capture_output=True)

        job.progress = 75
        db2.commit()

        # 5. Resolve subtitles, logo positioning, watermark, and running text ticker to produce result.mp4
        subs_edited = os.path.join(work_dir, "subtitles_edited.ass")
        subs_main = os.path.join(work_dir, "subtitles.ass")
        active_subs = subs_edited if os.path.exists(subs_edited) else subs_main

        result_path = os.path.join(work_dir, "result.mp4")

        # Backup old result with version tracking (history backup)
        if os.path.exists(result_path):
            backup_idx = 1
            while True:
                backup_path = os.path.join(work_dir, f"result_edited_{backup_idx}.mp4")
                if not os.path.exists(backup_path):
                    break
                backup_idx += 1
            shutil.copy(result_path, backup_path)

        # Build FFmpeg command for filters: subtitles, logo, watermark, running tap text
        cmd_args = ["ffmpeg", "-y", "-i", reframed_path]
        filter_parts = []
        current_v = "[0:v]"
        input_idx = 1

        # A. Subtitles Ass file
        if active_subs and os.path.exists(active_subs):
            safe_subs = active_subs.replace("\\", "/").replace(":", "\\:")
            filter_parts.append(f"{current_v}ass='{safe_subs}'[v_subs]")
            current_v = "[v_subs]"

        # B. Watermark overlay (right bottom, scaled to 180px width)
        watermark_path = meta.get("watermark_path")
        use_watermark = meta.get("use_watermark", True)
        if watermark_path and use_watermark and os.path.exists(watermark_path):
            cmd_args.extend(["-i", watermark_path])
            filter_parts.append(f"[{input_idx}:v]scale=180:-1[wat_scale]")
            filter_parts.append(f"{current_v}[wat_scale]overlay=main_w-overlay_w-40:main_h-overlay_h-40[v_wat]")
            current_v = "[v_wat]"
            input_idx += 1

        # C. Positional Logo Overlay (Left-Top, Right-Top, Left-Bottom, Right-Bottom, scaled to 150px width)
        logo_path = meta.get("logo_path")
        if not logo_path and job.settings:
            logo_path = job.settings.get("logo_path")
        use_logo = meta.get("use_logo", True)
        if logo_path and use_logo and os.path.exists(logo_path):
            cmd_args.extend(["-i", logo_path])
            logo_pos = meta.get("logo_position", "left_top")
            filter_parts.append(f"[{input_idx}:v]scale=150:-1[logo_scale]")
            
            overlay_coords = "40:40"
            if logo_pos == "right_top":
                overlay_coords = "main_w-overlay_w-40:40"
            elif logo_pos == "left_bottom":
                overlay_coords = "40:main_h-overlay_h-40"
            elif logo_pos == "right_bottom":
                overlay_coords = "main_w-overlay_w-40:main_h-overlay_h-40"
                
            filter_parts.append(f"{current_v}[logo_scale]overlay={overlay_coords}[v_logo]")
            current_v = "[v_logo]"
            input_idx += 1

        # D. Running Tap Text ticker drawtext filter
        running_text = meta.get("running_tap_text")
        use_running_tap = meta.get("use_running_tap", True)
        if running_text and use_running_tap:
            safe_text = running_text.replace("'", "'\\''").replace(":", "\\:")
            filter_parts.append(f"{current_v}drawtext=text='{safe_text}':x=w-mod(t*120\\,w+tw):y=h-100:fontsize=28:fontcolor=white:box=1:boxcolor=black@0.5[v_run]")
            current_v = "[v_run]"

        # Compile video filter complex argument
        if filter_parts:
            cmd_args.extend(["-filter_complex", ";".join(filter_parts), "-map", current_v, "-map", "0:a"])
        else:
            cmd_args.extend(["-map", "0:v", "-map", "0:a"])

        # Write to intermediate video file
        temp_main_subbed = os.path.join(work_dir, "temp_subbed.mp4")
        cmd_args.extend([
            "-c:v", "libx264", "-crf", "20", "-preset", "fast",
            "-c:a", "copy", temp_main_subbed
        ])
        
        subprocess.run(cmd_args, capture_output=True)

        if os.path.exists(temp_main_subbed):
            shutil.copy(temp_main_subbed, result_path)
            os.remove(temp_main_subbed)

        # 6. Intro & Outro Stitches
        intro_path = meta.get("intro_path")
        use_intro = meta.get("use_intro", True)
        outro_path = meta.get("outro_path")
        use_outro = meta.get("use_outro", True)

        has_intro = intro_path and use_intro and os.path.exists(intro_path)
        has_outro = outro_path and use_outro and os.path.exists(outro_path)

        if has_intro or has_outro:
            concat_list = []
            
            # Intro
            if has_intro:
                intro_std = os.path.join(work_dir, "intro_std.mp4")
                standardize_video(intro_path, intro_std)
                if os.path.exists(intro_std):
                    concat_list.append(intro_std)
                    
            # Main video
            main_std = os.path.join(work_dir, "main_std.mp4")
            standardize_video(result_path, main_std)
            if os.path.exists(main_std):
                concat_list.append(main_std)
                
            # Outro
            if has_outro:
                outro_std = os.path.join(work_dir, "outro_std.mp4")
                standardize_video(outro_path, outro_std)
                if os.path.exists(outro_std):
                    concat_list.append(outro_std)
                    
            if len(concat_list) > 1:
                concat_output = os.path.join(work_dir, "result_concat.mp4")
                concat_videos(concat_list, concat_output)
                if os.path.exists(concat_output):
                    shutil.copy(concat_output, result_path)
                    
                # Clean intermediate standard files
                for fpath in concat_list:
                    try:
                        if os.path.exists(fpath):
                            os.remove(fpath)
                    except Exception:
                        pass
                try:
                    if os.path.exists(concat_output):
                        os.remove(concat_output)
                except Exception:
                    pass

        job.progress = 95
        db2.commit()

        # Thumbnail extraction
        thumb_path = os.path.join(work_dir, "thumbnail.jpg")
        subprocess.run(["ffmpeg", "-y", "-i", result_path, "-ss", "00:00:01", "-vframes", "1", "-q:v", "2", thumb_path], capture_output=True)

        if job.result_video_path and job.result_video_path.startswith("http"):
            try:
                from app.services.r2_storage import upload_to_r2
                r2_key = f"ugc/{job_id}/result.mp4"
                new_url = upload_to_r2(result_path, r2_key)
                if new_url:
                    job.result_video_path = new_url
            except Exception as r2_err:
                logger.error(f"Failed to upload fast-rerendered video to R2: {r2_err}")

        job.status = "completed"
        job.progress = 100
        db2.commit()
        logger.info(f"Fast re-render complete for {job_id}")

    except Exception as e:
        logger.error(f"Fast re-render error {job_id}: {e}")
        try:
            job = db2.query(UgcJob).filter(UgcJob.job_id == job_id).first()
            if job:
                job.status = "error"
                job.error_message = str(e)
                db2.commit()
        except Exception:
            pass
    finally:
        db2.close()


@router.delete("/ugc/jobs/{job_id}", tags=["UGC Creator"])
async def delete_ugc_job(
    job_id: str,
    client=Depends(_require_client),
    db: Session = Depends(get_db),
):
    """Delete a UGC Job, its files from disk, and R2 metadata."""
    import shutil
    from app.core.config import settings

    job = db.query(UgcJob).filter(UgcJob.job_id == job_id).first()
    if not job:
        raise HTTPException(404, "Job not found.")

    # Delete R2 files if uploaded
    try:
        from app.services.r2_storage import delete_from_r2
        delete_from_r2(f"ugc/{job_id}/result.mp4")
        delete_from_r2(f"ugc/{job_id}/thumbnail.jpg")
    except Exception:
        pass

    # Delete local folder
    work_dir = os.path.join(str(settings.BASE_DIR), "static", "ugc", job_id)
    if os.path.exists(work_dir):
        try:
            shutil.rmtree(work_dir)
        except Exception as e:
            logger.error(f"Failed to delete local workdir {work_dir}: {e}")

    # Delete from DB
    db.delete(job)
    db.commit()
    return {"success": True, "message": f"Job {job_id} deleted successfully."}


@router.post("/ugc/jobs/{job_id}/approve", tags=["UGC Creator"])
async def approve_ugc_job(
    job_id: str,
    client=Depends(_require_client),
    db: Session = Depends(get_db),
):
    """Mark a UGC Job as approved by saving the status in metadata_json."""
    import json
    job = db.query(UgcJob).filter(UgcJob.job_id == job_id).first()
    if not job:
        raise HTTPException(404, "Job not found.")

    meta = json.loads(job.metadata_json) if job.metadata_json else {}
    meta["approved"] = True
    meta["rejected"] = False  # Reset rejection if approved
    job.metadata_json = json.dumps(meta)
    db.commit()
    return {"success": True, "message": f"Job {job_id} marked as approved.", "approved": True}


@router.post("/ugc/jobs/{job_id}/reject", tags=["UGC Creator"])
async def reject_ugc_job(
    job_id: str,
    client=Depends(_require_client),
    db: Session = Depends(get_db),
):
    """Mark a UGC Job as rejected by saving the status in metadata_json."""
    import json
    job = db.query(UgcJob).filter(UgcJob.job_id == job_id).first()
    if not job:
        raise HTTPException(404, "Job not found.")

    meta = json.loads(job.metadata_json) if job.metadata_json else {}
    meta["rejected"] = True
    meta["approved"] = False  # Ensure mutual exclusion
    job.metadata_json = json.dumps(meta)
    db.commit()
    return {"success": True, "message": f"Job {job_id} marked as rejected.", "rejected": True}

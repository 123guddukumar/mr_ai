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
        job = UgcJob(
            job_id=job_id,
            client_id=client["client_id"],
            filename=filename,
            status="pending",
            progress=0,
            original_video_path=video_path,
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
    """Returns the current status of the UGC video enhancement job."""
    job = db.query(UgcJob).filter(
        UgcJob.job_id == job_id,
        UgcJob.client_id == client["client_id"]
    ).first()
    if not job:
        raise HTTPException(404, "UGC Job not found.")

    return job.to_dict()



@router.get("/ugc/jobs", tags=["UGC Creator"])
async def list_ugc_jobs(
    client: dict = Depends(_require_client),
    db: Session = Depends(get_db),
):
    """Returns all UGC video jobs run by the authenticated client."""
    jobs = db.query(UgcJob).filter(
        UgcJob.client_id == client["client_id"]
    ).order_by(UgcJob.created_at.desc()).all()

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

    if not job.result_video_path:
        raise HTTPException(404, "Result video path is empty. Cannot re-render.")

    # Convert relative web URL path (e.g. /static/ugc/...) to local disk path
    video_rel_path = job.result_video_path.lstrip("/")
    local_video_path = os.path.join(str(settings.BASE_DIR), video_rel_path)

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

        # Replace old result with new
        import shutil
        shutil.move(new_result_path, result_video_path)

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
    meta = json.loads(job.metadata_json) if job.metadata_json else {}
    brolls = meta.get("broll_assets", [])
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
    client=Depends(_require_client),
    db: Session = Depends(get_db),
):
    """Replace a B-roll by uploading a new file OR re-generating via Pollinations."""
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
        raise HTTPException(400, "Provide either a file upload or a prompt for regeneration.")

    updated = False
    for ba in brolls:
        if ba.get("index") == index:
            ba["path"] = img_path
            ba["prompt"] = new_prompt
            updated = True
            break
    if not updated:
        brolls.append({"index": index, "path": img_path, "prompt": new_prompt, "start": 0, "end": 0})

    meta["broll_assets"] = brolls
    job.metadata_json = json.dumps(meta)
    db.commit()
    return {"success": True, "message": f"B-roll #{index} replaced.", "path": img_path}


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


async def _fast_rerender_task(job_id: str):
    """Re-run only FFmpeg subtitle burn + audio mix using updated metadata."""
    import json, subprocess, asyncio
    from app.core.config import settings
    from app.core.database import get_session_local

    SessionLocal = get_session_local()
    db2 = SessionLocal()
    try:
        job = db2.query(UgcJob).filter(UgcJob.job_id == job_id).first()
        if not job:
            return

        meta = json.loads(job.metadata_json) if job.metadata_json else {}
        features = meta.get("features", {})
        work_dir = os.path.join(str(settings.BASE_DIR), "static", "ugc", job_id)
        reframed_path = os.path.join(work_dir, "reframed.mp4")

        if not os.path.exists(reframed_path):
            job.status = "error"
            job.error_message = "reframed.mp4 not found."
            db2.commit()
            return

        # Find BGM
        bgm_path = features.get("custom_bgm_path")
        if not bgm_path or not os.path.exists(str(bgm_path)):
            bgm_path = os.path.join(work_dir, "bgm.mp3")
            if not os.path.exists(bgm_path):
                bgm_path = None

        # Find subtitles
        subs_edited = os.path.join(work_dir, "subtitles_edited.ass")
        subs_main = os.path.join(work_dir, "subtitles.ass")
        active_subs = subs_edited if os.path.exists(subs_edited) else subs_main

        job.progress = 70
        db2.commit()

        result_path = os.path.join(work_dir, "result.mp4")

        if active_subs and os.path.exists(active_subs):
            safe_subs = active_subs.replace("\\", "/").replace(":", "\\:")
            vf = f"ass='{safe_subs}'"
        else:
            vf = "null"

        if bgm_path and features.get("music", True):
            cmd = [
                "ffmpeg", "-y", "-i", reframed_path, "-i", bgm_path,
                "-filter_complex",
                f"[0:a]volume=1.0[main];[1:a]volume=0.12[bgm];[main][bgm]amix=inputs=2:duration=first[aout];[0:v]{vf}[vout]",
                "-map", "[vout]", "-map", "[aout]",
                "-c:v", "libx264", "-crf", "20", "-preset", "fast",
                "-c:a", "aac", "-b:a", "192k", result_path
            ]
        else:
            cmd = [
                "ffmpeg", "-y", "-i", reframed_path,
                "-vf", vf,
                "-c:v", "libx264", "-crf", "20", "-preset", "fast",
                "-c:a", "aac", "-b:a", "192k", result_path
            ]

        subprocess.run(cmd, capture_output=True)
        job.progress = 95
        db2.commit()

        # Thumbnail
        thumb_path = os.path.join(work_dir, "thumbnail.jpg")
        subprocess.run(["ffmpeg", "-y", "-i", result_path, "-ss", "00:00:01", "-vframes", "1", "-q:v", "2", thumb_path], capture_output=True)

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

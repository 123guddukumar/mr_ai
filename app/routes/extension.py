"""
Extension API Routes - Bridge between Chrome Extension and Backend
"""
import os
import json
import secrets
import logging
import asyncio
import subprocess
from datetime import datetime
from typing import Optional, List
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.clients import validate_client_token
from app.core.config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# ── In-memory job store (use Redis in production) ─────────────────────────────
JOBS_FILE = os.path.join(os.getcwd(), "vector_store", "ext_jobs.json")

def _load_jobs() -> dict:
    try:
        if os.path.exists(JOBS_FILE):
            return json.loads(open(JOBS_FILE, encoding='utf-8').read())
    except: pass
    return {}

def _save_jobs(jobs: dict):
    try:
        open(JOBS_FILE, 'w', encoding='utf-8').write(json.dumps(jobs, ensure_ascii=False))
    except Exception as e:
        logger.warning(f"Could not save jobs: {e}")

_jobs: dict = _load_jobs()

# ── Auth ──────────────────────────────────────────────────────────────────────
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


# ── Request Models ────────────────────────────────────────────────────────────
class CreateJobReq(BaseModel):
    subtopic_id: str
    language: Optional[str] = "English"
    voice_id: Optional[str] = None
    transcript: Optional[str] = ""

class ImageDoneReq(BaseModel):
    filename: str
    index: int

class VideoDoneReq(BaseModel):
    filename: str
    index: int

class AssembleReq(BaseModel):
    videos: List[str]
    images: List[str]


def robust_json_loads(s: str) -> list:
    import re
    # 1. Clean markdown JSON blocks
    s = re.sub(r'```json\s*', '', s, flags=re.IGNORECASE)
    s = re.sub(r'\s*```', '', s).strip()
    
    # 2. Try standard json.loads
    try:
        return json.loads(s)
    except Exception as e:
        logger.warning(f"Standard JSON parse failed: {e}. Attempting recovery...")
        
    # 3. Clean raw control characters and newlines inside strings
    def clean_string_match(m):
        content = m.group(1)
        # Escape any unescaped double quotes inside the string content
        content_escaped = re.sub(r'(?<!\\)"', r'\"', content)
        # Replace actual newlines and tabs with escaped versions
        content_escaped = content_escaped.replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')
        return f'"{content_escaped}"'
        
    # Match double quoted strings (handling escaped quotes inside)
    pattern = r'"((?:[^"\\]|\\.)*)"'
    fixed_s = re.sub(pattern, clean_string_match, s)
    
    # 4. Remove trailing commas before closing braces/brackets
    fixed_s = re.sub(r',\s*\]', ']', fixed_s)
    fixed_s = re.sub(r',\s*\}', '}', fixed_s)
    
    try:
        return json.loads(fixed_s)
    except Exception as e2:
        logger.warning(f"Recovery JSON parse failed: {e2}. Attempting manual bracket repair...")
        
    # 5. If it's still failing (e.g. truncated), try to find a prefix that forms a valid array
    try:
        last_obj_end = fixed_s.rfind('}')
        if last_obj_end != -1:
            truncated = fixed_s[:last_obj_end+1]
            if not truncated.endswith(']'):
                truncated += '\n]'
            return json.loads(truncated)
    except Exception as e3:
        logger.error(f"All JSON recovery attempts failed: {e3}")
        
    raise ValueError("Failed to parse LLM response as JSON")


# ── Create Job (called from dashboard) ───────────────────────────────────────
@router.post("/extension/create-job", tags=["Extension"])
async def create_extension_job(
    req: CreateJobReq,
    client: dict = Depends(_require_client),
    db: Session = Depends(get_db)
):
    global _jobs
    _jobs = _load_jobs()
    """
    Dashboard calls this to create a job.
    Returns job_id that user pastes into extension popup.
    """
    from app.core.models import SubtopicClassroom, TopicClassroom, ChapterClassroom, Subject, PaperClassroom, Exam
    from app.services.llm import generate_simple_response
    import re

    subtopic = db.query(SubtopicClassroom).join(TopicClassroom).join(ChapterClassroom).join(Subject).join(PaperClassroom).join(Exam).filter(
        SubtopicClassroom.subtopic_id == req.subtopic_id,
        Exam.client_id == client["client_id"]
    ).first()
    if not subtopic:
        raise HTTPException(404, "Subtopic not found")

    topic = subtopic.topic
    chapter = topic.chapter if topic else None
    subject = chapter.subject if chapter else None
    lang = req.language or "English"

    study_material = req.transcript.strip() if req.transcript and req.transcript.strip() else (
        subtopic.description or subtopic.notes or subtopic.name
    )
    # Clean markdown/html
    plain = re.sub(r'<[^>]+>', '', study_material)
    plain = re.sub(r'\[IMAGE:[^\]]+\]', '', plain)
    plain = re.sub(r'[#*`>_~]', '', plain).strip()[:3000]

    subject_name = subject.name if subject else "General"
    chapter_name = chapter.name if chapter else "General"
    topic_name = topic.name if topic else subtopic.name

    # Generate 12 scenes with image + animation prompts
    prompt = f"""You are a professional educational reel director.
Create exactly 12 scenes for a 1-minute educational reel about: "{subtopic.name}"
Subject: {subject_name} | Chapter: {chapter_name} | Topic: {topic_name}
Language for dialogue: {lang}

Study material:
{plain[:2000]}

Return a JSON array of exactly 12 scene objects:
[
  {{
    "scene_num": 1,
    "dialogue": "15-25 words narration in {lang}",
    "image_prompt": "Detailed photorealistic 9:16 portrait image description in English, cinematic, 4K, no text",
    "animation_prompt": "Camera movement description: slow zoom in / pan left / dolly forward etc."
  }}
]

Rules:
- dialogue: natural spoken {lang}, 15-25 words per scene.
  - CRITICAL: If the language is Hindi, you MUST write the dialogue strictly in proper Devanagari Unicode script (e.g. "भारत", "प्रौद्योगिकी"). NEVER write in Hinglish (Hindi written using English/Latin alphabet, e.g. "Bharat", "vigyan"), as TTS engines pronounce Hinglish with a highly robotic/incorrect accent.
  - CRITICAL: Spell out all numbers, place names, acronyms, and math symbols fully in spoken words of the target language (e.g. write "उन्नीस सौ सैंतालीस" instead of "1947", "प्रतिशत" / "percent" instead of "%", "किलोमीटर" instead of "km") so that ElevenLabs reads them with perfect professional pronunciation.
- image_prompt: detailed English description for AI image generation, always 9:16 portrait orientation
- animation_prompt: short camera movement instruction for video animation
- Make scenes flow as continuous educational explanation
Return ONLY the JSON array, no markdown."""

    try:
        raw = await generate_simple_response(prompt, "You are a professional video director. Return only valid JSON array.")
        scenes = robust_json_loads(raw)
        if not isinstance(scenes, list):
            raise ValueError("Not a list")
        # Ensure exactly 12
        scenes = scenes[:12]
    except Exception as e:
        logger.error(f"Scene generation failed: {e}")
        raise HTTPException(500, f"Failed to generate scenes: {str(e)}")

    job_id = "job-" + secrets.token_hex(8)
    _jobs[job_id] = {
        "job_id": job_id,
        "client_id": client["client_id"],
        "subtopic_id": req.subtopic_id,
        "subtopic_name": subtopic.name,
        "language": lang,
        "voice_id": req.voice_id,
        "scenes": scenes,
        "images_received": [],
        "videos_received": [],
        "status": "waiting_extension",
        "created_at": datetime.utcnow().isoformat(),
        "video_url": None
    }

    _save_jobs(_jobs)
    logger.info(f"Extension job created: {job_id} with {len(scenes)} scenes")
    return {
        "success": True,
        "job_id": job_id,
        "scene_count": len(scenes),
        "scenes": scenes
    }


# ── Get Job (called by extension) ─────────────────────────────────────────────
@router.get("/extension/job/{job_id}", tags=["Extension"])
async def get_job(job_id: str, client: dict = Depends(_require_client)):
    global _jobs
    _jobs = _load_jobs()
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job["client_id"] != client["client_id"]:
        raise HTTPException(403, "Access denied")
    return {"success": True, "scenes": job["scenes"], "status": job["status"]}


# ── Image Done (called by extension after each image download) ────────────────
@router.post("/extension/job/{job_id}/image-done", tags=["Extension"])
async def image_done(job_id: str, req: ImageDoneReq, client: dict = Depends(_require_client)):
    global _jobs
    _jobs = _load_jobs()
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if req.filename not in job["images_received"]:
        job["images_received"].append(req.filename)
    logger.info(f"Job {job_id}: image {len(job['images_received'])}/{len(job['scenes'])} done: {req.filename}")
    _save_jobs(_jobs)
    return {"success": True, "images_done": len(job["images_received"])}


# ── Start Videos (extension notifies backend it's starting video phase) ───────
@router.post("/extension/job/{job_id}/start-videos", tags=["Extension"])
async def start_videos(job_id: str, client: dict = Depends(_require_client)):
    global _jobs
    _jobs = _load_jobs()
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    job["status"] = "generating_videos"
    return {"success": True}


# ── Video Done (called by extension after each video download) ────────────────
@router.post("/extension/job/{job_id}/video-done", tags=["Extension"])
async def video_done(job_id: str, req: VideoDoneReq, client: dict = Depends(_require_client)):
    global _jobs
    _jobs = _load_jobs()
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if req.filename not in job["videos_received"]:
        job["videos_received"].append(req.filename)
    logger.info(f"Job {job_id}: video {len(job['videos_received'])}/{len(job['scenes'])} done: {req.filename}")
    _save_jobs(_jobs)
    return {"success": True, "videos_done": len(job["videos_received"])}


# ── Assemble Reel (called by extension when all videos are done) ──────────────
@router.post("/extension/job/{job_id}/assemble", tags=["Extension"])
async def assemble_reel(
    job_id: str,
    req: AssembleReq,
    client: dict = Depends(_require_client),
    db: Session = Depends(get_db)
):
    global _jobs
    _jobs = _load_jobs()
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")

    job["status"] = "assembling"
    _save_jobs(_jobs)
    
    base_uploads = os.path.join(os.getcwd(), "uploads", "social")
    work_dir = os.path.join(base_uploads, f"ext_work_{job_id[:8]}")
    os.makedirs(work_dir, exist_ok=True)

    import glob
    import shutil

    scenes = job["scenes"]
    num_scenes = len(scenes)
    
    video_files = []
    image_files = []

    # ── Resilient Local Helper to Discover Downloaded Files ──
    def resilient_find_file(filename: Optional[str], scene_num: int, job_id: str, is_video: bool = False) -> Optional[str]:
        import glob
        import os
        from pathlib import Path

        home = Path.home()
        downloads_dirs = [
            str(home / "Downloads"),
            str(home / "OneDrive" / "Downloads"),
            str(home / "OneDrive" / "Desktop"),
            str(home / "Desktop"),
            os.path.join(os.environ.get("USERPROFILE", ""), "Downloads"),
            os.path.join(os.environ.get("USERPROFILE", ""), "OneDrive", "Downloads")
        ]
        unique_dirs = []
        for d in downloads_dirs:
            if d:
                p = os.path.abspath(d)
                if os.path.exists(p) and p not in unique_dirs:
                    unique_dirs.append(p)

        logger.info(f"Resilient discovery: searching for scene {scene_num}, job {job_id}, filename {filename} (is_video={is_video}) in: {unique_dirs}")

        # A. Try exact filename match in any of the search folders
        if filename:
            for ddir in unique_dirs:
                full_path = os.path.join(ddir, filename)
                if os.path.exists(full_path):
                    size = os.path.getsize(full_path)
                    if (is_video and size > 1000) or (not is_video and size > 0):
                        logger.info(f"Resilient discovery: Found exact filename {filename} in {ddir} (size: {size} bytes)")
                        return full_path

        # B. Try pattern matching with job_id
        patterns = []
        for ddir in unique_dirs:
            if is_video:
                patterns.extend([
                    os.path.join(ddir, f"meta-vid-{scene_num}-{job_id}-*.mp4"),
                    os.path.join(ddir, f"meta-vid-{scene_num}-{job_id}*.mp4"),
                    os.path.join(ddir, f"meta-video-{scene_num}-{job_id}-*.mp4"),
                    os.path.join(ddir, f"meta-video-{scene_num}-{job_id}*.mp4"),
                    os.path.join(ddir, f"meta-vid-{scene_num}-{job_id}.mp4"),
                    os.path.join(ddir, f"meta-video-{scene_num}-{job_id}.mp4")
                ])
            else:
                patterns.extend([
                    os.path.join(ddir, f"meta-img-{scene_num}-{job_id}-*.jpg"),
                    os.path.join(ddir, f"meta-img-{scene_num}-{job_id}*.jpg"),
                    os.path.join(ddir, f"flow-image-{scene_num}-{job_id}-*.jpg"),
                    os.path.join(ddir, f"flow-image-{scene_num}-{job_id}*.jpg"),
                    os.path.join(ddir, f"meta-img-{scene_num}-{job_id}.jpg"),
                    os.path.join(ddir, f"flow-image-{scene_num}-{job_id}.jpg")
                ])

        matches = []
        for p in patterns:
            matches.extend(glob.glob(p))
        valid_matches = [m for m in matches if os.path.exists(m) and (os.path.getsize(m) > (1000 if is_video else 0))]
        if valid_matches:
            latest = max(valid_matches, key=os.path.getmtime)
            logger.info(f"Resilient discovery: Found pattern match with job_id: {latest}")
            return latest

        # C. Try index match/job_id independent fallbacks
        patterns_fallback = []
        for ddir in unique_dirs:
            if is_video:
                patterns_fallback.extend([
                    os.path.join(ddir, f"meta-vid-{scene_num}-*.mp4"),
                    os.path.join(ddir, f"meta-video-{scene_num}-*.mp4"),
                    os.path.join(ddir, f"meta-vid-*.mp4"),
                    os.path.join(ddir, f"meta-video-*.mp4")
                ])
            else:
                patterns_fallback.extend([
                    os.path.join(ddir, f"meta-img-{scene_num}-*.jpg"),
                    os.path.join(ddir, f"flow-image-{scene_num}-*.jpg"),
                    os.path.join(ddir, f"meta-img-*.jpg"),
                    os.path.join(ddir, f"flow-image-*.jpg")
                ])

        matches_fallback = []
        for p in patterns_fallback:
            matches_fallback.extend(glob.glob(p))
        valid_fallback = [m for m in matches_fallback if os.path.exists(m) and (os.path.getsize(m) > (1000 if is_video else 0))]
        if valid_fallback:
            # Check for scene_num anywhere in the filename basename
            precise_fallback = []
            for m in valid_fallback:
                base = os.path.basename(m).lower()
                if f"-{scene_num}-" in base or f"-{scene_num}." in base or f"image-{scene_num}-" in base or f"image-{scene_num}." in base or f"vid-{scene_num}-" in base or f"video-{scene_num}-" in base:
                    precise_fallback.append(m)
            if precise_fallback:
                latest = max(precise_fallback, key=os.path.getmtime)
                logger.info(f"Resilient discovery: Found precise fallback (scene {scene_num}): {latest}")
                return latest
            # Generic fallback (newest modified)
            latest = max(valid_fallback, key=os.path.getmtime)
            logger.info(f"Resilient discovery: Found general fallback: {latest}")
            return latest

        # D. Generic fallback: check any file with "meta" or "flow" in name
        general_matches = []
        for ddir in unique_dirs:
            if is_video:
                general_matches.extend(glob.glob(os.path.join(ddir, "*.mp4")))
            else:
                general_matches.extend(glob.glob(os.path.join(ddir, "*.jpg")) + glob.glob(os.path.join(ddir, "*.jpeg")))
        valid_general = []
        for m in general_matches:
            base = os.path.basename(m).lower()
            if ("meta" in base or "flow" in base) and os.path.exists(m):
                size = os.path.getsize(m)
                if (is_video and size > 1000) or (not is_video and size > 0):
                    valid_general.append(m)
        if valid_general:
            latest = max(valid_general, key=os.path.getmtime)
            logger.info(f"Resilient discovery: Found absolute general fallback: {latest}")
            return latest

        logger.warning(f"Resilient discovery: No file found for scene {scene_num}")
        return None

    # ── RESILIENT IMAGE DISCOVERY AND COPYING ──
    for i in range(num_scenes):
        scene_num = i + 1
        img_copied = False
        dest_img_path = os.path.join(work_dir, f"scene_{i}_orig_img.jpg")
        
        filename = req.images[i] if (req.images and i < len(req.images)) else None
        found_file = resilient_find_file(filename, scene_num, job_id, is_video=False)
        
        if found_file:
            try:
                shutil.copy2(found_file, dest_img_path)
                image_files.append(dest_img_path)
                img_copied = True
                logger.info(f"Copied discovered image {found_file} to {dest_img_path}")
            except Exception as e:
                logger.warning(f"Error copying image: {e}")
                
        # D. Dynamic AI Fallback (Generates custom image matching script instead of default boring unsplash image)
        if not img_copied:
            try:
                import urllib.parse
                import httpx
                prompt_text = scenes[i].get("image_prompt") or scenes[i].get("dialogue") or "abstract education concept"
                logger.info(f"Scene {i} image missing in Downloads. Dynamically generating visual matching script via Pollinations AI: {prompt_text}")
                encoded_prompt = urllib.parse.quote(f"{prompt_text}, 8k, cinematic lighting, masterpiece")
                fallback_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1080&height=1920&nologo=true&seed={secrets.token_hex(4)}&model=flux"
                
                with httpx.Client() as http_client:
                    res = http_client.get(fallback_url, timeout=30.0)
                    if res.status_code == 200:
                        with open(dest_img_path, "wb") as f:
                            f.write(res.content)
                        image_files.append(dest_img_path)
                        img_copied = True
                        logger.info(f"Successfully generated custom AI fallback image for scene {i} using Pollinations!")
            except Exception as e:
                logger.error(f"Dynamic AI fallback image generation failed: {e}")
            
            if not img_copied:
                # Absolute emergency 1x1 valid black JPEG write to prevent crash
                logger.warning(f"Extreme fallback: writing 1x1 valid black JPEG for scene {i}")
                with open(dest_img_path, "wb") as f:
                    f.write(b'\xff\xd8\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.\' ",#\x1c\x1c(7),01444\x1f\'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x15\x00\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x07\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9')
                image_files.append(dest_img_path)
                img_copied = True

    # ── RESILIENT VIDEO DISCOVERY AND COPYING ──
    for i in range(num_scenes):
        scene_num = i + 1
        vid_copied = False
        dest_vid_path = os.path.join(work_dir, f"scene_{i}_orig_vid.mp4")
        
        filename = req.videos[i] if (req.videos and i < len(req.videos)) else None
        found_file = resilient_find_file(filename, scene_num, job_id, is_video=True)
        
        if found_file:
            try:
                shutil.copy2(found_file, dest_vid_path)
                video_files.append(dest_vid_path)
                vid_copied = True
                logger.info(f"Copied discovered video {found_file} to {dest_vid_path}")
            except Exception as e:
                logger.warning(f"Error copying video: {e}")
                    
        # E. Final secure fallback: Generate high-quality cinematic Ken Burns video from scene image!
        if not vid_copied:
            dest_img_path = os.path.join(work_dir, f"scene_{i}_orig_img.jpg")
            if os.path.exists(dest_img_path) and os.path.getsize(dest_img_path) > 0:
                try:
                    logger.info(f"Scene {i} video missing or corrupt. Animating scene image into professional cinematic video: {dest_img_path}")
                    
                    # Alternating smooth Ken Burns zoom/pan (10s, 30fps = 300 frames)
                    zoom_val = "min(zoom+0.0015,1.5)" if i % 2 == 0 else "max(1.5-0.0015*on,1.0)"
                    pan_x = "iw/2-(iw/zoom/2)+on*0.3" if i % 3 == 0 else "iw/2-(iw/zoom/2)-on*0.3" if i % 3 == 1 else "iw/2-(iw/zoom/2)"
                    
                    # Smooth Ken Burns + pro color correction + cinematic vignette overlay
                    # STABLE SCALING TO PREVENT FFmpeg MEMORY OVERFLOWS / BLACK SCREEN: scale=1620:2880 with cropping
                    vf = (
                        f"scale=1620:2880:force_original_aspect_ratio=increase,crop=1620:2880,zoompan=z='{zoom_val}':x='{pan_x}':y='ih/2-(ih/zoom/2)':d=300:s=1080x1920:fps=30,setsar=1,"
                        f"eq=contrast=1.05:saturation=1.15,vignette=angle=0.10"
                    )
                    
                    cmd = [
                        "ffmpeg", "-y", "-nostdin",
                        "-loop", "1", "-i", dest_img_path,
                        "-vf", vf,
                        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                        "-t", "10.0",
                        "-pix_fmt", "yuv420p", "-r", "30",
                        dest_vid_path
                    ]
                    result = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL)
                    if result.returncode == 0:
                        video_files.append(dest_vid_path)
                        vid_copied = True
                        logger.info(f"Successfully generated animated fallback video from scene image for scene {i}")
                    else:
                        logger.error(f"FFmpeg fallback video generation failed: {result.stderr.decode('utf-8', errors='ignore')}")
                except Exception as ex:
                    logger.error(f"Failed to generate animated video from image for scene {i}: {ex}")
 
        # If it is STILL not copied, generate a solid-color aesthetic vertical clip to avoid empty files/crashes
        if not vid_copied:
            logger.warning(f"Extreme fallback: generating solid color 1080x1920 video for scene {i}")
            fallback_cmd = [
                "ffmpeg", "-y", "-nostdin",
                "-f", "lavfi", "-i", "color=c=0x1a1a2e:s=1080x1920:d=3.0",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
                dest_vid_path
            ]
            result = subprocess.run(fallback_cmd, capture_output=True, stdin=subprocess.DEVNULL)
            if result.returncode == 0:
                video_files.append(dest_vid_path)
                vid_copied = True
            else:
                with open(dest_vid_path, "wb") as f:
                    f.write(b"")
                video_files.append(dest_vid_path)

    logger.info(f"Assembling reel from {len(video_files)} copied video files in work directory")

    try:
        res_data = await assemble_from_videos(
            video_files=video_files,
            scenes=job["scenes"],
            language=job["language"],
            voice_id=job["voice_id"],
            job_id=job_id
        )
        video_url = res_data["video_url"]
        enriched_scenes = res_data["scenes"]
        
        # Save to database SocialContent table so it appears in history and lists!
        from app.core.models import SubtopicClassroom, TopicClassroom, ChapterClassroom, Subject, PaperClassroom, Exam, SocialContent
        
        subtopic_id = job.get("subtopic_id")
        subtopic = db.query(SubtopicClassroom).join(TopicClassroom).join(ChapterClassroom).join(Subject).join(PaperClassroom).join(Exam).filter(
            SubtopicClassroom.subtopic_id == subtopic_id,
            Exam.client_id == client["client_id"]
        ).first()
        
        subject_name = "General"
        exam_id = ""
        if subtopic and subtopic.topic and subtopic.topic.chapter and subtopic.topic.chapter.subject:
            subject_name = subtopic.topic.chapter.subject.name
            if subtopic.topic.chapter.subject.paper and subtopic.topic.chapter.subject.paper.exam:
                exam_id = subtopic.topic.chapter.subject.paper.exam.exam_id
                
        subtopic_name = subtopic.name if subtopic else job.get("subtopic_name", "Subtopic")
        title = f"{subtopic_name} — {subject_name}"
        
        # Compile formatted scenes narration script
        script_parts = []
        for s in job["scenes"]:
            script_parts.append(
                f"🎬 Scene {s.get('scene_num', 1)} (5 sec)\n"
                f"🎙️ Dialogue: {s.get('dialogue', '')}\n"
                f"📸 Visuals / Footage: {s.get('image_prompt', '')}\n"
                f"🎥 Editing Notes: {s.get('animation_prompt', '')}"
            )
        compiled_script = "\n\n".join(script_parts)
        
        # Create SocialContent record
        db_item = SocialContent(
            content_id=job_id,  # Use the job_id as the content_id for tracking
            client_id=client["client_id"],
            content_type="reel",
            title=title,
            body=compiled_script[:1000],  # Save script snippet in body
            media_url=video_url,
            scenes_json=json.dumps(enriched_scenes),  # SAVE THE ENRICHED SCENES PAYLOAD!
            metadata_json=json.dumps({
                "subtopic_id": subtopic_id,
                "exam_id": exam_id,
                "voice_id": job.get("voice_id"),
                "language": job.get("language"),
                "script": compiled_script,
                "job_id": job_id,
                "bgm_url": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3"
            })
        )
        db.add(db_item)
        db.commit()
        
        job["status"] = "done"
        job["video_url"] = video_url
        job["scenes"] = enriched_scenes
        _save_jobs(_jobs)
        
        logger.info(f"Extension reel successfully saved to classroom database history: {job_id}")
        return {"success": True, "video_url": video_url}
        
    except Exception as e:
        logger.error(f"Assembly failed: {e}")
        job["status"] = "error"
        _save_jobs(_jobs)
        raise HTTPException(500, f"Assembly failed: {str(e)}")


# ── Pending Job (extension polls this) ──────────────────────────────────────
@router.get("/extension/pending-job", tags=["Extension"])
async def get_pending_job(client: dict = Depends(_require_client)):
    """Extension polls this every 3 sec to auto-detect new jobs."""
    client_id = client.get("client_id")
    
    # Sort jobs by created_at in reverse (newest first) to always process the latest job first!
    sorted_jobs = sorted(
        _jobs.values(),
        key=lambda j: j.get("created_at", ""),
        reverse=True
    )
    
    for job in sorted_jobs:
        if job.get("client_id") == client_id and job.get("status") == "waiting_extension":
            logger.info(f"get_pending_job: client={client_id} -> returning job={job['job_id']}")
            return {"success": True, "job_id": job["job_id"], "status": "waiting_extension"}
            
    logger.info(f"get_pending_job: client={client_id} -> no pending jobs found")
    return {"success": True, "job_id": None, "status": "none"}


# ── Pickup (extension marks job as picked up) ────────────────────────────
@router.post("/extension/job/{job_id}/pickup", tags=["Extension"])
async def pickup_job(job_id: str, client: dict = Depends(_require_client)):
    global _jobs
    _jobs = _load_jobs()
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    job["status"] = "extension_running"
    _save_jobs(_jobs)
    return {"success": True}


# ── Error report ───────────────────────────────────────────────────────────────
class ErrorReq(BaseModel):
    error: str

@router.post("/extension/job/{job_id}/error", tags=["Extension"])
async def report_error(job_id: str, req: ErrorReq, client: dict = Depends(_require_client)):
    global _jobs
    _jobs = _load_jobs()
    job = _jobs.get(job_id)
    if job:
        job["status"] = "error"
        job["error"] = req.error
        _save_jobs(_jobs)
    return {"success": True}


# ── Job Status (polling) ──────────────────────────────────────────────────────
@router.get("/extension/job/{job_id}/status", tags=["Extension"])
async def job_status(job_id: str, client: dict = Depends(_require_client), db: Session = Depends(get_db)):
    global _jobs
    _jobs = _load_jobs()
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
        
    scenes_list = []
    metadata_info = {}
    if job["status"] == "done":
        from app.core.models import SocialContent
        db_item = db.query(SocialContent).filter(SocialContent.content_id == job_id).first()
        if db_item:
            scenes_list = db_item.scenes
            metadata_info = db_item.metadata_info
            
    return {
        "success": True,
        "status": job["status"],
        "images_done": len(job["images_received"]),
        "videos_done": len(job["videos_received"]),
        "total_scenes": len(job["scenes"]),
        "video_url": job.get("video_url"),
        "progress_msg": job.get("progress_msg", ""),
        "progress_pct": job.get("progress_pct", 75.0 if job["status"] == "assembling" else (100.0 if job["status"] == "done" else 0.0)),
        "scenes": scenes_list,
        "metadata": metadata_info
    }


def create_scene_subtitles(scenes: List[dict], scene_durations: List[float], work_dir: str) -> str:
    """Creates a premium CapCut-style .ass subtitle file with bold uppercase 3-word chunks and entry Pop-Scale animation."""
    sub_path = os.path.join(work_dir, "subs.ass")
    
    with open(sub_path, "w", encoding="utf-8") as f:
        f.write("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n")
        f.write("[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n")
        # CAPCUT STYLE: Impact font, size 85, Primary: Yellow (&H0000FFFF), Outline: Black (&H00000000) with thickness 5, Shadow 2, Alignment 2 (Bottom center)
        f.write("Style: Default,Impact,85,&H0000FFFF,&H0000FFFF,&H00000000,&H00000000,-1,0,0,0,100,100,1,0,1,5,2,2,30,30,350,1\n\n")
        f.write("[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
        
        def format_time(s):
            h = int(s // 3600)
            m = int((s % 3600) // 60)
            sec = s % 60
            return f"{h}:{m:02d}:{sec:05.2f}"
            
        current_time = 0.0
        for i, s in enumerate(scenes):
            dialogue = s.get("dialogue", "").strip()
            scene_dur = scene_durations[i]
            
            if dialogue:
                # Split dialogue into 3-word chunks (in uppercase)
                words = [w.strip().upper() for w in dialogue.split() if w.strip()]
                chunks = []
                chunk_size = 3
                for j in range(0, len(words), chunk_size):
                    chunks.append(" ".join(words[j:j+chunk_size]))
                
                if not chunks:
                    chunks = [dialogue.upper()]
                
                dur_per_chunk = scene_dur / len(chunks)
                for j, chunk_text in enumerate(chunks):
                    start_sec = current_time + (j * dur_per_chunk)
                    end_sec = current_time + ((j + 1) * dur_per_chunk)
                    
                    # Pop-scale transition animation
                    animated_text = f"{{\\fscx100\\fscy100\\t(0,100,\\fscx120\\fscy120)\\t(100,200,\\fscx100\\fscy100)}}{chunk_text}"
                    f.write(f"Dialogue: 0,{format_time(start_sec)},{format_time(end_sec)},Default,,0,0,0,,{animated_text}\n")
            
            current_time += scene_dur
            
    return sub_path

# ── Core Assembly Function ────────────────────────────────────────────────────
async def assemble_from_videos(
    video_files: List[str],
    scenes: List[dict],
    language: str,
    voice_id: Optional[str],
    job_id: str
) -> dict:
    from app.services.video_engine import generate_elevenlabs_voiceover, validate_video_asset, validate_audio_asset
    from gtts import gTTS
    import shutil

    def update_progress(msg: str, pct: Optional[float] = None):
        try:
            logger.info(f"Job {job_id} progress update: {msg} ({pct}%)")
            current_jobs = _load_jobs()
            if job_id in current_jobs:
                current_jobs[job_id]["progress_msg"] = msg
                if pct is not None:
                    current_jobs[job_id]["progress_pct"] = pct
                _save_jobs(current_jobs)
        except Exception as pe:
            logger.warning(f"Could not update progress: {pe}")

    base_uploads = os.path.join(os.getcwd(), "uploads", "social")
    work_dir = os.path.join(base_uploads, f"ext_work_{job_id[:8]}")
    os.makedirs(work_dir, exist_ok=True)

    lang_map = {"Hindi": "hi", "English": "en", "Spanish": "es", "French": "fr", "Bengali": "bn", "Marathi": "mr"}
    tts_lang = lang_map.get(language, "en")

    scene_audios = []
    scene_durations = []

    # 1. Generate segment-by-segment voiceover for each scene
    update_progress("Initializing premium ElevenLabs voice narration (scene by scene)...", 75.0)
    for i, s in enumerate(scenes):
        dialogue = s.get("dialogue", "").strip()
        scene_voice_path = os.path.join(work_dir, f"scene_{i}_voice.mp3")
        
        calc_pct = 75.0 + (i / len(scenes)) * 10.0
        update_progress(f"Generating premium AI narration voiceover for Scene {i + 1} of {len(scenes)}...", calc_pct)
        if dialogue:
            voice_result = await generate_elevenlabs_voiceover(dialogue, work_dir, voice_id=voice_id)
            if voice_result and os.path.exists(voice_result):
                if os.path.exists(scene_voice_path):
                    os.remove(scene_voice_path)
                os.rename(voice_result, scene_voice_path)
            else:
                gTTS(text=dialogue, lang=tts_lang).save(scene_voice_path)
                
            # PRO-LEVEL AUDIO TRANSITION: Trim leading and trailing silences to prevent disjointed "ruk-ruk kar" gaps
            trimmed_path = os.path.join(work_dir, f"scene_{i}_voice_trimmed.mp3")
            trim_cmd = [
                "ffmpeg", "-y", "-nostdin",
                "-i", scene_voice_path,
                "-af", "silenceremove=start_periods=1:start_threshold=-45dB:end_periods=1:end_threshold=-45dB",
                trimmed_path
            ]
            trim_res = subprocess.run(trim_cmd, capture_output=True, stdin=subprocess.DEVNULL)
            if trim_res.returncode == 0 and os.path.exists(trimmed_path) and os.path.getsize(trimmed_path) > 0:
                os.remove(scene_voice_path)
                os.rename(trimmed_path, scene_voice_path)
        else:
            # Create a 3-second silent audio segment if dialogue is empty
            subprocess.run([
                "ffmpeg", "-y", "-nostdin",
                "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                "-t", "3", "-c:a", "libmp3lame", scene_voice_path
            ], capture_output=True, stdin=subprocess.DEVNULL)
            
        # Probe scene audio duration
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", scene_voice_path],
            capture_output=True, text=True, stdin=subprocess.DEVNULL
        )
        scene_dur = float(probe.stdout.strip()) if probe.returncode == 0 and probe.stdout.strip() else 5.0
        
        # Asset Validation Layer for audio narration segment
        validated_voice_path = validate_audio_asset(scene_voice_path, scene_dur, work_dir, f"scene_{i}_voice")
        if os.path.exists(validated_voice_path) and validated_voice_path != scene_voice_path:
            try:
                shutil.copy2(validated_voice_path, scene_voice_path)
            except Exception as se:
                logger.warning(f"Failed to copy validated voice: {se}")

        scene_audios.append(scene_voice_path)
        scene_durations.append(scene_dur)

    total_audio_dur = sum(scene_durations)

    # 2. Process each video: scale, crop to 1080x1920, and trim to exact scene audio duration
    update_progress("Narration complete! Synchronizing and trimming scene video segments...", 85.0)
    processed_videos = []
    for i, vf in enumerate(video_files):
        out_path = os.path.join(work_dir, f"scene_{i}_proc.mp4")
        scene_dur = scene_durations[i]
        
        calc_pct = 85.0 + (i / len(video_files)) * 8.0
        update_progress(f"Trimming, cropping & panning scene video {i + 1} of {len(video_files)} to match audio...", calc_pct)
        
        # Determine Cinematic Transition (Flash, Focus Blur, Crossfade, Cut)
        trans_effect = 'fade'
        if i > 0:
            effects_pool = ['flash', 'blur', 'fade']
            trans_effect = effects_pool[i % len(effects_pool)]
            
        trans_filter = ""
        if trans_effect == "flash":
            trans_filter = ",eq=brightness='1.0+0.5*exp(-8*t)':contrast='1.0+0.3*exp(-8*t)'"
        elif trans_effect == "blur":
            trans_filter = ",boxblur=luma_radius='max(0,12-30*t)':luma_power=1"
        elif trans_effect == "fade":
            trans_filter = f",fade=t=in:st=0:d=0.3,fade=t=out:st={max(0, scene_dur-0.3):.2f}:d=0.3"
            
        # Color Enhancer Grader: High contrast, vibrancy saturation, unsharp sharpening filter, and subtle vignette
        enhancement = ",eq=contrast=1.08:saturation=1.18,unsharp=3:3:0.5:3:3:0.5,vignette=angle=0.10"
        
        cmd = [
            "ffmpeg", "-y", "-nostdin",
            "-stream_loop", "-1",
            "-i", vf,
            "-vf", f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1{trans_filter}{enhancement}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-an",  # remove original audio
            "-t", str(scene_dur),
            "-pix_fmt", "yuv420p", "-r", "30",
            out_path
        ]
        result = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL)
        
        # Asset Validation Layer for processed scene video
        validated_v_path = validate_video_asset(out_path, scene_dur, work_dir, f"scene_{i}_ext")
        processed_videos.append(validated_v_path)

    # 3. Concatenate all processed videos together
    update_progress("Visual segments synced! Merging scenes into a seamless timeline...", 93.0)
    list_path = os.path.join(work_dir, "concat_list.txt")
    with open(list_path, "w") as f:
        for v in processed_videos:
            f.write(f"file '{v.replace(chr(92), '/')}'\n")

    temp_video = os.path.join(work_dir, "temp_video.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-nostdin", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", temp_video],
        capture_output=True, stdin=subprocess.DEVNULL
    )

    # 4. Concatenate all scene voiceovers together
    update_progress("Merging narration segment audio tracks...", 95.0)
    audio_list_path = os.path.join(work_dir, "audio_concat_list.txt")
    with open(audio_list_path, "w") as f:
        for a in scene_audios:
            f.write(f"file '{a.replace(chr(92), '/')}'\n")
            
    full_voice_path = os.path.join(work_dir, "full_voice.mp3")
    subprocess.run(
        ["ffmpeg", "-y", "-nostdin", "-f", "concat", "-safe", "0", "-i", audio_list_path, "-c", "copy", full_voice_path],
        capture_output=True, stdin=subprocess.DEVNULL
    )

    # 5. BGM
    bgm_url = "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3"
    bgm_path = os.path.join(work_dir, "bgm.mp3")
    try:
        import httpx
        async with httpx.AsyncClient() as http_client:
            r = await http_client.get(bgm_url, timeout=30.0, follow_redirects=True)
            if r.status_code == 200:
                with open(bgm_path, "wb") as f: f.write(r.content)
            else:
                bgm_path = None
    except:
        bgm_path = None

    # 6. Generate precise synchronized subtitles
    update_progress("Generating precise synchronized subtitles...", 97.0)
    sub_path = create_scene_subtitles(scenes, scene_durations, work_dir)
    safe_sub = sub_path.replace("\\", "/").replace(":", "\\:")

    # 7. Final assembly (Video + Concatenated Voiceover + BGM + Subtitles)
    update_progress("Merging visuals, voiceovers, BGM, and neon yellow subtitles into final reel...", 98.0)
    reel_id = secrets.token_hex(6)
    output_filename = f"ext_reel_{reel_id}.mp4"
    output_path = os.path.join(base_uploads, output_filename)

    inputs = ["-i", temp_video, "-i", full_voice_path]
    if bgm_path and os.path.exists(bgm_path):
        inputs += ["-i", bgm_path]
        fc = (
            f"[0:v]ass='{safe_sub}'[v];"
            f"[1:a]volume=1.8[av];"
            f"[2:a]volume=0.07,atrim=0:{total_audio_dur:.2f},asetpts=PTS-STARTPTS[abg];"
            f"[av][abg]amix=inputs=2:duration=first[a]"
        )
        maps = ["-map", "[v]", "-map", "[a]"]
    else:
        fc = f"[0:v]ass='{safe_sub}'[v];[1:a]volume=1.8[a]"
        maps = ["-map", "[v]", "-map", "[a]"]

    final_cmd = (
        ["ffmpeg", "-y", "-nostdin"] + inputs +
        ["-filter_complex", fc] + maps +
        ["-c:v", "libx264", "-preset", "fast", "-crf", "20",
         "-c:a", "aac", "-b:a", "192k",
         "-pix_fmt", "yuv420p", "-r", "30",
         "-t", str(total_audio_dur),
         output_path]
    )

    result = subprocess.run(final_cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if result.returncode != 0:
        raise Exception(f"FFmpeg failed: {result.stderr[-300:]}")

    # Build the enriched scenes list matching storyboard schema
    enriched_scenes = []
    accumulated_time = 0.0
    for i, s in enumerate(scenes):
        anim_prompt = s.get("animation_prompt", "").lower()
        effect = "zoom_in"
        if "out" in anim_prompt or "dolly out" in anim_prompt:
            effect = "zoom_out"
            
        trans_effect = 'fade'
        if i > 0:
            effects_pool = ['flash', 'blur', 'fade']
            trans_effect = effects_pool[i % len(effects_pool)]
            
        scene_dur = scene_durations[i]
        start_time = accumulated_time
        end_time = accumulated_time + scene_dur
        accumulated_time += scene_dur
        
        enriched_scenes.append({
            "id": 1000 + i,
            "scene_id": 1000 + i,
            "start": round(start_time, 2),
            "end": round(end_time, 2),
            "duration": round(scene_dur, 2),
            "video": f"/uploads/social/ext_work_{job_id[:8]}/scene_{i}_proc.mp4",
            "videoThumb": f"/uploads/social/ext_work_{job_id[:8]}/scene_{i}_proc.mp4",
            "audio": f"/uploads/social/ext_work_{job_id[:8]}/scene_{i}_voice.mp3",
            "thumb": f"/uploads/social/ext_work_{job_id[:8]}/scene_{i}_orig_img.jpg",
            "image": f"/uploads/social/ext_work_{job_id[:8]}/scene_{i}_orig_img.jpg",
            "script": s.get("dialogue", ""),
            "transition": trans_effect,
            "motion": effect,
            "voice": voice_id or "adam"
        })

    return {
        "video_url": f"/uploads/social/{output_filename}",
        "scenes": enriched_scenes
    }

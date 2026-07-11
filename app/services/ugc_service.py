import os
import re
import cv2
import json
import numpy as np
import httpx
import shutil
import secrets
import asyncio
import wave
import math
import logging
import subprocess
from datetime import datetime
from typing import List, Dict, Optional
from urllib.parse import quote
from PIL import Image, ImageDraw, ImageFont

from app.core.config import settings
from app.core.database import get_session_local
from app.core.models import UgcJob
from app.services.llm import generate_simple_response

logger = logging.getLogger(__name__)

# Default Royalty Free Audio Links & Mood mapping
MOOD_MUSIC_URLS = {
    "Motivational": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-9.mp3",
    "Sad": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3",
    "Funny": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-6.mp3",
    "Corporate": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-3.mp3",
    "News": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-8.mp3",
    "Podcast": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-5.mp3",
    "Lo-Fi Chill": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-4.mp3",
    "Energetic Hype": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-2.mp3",
    "Cinematic Epic": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-7.mp3",
    "Dark Mystery": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-13.mp3",
    "Ambient Meditation": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-10.mp3",
    "Hip Hop Beat": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-11.mp3",
    "Acoustic Folk": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-12.mp3"
}

def update_job_status(job_id: str, status: str, progress: int, error_msg: Optional[str] = None, transcript_json: Optional[str] = None, result_paths: Optional[dict] = None):
    """Updates UgcJob status in the database."""
    SessionLocal = get_session_local()
    db = SessionLocal()
    try:
        job = db.query(UgcJob).filter(UgcJob.job_id == job_id).first()
        if job:
            job.status = status
            job.progress = progress
            if error_msg is not None:
                job.error_message = error_msg
            if transcript_json is not None:
                job.transcript_json = transcript_json
            if result_paths:
                if "video" in result_paths:
                    job.result_video_path = result_paths["video"]
                if "thumbnail" in result_paths:
                    job.result_thumbnail_path = result_paths["thumbnail"]
                if "viral" in result_paths:
                    job.viral_video_path = result_paths["viral"]
            db.commit()
    except Exception as e:
        logger.error(f"Error updating job {job_id} in DB: {e}")
        db.rollback()
    finally:
        db.close()


def synthesize_sfx_bytes(sfx_type: str) -> bytes:
    """Synthesizes simple sound effect wave bytes to avoid network dependency."""
    sample_rate = 22050
    duration = 0.3 if sfx_type != "whoosh" else 0.4
    num_samples = int(sample_rate * duration)
    data = np.zeros(num_samples, dtype=np.int16)

    if sfx_type == "pop":
        # Frequency sweep from 400Hz to 1200Hz, fading out quickly
        for i in range(num_samples):
            t = i / sample_rate
            freq = 400 + (1200 - 400) * (i / num_samples)
            vol = math.exp(-15 * t)  # rapid decay
            val = math.sin(2 * math.pi * freq * t) * vol
            data[i] = int(val * 32767)
    elif sfx_type == "swipe":
        # Quick white noise sweep
        for i in range(num_samples):
            t = i / sample_rate
            noise = np.random.uniform(-1.0, 1.0)
            vol = math.sin(math.pi * (i / num_samples))  # bell shape
            data[i] = int(noise * vol * 0.5 * 32767)
    elif sfx_type == "whoosh":
        # Smooth pitch/noise sweeping upwards then downwards
        for i in range(num_samples):
            t = i / sample_rate
            noise = np.random.uniform(-1.0, 1.0)
            # Lowpass style envelope
            vol = math.exp(-8 * ((t - duration/2) ** 2))
            data[i] = int(noise * vol * 0.6 * 32767)
    else:
        # Simple beep
        for i in range(num_samples):
            t = i / sample_rate
            val = math.sin(2 * math.pi * 440 * t) * 0.3
            data[i] = int(val * 32767)

    import io
    wav_io = io.BytesIO()
    with wave.open(wav_io, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(data.tobytes())
    return wav_io.getvalue()


def get_or_create_sfx(work_dir: str) -> Dict[str, str]:
    """Ensures Pop, Swipe, and Whoosh sound effect files are present locally."""
    sfx_paths = {}
    for sfx in ["pop", "swipe", "whoosh"]:
        path = os.path.join(work_dir, f"sfx_{sfx}.wav")
        if not os.path.exists(path):
            try:
                wav_bytes = synthesize_sfx_bytes(sfx)
                with open(path, "wb") as f:
                    f.write(wav_bytes)
            except Exception as e:
                logger.error(f"Failed to synthesize {sfx}: {e}")
        sfx_paths[sfx] = path
    return sfx_paths


async def download_file_async(url: str, dest_path: str):
    """Downloads a file asynchronously."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, timeout=60.0)
        if resp.status_code == 200:
            with open(dest_path, "wb") as f:
                f.write(resp.content)
            return True
    return False


async def run_ugc_pipeline(job_id: str, client_id: str, video_path: str, filename: str, features: Dict):
    """Runs the full end-to-end 11-stage UGC video pipeline in the background."""
    logger.info(f"Starting UGC Pipeline for job {job_id}")
    work_dir = os.path.join(settings.BASE_DIR, "uploads", "ugc", job_id)
    os.makedirs(work_dir, exist_ok=True)

    try:
        # ── STAGE 1: Extract Audio ──
        update_job_status(job_id, "processing", 10)
        audio_path = os.path.join(work_dir, "extracted_audio.mp3")
        extract_cmd = ["ffmpeg", "-y", "-i", video_path, "-vn", "-acodec", "libmp3lame", "-ar", "16000", audio_path]
        proc = subprocess.run(extract_cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise Exception(f"FFmpeg audio extraction failed: {proc.stderr}")

        # ── STAGE 2: Speech Recognition (Whisper) ──
        update_job_status(job_id, "transcribing", 20)
        import whisper
        # Load local Whisper model (base)
        model = whisper.load_model("base")
        trans_res = model.transcribe(
            audio_path,
            fp16=False,
            word_timestamps=True,
            initial_prompt="Hindi voice to be written in Devanagari script. English voice to be written in English script. Mixed English and Hindi Hinglish transcription. Do not translate Hindi spoken words to English."
        )

        segments = trans_res.get("segments", [])
        if not segments:
            raise Exception("Whisper transcription yielded no speech segments. Please check video audio.")

        # Structure transcript segments and words
        full_transcript = []
        all_words = []
        for s in segments:
            seg_words = []
            for w in s.get("words", []):
                word_clean = w["word"].strip()
                if word_clean:
                    all_words.append({
                        "word": word_clean,
                        "start": float(w["start"]),
                        "end": float(w["end"])
                    })
                    seg_words.append({
                        "word": word_clean,
                        "start": float(w["start"]),
                        "end": float(w["end"])
                    })
            full_transcript.append({
                "start": float(s["start"]),
                "end": float(s["end"]),
                "text": s["text"].strip(),
                "words": seg_words
            })

        # Save transcript JSON to DB
        transcript_json = json.dumps(full_transcript)

        # ── STAGE 3: LLM Analysis ──
        update_job_status(job_id, "processing", 35, transcript_json=transcript_json)
        
        # Build rich transcript snippet with word-level timestamps for B-roll precision
        clean_text_lines = []
        for s in full_transcript[:80]:
            line_str = f"[{round(s['start'], 2)}s-{round(s['end'], 2)}s] {s['text']}"
            # Append first 6 word timestamps inline for precise B-roll matching
            word_timestamps = " | ".join(
                f"{w['word'].strip()}@{round(w['start'],1)}s"
                for w in s.get('words', [])[:6]
            )
            if word_timestamps:
                line_str += f"  ({word_timestamps})"
            clean_text_lines.append(line_str)
        transcript_snippet = "\n".join(clean_text_lines)

        system_prompt = (
            "You are a world-class video editor and creative director. "
            "Analyze the transcript with precise word timestamps and suggest detailed editing actions. "
            "For B-roll prompts, you MUST identify the EXACT spoken words and create hyper-specific, "
            "visually stunning Flux AI image prompts that directly visualize what is being talked about. "
            "Output strictly a JSON object matching the requested schema with no extra text."
        )
        user_prompt = f"""
Transcript with word timestamps:
{transcript_snippet}

Analyze the transcript and provide PRECISION editing suggestions:

1. MOOD: Identify the emotional tone (Motivational, Sad, Funny, Corporate, News, Podcast).

2. ZOOMS: Identify 2-4 high-impact moments for camera zoom. Pick the most emotional or keyword-heavy words.

3. B-ROLLS (CRITICAL): Suggest exactly 2-3 B-roll overlays. For each:
   - Pick the exact timestamp when an important concept/keyword is spoken.
   - Create a HYPER-SPECIFIC Flux AI visual prompt that DIRECTLY illustrates what is being said at that moment.
   - The prompt MUST be cinematic, photorealistic, and ultra-detailed (not generic).
   - Example: if speaker says 'I grew my business from zero' at 5.2s → prompt: 'cinematic close-up of a glowing holographic bar chart rising from flat to peak inside a dark minimalist office, warm amber accent lighting, shallow depth of field, 85mm lens, 8k photorealistic, masterpiece'
   - Example: if speaker says 'meditation changed my life' at 8.1s → prompt: 'serene aerial drone shot of a lone person sitting cross-legged on a mountain peak above clouds at golden hour, ultra wide, cinematic color grading, masterpiece, 8k'
   - DO NOT use generic prompts like 'person working' or 'business meeting'. Be SPECIFIC.

4. VIRAL MOMENT: The single most engaging 10-15 second clip.

Output ONLY raw JSON (no markdown fences, no extra text):
{{
  "mood": "Motivational",
  "zooms": [
    {{"start": 1.5, "end": 4.0, "reason": "emphasis on keyword"}}
  ],
  "brolls": [
    {{"start": 5.2, "end": 9.0, "keyword": "exact word spoken", "prompt": "hyper-specific cinematic Flux AI scene prompt here"}}
  ],
  "viral_moment": {{"start": 2.0, "end": 14.5}}
}}
"""
        llm_response = await generate_simple_response(user_prompt, system_prompt)
        # Parse JSON from response
        try:
            # Clean possible markdown wrap
            cleaned_resp = re.sub(r'^```json\s*|\s*```$', '', llm_response.strip(), flags=re.MULTILINE)
            edit_plan = json.loads(cleaned_resp)
        except Exception as e:
            logger.warning(f"LLM json parsing failed, using default edit plan. Response was: {llm_response}. Error: {e}")
            
            # Fallback edit plan with default spaced B-rolls
            total_duration = float(segments[-1]["end"]) if segments else 15.0
            fallback_brolls = []
            t = 2.0
            idx = 0
            while t + 3.0 <= total_duration:
                fallback_brolls.append({
                    "start": t,
                    "end": t + 3.0,
                    "keyword": "scene",
                    "prompt": f"aesthetic cinematic scene for segment {idx + 1}"
                })
                t += 6.0
                idx += 1

            edit_plan = {
                "mood": "Corporate",
                "zooms": [],
                "brolls": fallback_brolls,
                "viral_moment": {"start": 0.0, "end": min(12.0, total_duration)}
            }

        # ── Clamp B-roll durations to 2–4 seconds ──
        for br in edit_plan.get("brolls", []):
            start = float(br.get("start", 0))
            end   = float(br.get("end",   start + 3.0))
            dur   = end - start
            if dur < 2.0:
                br["end"] = start + 2.0
            elif dur > 4.0:
                br["end"] = start + 4.0

        # ── STAGE 4: Generate B-roll Assets (Flux AI or Meta AI pre-uploaded) ──
        broll_assets = []
        broll_source = features.get("broll_source", "pollinations")
        
        if features.get("broll") and edit_plan.get("brolls"):
            update_job_status(job_id, "processing", 45)
            
            if broll_source == "meta_ai":
                # Load pre-generated images uploaded by the frontend via Meta AI extension
                logger.info("B-roll source: Meta AI Extension (using pre-uploaded images)")
                
                # Read job metadata to get pre-uploaded paths
                SessionLocal = get_session_local()
                db_temp = SessionLocal()
                try:
                    job_record = db_temp.query(UgcJob).filter(UgcJob.job_id == job_id).first()
                    meta_json = job_record.metadata_json if job_record else "{}"
                    try:
                        meta_data = json.loads(meta_json)
                    except Exception:
                        meta_data = {}
                    meta_brolls = meta_data.get("meta_brolls", [])
                finally:
                    db_temp.close()
                
                # Map pre-uploaded Meta AI images to broll_assets
                meta_brolls_by_index = {mb["index"]: mb for mb in meta_brolls}
                for i, br in enumerate(edit_plan["brolls"]):
                    if i in meta_brolls_by_index:
                        mb = meta_brolls_by_index[i]
                        img_path = mb["path"]
                        if os.path.exists(img_path):
                            broll_assets.append({
                                "start": float(br["start"]),
                                "end": float(br["end"]),
                                "path": img_path
                            })
                            logger.info(f"Using Meta AI B-roll #{i}: {img_path}")
                        else:
                            logger.warning(f"Meta AI B-roll #{i} path not found: {img_path}.")
                    else:
                        logger.warning(f"No Meta AI image uploaded for B-roll #{i}.")

            elif broll_source == "pexels":
                # ── Pexels Stock Video B-roll ──────────────────────────────────────
                logger.info("B-roll source: Pexels Stock Videos")
                pexels_api_key = settings.PEXELS_API_KEY

                for i, br in enumerate(edit_plan["brolls"]):
                    prompt = br["prompt"]
                    # Extract clean keywords from the cinematic prompt for Pexels search
                    search_words = re.sub(r'[^a-zA-Z\s]', '', prompt).split()[:6]
                    search_query = " ".join(search_words)
                    logger.info(f"Searching Pexels Videos for B-roll #{i}: '{search_query}'")

                    vid_path = os.path.join(work_dir, f"broll_{i}.mp4")
                    pexels_success = False

                    try:
                        async with httpx.AsyncClient() as client:
                            # Search Pexels Videos API (portrait orientation)
                            pexels_res = await client.get(
                                "https://api.pexels.com/videos/search",
                                params={"query": search_query, "per_page": 5, "orientation": "portrait"},
                                headers={"Authorization": pexels_api_key},
                                timeout=30.0
                            )
                            if pexels_res.status_code == 200:
                                pexels_data = pexels_res.json()
                                videos = pexels_data.get("videos", [])
                                if videos:
                                    video = videos[0]
                                    # Pick best quality vertical video file
                                    video_files = video.get("video_files", [])
                                    # Prefer HD portrait files
                                    portrait_files = [
                                        vf for vf in video_files
                                        if vf.get("height", 0) > vf.get("width", 0)
                                    ]
                                    chosen = portrait_files[0] if portrait_files else (video_files[0] if video_files else None)
                                    if chosen:
                                        vid_url = chosen.get("link", "")
                                        if vid_url:
                                            dl_res = await client.get(vid_url, timeout=120.0)
                                            if dl_res.status_code == 200:
                                                with open(vid_path, "wb") as f:
                                                    f.write(dl_res.content)
                                                broll_assets.append({
                                                    "start": float(br["start"]),
                                                    "end": float(br["end"]),
                                                    "path": vid_path
                                                })
                                                pexels_success = True
                                                logger.info(f"Pexels Video B-roll #{i} downloaded: {video.get('url', '')}")
                            else:
                                logger.warning(f"Pexels Videos API returned {pexels_res.status_code} for query '{search_query}'")
                    except Exception as px_err:
                        logger.error(f"Pexels Video B-roll #{i} failed: {px_err}")

                    if not pexels_success:
                        logger.warning(f"Pexels Video B-roll #{i} not found, skipping.")

            else:
                # Default: generate B-roll images via Pollinations AI (Flux)
                logger.info("B-roll source: Pollinations AI (Flux)")
                for i, br in enumerate(edit_plan["brolls"]):
                    base_prompt = br["prompt"]
                    keyword = br.get("keyword", "")
                    # Enhance the prompt with cinematic quality suffix
                    # If keyword present, prepend it for stronger semantic grounding
                    if keyword:
                        enhanced_prompt = f"{base_prompt}, subject: {keyword}, photorealistic, 8k, cinematic color grading, masterpiece, ultra-detailed"
                    else:
                        enhanced_prompt = f"{base_prompt}, photorealistic, 8k, cinematic lighting, masterpiece, ultra-detailed"
                    encoded_prompt = quote(enhanced_prompt)
                    img_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1080&height=1920&nologo=true&model=flux&seed={secrets.token_hex(4)}"
                    img_path = os.path.join(work_dir, f"broll_{i}.jpg")
                    logger.info(f"Generating B-roll image for: {enhanced_prompt}")
                    try:
                        success = await download_file_async(img_url, img_path)
                        if success:
                            broll_assets.append({
                                "start": float(br["start"]),
                                "end": float(br["end"]),
                                "path": img_path,
                                "prompt": enhanced_prompt,
                                "keyword": keyword,
                                "index": i
                            })
                    except Exception as e:
                        logger.error(f"Error fetching B-roll image: {e}")



        # Save B-roll metadata to job for editor access
        if broll_assets:
            try:
                existing_meta = json.loads(job.metadata_json) if job.metadata_json else {}
                existing_meta["broll_assets"] = [
                    {
                        "index": ba.get("index", i),
                        "start": ba["start"],
                        "end": ba["end"],
                        "path": ba.get("path", ""),
                        "prompt": ba.get("prompt", ""),
                        "keyword": ba.get("keyword", "")
                    }
                    for i, ba in enumerate(broll_assets)
                ]
                existing_meta["edit_plan"] = {
                    "mood": edit_plan.get("mood", "Corporate"),
                    "viral_moment": edit_plan.get("viral_moment", {})
                }
                job.metadata_json = json.dumps(existing_meta)
                db.commit()
            except Exception as meta_err:
                logger.warning(f"Could not save broll metadata: {meta_err}")

        # ── STAGE 5: Background Green Screen Image ──
        bg_image_path = None
        if features.get("background"):
            bg_style = features.get("background_style") or "premium_abstract_dark_studio_background_neon_lights_orange_vertical_aesthetic"
            bg_url = f"https://image.pollinations.ai/prompt/{quote(bg_style)}?width=1080&height=1920&nologo=true&model=flux"
            bg_image_path = os.path.join(work_dir, "chroma_bg.jpg")
            try:
                await download_file_async(bg_url, bg_image_path)
            except Exception as e:
                logger.error(f"Error fetching background image: {e}")
                bg_image_path = None

        # ── STAGE 6: Local SFX setup ──
        sfx_files = get_or_create_sfx(work_dir)

        # ── STAGE 7: OpenCV Video Processing (Face Tracking, Re-framing, Zoom, Green Screen, B-rolls) ──
        update_job_status(job_id, "processing", 60)
        
        # Open source video
        cap = cv2.VideoCapture(video_path)
        orig_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        orig_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        orig_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        crop_h = orig_h
        crop_w = int(orig_h * 9 / 16)
        if crop_w > orig_w:
            crop_w = orig_w
            crop_h = int(orig_w * 16 / 9)

        # Load Face Cascade (with robust download/fallback for Linux EC2 servers)
        cascade_filename = "haarcascade_frontalface_default.xml"
        cascade_path = None

        if hasattr(cv2, 'data') and hasattr(cv2.data, 'haarcascades'):
            test_path = os.path.join(cv2.data.haarcascades, cascade_filename)
            if os.path.exists(test_path):
                cascade_path = test_path

        if not cascade_path:
            local_path = os.path.join(str(settings.BASE_DIR), cascade_filename)
            if not os.path.exists(local_path):
                logger.info("Haarcascade XML not found. Downloading from OpenCV GitHub...")
                try:
                    import urllib.request
                    url = "https://raw.githubusercontent.com/opencv/opencv/master/data/haarcascades/haarcascade_frontalface_default.xml"
                    urllib.request.urlretrieve(url, local_path)
                    logger.info(f"Downloaded haarcascade XML to: {local_path}")
                except Exception as dl_err:
                    logger.error(f"Failed to download haarcascade XML: {dl_err}")
            if os.path.exists(local_path):
                cascade_path = local_path
        if not cascade_path:
            raise Exception("Haarcascade XML classifier could not be located or downloaded.")

        face_cascade = cv2.CascadeClassifier(cascade_path)
        if face_cascade.empty():
            raise Exception("Loaded face cascade classifier is empty. XML parsing failed.")

        # Determine target resolution for scaling/upscaling
        quality = features.get("video_quality") or "1080p"
        quality_map = {
            "720p": (720, 1280),
            "1080p": (1080, 1920),
            "1080": (1080, 1920),
            "2k": (1440, 2560),
            "4k": (2160, 3840),
            "8k": (4320, 7680)
        }
        target_w, target_h = quality_map.get(str(quality).lower(), (1080, 1920))

        # Reframed output path
        reframed_video_path = os.path.join(work_dir, "reframed_silent.mp4")
        reframed_clean_path = os.path.join(work_dir, "reframed_clean.mp4")
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        out_video = cv2.VideoWriter(reframed_video_path, fourcc, orig_fps, (target_w, target_h))
        out_video_clean = cv2.VideoWriter(reframed_clean_path, fourcc, orig_fps, (target_w, target_h))

        # Face tracking state variables
        last_center_x = orig_w // 2
        ema_alpha = 0.12  # Smooth panning filter
        face_rect = None

        # Scene detection state variables
        last_hist = None
        detected_scene_cuts = []  # List of timestamps

        # Prep background green screen if needed
        bg_img = None
        if bg_image_path and os.path.exists(bg_image_path):
            bg_img = cv2.imread(bg_image_path)
            if bg_img is not None:
                bg_img = cv2.resize(bg_img, (crop_w, crop_h))

        # Initialize MediaPipe Selfie Segmentation for background removal / dress color shift
        mp_segmenter = None
        if features.get("background") or features.get("dress_color_shift"):
            try:
                import mediapipe as mp
                mp_segmenter = mp.solutions.selfie_segmentation.SelfieSegmentation(model_selection=0)
                logger.info("MediaPipe Selfie Segmentation initialized successfully.")
            except Exception as mp_err:
                logger.error(f"Failed to import/init mediapipe: {mp_err}")

        # Initialize B-roll video player state variables
        broll_cap = None
        active_broll_cap_path = None

        # ── Load Brand Logo (if enabled) ──
        logo_img_rgba = None
        if features.get("logo"):
            try:
                SessionLocal_logo = get_session_local()
                db_logo = SessionLocal_logo()
                try:
                    job_rec = db_logo.query(UgcJob).filter(UgcJob.job_id == job_id).first()
                    meta_logo = json.loads(job_rec.metadata_json) if job_rec and job_rec.metadata_json else {}
                finally:
                    db_logo.close()
                logo_path_val = meta_logo.get("logo_path", "")
                if logo_path_val and os.path.exists(logo_path_val):
                    # Read with alpha if PNG, else force RGBA
                    logo_raw = cv2.imread(logo_path_val, cv2.IMREAD_UNCHANGED)
                    if logo_raw is not None:
                        if logo_raw.shape[2] == 3:  # No alpha channel
                            logo_raw = cv2.cvtColor(logo_raw, cv2.COLOR_BGR2BGRA)
                        logo_img_rgba = logo_raw
                        logger.info(f"Brand logo loaded: {logo_path_val} ({logo_raw.shape})")
                    else:
                        logger.warning(f"Could not read logo file: {logo_path_val}")
                else:
                    logger.warning("Logo enabled but no logo_path found in metadata.")
            except Exception as logo_load_err:
                logger.error(f"Failed to load brand logo: {logo_load_err}")

        # Setup keep intervals for Silence / Jump Cut removal
        # Compute segments to remove
        intervals_to_cut = []
        if features.get("silence"):
            # Whisper segment gaps > 2s
            last_end = 0.0
            for seg in full_transcript:
                gap = seg["start"] - last_end
                if gap > 2.0:
                    intervals_to_cut.append((last_end + 0.3, seg["start"] - 0.3))
                last_end = seg["end"]
            
        if features.get("jumpcut"):
            # Detect filler segments
            fillers = ["aa", "hmm", "umm", "uh", "ah", "like", "so"]
            for s in full_transcript:
                clean_text = re.sub(r'[^\w\s]', '', s["text"].lower()).strip()
                if clean_text in fillers:
                    intervals_to_cut.append((s["start"], s["end"]))

        # Merge overlapping cut intervals
        intervals_to_cut.sort(key=lambda x: x[0])
        merged_cuts = []
        for cut in intervals_to_cut:
            if not merged_cuts:
                merged_cuts.append(cut)
            else:
                last_cut = merged_cuts[-1]
                if cut[0] <= last_cut[1]:  # overlap
                    merged_cuts[-1] = (last_cut[0], max(last_cut[1], cut[1]))
                else:
                    merged_cuts.append(cut)

        # Invert cuts to get keep intervals
        video_dur = total_frames / orig_fps
        keep_intervals = []
        curr = 0.0
        for cut in merged_cuts:
            if cut[0] > curr:
                keep_intervals.append((curr, cut[0]))
            curr = cut[1]
        if curr < video_dur:
            keep_intervals.append((curr, video_dur))

        # If no cuts made or filters disabled, keep entire video
        if not keep_intervals:
            keep_intervals = [(0.0, video_dur)]

        frame_idx = 0
        written_frames = 0
        
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break

            frame_time = frame_idx / orig_fps
            frame_idx += 1

            # Check if this frame should be cut (Silence/Jumpcut)
            should_keep = False
            for start, end in keep_intervals:
                if start <= frame_time <= end:
                    should_keep = True
                    break
            if not should_keep:
                continue

            # ── Scene Detection (histogram correlation) ──
            if frame_idx % 3 == 0:
                hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
                hist = cv2.calcHist([hsv], [0, 1], None, [50, 60], [0, 180, 0, 256])
                cv2.normalize(hist, hist, 0, 1, cv2.NORM_MINMAX)
                if last_hist is not None:
                    corr = cv2.compareHist(last_hist, hist, cv2.HISTCMP_CORREL)
                    if corr < 0.65:
                        detected_scene_cuts.append(written_frames / orig_fps)
                last_hist = hist

            # ── Face Tracking & Cropping ──
            target_center_x = orig_w // 2
            if features.get("facetrack"):
                # Run face cascade every 6 frames to keep CPU utilization lower
                if frame_idx % 6 == 0:
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    faces = face_cascade.detectMultiScale(gray, 1.15, 5, minSize=(100, 100))
                    if len(faces) > 0:
                        # Sort by size to get closest face
                        faces = sorted(faces, key=lambda f: f[2]*f[3], reverse=True)
                        face_rect = faces[0]
                
                if face_rect is not None:
                    x, y, w, h = face_rect
                    target_center_x = x + w // 2

            # Smooth camera panning
            smoothed_center_x = int(ema_alpha * target_center_x + (1 - ema_alpha) * last_center_x)
            last_center_x = smoothed_center_x

            # Clamp crop window boundaries
            x1 = smoothed_center_x - crop_w // 2
            if x1 < 0:
                x1 = 0
            elif x1 + crop_w > orig_w:
                x1 = orig_w - crop_w
            
            cropped = frame[0:crop_h, x1:x1+crop_w]

            # Compute dress torso area based on face cascade box
            if face_rect is not None:
                fx, fy, fw, fh = face_rect
                # Adjust face position coordinates for horizontal panning crop offset x1
                fx_c = fx - x1
                dress_y1 = fy + fh + int(fh * 0.15)
                dress_y2 = min(fy + fh * 4, crop_h)
                dress_x1 = max(0, fx_c - int(fw * 0.8))
                dress_x2 = min(crop_w, fx_c + fw + int(fw * 0.8))
            else:
                dress_y1 = int(crop_h * 0.4)
                dress_y2 = crop_h
                dress_x1 = 0
                dress_x2 = crop_w

            # Clamp ROI bounds safely to valid dimensions
            dress_y1 = max(0, min(dress_y1, crop_h - 1))
            dress_y2 = max(0, min(dress_y2, crop_h))
            dress_x1 = max(0, min(dress_x1, crop_w - 1))
            dress_x2 = max(0, min(dress_x2, crop_w))
            if dress_y2 <= dress_y1:
                dress_y2 = crop_h
                dress_y1 = int(crop_h * 0.4)
            if dress_x2 <= dress_x1:
                dress_x1 = 0
                dress_x2 = crop_w

            # ── MediaPipe Segmentation (Runs once per frame if background removal or dress color shifting is enabled) ──
            person_mask = None
            seg_mask_raw = None
            if (features.get("background") or features.get("dress_color_shift")) and mp_segmenter is not None:
                try:
                    rgb_crop = cv2.cvtColor(cropped, cv2.COLOR_BGR2RGB)
                    seg_results = mp_segmenter.process(rgb_crop)
                    if seg_results is not None and seg_results.segmentation_mask is not None:
                        seg_mask_raw = seg_results.segmentation_mask
                        person_mask = seg_mask_raw > 0.45
                except Exception as seg_err:
                    logger.error(f"Failed selfie segmentation: {seg_err}")

            # ── Dress Color Shift ──
            if features.get("dress_color_shift") and person_mask is not None:
                try:
                    # Convert to HSV to isolate color channels
                    hsv = cv2.cvtColor(cropped, cv2.COLOR_BGR2HSV)
                    h, s, v = cv2.split(hsv)
                    
                    # Detect skin tones in HSV to prevent shifting face/neck/hand colors
                    skin_mask = (((h >= 0) & (h <= 20)) | ((h >= 165) & (h <= 180))) & (s >= 20) & (s <= 160) & (v >= 50) & (v <= 255)
                    
                    # Create dress region mask: within person mask, within torso bounds, and NOT skin
                    torso_mask = np.zeros_like(person_mask, dtype=bool)
                    torso_mask[dress_y1:dress_y2, dress_x1:dress_x2] = True
                    dress_mask = person_mask & torso_mask & (~skin_mask)
                    
                    # Target color selection
                    target_color = features.get("dress_color") or "Blue"
                    color_hue_map = {
                        "Red": 0,
                        "Orange": 15,
                        "Yellow": 30,
                        "Green": 60,
                        "Cyan": 90,
                        "Blue": 120,
                        "Purple": 150,
                        "Pink": 165
                    }
                    target_hue = color_hue_map.get(target_color, 120)
                    
                    # Shift Hue channel
                    h[dress_mask] = target_hue
                    
                    # Saturation booster: if clothing is white/grey/black, saturation is low. 
                    # We boost it so the color shows up beautifully!
                    low_sat = dress_mask & (s < 50)
                    s[low_sat] = 150
                    
                    # Brightness optimizer: if clothing is too dark, raise Value so color is visible
                    dark_val = dress_mask & (v < 50)
                    v[dark_val] = 100
                    
                    # If clothing is too bright/white, lower Value slightly to make color rich and natural
                    bright_val = dress_mask & (v > 220)
                    v[bright_val] = 180
                    
                    # Reconstruct modified image
                    hsv_modified = cv2.merge([h, s, v])
                    cropped = cv2.cvtColor(hsv_modified, cv2.COLOR_HSV2BGR)
                except Exception as dress_err:
                    logger.error(f"Failed dress color shift: {dress_err}")

            # ── Dynamic Background (Refined feather alpha-blend) ──
            if features.get("background") and seg_mask_raw is not None:
                try:
                    # Apply Gaussian Blur to smooth the segmentation mask boundaries
                    smooth_mask = cv2.GaussianBlur(seg_mask_raw, (5, 5), 0)
                    # Contrast adjust/feather mapping: maps [0.15, 0.65] to [0.0, 1.0]
                    smooth_mask = np.clip((smooth_mask - 0.15) / 0.50, 0.0, 1.0)
                    
                    # Create 3-channel alpha multiplier
                    alpha = np.expand_dims(smooth_mask, axis=-1)
                    
                    target_bg = bg_img
                    if target_bg is None:
                        # Solid green screen fallback if background preset fails to load
                        target_bg = np.zeros_like(cropped)
                        target_bg[:, :] = [0, 220, 0] # BGR Green
                        
                    # Smooth soft alpha blend
                    cropped = (alpha * cropped + (1.0 - alpha) * target_bg).astype(np.uint8)
                except Exception as bg_err:
                    logger.error(f"Failed background segmentation: {bg_err}")

            # ── Auto Zoom In / Out ──
            is_zoomed = False
            if features.get("zoom") and edit_plan.get("zooms"):
                for z in edit_plan["zooms"]:
                    if z["start"] <= frame_time <= z["end"]:
                        is_zoomed = True
                        break
            
            if is_zoomed:
                # Zoom in 105%
                zoom_factor = 1.05
                zh = int(crop_h / zoom_factor)
                zw = int(crop_w / zoom_factor)
                zy = (crop_h - zh) // 2
                zx = (crop_w - zw) // 2
                cropped = cropped[zy:zy+zh, zx:zx+zw]
                cropped = cv2.resize(cropped, (crop_w, crop_h))

            # ── B-roll Overlay ──
            speaker_face_frame = cropped.copy()
            active_broll_path = None
            broll_progress = 0.0
            if features.get("broll"):
                for br in broll_assets:
                    if br["start"] <= frame_time <= br["end"]:
                        active_broll_path = br["path"]
                        broll_progress = (frame_time - br["start"]) / (br["end"] - br["start"])
                        break

            broll_applied = False
            if active_broll_path and os.path.exists(active_broll_path):
                broll_img = None
                
                # Check if it's a video file (.mp4, .mov, etc.)
                if active_broll_path.lower().endswith((".mp4", ".mov", ".avi", ".mkv")):
                    try:
                        if active_broll_cap_path != active_broll_path:
                            if broll_cap is not None:
                                broll_cap.release()
                            broll_cap = cv2.VideoCapture(active_broll_path)
                            active_broll_cap_path = active_broll_path
                        
                        if broll_cap is not None and broll_cap.isOpened():
                            b_fps = broll_cap.get(cv2.CAP_PROP_FPS) or 30.0
                            b_total = int(broll_cap.get(cv2.CAP_PROP_FRAME_COUNT))
                            # Determine current frame to read based on active B-roll block start time
                            broll_start_time = 0.0
                            for br in broll_assets:
                                if br["path"] == active_broll_path and br["start"] <= frame_time <= br["end"]:
                                    broll_start_time = br["start"]
                                    break
                            
                            elapsed = frame_time - broll_start_time
                            b_frame_idx = int(elapsed * b_fps)
                            if b_total > 0:
                                b_frame_idx = b_frame_idx % b_total
                            
                            broll_cap.set(cv2.CAP_PROP_POS_FRAMES, b_frame_idx)
                            ret_v, broll_frame = broll_cap.read()
                            if ret_v and broll_frame is not None:
                                broll_img = broll_frame
                    except Exception as broll_vid_err:
                        logger.error(f"Error reading B-roll video: {broll_vid_err}")
                
                # Fallback to image reading if it's an image or video frame read failed
                if broll_img is None:
                    broll_img = cv2.imread(active_broll_path)
                
                if broll_img is not None:
                    # Apply Ken Burns Zoom effect
                    scale = 1.0 + 0.05 * broll_progress  # zoom 100% to 105%
                    bh, bw = broll_img.shape[:2]
                    sz_h, sz_w = int(bh / scale), int(bw / scale)
                    sy, sx = (bh - sz_h) // 2, (bw - sz_w) // 2
                    broll_img = broll_img[sy:sy+sz_h, sx:sx+sz_w]
                    broll_img = cv2.resize(broll_img, (crop_w, crop_h))
                    
                    # ── Pro Level Zoom Out: Circular Face Bubble on B-roll (Bottom-Right CAM) ──
                    try:
                        # Extract a square from the center of the cropped speaker frame
                        face_square_size = min(crop_w, crop_h)
                        y_center = crop_h // 2
                        x_center = crop_w // 2
                        half_s = face_square_size // 2
                        
                        square_speaker = cropped[max(0, y_center - half_s): min(crop_h, y_center + half_s), max(0, x_center - half_s): min(crop_w, x_center + half_s)]
                        
                        # Size of the bubble on screen (e.g. 38% of canvas width)
                        bubble_size = int(crop_w * 0.38)
                        bubble_img = cv2.resize(square_speaker, (bubble_size, bubble_size))
                        
                        # Create circular mask
                        mask = np.zeros((bubble_size, bubble_size), dtype=np.uint8)
                        cv2.circle(mask, (bubble_size // 2, bubble_size // 2), (bubble_size // 2) - 2, 255, -1)
                        
                        # Draw high-quality white circle border
                        border_color = (255, 255, 255) # white
                        cv2.circle(bubble_img, (bubble_size // 2, bubble_size // 2), (bubble_size // 2) - 2, border_color, 5)
                        
                        # Position coordinates: bottom-right (clear of subtitles)
                        px = crop_w - bubble_size - 40
                        py = crop_h - bubble_size - 220  # 220px from bottom (above captions)
                        
                        # Overlay bubble onto B-roll image using circular mask
                        for c in range(3):
                            broll_img[py:py+bubble_size, px:px+bubble_size, c] = np.where(
                                mask == 255,
                                bubble_img[:, :, c],
                                broll_img[py:py+bubble_size, px:px+bubble_size, c]
                            )
                    except Exception as bubble_err:
                        logger.error(f"Failed to generate face bubble on B-roll: {bubble_err}")
                        
                    cropped = broll_img
                    broll_applied = True

            if not broll_applied:
                # ── Jump Cut Zoom After B-roll Ends ──
                broll_ended_recently = False
                if features.get("zoom") and edit_plan.get("brolls"):
                    for br in edit_plan["brolls"]:
                        # Apply zoom for 2.5 seconds right after B-roll ends
                        if float(br["end"]) < frame_time <= float(br["end"]) + 2.5:
                            broll_ended_recently = True
                            break
                
                if broll_ended_recently:
                    try:
                        zoom_factor = 1.08  # slight pro zoom in
                        zh = int(crop_h / zoom_factor)
                        zw = int(crop_w / zoom_factor)
                        zy = (crop_h - zh) // 2
                        zx = (crop_w - zw) // 2
                        cropped = cropped[zy:zy+zh, zx:zx+zw]
                        cropped = cv2.resize(cropped, (crop_w, crop_h))
                    except Exception as zoom_err:
                        pass
                cropped_clean = cropped.copy()
            else:
                cropped_clean = speaker_face_frame

            # ── Brand Logo Overlay (Top-Left, Zoom-in/out Pulse) ──
            if logo_img_rgba is not None:
                try:
                    import math
                    # Smooth sine-wave scale: oscillates between 0.90x and 1.10x every 2 seconds
                    logo_phase = (frame_time % 2.0) / 2.0  # 0.0 → 1.0 over 2s cycle
                    logo_scale = 1.0 + 0.10 * math.sin(logo_phase * 2 * math.pi)

                    # Base logo size = 18% of canvas width
                    base_logo_w = int(crop_w * 0.18)
                    base_logo_h = int(logo_img_rgba.shape[0] * base_logo_w / logo_img_rgba.shape[1])
                    scaled_w = max(10, int(base_logo_w * logo_scale))
                    scaled_h = max(10, int(base_logo_h * logo_scale))

                    logo_resized = cv2.resize(logo_img_rgba, (scaled_w, scaled_h), interpolation=cv2.INTER_LANCZOS4)

                    # Top-left placement with 18px margin, centered around the base position
                    margin = 18
                    center_x = margin + base_logo_w // 2
                    center_y = margin + base_logo_h // 2
                    lx = max(0, center_x - scaled_w // 2)
                    ly = max(0, center_y - scaled_h // 2)

                    # Clip to canvas bounds
                    rx = min(crop_w, lx + scaled_w)
                    ry = min(crop_h, ly + scaled_h)
                    logo_clip_w = rx - lx
                    logo_clip_h = ry - ly

                    if logo_clip_w > 0 and logo_clip_h > 0:
                        logo_region = logo_resized[:logo_clip_h, :logo_clip_w]
                        alpha = logo_region[:, :, 3:4] / 255.0
                        logo_bgr = logo_region[:, :, :3]
                        
                        # 1. Overlay on main cropped frame
                        canvas_region = cropped[ly:ry, lx:rx]
                        blended = (logo_bgr * alpha + canvas_region * (1.0 - alpha)).astype(np.uint8)
                        cropped[ly:ry, lx:rx] = blended

                        # 2. Overlay on clean speaker frame
                        canvas_region_clean = cropped_clean[ly:ry, lx:rx]
                        blended_clean = (logo_bgr * alpha + canvas_region_clean * (1.0 - alpha)).astype(np.uint8)
                        cropped_clean[ly:ry, lx:rx] = blended_clean
                except Exception as logo_err:
                    logger.error(f"Logo overlay error: {logo_err}")

            # Resize processed frame to the chosen target resolution
            if target_w > cropped.shape[1]:
                interpolation = cv2.INTER_LANCZOS4
            elif target_w < cropped.shape[1]:
                interpolation = cv2.INTER_AREA
            else:
                interpolation = cv2.INTER_CUBIC
            
            cropped_final = cv2.resize(cropped, (target_w, target_h), interpolation=interpolation)
            cropped_clean_final = cv2.resize(cropped_clean, (target_w, target_h), interpolation=interpolation)

            # Write processed frames
            out_video.write(cropped_final)
            out_video_clean.write(cropped_clean_final)
            written_frames += 1

        cap.release()
        out_video.release()
        out_video_clean.release()
        if broll_cap is not None:
            broll_cap.release()

        # ── STAGE 8: Create AI Subtitles (ASS) ──
        update_job_status(job_id, "processing", 75)
        
        # Build subtitle events
        subs_ass_path = os.path.join(work_dir, "subtitles.ass")
        sub_style = features.get("subtitle_style", "default")
        font_name = "Impact" if sub_style in ["two_line_slide_right_left", "two_line_slide_left_right", "two_line_slide_top_bottom", "two_line_zoom_in"] else "Arial"
        outline_val = 5 if font_name == "Impact" else 6

        with open(subs_ass_path, "w", encoding="utf-8") as sf:
            sf.write("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n")
            sf.write("[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n")
            # Yellow text primary &H00FFFFFF, Outline: Black &H00000000, Alignment: 2 (bottom center)
            sf.write(f"Style: Default,{font_name},82,&H00FFFFFF,&H0000FFFF,&H00000000,&H00000000,-1,0,0,0,100,100,1,0,1,{outline_val},2,2,30,30,420,1\n\n")
            sf.write("[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")

            def format_ass_time(s):
                h = int(s // 3600)
                m = int((s % 3600) // 60)
                sec = s % 60
                return f"{h}:{m:02d}:{sec:05.2f}"

            # Group all words into lines/phrases (max 4 words or 1.8 seconds max duration)
            grouped_lines = []
            current_line_words = []
            for w in all_words:
                # Filter out words that fall in cut intervals
                word_time = w["start"]
                cut_out = False
                for start, end in merged_cuts:
                    if start <= word_time <= end:
                        cut_out = True
                        break
                if cut_out:
                    continue

                if not current_line_words:
                    current_line_words.append(w)
                else:
                    line_dur = w["end"] - current_line_words[0]["start"]
                    if len(current_line_words) >= 4 or line_dur > 1.8:
                        grouped_lines.append(current_line_words)
                        current_line_words = [w]
                    else:
                        current_line_words.append(w)
            if current_line_words:
                grouped_lines.append(current_line_words)

            # Map word timings to post-cut timings
            # Calculate actual playback offset for each word
            def get_playback_time(real_time):
                subtracted = 0.0
                for start, end in merged_cuts:
                    if real_time > end:
                        subtracted += (end - start)
                    elif real_time > start:
                        subtracted += (real_time - start)
                return real_time - subtracted

            sub_style = features.get("subtitle_style", "default")

            for line in grouped_lines:
                line_start = get_playback_time(line[0]["start"])
                line_end = get_playback_time(line[-1]["end"])
                
                # Write a dialogue event for each active word highlighting
                for idx, active_w in enumerate(line):
                    w_start = get_playback_time(active_w["start"])
                    w_end = get_playback_time(active_w["end"])

                    # Build karaoke text
                    parts = []
                    
                    if sub_style == "important_large":
                        # Find longest word in this line
                        word_lengths = [len(wl["word"].strip(",.?!;:\"'")) for wl in line]
                        longest_idx = word_lengths.index(max(word_lengths)) if word_lengths else 0
                        
                        for k, word_item in enumerate(line):
                            w_text = word_item["word"].upper()
                            if k == longest_idx:
                                # This is the important word (always large)
                                if k == idx:
                                    # Active highlighted important word (Yellow, large)
                                    parts.append(f"{{\\fs115\\c&H0000FFFF&\\fscx100\\fscy100\\t(0,80,\\fscx114\\fscy114)\\t(80,160,\\fscx100\\fscy100)}}{w_text}{{\\r}}")
                                else:
                                    # Non-active important word (White, large)
                                    parts.append(f"{{\\fs115\\c&H00FFFFFF&}}{w_text}{{\\r}}")
                            else:
                                # Other words (always small)
                                if k == idx:
                                    # Active highlighted other word (Yellow, small)
                                    parts.append(f"{{\\fs72\\c&H0000FFFF&\\fscx100\\fscy100\\t(0,80,\\fscx114\\fscy114)\\t(80,160,\\fscx100\\fscy100)}}{w_text}{{\\r}}")
                                else:
                                    # Non-active other word (White, small)
                                    parts.append(f"{{\\fs72\\c&H00FFFFFF&}}{w_text}{{\\r}}")
                                    
                    elif sub_style == "neon_bounce":
                        for k, word_item in enumerate(line):
                            w_text = word_item["word"].upper()
                            if k == idx:
                                # Active neon green highlighted word
                                parts.append(f"{{\\c&H0000FF00&\\fscx100\\fscy100\\t(0,80,\\fscx114\\fscy114)\\t(80,160,\\fscx100\\fscy100)}}{w_text}{{\\c&H00FFFFFF&}}")
                            else:
                                parts.append(w_text)
                                
                    elif sub_style == "minimal_white":
                        for k, word_item in enumerate(line):
                            w_text = word_item["word"].upper()
                            parts.append(w_text)
                            
                    elif sub_style == "bold_yellow":
                        for k, word_item in enumerate(line):
                            w_text = word_item["word"].upper()
                            parts.append(f"{{\\c&H0000FFFF&}}{w_text}{{\\r}}")
                            
                    elif sub_style in ["two_line_slide_right_left", "two_line_slide_left_right", "two_line_slide_top_bottom", "two_line_zoom_in"]:
                        mid = max(1, len(line) // 2)
                        top_line = line[:mid]
                        bottom_line = line[mid:]

                        word_lengths = [len(wl['word'].strip(',.?!;:"')) for wl in line]
                        longest_idx = word_lengths.index(max(word_lengths)) if word_lengths else 0

                        # Helper: build formatted ASS word parts for a line group
                        def _build_parts(word_group, active_k, group_offset=0):
                            parts = []
                            for k, wi in enumerate(word_group):
                                actual_k = k + group_offset
                                wt = wi['word'].upper()
                                imp = (actual_k == longest_idx)
                                if k == active_k:
                                    sz = 115 if imp else 100
                                    parts.append("{\\c&H0000FFFF&\\fs" + str(sz) + "}" + wt + "{\\r}")
                                else:
                                    sz = 95 if imp else 80
                                    parts.append("{\\c&H00FFFFFF&\\fs" + str(sz) + "}" + wt + "{\\r}")
                            return " ".join(parts)

                        # Helper: build entrance/position animation tag
                        def _build_anim(pos_y, is_first):
                            if not is_first:
                                return "\\pos(540," + str(pos_y) + ")"
                            if sub_style == "two_line_slide_right_left":
                                return "\\move(1480," + str(pos_y) + ",540," + str(pos_y) + ",0,200)"
                            elif sub_style == "two_line_slide_left_right":
                                return "\\move(-400," + str(pos_y) + ",540," + str(pos_y) + ",0,200)"
                            elif sub_style == "two_line_slide_top_bottom":
                                src_y = -100 if pos_y == 1420 else 2020
                                return "\\move(540," + str(src_y) + ",540," + str(pos_y) + ",0,200)"
                            elif sub_style == "two_line_zoom_in":
                                return "\\pos(540," + str(pos_y) + ")\\fscx0\\fscy0\\t(0,200,\\fscx100\\fscy100)"
                            return "\\pos(540," + str(pos_y) + ")"

                        # Compute full group timing boundaries
                        l1_group_start = get_playback_time(top_line[0]["start"])
                        l1_group_end   = get_playback_time(top_line[-1]["end"])
                        l2_group_start = get_playback_time(bottom_line[0]["start"]) if bottom_line else l1_group_end
                        l2_group_end   = get_playback_time(bottom_line[-1]["end"])  if bottom_line else l1_group_end

                        # LINE 1 — word-by-word highlight during its own time window
                        for k, word_item in enumerate(top_line):
                            wk_start = get_playback_time(word_item["start"])
                            wk_end   = get_playback_time(word_item["end"])
                            l1_text  = _build_parts(top_line, k, group_offset=0)
                            anim     = _build_anim(1420, k == 0)
                            sf.write("Dialogue: 0," + format_ass_time(wk_start) + "," + format_ass_time(wk_end) + ",Default,,0,0,0,,{" + anim + "}" + l1_text + "\n")

                        # LINE 1 static (no highlight) — persists throughout all of Line 2 so it stays visible
                        if bottom_line:
                            l1_static = _build_parts(top_line, -1, group_offset=0)   # -1 → no active word → all white
                            sf.write("Dialogue: 0," + format_ass_time(l2_group_start) + "," + format_ass_time(l2_group_end) + ",Default,,0,0,0,,{\\pos(540,1420)}" + l1_static + "\n")

                        # LINE 2 — word-by-word highlight; entrance animation on first word only
                        for k, word_item in enumerate(bottom_line):
                            wk_start = get_playback_time(word_item["start"])
                            wk_end   = get_playback_time(word_item["end"])
                            l2_text  = _build_parts(bottom_line, k, group_offset=mid)
                            anim     = _build_anim(1550, k == 0)
                            sf.write("Dialogue: 0," + format_ass_time(wk_start) + "," + format_ass_time(wk_end) + ",Default,,0,0,0,,{" + anim + "}" + l2_text + "\n")
                        continue
                            
                    elif sub_style == "split_top_bottom":
                        mid = max(1, len(line) // 2)
                        top_line = line[:mid]
                        bottom_line = line[mid:]
                        
                        # Find important (longest) word in the entire line
                        word_lengths = [len(wl["word"].strip(",.?!;:\"'")) for wl in line]
                        longest_idx = word_lengths.index(max(word_lengths)) if word_lengths else 0
                        
                        # Top part text
                        top_parts = []
                        for k, word_item in enumerate(top_line):
                            w_text = word_item["word"].upper()
                            if k == idx:
                                top_parts.append(f"{{\\c&H0000FFFF&\\fscx100\\fscy100\\t(0,80,\\fscx114\\fscy114)\\t(80,160,\\fscx100\\fscy100)}}{w_text}{{\\c&H00FFFFFF&}}")
                            else:
                                top_parts.append(w_text)
                        
                        # Bottom part text
                        bottom_parts = []
                        for k, word_item in enumerate(bottom_line):
                            actual_k = mid + k
                            w_text = word_item["word"].upper()
                            is_important = (actual_k == longest_idx)
                            
                            if is_important:
                                if actual_k == idx:
                                    bottom_parts.append(f"{{\\fs115\\c&H0000FFFF&\\fscx100\\fscy100\\t(0,80,\\fscx114\\fscy114)\\t(80,160,\\fscx100\\fscy100)}}{w_text}{{\\r}}")
                                else:
                                    bottom_parts.append(f"{{\\fs115\\c&H00FFFFFF&}}{w_text}{{\\r}}")
                            else:
                                if actual_k == idx:
                                    bottom_parts.append(f"{{\\fs72\\c&H0000FFFF&\\fscx100\\fscy100\\t(0,80,\\fscx114\\fscy114)\\t(80,160,\\fscx100\\fscy100)}}{w_text}{{\\r}}")
                                else:
                                    bottom_parts.append(f"{{\\fs72\\c&H00FFFFFF&}}{w_text}{{\\r}}")
                                    
                        top_text = " ".join(top_parts)
                        bottom_text = " ".join(bottom_parts)
                        
                        if top_text:
                            sf.write(f"Dialogue: 0,{format_ass_time(w_start)},{format_ass_time(w_end)},Default,,0,0,0,,{{\\an8\\fs72}}{top_text}\n")
                        if bottom_text:
                            sf.write(f"Dialogue: 0,{format_ass_time(w_start)},{format_ass_time(w_end)},Default,,0,0,0,,{{\\an2}}{bottom_text}\n")
                        continue
                            
                    else:  # "default"
                        for k, word_item in enumerate(line):
                            w_text = word_item["word"].upper()
                            if k == idx:
                                parts.append(f"{{\\c&H0000FFFF&\\fscx100\\fscy100\\t(0,80,\\fscx114\\fscy114)\\t(80,160,\\fscx100\\fscy100)}}{w_text}{{\\c&H00FFFFFF&}}")
                            else:
                                parts.append(w_text)
                    
                    dialogue_text = " ".join(parts)
                    sf.write(f"Dialogue: 0,{format_ass_time(w_start)},{format_ass_time(w_end)},Default,,0,0,0,,{dialogue_text}\n")

        # ── STAGE 9: Assemble Final Audio & Duck BGM (FFmpeg sidechain compress & SFX) ──
        update_job_status(job_id, "processing", 85)
        
        # Audio trimming (trim original audio to match keep intervals)
        trimmed_audio_path = os.path.join(work_dir, "trimmed_audio.mp3")
        # Build trim filter complex for audio
        audio_filter_parts = []
        for i, (start, end) in enumerate(keep_intervals):
            audio_filter_parts.append(f"[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS[a{i}];")
        
        concat_inputs = "".join(f"[a{i}]" for i in range(len(keep_intervals)))
        audio_filter_parts.append(f"{concat_inputs}concat=n={len(keep_intervals)}:v=0:a=1[a_trimmed]")
        
        trim_audio_cmd = ["ffmpeg", "-y", "-i", audio_path, "-filter_complex", "".join(audio_filter_parts), "-map", "[a_trimmed]", trimmed_audio_path]
        subprocess.run(trim_audio_cmd, capture_output=True)

        # Download / Cache BGM based on mood
        bgm_track_path = os.path.join(work_dir, "bgm.mp3")
        user_mood = features.get("bgm_mood") or edit_plan.get("mood", "Corporate")
        bgm_url = MOOD_MUSIC_URLS.get(user_mood)
        
        has_bgm = False
        if features.get("music") and bgm_url:
            try:
                has_bgm = await download_file_async(bgm_url, bgm_track_path)
            except Exception as e:
                logger.error(f"Failed to download BGM track: {e}")
                has_bgm = False

        # Build dynamic SFX track if sound effects enabled
        mixed_sfx_path = os.path.join(work_dir, "mixed_sfx.wav")
        sfx_filter_parts = []
        sfx_inputs = []
        
        if features.get("sfx"):
            sfx_events = []
            # Add swipe SFX at scene cuts
            for sc in detected_scene_cuts[:10]:
                sfx_events.append((sc, "swipe"))
            # Add whoosh SFX at zooms
            if edit_plan.get("zooms"):
                for z in edit_plan["zooms"]:
                    z_start = get_playback_time(z["start"])
                    sfx_events.append((z_start, "whoosh"))
            # Add whoosh SFX at B-roll entry transitions
            if edit_plan.get("brolls"):
                for br in edit_plan["brolls"]:
                    b_start = get_playback_time(br["start"])
                    sfx_events.append((b_start, "whoosh"))
            # Add pop SFX at subtitle lines
            for line in grouped_lines[:15]:
                l_start = get_playback_time(line[0]["start"])
                sfx_events.append((l_start, "pop"))

            sfx_events = sorted(sfx_events, key=lambda x: x[0])[:20]

            sfx_cmd = ["ffmpeg", "-y"]
            final_duration = written_frames / orig_fps
            sfx_cmd.extend(["-f", "lavfi", "-i", f"anullsrc=r=22050:cl=mono:d={final_duration}"])
            
            input_idx = 1
            for ts, sfx_type in sfx_events:
                sfx_path = sfx_files.get(sfx_type)
                if sfx_path and os.path.exists(sfx_path):
                    sfx_cmd.extend(["-i", sfx_path])
                    delay_ms = int(ts * 1000)
                    sfx_filter_parts.append(f"[{input_idx}:a]adelay={delay_ms}|{delay_ms}[sfx{input_idx}];")
                    sfx_inputs.append(f"[sfx{input_idx}]")
                    input_idx += 1

            if sfx_inputs:
                mix_inputs_str = "[0:a]" + "".join(sfx_inputs)
                sfx_filter_parts.append(f"{mix_inputs_str}amix=inputs={input_idx}:duration=first,volume=0.4[sfx_out]")
                sfx_cmd.extend(["-filter_complex", "".join(sfx_filter_parts), "-map", "[sfx_out]", mixed_sfx_path])
                subprocess.run(sfx_cmd, capture_output=True)

        # Assemble final audio mix (Trimmed Voice, Auto-ducked BGM, SFX)
        final_audio_path = os.path.join(work_dir, "final_audio.mp3")
        
        mix_cmd = ["ffmpeg", "-y", "-i", trimmed_audio_path]
        mix_filter = []
        mix_inputs = ["[0:a]volume=1.4[voice];"]
        
        current_input_idx = 1
        bgm_input_tag = None
        sfx_input_tag = None
        
        if has_bgm and os.path.exists(bgm_track_path):
            mix_cmd.extend(["-i", bgm_track_path])
            bgm_input_tag = f"[{current_input_idx}:a]"
            current_input_idx += 1
            
            # Apply Sidechain compress filter
            mix_filter.append(f"{bgm_input_tag}volume=0.08[bgm_base];")
            mix_filter.append(f"[bgm_base][0:a]sidechaincompress=threshold=0.03:ratio=4:attack=100:release=400[bgm_ducked];")
            mix_inputs.append("[bgm_ducked]")
        
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

        # ── STAGE 10: Final Rendering & Caption Burn ──
        update_job_status(job_id, "processing", 95)
        
        output_folder = os.path.join(settings.BASE_DIR, "static", "ugc", job_id)
        os.makedirs(output_folder, exist_ok=True)
        final_video_path = os.path.join(output_folder, "result.mp4")

        audio_source = final_audio_path if os.path.exists(final_audio_path) else trimmed_audio_path
        clean_video_path = os.path.join(output_folder, "reframed.mp4")

        # 1. Render clean video (with audio, but without subtitles)
        logger.info(f"Rendering clean reframed video (no subtitles) to: {clean_video_path}")
        clean_cmd = [
            "ffmpeg", "-y",
            "-i", reframed_video_path,
            "-i", audio_source,
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-shortest",
            clean_video_path
        ]
        clean_proc = subprocess.run(clean_cmd, capture_output=True, text=True)
        if clean_proc.returncode != 0:
            raise Exception(f"FFmpeg clean video render failed: {clean_proc.stderr}")

        # 2. Burn captions on top of clean video if enabled
        if features.get("caption") and os.path.exists(subs_ass_path):
            logger.info(f"Burning subtitles onto final video: {final_video_path}")
            safe_subs_path = subs_ass_path.replace("\\", "/").replace(":", "\\:")
            burn_cmd = [
                "ffmpeg", "-y",
                "-i", clean_video_path,
                "-vf", f"ass='{safe_subs_path}'",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "copy",
                final_video_path
            ]
            burn_proc = subprocess.run(burn_cmd, capture_output=True, text=True)
            if burn_proc.returncode != 0:
                raise Exception(f"FFmpeg subtitle burn failed: {burn_proc.stderr}")
        else:
            # Copy clean video directly to result.mp4
            shutil.copy(clean_video_path, final_video_path)

        # Copy clean reframed video (without B-rolls) to output folder
        clean_reframed_source = os.path.join(work_dir, "reframed_clean.mp4")
        clean_reframed_dest = os.path.join(output_folder, "reframed_clean.mp4")
        if os.path.exists(clean_reframed_source):
            shutil.copy(clean_reframed_source, clean_reframed_dest)

        # ── STAGE 11: AI Thumbnail & Viral Moment Shorts ──
        thumbnail_dest_path = os.path.join(output_folder, "thumbnail.jpg")
        try:
            v_cap = cv2.VideoCapture(final_video_path)
            ret, thumb_frame = v_cap.read()
            v_cap.release()
            
            if ret and thumb_frame is not None:
                im = Image.fromarray(cv2.cvtColor(thumb_frame, cv2.COLOR_BGR2RGB))
                draw = ImageDraw.Draw(im)
                w, h = im.size
                title_text = "UGC CREATOR"
                draw.rectangle([20, h - 140, w - 20, h - 30], fill=(255, 122, 0, 180))
                draw.text((40, h - 110), title_text, fill=(255, 255, 255))
                im.save(thumbnail_dest_path)
            else:
                gradient = np.zeros((1920, 1080, 3), dtype=np.uint8)
                gradient[:, :] = [0, 122, 255]
                cv2.imwrite(thumbnail_dest_path, gradient)
        except Exception as e:
            logger.error(f"Error creating thumbnail: {e}")

        # ── Viral Shorts — Smart Hook Extraction ────────────────────────────
        # Pick the most energy-packed 7–12s window using word-density scoring
        viral_dest_path = None
        if features.get("viral") and edit_plan.get("viral_moment"):
            vm = edit_plan["viral_moment"]
            raw_start = get_playback_time(float(vm.get("start", 0)))
            raw_end   = get_playback_time(float(vm.get("end",   raw_start + 10.0)))

            # Build word-density map from transcript (words per second for each 0.5s bucket)
            word_density: dict = {}
            for seg in full_transcript:
                for w in seg.get("words", []):
                    bucket = round(float(w["start"]) * 2) / 2   # 0.5s resolution
                    word_density[bucket] = word_density.get(bucket, 0) + 1

            # Clamp window to 7–12 seconds and find best starting point within LLM suggestion ±5s
            TARGET_DUR = 9.0          # ideal hook length (seconds)
            MIN_DUR    = 7.0
            MAX_DUR    = 12.0

            search_start = max(0.0, raw_start - 5.0)
            search_end   = raw_end + 5.0

            best_score   = -1.0
            best_start   = raw_start
            best_dur     = min(MAX_DUR, max(MIN_DUR, raw_end - raw_start))

            # Slide a TARGET_DUR window across the search range in 0.5s steps
            step = 0.5
            t = search_start
            while t + MIN_DUR <= search_end:
                win_end = t + TARGET_DUR
                score = sum(
                    cnt for bucket, cnt in word_density.items()
                    if t <= bucket < win_end
                )
                if score > best_score:
                    best_score = score
                    best_start = t
                    best_dur   = TARGET_DUR
                t += step

            # Ensure duration is within bounds
            best_dur = min(MAX_DUR, max(MIN_DUR, best_dur))

            logger.info(f"Viral hook: start={best_start:.1f}s dur={best_dur:.1f}s density={best_score}")

            if best_dur >= MIN_DUR:
                viral_dest_path = os.path.join(output_folder, "viral.mp4")
                viral_cmd = [
                    "ffmpeg", "-y",
                    "-ss", str(best_start),
                    "-i", final_video_path,
                    "-t", str(best_dur),
                    "-c", "copy",
                    viral_dest_path
                ]
                subprocess.run(viral_cmd, capture_output=True)
                if not os.path.exists(viral_dest_path) or os.path.getsize(viral_dest_path) == 0:
                    viral_dest_path = None

        # ── Upload Final Videos to Cloudflare R2 (if configured) ────────────
        result_video_url  = f"/static/ugc/{job_id}/result.mp4"
        viral_video_url   = f"/static/ugc/{job_id}/viral.mp4" if viral_dest_path else None

        try:
            from app.services.r2_storage import upload_to_r2
            r2_configured = bool(
                settings.R2_ACCESS_KEY_ID and
                settings.R2_SECRET_ACCESS_KEY and
                settings.R2_BUCKET_NAME and
                settings.R2_ENDPOINT
            )
            if r2_configured:
                logger.info(f"Uploading UGC result video to R2 for job {job_id}...")
                r2_result_url = upload_to_r2(
                    final_video_path,
                    f"ugc/{job_id}/result.mp4",
                    content_type="video/mp4"
                )
                if r2_result_url:
                    result_video_url = r2_result_url
                    logger.info(f"R2 result video URL: {r2_result_url}")

                if viral_dest_path and os.path.exists(viral_dest_path):
                    r2_viral_url = upload_to_r2(
                        viral_dest_path,
                        f"ugc/{job_id}/viral.mp4",
                        content_type="video/mp4"
                    )
                    if r2_viral_url:
                        viral_video_url = r2_viral_url
                        logger.info(f"R2 viral video URL: {r2_viral_url}")
        except Exception as r2_err:
            logger.warning(f"R2 upload failed (falling back to local): {r2_err}")

        # ── Success ──────────────────────────────────────────────────────────
        logger.info(f"UGC Pipeline Completed for job {job_id}")

        result_db_paths = {
            "video":     result_video_url,
            "thumbnail": f"/static/ugc/{job_id}/thumbnail.jpg"
        }
        if viral_video_url:
            result_db_paths["viral"] = viral_video_url

        update_job_status(job_id, "completed", 100, result_paths=result_db_paths)

    except Exception as e:
        logger.error(f"UGC Pipeline failed for job {job_id}: {e}", exc_info=True)
        update_job_status(job_id, "failed", 100, error_msg=str(e))
    finally:
        if 'broll_cap' in locals() and broll_cap is not None:
            try:
                broll_cap.release()
            except:
                pass
        try:
            shutil.rmtree(work_dir, ignore_errors=True)
        except:
            pass

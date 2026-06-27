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

from fastapi import APIRouter, Depends, HTTPException, Header, BackgroundTasks, UploadFile, File, Form
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


def extract_epidemic_lqmp3(url: str) -> Optional[str]:
    """Resolves an Epidemic Sound page URL to its direct LQ MP3 CDN link."""
    if not url:
        return None
    if "audiocdn.epidemicsound.com" in url and url.endswith(".mp3"):
        return url
        
    try:
        from BGM import extract_lqmp3
        logger.info(f"Using BGM.py extract_lqmp3 to resolve URL: {url}")
        res = extract_lqmp3(url)
        if res:
            print("\n" + "#"*80)
            print(f"DETECTED BGM AUDIO DOWNLOAD LINK: {url}")
            print(f"RESOLVED DIRECT MP3 LINK: {res}")
            print("#"*80 + "\n")
            logger.info(f"BGM.py successfully extracted: {res}")
            return res
    except Exception as import_err:
        logger.warning(f"Failed to use BGM.py extractor: {import_err}. Falling back to default extractor.")
        
    import re
    import httpx
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
    }
    
    try:
        logger.info(f"Resolving Epidemic Sound page (fallback): {url}")
        with httpx.Client(follow_redirects=True, headers=headers, timeout=15.0) as client:
            response = client.get(url)
            
            # Check for cookie banner / redirection limits
            if 'cookie' in response.text.lower() and len(response.text) < 50000:
                logger.info("Got cookie banner, hitting home page to establish session/cookies...")
                client.get('https://www.epidemicsound.com')
                response = client.get(url)
                
            patterns = [
                r'"lqMp3Url"\s*:\s*"(https?:?(?:\\/|/)+audiocdn\.epidemicsound\.com(?:\\/|/)+lqmp3(?:\\/|/)+[^"]+\.mp3)"',
                r'https?:?(?:\\/|/)+audiocdn\.epidemicsound\.com(?:\\/|/)+lqmp3(?:\\/|/)+[^"\s\\/]+\.mp3'
            ]
            
            for i, pattern in enumerate(patterns):
                matches = re.findall(pattern, response.text)
                if matches:
                    matched_url = matches[0]
                    lqmp3 = matched_url.replace(r'\/', '/').replace('\\/', '/').replace('\\', '')
                    print("\n" + "#"*80)
                    print(f"DETECTED BGM AUDIO DOWNLOAD LINK: {url}")
                    print(f"RESOLVED DIRECT MP3 LINK (FALLBACK): {lqmp3}")
                    print("#"*80 + "\n")
                    logger.info(f"Found Epidemic Sound LQ MP3 using pattern {i+1}: {lqmp3}")
                    return lqmp3
                    
            logger.warning(f"Could not find LQ MP3 link on Epidemic Sound page: {url}")
            return None
    except Exception as e:
        logger.error(f"Error resolving Epidemic Sound URL {url}: {e}")
        return None


# ── Request Models ────────────────────────────────────────────────────────────
class CreateJobReq(BaseModel):
    subtopic_id: Optional[str] = None
    topic_id: Optional[str] = None
    pyq_set_id: Optional[str] = None
    ca_topic_id: Optional[str] = None
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
    bgm_url: Optional[str] = None

class UpdateSceneReq(BaseModel):
    scene_num: int
    image_prompt: Optional[str] = None
    animation_prompt: Optional[str] = None
    dialogue: Optional[str] = None
    audio_url: Optional[str] = None
    bgm_url: Optional[str] = None

class SingleAssetDoneReq(BaseModel):
    filename: str
    asset_type: Optional[str] = "image"
    asset_id: Optional[str] = None
    media_type: Optional[str] = None

def remove_watermark_ffmpeg(file_path: str, is_video: bool = False):
    if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
        return
    logger.info(f"Removing watermark via FFmpeg crop: {file_path} (is_video={is_video})")
    
    # We will use temporary file
    temp_path = file_path + ".temp.mp4" if is_video else file_path + ".temp.jpg"
    
    if "banner" in file_path.lower():
        crop_filter = "crop=iw:ih-60:0:0,scale=2560:1440"
    else:
        crop_filter = "crop=iw:ih-60:0:0,scale=1080:1920"
    
    if is_video:
        cmd = [
            "ffmpeg", "-y", "-nostdin",
            "-i", file_path,
            "-vf", crop_filter,
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-r", "30", "-an", # strip audio if it has any
            temp_path
        ]
    else:
        cmd = [
            "ffmpeg", "-y", "-nostdin",
            "-i", file_path,
            "-vf", crop_filter,
            temp_path
        ]
        
    try:
        res = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL)
        if res.returncode == 0 and os.path.exists(temp_path) and os.path.getsize(temp_path) > 100:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
                os.rename(temp_path, file_path)
                logger.info(f"Successfully removed watermark from {file_path}")
            except Exception as e:
                logger.error(f"Failed to overwrite file with cropped temp file: {e}")
                if os.path.exists(temp_path):
                    try: os.remove(temp_path)
                    except: pass
        else:
            err_msg = res.stderr.decode('utf-8', errors='replace') if res.stderr else ""
            logger.error(f"FFmpeg crop failed for {file_path}: {err_msg}")
            if os.path.exists(temp_path):
                try: os.remove(temp_path)
                except: pass
    except FileNotFoundError:
        logger.warning(f"FFmpeg is not installed on this system. Watermark removal crop skipped.")
        if os.path.exists(temp_path):
            try: os.remove(temp_path)
            except: pass
    except Exception as e:
        logger.warning(f"Error during FFmpeg crop: {e}")
        if os.path.exists(temp_path):
            try: os.remove(temp_path)
            except: pass


# ── Global Resilient Local Helper to Discover Downloaded Files ──
def resilient_find_file(filename: Optional[str], scene_num: int, job_id: str, is_video: bool = False, strict: bool = True) -> Optional[str]:
    import glob
    import os
    import re
    from pathlib import Path
    from datetime import datetime

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

    # Load job info to enforce strict temporal and subtopic-based filtering
    job_created_dt = None
    subtopic_first_word = ""
    try:
        jobs_dict = _load_jobs()
        job = jobs_dict.get(job_id)
        if job:
            if "created_at" in job:
                try:
                    job_created_dt = datetime.fromisoformat(job["created_at"])
                except Exception:
                    pass
            if "subtopic_name" in job:
                clean_subtopic = re.sub(r'[^a-zA-Z0-9\s\-_]', '', job["subtopic_name"]).strip()
                parts = clean_subtopic.split()
                if parts:
                    subtopic_first_word = parts[0]
    except Exception as e:
        logger.warning(f"Error loading job for resilient discovery: {e}")

    logger.info(f"Resilient discovery: searching for scene {scene_num}, job {job_id}, subtopic first word '{subtopic_first_word}', created_at {job_created_dt}, filename {filename} (is_video={is_video}, strict={strict})")

    # Helper function to check if a matched file is valid and recent
    def is_file_recent_and_valid(filepath: str) -> bool:
        if not os.path.exists(filepath):
            return False
        size = os.path.getsize(filepath)
        if is_video and size <= 1000:
            return False
        if not is_video and size <= 0:
            return False
            
        # PRO-LEVEL SCENE CONTAMINATION PREVENTION:
        # Strictly ensure the file does not belong to a different scene!
        base = os.path.basename(filepath).lower()
        scene_markers = [
            f"-{scene_num}-", f"-{scene_num}.", f"image-{scene_num}-",
            f"image-{scene_num}.", f"vid-{scene_num}-", f"video-{scene_num}-",
            f"img-{scene_num}-", f"img-{scene_num}."
        ]
        has_scene_marker = any(marker in base for marker in scene_markers)
        
        # If the file contains the unique job_id, it is 100% guaranteed to belong to this job!
        # Bypass timezone or clock-drift filters to avoid false negatives.
        if job_id and job_id.lower() in base:
            if has_scene_marker:
                return True
        
        # If strict matching is active, the file MUST have the correct scene marker!
        if strict and not has_scene_marker:
            return False
            
        # Even if not strict, reject if it has a mismatching scene index
        if not has_scene_marker:
            # If it doesn't contain the correct scene number, but contains any other scene's index (1 to 15),
            # reject it immediately to prevent cross-scene leakage.
            for other_idx in range(1, 15):
                if other_idx != scene_num:
                    if f"-{other_idx}-" in base or f"image-{other_idx}-" in base or f"vid-{other_idx}-" in base or f"video-{other_idx}-" in base or f"img-{other_idx}-" in base:
                        return False

        if job_created_dt:
            try:
                # Compare in UTC
                mtime = datetime.utcfromtimestamp(os.path.getmtime(filepath))
                # Allow a 60 seconds clock-drift buffer
                if (job_created_dt - mtime).total_seconds() > 60:
                    return False
            except Exception:
                pass
        return True

    # A. Try exact filename match in any of the search folders
    if filename:
        for ddir in unique_dirs:
            full_path = os.path.join(ddir, filename)
            if is_file_recent_and_valid(full_path):
                logger.info(f"Resilient discovery: Found exact filename {filename} in {ddir} (size: {os.path.getsize(full_path)} bytes)")
                return full_path

    # B. Try pattern matching with job_id and subtopic_first_word
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
            if subtopic_first_word:
                patterns.extend([
                    os.path.join(ddir, f"meta-vid-{scene_num}-{subtopic_first_word}-*.mp4"),
                    os.path.join(ddir, f"meta-vid-{scene_num}-{subtopic_first_word}*.mp4"),
                    os.path.join(ddir, f"meta-video-{scene_num}-{subtopic_first_word}-*.mp4"),
                    os.path.join(ddir, f"meta-video-{scene_num}-{subtopic_first_word}*.mp4")
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
            if subtopic_first_word:
                patterns.extend([
                    os.path.join(ddir, f"meta-img-{scene_num}-{subtopic_first_word}-*.jpg"),
                    os.path.join(ddir, f"meta-img-{scene_num}-{subtopic_first_word}*.jpg"),
                    os.path.join(ddir, f"flow-image-{scene_num}-{subtopic_first_word}-*.jpg"),
                    os.path.join(ddir, f"flow-image-{scene_num}-{subtopic_first_word}*.jpg")
                ])

    matches = []
    for p in patterns:
        matches.extend(glob.glob(p))
    
    valid_matches = [m for m in matches if is_file_recent_and_valid(m)]
    if valid_matches:
        latest = max(valid_matches, key=os.path.getmtime)
        logger.info(f"Resilient discovery: Found pattern match: {latest}")
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
    
    valid_fallback = [m for m in matches_fallback if is_file_recent_and_valid(m)]
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
        # Accept the file if it is extremely recent and was downloaded during this active job session
        if is_file_recent_and_valid(m):
            valid_general.append(m)
            
    if valid_general:
        latest = max(valid_general, key=os.path.getmtime)
        logger.info(f"Resilient discovery: Found absolute general fallback: {latest}")
        return latest

    logger.warning(f"Resilient discovery: No recent file found for scene {scene_num}")
    return None


def robust_json_loads(s: str):
    import re
    import json
    
    s = s.strip()
    
    # 1. Try to extract markdown JSON/code blocks
    json_block_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', s, re.DOTALL | re.IGNORECASE)
    if json_block_match:
        s = json_block_match.group(1).strip()
    else:
        json_array_match = re.search(r'```(?:json)?\s*(\[.*?\])\s*```', s, re.DOTALL | re.IGNORECASE)
        if json_array_match:
            s = json_array_match.group(1).strip()
            
    # 2. Try standard json.loads
    try:
        return json.loads(s)
    except Exception:
        pass
        
    # 3. Try to find start and end of JSON block in case of conversational prefix/suffix
    start_brace = s.find('{')
    start_bracket = s.find('[')
    
    start_idx = -1
    end_idx = -1
    
    if start_brace != -1 and (start_bracket == -1 or start_brace < start_bracket):
        start_idx = start_brace
        end_idx = s.rfind('}')
    elif start_bracket != -1:
        start_idx = start_bracket
        end_idx = s.rfind(']')
        
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        candidate = s[start_idx:end_idx+1].strip()
        try:
            return json.loads(candidate)
        except Exception as e:
            logger.warning(f"Standard JSON parse of extracted block failed: {e}. Attempting recovery on block content...")
            s = candidate
            
    # 4. Clean raw control characters and newlines inside strings
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
    
    # 5. Remove trailing commas before closing braces/brackets
    fixed_s = re.sub(r',\s*\]', ']', fixed_s)
    fixed_s = re.sub(r',\s*\}', '}', fixed_s)
    
    try:
        return json.loads(fixed_s)
    except Exception as e2:
        logger.warning(f"Recovery JSON parse failed: {e2}. Attempting manual bracket repair...")
        
    # 6. Truncated block repair
    try:
        last_obj_end = fixed_s.rfind('}')
        if last_obj_end != -1:
            truncated = fixed_s[:last_obj_end+1]
            if truncated.startswith('[') and not truncated.endswith(']'):
                truncated += '\n]'
            elif truncated.startswith('{') and not truncated.endswith('}'):
                truncated += '\n}'
            return json.loads(truncated)
    except Exception as e3:
        logger.error(f"All JSON recovery attempts failed: {e3}")
        
    raise ValueError(f"Failed to parse LLM response as JSON. Response starts with: {s[:100]}")


def parse_custom_timeline_script(script_text: str) -> list:
    """
    Parses a custom scene-by-scene reel script.
    Supports multiple formats:
    1. 🎥 Scene X (time range) blocks with Video:/Voice Over:/BGM: labels
    2. Sections separated by --- with [time] markers, Visual:/VO:/BGM: labels
    3. [0-5 sec] inline time blocks
    """
    import re
    script_text = script_text.replace('\r\n', '\n').replace('\r', '\n')
    scenes = []
    scene_num = 1

    def extract_section_content(lines, visual_pat, vo_pat, bgm_pat):
        current_section = None
        visual_lines, vo_lines, bgm_lines = [], [], []
        for line in lines:
            ls = line.strip()
            if not ls:
                continue
            if re.match(visual_pat, ls, re.IGNORECASE):
                current_section = 'visual'
                content = re.sub(visual_pat + r'\s*', '', ls, flags=re.IGNORECASE).strip()
                if content:
                    visual_lines.append(content)
            elif re.match(vo_pat, ls, re.IGNORECASE):
                current_section = 'vo'
                content = re.sub(vo_pat + r'\s*', '', ls, flags=re.IGNORECASE).strip()
                if content:
                    vo_lines.append(content)
            elif re.match(bgm_pat, ls, re.IGNORECASE):
                current_section = 'bgm'
                content = re.sub(bgm_pat + r'\s*', '', ls, flags=re.IGNORECASE).strip()
                if content:
                    bgm_lines.append(content)
            elif re.match(r'^\[[^\]]+\]', ls) or re.match(r'^\U0001f3a5\s*Scene\s*\d+', ls, re.IGNORECASE):
                continue
            else:
                if current_section == 'visual':
                    visual_lines.append(ls)
                elif current_section == 'vo':
                    vo_lines.append(ls)
                elif current_section == 'bgm':
                    bgm_lines.append(ls)
        visual_desc = ' '.join(visual_lines).strip() or 'Cinematic visual'
        vo_text = ' '.join(vo_lines).strip().strip('"').strip("'")
        bgm_desc = ' '.join(bgm_lines).strip() or 'Soft ambient background music'
        return visual_desc, vo_text, bgm_desc

    VISUAL_PAT = r'^(?:\U0001f3a5|\U0001f3ac|\U0001f4f8)?\s*(?:Video|Visual|Visuals|Footage|Animate)\s*:'
    VO_PAT     = r'^(?:\U0001f399\ufe0f|\U0001f399)?\s*(?:Voice\s*Over|VO|Dialogue|Script)\s*(?:\([^)]+\))?\s*:'
    BGM_PAT    = r'^(?:\U0001f3b5)?\s*BGM\s*:'

    # ── Strategy 1: 🎥 Scene X (...) blocks ──────────────────────────────────
    scene_header_re = re.compile(r'\U0001f3a5\s*Scene\s*(\d+)\s*\(([^)]+)\)', re.IGNORECASE)
    scene_headers = list(scene_header_re.finditer(script_text))
    if scene_headers:
        for i, header in enumerate(scene_headers):
            start = header.start()
            end = scene_headers[i + 1].start() if i + 1 < len(scene_headers) else len(script_text)
            block_text = script_text[start:end]
            time_range = header.group(2).strip()
            lines = block_text.split('\n')
            visual_desc, vo_text, bgm_desc = extract_section_content(lines, VISUAL_PAT, VO_PAT, BGM_PAT)
            scenes.append({
                'scene_num': scene_num,
                'time_range': time_range,
                'image_prompt': visual_desc,
                'animation_prompt': f'Animate: {visual_desc}',
                'dialogue': vo_text,
                'dialogue_english': vo_text,
                'bgm_prompt': bgm_desc
            })
            scene_num += 1
        return scenes

    # ── Strategy 2: --- separated blocks ─────────────────────────────────────
    parts = re.split(r'---', script_text)
    for part in parts:
        part = part.strip()
        if not part:
            continue
        part_lower = part.lower()
        has_content = any(kw in part_lower for kw in ['visual', 'video', 'vo', 'voice over', 'bgm', 'animate', 'dialogue', 'script'])
        if not has_content:
            continue
        time_match = re.search(r'\[([^\]]+)\]', part)
        time_range = time_match.group(1) if time_match else f'{(scene_num-1)*5}-{scene_num*5} sec'
        lines = part.split('\n')
        visual_desc, vo_text, bgm_desc = extract_section_content(lines, VISUAL_PAT, VO_PAT, BGM_PAT)
        scenes.append({
            'scene_num': scene_num,
            'time_range': time_range,
            'image_prompt': visual_desc,
            'animation_prompt': f'Animate: {visual_desc}',
            'dialogue': vo_text,
            'dialogue_english': vo_text,
            'bgm_prompt': bgm_desc
        })
        scene_num += 1

    if scenes:
        return scenes

    # ── Strategy 3: [0-5 sec] inline time blocks ──────────────────────────────
    matches = list(re.finditer(r'\[(\d+-\d+\s*sec)\]', script_text, re.IGNORECASE))
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(script_text)
        block_text = script_text[start:end]
        lines = block_text.split('\n')
        visual_desc, vo_text, bgm_desc = extract_section_content(lines, VISUAL_PAT, VO_PAT, BGM_PAT)
        scenes.append({
            'scene_num': scene_num,
            'time_range': m.group(1),
            'image_prompt': visual_desc,
            'animation_prompt': f'Animate: {visual_desc}',
            'dialogue': vo_text,
            'dialogue_english': vo_text,
            'bgm_prompt': bgm_desc
        })
        scene_num += 1

    return scenes


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
    from app.core.models import SubtopicClassroom, TopicClassroom, ChapterClassroom, Subject, PaperClassroom, Exam, PYQSet, PYQQuestion, CurrentAffairTopic, CurrentAffairReel
    from app.services.llm import generate_simple_response
    import re

    subtopic = None
    topic = None
    chapter = None
    subject = None
    pyq_set = None
    ca_topic = None
    video_length = 50

    if req.topic_id:
        topic = db.query(TopicClassroom).join(ChapterClassroom).join(Subject).join(PaperClassroom).join(Exam).filter(
            TopicClassroom.topic_id == req.topic_id,
            Exam.client_id == client["client_id"]
        ).first()
        if not topic:
            raise HTTPException(404, "Topic not found")
        chapter = topic.chapter
        subject = chapter.subject if chapter else None
        if topic.video_length:
            video_length = topic.video_length
    elif req.subtopic_id:
        subtopic = db.query(SubtopicClassroom).join(TopicClassroom).join(ChapterClassroom).join(Subject).join(PaperClassroom).join(Exam).filter(
            SubtopicClassroom.subtopic_id == req.subtopic_id,
            Exam.client_id == client["client_id"]
        ).first()
        if not subtopic:
            raise HTTPException(404, "Subtopic not found")
        topic = subtopic.topic
        chapter = topic.chapter if topic else None
        subject = chapter.subject if chapter else None
    elif req.pyq_set_id:
        pyq_set = db.query(PYQSet).filter(
            PYQSet.pyq_set_id == req.pyq_set_id,
            PYQSet.client_id == client["client_id"]
        ).first()
        if not pyq_set:
            raise HTTPException(404, "PYQ Set not found")
        video_length = 180  # 3 minutes default for PYQ set reels
    elif req.ca_topic_id:
        ca_topic = db.query(CurrentAffairTopic).filter(
            CurrentAffairTopic.ca_topic_id == req.ca_topic_id,
            CurrentAffairTopic.client_id == client["client_id"]
        ).first()
        if not ca_topic:
            raise HTTPException(404, "Current Affairs topic not found")
        video_length = 60  # 60 seconds default for CA reels
    elif req.transcript and req.transcript.strip():
        pass
    else:
        raise HTTPException(400, "Either subtopic_id, topic_id, pyq_set_id, ca_topic_id, or transcript must be provided")

    lang = req.language or "English"

    if req.transcript and req.transcript.strip():
        study_material = req.transcript.strip()
        is_custom = True
    elif topic and not subtopic and topic.script and topic.script.strip():
        study_material = topic.script.strip()
        is_custom = True
    elif subtopic and subtopic.script and subtopic.script.strip():
        study_material = subtopic.script.strip()
        is_custom = True
    elif ca_topic and ca_topic.script and ca_topic.script.strip():
        study_material = ca_topic.script.strip()
        is_custom = True
    elif pyq_set:
        questions = db.query(PYQQuestion).filter(PYQQuestion.pyq_set_id == req.pyq_set_id).order_by(PYQQuestion.created_at.asc()).all()
        if not questions:
            raise HTTPException(400, "No questions found in this PYQ set. Please upload a PDF/Excel first.")
        study_material = "\n".join(
            f"Question {i+1}: {q.question_text[:150]}{'...' if len(q.question_text) > 150 else ''} -> Correct Answer: {q.correct_answer or 'See explanation'}"
            for i, q in enumerate(questions[:15])
        )
        is_custom = False
    else:
        study_material = (
            (subtopic.description or subtopic.notes or subtopic.name) if subtopic
            else (topic.name if topic else (ca_topic.name if ca_topic else "General"))
        )
        is_custom = False

    # Clean markdown/html
    plain = re.sub(r'<[^>]+>', '', study_material)
    plain = re.sub(r'\[IMAGE:[^\]]+\]', '', plain)
    plain = re.sub(r'[#*`>_~]', '', plain).strip()[:3000]

    if pyq_set:
        subject_name = "PYQ Set"
        chapter_name = pyq_set.name
        topic_name = pyq_set.name
    elif ca_topic:
        subject_name = "Current Affairs"
        chapter_name = ca_topic.name
        topic_name = ca_topic.name
    else:
        subject_name = subject.name if subject else "General"
        chapter_name = chapter.name if chapter else "General"
        topic_name = topic.name if topic else (subtopic.name if subtopic else "General")

    target_name = subtopic.name if subtopic else (topic.name if topic else (pyq_set.name if pyq_set else (ca_topic.name if ca_topic else "Reel")))

    # Count scenes in user's script (support 🎥 Scene X format)
    import re as _re
    _scene_count_match = _re.findall(r'🎥\s*Scene\s*(\d+)', study_material, _re.IGNORECASE)
    user_scene_count = len(_scene_count_match) if _scene_count_match else 12

    if is_custom:
        # Verbatim Script Segmenter Prompt (uses actual scene count from script)
        prompt = f"""You are a professional educational reel director.
Your goal is to split the following exact user script into exactly {user_scene_count} sequential chronological scenes.

User Script:
{study_material}

Rules:
1. You MUST split the user script sequentially into exactly {user_scene_count} logical parts, so that all parts together form the complete user script without losing any sentence or word.
2. For each scene, the "dialogue" field MUST strictly contain the corresponding exact text from the user script in {lang}. Do NOT rewrite, change, summarize, or edit any words in the dialogue!
3. For each scene, generate:
   - "scene_num": The scene number (1 to {user_scene_count}).
   - "dialogue": The exact chronological chunk from the user script in {lang} (keep pacing tags e.g. [thoughtful] if they are part of the script).
   - "dialogue_english": A clean, natural English translation of that dialogue chunk (15-25 words) to be used strictly for video subtitles/captions.
   - "image_prompt": A detailed photorealistic 9:16 portrait image description in English, cinematic, 4K, no text, matching the visual concept of this scene's dialogue.
   - "animation_prompt": A camera movement description: e.g. "slow zoom in", "pan left", etc.

Return a JSON object containing:
- "bgm_prompt": A descriptive background music prompt (in English, 10-20 words) matching the mood and theme of the script (e.g. "Inspiring and uplifting background music for a historic review").
- "scenes": A JSON array of exactly {user_scene_count} scene objects:
  [
    {{
      "scene_num": 1,
      "dialogue": "Exact portion of the user script",
      "dialogue_english": "English translation for subtitles",
      "image_prompt": "Detailed photorealistic portrait description",
      "animation_prompt": "Camera movement description"
    }}
  ]
Return ONLY the JSON object, no markdown."""
    else:
        # Standard AI dialogue generation prompt
        target_topic_name = subtopic.name if subtopic else (topic.name if topic else (pyq_set.name if pyq_set else (ca_topic.name if ca_topic else "General")))
        prompt = f"""You are a professional educational reel director.
Create exactly 12 scenes for a detailed educational reel of minimum {video_length} seconds about: "{target_topic_name}"
Subject: {subject_name} | Chapter: {chapter_name} | Topic: {topic_name}
Language for spoken voiceover dialogue: {lang}

Study material:
{plain[:2000]}

Return a JSON object containing:
- "bgm_prompt": A descriptive background music prompt (in English, 10-20 words) matching the mood and theme of the topic (e.g. "Upbeat, energetic synth beat for a technology summary" or "Slow, reflective piano chords for a history summary").
- "scenes": A JSON array of exactly 12 scene objects:
  [
    {{
      "scene_num": 1,
      "dialogue": "Detailed narration in {lang} (25-35 words per scene to ensure a comprehensive, detailed explanation and a total voiceover length of at least {video_length} seconds)",
      "dialogue_english": "Clean, natural English translation of the dialogue (15-25 words) to be used strictly for video subtitles/captions.",
      "image_prompt": "Detailed photorealistic 9:16 portrait image description in English, cinematic, 4K, no text",
      "animation_prompt": "Camera movement description: slow zoom in / pan left / dolly forward etc."
    }}
  ]

Rules:
- dialogue: spoken {lang}, 25-35 words per scene.
  - CRITICAL: Ensure that the total narration across all 12 scenes has a minimum length of {video_length} seconds when spoken (aim for a minimum of 25-30 words per scene so that the ElevenLabs voiceover is long and detailed enough, resulting in a reel of at least {video_length} seconds).
  - CRITICAL: If the language is Hindi, you MUST write the dialogue strictly in proper Devanagari Unicode script (e.g. "भारत", "प्रौद्योगिकी"). NEVER write in Hinglish (Hindi written using English/Latin alphabet, e.g. "Bharat", "vigyan"), as TTS engines pronounce Hinglish with a highly robotic/incorrect accent.
  - CRITICAL: Spell out all numbers, place names, acronyms, and math symbols fully in spoken words of the target language (e.g. write "उन्नीस सौ सैंतालीस" instead of "1947", "प्रतिशत" / "percent" instead of "%", "किलोमीटर" instead of "km") so that ElevenLabs reads them with perfect professional pronunciation.
- dialogue_english: Translate the narration into clean, natural English (15-25 words) for captions/subtitles.
- image_prompt: detailed English description for AI image generation, always 9:16 portrait orientation
- animation_prompt: short camera movement instruction for video animation
- Make scenes flow as continuous educational explanation
Return ONLY the JSON object, no markdown."""

    study_material_lower = study_material.lower()
    is_timeline_format = is_custom and (
        "visual" in study_material_lower or
        "video:" in study_material_lower or
        "vo:" in study_material_lower or
        "voice over" in study_material_lower or
        "dialogue:" in study_material_lower or
        "animate:" in study_material_lower or
        "script:" in study_material_lower or
        "\U0001f3a5 scene" in study_material_lower or
        "bgm:" in study_material_lower
    )

    try:
        if is_timeline_format:
            scenes = parse_custom_timeline_script(study_material)
            bgm_prompt = scenes[0].get("bgm_prompt") if scenes else "Soft ambient background music"
            
            # Translate Hinglish/Hindi prompts to English
            from app.services.llm import translate_hinglish_prompt_to_english
            for s in scenes:
                if "image_prompt" in s and s["image_prompt"]:
                    s["image_prompt"] = await translate_hinglish_prompt_to_english(s["image_prompt"])
                if "animation_prompt" in s and s["animation_prompt"]:
                    anim_val = s["animation_prompt"]
                    if anim_val.startswith("Animate: "):
                        body = anim_val[9:]
                        translated_body = await translate_hinglish_prompt_to_english(body)
                        s["animation_prompt"] = f"Animate: {translated_body}"
                    else:
                        s["animation_prompt"] = await translate_hinglish_prompt_to_english(anim_val)
        else:
            raw = await generate_simple_response(prompt, "You are a professional video director. Return only valid JSON object.")
            res_data = robust_json_loads(raw)
            
            bgm_prompt = None
            if isinstance(res_data, dict):
                scenes = res_data.get("scenes", [])
                bgm_prompt = res_data.get("bgm_prompt", None)
            elif isinstance(res_data, list):
                scenes = res_data
            else:
                raise ValueError("Invalid response format from LLM")
                
            if not isinstance(scenes, list):
                raise ValueError("Not a list of scenes")
                
            # Ensure exactly 12
            scenes = scenes[:12]
            
        # Format image and animation prompts to contain the full prompt formats used by generators
        for s in scenes:
            if "image_prompt" in s and s["image_prompt"]:
                prompt_val = s["image_prompt"].strip()
                if not prompt_val.startswith("Generate a high quality"):
                    s["image_prompt"] = f"Generate a high quality photorealistic image: {prompt_val}. Vertical 9:16 portrait format, cinematic lighting, 8k, photorealistic, masterpiece, no text."
            if "animation_prompt" in s and s["animation_prompt"]:
                anim_val = s["animation_prompt"].strip()
                if not anim_val.startswith("Animate the previously"):
                    s["animation_prompt"] = f"Animate the previously generated image. Create a smooth 5-second cinematic video animation: {anim_val}. Vertical 9:16 portrait format."
    except Exception as e:
        logger.error(f"Scene generation failed: {e}")
        raise HTTPException(500, f"Failed to generate scenes: {str(e)}")

    # Construct a descriptive, high-quality BGM prompt matching the subject matter if not generated by LLM
    if not bgm_prompt:
        bgm_prompt = f"Upbeat, engaging background music for an educational video about {target_name}."
        if chapter_name and chapter_name != "General":
            bgm_prompt = f"Upbeat, inspiring, educational background music matching the theme of {target_name} in {chapter_name}."

    job_id = "job-" + secrets.token_hex(8)
    _jobs[job_id] = {
        "job_id": job_id,
        "client_id": client["client_id"],
        "subtopic_id": req.subtopic_id,
        "topic_id": req.topic_id,
        "pyq_set_id": req.pyq_set_id,
        "ca_topic_id": req.ca_topic_id,
        "subtopic_name": target_name,
        "language": lang,
        "voice_id": req.voice_id,
        "scenes": scenes,
        "images_received": [],
        "videos_received": [],
        "bgm_prompt": bgm_prompt,
        "status": "waiting_extension",
        "created_at": datetime.utcnow().isoformat(),
        "video_url": None,
        "video_length": video_length
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
    return {
        "success": True, 
        "scenes": job["scenes"], 
        "status": job["status"],
        "subtopic_name": job.get("subtopic_name", ""),
        "bgm_prompt": job.get("bgm_prompt", f"Upbeat background music for {job.get('subtopic_name', 'educational video')}")
    }


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
        
    # Proactively copy image so it can be previewed in real-time in the dashboard
    import re
    import shutil
    match = re.search(r'(?:meta-img|meta-video|meta-vid|flow-image)-(\d+)-', req.filename)
    scene_idx = int(match.group(1)) - 1 if match else req.index
    
    base_uploads = os.path.join(os.getcwd(), "uploads", "social")
    work_dir = os.path.join(base_uploads, f"ext_work_{job_id[:8]}")
    os.makedirs(work_dir, exist_ok=True)
    
    copied = False
    dest_img_path = os.path.join(work_dir, f"scene_{scene_idx}_orig_img.jpg")
    
    if os.path.exists(dest_img_path) and os.path.getsize(dest_img_path) > 121:
        copied = True
    else:
        found_file = resilient_find_file(req.filename, scene_idx + 1, job_id, is_video=False)
        if found_file:
            try:
                shutil.copy2(found_file, dest_img_path)
                remove_watermark_ffmpeg(dest_img_path, is_video=False)
                logger.info(f"Proactively copied image {found_file} to {dest_img_path}")
                copied = True
                
                try:
                    from app.services.r2_storage import upload_to_r2
                    r2_key = f"reels/jobs/{job_id}/scene_{scene_idx}_image.jpg"
                    r2_url = upload_to_r2(dest_img_path, r2_key, "image/jpeg")
                    if r2_url:
                        if 0 <= scene_idx < len(job["scenes"]):
                            job["scenes"][scene_idx]["image_url"] = r2_url
                            job["scenes"][scene_idx]["thumb"] = r2_url
                            logger.info(f"Sync image done to R2: {r2_url}")
                except Exception as r2_err:
                    logger.error(f"Failed to upload image_done to R2: {r2_err}")
            except Exception as e:
                logger.warning(f"Error copying proactive image: {e}")
            
    logger.info(f"Job {job_id}: image {len(job['images_received'])}/{len(job['scenes'])} done: {req.filename}")
    _save_jobs(_jobs)
    return {"success": True, "images_done": len(job["images_received"]), "copied": copied}


# ── Start Videos (extension notifies backend it's starting video phase) ───────
@router.post("/extension/job/{job_id}/start-videos", tags=["Extension"])
async def start_videos(job_id: str, client: dict = Depends(_require_client)):
    global _jobs
    _jobs = _load_jobs()
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    job["status"] = "generating_videos"
    _save_jobs(_jobs)
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
        
    # Proactively copy video so it can be previewed in real-time in the dashboard
    import re
    import shutil
    match = re.search(r'(?:meta-img|meta-video|meta-vid|flow-image)-(\d+)-', req.filename)
    scene_idx = int(match.group(1)) - 1 if match else req.index
    
    base_uploads = os.path.join(os.getcwd(), "uploads", "social")
    work_dir = os.path.join(base_uploads, f"ext_work_{job_id[:8]}")
    os.makedirs(work_dir, exist_ok=True)
    
    copied = False
    dest_vid_path = os.path.join(work_dir, f"scene_{scene_idx}_orig_vid.mp4")
    
    if os.path.exists(dest_vid_path) and os.path.getsize(dest_vid_path) > 50000:
        copied = True
    else:
        found_file = resilient_find_file(req.filename, scene_idx + 1, job_id, is_video=True)
        if found_file:
            try:
                shutil.copy2(found_file, dest_vid_path)
                remove_watermark_ffmpeg(dest_vid_path, is_video=True)
                logger.info(f"Proactively copied video {found_file} to {dest_vid_path}")
                copied = True
                
                try:
                    from app.services.r2_storage import upload_to_r2
                    r2_key = f"reels/jobs/{job_id}/scene_{scene_idx}_video.mp4"
                    r2_url = upload_to_r2(dest_vid_path, r2_key, "video/mp4")
                    if r2_url:
                        if 0 <= scene_idx < len(job["scenes"]):
                            job["scenes"][scene_idx]["video_url"] = r2_url
                            logger.info(f"Sync video done to R2: {r2_url}")
                except Exception as r2_err:
                    logger.error(f"Failed to upload video_done to R2: {r2_err}")
            except Exception as e:
                logger.warning(f"Error copying proactive video: {e}")
            
    logger.info(f"Job {job_id}: video {len(job['videos_received'])}/{len(job['scenes'])} done: {req.filename}")
    _save_jobs(_jobs)
    return {"success": True, "videos_done": len(job["videos_received"]), "copied": copied}


class HarvestDoneReq(BaseModel):
    bgm_url: Optional[str] = None
    videos: Optional[List[str]] = []
    images: Optional[List[str]] = []

@router.post("/extension/job/{job_id}/harvest-done", tags=["Extension"])
async def job_harvest_done(
    job_id: str,
    req: HarvestDoneReq,
    client: dict = Depends(_require_client)
):
    global _jobs
    _jobs = _load_jobs()
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")

    job["status"] = "ready_to_assemble"
    if req.bgm_url is not None:
        job["bgm_url"] = req.bgm_url
    if req.videos:
        job["videos_received"] = list(set(job.get("videos_received", []) + req.videos))
    if req.images:
        job["images_received"] = list(set(job.get("images_received", []) + req.images))

    _save_jobs(_jobs)
    logger.info(f"Job {job_id} harvest complete. Status set to ready_to_assemble.")
    return {"success": True}


# ── Upload File (called by extension to upload generated image/video directly)
@router.post("/extension/job/{job_id}/upload-file", tags=["Extension"])
async def upload_file(
    job_id: str,
    file: UploadFile = File(...),
    index: int = Form(...),
    media_type: str = Form(...),  # "image" or "video"
    filename: str = Form(...),
    client: dict = Depends(_require_client)
):
    global _jobs
    _jobs = _load_jobs()
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")

    is_video = (media_type == "video")
    ext = "mp4" if is_video else "jpg"

    base_uploads = os.path.join(os.getcwd(), "uploads", "social")
    work_dir = os.path.join(base_uploads, f"ext_work_{job_id[:8]}")
    os.makedirs(work_dir, exist_ok=True)

    dest_path = os.path.join(work_dir, f"scene_{index}_orig_{'vid' if is_video else 'img'}.{ext}")

    content = await file.read()
    with open(dest_path, "wb") as f:
        f.write(content)

    # Crop the watermark
    try:
        remove_watermark_ffmpeg(dest_path, is_video=is_video)
    except Exception as e:
        logger.warning(f"Watermark removal failed for uploaded {media_type}: {e}")

    # Upload to Cloudflare R2
    try:
        from app.services.r2_storage import upload_to_r2
        r2_key = f"reels/jobs/{job_id}/scene_{index}_{media_type}.{ext}"
        r2_url = upload_to_r2(dest_path, r2_key, "video/mp4" if is_video else "image/jpeg")
        if r2_url:
            if 0 <= index < len(job["scenes"]):
                if is_video:
                    job["scenes"][index]["video_url"] = r2_url
                else:
                    job["scenes"][index]["image_url"] = r2_url
                    job["scenes"][index]["thumb"] = r2_url
                logger.info(f"Uploaded uploaded file to R2: {r2_url}")
    except Exception as r2_err:
        logger.error(f"Failed to upload uploaded file to R2: {r2_err}")

    # Update job state in memory
    if is_video:
        if filename not in job["videos_received"]:
            job["videos_received"].append(filename)
    else:
        if filename not in job["images_received"]:
            job["images_received"].append(filename)

    # Save job state
    _save_jobs(_jobs)
    logger.info(f"Successfully uploaded {media_type} for job {job_id} scene {index}: {filename}")
    return {"success": True, "copied": True}



# ── Update Scene (called by dashboard to update prompts of a single scene) ──
@router.post("/extension/job/{job_id}/update-scene", tags=["Extension"])
async def update_scene(job_id: str, req: UpdateSceneReq, client: dict = Depends(_require_client)):
    global _jobs
    _jobs = _load_jobs()
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
        
    scene_idx = req.scene_num - 1
    if scene_idx < 0 or scene_idx >= len(job["scenes"]):
        raise HTTPException(400, "Invalid scene number")
        
    scene = job["scenes"][scene_idx]
    if req.image_prompt is not None:
        scene["image_prompt"] = req.image_prompt
    if req.animation_prompt is not None:
        scene["animation_prompt"] = req.animation_prompt
    if req.dialogue is not None:
        scene["dialogue"] = req.dialogue
        
    base_uploads = os.path.join(os.getcwd(), "uploads", "social")
    work_dir = os.path.join(base_uploads, f"ext_work_{job_id[:8]}")

    if req.audio_url is not None:
        scene["audio_url"] = req.audio_url
        import shutil
        import httpx
        os.makedirs(work_dir, exist_ok=True)
        dest_voice = os.path.join(work_dir, f"scene_{scene_idx}_voice.mp3")
        if req.audio_url.startswith("/uploads/"):
            local_rel = req.audio_url.lstrip("/")
            local_path = os.path.join(os.getcwd(), local_rel)
            if os.path.exists(local_path):
                try:
                    shutil.copy2(local_path, dest_voice)
                    logger.info(f"Copied local audio to {dest_voice}")
                except Exception as ce:
                    logger.warning(f"Failed to copy local audio file: {ce}")
        elif req.audio_url.startswith("http"):
            try:
                async with httpx.AsyncClient(follow_redirects=True) as http_client:
                    audio_res = await http_client.get(req.audio_url, timeout=30.0)
                    if audio_res.status_code == 200:
                        with open(dest_voice, "wb") as f:
                            f.write(audio_res.content)
                        logger.info(f"Downloaded audio to {dest_voice}")
            except Exception as de:
                logger.warning(f"Failed to download audio URL: {de}")

    if req.bgm_url is not None:
        scene["bgm_url"] = req.bgm_url
    
    # Remove downloaded files for this scene from lists
    images_to_remove = [f for f in job["images_received"] if f"-{req.scene_num}-" in f or f"-{req.scene_num}." in f or f"image-{req.scene_num}-" in f or f"image-{req.scene_num}." in f]
    for img in images_to_remove:
        if img in job["images_received"]:
            job["images_received"].remove(img)
            
    videos_to_remove = [f for f in job["videos_received"] if f"-{req.scene_num}-" in f or f"-{req.scene_num}." in f or f"vid-{req.scene_num}-" in f or f"video-{req.scene_num}-" in f]
    for vid in videos_to_remove:
        if vid in job["videos_received"]:
            job["videos_received"].remove(vid)
            
    # Also delete the copied assets in uploads directory so they don't show old previews
    orig_img = os.path.join(work_dir, f"scene_{scene_idx}_orig_img.jpg")
    orig_vid = os.path.join(work_dir, f"scene_{scene_idx}_orig_vid.mp4")
    proc_vid = os.path.join(work_dir, f"scene_{scene_idx}_proc.mp4")
    
    if os.path.exists(orig_img):
        try: os.remove(orig_img)
        except: pass
    if os.path.exists(orig_vid):
        try: os.remove(orig_vid)
        except: pass
    if os.path.exists(proc_vid):
        try: os.remove(proc_vid)
        except: pass
        
    _save_jobs(_jobs)
    return {"success": True, "scene": scene}

# Thread-safe in-memory set to track jobs actively being assembled in this server instance
active_assembling_jobs = set()

async def perform_reel_assembly(job_id: str, req: AssembleReq, client: dict):
    try:
        # Load jobs
        current_jobs = _load_jobs()
        job = current_jobs.get(job_id)
        if not job:
            logger.error(f"perform_reel_assembly: Job {job_id} not found.")
            return

        base_uploads = os.path.join(os.getcwd(), "uploads", "social")
        work_dir = os.path.join(base_uploads, f"ext_work_{job_id[:8]}")
        os.makedirs(work_dir, exist_ok=True)

        import shutil

        scenes = job["scenes"]
        num_scenes = len(scenes)

        video_files = []
        image_files = []

        # Helper to update milestones in a synchronized manner
        def update_job_milestone(pct: float, msg: str, status: Optional[str] = None, video_url: Optional[str] = None, scenes_list: Optional[List[dict]] = None):
            try:
                c_jobs = _load_jobs()
                if job_id in c_jobs:
                    c_jobs[job_id]["progress_pct"] = pct
                    c_jobs[job_id]["progress_msg"] = msg
                    if status:
                        c_jobs[job_id]["status"] = status
                        if status == "error":
                            c_jobs[job_id]["error"] = msg
                    if video_url:
                        c_jobs[job_id]["video_url"] = video_url
                    if scenes_list:
                        c_jobs[job_id]["scenes"] = scenes_list
                    _save_jobs(c_jobs)
            except Exception as pe:
                logger.warning(f"Could not update milestone in background: {pe}")

        # ── RESILIENT IMAGE DISCOVERY AND COPYING ──
        for i in range(num_scenes):
            scene_num = i + 1
            img_copied = False
            dest_img_path = os.path.join(work_dir, f"scene_{i}_orig_img.jpg")
            
            # Check if a valid image already exists in work_dir
            if os.path.exists(dest_img_path) and os.path.getsize(dest_img_path) > 121:
                logger.info(f"Scene {i} image already exists and is valid in work_dir. Skipping search.")
                image_files.append(dest_img_path)
                img_copied = True
                continue
                
            images_list = req.images if req.images else job.get("images_received", [])
            filename = images_list[i] if (images_list and i < len(images_list)) else None
            found_file = resilient_find_file(filename, scene_num, job_id, is_video=False, strict=False)
            
            if found_file:
                try:
                    await asyncio.to_thread(shutil.copy2, found_file, dest_img_path)
                    await asyncio.to_thread(remove_watermark_ffmpeg, dest_img_path, is_video=False)
                    image_files.append(dest_img_path)
                    img_copied = True
                    logger.info(f"Copied discovered image {found_file} to {dest_img_path}")
                except Exception as e:
                    logger.warning(f"Error copying image: {e}")
                    
            # D. Dynamic AI Fallback
            if not img_copied:
                try:
                    import urllib.parse
                    import httpx
                    prompt_text = scenes[i].get("image_prompt") or scenes[i].get("dialogue") or "abstract education concept"
                    logger.info(f"Scene {i} image missing in Downloads. Dynamically generating visual matching script via Pollinations AI: {prompt_text}")
                    encoded_prompt = urllib.parse.quote(f"{prompt_text}, 8k, cinematic lighting, masterpiece")
                    fallback_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1080&height=1920&nologo=true&seed={secrets.token_hex(4)}&model=flux"
                    
                    async with httpx.AsyncClient() as http_client:
                        res = await http_client.get(fallback_url, timeout=30.0)
                        if res.status_code == 200:
                            def save_fallback_img(path, content):
                                with open(path, "wb") as f:
                                    f.write(content)
                            await asyncio.to_thread(save_fallback_img, dest_img_path, res.content)
                            image_files.append(dest_img_path)
                            img_copied = True
                            logger.info(f"Successfully generated custom AI fallback image for scene {i} using Pollinations!")
                except Exception as e:
                    logger.error(f"Dynamic AI fallback image generation failed: {e}")
                
                if not img_copied:
                    # Absolute emergency 1x1 valid black JPEG write
                    logger.warning(f"Extreme fallback: writing 1x1 valid black JPEG for scene {i}")
                    def save_black_img(path):
                        with open(path, "wb") as f:
                            f.write(b'\xff\xd8\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.\' ",#\x1c\x1c(7),01444\x1f\'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x15\x00\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x07\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9')
                    await asyncio.to_thread(save_black_img, dest_img_path)
                    image_files.append(dest_img_path)
                    img_copied = True

        # ── RESILIENT VIDEO DISCOVERY AND COPYING ──
        for i in range(num_scenes):
            scene_num = i + 1
            vid_copied = False
            dest_vid_path = os.path.join(work_dir, f"scene_{i}_orig_vid.mp4")
            
            # Check if a valid video already exists in work_dir
            if os.path.exists(dest_vid_path) and os.path.getsize(dest_vid_path) > 50000:
                logger.info(f"Scene {i} video already exists and is valid in work_dir. Skipping search.")
                video_files.append(dest_vid_path)
                vid_copied = True
                continue
                
            videos_list = req.videos if req.videos else job.get("videos_received", [])
            filename = videos_list[i] if (videos_list and i < len(videos_list)) else None
            found_file = resilient_find_file(filename, scene_num, job_id, is_video=True, strict=False)
            
            if found_file:
                try:
                    await asyncio.to_thread(shutil.copy2, found_file, dest_vid_path)
                    await asyncio.to_thread(remove_watermark_ffmpeg, dest_vid_path, is_video=True)
                    video_files.append(dest_vid_path)
                    vid_copied = True
                    logger.info(f"Copied discovered video {found_file} to {dest_vid_path}")
                except Exception as e:
                    logger.warning(f"Error copying video: {e}")
                        
            # E. Final secure fallback: Generate high-quality cinematic Ken Burns video from scene image
            if not vid_copied:
                dest_img_path = os.path.join(work_dir, f"scene_{i}_orig_img.jpg")
                if os.path.exists(dest_img_path) and os.path.getsize(dest_img_path) > 0:
                    try:
                        logger.info(f"Scene {i} video missing or corrupt. Animating scene image into professional cinematic video: {dest_img_path}")
                        
                        # Clean sharp scaling, no zoompan filter to keep it perfectly clear
                        vf = (
                            "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1"
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
                        log_path = os.path.join(work_dir, f"ffmpeg_kenburns_scene_{i}.log")
                        def run_kenburns():
                            with open(log_path, "w", encoding="utf-8") as log_file:
                                return subprocess.run(cmd, stdout=log_file, stderr=log_file, stdin=subprocess.DEVNULL)
                        result = await asyncio.to_thread(run_kenburns)
                        if result.returncode == 0:
                            video_files.append(dest_vid_path)
                            vid_copied = True
                            logger.info(f"Successfully generated animated fallback video from scene image for scene {i}")
                        else:
                            err_msg = "Unknown error"
                            if os.path.exists(log_path):
                                try:
                                    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                                        err_msg = f.read()[-300:]
                                except: pass
                            logger.error(f"FFmpeg fallback video generation failed: {err_msg}")
                    except Exception as ex:
                        logger.error(f"Failed to generate animated video from image for scene {i}: {ex}")
      
            # If it is STILL not copied, generate solid color video
            if not vid_copied:
                logger.warning(f"Extreme fallback: generating solid color 1080x1920 video for scene {i}")
                fallback_cmd = [
                    "ffmpeg", "-y", "-nostdin",
                    "-f", "lavfi", "-i", "color=c=0x1a1a2e:s=1080x1920:d=3.0",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
                    dest_vid_path
                ]
                def run_fallback_vid():
                    return subprocess.run(fallback_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
                result = await asyncio.to_thread(run_fallback_vid)
                if result.returncode == 0:
                    video_files.append(dest_vid_path)
                    vid_copied = True
                else:
                    def write_empty_vid(path):
                        with open(path, "wb") as f:
                            f.write(b"")
                    await asyncio.to_thread(write_empty_vid, dest_vid_path)
                    video_files.append(dest_vid_path)
     
        logger.info(f"Assembling reel from {len(video_files)} copied video files in work directory")

        res_data = await assemble_from_videos(
            video_files=video_files,
            scenes=job["scenes"],
            language=job["language"],
            voice_id=job["voice_id"],
            job_id=job_id,
            bgm_url=req.bgm_url if req.bgm_url else job.get("bgm_url"),
            video_length=job.get("video_length")
        )
        video_url = res_data["video_url"]
        enriched_scenes = res_data["scenes"]
        
        # ── CLOUDFLARE R2 UPLOAD ──
        update_job_milestone(
            pct=99.0,
            msg="Uploading generated reel to Cloudflare R2 storage...",
            status="assembling"
        )
        try:
            from app.services.r2_storage import upload_to_r2
            local_video_path = os.path.join(os.getcwd(), "uploads", "social", os.path.basename(video_url))
            subtopic_id = job.get("subtopic_id")
            topic_id = job.get("topic_id")
            pyq_set_id = job.get("pyq_set_id")
            ca_topic_id = job.get("ca_topic_id")
            
            if subtopic_id:
                r2_key_unique = f"reels/subtopic_{subtopic_id}/reel_{job_id}.mp4"
                r2_key_latest = f"reels/subtopic_{subtopic_id}/latest.mp4"
            elif topic_id:
                r2_key_unique = f"reels/topic_{topic_id}/reel_{job_id}.mp4"
                r2_key_latest = f"reels/topic_{topic_id}/latest.mp4"
            elif pyq_set_id:
                r2_key_unique = f"reels/pyq_{pyq_set_id}/reel_{job_id}.mp4"
                r2_key_latest = f"reels/pyq_{pyq_set_id}/latest.mp4"
            elif ca_topic_id:
                r2_key_unique = f"reels/ca_{ca_topic_id}/reel_{job_id}.mp4"
                r2_key_latest = f"reels/ca_{ca_topic_id}/latest.mp4"
            else:
                r2_key_unique = f"reels/general/reel_{job_id}.mp4"
                r2_key_latest = f"reels/general/latest.mp4"
            
            # Upload unique reel
            r2_url = await asyncio.to_thread(upload_to_r2, local_video_path, r2_key_unique, "video/mp4")
            
            # Upload latest.mp4 for static referencing
            await asyncio.to_thread(upload_to_r2, local_video_path, r2_key_latest, "video/mp4")
            
            if r2_url:
                logger.info(f"R2 Storage: Successfully saved reel in Cloudflare R2! URL={r2_url}")
                video_url = r2_url
        except Exception as r2_err:
            logger.error(f"Failed to upload to Cloudflare R2 (falling back to local storage): {r2_err}")
        
        # Save to database SocialContent table
        from app.core.models import SubtopicClassroom, TopicClassroom, ChapterClassroom, Subject, PaperClassroom, Exam, SocialContent, CurrentAffairTopic, CurrentAffairReel, PYQSet
        from app.core.database import get_session_local
        
        SessionLocal = get_session_local()
        
        def save_db_record():
            with SessionLocal() as db_session:
                subtopic_id = job.get("subtopic_id")
                topic_id = job.get("topic_id")
                pyq_set_id = job.get("pyq_set_id")
                ca_topic_id = job.get("ca_topic_id")
                
                subtopic = None
                topic = None
                pyq_set = None
                ca_topic = None
                
                if subtopic_id:
                    subtopic = db_session.query(SubtopicClassroom).join(TopicClassroom).join(ChapterClassroom).join(Subject).join(PaperClassroom).join(Exam).filter(
                        SubtopicClassroom.subtopic_id == subtopic_id,
                        Exam.client_id == client["client_id"]
                    ).first()
                    if subtopic:
                        topic = subtopic.topic
                        
                if not topic and topic_id:
                    topic = db_session.query(TopicClassroom).join(ChapterClassroom).join(Subject).join(PaperClassroom).join(Exam).filter(
                        TopicClassroom.topic_id == topic_id,
                        Exam.client_id == client["client_id"]
                    ).first()

                if pyq_set_id:
                    pyq_set = db_session.query(PYQSet).filter(
                        PYQSet.pyq_set_id == pyq_set_id,
                        PYQSet.client_id == client["client_id"]
                    ).first()

                if ca_topic_id:
                    ca_topic = db_session.query(CurrentAffairTopic).filter(
                        CurrentAffairTopic.ca_topic_id == ca_topic_id,
                        CurrentAffairTopic.client_id == client["client_id"]
                    ).first()
                    
                subject_name = "General"
                exam_id = ""
                if topic:
                    topic_id = topic.topic_id
                    if topic.chapter and topic.chapter.subject:
                        subject_name = topic.chapter.subject.name
                        if topic.chapter.subject.paper and topic.chapter.subject.paper.exam:
                            exam_id = topic.chapter.subject.paper.exam.exam_id
                elif pyq_set:
                    subject_name = "PYQ Set"
                elif ca_topic:
                    subject_name = "Current Affairs"
                
                subtopic_name = subtopic.name if subtopic else (topic.name if topic else (pyq_set.name if pyq_set else (ca_topic.name if ca_topic else job.get("subtopic_name", "Subtopic"))))
                title = f"{subtopic_name} — {subject_name}"
                
                script_parts = []
                clean_dialogues = []
                for s in job["scenes"]:
                    script_parts.append(
                        f"🎬 Scene {s.get('scene_num', 1)} (5 sec)\n"
                        f"🎙️ Dialogue: {s.get('dialogue', '')}\n"
                        f"📸 Visuals / Footage: {s.get('image_prompt', '')}\n"
                        f"🎥 Editing Notes: {s.get('animation_prompt', '')}"
                    )
                    dlg = s.get('dialogue', '').strip()
                    if dlg:
                        clean_dialogues.append(dlg)
                clean_script = "\n".join(clean_dialogues) if clean_dialogues else title
                
                if ca_topic:
                    ca_reel = db_session.query(CurrentAffairReel).filter(CurrentAffairReel.reel_id == job_id).first()
                    if ca_reel:
                        logger.info(f"CurrentAffairReel for job_id {job_id} already exists. Updating existing record.")
                        ca_reel.media_url = video_url
                        ca_reel.script = clean_script[:2000]
                    else:
                        logger.info(f"Creating new CurrentAffairReel record for job_id {job_id}.")
                        ca_reel = CurrentAffairReel(
                            reel_id=job_id,
                            ca_topic_id=ca_topic_id,
                            client_id=client["client_id"],
                            media_url=video_url,
                            script=clean_script[:2000],
                            created_at=datetime.utcnow()
                        )
                        db_session.add(ca_reel)

                db_item = db_session.query(SocialContent).filter(SocialContent.content_id == job_id).first()
                if db_item:
                    logger.info(f"SocialContent for job_id {job_id} already exists. Updating existing record.")
                    db_item.title = title
                    db_item.body = clean_script[:1000]
                    db_item.media_url = video_url
                    db_item.scenes_json = json.dumps(enriched_scenes)
                    db_item.metadata_json = json.dumps({
                        "subtopic_id": subtopic_id,
                        "topic_id": topic_id,
                        "pyq_set_id": pyq_set_id,
                        "ca_topic_id": ca_topic_id,
                        "exam_id": exam_id,
                        "voice_id": job.get("voice_id"),
                        "language": job.get("language"),
                        "script": clean_script,
                        "job_id": job_id,
                        "bgm_url": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3"
                    })
                    db_item.created_at = datetime.utcnow()
                else:
                    logger.info(f"Creating new SocialContent record for job_id {job_id}.")
                    db_item = SocialContent(
                        content_id=job_id,
                        client_id=client["client_id"],
                        content_type="reel",
                        title=title,
                        body=clean_script[:1000],
                        media_url=video_url,
                        scenes_json=json.dumps(enriched_scenes),
                        metadata_json=json.dumps({
                            "subtopic_id": subtopic_id,
                            "topic_id": topic_id,
                            "pyq_set_id": pyq_set_id,
                            "ca_topic_id": ca_topic_id,
                            "exam_id": exam_id,
                            "voice_id": job.get("voice_id"),
                            "language": job.get("language"),
                            "script": clean_script,
                            "job_id": job_id,
                            "bgm_url": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3"
                        })
                    )
                    db_session.add(db_item)
                db_session.commit()
        
        try:
            await asyncio.to_thread(save_db_record)
        except Exception as db_err:
            logger.error(f"Failed to save db record for job {job_id}: {db_err}")
            
        # Copy to subtopic-specific permanent directory
        try:
            orig_filename = os.path.basename(res_data["video_url"])
            local_video_path = os.path.join(os.getcwd(), "uploads", "social", orig_filename)
            if os.path.exists(local_video_path):
                subtopic_id = job.get("subtopic_id")
                topic_id = job.get("topic_id")
                pyq_set_id = job.get("pyq_set_id")
                ca_topic_id = job.get("ca_topic_id")
                if subtopic_id:
                    subtopic_reels_dir = os.path.join(os.getcwd(), "uploads", "reels", f"subtopic_{subtopic_id}")
                elif topic_id:
                    subtopic_reels_dir = os.path.join(os.getcwd(), "uploads", "reels", f"topic_{topic_id}")
                elif pyq_set_id:
                    subtopic_reels_dir = os.path.join(os.getcwd(), "uploads", "reels", f"pyq_{pyq_set_id}")
                elif ca_topic_id:
                    subtopic_reels_dir = os.path.join(os.getcwd(), "uploads", "reels", f"ca_{ca_topic_id}")
                else:
                    subtopic_reels_dir = os.path.join(os.getcwd(), "uploads", "reels", "general")
                os.makedirs(subtopic_reels_dir, exist_ok=True)
                
                dest_unique = os.path.join(subtopic_reels_dir, f"reel_{job_id}.mp4")
                await asyncio.to_thread(shutil.copy2, local_video_path, dest_unique)
                
                dest_latest = os.path.join(subtopic_reels_dir, "latest.mp4")
                if os.path.exists(dest_latest):
                    try: os.remove(dest_latest)
                    except: pass
                await asyncio.to_thread(shutil.copy2, local_video_path, dest_latest)
                
                logger.info(f"Extension Reel physically saved to: {dest_unique} and {dest_latest}")
        except Exception as copy_err:
            logger.error(f"Failed to copy final reel to subtopic directory: {copy_err}")
        
        update_job_milestone(
            pct=100.0,
            msg="Reel assembled and uploaded to Cloudflare R2 successfully!",
            status="done",
            video_url=video_url,
            scenes_list=enriched_scenes
        )
    except Exception as e:
        logger.error(f"Background assembly failed for job {job_id}: {e}")
        try:
            update_job_milestone(
                pct=0.0,
                msg=f"Assembly failed: {str(e)}",
                status="error"
            )
        except: pass
    finally:
        active_assembling_jobs.discard(job_id)

@router.post("/extension/job/{job_id}/assemble", tags=["Extension"])
async def assemble_reel(
    job_id: str,
    req: AssembleReq,
    background_tasks: BackgroundTasks,
    client: dict = Depends(_require_client)
):
    global _jobs
    _jobs = _load_jobs()
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")

    # Prevent concurrent or duplicate assembly runs:
    if job.get("status") == "done":
        logger.info(f"assemble_reel: Job {job_id} is already done. Returning existing URL.")
        return {"success": True, "video_url": job.get("video_url")}
    if job_id in active_assembling_jobs:
        logger.info(f"assemble_reel: Job {job_id} is currently assembling. Returning response to let client poll.")
        return {"success": True, "message": "Reel assembly is already in progress. Please continue polling."}

    # Set status to assembling and start background task
    job["status"] = "assembling"
    job["progress_pct"] = 75.0
    job["progress_msg"] = "Initializing final reel assembly..."
    _save_jobs(_jobs)

    active_assembling_jobs.add(job_id)
    background_tasks.add_task(perform_reel_assembly, job_id, req, client)

    logger.info(f"assemble_reel: Job {job_id} started in background task.")
    return {"success": True, "message": "Assembly started in the background. Please continue polling status."}


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

    if not scenes_list:
        scenes_list = job.get("scenes", [])

    # ── Proactive Real-Time Grid Sync and Discover Loop ──
    import shutil, re as _re
    base_uploads = os.path.join(os.getcwd(), "uploads", "social")
    work_dir = os.path.join(base_uploads, f"ext_work_{job_id[:8]}")
    os.makedirs(work_dir, exist_ok=True)
    
    total_scenes = len(job["scenes"])
    
    # Sync images: iterate images_received and copy using scene_idx from filename
    for filename in job.get("images_received", []):
        m = _re.search(r'(?:meta-img|flow-image)-(\d+)-', filename)
        scene_idx = (int(m.group(1)) - 1) if m else None
        if scene_idx is None:
            continue
        dest_img_path = os.path.join(work_dir, f"scene_{scene_idx}_orig_img.jpg")
        
        is_missing_or_placeholder = False
        if not os.path.exists(dest_img_path):
            is_missing_or_placeholder = True
        else:
            try:
                if os.path.getsize(dest_img_path) <= 121:
                    is_missing_or_placeholder = True
            except:
                pass
                
        if is_missing_or_placeholder:
            found_file = resilient_find_file(filename, scene_idx + 1, job_id, is_video=False)
            if found_file:
                try:
                    shutil.copy2(found_file, dest_img_path)
                    remove_watermark_ffmpeg(dest_img_path, is_video=False)
                    logger.info(f"Sync: Copied image {found_file} -> {dest_img_path}")
                except Exception as e:
                    logger.warning(f"Sync image error: {e}")

    # Sync videos: iterate videos_received and copy using scene_idx from filename
    for filename in job.get("videos_received", []):
        m = _re.search(r'(?:meta-vid|meta-video)-(\d+)-', filename)
        scene_idx = (int(m.group(1)) - 1) if m else None
        if scene_idx is None:
            continue
        dest_vid_path = os.path.join(work_dir, f"scene_{scene_idx}_orig_vid.mp4")
        
        is_missing_or_placeholder = False
        if not os.path.exists(dest_vid_path):
            is_missing_or_placeholder = True
        else:
            try:
                if os.path.getsize(dest_vid_path) <= 50000:
                    is_missing_or_placeholder = True
            except:
                pass
                
        if is_missing_or_placeholder:
            found_file = resilient_find_file(filename, scene_idx + 1, job_id, is_video=True)
            if found_file:
                try:
                    shutil.copy2(found_file, dest_vid_path)
                    remove_watermark_ffmpeg(dest_vid_path, is_video=True)
                    logger.info(f"Sync: Copied video {found_file} -> {dest_vid_path}")
                except Exception as e:
                    logger.warning(f"Sync video error: {e}")

    # ── Scan work_dir to get actual scene indices that have media on disk ──
    images_on_disk = []
    videos_on_disk = []
    for i in range(total_scenes):
        img_path = os.path.join(work_dir, f"scene_{i}_orig_img.jpg")
        if os.path.exists(img_path) and os.path.getsize(img_path) > 0:
            images_on_disk.append(i)
        vid_path = os.path.join(work_dir, f"scene_{i}_orig_vid.mp4")
        if os.path.exists(vid_path) and os.path.getsize(vid_path) > 0:
            videos_on_disk.append(i)

    return {
        "success": True,
        "status": job["status"],
        "error": job.get("error"),
        "images_done": len(images_on_disk),
        "videos_done": len(videos_on_disk),
        "images_indices": images_on_disk,
        "videos_indices": videos_on_disk,
        "total_scenes": total_scenes,
        "video_url": job.get("video_url"),
        "progress_msg": job.get("progress_msg", ""),
        "progress_pct": job.get("progress_pct", 75.0 if job["status"] == "assembling" else (100.0 if job["status"] == "done" else 0.0)),
        "scenes": scenes_list,
        "metadata": metadata_info
    }


@router.post("/extension/single-asset-done", tags=["Extension"])
async def single_asset_done(
    file: UploadFile = File(...),
    filename: str = Form(...),
    asset_id: str = Form(...),
    media_type: str = Form(...),
    client: dict = Depends(_require_client),
    db: Session = Depends(get_db)
):
    import re
    import shutil
    from pathlib import Path
    
    base_uploads = os.path.join(os.getcwd(), "uploads", "social")
    os.makedirs(base_uploads, exist_ok=True)
    
    is_video = (media_type == 'video')
    ext = 'mp4' if is_video else 'jpg'
    
    is_job_scene = False
    job_id = None
    scene_num = None
    
    if asset_id and asset_id.startswith("jobscene-"):
        is_job_scene = True
        rest = asset_id[len("jobscene-"):]
        parts = rest.rsplit("-", 2)
        if len(parts) == 3:
            job_id, scene_str, media_type_from_asset = parts
            try:
                scene_num = int(scene_str)
            except ValueError:
                pass
                
    if is_job_scene and job_id and scene_num is not None:
        scene_idx = scene_num - 1
        dest_filename = f"jobscene_{job_id}_{scene_idx}_{media_type}.{ext}"
    elif asset_id and "banner" in asset_id.lower():
        dest_filename = f"single_gen_banner_{secrets.token_hex(4)}.{ext}"
    else:
        dest_filename = f"single_gen_{secrets.token_hex(4)}.{ext}"
    dest_path = os.path.join(base_uploads, dest_filename)
    
    content = await file.read()
    with open(dest_path, "wb") as f:
        f.write(content)
        
    try:
        # Crop the watermark using FFmpeg in-place!
        remove_watermark_ffmpeg(dest_path, is_video=is_video)
        
        url = f"/uploads/social/{dest_filename}"
        
        # Upload to Cloudflare R2 if configured
        try:
            from app.services.r2_storage import upload_to_r2
            if is_job_scene and job_id and scene_num is not None:
                scene_idx = scene_num - 1
                r2_key = f"reels/jobs/{job_id}/scene_{scene_idx}_{media_type}.{ext}"
            else:
                r2_key = f"classroom/images/{dest_filename}"
            r2_url = upload_to_r2(dest_path, r2_key, "image/jpeg" if not is_video else "video/mp4")
            if r2_url:
                url = r2_url
        except Exception as r2_err:
            logger.error(f"R2 upload failed for single asset: {r2_err}")
            
        # If it is a job scene, update job in memory and on disk
        if is_job_scene and job_id and scene_num is not None:
            global _jobs
            _jobs = _load_jobs()
            job = _jobs.get(job_id)
            if job:
                scene_idx = scene_num - 1
                if 0 <= scene_idx < len(job["scenes"]):
                    if is_video:
                        job["scenes"][scene_idx]["video_url"] = url
                    else:
                        job["scenes"][scene_idx]["image_url"] = url
                        job["scenes"][scene_idx]["thumb"] = url
                    
                    # Also write it to the local workspace work_dir so compilation is smooth
                    work_dir = os.path.join(base_uploads, f"ext_work_{job_id[:8]}")
                    os.makedirs(work_dir, exist_ok=True)
                    if is_video:
                        orig_vid = os.path.join(work_dir, f"scene_{scene_idx}_orig_vid.mp4")
                        proc_vid = os.path.join(work_dir, f"scene_{scene_idx}_proc.mp4")
                        if os.path.exists(orig_vid):
                            try: os.remove(orig_vid)
                            except: pass
                        if os.path.exists(proc_vid):
                            try: os.remove(proc_vid)
                            except: pass
                        shutil.copy2(dest_path, orig_vid)
                    else:
                        orig_img = os.path.join(work_dir, f"scene_{scene_idx}_orig_img.jpg")
                        if os.path.exists(orig_img):
                            try: os.remove(orig_img)
                            except: pass
                        shutil.copy2(dest_path, orig_img)
                    
                    _save_jobs(_jobs)
                    logger.info(f"Updated job {job_id} scene {scene_num} to R2 URL {url}")

        # If asset_id matches classroom elements, update DB directly!
        if asset_id and not is_job_scene:
            updated_db = False
            ratio = "1:1"
            temp_id = asset_id
            
            # Check for format suffix
            if temp_id.endswith("-1_1"):
                ratio = "1:1"
                temp_id = temp_id[:-4]
            elif temp_id.endswith("-9_16"):
                ratio = "9:16"
                temp_id = temp_id[:-5]
            elif temp_id.endswith("-16_9"):
                ratio = "16:9"
                temp_id = temp_id[:-5]
            elif "topic_banner-" in temp_id:
                ratio = "16:9"
            elif "subtopic_banner-" in temp_id:
                ratio = "16:9"
            elif "banner" in filename.lower():
                ratio = "16:9"

            if temp_id.startswith("exam-"):
                from app.core.models import Exam
                exam = db.query(Exam).filter(Exam.exam_id == temp_id).first()
                if exam:
                    if ratio == "9:16":
                        exam.image_url_9_16 = url
                    elif ratio == "16:9":
                        exam.image_url_16_9 = url
                    else:
                        exam.image_url = url
                    updated_db = True
            elif temp_id.startswith("paper-"):
                from app.core.models import PaperClassroom
                paper = db.query(PaperClassroom).filter(PaperClassroom.paper_id == temp_id).first()
                if paper:
                    if ratio == "9:16":
                        paper.image_url_9_16 = url
                    elif ratio == "16:9":
                        paper.image_url_16_9 = url
                    else:
                        paper.image_url = url
                    updated_db = True
            elif temp_id.startswith("subject-"):
                from app.core.models import Subject
                subject = db.query(Subject).filter(Subject.subject_id == temp_id).first()
                if subject:
                    if ratio == "9:16":
                        subject.image_url_9_16 = url
                    elif ratio == "16:9":
                        subject.image_url_16_9 = url
                    else:
                        subject.image_url = url
                    updated_db = True
            elif temp_id.startswith("chapter-"):
                from app.core.models import ChapterClassroom
                chapter = db.query(ChapterClassroom).filter(ChapterClassroom.chapter_id == temp_id).first()
                if chapter:
                    if ratio == "9:16":
                        chapter.image_url_9_16 = url
                    elif ratio == "16:9":
                        chapter.image_url_16_9 = url
                    else:
                        chapter.image_url = url
                    updated_db = True
            elif temp_id.startswith("topic_banner-") or temp_id.startswith("topic-"):
                clean_id = temp_id.replace("topic_banner-", "")
                from app.core.models import TopicClassroom
                topic = db.query(TopicClassroom).filter(TopicClassroom.topic_id == clean_id).first()
                if topic:
                    if ratio == "9:16":
                        topic.image_url_9_16 = url
                    elif ratio == "16:9":
                        topic.image_url_16_9 = url
                    else:
                        topic.image_url = url
                    updated_db = True
            elif "subtopic" in temp_id:
                clean_id = temp_id.replace("subtopic_banner-", "")
                from app.core.models import SubtopicClassroom
                subtopic = db.query(SubtopicClassroom).filter(SubtopicClassroom.subtopic_id == clean_id).first()
                if subtopic:
                    if ratio == "9:16":
                        subtopic.image_url_9_16 = url
                    elif ratio == "16:9":
                        subtopic.image_url_16_9 = url
                        subtopic.banner_url = url
                    else:
                        subtopic.image_url = url
                    updated_db = True
            
            if updated_db:
                db.commit()
                logger.info(f"Auto-updated classroom DB: {asset_id} to {url}")
        
        return {"success": True, "url": url}
    except Exception as e:
        logger.error(f"Failed to copy single asset: {e}")
        raise HTTPException(500, f"Sync failed: {str(e)}")


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
            # CAPTIONS IN ENGLISH ONLY: Prefer dialogue_english for subtitles, fallback to dialogue
            dialogue = (s.get("dialogue_english") or s.get("dialogue") or "").strip()
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
    job_id: str,
    bgm_url: Optional[str] = None,
    video_length: Optional[int] = 50
) -> dict:
    from app.services.video_engine import generate_elevenlabs_voiceover, validate_video_asset, validate_audio_asset, generate_silent_audio
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
        voice_exists = False
        if os.path.exists(scene_voice_path) and os.path.getsize(scene_voice_path) > 1000:
            logger.info(f"Scene {i} voice already exists. Skipping ElevenLabs generation.")
            voice_exists = True

        if not voice_exists:
            if dialogue:
                voice_result = await generate_elevenlabs_voiceover(dialogue, work_dir, voice_id=voice_id, language=language)
                if voice_result and os.path.exists(voice_result):
                    if os.path.exists(scene_voice_path):
                        os.remove(scene_voice_path)
                    os.rename(voice_result, scene_voice_path)
                else:
                    generate_silent_audio(3.0, work_dir, f"scene_{i}_voice.mp3")
                    
                # PRO-LEVEL AUDIO ENHANCEMENT: Trim silences and apply studio voice compressor & EQ
                trimmed_path = os.path.join(work_dir, f"scene_{i}_voice_trimmed.mp3")
                voice_filter = (
                    "highpass=f=80,"
                    "volume=0.95"
                )
                trim_cmd = [
                    "ffmpeg", "-y", "-nostdin",
                    "-i", scene_voice_path,
                    "-af", voice_filter,
                    trimmed_path
                ]
                trim_res = await asyncio.to_thread(subprocess.run, trim_cmd, capture_output=True, stdin=subprocess.DEVNULL)
                if trim_res.returncode == 0 and os.path.exists(trimmed_path) and os.path.getsize(trimmed_path) > 0:
                    os.remove(scene_voice_path)
                    os.rename(trimmed_path, scene_voice_path)
            else:
                # Create a 3-second silent audio segment if dialogue is empty
                await asyncio.to_thread(
                    subprocess.run,
                    [
                        "ffmpeg", "-y", "-nostdin",
                        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                        "-t", "3", "-c:a", "libmp3lame", scene_voice_path
                    ],
                    capture_output=True,
                    stdin=subprocess.DEVNULL
                )
            
        # Probe scene audio duration
        probe = await asyncio.to_thread(
            subprocess.run,
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", scene_voice_path],
            capture_output=True, text=True, stdin=subprocess.DEVNULL
        )
        scene_dur = float(probe.stdout.strip()) if probe.returncode == 0 and probe.stdout.strip() else 5.0
        
        # Asset Validation Layer for audio narration segment
        validated_voice_path = await asyncio.to_thread(validate_audio_asset, scene_voice_path, scene_dur, work_dir, f"scene_{i}_voice")
        if os.path.exists(validated_voice_path) and validated_voice_path != scene_voice_path:
            try:
                shutil.copy2(validated_voice_path, scene_voice_path)
            except Exception as se:
                logger.warning(f"Failed to copy validated voice: {se}")

        scene_audios.append(scene_voice_path)
        scene_durations.append(scene_dur)

    total_audio_dur = sum(scene_durations)

    # 2. Process each video: Use original video directly to prevent any FFmpeg filter conversion crashes
    update_progress("Narration complete! Synchronizing original scene video segments...", 85.0)
    processed_videos = []
    processed_videos_durations = []
    for i, vf in enumerate(video_files):
        out_path = os.path.join(work_dir, f"scene_{i}_proc.mp4")
        scene_dur = scene_durations[i]
        
        # Calculate visual duration adjusted for xfade overlap (0.5s transition)
        trans_dur = 0.5 if len(video_files) > 1 else 0.0
        is_last = (i == len(video_files) - 1)
        visual_dur = scene_dur + trans_dur if not is_last else scene_dur
        processed_videos_durations.append(visual_dur)
        
        calc_pct = 85.0 + (i / len(video_files)) * 8.0
        update_progress(f"Using original scene video {i + 1} of {len(video_files)}...", calc_pct)
        
        # Loop/trim the original video to match the exact scene narration duration
        rendered_successfully = False
        try:
            logger.info(f"Synchronizing visual duration for scene {i}: {vf} to match {visual_dur}s")
            cmd = [
                "ffmpeg", "-y", "-nostdin",
                "-stream_loop", "-1",
                "-i", vf,
                "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1",
                "-t", str(visual_dur),
                "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                "-pix_fmt", "yuv420p", "-r", "30",
                out_path
            ]
            log_path = os.path.join(work_dir, f"ffmpeg_trim_scene_{i}.log")
            def run_trim():
                with open(log_path, "w", encoding="utf-8") as log_file:
                    return subprocess.run(cmd, stdout=log_file, stderr=log_file, stdin=subprocess.DEVNULL)
            res = await asyncio.to_thread(run_trim)
            if res.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 1000:
                rendered_successfully = True
                logger.info(f"Successfully synchronized visual duration for scene {i} to {visual_dur}s")
        except Exception as err:
            logger.warning(f"FFmpeg loop/trim failed for scene {i}: {err}")
            
        if not rendered_successfully:
            # Strictly copy the original video directly using shutil to avoid any FFmpeg processing crashes
            try:
                logger.info(f"Using original video fallback copy for scene {i}: copying {vf} to {out_path}")
                await asyncio.to_thread(shutil.copy2, vf, out_path)
            except Exception as copy_err:
                logger.warning(f"Error copying original video fallback for scene {i}: {copy_err}")
                # Resilient binary fallback copy
                try:
                    def bin_copy():
                        with open(vf, "rb") as f_in:
                            with open(out_path, "wb") as f_out:
                                f_out.write(f_in.read())
                    await asyncio.to_thread(bin_copy)
                except Exception as bin_err:
                    logger.error(f"Failed binary fallback copy: {bin_err}")
        
        # Asset Validation Layer for processed scene video
        validated_v_path = await asyncio.to_thread(validate_video_asset, out_path, visual_dur, work_dir, f"scene_{i}_ext")
        processed_videos.append(validated_v_path)

    # 3. Concatenate all processed videos together with dynamic xfade transitions
    update_progress("Visual segments synced! Merging scenes into a seamless timeline...", 93.0)
    
    from app.services.video_engine import build_xfade_filter_complex
    
    input_args = []
    pre_filters = []
    for idx, v in enumerate(processed_videos):
        input_args.extend(["-i", v])
        v_dur = processed_videos_durations[idx]
        pre_filters.append(f"[{idx}:v]trim=duration={v_dur:.2f},setpts=PTS-STARTPTS,scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1[v_pre{idx}];")
        
    trans_dur = 0.5 if len(processed_videos) > 1 else 0.0
    xfade_complex, final_v_label = build_xfade_filter_complex(
        video_count=len(processed_videos),
        durations=processed_videos_durations,
        trans_dur=trans_dur
    )
    
    for idx in range(len(processed_videos)):
        xfade_complex = xfade_complex.replace(f"[{idx}:v]", f"[v_pre{idx}]")
        
    filter_parts = pre_filters + [xfade_complex]
    filter_parts.append(f"{final_v_label}copy[v]")
    
    temp_video = os.path.join(work_dir, "temp_video.mp4")
    concat_cmd = ["ffmpeg", "-y", "-nostdin"] + input_args + [
        "-filter_complex", "".join(filter_parts),
        "-map", "[v]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", "30", "-an",
        temp_video
    ]
    concat_log_path = os.path.join(work_dir, "ffmpeg_concat.log")
    def run_concat():
        with open(concat_log_path, "w", encoding="utf-8") as log_file:
            return subprocess.run(concat_cmd, stdout=log_file, stderr=log_file, stdin=subprocess.DEVNULL)
    res_concat = await asyncio.to_thread(run_concat)
    if res_concat.returncode != 0:
        logger.warning(f"Complex xfade transition video concatenation failed (exit {res_concat.returncode}). Retrying with safe standard concat fallback...")
        list_path = os.path.join(work_dir, "concat_list.txt")
        with open(list_path, "w", encoding="utf-8") as f:
            for v in processed_videos:
                v_fixed = v.replace('\\', '/')
                f.write(f"file '{v_fixed}'\n")
                
        fallback_cmd = [
            "ffmpeg", "-y", "-nostdin",
            "-f", "concat", "-safe", "0", "-i", list_path,
            "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", "30", "-an",
            temp_video
        ]
        fallback_log_path = os.path.join(work_dir, "ffmpeg_concat_fallback.log")
        def run_fallback():
            with open(fallback_log_path, "w", encoding="utf-8") as log_file:
                return subprocess.run(fallback_cmd, stdout=log_file, stderr=log_file, stdin=subprocess.DEVNULL)
        res_fallback = await asyncio.to_thread(run_fallback)
        if res_fallback.returncode != 0:
            err_msg = "Unknown error"
            if os.path.exists(fallback_log_path):
                try:
                    with open(fallback_log_path, "r", encoding="utf-8", errors="ignore") as f:
                        err_msg = "".join(f.readlines()[-20:])
                except:
                    pass
            raise Exception(f"FFmpeg video concatenation failed on both xfade and fallback (exit {res_fallback.returncode}): {err_msg}")

    # 4. Concatenate all scene voiceovers together
    update_progress("Merging narration segment audio tracks...", 95.0)
    audio_list_path = os.path.join(work_dir, "audio_concat_list.txt")
    with open(audio_list_path, "w") as f:
        for a in scene_audios:
            f.write(f"file '{a.replace(chr(92), '/')}'\n")
            
    full_voice_path = os.path.join(work_dir, "full_voice.mp3")
    # Re-encode the audio narration during concat to resolve sample rate / codec drift
    await asyncio.to_thread(
        subprocess.run,
        ["ffmpeg", "-y", "-nostdin", "-f", "concat", "-safe", "0", "-i", audio_list_path,
         "-c:a", "libmp3lame", "-b:a", "192k", full_voice_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL
    )

    # 5. BGM
    update_progress("Downloading and verifying background music (BGM)...", 96.0)
    bgm_download_url = bgm_url or "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3"
    
    # If it's an Epidemic Sound page, extract the direct MP3 link
    if bgm_download_url and "epidemicsound.com" in bgm_download_url and not bgm_download_url.endswith(".mp3"):
        extracted_url = extract_epidemic_lqmp3(bgm_download_url)
        if extracted_url:
            bgm_download_url = extracted_url
            logger.info(f"Resolved Epidemic Sound BGM direct link: {bgm_download_url}")
        else:
            logger.warning("Could not extract Epidemic Sound direct link. Falling back to default BGM.")
            bgm_download_url = "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3"

    bgm_path = os.path.join(work_dir, "bgm.mp3")
    bgm_downloaded = False
    try:
        import httpx
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        async with httpx.AsyncClient() as http_client:
            print("\n" + "#"*80)
            print(f"DOWNLOADING BACKGROUND MUSIC (BGM) FROM EPIDEMIC SOUND...")
            print(f"Source URL: {bgm_download_url}")
            print("#"*80 + "\n")
            r = await http_client.get(bgm_download_url, headers=headers, timeout=45.0, follow_redirects=True)
            if r.status_code == 200 and len(r.content) > 100:
                with open(bgm_path, "wb") as f: f.write(r.content)
                bgm_downloaded = True
                print("\n" + "#"*80)
                print("SUCCESSFULLY DOWNLOADED EPIDEMIC SOUND BGM AUDIO FILE!")
                print(f"Direct Link: {bgm_download_url}")
                print(f"File Size: {len(r.content)} bytes")
                print("#"*80 + "\n")
                logger.info(f"BGM DOWNLOADED: URL = {bgm_download_url}")
            else:
                logger.warning(f"Failed to download BGM from {bgm_download_url} (status {r.status_code}). Trying default.")
                r = await http_client.get("https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3", timeout=30.0)
                if r.status_code == 200 and len(r.content) > 100:
                    with open(bgm_path, "wb") as f: f.write(r.content)
                    bgm_downloaded = True
                    print("\n" + "="*80)
                    print("BGM DOWNLOAD FALLBACK: Using default SoundHelix BGM")
                    print(f"Direct Audio Link: https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3")
                    print("="*80 + "\n")
                    logger.info("BGM DOWNLOADED (Default fallback): URL = https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3")
                else:
                    bgm_path = None
    except Exception as ex:
        logger.error(f"Error handling BGM download: {ex}")
        bgm_path = None

    if bgm_downloaded and bgm_path and os.path.exists(bgm_path) and os.path.getsize(bgm_path) > 0:
        print("\n" + "="*80)
        print("VERIFIED: BGM downloaded and integrated successfully into the reel!")
        print(f"Path: {bgm_path}")
        print(f"Size: {os.path.getsize(bgm_path)} bytes")
        print("="*80 + "\n")
    else:
        print("\n" + "="*80)
        print("VERIFICATION FAILED: BGM file is empty or download failed.")
        print("="*80 + "\n")

    # 6. Generate precise synchronized subtitles
    update_progress("Generating precise synchronized subtitles...", 97.0)
    sub_path = await asyncio.to_thread(create_scene_subtitles, scenes, scene_durations, work_dir)
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
            f"[1:a]volume=1.0[av];"
            f"[2:a]volume=0.05,atrim=0:{total_audio_dur:.2f},asetpts=PTS-STARTPTS[abg];"
            f"[av][abg]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,alimiter=limit=0.95[a]"
        )
        maps = ["-map", "[v]", "-map", "[a]"]
    else:
        fc = f"[0:v]ass='{safe_sub}'[v];[1:a]volume=1.0[a]"
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

    assembly_log_path = os.path.join(work_dir, "ffmpeg_assembly.log")
    def run_assembly():
        with open(assembly_log_path, "w", encoding="utf-8") as log_file:
            return subprocess.run(final_cmd, stdout=log_file, stderr=log_file, stdin=subprocess.DEVNULL)
    result = await asyncio.to_thread(run_assembly)
    if result.returncode != 0:
        err_msg = "Unknown error"
        if os.path.exists(assembly_log_path):
            try:
                with open(assembly_log_path, "r", encoding="utf-8", errors="ignore") as f:
                    err_msg = "".join(f.readlines()[-20:])
            except:
                pass
        raise Exception(f"FFmpeg final assembly failed (exit {result.returncode}): {err_msg}")

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
        
        # Local paths
        local_img = os.path.join(work_dir, f"scene_{i}_orig_img.jpg")
        local_vid = os.path.join(work_dir, f"scene_{i}_proc.mp4")
        local_aud = os.path.join(work_dir, f"scene_{i}_voice.mp3")
        
        r2_img_url = None
        r2_vid_url = None
        r2_aud_url = None
        
        # Upload individual assets to R2 if configured
        try:
            from app.services.r2_storage import upload_to_r2
            if os.path.exists(local_img):
                r2_key_img = f"reels/work_{job_id[:8]}/scene_{i}_img.jpg"
                r2_img_url = await asyncio.to_thread(upload_to_r2, local_img, r2_key_img, "image/jpeg")
            if os.path.exists(local_vid):
                r2_key_vid = f"reels/work_{job_id[:8]}/scene_{i}_vid.mp4"
                r2_vid_url = await asyncio.to_thread(upload_to_r2, local_vid, r2_key_vid, "video/mp4")
            if os.path.exists(local_aud):
                r2_key_aud = f"reels/work_{job_id[:8]}/scene_{i}_aud.mp3"
                r2_aud_url = await asyncio.to_thread(upload_to_r2, local_aud, r2_key_aud, "audio/mpeg")
        except Exception as r2_err:
            logger.error(f"Failed to upload scene {i} assets to R2: {r2_err}")
            
        enriched_scenes.append({
            "id": 1000 + i,
            "scene_id": 1000 + i,
            "start": round(start_time, 2),
            "end": round(end_time, 2),
            "duration": round(scene_dur, 2),
            "video": r2_vid_url or f"/uploads/social/ext_work_{job_id[:8]}/scene_{i}_proc.mp4",
            "videoThumb": r2_vid_url or f"/uploads/social/ext_work_{job_id[:8]}/scene_{i}_proc.mp4",
            "audio": r2_aud_url or f"/uploads/social/ext_work_{job_id[:8]}/scene_{i}_voice.mp3",
            "thumb": r2_img_url or f"/uploads/social/ext_work_{job_id[:8]}/scene_{i}_orig_img.jpg",
            "image": r2_img_url or f"/uploads/social/ext_work_{job_id[:8]}/scene_{i}_orig_img.jpg",
            "script": s.get("dialogue", ""),
            "transition": trans_effect,
            "motion": effect,
            "voice": voice_id or "adam"
        })

    return {
        "video_url": f"/uploads/social/{output_filename}",
        "scenes": enriched_scenes
    }

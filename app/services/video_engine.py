import os
import asyncio
import httpx
import secrets
import subprocess
import logging
import urllib.parse
from typing import List, Optional, Dict
from app.core.config import settings

logger = logging.getLogger(__name__)

# Constants
PEXELS_SEARCH_URL = "https://api.pexels.com/videos/search"
ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech"
DEFAULT_BGM_URL = "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3" # More stable public MP3 URL

async def generate_elevenlabs_voiceover(text: str, work_dir: str, voice_id: Optional[str] = None) -> Optional[str]:
    """Generates high-quality voiceover using ElevenLabs."""
    api_key = settings.ELEVENLABS_API_KEY
    if not api_key:
        logger.warning("ElevenLabs API Key missing.")
        return None
    
    vid = voice_id or "pNInz6obpgDQGcFmaJgB" # Adam voice (default pro)
    url = f"{ELEVENLABS_TTS_URL}/{vid}"
    
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json"
    }
    
    data = {
        "text": text,
        "model_id": "eleven_multilingual_v2", 
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.06, # Add a bit of 'expressive' style
            "use_speaker_boost": True
        }
    }
    
    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(url, json=data, headers=headers, timeout=60.0)
            if res.status_code == 200:
                audio_path = os.path.join(work_dir, "voice.mp3")
                with open(audio_path, "wb") as f:
                    f.write(res.content)
                return audio_path
            else:
                logger.error(f"ElevenLabs Error: {res.status_code} - {res.text}")
                return None
    except Exception as e:
        logger.error(f"ElevenLabs Request Failed: {e}")
        return None

async def search_pexels_videos(query: str, count: int = 1) -> List[str]:
    """Search for vertical stock videos on Pexels."""
    api_key = settings.PEXELS_API_KEY
    if not api_key:
        return []
    
    headers = {"Authorization": api_key}
    params = {
        "query": query,
        "per_page": count,
        "orientation": "portrait",
        "size": "medium"
    }
    
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(PEXELS_SEARCH_URL, headers=headers, params=params, timeout=20.0)
            if res.status_code == 200:
                data = res.json()
                video_urls = []
                for v in data.get("videos", []):
                    # Get the HD/Mobile file
                    files = v.get("video_files", [])
                    # Prefer 1080x1920 or similar
                    best_file = next((f["link"] for f in files if f["width"] >= 720), files[0]["link"] if files else None)
                    if best_file:
                        video_urls.append(best_file)
                return video_urls
            return []
    except Exception as e:
        logger.error(f"Pexels Search Failed: {e}")
        return []

def create_subtitle_file(script_text: str, total_duration: float, work_dir: str) -> str:
    """Creates a simple .ass subtitle file with professional styling."""
    sub_path = os.path.join(work_dir, "subs.ass")
    
    # Split script into chunks (roughly 5-7 words each)
    words = script_text.split()
    chunks = []
    chunk_size = 6
    for i in range(0, len(words), chunk_size):
        chunks.append(" ".join(words[i:i+chunk_size]))
    
    if not chunks: chunks = [script_text]
    
    duration_per_chunk = total_duration / len(chunks)
    
    with open(sub_path, "w", encoding="utf-8") as f:
        f.write("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n")
        f.write("[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n")
        # Yellow neon style: &H0000FFFF (Yellow), Border 2, Shadow 1, Alignment 2 (Bottom Center)
        f.write("Style: Default,Arial,70,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,3,2,2,30,30,300,1\n\n")
        f.write("[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
        
        for i, text in enumerate(chunks):
            start_sec = i * duration_per_chunk
            end_sec = (i + 1) * duration_per_chunk
            
            def format_time(s):
                h = int(s // 3600)
                m = int((s % 3600) // 60)
                sec = s % 60
                return f"{h}:{m:02d}:{sec:05.2f}"
            
            f.write(f"Dialogue: 0,{format_time(start_sec)},{format_time(end_sec)},Default,,0,0,0,,{text}\n")
            
    return sub_path

async def assemble_pro_reel(
    script_text: str, 
    topic: str, 
    image_prompts: List[str] = None, 
    search_terms: List[str] = None, 
    language: str = "English",
    voice_id: Optional[str] = None, 
    logo_url: Optional[str] = None,
    subtitles_text: Optional[str] = None
) -> Optional[str]:
    """Master Pipeline: Research -> Script -> Assets -> TTS -> BGM -> FFmpeg Sync -> Final Reel."""
    base_uploads = os.path.join(os.getcwd(), "uploads", "social")
    reel_id = secrets.token_hex(6)
    work_dir = os.path.join(base_uploads, f"pro_work_{reel_id}")
    os.makedirs(work_dir, exist_ok=True)
    
    image_prompts = image_prompts or []
    search_terms = search_terms or [topic]
    # Use provided subtitles_text or fall back to narration script
    sub_text = subtitles_text or script_text
    
    # Language mapping for gTTS
    lang_map = {
        "Hindi": "hi",
        "English": "en",
        "Spanish": "es",
        "French": "fr",
        "Bengali": "bn",
        "Marathi": "mr",
        "Gujarati": "gu",
        "Tamil": "ta",
        "Telugu": "te",
        "Kannada": "kn"
    }
    tts_lang = lang_map.get(language, "en")
    
    try:
        # 1. Voiceover (ElevenLabs)
        logger.info(f"Step 1: Generating Pro Voiceover (Voice: {voice_id}, Lang: {language})...")
        audio_path = await generate_elevenlabs_voiceover(script_text, work_dir, voice_id=voice_id)
        if not audio_path:
            logger.warning(f"ElevenLabs failed, using basic gTTS fallback (Lang: {tts_lang})...")
            from gtts import gTTS
            tts = gTTS(text=script_text, lang=tts_lang)
            audio_path = os.path.join(work_dir, "voice.mp3")
            tts.save(audio_path)
            
        # 2. Get Audio Duration
        probe_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", audio_path]
        probe_proc = subprocess.run(probe_cmd, capture_output=True, text=True)
        total_duration = float(probe_proc.stdout.strip()) if probe_proc.returncode == 0 else 15.0
        
        # 3. Fetch Scene Assets (Videos or AI Images)
        # We aim for ~7 scenes or based on search_terms length
        target_scenes = len(search_terms) if len(search_terms) > 2 else 7
        segment_dur = total_duration / target_scenes
        
        video_paths = []
        logger.info(f"Step 2: Fetching assets for {target_scenes} scenes...")
        
        async with httpx.AsyncClient() as client:
            for i in range(target_scenes):
                term = search_terms[i] if i < len(search_terms) else topic
                prompt = image_prompts[i] if i < len(image_prompts) else term
                
                v_path = os.path.join(work_dir, f"scene_{i}.mp4")
                asset_found = False
                
                # Try Pexels Video first
                logger.info(f"Scene {i}: Searching Pexels for '{term}'")
                v_urls = await search_pexels_videos(term, count=1)
                if v_urls:
                    try:
                        v_res = await client.get(v_urls[0], timeout=30.0)
                        if v_res.status_code == 200:
                            with open(v_path, "wb") as f:
                                f.write(v_res.content)
                            video_paths.append(v_path)
                            asset_found = True
                            logger.info(f"Scene {i}: Video found.")
                    except: pass
                
                # Fallback to AI Image + Convert to static video segment
                if not asset_found:
                    logger.info(f"Scene {i}: No video, generating AI image for '{prompt}'")
                    # Pollinations is fast and free
                    encoded_prompt = urllib.parse.quote(prompt)
                    img_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1080&height=1920&nologo=true&seed={secrets.token_hex(4)}&model=turbo"
                    
                    try:
                        img_res = await client.get(img_url, timeout=30.0)
                        if img_res.status_code == 200:
                            img_path = os.path.join(work_dir, f"scene_{i}.jpg")
                            with open(img_path, "wb") as f:
                                f.write(img_res.content)
                            
                            # Convert image to a video segment of segment_dur
                            conv_cmd = [
                                "ffmpeg", "-y", "-loop", "1", "-i", img_path,
                                "-c:v", "libx264", "-t", str(segment_dur + 0.5), # extra bit for safety
                                "-pix_fmt", "yuv420p", "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
                                v_path
                            ]
                            subprocess.run(conv_cmd, capture_output=True)
                            if os.path.exists(v_path):
                                video_paths.append(v_path)
                                asset_found = True
                                logger.info(f"Scene {i}: AI Image segment created.")
                    except Exception as e:
                        logger.error(f"Scene {i}: Asset failure: {e}")

        # Final safety check
        if not video_paths:
            logger.error("Failed to get any background assets.")
            return None
            
        # 4. Background Music (BGM)
        logger.info(f"Step 3: Fetching Cinematic BGM...")
        bgm_path = os.path.join(work_dir, "bgm.mp3")
        async with httpx.AsyncClient() as client:
            bgm_res = await client.get(DEFAULT_BGM_URL, timeout=30.0, follow_redirects=True)
            if bgm_res.status_code == 200:
                with open(bgm_path, "wb") as f:
                    f.write(bgm_res.content)
            else: bgm_path = None
                
        # 5. Create Subtitles
        logger.info(f"Step 4: Generating Dynamic Subtitles...")
        sub_path = create_subtitle_file(sub_text, total_duration, work_dir)
        
        # 6. Final FFmpeg Assembly
        safe_sub_path = sub_path.replace("\\", "/").replace(":", "\\:")
        output_filename = f"pro_reel_{reel_id}.mp4"
        output_path = os.path.join(base_uploads, output_filename)
        
        input_args = []
        filter_parts = []
        for i, v_p in enumerate(video_paths):
            input_args.extend(["-i", v_p])
            filter_parts.append(f"[{i}:v]trim=duration={segment_dur},setpts=PTS-STARTPTS,scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,fade=t=in:st=0:d=0.5,fade=t=out:st={segment_dur-0.5}:d=0.5[v{i}];")
        
        v_concat = "".join([f"[v{i}]" for i in range(len(video_paths))])
        filter_parts.append(f"{v_concat}concat=n={len(video_paths)}:v=1:a=0[v_bg];")
        filter_parts.append(f"[v_bg]ass='{safe_sub_path}'[v_sub];")
        
        # Logo handling
        logo_path = None
        if logo_url:
            try:
                logo_path = os.path.join(work_dir, "logo.png")
                async with httpx.AsyncClient() as client:
                    l_res = await client.get(logo_url, timeout=10.0)
                    if l_res.status_code == 200:
                        with open(logo_path, "wb") as f: f.write(l_res.content)
                    else: logo_path = None
            except: logo_path = None

        audio_inputs = ["-i", audio_path]
        if bgm_path:
            audio_inputs.extend(["-i", bgm_path])
            filter_parts.append(f"[{len(video_paths)}:a]volume=1.5[a1]; [{len(video_paths)+1}:a]volume=0.15[a2]; [a1][a2]amix=inputs=2:duration=first[a]")
        else:
            filter_parts.append(f"[{len(video_paths)}:a]volume=1.5[a]")

        if logo_path:
            input_args.extend(["-i", logo_path])
            logo_idx = len(video_paths) + (2 if bgm_path else 1)
            filter_parts.append(f";[v_sub][{logo_idx}:v]overlay=W-w-50:H-h-50[v]")
        else:
            filter_parts.append(f";[v_sub]copy[v]")

        cmd = ["ffmpeg", "-y"]
        cmd.extend(input_args)
        cmd.extend(audio_inputs)
        cmd.extend([
            "-filter_complex", "".join(filter_parts),
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
            "-t", str(total_duration),
            output_path
        ])
        
        logger.info(f"Running Pro FFmpeg assembly...")
        process = subprocess.run(cmd, cwd=work_dir, capture_output=True, text=True)
        return f"/uploads/social/{output_filename}" if process.returncode == 0 else None
            
    except Exception as e:
        logger.error(f"Pro Reel Pipeline failed: {e}")
        return None

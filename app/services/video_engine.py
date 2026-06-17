import os
import re
import asyncio
import httpx
import secrets
import subprocess
import logging
import urllib.parse
from typing import List, Optional, Dict
from app.core.config import settings
from app.services.llm import generate_simple_response

logger = logging.getLogger(__name__)

def validate_video_asset(v_path: str, duration: float, work_dir: str, fallback_prefix: str = "fallback") -> str:
    """Ensures a video exists, is valid, and has a size > 1000 bytes. 
    Otherwise, generates a beautiful vertical aesthetic fallback video clip."""
    if os.path.exists(v_path) and os.path.getsize(v_path) > 1000:
        return v_path
    
    logger.warning(f"Validation Layer: Video asset {v_path} is missing or corrupted. Compiling a solid visual vertical fallback.")
    fallback_path = os.path.join(work_dir, f"{fallback_prefix}_{secrets.token_hex(4)}_valid.mp4")
    
    # Compile 1080x1920 solid dark blue-gray aesthetic vertical clip at 30 FPS using FFmpeg's color filter
    cmd = [
        "ffmpeg", "-y", "-nostdin",
        "-f", "lavfi", "-i", f"color=c=0x1a1a2e:s=1080x1920:d={duration}",
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-shortest",
        fallback_path
    ]
    res = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL)
    if res.returncode == 0 and os.path.exists(fallback_path) and os.path.getsize(fallback_path) > 1000:
        return fallback_path
    
    # Emergency write of an empty but existing file if FFmpeg fails completely (to prevent loop crash)
    with open(fallback_path, "wb") as f:
        f.write(b"")
    return fallback_path

def validate_audio_asset(a_path: str, duration: float, work_dir: str, fallback_prefix: str = "fallback") -> str:
    """Ensures a voice audio asset exists and is non-empty.
    Otherwise, generates a silent audio segment of the matching duration."""
    if os.path.exists(a_path) and os.path.getsize(a_path) > 100:
        return a_path
        
    logger.warning(f"Validation Layer: Audio asset {a_path} is missing or corrupted. Generating silence track.")
    fallback_path = os.path.join(work_dir, f"{fallback_prefix}_{secrets.token_hex(4)}_valid.mp3")
    
    cmd = [
        "ffmpeg", "-y", "-nostdin",
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-t", str(duration),
        "-c:a", "libmp3lame",
        fallback_path
    ]
    res = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL)
    if res.returncode == 0 and os.path.exists(fallback_path):
        return fallback_path
        
    with open(fallback_path, "wb") as f:
        f.write(b"")
    return fallback_path

# Constants
PEXELS_SEARCH_URL = "https://api.pexels.com/videos/search"
ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech"
DEFAULT_BGM_URL = "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3" # More stable public MP3 URL

def generate_silent_audio(duration: float, work_dir: str, filename: str = "silence.mp3") -> str:
    """Generates a silent audio segment of specified duration using FFmpeg."""
    silent_path = os.path.join(work_dir, filename)
    cmd = [
        "ffmpeg", "-y", "-nostdin",
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-t", str(duration),
        "-c:a", "libmp3lame",
        silent_path
    ]
    res = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL)
    if res.returncode == 0 and os.path.exists(silent_path):
        return silent_path
    # Fallback to creating a tiny empty file if FFmpeg fails
    with open(silent_path, "wb") as f:
        f.write(b"")
    return silent_path


def clean_and_normalize_hindi_text(text: str) -> str:
    if not text:
        return ""
        
    def num_to_hindi_words(num: int) -> str:
        hindi_0_to_100 = [
            "शून्य", "एक", "दो", "तीन", "चार", "पांच", "छह", "सात", "आठ", "नौ", "दस",
            "ग्यारह", "बारह", "तेरह", "चौदह", "पंद्रह", "सोलह", "सत्रह", "अठारह", "उन्नीस", "बीस",
            "इक्कीस", "बाईस", "तेईस", "चौबीस", "पच्चीस", "छब्बीस", "सत्ताईस", "अठ्ठाईस", "उनतीस", "तीस",
            "इकतीस", "बत्तीस", "तेतीस", "चौंतीस", "पैंतीस", "छत्तीस", "सैंतीस", "अड़तीस", "उनतालीस", "चालीस",
            "इकतालीस", "बयालीस", "तियालीस", "चियालीस", "पैंतालीस", "छियालीस", "सैंतालीस", "अड़तालीस", "उनचास", "पचास",
            "इक्यावन", "बावन", "तिरेपन", "चौवन", "पचपन", "छप्पन", "सत्तावन", "अठावन", "उनसठ", "साठ",
            "इकसठ", "बासठ", "तिरसठ", "चौंसठ", "पैंसठ", "छियासठ", "सरसठ", "अड़सठ", "उनहत्तर", "सत्तर",
            "इकहत्तर", "बहत्तर", "तिहत्तर", "चौहत्तर", "पचहत्तर", "छिहत्तर", "सतहत्तर", "अठहत्तर", "उन्यासी", "अस्सी",
            "इक्यासी", "बयासी", "तिरासी", "चौरासी", "पचासी", "छियासी", "सत्तासी", "अठासी", "नवासी", "नब्बे",
            "इक्यान्वे", "बयान्वे", "तिरान्वे", "चौरान्वे", "पच्चान्वे", "छियान्वे", "सत्तान्वे", "अठान्वे", "निन्यानवे", "सौ"
        ]
        
        if num < 0:
            return "ऋण " + num_to_hindi_words(abs(num))
        if num <= 100:
            return hindi_0_to_100[num]
            
        parts = []
        
        # Crores (1,00,00,000)
        if num >= 10000000:
            crore_val = num // 10000000
            num %= 10000000
            parts.append(f"{num_to_hindi_words(crore_val)} करोड़")
            
        # Lakhs (1,00,000)
        if num >= 100000:
            lakh_val = num // 100000
            num %= 100000
            parts.append(f"{num_to_hindi_words(lakh_val)} लाख")
            
        # Thousands (1,000)
        if num >= 1000:
            thousand_val = num // 1000
            num %= 1000
            parts.append(f"{num_to_hindi_words(thousand_val)} हजार")
            
        # Hundreds (100)
        if num >= 100:
            hundred_val = num // 100
            num %= 100
            if hundred_val == 1:
                parts.append("सौ")
            else:
                parts.append(f"{hindi_0_to_100[hundred_val]} सौ")
                
        if num > 0:
            parts.append(hindi_0_to_100[num])
            
        return " ".join(parts)

    # Convert decimals first, e.g., "12.5" -> "बारह दशमलव पांच"
    def replace_decimal(match):
        integer_part = int(match.group(1))
        decimal_digits = match.group(2)
        
        int_words = num_to_hindi_words(integer_part)
        dec_words = " ".join([num_to_hindi_words(int(d)) for d in decimal_digits])
        return f"{int_words} दशमलव {dec_words}"
        
    text = re.sub(r'(\d+)\.(\d+)', replace_decimal, text)
    
    # Convert integers
    def replace_integer(match):
        val = int(match.group(0))
        return num_to_hindi_words(val)
        
    text = re.sub(r'\b\d+\b', replace_integer, text)

    # Symbol replacements
    symbol_map = {
        "%": " प्रतिशत",
        "+": " प्लस",
        "=": " बराबर",
        "&": " और",
        " km": " किलोमीटर",
        "km": " किलोमीटर",
        " kg": " किलोग्राम",
        "kg": " किलोग्राम",
        " m": " मीटर",
        " cm": " सेंटीमीटर",
        " mm": " मिलीमीटर",
    }
    for sym, word in symbol_map.items():
        text = text.replace(sym, word)

    # English acronyms/terms to spoken Hindi words
    english_to_hindi_phonetic = {
        "AI": "एआई",
        "RAG": "रैग",
        "API": "एपीआई",
        "PDF": "पीडीएफ",
        "LLM": "एलएलएम",
        "YT": "वाईटी",
        "URL": "यूआरएल",
        "BPSC": "बीपीएससी",
        "UPSC": "यूपीएससी",
        "MCQ": "एमसीक्यू",
        "MCQS": "एमसीक्यू",
        "GK": "जीके",
        "GS": "जीएस",
        "IT": "आईटी",
        "IP": "आईपी",
        "PC": "पीसी",
        "TV": "टीवी",
        "SMS": "एसएमएस",
        "OTP": "ओटीपी",
        "GB": "जीबी",
        "MB": "एमबी",
        "KB": "केबी",
        "SQL": "एसक्यूएल",
        "UI": "यूआई",
        "UX": "यूएक्स",
        "CSS": "सीएसएस",
        "HTML": "एचटीएमएल",
        "JS": "जेएस",
        "VS": "वर्सेस",
        "GST": "जीएसटी",
        "AI REELS": "एआई रील्स",
        "REEL": "रील",
        "REELS": "रील्स",
        "VIDEO": "वीडियो",
        "VIDEOS": "वीडियो",
        "AUDIO": "ऑडियो",
        "IMAGE": "इमेज",
        "IMAGES": "इमेज",
        "GENERATE": "जेनरेट",
        "GENERATOR": "जेनरेटर",
        "PROMPT": "प्रॉम्प्ट",
        "PROMPTS": "प्रॉम्प्ट्स",
        "STUDIO": "स्टूडियो",
        "DASHBOARD": "डैशबोर्ड",
        "SYSTEM": "सिस्टम",
        "DATA": "डेटा",
        "DATABASE": "डेटाबेस",
        "SCIENCE": "साइंस",
        "MATH": "मैथ",
        "MATHS": "मैथ्स",
        "WEBSITE": "वेबसाइट",
        "INTERNET": "इंटरनेट",
        "MOBILE": "मोबाइल",
        "APP": "ऐप",
        "APPS": "ऐप्स",
        "GOOGLE": "गूगल",
        "FACEBOOK": "फेसबुक",
        "YOUTUBE": "यूट्यूब",
        "INSTAGRAM": "इंस्टाग्राम",
        "WHATSAPP": "व्हाट्सएप",
    }
    
    for eng, hin in english_to_hindi_phonetic.items():
        pattern = re.compile(r'\b' + re.escape(eng) + r'\b', re.IGNORECASE)
        text = pattern.sub(hin, text)
        
    return text


async def generate_elevenlabs_voiceover(
    text: str, 
    work_dir: str, 
    voice_id: Optional[str] = None, 
    language: Optional[str] = None
) -> Optional[str]:
    """Generates high-quality voiceover using ElevenLabs with automatic gTTS fallback and silence generation."""
    
    # 1. Clean bracketed pacing/directing tags
    cleaned_text = re.sub(r'\[[^\]]*\]', '', text)
    cleaned_text = re.sub(r'\.\.\.+', ', ', cleaned_text)
    cleaned_text = cleaned_text.replace('—', ', ').replace('–', ', ')
    cleaned_text = re.sub(r'\s+', ' ', cleaned_text).strip()
    
    # Apply Hindi normalization if language is Hindi
    if language and language.lower() == "hindi":
        cleaned_text = clean_and_normalize_hindi_text(cleaned_text)
    
    # 2. Check if there are any actual spoken words
    only_words = re.sub(r'[\s.,\/#!$%\^&\*;:{}=\-_`~()।|?]+', '', cleaned_text)
    if not only_words.strip():
        logger.info("Dialogue has no spoken words (only pacing/silence tags). Generating silent track.")
        # Generate a silent 2.0-second track
        return generate_silent_audio(2.0, work_dir, f"silence_{secrets.token_hex(4)}.mp3")

    # Determine language mapping for gTTS
    lang_map = {"Hindi": "hi", "English": "en", "Spanish": "es", "French": "fr", "Bengali": "bn", "Marathi": "mr"}
    gtts_lang = lang_map.get(language, "en") if language else "en"
    
    # 3. Try ElevenLabs
    api_key = settings.ELEVENLABS_API_KEY
    if api_key:
        vid = voice_id or "pNInz6obpgDQGcFmaJgB" # Adam voice (default pro)
        url = f"{ELEVENLABS_TTS_URL}/{vid}"
        headers = {
            "xi-api-key": api_key,
            "Content-Type": "application/json"
        }
        model_id = "eleven_flash_v2_5"
        data = {
            "text": cleaned_text,
            "model_id": model_id, 
            "voice_settings": {
                "stability": 0.75,       # Increased stability (0.75) for smooth, clear, professional voiceover
                "similarity_boost": 0.85, 
                "style": 0.15,            # Slight style boost for premium emotional expression
                "use_speaker_boost": True
            }
        }
        try:
            async with httpx.AsyncClient() as client:
                res = await client.post(url, json=data, headers=headers, timeout=60.0)
                if res.status_code == 200:
                    audio_path = os.path.join(work_dir, f"voice_{secrets.token_hex(4)}.mp3")
                    with open(audio_path, "wb") as f:
                        f.write(res.content)
                    return audio_path
                else:
                    logger.error(f"ElevenLabs Error: {res.status_code} - {res.text}. Falling back to gTTS.")
        except Exception as e:
            logger.error(f"ElevenLabs Request Failed: {e}. Falling back to gTTS.")
            
    # 4. Fallback to gTTS if ElevenLabs is not configured or failed
    logger.info(f"Using basic gTTS fallback (lang={gtts_lang}) for text: {cleaned_text[:50]}...")
    try:
        from gtts import gTTS
        tts = gTTS(text=cleaned_text, lang=gtts_lang)
        audio_path = os.path.join(work_dir, f"voice_{secrets.token_hex(4)}.mp3")
        tts.save(audio_path)
        return audio_path
    except Exception as ge:
        logger.error(f"gTTS generation failed: {ge}")
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
    """Creates a premium CapCut-style .ass subtitle file with bold uppercase text and outline styling."""
    sub_path = os.path.join(work_dir, "subs.ass")
    
    # Split script into clean uppercase chunks (2-3 words each for rapid, high-retention pacing!)
    words = [w.strip().upper() for w in script_text.split() if w.strip()]
    chunks = []
    chunk_size = 3  # 3 words per chunk for rapid viral retention pacing
    for i in range(0, len(words), chunk_size):
        chunks.append(" ".join(words[i:i+chunk_size]))
    
    if not chunks:
        chunks = [script_text.upper()]
    
    duration_per_chunk = total_duration / len(chunks)
    
    with open(sub_path, "w", encoding="utf-8") as f:
        f.write("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n")
        f.write("[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n")
        # CAPCUT STYLE: Arial font for universal multilingual support, size 85, Primary: Yellow (&H0000FFFF), Outline: Black (&H00000000) with thickness 5, Shadow 2, Alignment 2 (Bottom center)
        f.write("Style: Default,Arial,85,&H0000FFFF,&H0000FFFF,&H00000000,&H00000000,-1,0,0,0,100,100,1,0,1,5,2,2,30,30,350,1\n\n")
        f.write("[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
        
        for i, text in enumerate(chunks):
            start_sec = i * duration_per_chunk
            end_sec = (i + 1) * duration_per_chunk
            
            # Subtitle animations: subtle scaling pop effect on transition!
            # \t(0, 100, \fscx120\fscy120) pops the text scale slightly on start
            animated_text = f"{{\\fscx100\\fscy100\\t(0,100,\\fscx120\\fscy120)\\t(100,200,\\fscx100\\fscy100)}}{text}"
            
            def format_time(s):
                h = int(s // 3600)
                m = int((s % 3600) // 60)
                sec = s % 60
                return f"{h}:{m:02d}:{sec:05.2f}"
            
            f.write(f"Dialogue: 0,{format_time(start_sec)},{format_time(end_sec)},Default,,0,0,0,,{animated_text}\n")
            
    return sub_path

def create_scene_subtitles_pro(scenes: List[dict], work_dir: str) -> str:
    """Creates a premium CapCut-style .ass subtitle file with bold uppercase 3-word chunks synchronized per scene."""
    sub_path = os.path.join(work_dir, "subs.ass")
    
    with open(sub_path, "w", encoding="utf-8") as f:
        f.write("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n")
        f.write("[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n")
        # CAPCUT STYLE: Arial font for universal multilingual support, size 85, Primary: Yellow (&H0000FFFF), Outline: Black (&H00000000) with thickness 5, Shadow 2, Alignment 2 (Bottom center)
        f.write("Style: Default,Arial,85,&H0000FFFF,&H0000FFFF,&H00000000,&H00000000,-1,0,0,0,100,100,1,0,1,5,2,2,30,30,350,1\n\n")
        f.write("[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
        
        def format_time(s):
            h = int(s // 3600)
            m = int((s % 3600) // 60)
            sec = s % 60
            return f"{h}:{m:02d}:{sec:05.2f}"
            
        current_time = 0.0
        for i, s in enumerate(scenes):
            dialogue = (s.get("script") or s.get("dialogue") or "").strip()
            scene_dur = float(s.get("duration") or s.get("suggested_duration") or 5.0)
            
            if dialogue:
                # Clean and split into 3-word chunks (in uppercase)
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

def build_xfade_filter_complex(video_count: int, durations: List[float], trans_dur: float = 0.5) -> tuple:
    """
    Generates a cascading FFmpeg filter_complex and the final video output label
    using high-end transition effects from xfade.
    """
    if video_count <= 1:
        return "[0:v]copy[v_out];", "[v_out]"
        
    # Curated pool of highly aesthetic, pro-editor transitions
    transitions = [
        'smoothleft', 'smoothright', 'smoothup', 'smoothdown',
        'radial', 'zoomin', 'rectcrop', 'circlecrop',
        'hlslice', 'hrslice', 'squeezeh', 'squeezev',
        'fadeblack', 'fadewhite', 'dissolve', 'pixelize'
    ]
    
    filter_parts = []
    current_label = "[0:v]"
    
    for i in range(video_count - 1):
        next_label = f"[{i+1}:v]"
        out_label = f"[v_trans_{i}]"
        
        # Calculate offset using sum of preceding durations minus overlaps
        offset = sum(durations[:i+1]) - (i+1) * trans_dur
        trans_name = transitions[i % len(transitions)]
        
        filter_parts.append(f"{current_label}{next_label}xfade=transition={trans_name}:duration={trans_dur}:offset={offset:.2f}{out_label}")
        current_label = out_label
        
    return "; ".join(filter_parts) + ";", current_label

async def assemble_pro_reel(
    script_text: str, 
    topic: str, 
    image_prompts: List[str] = None, 
    search_terms: List[str] = None, 
    language: str = "English",
    voice_id: Optional[str] = None, 
    logo_url: Optional[str] = None,
    subtitles_text: Optional[str] = None,
    source_plan: List[str] = None,
    bgm_style: str = "cinematic"
) -> Dict:
    """Master Pipeline: Research -> Script -> Assets -> TTS -> BGM -> FFmpeg Sync -> Final Reel."""
    base_uploads = os.path.join(os.getcwd(), "uploads", "social")
    reel_id = secrets.token_hex(6)
    work_dir = os.path.join(base_uploads, f"pro_work_{reel_id}")
    os.makedirs(work_dir, exist_ok=True)
    
    image_prompts = image_prompts or []
    search_terms = search_terms or [topic]
    source_plan = source_plan or ["stock"] * len(search_terms)
    sub_text = subtitles_text or script_text
    target_scenes = len(search_terms)
    
    # BGM Styles Mapping
    bgm_map = {
        "cinematic": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3",
        "energetic": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-2.mp3",
        "corporate": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-3.mp3",
        "dramatic": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-8.mp3"
    }
    bgm_url = bgm_map.get(bgm_style.lower(), bgm_map["cinematic"])
    
    # Language mapping for gTTS
    lang_map = { "Hindi": "hi", "English": "en", "Spanish": "es", "French": "fr", "Bengali": "bn" }
    tts_lang = lang_map.get(language, "en")
    
    try:
        # 1. Voiceover (ElevenLabs)
        logger.info(f"Step 1: Generating Pro Voiceover (Voice: {voice_id}, Lang: {language})...")
        audio_path = await generate_elevenlabs_voiceover(script_text, work_dir, voice_id=voice_id, language=language)
        if not audio_path:
            logger.warning(f"Voiceover generation failed completely, generating silence fallback...")
            audio_path = generate_silent_audio(10.0, work_dir, "voice.mp3")
            
        # 2. Get Audio Duration
        probe_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", audio_path]
        probe_proc = subprocess.run(probe_cmd, capture_output=True, text=True)
        total_duration = float(probe_proc.stdout.strip()) if probe_proc.returncode == 0 else 15.0
        
        # Split script_text into target_scenes sentences/chunks for exact timeline match
        sentences = re.split(r'(?<=[.!?])\s+', script_text.strip())
        sentences = [s for s in sentences if s.strip()]
        
        if len(sentences) < target_scenes:
            words = script_text.split()
            words_per_scene = max(1, len(words) // target_scenes)
            scene_scripts = []
            for i in range(target_scenes):
                start_w = i * words_per_scene
                end_w = (i + 1) * words_per_scene if i < target_scenes - 1 else len(words)
                scene_scripts.append(" ".join(words[start_w:end_w]))
        else:
            chunk_size = len(sentences) // target_scenes
            scene_scripts = []
            for i in range(target_scenes):
                start_s = i * chunk_size
                end_s = (i + 1) * chunk_size if i < target_scenes - 1 else len(sentences)
                scene_scripts.append(" ".join(sentences[start_s:end_s]))

        # 3. Fetch Scene Assets (PARALLEL with Throttling to avoid 429)
        raw_assets_ordered = [None] * target_scenes
        scene_data_ordered = [None] * target_scenes
        
        # Limit parallel Pollinations requests to 4 for hyper-fast 15-scene generation
        sem = asyncio.Semaphore(4)
        
        async def fetch_scene_asset(idx: int, client: httpx.AsyncClient):
            async with sem:
                term = search_terms[idx]
                prompt = image_prompts[idx] if idx < len(image_prompts) else term
                plan_source = source_plan[idx] if idx < len(source_plan) else "stock"
                
                asset_found = False
                raw_path = None
                img_url = None
                
                if plan_source == "stock":
                    v_urls = await search_pexels_videos(term, count=1)
                    if v_urls:
                        try:
                            v_res = await client.get(v_urls[0], timeout=30.0)
                            if v_res.status_code == 200:
                                raw_path = os.path.join(work_dir, f"raw_scene_{idx}.mp4")
                                with open(raw_path, "wb") as f: f.write(v_res.content)
                                asset_found = True
                        except: pass
                
                if not asset_found:
                    # Flux AI with Fallback to Turbo for speed/reliability
                    encoded_prompt = urllib.parse.quote(f"{prompt}, 8k, cinematic lighting, masterpiece")
                    base_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1080&height=1920&nologo=true&seed={secrets.token_hex(4)}"
                    
                    for attempt in range(4):
                        # Use 'turbo' model if 'flux' is hitting 429 repeatedly
                        model = "flux" if attempt < 2 else "turbo"
                        img_url = f"{base_url}&model={model}"
                        
                        try:
                            img_res = await client.get(img_url, timeout=75.0)
                            if img_res.status_code == 200:
                                raw_path = os.path.join(work_dir, f"raw_scene_{idx}.jpg")
                                with open(raw_path, "wb") as f: f.write(img_res.content)
                                asset_found = True
                                break
                            elif img_res.status_code == 429:
                                wait_time = (attempt + 1) * 4
                                logger.warning(f"Pollinations 429 (Model: {model}). Waiting {wait_time}s...")
                                await asyncio.sleep(wait_time)
                        except:
                            await asyncio.sleep(3)
                
                if asset_found:
                    raw_assets_ordered[idx] = {
                        "type": "video" if raw_path.endswith(".mp4") else "image",
                        "path": raw_path
                    }
                    # Store for return
                    scene_data_ordered[idx] = {
                        "id": idx + 1,
                        "thumb": img_url if raw_path.endswith(".jpg") else "https://images.pexels.com/photos/3183150/pexels-photo-3183150.jpeg?auto=compress&cs=tinysrgb&w=300",
                        "script": scene_scripts[idx] if idx < len(scene_scripts) else f"Scene {idx+1} Narration",
                        "effect": "zoom_in"
                    }

        logger.info(f"Step 2: Fetching {target_scenes} assets (Throttled Parallel)...")
        async with httpx.AsyncClient() as client:
            tasks = [fetch_scene_asset(i, client) for i in range(target_scenes)]
            await asyncio.gather(*tasks)

        # Filter out succeeded assets
        succeeded_indices = [i for i, asset in enumerate(raw_assets_ordered) if asset is not None]
        if not succeeded_indices:
            logger.error("No video segments were created.")
            return None
            
        # Calculate the exact segment duration adjusted for xfade overlap (0.5s per transition)
        num_succeeded = len(succeeded_indices)
        trans_dur = 0.5 if num_succeeded > 1 else 0.0
        final_segment_dur = (total_duration + (num_succeeded - 1) * trans_dur) / num_succeeded
        trans_effect = "fade"
        
        # Render the final clips to the exact duration sequentially to ensure perfect timing
        video_paths_ordered = [None] * target_scenes
        accumulated_time = 0.0
        for idx in succeeded_indices:
            asset = raw_assets_ordered[idx]
            v_path = os.path.join(work_dir, f"scene_{idx}.mp4")
            
            # Disable visual filters, transitions, and zoompans to ensure maximum visual clarity
            trans_filter = ""
            enhancement = ""
            
            if asset["type"] == "image":
                # Static loop scaled and cropped to 1080x1920 (no zoompan blur)
                conv_cmd = [
                    "ffmpeg", "-y", "-loop", "1", "-i", asset["path"],
                    "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1",
                    "-c:v", "libx264", "-t", str(final_segment_dur), "-pix_fmt", "yuv420p", "-r", "30",
                    v_path
                ]
                sub_res = subprocess.run(conv_cmd, capture_output=True)
                if sub_res.returncode == 0:
                    video_paths_ordered[idx] = v_path
            else:
                # Video scaled and cropped to 1080x1920
                conv_cmd = [
                    "ffmpeg", "-y", "-stream_loop", "-1", "-i", asset["path"],
                    "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1",
                    "-t", str(final_segment_dur), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30",
                    v_path
                ]
                sub_res = subprocess.run(conv_cmd, capture_output=True)
                if sub_res.returncode == 0:
                    video_paths_ordered[idx] = v_path
            
            # Asset Validation Layer: Make sure video file is correct size and exists
            validated_v_path = validate_video_asset(v_path, final_segment_dur, work_dir, f"scene_{idx}_pro")
            video_paths_ordered[idx] = validated_v_path
            
            start_time = accumulated_time
            end_time = accumulated_time + final_segment_dur
            accumulated_time += final_segment_dur
            
            # Enrich scene data payload with the full temporal timeline schema
            if scene_data_ordered[idx]:
                scene_data_ordered[idx].update({
                    "scene_id": idx + 1,
                    "start": round(start_time, 2),
                    "end": round(end_time, 2),
                    "duration": round(final_segment_dur, 2),
                    "video": f"/uploads/social/pro_work_{reel_id}/scene_{idx}.mp4",
                    "videoThumb": f"/uploads/social/pro_work_{reel_id}/scene_{idx}.mp4",
                    "audio": f"/uploads/social/pro_work_{reel_id}/voice.mp3",
                    "transition": trans_effect,
                    "motion": "zoom_in",
                    "voice": voice_id or "adam"
                })

        video_paths = [p for p in video_paths_ordered if p is not None]
        if not video_paths:
            logger.error("Failed to render video segments.")
            return None
        
        # 4. Background Music (BGM)
        bgm_path = os.path.join(work_dir, "bgm.mp3")
        async with httpx.AsyncClient() as client:
            try:
                bgm_res = await client.get(bgm_url, timeout=30.0, follow_redirects=True)
                if bgm_res.status_code == 200:
                    with open(bgm_path, "wb") as f: f.write(bgm_res.content)
                else: bgm_path = None
            except: bgm_path = None
                
        # 5. Create Subtitles (synchronized by scene)
        scenes_for_subtitles = [s for s in scene_data_ordered if s is not None]
        sub_path = create_scene_subtitles_pro(scenes_for_subtitles, work_dir)
        
        # 6. Final Assembly
        safe_sub_path = sub_path.replace("\\", "/").replace(":", "\\:")
        output_filename = f"pro_reel_{reel_id}.mp4"
        output_path = os.path.join(base_uploads, output_filename)
        
        input_args = []
        pre_filters = []
        for i, v_p in enumerate(video_paths):
            input_args.extend(["-i", v_p])
            pre_filters.append(f"[{i}:v]trim=duration={final_segment_dur:.2f},setpts=PTS-STARTPTS,scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1[v_pre{i}];")
            
        # Build xfade cascade complex
        xfade_complex, final_v_label = build_xfade_filter_complex(
            video_count=len(video_paths),
            durations=[final_segment_dur] * len(video_paths),
            trans_dur=trans_dur
        )
        
        # Rewrite xfade complex inputs to map to our pre-filtered labels [v_preX]
        for i in range(len(video_paths)):
            xfade_complex = xfade_complex.replace(f"[{i}:v]", f"[v_pre{i}]")
            
        filter_parts = pre_filters + [xfade_complex]
        filter_parts.append(f"{final_v_label}ass='{safe_sub_path}'[v_sub];")
        
        voice_idx = len(video_paths)
        filter_parts.append(f"[{voice_idx}:a]highpass=f=60,volume=0.95[a_pro];")
        
        audio_inputs = ["-i", audio_path]
        if bgm_path:
            audio_inputs.extend(["-i", bgm_path])
            filter_parts.append(f"[a_pro]volume=1.0[a1]; [{voice_idx+1}:a]volume=0.05[a2]; [a1][a2]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,alimiter=limit=0.95[a];")
        else:
            filter_parts.append(f"[a_pro]volume=1.0[a];")
        filter_parts.append(f"[v_sub]copy[v]")

        cmd = ["ffmpeg", "-y"]
        cmd.extend(input_args)
        cmd.extend(audio_inputs)
        cmd.extend([
            "-filter_complex", "".join(filter_parts),
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30", "-t", str(total_duration),
            output_path
        ])
        
        logger.info(f"Running Final Cinematic Assembly...")
        assembly_log_path = os.path.join(work_dir, "ffmpeg_assembly.log")
        with open(assembly_log_path, "w", encoding="utf-8") as log_file:
            final_res = subprocess.run(cmd, cwd=work_dir, stdout=log_file, stderr=log_file, stdin=subprocess.DEVNULL)
        if final_res.returncode != 0:
            logger.warning("Cinematic Reel assembly failed (likely xfade filter complex crash). Retrying with safe standard concat + mix fallback...")
            list_path = os.path.join(work_dir, "concat_list.txt")
            with open(list_path, "w", encoding="utf-8") as f:
                for v_p in video_paths:
                    v_fixed = v_p.replace('\\', '/')
                    f.write(f"file '{v_fixed}'\n")
                    
            temp_video = os.path.join(work_dir, "temp_video.mp4")
            concat_cmd = [
                "ffmpeg", "-y", "-nostdin",
                "-f", "concat", "-safe", "0", "-i", list_path,
                "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", "30", "-an",
                temp_video
            ]
            concat_res = subprocess.run(concat_cmd, capture_output=True, stdin=subprocess.DEVNULL)
            if concat_res.returncode != 0:
                logger.error("Safe concat fallback failed during video concatenation.")
                return None
                
            inputs = ["-i", temp_video, "-i", audio_path]
            if bgm_path:
                inputs.extend(["-i", bgm_path])
                fc = (
                    f"[0:v]ass='{safe_sub_path}'[v];"
                    f"[1:a]highpass=f=60,volume=0.95[a_voice];"
                    f"[2:a]volume=0.05[a_bgm];"
                    f"[a_voice][a_bgm]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,alimiter=limit=0.95[a]"
                )
            else:
                fc = (
                    f"[0:v]ass='{safe_sub_path}'[v];"
                    f"[1:a]highpass=f=60,volume=0.95[a]"
                )
            
            fallback_assembly_cmd = (
                ["ffmpeg", "-y", "-nostdin"]
                + inputs
                + ["-filter_complex", fc, "-map", "[v]", "-map", "[a]"]
                + ["-c:v", "libx264", "-preset", "fast", "-crf", "20",
                   "-c:a", "aac", "-b:a", "192k",
                   "-pix_fmt", "yuv420p", "-r", "30",
                   "-t", str(total_duration),
                   output_path]
            )
            fallback_res = subprocess.run(fallback_assembly_cmd, capture_output=True, stdin=subprocess.DEVNULL)
            if fallback_res.returncode != 0:
                logger.error("Safe concat fallback failed during final mixing and assembly.")
                return None
            
        if os.path.exists(output_path):
            logger.info(f"Reel Generated Successfully: {output_filename}")
            return {
                "video_url": f"/uploads/social/{output_filename}",
                "scenes": [s for s in scene_data_ordered if s],
                "bgm_url": bgm_url,
                "voice_id": voice_id
            }
        else:
            return {"video_url": None, "scenes": []}
            
    except Exception as e:
        logger.error(f"Cinematic Reel Pipeline failed: {e}")
        return None

async def assemble_advanced_reel(
    script_text: str, 
    language: str = "English",
    voice_id: Optional[str] = None,
    bgm_style: str = "dramatic"
) -> Dict:
    """
    Pro Pipeline: Full single voiceover -> divide video scenes by audio duration.
    Audio drives timing — video scenes stretch/fit to match audio segments.
    """
    base_uploads = os.path.join(os.getcwd(), "uploads", "social")
    reel_id = secrets.token_hex(6)
    work_dir = os.path.join(base_uploads, f"adv_work_{reel_id}")
    os.makedirs(work_dir, exist_ok=True)

    # ── 1. Parse Script ──────────────────────────────────────────────────────
    scenes = []
    scene_blocks = re.split(r'\U0001f3ac Scene \d+', script_text)
    scene_blocks = [b.strip() for b in scene_blocks if b.strip()]

    for i, block in enumerate(scene_blocks):
        scene_num = i + 1
        dialogue_match = re.search(r'\U0001f399\ufe0f Dialogue:\s*(.*?)(?=\U0001f4f8|$)', block, re.DOTALL)
        dialogue = dialogue_match.group(1).strip() if dialogue_match else ""
        dialogue = dialogue.replace('\u201c', '').replace('\u201d', '').replace('"', '')
        visuals_match = re.search(r'\U0001f4f8 Visuals.*?:\s*(.*?)(?=\U0001f3a5|$)', block, re.DOTALL)
        visuals = visuals_match.group(1).strip() if visuals_match else ""
        scenes.append({"scene_num": scene_num, "dialogue": dialogue, "visuals": visuals})

    if not scenes:
        logger.error("No scenes parsed.")
        return None

    # ── 2. Generate ONE full voiceover (all dialogues joined) ────────────────
    full_dialogue = " ".join([s['dialogue'] for s in scenes if s['dialogue']])
    full_voice_path = os.path.join(work_dir, "full_voice.mp3")

    logger.info("Generating full voiceover...")
    lang_map = {"Hindi": "hi", "English": "en", "Spanish": "es", "French": "fr", "Bengali": "bn", "Marathi": "mr"}
    tts_lang = lang_map.get(language, "en")

    voice_result = await generate_elevenlabs_voiceover(full_dialogue, work_dir, voice_id=voice_id, language=language)
    if voice_result and os.path.exists(voice_result):
        if os.path.exists(full_voice_path):
            os.remove(full_voice_path)
        os.rename(voice_result, full_voice_path)
    else:
        logger.warning("Voiceover generation failed completely, generating silence fallback...")
        generate_silent_audio(15.0, work_dir, "full_voice.mp3")

    # Probe total audio duration
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", full_voice_path],
        capture_output=True, text=True
    )
    total_audio_dur = float(probe.stdout.strip()) if probe.returncode == 0 and probe.stdout.strip() else 60.0
    num_scenes = len(scenes)
    trans_dur = 0.5 if num_scenes > 1 else 0.0
    scene_dur = (total_audio_dur + (num_scenes - 1) * trans_dur) / num_scenes
    trans_effect = "fade"
    effect = "zoom_in"
    logger.info(f"Total audio: {total_audio_dur:.2f}s | {num_scenes} scenes | adjusted scene duration: {scene_dur:.2f}s each")

    # ── 3. Generate images in parallel ──────────────────────────────────────
    img_paths = [None] * len(scenes)
    sem = asyncio.Semaphore(3)

    async def fetch_image(idx: int, scene: dict):
        async with sem:
            user_p = f"Dialogue: {scene['dialogue']}\nVisuals: {scene['visuals']}"
            sys_p = "You are an expert cinematic prompt engineer. Create a highly detailed photorealistic 4K image prompt (MAX 50 words) for AI image generation (English only, no text in image). The visual prompt MUST perfectly match the scene dialogue and visuals description to create a correct, matching, contextually accurate scene."
            try:
                prompt = await generate_simple_response(user_p, sys_p)
            except:
                prompt = scene['visuals'][:150]

            enc = urllib.parse.quote(prompt)
            img_path = os.path.join(work_dir, f"scene_{scene['scene_num']}.jpg")
            base_url = f"https://image.pollinations.ai/prompt/{enc}?width=1080&height=1920&nologo=true&seed={secrets.token_hex(4)}"

            async with httpx.AsyncClient() as client:
                for attempt in range(4):
                    model = "flux" if attempt < 2 else "turbo"
                    try:
                        res = await client.get(f"{base_url}&model={model}", timeout=90.0)
                        if res.status_code == 200:
                            with open(img_path, "wb") as f: f.write(res.content)
                            img_paths[idx] = img_path
                            return
                        elif res.status_code == 429:
                            await asyncio.sleep(5 * (attempt + 1))
                    except:
                        await asyncio.sleep(3)
            # fallback
            try:
                async with httpx.AsyncClient() as client:
                    res = await client.get("https://images.unsplash.com/photo-1677442136019-21780ecad995?auto=format&fit=crop&q=80&w=1080&h=1920", timeout=20.0)
                    if res.status_code == 200:
                        with open(img_path, "wb") as f: f.write(res.content)
                        img_paths[idx] = img_path
            except: pass

    logger.info(f"Fetching {len(scenes)} scene images...")
    await asyncio.gather(*[fetch_image(i, s) for i, s in enumerate(scenes)])

    # ── 4. Render each scene video clip (video only, no audio) ───────────────
    scene_videos = []
    advanced_scenes_data = []
    accumulated_time = 0.0

    for i, scene in enumerate(scenes):
        img_path = img_paths[i]
        if not img_path or not os.path.exists(img_path):
            logger.warning(f"Scene {i+1} image missing, skipping")
            continue

        v_path = os.path.join(work_dir, f"scene_{scene['scene_num']}_v.mp4")
        dur = scene_dur
        frames = int(dur * 30) + 5

        # Disable visual filters, transitions, and zoompans to ensure maximum visual clarity
        trans_filter = ""
        enhancement = ""
        
        vf = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1"

        cmd = [
            "ffmpeg", "-y", "-loop", "1", "-i", img_path,
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-t", str(dur), "-pix_fmt", "yuv420p", "-r", "30",
            v_path
        ]
        result = subprocess.run(cmd, capture_output=True)
        
        # Asset Validation Layer
        validated_v_path = validate_video_asset(v_path, dur, work_dir, f"scene_{i}_adv")
        
        scene_videos.append(validated_v_path)
        
        start_time = accumulated_time
        end_time = accumulated_time + dur
        accumulated_time += dur
        
        advanced_scenes_data.append({
            "id": i + 1,
            "scene_id": i + 1,
            "start": round(start_time, 2),
            "end": round(end_time, 2),
            "duration": round(dur, 2),
            "video": f"/uploads/social/adv_work_{reel_id}/scene_{scene['scene_num']}_v.mp4",
            "videoThumb": f"/uploads/social/adv_work_{reel_id}/scene_{scene['scene_num']}_v.mp4",
            "audio": f"/uploads/social/adv_work_{reel_id}/full_voice.mp3",
            "thumb": f"https://image.pollinations.ai/prompt/{urllib.parse.quote(scene['visuals'][:80])}?width=400&height=711&nologo=true",
            "script": scene['dialogue'],
            "transition": trans_effect,
            "motion": effect,
            "voice": voice_id or "adam"
        })

    if not scene_videos:
        return None

    # ── 5. Concatenate video scenes with dynamic xfade transitions ───────────
    temp_video = os.path.join(work_dir, "temp_video.mp4")
    
    input_args = []
    pre_filters = []
    for idx, v in enumerate(scene_videos):
        input_args.extend(["-i", v])
        pre_filters.append(f"[{idx}:v]trim=duration={scene_dur:.2f},setpts=PTS-STARTPTS,scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1[v_pre{idx}];")
        
    xfade_complex, final_v_label = build_xfade_filter_complex(
        video_count=len(scene_videos),
        durations=[scene_dur] * len(scene_videos),
        trans_dur=trans_dur
    )
    
    for idx in range(len(scene_videos)):
        xfade_complex = xfade_complex.replace(f"[{idx}:v]", f"[v_pre{idx}]")
        
    filter_parts = pre_filters + [xfade_complex]
    filter_parts.append(f"{final_v_label}copy[v]")
    
    concat_cmd = ["ffmpeg", "-y"] + input_args + [
        "-filter_complex", "".join(filter_parts),
        "-map", "[v]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", "30", "-an",
        temp_video
    ]
    concat_log_path = os.path.join(work_dir, "ffmpeg_concat.log")
    with open(concat_log_path, "w", encoding="utf-8") as log_file:
        res_concat = subprocess.run(concat_cmd, stdout=log_file, stderr=log_file, stdin=subprocess.DEVNULL)
        
    if res_concat.returncode != 0:
        logger.warning(f"Advanced Reel complex xfade video concatenation failed (exit {res_concat.returncode}). Retrying with safe standard concat fallback...")
        list_path = os.path.join(work_dir, "concat_list.txt")
        with open(list_path, "w", encoding="utf-8") as f:
            for v in scene_videos:
                v_fixed = v.replace('\\', '/')
                f.write(f"file '{v_fixed}'\n")
                
        fallback_cmd = [
            "ffmpeg", "-y", "-nostdin",
            "-f", "concat", "-safe", "0", "-i", list_path,
            "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", "30", "-an",
            temp_video
        ]
        fallback_log_path = os.path.join(work_dir, "ffmpeg_concat_fallback.log")
        with open(fallback_log_path, "w", encoding="utf-8") as log_file:
            res_fallback = subprocess.run(fallback_cmd, stdout=log_file, stderr=log_file, stdin=subprocess.DEVNULL)
        if res_fallback.returncode != 0:
            logger.error("Advanced Reel safe concat fallback failed on both xfade and fallback.")
            return None

    # ── 6. BGM ───────────────────────────────────────────────────────────────
    bgm_map = {
        "cinematic": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3",
        "energetic": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-2.mp3",
        "corporate": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-3.mp3",
        "dramatic":  "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-8.mp3"
    }
    bgm_url = bgm_map.get(bgm_style.lower(), bgm_map["dramatic"])
    bgm_path = os.path.join(work_dir, "bgm.mp3")
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(bgm_url, timeout=30.0, follow_redirects=True)
            if r.status_code == 200:
                with open(bgm_path, "wb") as f: f.write(r.content)
            else: bgm_path = None
    except: bgm_path = None

    # ── 7. Subtitles from full dialogue (synchronized by scene) ──────────────
    sub_path = create_scene_subtitles_pro(advanced_scenes_data, work_dir)
    safe_sub = sub_path.replace("\\", "/").replace(":", "\\:")

    # ── 8. Final assembly: video + full_voice + bgm + subs ───────────────────
    output_filename = f"adv_reel_{reel_id}.mp4"
    output_path = os.path.join(base_uploads, output_filename)

    # Build filter_complex
    # inputs: [0]=video [1]=full_voice [2]=bgm(optional)
    inputs = ["-i", temp_video, "-i", full_voice_path]
    if bgm_path and os.path.exists(bgm_path):
        inputs += ["-i", bgm_path]
        fc = (
            f"[0:v]ass='{safe_sub}'[v];"
            f"[1:a]highpass=f=60,volume=0.95[av];"
            f"[2:a]volume=0.05,atrim=0:{total_audio_dur:.2f},asetpts=PTS-STARTPTS[abg];"
            f"[av][abg]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,alimiter=limit=0.95[a]"
        )
        maps = ["-map", "[v]", "-map", "[a]"]
    else:
        fc = (
            f"[0:v]ass='{safe_sub}'[v];"
            f"[1:a]highpass=f=60,volume=0.95[a]"
        )
        maps = ["-map", "[v]", "-map", "[a]"]

    final_cmd = (
        ["ffmpeg", "-y"]
        + inputs
        + ["-filter_complex", fc]
        + maps
        + ["-c:v", "libx264", "-preset", "fast", "-crf", "20",
           "-c:a", "aac", "-b:a", "192k",
           "-pix_fmt", "yuv420p", "-r", "30",
           "-t", str(total_audio_dur),
           output_path]
    )

    logger.info("Final assembly...")
    assembly_log_path = os.path.join(work_dir, "ffmpeg_assembly.log")
    with open(assembly_log_path, "w", encoding="utf-8") as log_file:
        res = subprocess.run(final_cmd, stdout=log_file, stderr=log_file, stdin=subprocess.DEVNULL)
    if res.returncode != 0:
        err_msg = "Unknown error"
        if os.path.exists(assembly_log_path):
            try:
                with open(assembly_log_path, "r", encoding="utf-8", errors="ignore") as f:
                    err_msg = f.read()[-500:]
            except: pass
        logger.error(f"Final assembly failed: {err_msg}")
        return None

    return {
        "video_url": f"/uploads/social/{output_filename}",
        "scenes": advanced_scenes_data,
        "bgm_url": bgm_url,
        "voice_id": voice_id
    }


async def assemble_edited_reel(
    scenes: List[Dict],
    voice_id: Optional[str] = None,
    bgm_style: str = "cinematic",
    audio_tracks: Optional[List[Dict]] = None,
    watermark_path: Optional[str] = None,
    language: Optional[str] = "English"
) -> Dict:
    """
    Production-grade Editing Assembly: Compiles the edited/reordered scenes 
    scene-by-scene, generating individual voiceovers for each scene to ensure 
    perfect audio-visual synchronization without timing drift or dialogue splitting.
    """
    import shutil
    base_uploads = os.path.join(os.getcwd(), "uploads", "social")
    reel_id = secrets.token_hex(6)
    work_dir = os.path.join(base_uploads, f"edit_work_{reel_id}")
    os.makedirs(work_dir, exist_ok=True)
    
    scene_videos = []
    final_scenes_data = []
    
    # Helper to download or copy thumb
    async def resolve_thumb_image(url: str, dest_path: str):
        if not url:
            # Fallback placeholder image
            fallback_url = "https://images.unsplash.com/photo-1677442136019-21780ecad995?auto=format&fit=crop&q=80&w=1080&h=1920"
            try:
                async with httpx.AsyncClient() as client:
                    res = await client.get(fallback_url, timeout=20.0)
                    if res.status_code == 200:
                        with open(dest_path, "wb") as f: f.write(res.content)
                        return
            except Exception as e:
                logger.error(f"Failed to fetch fallback remote image: {e}")
            return
            
        if "uploads/" in url or url.startswith("/uploads"):
            clean_url = url.split("uploads/")[-1].lstrip("/")
            local_src = os.path.join(os.getcwd(), "uploads", clean_url)
            if os.path.exists(local_src):
                shutil.copy(local_src, dest_path)
                return
                
        # Remote file download
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(url, timeout=30.0)
                if res.status_code == 200:
                    with open(dest_path, "wb") as f: f.write(res.content)
                    return
        except Exception as e:
            logger.error(f"Failed to download remote thumbnail: {e}")
            
        # If download fails, copy fallback placeholder
        fallback_url = "https://images.unsplash.com/photo-1677442136019-21780ecad995?auto=format&fit=crop&q=80&w=1080&h=1920"
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(fallback_url, timeout=20.0)
                if res.status_code == 200:
                    with open(dest_path, "wb") as f: f.write(res.content)
        except Exception as e:
            logger.error(f"Final fallback failed: {e}")

    # Process each scene sequentially
    accumulated_time = 0.0
    for i, s in enumerate(scenes):
        logger.info(f"Assembling Edited Scene {i+1}/{len(scenes)}...")
        
        dialogue = s.get('script', '')
        effect = s.get('effect', 'zoom_in')
        thumb_url = s.get('thumb') or s.get('videoThumb') or s.get('image') or s.get('url') or s.get('media_url')
        
        # 1. Resolve Image Asset
        img_path = os.path.join(work_dir, f"scene_{i}.jpg")
        term = s.get('term') or s.get('script', 'scene')[:20]
        prompt = s.get('prompt') or term
        source = s.get('source') or 'stock'
        
        asset_found = False
        
        if thumb_url and (thumb_url.startswith("http") or "uploads" in thumb_url) and not thumb_url.endswith("pexels-photo-3183150.jpeg?auto=compress&cs=tinysrgb&w=300"):
            await resolve_thumb_image(thumb_url, img_path)
            if os.path.exists(img_path) and os.path.getsize(img_path) > 100:
                asset_found = True
                
        if not asset_found:
            # Dynamically fetch standard assets
            if source == "stock":
                v_urls = await search_pexels_videos(term, count=1)
                if v_urls:
                    try:
                        async with httpx.AsyncClient() as client:
                            v_res = await client.get(v_urls[0], timeout=30.0)
                            if v_res.status_code == 200:
                                raw_video_path = os.path.join(work_dir, f"raw_scene_{i}.mp4")
                                with open(raw_video_path, "wb") as f: f.write(v_res.content)
                                extract_cmd = ["ffmpeg", "-y", "-i", raw_video_path, "-vframes", "1", img_path]
                                if subprocess.run(extract_cmd, capture_output=True).returncode == 0:
                                    asset_found = True
                                    s["raw_video"] = raw_video_path
                    except Exception as e:
                        logger.error(f"Failed to fetch stock video for scene {i}: {e}")
            
            if not asset_found:
                encoded_prompt = urllib.parse.quote(f"{prompt}, 8k, cinematic lighting, masterpiece")
                base_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1080&height=1920&nologo=true&seed={secrets.token_hex(4)}"
                
                async with httpx.AsyncClient() as client:
                    for attempt in range(3):
                        model = "flux" if attempt < 2 else "turbo"
                        img_url = f"{base_url}&model={model}"
                        try:
                            img_res = await client.get(img_url, timeout=60.0)
                            if img_res.status_code == 200:
                                with open(img_path, "wb") as f: f.write(img_res.content)
                                asset_found = True
                                thumb_url = img_url
                                break
                            elif img_res.status_code == 429:
                                await asyncio.sleep((attempt + 1) * 3)
                        except:
                            await asyncio.sleep(2)
                            
        if not asset_found:
            await resolve_thumb_image(None, img_path)
            thumb_url = "https://images.pexels.com/photos/3183150/pexels-photo-3183150.jpeg?auto=compress&cs=tinysrgb&w=300"
        
        # 2. Generate Scene-Specific Voiceover
        voice_path = os.path.join(work_dir, f"voice_{i}.mp3")
        audio_path = await generate_elevenlabs_voiceover(dialogue, work_dir, voice_id=voice_id, language=language)
        if audio_path and os.path.exists(audio_path):
            if os.path.exists(voice_path): os.remove(voice_path)
            os.rename(audio_path, voice_path)
        else:
            # Secure silence fallback
            generate_silent_audio(3.0, work_dir, f"voice_{i}.mp3")
            
        # 3. Probe Exact Duration based on narration audio
        is_image_scene = not ("raw_video" in s and os.path.exists(s["raw_video"]))
        duration = 5.0
        if os.path.exists(voice_path):
            probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", voice_path], capture_output=True, text=True)
            if probe.returncode == 0 and probe.stdout.strip():
                duration = float(probe.stdout.strip()) + 0.3
        elif is_image_scene:
            duration = 3.0
            
        # 4. Render Single Scene Video Clip with Stereo Audio
        v_path = os.path.join(work_dir, f"scene_{i}_final.mp4")
        zoom = "min(zoom+0.0015,1.5)" if effect == "zoom_in" else "max(1.5-0.0015*on,1)" if effect == "zoom_out" else "1"
        
        # Disable visual filters, transitions, and zoompans to ensure maximum visual clarity
        trans_filter = ""
        enhancement = ""
        
        filter_v = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1"
        
        if "raw_video" in s and os.path.exists(s["raw_video"]):
            cmd = ["ffmpeg", "-y", "-stream_loop", "-1", "-i", s["raw_video"]]
            if os.path.exists(voice_path):
                cmd.extend(["-i", voice_path])
                cmd.extend(["-vf", filter_v, "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-t", str(duration), "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-shortest", v_path])
            else:
                cmd.extend([
                    "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                    "-vf", filter_v, "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-t", str(duration), "-pix_fmt", "yuv420p", "-r", "30",
                    "-c:a", "aac", "-shortest", v_path
                ])
        else:
            cmd = ["ffmpeg", "-y", "-loop", "1", "-i", img_path]
            if os.path.exists(voice_path):
                cmd.extend(["-i", voice_path])
                cmd.extend(["-vf", filter_v, "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-t", str(duration), "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-shortest", v_path])
            else:
                cmd.extend([
                    "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                    "-vf", filter_v, "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-t", str(duration), "-pix_fmt", "yuv420p", "-r", "30",
                    "-c:a", "aac", "-shortest", v_path
                ])
            
        # Asset Validation Layer for voice audio
        validated_voice_path = validate_audio_asset(voice_path, duration, work_dir, f"voice_{i}")
        if os.path.exists(validated_voice_path) and validated_voice_path != voice_path:
            try:
                shutil.copy2(validated_voice_path, voice_path)
            except Exception as se:
                logger.warning(f"Failed to copy validated voice: {se}")

        # Run compilation
        subprocess.run(cmd, capture_output=True)
        
        # Asset Validation Layer for video segment
        validated_v_path = validate_video_asset(v_path, duration, work_dir, f"scene_{i}_edit")
        scene_videos.append(validated_v_path)
        
        start_time = accumulated_time
        end_time = accumulated_time + duration
        accumulated_time += duration
        
        final_scenes_data.append({
            "id": i + 1,
            "scene_id": i + 1,
            "start": round(start_time, 2),
            "end": round(end_time, 2),
            "duration": round(duration, 2),
            "video": f"/uploads/social/edit_work_{reel_id}/scene_{i}_final.mp4",
            "videoThumb": f"/uploads/social/edit_work_{reel_id}/scene_{i}_final.mp4",
            "audio": f"/uploads/social/edit_work_{reel_id}/voice_{i}.mp3",
            "thumb": thumb_url or "https://images.pexels.com/photos/3183150/pexels-photo-3183150.jpeg?auto=compress&cs=tinysrgb&w=300",
            "script": dialogue,
            "transition": trans_effect,
            "motion": effect or "zoom_in",
            "voice": voice_id or "adam"
        })

    if not scene_videos:
        logger.error("No scene videos could be compiled.")
        return {"video_url": None, "scenes": []}

    # 5. Concatenate Scene Clips
    list_path = os.path.join(work_dir, "concat_list.txt")
    with open(list_path, "w") as f:
        for v in scene_videos:
            v_fixed = v.replace('\\', '/')
            f.write(f"file '{v_fixed}'\n")
            
    temp_concat = os.path.join(work_dir, "temp_concat.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
         "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", temp_concat],
        capture_output=True
    )

    # 6. Fetch Background Music (BGM)
    bgm_map = {
        "cinematic": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3",
        "energetic": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-2.mp3",
        "corporate": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-3.mp3",
        "dramatic": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-8.mp3"
    }
    bgm_url = bgm_map.get(bgm_style.lower(), bgm_map["cinematic"])
    bgm_path = os.path.join(work_dir, "bgm.mp3")
    
    async with httpx.AsyncClient() as client:
        try:
            bgm_res = await client.get(bgm_url, timeout=30.0, follow_redirects=True)
            if bgm_res.status_code == 200:
                with open(bgm_path, "wb") as f: f.write(bgm_res.content)
            else: bgm_path = None
        except: bgm_path = None

    # 7. Create Subtitles (synchronized by scene)
    sub_path = create_scene_subtitles_pro(final_scenes_data, work_dir)
    safe_sub = sub_path.replace("\\", "/").replace(":", "\\:")

    # 8. Render Final Output with Subtitles, Watermark and Multi-Track Audio Mixing
    output_filename = f"edit_reel_{reel_id}.mp4"
    output_path = os.path.join(base_uploads, output_filename)
    
    # Resolve local paths of custom audio tracks
    valid_tracks = []
    audio_tracks_list = audio_tracks or []
    for idx, track in enumerate(audio_tracks_list):
        track_url = track.get("url") or track.get("audio")
        if not track_url:
            continue
        
        local_track_path = None
        if "uploads/" in track_url or track_url.startswith("/uploads"):
            clean_url = track_url.split("uploads/")[-1].lstrip("/")
            local_track_path = os.path.join(os.getcwd(), "uploads", clean_url)
        elif os.path.exists(track_url):
            local_track_path = track_url
            
        if local_track_path and os.path.exists(local_track_path):
            valid_tracks.append({
                "path": local_track_path,
                "start": float(track.get("start", 0)),
                "volume": float(track.get("volume", 1.0))
            })
            
    final_cmd = ["ffmpeg", "-y", "-i", temp_concat]
    
    # Dynamic input tracking
    next_idx = 1
    
    bgm_idx = None
    if bgm_path and os.path.exists(bgm_path):
        final_cmd.extend(["-i", bgm_path])
        bgm_idx = next_idx
        next_idx += 1
        
    watermark_idx = None
    if watermark_path and os.path.exists(watermark_path):
        final_cmd.extend(["-loop", "1", "-i", watermark_path])
        watermark_idx = next_idx
        next_idx += 1
        
    track_indices = []
    for track in valid_tracks:
        final_cmd.extend(["-i", track["path"]])
        track_indices.append((next_idx, track["start"], track["volume"]))
        next_idx += 1
        
    filters = []
    
    # Video Filters: Subtitles + Watermark Overlay
    filters.append(f"[0:v]ass='{safe_sub}'[v_sub]")
    current_v = "[v_sub]"
    
    if watermark_idx is not None:
        filters.append(f"{current_v}[{watermark_idx}:v]overlay=0:0:shortest=1[v_wat]")
        current_v = "[v_wat]"
        
    # Audio Filters: Main voiceover + BGM + Custom overlapping audio tracks
    filters.append(f"[0:a]volume=1.0[a_v]")
    audio_mix_inputs = ["[a_v]"]
    
    if bgm_idx is not None:
        filters.append(f"[{bgm_idx}:a]volume=0.05,atrim=0:{total_dur}[a_bg]")
        audio_mix_inputs.append("[a_bg]")
        
    for idx, (t_idx, t_start, t_vol) in enumerate(track_indices):
        t_start_ms = int(max(0, t_start) * 1000)
        filters.append(f"[{t_idx}:a]volume={t_vol},adelay={t_start_ms}|{t_start_ms}[a_track_{idx}]")
        audio_mix_inputs.append(f"[a_track_{idx}]")
    if len(audio_mix_inputs) > 1:
        mix_inputs_str = "".join(audio_mix_inputs)
        filters.append(f"{mix_inputs_str}amix=inputs={len(audio_mix_inputs)}:duration=first:dropout_transition=0:normalize=0,alimiter=limit=0.95[a_fin]")
        current_a = "[a_fin]"
    else:
        current_a = "[a_v]"
        
    filter_complex_str = "; ".join(filters)
    final_cmd.extend(["-filter_complex", filter_complex_str, "-map", current_v, "-map", current_a])
    final_cmd.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p", output_path])
    
    logger.info(f"Running Final Multi-Track Assembly: {' '.join(final_cmd)}")
    if subprocess.run(final_cmd, capture_output=True).returncode == 0:
        return {
            "video_url": f"/uploads/social/{output_filename}",
            "scenes": final_scenes_data,
            "bgm_url": bgm_url,
            "voice_id": voice_id
        }
    return {"video_url": None, "scenes": []}

import re
import logging
import os
import tempfile
import httpx
import asyncio
from pathlib import Path
from typing import Optional, List, Tuple

logger = logging.getLogger(__name__)

def extract_video_id(url: str) -> Optional[str]:
    m = re.search(r"(?:v=|/v/|youtu\.be/|/embed/|/shorts/)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else None

async def fetch_video_title(video_id: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"https://www.youtube.com/watch?v={video_id}", headers={"User-Agent":"Mozilla/5.0"})
            m = re.search(r'"title":"([^"]{1,200})"', r.text)
            if m:
                t = m.group(1)
                t = re.sub(r'\\u([\da-fA-F]{4})', lambda x: chr(int(x.group(1),16)), t)
                return t[:150]
    except Exception: pass
    return f"YouTube {video_id}"

async def get_youtube_transcript(url: str, whisper_model: str = "base") -> Tuple[Optional[str], str]:
    """
    Robust 3-stage YouTube transcript extraction.
    Returns (transcript_text, title).
    """
    video_id = extract_video_id(url)
    if not video_id:
        raise ValueError("Invalid YouTube URL")

    title = await fetch_video_title(video_id)
    text = None

    # Stage 1: youtube-transcript-api
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        logger.info(f"[YT {video_id}] Trying Stage 1 (API)...")
        transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=['en', 'hi', 'en-US', 'en-GB', 'a.en'])
        text = " ".join(e["text"] for e in transcript)
        if text:
            logger.info(f"[YT {video_id}] Stage 1 success.")
            return text, title
    except Exception as e:
        logger.warning(f"[YT {video_id}] Stage 1 failed: {e}")

    # Stage 2: yt-dlp subtitles
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            import yt_dlp
            logger.info(f"[YT {video_id}] Trying Stage 2 (yt-dlp subs)...")
            opts = {
                "writesubtitles": True, "writeautomaticsub": True,
                "subtitleslangs": ["en", "hi", "a.en"], "subtitlesformat": "vtt",
                "skip_download": True, "outtmpl": os.path.join(tmpdir, "%(id)s.%(ext)s"),
                "quiet": True, "no_warnings": True,
                "nocheckcertificate": True,
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
                "referer": "https://www.youtube.com/",
            }
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
            
            vtt_files = list(Path(tmpdir).glob("*.vtt"))
            if vtt_files:
                vtt_text = vtt_files[0].read_text(encoding="utf-8", errors="replace")
                # Simple VTT to text
                text = re.sub(r'<[^>]+>', '', vtt_text) # Remove tags
                text = re.sub(r'\d+:\d+:\d+\.\d+ --> \d+:\d+:\d+\.\d+', '', text) # Remove timestamps
                text = " ".join(text.split())
                if text:
                    logger.info(f"[YT {video_id}] Stage 2 success.")
                    return text, title
        except Exception as e:
            logger.warning(f"[YT {video_id}] Stage 2 failed: {e}")

        # Stage 3: yt-dlp audio + Whisper
        try:
            import yt_dlp
            import whisper
            logger.info(f"[YT {video_id}] Trying Stage 3 (Whisper)...")
            audio_opts = {
                "format": "bestaudio/best",
                "outtmpl": os.path.join(tmpdir, "audio.%(ext)s"),
                "postprocessors": [{"key":"FFmpegExtractAudio","preferredcodec":"mp3","preferredquality":"96"}],
                "quiet": True, "no_warnings": True,
                "nocheckcertificate": True,
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
                "referer": "https://www.youtube.com/",
            }
            with yt_dlp.YoutubeDL(audio_opts) as ydl:
                ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
            
            mp3_files = list(Path(tmpdir).glob("*.mp3"))
            if mp3_files:
                model = whisper.load_model(whisper_model)
                result = model.transcribe(str(mp3_files[0]))
                text = result.get("text", "").strip()
                if text:
                    logger.info(f"[YT {video_id}] Stage 3 success.")
                    return text, title
        except Exception as e:
            logger.error(f"[YT {video_id}] Stage 3 failed: {e}")

    return None, title

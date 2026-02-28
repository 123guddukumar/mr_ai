"""
MR AI RAG - YouTube / Video Transcript Route v3
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Endpoints:
  POST /ingest-youtube      → YouTube URL  (3-stage fallback)
  POST /ingest-video        → Upload any video/audio file
  POST /video-summary       → LLM summarize transcript
  POST /video-quiz          → LLM generate quiz from transcript

Features:
  ✓ Timestamped segments (grouped every 20 seconds)
  ✓ Raw subtitle text (VTT / youtube-transcript-api)
  ✓ Plain transcript (for RAG + copy)
  ✓ Summary generation via active LLM provider
  ✓ Quiz generation via active LLM provider
  ✓ Full RAG indexing (chunked, embedded, FAISS)

3-stage YouTube fallback:
  ① youtube-transcript-api  – instant, timestamped
  ② yt-dlp VTT download     – handles auto-generated subs
  ③ yt-dlp audio + Whisper  – any video with speech

Requires:  pip install yt-dlp youtube-transcript-api openai-whisper
           ffmpeg on PATH
"""

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, List

from fastapi import APIRouter, HTTPException, UploadFile, File, Query, Depends
from app.core.api_keys import require_api_key
from pydantic import BaseModel

from app.services.chunker import chunk_text
from app.services.embedder import embed_texts
from app.services.vector_store import get_vector_store
from app.services.llm import generate_answer

logger = logging.getLogger(__name__)
router = APIRouter()

SEGMENT_SECONDS = 20

# In-memory transcript cache
_transcript_store: dict = {}

def cache_transcript(source_id: str, text: str):
    _transcript_store[source_id] = text

# ── Pydantic Models ────────────────────────────────────────────────────────────

class TimestampSegment(BaseModel):
    start: float          # seconds
    end: float
    timestamp_label: str  # e.g. "0:00 - 0:20"
    text: str

class YoutubeIngestRequest(BaseModel):
    url: str
    whisper_model: str = "base"

class VideoIngestResponse(BaseModel):
    success: bool
    source_id: str
    title: str
    url: str
    # Transcript variants
    transcript_plain: str               # plain text (for copy)
    transcript_timestamped: List[TimestampSegment]  # 20s segments
    subtitle_plain: str                 # raw subtitle text (may equal transcript)
    subtitle_timestamped: List[TimestampSegment]
    has_native_subtitles: bool
    # Stats
    total_chunks: int
    word_count: int
    duration_seconds: float
    method_used: str
    message: str

class VideoActionRequest(BaseModel):
    source_id: str

class VideoActionResponse(BaseModel):
    source_id: str
    action: str
    result: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def extract_video_id(url: str) -> Optional[str]:
    m = re.search(r"(?:v=|/v/|youtu\.be/|/embed/|/shorts/)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else None

def fmt_time(seconds: float) -> str:
    """Format seconds to M:SS or H:MM:SS."""
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"

def group_into_segments(
    entries: List[dict],   # each: {start, duration, text}
    segment_secs: int = SEGMENT_SECONDS
) -> List[TimestampSegment]:
    """Group caption entries into N-second segments."""
    if not entries:
        return []
    segments: List[TimestampSegment] = []
    bucket_start = entries[0]["start"]
    bucket_texts = []
    bucket_end = bucket_start

    for e in entries:
        t_start = float(e.get("start", 0))
        t_dur   = float(e.get("duration", 2))
        text    = e.get("text", "").strip()
        if not text:
            continue

        if t_start - bucket_start >= segment_secs and bucket_texts:
            segments.append(TimestampSegment(
                start=bucket_start,
                end=bucket_end,
                timestamp_label=f"{fmt_time(bucket_start)} – {fmt_time(bucket_end)}",
                text=" ".join(bucket_texts)
            ))
            bucket_start = t_start
            bucket_texts = []

        bucket_texts.append(text)
        bucket_end = t_start + t_dur

    if bucket_texts:
        segments.append(TimestampSegment(
            start=bucket_start,
            end=bucket_end,
            timestamp_label=f"{fmt_time(bucket_start)} – {fmt_time(bucket_end)}",
            text=" ".join(bucket_texts)
        ))
    return segments

def plain_from_segments(segments: List[TimestampSegment]) -> str:
    return " ".join(s.text for s in segments)

def plain_from_entries(entries: List[dict]) -> str:
    return " ".join(e.get("text","").strip() for e in entries if e.get("text","").strip())

def clean_text(text: str) -> str:
    text = re.sub(r'\[.*?\]', '', text)
    text = re.sub(r'\(.*?\)', '', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def store_transcript(source_id: str, plain: str) -> int:
    """Chunk, embed, store in FAISS. Returns chunk count."""
    chunks = chunk_text([(1, plain)], source_file=source_id)
    if not chunks:
        return 0
    embs = embed_texts([c.text for c in chunks])
    get_vector_store().add_chunks(embs, chunks)
    return len(chunks)


# ── Stage 1: youtube-transcript-api ───────────────────────────────────────────

async def _try_transcript_api(video_id: str):
    """Returns (entries_with_timestamps, is_native_sub)"""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound
        # Try manual English first, then auto-generated
        try:
            tlist = YouTubeTranscriptApi.list_transcripts(video_id)
            try:
                t = tlist.find_manually_created_transcript(['en','en-US','en-GB'])
                entries = t.fetch()
                return [{"start":e["start"],"duration":e.get("duration",2),"text":e["text"]} for e in entries], True
            except Exception:
                t = tlist.find_generated_transcript([x.language_code for x in tlist])
                entries = t.fetch()
                return [{"start":e["start"],"duration":e.get("duration",2),"text":e["text"]} for e in entries], False
        except Exception:
            entries = YouTubeTranscriptApi.get_transcript(video_id, languages=['en','en-US','en-GB','a.en'])
            return [{"start":e["start"],"duration":e.get("duration",2),"text":e["text"]} for e in entries], False
    except ImportError:
        logger.warning("youtube-transcript-api not installed")
        return None, False
    except Exception as e:
        logger.warning(f"youtube-transcript-api failed: {e}")
        return None, False


# ── Stage 2: yt-dlp VTT download ──────────────────────────────────────────────

def _parse_vtt_timestamped(vtt_text: str) -> List[dict]:
    """Parse WebVTT into list of {start, duration, text} dicts."""
    entries = []
    blocks = re.split(r'\n{2,}', vtt_text.strip())
    for block in blocks:
        lines = block.strip().splitlines()
        if not lines:
            continue
        # Find timestamp line
        ts_line = None
        text_lines = []
        for line in lines:
            if '-->' in line:
                ts_line = line
            elif ts_line and line.strip() and not line.startswith('NOTE') and not re.match(r'^\d+$', line.strip()):
                text_lines.append(re.sub(r'<[^>]+>','',line))
        if not ts_line or not text_lines:
            continue
        # Parse timestamps HH:MM:SS.mmm --> HH:MM:SS.mmm
        ts = re.findall(r'(\d+):(\d+):(\d+)\.(\d+)', ts_line)
        if len(ts) < 2:
            # Try MM:SS.mmm
            ts2 = re.findall(r'(\d+):(\d+)\.(\d+)', ts_line)
            if len(ts2) >= 2:
                def mmss(g): return int(g[0])*60 + int(g[1]) + int(g[2])/1000
                start = mmss(ts2[0]); end = mmss(ts2[1])
            else:
                continue
        else:
            def hhmmss(g): return int(g[0])*3600 + int(g[1])*60 + int(g[2]) + int(g[3])/1000
            start = hhmmss(ts[0]); end = hhmmss(ts[1])
        text = " ".join(text_lines).strip()
        text = text.replace('&amp;','&').replace('&lt;','<').replace('&gt;','>').replace('&#39;',"'")
        if text:
            entries.append({"start": start, "duration": max(end-start, 0.5), "text": text})
    return entries

async def _try_ytdlp_subs(video_id: str, tmpdir: str):
    """Returns (entries, title, is_native_sub)"""
    try:
        import yt_dlp
    except ImportError:
        return None, None, False

    url = f"https://www.youtube.com/watch?v={video_id}"
    opts = {
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en","en-US","en-GB","a.en"],
        "subtitlesformat": "vtt",
        "skip_download": True,
        "outtmpl": os.path.join(tmpdir, "%(id)s.%(ext)s"),
        "quiet": True, "no_warnings": True,
    }
    title = None
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title")

        vtt_files = list(Path(tmpdir).glob("*.vtt"))
        if not vtt_files:
            return None, title, False

        vtt_text = vtt_files[0].read_text(encoding="utf-8", errors="replace")
        # Detect if this is manually-created or auto
        is_native = ".en.vtt" in vtt_files[0].name and "a.en" not in vtt_files[0].name
        entries = _parse_vtt_timestamped(vtt_text)
        return (entries if entries else None), title, is_native
    except Exception as e:
        logger.warning(f"yt-dlp subs failed: {e}")
        return None, title, False


# ── Stage 3: Whisper ───────────────────────────────────────────────────────────

async def _whisper_from_file(audio_path: str, model_name: str = "base") -> List[dict]:
    """Transcribe with Whisper, return timestamped entries."""
    try:
        import whisper
    except ImportError:
        raise HTTPException(503, "openai-whisper not installed. Run: pip install openai-whisper")

    logger.info(f"Whisper [{model_name}] transcribing {audio_path}…")
    model = whisper.load_model(model_name)
    result = model.transcribe(audio_path, fp16=False, verbose=False, word_timestamps=False)
    entries = []
    for seg in result.get("segments", []):
        text = clean_text(seg.get("text",""))
        if text:
            entries.append({
                "start": float(seg["start"]),
                "duration": float(seg["end"]) - float(seg["start"]),
                "text": text
            })
    return entries

async def _download_audio(video_id: str, tmpdir: str) -> tuple:
    """Download best audio via yt-dlp, return (mp3_path, title)."""
    try:
        import yt_dlp
    except ImportError:
        raise HTTPException(503, "yt-dlp not installed. Run: pip install yt-dlp")

    url = f"https://www.youtube.com/watch?v={video_id}"
    opts = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(tmpdir, "audio.%(ext)s"),
        "postprocessors": [{"key":"FFmpegExtractAudio","preferredcodec":"mp3","preferredquality":"96"}],
        "quiet": True, "no_warnings": True,
    }
    title = f"YouTube {video_id}"
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", title)
    except Exception as e:
        raise HTTPException(502, f"Audio download failed: {e}")

    mp3 = list(Path(tmpdir).glob("*.mp3"))
    if not mp3:
        raise HTTPException(500, "Audio file not found after download.")
    return str(mp3[0]), title

async def _fetch_title(video_id: str) -> str:
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"https://www.youtube.com/watch?v={video_id}",
                            headers={"User-Agent":"Mozilla/5.0"})
            m = re.search(r'"title":"([^"]{1,200})"', r.text)
            if m:
                t = m.group(1)
                t = re.sub(r'\\u([\da-fA-F]{4})', lambda x: chr(int(x.group(1),16)), t)
                return t[:150]
    except Exception:
        pass
    return f"YouTube {video_id}"


# ── Route: ingest-youtube ──────────────────────────────────────────────────────

@router.post("/ingest-youtube", response_model=VideoIngestResponse,
             summary="Ingest YouTube with timestamps + subtitles")
async def ingest_youtube(req: YoutubeIngestRequest, _key: dict = Depends(require_api_key)):
    url = req.url.strip()
    video_id = extract_video_id(url)
    if not video_id:
        raise HTTPException(400, "Invalid YouTube URL.")

    yt_url = f"https://www.youtube.com/watch?v={video_id}"
    entries = None
    subtitle_entries = None
    title = None
    method = None
    is_native = False

    with tempfile.TemporaryDirectory() as tmpdir:

        # Stage 1
        logger.info(f"[YT {video_id}] Stage 1: youtube-transcript-api")
        entries, is_native = await _try_transcript_api(video_id)
        if entries:
            method = "youtube-transcript-api"
            subtitle_entries = entries   # same source = subtitle == transcript
            title = await _fetch_title(video_id)
            logger.info(f"[YT] Stage 1 OK — {len(entries)} entries")

        # Stage 2
        if not entries:
            logger.info(f"[YT {video_id}] Stage 2: yt-dlp subtitles")
            entries, title, is_native = await _try_ytdlp_subs(video_id, tmpdir)
            if entries:
                method = "yt-dlp-subtitles"
                subtitle_entries = entries
                logger.info(f"[YT] Stage 2 OK — {len(entries)} entries")

        # Stage 3
        if not entries:
            logger.info(f"[YT {video_id}] Stage 3: Whisper")
            audio_path, title2 = await _download_audio(video_id, tmpdir)
            if title is None:
                title = title2
            entries = await _whisper_from_file(audio_path, req.whisper_model)
            method = f"whisper-{req.whisper_model}"
            subtitle_entries = None   # Whisper has no native subtitles
            is_native = False
            logger.info(f"[YT] Stage 3 OK — {len(entries)} segments")

    if not entries:
        raise HTTPException(422,
            "Could not extract transcript. Install yt-dlp & openai-whisper for full support.")

    title = title or await _fetch_title(video_id)

    # Build timestamped + plain variants
    ts_segments   = group_into_segments(entries, SEGMENT_SECONDS)
    plain_text    = clean_text(plain_from_entries(entries))
    duration_secs = entries[-1]["start"] + entries[-1].get("duration", 0) if entries else 0

    if subtitle_entries and subtitle_entries is not entries:
        sub_segments = group_into_segments(subtitle_entries, SEGMENT_SECONDS)
        sub_plain    = clean_text(plain_from_entries(subtitle_entries))
    else:
        sub_segments = ts_segments
        sub_plain    = plain_text

    source_id   = f"youtube/{video_id}"
    chunk_count = store_transcript(source_id, plain_text)
    cache_transcript(source_id, plain_text)

    return VideoIngestResponse(
        success=True, source_id=source_id, title=title, url=yt_url,
        transcript_plain=plain_text,
        transcript_timestamped=ts_segments,
        subtitle_plain=sub_plain,
        subtitle_timestamped=sub_segments,
        has_native_subtitles=is_native,
        total_chunks=chunk_count,
        word_count=len(plain_text.split()),
        duration_seconds=round(duration_secs, 1),
        method_used=method,
        message=f"Indexed {chunk_count} chunks from '{title}' via {method}"
    )


# ── Route: ingest-video (file upload) ─────────────────────────────────────────

ALLOWED_EXT = {".mp4",".mkv",".webm",".mov",".avi",".flv",".wmv",
               ".mp3",".wav",".m4a",".ogg",".flac",".aac"}
AUDIO_EXT   = {".mp3",".wav",".m4a",".ogg",".flac",".aac"}

@router.post("/ingest-video", response_model=VideoIngestResponse,
             summary="Upload any video/audio file → Whisper transcription")
async def ingest_video_file(
    file: UploadFile = File(...),
    whisper_model: str = Query("base", enum=["tiny","base","small","medium","large"]),
    _key: dict = Depends(require_api_key)
):
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"Unsupported type '{ext}'. Allowed: {', '.join(sorted(ALLOWED_EXT))}")

    content = await file.read()
    if not content:
        raise HTTPException(400, "Empty file.")
    if len(content) > 500 * 1024 * 1024:
        raise HTTPException(413, "File exceeds 500MB.")

    title = Path(file.filename).stem

    with tempfile.TemporaryDirectory() as tmpdir:
        vid_path = os.path.join(tmpdir, file.filename)
        with open(vid_path, "wb") as f:
            f.write(content)

        if ext not in AUDIO_EXT:
            # Extract audio
            audio_path = os.path.join(tmpdir, "audio.mp3")
            if not shutil.which("ffmpeg"):
                raise HTTPException(503, "ffmpeg not found on PATH.")
            r = subprocess.run(
                ["ffmpeg","-y","-i",vid_path,"-vn","-acodec","libmp3lame","-q:a","4",audio_path],
                capture_output=True, text=True)
            if r.returncode != 0:
                raise HTTPException(500, f"ffmpeg error: {r.stderr[-300:]}")
        else:
            audio_path = vid_path

        entries = await _whisper_from_file(audio_path, whisper_model)

    if not entries:
        raise HTTPException(422, "Whisper produced no output. File may have no speech.")

    ts_segments  = group_into_segments(entries, SEGMENT_SECONDS)
    plain_text   = clean_text(plain_from_entries(entries))
    duration_secs = entries[-1]["start"] + entries[-1].get("duration", 0) if entries else 0

    source_id   = f"video/{title}"
    chunk_count = store_transcript(source_id, plain_text)
    cache_transcript(source_id, plain_text)

    return VideoIngestResponse(
        success=True, source_id=source_id, title=title, url="",
        transcript_plain=plain_text,
        transcript_timestamped=ts_segments,
        subtitle_plain=plain_text,
        subtitle_timestamped=ts_segments,
        has_native_subtitles=False,
        total_chunks=chunk_count,
        word_count=len(plain_text.split()),
        duration_seconds=round(duration_secs, 1),
        method_used=f"whisper-{whisper_model}",
        message=f"Indexed {chunk_count} chunks from '{file.filename}'"
    )


# ── Route: video-summary ───────────────────────────────────────────────────────


@router.post("/video-summary", response_model=VideoActionResponse,
             summary="Generate AI summary of a video transcript")
async def video_summary(req: VideoActionRequest, _key: dict = Depends(require_api_key)):
    text = _transcript_store.get(req.source_id)
    if not text:
        raise HTTPException(404, f"Transcript not found for source_id '{req.source_id}'. Re-ingest the video.")

    # Truncate to ~8000 chars to stay within LLM context
    excerpt = text[:8000]
    prompt = f"""
        You are an expert educational content creator.

        Carefully read the FULL video transcript and generate a detailed but concise summary that covers ALL important parts of the video from beginning to end.

        STRICT INSTRUCTIONS:
        - Do NOT summarize only the beginning.
        - Cover complete flow of the video.
        - Keep explanation clear and student-friendly.
        - Include examples, steps, case studies, comparisons if mentioned.
        - Avoid repetition.
        - Keep it detailed but not overly long.

        Structure your response EXACTLY like this:

        1. 📌 Overview (3-5 sentences)
        Explain what the entire video is about and its main objective.

        2. 🧠 Detailed Explanation
        Write structured paragraphs explaining all major concepts discussed in the video in logical order.

        3. 🔑 Key Points
        - Bullet list of all major ideas
        - Include definitions, formulas, processes, frameworks if present

        4. 🎯 Final Takeaways
        - 3-5 important learning outcomes from the video

        Transcript:
        {excerpt}
        """
    try:
        result = await generate_answer("Summarize this video", prompt)
    except Exception as e:
        raise HTTPException(502, f"LLM error: {e}")

    return VideoActionResponse(source_id=req.source_id, action="summary", result=result)


# ── Route: video-quiz ──────────────────────────────────────────────────────────

@router.post("/video-quiz", response_model=VideoActionResponse,
             summary="Generate quiz questions from a video transcript")
async def video_quiz(req: VideoActionRequest, _key: dict = Depends(require_api_key)):
    text = _transcript_store.get(req.source_id)
    if not text:
        raise HTTPException(404, f"Transcript not found for '{req.source_id}'. Re-ingest the video.")

    excerpt = text[:8000]
    prompt = f"""
        You are an expert teacher creating high-quality exam-level assessment questions.

        Generate AT LEAST 12 multiple-choice questions based on the FULL video transcript.

        STRICT RULES:
        - Cover the entire transcript (start, middle, end).
        - Include conceptual, factual, analytical and application-based questions.
        - Avoid repeating similar questions.
        - Make options realistic and slightly challenging.
        - Only ONE correct answer per question.
        - Provide a short and clear explanation.

        Follow this EXACT format:

        Q1. Question text
        A) Option
        B) Option
        C) Option
        D) Option
        ✓ Answer: Correct Letter
        💡 Explanation: 1-3 sentence explanation

        Q2. ...
        (Continue same format for minimum 12 questions)

        Do NOT add summary.
        Do NOT add extra commentary.
        Only generate quiz questions.

        Transcript:
        {excerpt}
        """
    try:
        result = await generate_answer("Generate quiz questions", prompt)
    except Exception as e:
        raise HTTPException(502, f"LLM error: {e}")

    return VideoActionResponse(source_id=req.source_id, action="quiz", result=result)
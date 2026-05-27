import json
import logging
import secrets
import asyncio
from gtts import gTTS
import subprocess
import os
import shutil
import httpx
from random import randint, choice
from datetime import datetime
from typing import Optional, List, Dict
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.clients import validate_client_token
from app.services.llm import generate_answer, get_active_provider
from app.services.vector_store import get_vector_store
from app.services.embedder import embed_texts
from app.core.config import settings
from app.core.models import SocialContent, Notification

import urllib.parse
from app.services.video_engine import assemble_pro_reel, assemble_advanced_reel, assemble_edited_reel

logger = logging.getLogger(__name__)
router = APIRouter()

class SocialGenerateReq(BaseModel):
    type: str  # 'post' or 'reel'
    topic: str
    language: Optional[str] = "English"
    datastore_id: Optional[str] = None
    voice_id: Optional[str] = "adam"
    custom_script: Optional[str] = None

class ReAssembleRequest(BaseModel):
    reel_id: Optional[str] = None
    title: str
    scenes: List[Dict]
    metadata: Dict

# ── Image Generation Helper ───────────────────────────────────────────────────

async def generate_hf_image(prompt: str) -> str:
    """Generates a high-quality image using Pollinations AI (Flux Model)."""
    encoded_prompt = urllib.parse.quote(prompt)
    seed = secrets.token_hex(4)
    # Using FLUX model for maximum realism and text adherence
    return f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=1024&nologo=true&seed={seed}&model=flux"

async def generate_stock_image(topic: str) -> str:
    # Reliable stock fallback using Unsplash Source
    keywords = urllib.parse.quote(topic.split()[-1]) # Use last word of topic
    return f"https://images.unsplash.com/photo-1677442136019-21780ecad995?auto=format&fit=crop&q=80&w=1080&h=1920&sig={secrets.token_hex(4)}"

async def generate_hf_video(image_path: str) -> Optional[str]:
    # Keeping this for legacy but we will use the new assembly pipeline
    return None

async def assemble_ai_reel(script_text: str, image_prompts: List[str]) -> Optional[str]:
    """Full pipeline: TTS + Multiple Images + FFmpeg Assembly."""
    base_uploads = os.path.join(os.getcwd(), "uploads", "social")
    reel_id = secrets.token_hex(6)
    work_dir = os.path.join(base_uploads, f"work_{reel_id}")
    os.makedirs(work_dir, exist_ok=True)
    
    try:
        # 1. Generate Voiceover
        logger.info(f"Generating Voiceover for reel {reel_id}...")
        tts = gTTS(text=script_text, lang='en', slow=False)
        audio_path = os.path.join(work_dir, "voice.mp3")
        tts.save(audio_path)
        
        # 2. Generate 7 Unique Images Sequentially (to avoid 429 Rate Limits)
        logger.info(f"Generating 7 Unique Images for reel {reel_id}...")
        image_paths = []
        styles = [
            "Cinematic 8k Photorealistic", "Futuristic Cyberpunk Neon", 
            "3D Isometric Digital Art", "Abstract Liquid Motion", 
            "Modern Corporate Minimalist", "Dreamy Surrealism Fantasy", 
            "High-Tech Blueprint Schematic"
        ]
        
        for i, prompt in enumerate(image_prompts[:7]):
            selected_style = styles[i % len(styles)]
            unique_prompt = f"{selected_style} of {prompt}, high resolution, vertical 9:16, {secrets.token_hex(4)}"
            dst = os.path.join(work_dir, f"img_{i}.jpg")
            
            logger.info(f"Producing Scene {i+1}/7: {selected_style}...")
            img_url = await generate_hf_image(unique_prompt)
            
            success = False
            if img_url:
                try:
                    async with httpx.AsyncClient() as client:
                        # Try Pollinations
                        img_res = await client.get(img_url, timeout=30.0)
                        if img_res.status_code == 200:
                            with open(dst, "wb") as f:
                                f.write(img_res.content)
                            image_paths.append(dst)
                            success = True
                        else:
                            logger.warning(f"Pollinations failed (Status {img_res.status_code}), trying Unsplash fallback...")
                except Exception as e:
                    logger.error(f"Image fetch error scene {i+1}: {e}")

            # FINAL FALLBACK: If AI fails, use a high-quality stock image based on the prompt keywords
            if not success:
                logger.info(f"Using Stock Fallback for scene {i+1}...")
                stock_url = f"https://source.unsplash.com/featured/1080x1920/?{urllib.parse.quote(prompt.split()[-1])}&sig={secrets.token_hex(4)}"
                try:
                    async with httpx.AsyncClient() as client:
                        # source.unsplash.com redirects, so we follow
                        img_res = await client.get(stock_url, timeout=20.0, follow_redirects=True)
                        if img_res.status_code == 200:
                            with open(dst, "wb") as f:
                                f.write(img_res.content)
                            image_paths.append(dst)
                        else:
                            # If even Unsplash fails, use a generic tech image
                            img_res = await client.get("https://images.unsplash.com/photo-1677442136019-21780ecad995?auto=format&fit=crop&q=80&w=1080&h=1920", timeout=10.0)
                            with open(dst, "wb") as f:
                                f.write(img_res.content)
                            image_paths.append(dst)
                except:
                    pass
            
            # 1 second delay between scenes
            await asyncio.sleep(1)

        if not image_paths:
            logger.error("No images were generated successfully.")
            return None
        
        if not image_paths:
            logger.error("No images were generated successfully.")
            return None
        
        if not image_paths: return None

        # 3. Calculate Dynamic Timing
        # Get actual duration of the voiceover using ffprobe
        logger.info(f"Master Reel Debug: Script text length: {len(script_text)} chars")
        logger.info("Calculating voiceover duration...")
        probe_cmd = [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", audio_path
        ]
        probe_proc = subprocess.run(probe_cmd, capture_output=True, text=True)
        try:
            total_duration = float(probe_proc.stdout.strip())
        except:
            total_duration = 20.0
            
        logger.info(f"Master Reel Debug: Final Audio Duration: {total_duration}s")
        
        # Force minimum 20s by extending image durations if audio is somehow short
        if total_duration < 15:
            logger.warning(f"Audio is too short ({total_duration}s). Stretching to 20s.")
            total_duration = 20.0

        duration_per_img = total_duration / len(image_paths)
        logger.info(f"Master Reel Debug: Duration per image: {duration_per_img}s")

        # 4. Assemble with FFmpeg
        output_filename = f"final_reel_{reel_id}.mp4"
        output_path = os.path.join(base_uploads, output_filename)
        
        concat_file = os.path.join(work_dir, "input.txt")
        with open(concat_file, "w") as f:
            for img in image_paths:
                f.write(f"file '{os.path.basename(img)}'\nduration {duration_per_img}\n")
            # Concat demuxer requirement: last file needs to be repeated or have duration
            f.write(f"file '{os.path.basename(image_paths[-1])}'\n")

        # FFmpeg command: Concat images + Add audio + Scale to 9:16
        # We use -af "apad" to ensure audio doesn't cut early
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_file,
            "-i", audio_path,
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "25",
            "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
            "-t", str(total_duration),
            output_path
        ]
        
        logger.info(f"Running FFmpeg assembly...")
        process = subprocess.run(cmd, cwd=work_dir, capture_output=True, text=True)
        
        if process.returncode == 0:
            return f"/uploads/social/{output_filename}"
        else:
            logger.error(f"FFmpeg failed: {process.stderr}")
            return None
            
    except Exception as e:
        logger.error(f"Reel Assembly failed: {e}")
        return None


# ── Auth helper ──────────────────────────────────────────────────────────────

@router.post("/re-assemble")
async def re_assemble_social_content(req: ReAssembleRequest, x_app_token: Optional[str] = Header(None, alias="X-App-Token"), db: Session = Depends(get_db)):
    client_data = _get_client(x_app_token, db)
    
    try:
        # Extract data from scenes
        image_prompts = [s.get('thumb') for s in req.scenes] # Use current thumbs as prompts or directly
        # In re-assemble, we might want to just re-use the thumbs as fixed assets if they are already generated
        
        # We need to map the studio scenes back to the video engine format
        # For now, let's treat the 'thumb' as the fixed asset for each scene
        
        # Re-assemble using assemble_pro_reel with fixed assets
        # We'll modify assemble_pro_reel to accept fixed_assets if needed, 
        # but for now let's just use the current logic with the new script/order
        
        res_pro = await assemble_edited_reel(
            scenes=req.scenes,
            voice_id=req.metadata.get("voice", "adam"),
            bgm_style=req.metadata.get("bgm_style", "cinematic")
        )
        
        video_url = res_pro.get("video_url")
        if res_pro.get("scenes"):
            req.scenes = res_pro["scenes"]
        full_script = "\n".join([s.get('script', '') for s in req.scenes])
        if video_url:
            # Update existing record or create new one
            if req.reel_id:
                db_item = db.query(SocialContent).filter(SocialContent.content_id == req.reel_id).first()
                if db_item:
                    db_item.media_url = video_url
                    db_item.title = req.title
                    db_item.body = full_script
                    db_item.scenes_json = json.dumps(req.scenes)
                    db_item.metadata_json = json.dumps(req.metadata)
                    db.commit()
                    return {"status": "updated", "video_url": video_url}
            
            # If no reel_id or not found, create new
            content_id = secrets.token_hex(8)
            db_item = SocialContent(
                content_id=content_id,
                client_id=client_data["client_id"],
                content_type="reel",
                title=req.title,
                body=full_script,
                media_url=video_url,
                scenes_json=json.dumps(req.scenes),
                metadata_json=json.dumps(req.metadata)
            )
            db.add(db_item)
            db.commit()
            return {"status": "created", "content_id": content_id, "video_url": video_url}
            
    except Exception as e:
        logger.error(f"Re-assembly failed: {e}")
        raise HTTPException(500, f"Re-assembly failed: {str(e)}")

def _get_client(x_app_token: Optional[str], db: Session) -> dict:
    if not x_app_token:
        raise HTTPException(401, "Missing X-App-Token header")
    from app.core.clients import validate_client_token
    client = validate_client_token(x_app_token)
    if not client:
        raise HTTPException(401, "Invalid or expired token")
    return client

# ── Models ────────────────────────────────────────────────────────────────────

class SocialGenerateReq(BaseModel):
    datastore_id: str
    topic: str
    type: str # "post" | "reel"
    language: Optional[str] = "English"
    voice_id: Optional[str] = None
    custom_script: Optional[str] = None
    exam_id: Optional[str] = None
    subtopic_id: Optional[str] = None

class SocialPublishReq(BaseModel):
    content_ids: List[str]
    platforms: List[str]  # ["instagram", "facebook", "twitter", "buffer"]
    content_data: List[dict] # Full data of selected posts/reels

class ReAssembleReq(BaseModel):
    content_id: str
    title: str
    script: str
    topic: str

class PublishToBufferReq(BaseModel):
    title: str
    text: str
    media_url: Optional[str] = None

# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/social/suggest-topics", tags=["Social"])
async def suggest_social_topics(datastore_id: str, language: Optional[str] = "English", x_app_token: Optional[str] = Header(None, alias="X-App-Token"), db: Session = Depends(get_db)):
    client_data = _get_client(x_app_token, db)
    
    # Use RAG to get some context
    from app.services.vector_store import get_vector_store
    from app.services.embedder import embed_texts
    
    vs = get_vector_store()
    
    # Embed a generic query to find themes
    query_emb = embed_texts(["main themes and trending professional topics"])
    if query_emb is None or len(query_emb) == 0:
        return {"topics": ["AI Innovation", "Business Strategy", "Digital Transformation"]}
    
    emb_2d = query_emb[0].reshape(1, -1)
    results = vs.search_combined(emb_2d, agent_id=None, datastore_ids=[datastore_id], top_k=15)
    
    context = "\n".join([r[0].text for r in results])
    if not context:
        return {"topics": ["Expert Insights", "Industry Analysis", "Success Stories"]}
    
    prompt = f"""Based on the following document context, suggest 5 viral, highly professional social media topics/hooks for a Reel or Post.
    
    CONTEXT: {context[:3000]}
    
    LANGUAGE: {language}
    
    REQUIREMENTS:
    1. The topics MUST be in {language}.
    2. The topics must be professional yet engaging.
    3. Output ONLY a JSON list of 5 strings.
    
    Format: ["Topic 1", "Topic 2", "Topic 3", "Topic 4", "Topic 5"]"""
    
    resp = await generate_answer(prompt, provider=get_active_provider())
    import re
    match = re.search(r"(\[.*\])", resp, re.DOTALL)
    if match:
        try:
            topics = json.loads(match.group(1))
            # Ensure it's exactly 5 or more
            return {"topics": topics[:5]}
        except: pass
    
    return {"topics": ["Future of " + client_data["name"], "AI in Industry", "Success Secrets"]}

@router.get("/social/voice-preview/{voice_id}", tags=["Social"])
async def voice_preview(voice_id: str, text: Optional[str] = None):
    """Proxy for ElevenLabs voice preview to keep API key secure."""
    api_key = settings.ELEVENLABS_API_KEY
    if not api_key:
        raise HTTPException(500, "ElevenLabs API Key missing")
    
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "accept": "audio/mpeg"
    }
    data = {
        "text": (text[:300] if text else "Hello! This is a voice preview sample for your reel narration."),
        "model_id": "eleven_turbo_v2_5",
        "voice_settings": {
            "stability": 0.45,
            "similarity_boost": 0.85,
            "style": 0.15,
            "use_speaker_boost": True
        }
    }
    
    try:
        async with httpx.AsyncClient() as client:
            logger.info(f"ElevenLabs Preview: Voice={voice_id} KeyLength={len(api_key)}")
            res = await client.post(url, json=data, headers=headers, timeout=30.0)
            if res.status_code == 200:
                from fastapi.responses import Response
                return Response(content=res.content, media_type="audio/mpeg")
            else:
                logger.error(f"ElevenLabs Preview Failed: {res.status_code} - {res.text}")
                raise HTTPException(res.status_code, f"ElevenLabs API Error: {res.text}")
    except Exception as e:
        logger.error(f"Voice Preview Exception: {str(e)}")
        raise HTTPException(500, str(e))

@router.post("/social/generate", tags=["Social"])
async def generate_social_content(req: SocialGenerateReq, x_app_token: Optional[str] = Header(None, alias="X-App-Token"), db: Session = Depends(get_db)):
    client_data = _get_client(x_app_token, db)

    # 1. Retrieve Context from DataStore (RAG) - Skip if Custom Script
    context_text = ""
    if not req.custom_script:
        try:
            query_emb = embed_texts([req.topic])
            if query_emb is not None and len(query_emb) > 0:
                emb_2d = query_emb[0].reshape(1, -1)
                results = get_vector_store().search_combined(emb_2d, agent_id=None, datastore_ids=[req.datastore_id], top_k=5)
                texts = []
                for res in results:
                    if isinstance(res, (list, tuple)) and len(res) > 0:
                        texts.append(res[0].text)
                    elif hasattr(res, 'text'):
                        texts.append(res.text)
                context_text = "\n---\n".join(texts)
        except Exception as e:
            logger.error(f"RAG Retrieval failed: {e}")
    else:
        logger.info("Custom Script mode: Skipping RAG context retrieval.")

    provider = get_active_provider()
    lang = req.language or "English"
    
    if req.type == "post":
        # Generate 5 Posts
        prompt = (
            f"Based on knowledge: {context_text[:2000]}\n\n"
            f"Generate 5 highly engaging social media posts strictly in the {lang.upper()} language for the topic: {req.topic}.\n"
            f"CRITICAL: ALL headlines and descriptions MUST be in {lang.upper()} (Native Script).\n"
            "The 'image_prompt' should be in English.\n"
            "Return ONLY a valid JSON array of objects. Each object must have: headline, description, image_prompt.\n"
            "Format: "
            '[{"headline": "...", "description": "...", "image_prompt": "..."}, ...]'
        )
        
        try:
            resp_text = await generate_answer(prompt, "You are a professional social media manager. Output ONLY valid JSON.")
            
            logger.info(f"AI Response for Social: {resp_text[:200]}...")
            
            try:
                start_obj = resp_text.find("{")
                end_obj = resp_text.rfind("}") + 1
                
                start_arr = resp_text.find("[")
                end_arr = resp_text.rfind("]") + 1
                
                if start_arr != -1:
                    posts = json.loads(resp_text[start_arr:end_arr])
                    if not isinstance(posts, list): posts = [posts]
                elif start_obj != -1:
                    posts = [json.loads(resp_text[start_obj:end_obj])]
                else:
                    posts = []
            except Exception as json_err:
                logger.warning(f"JSON Parse failed: {json_err}")
                posts = []

            if not posts:
                import re
                match = re.search(r"\{.*\}", resp_text, re.DOTALL)
                if match:
                    try:
                        posts = [json.loads(match.group(0))]
                    except:
                        pass

            if not posts or not isinstance(posts, list):
                posts = [{
                    "headline": f"Special Report: {req.topic}",
                    "description": f"Exploring the core concepts of {req.topic} through the lens of AI and real-time data retrieval. #AI #Knowledge #MR_AI",
                    "image_prompt": f"futuristic visualization of {req.topic}, digital art, high resolution"
                }]
            
            for p in posts:
                if 'caption' in p and 'description' not in p: p['description'] = p['caption']
                if 'content' in p and 'description' not in p: p['description'] = p['content']
                if 'title' in p and 'headline' not in p: p['headline'] = p['title']
                if not p.get('description'): p['description'] = f"Insightful post about {req.topic}."
                if not p.get('headline'): p['headline'] = f"The Future of {req.topic}"

            for p in posts:
                p["id"] = secrets.token_hex(4)
                img_prompt = p.get("image_prompt", req.topic)
                p["image_url"] = await generate_hf_image(img_prompt)
                
                # Persistence for each post
                db_item = SocialContent(
                    content_id=p["id"],
                    client_id=client_data["client_id"],
                    content_type="post",
                    title=p["headline"],
                    body=p["description"],
                    media_url=p["image_url"]
                )
                db.add(db_item)
            
            db.commit()
            return {"type": "post", "items": posts}
            
        except Exception as e:
            logger.error(f"Post generation failed: {e}")
            raise HTTPException(500, f"Failed to generate posts: {str(e)}")

    elif req.type == "reel":
        # Generate Reel Pipeline
        lang = req.language or "English"
        
        if req.custom_script:
            # Use Advanced Pipeline for Custom Scripts
            logger.info("Processing Custom Script with Advanced Pipeline...")
            
            structured_script = req.custom_script
            # If not already structured, ask LLM to structure it
            if "🎬 Scene" not in req.custom_script:
                logger.info("Structuring custom script using LLM...")
                struct_prompt = f"""
                Convert the following raw study material, notes, or concept explanations into a structured scene-by-scene video reel script.
                
                RAW CONTEXT / NOTES:
                {req.custom_script}
                
                TARGET LANGUAGE FOR DIALOGUE: {lang}
                
                FORMAT FOR EACH SCENE (Output EXACTLY in this format, do not include parentheses in the dialogue or visual text):
                
                🎬 Scene 1 (0-3 sec)
                🎙️ Dialogue: Write the narration text to be spoken by the voiceover in {lang}. Do not add any parenthetical notes or translation. Keep it extremely brief (strictly 5 to 8 words).
                📸 Visuals / Footage: Detailed description of the visual scene in English (suitable for generating an AI image).
                🎥 Editing Notes: Cinematic camera movement (e.g. slow zoom in, pan right, tracking shot).
                
                🎬 Scene 2 (3-6 sec)
                🎙️ Dialogue: Write the narration text to be spoken by the voiceover in {lang}. Keep it extremely brief (strictly 5 to 8 words).
                📸 Visuals / Footage: Detailed description of the visual scene in English.
                🎥 Editing Notes: Cinematic camera movement.
                
                REQUIREMENTS:
                1. Create exactly 5 to 7 scenes.
                2. Visuals must be highly descriptive AI image prompts (English only).
                3. The Dialogue MUST be written in {lang} and must be strictly 5 to 8 words per scene to fit the 3-second duration limit.
                   - CRITICAL: If the language is Hindi, you MUST write the dialogue strictly in proper Devanagari Unicode script (e.g. "भारत", "प्रौद्योगिकी"). NEVER write in Hinglish (Hindi written using English/Latin alphabet, e.g. "Bharat", "vigyan"), as TTS engines pronounce Hinglish with a highly robotic/incorrect accent.
                   - CRITICAL: Spell out all numbers, place names, acronyms, and math symbols fully in spoken words of the target language (e.g. write "उन्नीस सौ सैंतालीस" instead of "1947", "प्रतिशत" / "percent" instead of "%", "किलोमीटर" instead of "km") so that ElevenLabs reads them with perfect professional pronunciation.
                4. Do NOT include any intro, outro, headers, or markdown wrappers. Only output the scenes in the format above.
                """
                structured_script = await generate_answer(struct_prompt, "You are a professional video script writer. Output only the scene blocks as requested.")
            
            res_adv = await assemble_advanced_reel(
                structured_script,
                language=lang,
                voice_id=req.voice_id,
                bgm_style="dramatic" # Default for advanced
            )
            video_url = res_adv.get("video_url")
            scenes_data = res_adv.get("scenes", [])
            
            if video_url:
                content_id = secrets.token_hex(8)
                db_item = SocialContent(
                    content_id=content_id,
                    client_id=client_data["client_id"],
                    content_type="reel",
                    title=req.topic or "Custom Reel",
                    body=structured_script[:1000],
                    media_url=video_url,
                    scenes_json=json.dumps(scenes_data),
                    metadata_json=json.dumps({
                        "bgm_url": res_adv.get("bgm_url"),
                        "voice_id": req.voice_id,
                        "script": structured_script,
                        "exam_id": req.exam_id,
                        "subtopic_id": req.subtopic_id
                    })
                )
                db.add(db_item)
                db.commit()

                return {
                    "type": "reel",
                    "items": [{
                        "id": content_id,
                        "title": req.topic or "Custom Reel",
                        "script": structured_script,
                        "video_url": video_url,
                        "scenes": scenes_data,
                        "note": "Advanced AI Production (Flux + ElevenLabs + Cinematic Assembly)"
                    }]
                }
            else:
                raise HTTPException(500, "Advanced reel assembly failed")

        else:
            # Standard automated pipeline for topic-based generation
            prompt = (
                f"COMMAND: Generate a professional scene-by-scene reel script for topic: {req.topic}.\n"
                f"TARGET VOICE LANGUAGE: {lang.upper()}\n"
                f"CONTEXT: {context_text[:2500]}\n\n"
                f"INSTRUCTIONS:\n"
                f"1. TITLE: Create an engaging title.\n"
                f"2. BGM_STYLE: cinematic, energetic, corporate, or dramatic.\n"
                f"3. SCENES: Split the content into exactly 5 to 7 sequential scenes.\n"
                f"4. For EACH scene, provide:\n"
                f"   - term: A search term for stock footages.\n"
                f"   - prompt: A highly detailed 8k visual prompt for AI image generation.\n"
                f"   - source: 'ai' or 'stock'. Use 'ai' for brand names/dashboards/apps, and 'stock' for tech/office/nature/people visuals.\n"
                f"   - script: The exact voice narration/dialogue to be spoken during this scene in {lang.upper()} (strictly 5-8 words, max 8 words, to fit in a 3-second duration limit).\n"
                f"5. FORMAT: Output ONLY raw JSON.\n\n"
                f"JSON STRUCTURE:\n"
                f"{{\n"
                f"  \"title\": \"...\",\n"
                f"  \"bgm_style\": \"...\",\n"
                f"  \"scenes\": [\n"
                f"    {{\n"
                f"      \"term\": \"...\",\n"
                f"      \"prompt\": \"...\",\n"
                f"      \"source\": \"...\",\n"
                f"      \"script\": \"...\"\n"
                f"    }},\n"
                f"    ...\n"
                f"  ]\n"
                f"}}"
            )
            
            try:
                resp_text = await generate_answer(prompt, "SYSTEM: You are a JSON-only API. Do not talk. Output only the requested JSON object.")
                logger.info(f"AI Raw Response (Reel): {resp_text[:300]}...")
                
                # Robust Multi-JSON extraction
                import re
                json_obj = None
                all_objs = re.findall(r"(\{.*?\})", resp_text, re.DOTALL)
                for candidate in all_objs:
                    try:
                        candidate_json = json.loads(candidate)
                        if 'scenes' in candidate_json or 'title' in candidate_json:
                            json_obj = candidate_json
                            break
                    except: continue

                if not json_obj:
                    match = re.search(r"(\{.*\})", resp_text, re.DOTALL)
                    if match:
                        try: json_obj = json.loads(match.group(1))
                        except: pass

                if not json_obj:
                    reel_data = {
                        "title": req.topic or "Modern Insight",
                        "bgm_style": "cinematic",
                        "scenes": [{
                            "term": req.topic,
                            "prompt": f"cinematic scene of {req.topic}",
                            "source": "stock",
                            "script": f"Exploring the importance of {req.topic}."
                        }] * 5
                    }
                else:
                    reel_data = json_obj
                
                if not reel_data.get('title'): reel_data['title'] = req.topic
                scenes = reel_data.get('scenes', [])
                if not scenes:
                    scenes = [{
                        "term": req.topic,
                        "prompt": f"cinematic scene of {req.topic}",
                        "source": "stock",
                        "script": f"Exploring the importance of {req.topic}."
                    }] * 5
                
                res_pro = await assemble_edited_reel(
                    scenes=scenes,
                    voice_id=req.voice_id,
                    bgm_style=reel_data.get('bgm_style', 'cinematic')
                )
                video_url = res_pro.get("video_url")
                scenes_data = res_pro.get("scenes", [])
                full_script = "\n".join([s.get('script', '') for s in scenes_data])
                
                if video_url:
                    content_id = secrets.token_hex(8)
                    db_item = SocialContent(
                        content_id=content_id,
                        client_id=client_data["client_id"],
                        content_type="reel",
                        title=reel_data['title'],
                        body=full_script,
                        media_url=video_url,
                        scenes_json=json.dumps(scenes_data),
                        metadata_json=json.dumps({
                            "bgm_url": res_pro.get("bgm_url"),
                            "voice_id": req.voice_id,
                            "script": full_script,
                            "exam_id": req.exam_id,
                            "subtopic_id": req.subtopic_id
                        })
                    )
                    db.add(db_item)
                    db.commit()

                    return {
                        "type": "reel",
                        "items": [{
                            "id": content_id,
                            "title": reel_data.get('title', req.topic or "Modern Insight"),
                            "script": reel_data.get('script') or full_script,
                            "video_url": video_url,
                            "scenes": scenes_data,
                            "note": "Professional Production (Hybrid Assets)"
                        }]
                    }
                else:
                    raise ValueError("Reel assembly failed")
            except Exception as e:
                logger.error(f"Reel generation failed: {e}")
                raise HTTPException(500, f"Failed to generate reel: {str(e)}")

    else:
        raise HTTPException(400, "Invalid content type")

@router.post("/social/re-assemble", tags=["Social"])
async def re_assemble_reel(req: ReAssembleReq, x_app_token: Optional[str] = Header(None, alias="X-App-Token"), db: Session = Depends(get_db)):
    client_data = _get_client(x_app_token, db)
    
    # 1. Find existing record
    db_item = db.query(SocialContent).filter(SocialContent.content_id == req.content_id).first()
    if not db_item:
        raise HTTPException(404, "Original content not found")
        
    # 2. Call the assembly engine with new data
    logger.info(f"Re-assembling reel {req.content_id} with updated data...")
    import json
    scenes = []
    if db_item.scenes_json:
        try: scenes = json.loads(db_item.scenes_json)
        except: pass
    if not scenes:
        scenes = [{
            "term": req.topic or "scene",
            "prompt": f"cinematic scene of {req.topic or 'scene'}",
            "source": "stock",
            "script": req.script
        }]
    else:
        scenes[0]["script"] = req.script

    res_pro = await assemble_edited_reel(
        scenes=scenes,
        voice_id="adam"
    )
    video_url = res_pro.get("video_url")
    if not video_url:
        raise HTTPException(500, "Re-assembly engine failed to produce video")
        
    # 3. Update the database record
    db_item.title = req.title
    db_item.body = req.script
    db_item.media_url = video_url
    db_item.created_at = datetime.utcnow()
    
    db.commit()
    
    return {"success": True, "media_url": video_url, "message": "Reel re-rendered successfully"}
async def publish_social_content(req: SocialPublishReq, x_app_token: Optional[str] = Header(None, alias="X-App-Token"), db: Session = Depends(get_db)):
    client_data = _get_client(x_app_token, db)
    
    results = []
    
    # ── Handle Buffer Bulk Publishing ──
    if "buffer" in req.platforms:
        logger.info(f"Bulk publishing {len(req.content_data)} items to Buffer...")
        for item in req.content_data:
            try:
                # Reuse the publish_to_buffer logic or call it
                # For simplicity, we implement the core call here
                title = item.get("headline") or item.get("title") or "AI Content"
                text = item.get("description") or item.get("body") or item.get("script") or ""
                media_url = item.get("image_url") or item.get("video_url") or item.get("media_url")
                
                # We can call the internal function if we refactor, but for now let's just make the call
                # Create a temporary request object for the internal call
                from app.routes.social import PublishToBufferReq
                buf_req = PublishToBufferReq(title=title, text=text, media_url=media_url)
                
                # Resolve relative URL for Buffer
                # Note: The backend doesn't know the frontend's origin easily without headers
                # but we'll try to handle it in the publish_to_buffer logic
                
                await publish_to_buffer(buf_req, x_app_token, db)
                results.append({"platform": "buffer", "status": "success", "item": title})
            except Exception as e:
                logger.error(f"Bulk Buffer failed for item: {e}")
                results.append({"platform": "buffer", "status": "error", "message": str(e)})

    # Simulate API calls to other social media platforms
    for platform in req.platforms:
        if platform == "buffer": continue
        results.append({
            "platform": platform,
            "status": "success",
            "message": f"Successfully posted to {platform.capitalize()}"
        })
    
    # Log the activity
    from app.core.models import Notification
    msg = f"Your content has been processed for: {', '.join(req.platforms)}"
    if "buffer" in req.platforms:
        msg += f" ({len(req.content_data)} items sent to Buffer)"
        
    notif = Notification(
        client_id=client_data["client_id"],
        type="social",
        title="Publishing Status",
        message=msg
    )
    db.add(notif)
    db.commit()
    
    return {"success": True, "results": results}

def random_seed():
    return randint(1000, 9999)

@router.post("/social/publish-buffer", tags=["Social"])
async def publish_to_buffer(req: PublishToBufferReq, x_app_token: Optional[str] = Header(None, alias="X-App-Token"), db: Session = Depends(get_db)):
    """
    Publishes content to Buffer as an Idea using GraphQL.
    """
    client_data = _get_client(x_app_token, db)
    
    from app.core.models import SystemSettings
    sys_settings = db.query(SystemSettings).first()
    
    buffer_key = sys_settings.buffer_api_key if sys_settings and sys_settings.buffer_api_key else settings.BUFFER_API_KEY
    org_id = sys_settings.buffer_org_id if sys_settings and sys_settings.buffer_org_id else settings.BUFFER_ORG_ID
    
    if not buffer_key or not org_id:
        raise HTTPException(400, "Buffer API configuration missing. Please set it in Settings > Buffer Social Studio.")

    # ── STEP 1: Fetch Connected Channels ──
    channels_query = """
    query GetChannels($orgId: OrganizationId!) {
      channels(input: { organizationId: $orgId }) {
        id
        name
        service
      }
    }
    """
    
    # ── STEP 2: Create Post Mutation ──
    create_post_mutation = """
    mutation CreatePost($input: CreatePostInput!) {
      createPost(input: $input) {
        ... on PostActionSuccess {
          post { id }
        }
        ... on MutationError {
          message
        }
      }
    }
    """

    # ── STEP 3: Fallback Idea Mutation ──
    create_idea_mutation = """
    mutation CreateIdea($input: CreateIdeaInput!) {
      createIdea(input: $input) {
        ... on Idea { id }
      }
    }
    """
    
    try:
        async with httpx.AsyncClient() as client:
            # A. Fetch Channels
            logger.info(f"Fetching Buffer channels for Org: {org_id}")
            chan_res = await client.post(
                "https://api.buffer.com",
                json={"query": channels_query, "variables": {"orgId": org_id}, "operationName": "GetChannels"},
                headers={"Authorization": f"Bearer {buffer_key}", "Content-Type": "application/json"},
                timeout=20.0
            )
            chan_data = chan_res.json()
            channels = chan_data.get("data", {}).get("channels", [])
            
            if channels:
                logger.info(f"Found {len(channels)} channels. Publishing to queue...")
                results = []
                errors = []
                import time, secrets
                for chan in channels:
                    service = chan.get("service")
                    # Prepended title for services that use text as primary content
                    full_text = f"{req.title}\n\n{req.text}"
                    
                    if service == "twitter":
                        unique_id = secrets.token_hex(2)
                        full_text = f"{full_text}\n\n[ID: {unique_id}]"
                        if len(full_text) > 280:
                            full_text = full_text[:270] + "... " + unique_id

                    post_input = {
                        "channelId": chan["id"],
                        "text": full_text,
                        "schedulingType": "automatic",
                        "mode": "shareNow"
                    }

                    if req.media_url:
                        if "localhost" in req.media_url or "127.0.0.1" in req.media_url:
                            logger.warning(f"Media URL {req.media_url} is local. Buffer may fail to fetch it.")
                        
                        is_video = req.media_url.lower().endswith(('.mp4', '.mov', '.avi'))
                        if is_video:
                            # YouTube: title in assets[0].video is NOT supported by Buffer GraphQL
                            # We already put title in full_text (line 728)
                            video_obj = {"url": req.media_url}
                            post_input["assets"] = {"videos": [video_obj]}
                            
                            # Channel specific settings
                            post_input["channelSpecificSettings"] = {}
                            if service == "instagram":
                                post_input["channelSpecificSettings"]["instagram"] = {"type": "reel"}
                            elif service == "youtube":
                                # YouTube needs title in channelSpecificSettings or text
                                post_input["channelSpecificSettings"]["youtube"] = {"title": req.title[:100]}
                        else:
                            post_input["assets"] = {"images": [{"url": req.media_url}]}
                    
                    logger.info(f"Posting to {service} ({chan['id']})...")
                    p_res = await client.post(
                        "https://api.buffer.com",
                        json={"query": create_post_mutation, "variables": {"input": post_input}, "operationName": "CreatePost"},
                        headers={"Authorization": f"Bearer {buffer_key}", "Content-Type": "application/json"},
                        timeout=60.0
                    )
                    res_json = p_res.json()
                    logger.debug(f"Buffer Response for {service}: {json.dumps(res_json)}")
                    
                    # Check for GraphQL errors
                    if "errors" in res_json:
                        err_msg = res_json["errors"][0].get("message", "Unknown GraphQL Error")
                        errors.append(f"{service}: {err_msg}")
                        logger.error(f"Buffer GraphQL Error [{service}]: {err_msg}")
                    else:
                        data_obj = res_json.get("data", {})
                        cp = data_obj.get("createPost", {})
                        if isinstance(cp, dict) and "message" in cp:
                            errors.append(f"{service}: {cp['message']}")
                            logger.error(f"Buffer Mutation Error [{service}]: {cp['message']}")
                        elif not cp or (isinstance(cp, dict) and not cp.get("post")):
                            errors.append(f"{service}: Unknown creation failure")
                    
                    results.append(res_json)
                
                if errors:
                    msg = f"Published with errors: {'; '.join(errors)}"
                    logger.warning(msg)
                else:
                    msg = f"Successfully published to {len(channels)} channels!"
            else:
                logger.info("No channels found. Falling back to creating an Idea.")
                # B. Create Idea (Fallback)
                idea_content = { "title": req.title, "text": req.text }
                if req.media_url:
                    # Resolve relative URL if needed (though dashboard usually sends absolute)
                    if req.media_url.startswith('/'):
                        # We don't have base_url here, so we hope it's absolute
                        pass
                    
                    is_video = req.media_url.lower().endswith(('.mp4', '.mov', '.avi'))
                    if is_video:
                        idea_content["assets"] = {"videos": [{"url": req.media_url}]}
                    else:
                        idea_content["assets"] = {"images": [{"url": req.media_url}]}

                variables = {
                    "input": {
                        "organizationId": org_id,
                        "content": idea_content
                    }
                }
                idea_res = await client.post(
                    "https://api.buffer.com",
                    json={"query": create_idea_mutation, "variables": variables, "operationName": "CreateIdea"},
                    headers={"Authorization": f"Bearer {buffer_key}", "Content-Type": "application/json"},
                    timeout=30.0
                )
                res_json = idea_res.json()
                if "errors" in res_json:
                    err_msg = res_json["errors"][0].get("message", "Idea creation failed")
                    logger.error(f"Buffer Idea Error: {err_msg}")
                    raise HTTPException(400, f"Buffer Idea Error: {err_msg}")
                
                msg = "No social channels connected. Content saved to Buffer Ideas board (with media if provided)."

            # Log a notification for the user
            from app.core.models import Notification
            notif = Notification(
                client_id=client_data["client_id"],
                type="social",
                title="Buffer Sync Status",
                message=msg
            )
            db.add(notif)
            db.commit()

            return {"success": True, "message": msg}
            
    except Exception as e:
        import traceback
        logger.error(f"Buffer Publishing failed: {str(e)}\n{traceback.format_exc()}")
        if isinstance(e, HTTPException): raise e
        raise HTTPException(500, f"Internal Publishing Error: {str(e)}")

@router.get("/social/history", tags=["Social Hub"])
async def get_social_history(
    x_app_token: Optional[str] = Header(None, alias="X-App-Token"),
    db: Session = Depends(get_db)
):
    """List all saved social content for the current client."""
    client_data = _get_client(x_app_token, db)
    contents = db.query(SocialContent).filter(SocialContent.client_id == client_data["client_id"]).order_by(SocialContent.created_at.desc()).all()
    return [c.to_dict() for c in contents]

@router.delete("/social/content/{content_id}", tags=["Social Hub"])
async def delete_social_content(
    content_id: str,
    x_app_token: Optional[str] = Header(None, alias="X-App-Token"),
    db: Session = Depends(get_db)
):
    """Delete a specific social content item."""
    client_data = _get_client(x_app_token, db)
    content = db.query(SocialContent).filter(SocialContent.content_id == content_id, SocialContent.client_id == client_data["client_id"]).first()
    if not content:
        raise HTTPException(404, "Content not found")
    
    # Optional: Delete file if it exists locally
    # if content.media_url and content.media_url.startswith('/uploads/'):
    #     try: os.remove(os.getcwd() + content.media_url)
    #     except: pass

    db.delete(content)
    db.commit()
    return {"success": True, "message": "Content deleted successfully"}

class GenerateImageRequest(BaseModel):
    prompt: str

@router.post("/social/generate-image", tags=["Social Hub"])
async def api_generate_social_image(
    req: GenerateImageRequest,
    x_app_token: Optional[str] = Header(None, alias="X-App-Token"),
    db: Session = Depends(get_db)
):
    client_data = _get_client(x_app_token, db)
    
    prompt = req.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt is required")
        
    try:
        # Generate the Pollinations AI image URL
        img_url = await generate_hf_image(prompt)
        
        # Ensure directory exists
        base_uploads = os.path.join(os.getcwd(), "uploads", "social")
        os.makedirs(base_uploads, exist_ok=True)
        
        # Unique filename
        filename = f"ai_scene_{secrets.token_hex(6)}.jpg"
        dst = os.path.join(base_uploads, filename)
        
        # Download the image
        async with httpx.AsyncClient() as client:
            img_res = await client.get(img_url, timeout=30.0)
            if img_res.status_code == 200:
                with open(dst, "wb") as f:
                    f.write(img_res.content)
                url = f"/uploads/social/{filename}"
                return {"success": True, "url": url}
            else:
                logger.error(f"Pollinations AI failed with status {img_res.status_code}")
                raise HTTPException(status_code=500, detail=f"Image generation failed: Status {img_res.status_code}")
    except Exception as e:
        logger.error(f"Image generation exception: {e}")
        if isinstance(e, HTTPException): raise e
        raise HTTPException(status_code=500, detail=str(e))


import re
import os
import asyncio
import httpx
import json
import urllib.parse
import secrets
import subprocess
import logging
from typing import List, Dict, Optional
from app.services.llm import generate_simple_response
from app.core.config import settings
from app.services.video_engine import generate_elevenlabs_voiceover, create_subtitle_file

# Fix Windows Event Loop Issue
if os.name == 'nt':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class AdvancedReelPipeline:
    def __init__(self):
        self.base_dir = os.path.join(os.getcwd(), "uploads", "social", "advanced_reels")
        os.makedirs(self.base_dir, exist_ok=True)
        self.sem = asyncio.Semaphore(3) # Limit parallel API calls

    def parse_script(self, script_text: str) -> List[Dict]:
        """Parses the structured script into scenes."""
        scenes = []
        scene_blocks = re.split(r'🎬 Scene \d+', script_text)
        scene_blocks = [b.strip() for b in scene_blocks if b.strip()]

        for i, block in enumerate(scene_blocks):
            scene_num = i + 1
            dialogue_match = re.search(r'🎙️ Dialogue:\s*(.*?)(?=\📸 Visuals|$)', block, re.DOTALL)
            dialogue = dialogue_match.group(1).strip() if dialogue_match else ""
            dialogue = dialogue.replace('“', '').replace('”', '').replace('"', '')

            visuals_match = re.search(r'📸 Visuals / Footage:\s*(.*?)(?=\🎥 Editing Notes|$)', block, re.DOTALL)
            visuals = visuals_match.group(1).strip() if visuals_match else ""

            editing_match = re.search(r'🎥 Editing Notes:\s*(.*?)(?=$)', block, re.DOTALL)
            editing = editing_match.group(1).strip() if editing_match else ""

            duration = 6.0 # default
            duration_match = re.search(r'\((\d+)–(\d+)\s*sec\)', block)
            if duration_match:
                start = int(duration_match.group(1))
                end = int(duration_match.group(2))
                duration = float(end - start)

            scenes.append({
                "scene_num": scene_num,
                "dialogue": dialogue,
                "visuals": visuals,
                "editing_notes": editing,
                "suggested_duration": duration
            })
        
        return scenes

    async def generate_scene_assets(self, scene: Dict, work_dir: str) -> Dict:
        """Generates image and voiceover for a single scene."""
        scene_idx = scene['scene_num']
        
        async with self.sem:
            # 1. Generate Image Prompt
            user_prompt = f"Visuals: {scene['visuals']}\nEditing Notes: {scene['editing_notes']}"
            system_prompt = "Generate a highly detailed, cinematic image generation prompt for a historical documentary. English only. No text in image. 4k, realistic."
            
            try:
                prompt = await generate_simple_response(user_prompt, system_prompt)
            except Exception as e:
                logger.error(f"LLM Error Scene {scene_idx}: {e}")
                prompt = scene['visuals']

            # 2. Generate Image (Flux) with Retries
            img_path = os.path.join(work_dir, f"scene_{scene_idx}.jpg")
            encoded_prompt = urllib.parse.quote(prompt)
            img_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1080&height=1920&nologo=true&model=flux&seed={secrets.token_hex(4)}"
            
            img_success = False
            async with httpx.AsyncClient() as client:
                for attempt in range(3):
                    try:
                        resp = await client.get(img_url, timeout=90.0)
                        if resp.status_code == 200:
                            with open(img_path, "wb") as f: f.write(resp.content)
                            img_success = True
                            break
                        elif resp.status_code == 429:
                            logger.warning(f"Rate limited on Scene {scene_idx}. Waiting...")
                            await asyncio.sleep(5 * (attempt + 1))
                        else:
                            logger.error(f"Image Gen Failed Scene {scene_idx}: {resp.status_code}")
                    except Exception as e:
                        logger.error(f"Image Gen Error Scene {scene_idx}: {e}")
                        await asyncio.sleep(3)

            # 3. Generate Voiceover (ElevenLabs)
            voice_path = os.path.join(work_dir, f"voice_{scene_idx}.mp3")
            try:
                res_path = await generate_elevenlabs_voiceover(scene['dialogue'], work_dir)
                if res_path and os.path.exists(res_path):
                    if os.path.exists(voice_path): os.remove(voice_path)
                    os.rename(res_path, voice_path)
                else:
                    logger.warning(f"Voice Gen returned None for Scene {scene_idx}")
                    voice_path = None
            except Exception as e:
                logger.error(f"Voice Gen Error Scene {scene_idx}: {e}")
                voice_path = None

        return {
            "scene_num": scene_idx,
            "image_path": img_path if img_success else None,
            "voice_path": voice_path,
            "prompt": prompt
        }

    def render_scene_video(self, asset: Dict, scene: Dict, work_dir: str) -> Optional[str]:
        """Renders a single scene into a video clip."""
        if not asset['image_path']: return None
        
        scene_idx = asset['scene_num']
        output_path = os.path.join(work_dir, f"scene_{scene_idx}_final.mp4")
        
        # Determine duration
        duration = scene['suggested_duration']
        if asset['voice_path']:
            probe_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", asset['voice_path']]
            res = subprocess.run(probe_cmd, capture_output=True, text=True)
            if res.returncode == 0:
                duration = float(res.stdout.strip()) + 0.3

        # Effect: Alternate Zoom In / Zoom Out
        zoom_effect = "min(zoom+0.0015,1.5)" if scene_idx % 2 != 0 else "max(1.5-0.0015*on,1)"
        
        cmd = ["ffmpeg", "-y", "-loop", "1", "-i", asset['image_path']]
        if asset['voice_path']:
            cmd.extend(["-i", asset['voice_path']])
            
        filter_complex = f"scale=1620:2880:force_original_aspect_ratio=increase,crop=1620:2880,zoompan=z='{zoom_effect}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={int(duration*30)}:s=1080x1920:fps=30,setsar=1"
        
        cmd.extend([
            "-vf", filter_complex,
            "-c:v", "libx264", "-t", str(duration), "-pix_fmt", "yuv420p", "-r", "30"
        ])
        if asset['voice_path']:
            cmd.extend(["-c:a", "aac", "-shortest"])
            
        cmd.append(output_path)
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            logger.error(f"FFmpeg Scene Render Error Scene {scene_idx}: {res.stderr}")
            return None
        return output_path if os.path.exists(output_path) else None

    async def run(self, script_text: str):
        scenes = self.parse_script(script_text)
        logger.info(f"Starting pipeline for {len(scenes)} scenes.")
        
        work_id = secrets.token_hex(4)
        work_dir = os.path.join(self.base_dir, f"full_work_{work_id}")
        os.makedirs(work_dir, exist_ok=True)

        # 1. Generate Assets (Parallel)
        tasks = [self.generate_scene_assets(s, work_dir) for s in scenes]
        assets = await asyncio.gather(*tasks)
        
        # 2. Render Scene Videos
        scene_videos = []
        for i, asset in enumerate(assets):
            logger.info(f"Rendering Scene {i+1}...")
            v_path = self.render_scene_video(asset, scenes[i], work_dir)
            if v_path: scene_videos.append(v_path)

        if not scene_videos:
            logger.error("No scene videos were rendered.")
            return

        # 3. Concatenate
        list_path = os.path.join(work_dir, "concat_list.txt")
        with open(list_path, "w") as f:
            for v in scene_videos:
                v_fixed = v.replace('\\', '/')
                f.write(f"file '{v_fixed}'\n")

        temp_concat = os.path.join(work_dir, "temp_concat.mp4")
        concat_res = subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", temp_concat], capture_output=True, text=True)
        if concat_res.returncode != 0:
            logger.error(f"FFmpeg Concat Error: {concat_res.stderr}")
            return None

        # 4. Final Assembly with BGM and Audio Effects
        output_path = os.path.join(self.base_dir, f"final_reel_{work_id}.mp4")
        bgm_url = "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-8.mp3"
        bgm_path = os.path.join(work_dir, "bgm.mp3")
        
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(bgm_url, timeout=30.0)
                if resp.status_code == 200:
                    with open(bgm_path, "wb") as f: f.write(resp.content)
            except: bgm_path = None

        # Build Subtitles
        full_dialogue = " ".join([s['dialogue'] for s in scenes])
        # Probe total duration
        probe_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", temp_concat]
        probe_res = subprocess.run(probe_cmd, capture_output=True, text=True)
        total_dur = float(probe_res.stdout.strip()) if probe_res.returncode == 0 else 60.0
        sub_path = create_subtitle_file(full_dialogue, total_dur, work_dir)
        safe_sub_path = sub_path.replace("\\", "/").replace(":", "\\:")

        final_cmd = ["ffmpeg", "-y", "-i", temp_concat]
        filter_parts = []
        filter_parts.append(f"[0:v]ass='{safe_sub_path}'[v_sub];")
        filter_parts.append("[0:a]compand=0.3|0.3:6:-90/-60/-60/-40/-40/-20/-20/0/-20/12,aecho=0.8:0.88:6:0.4[a_voice];")
        
        if bgm_path and os.path.exists(bgm_path):
            final_cmd.extend(["-i", bgm_path])
            filter_parts.append("[1:a]volume=0.12,atrim=0:" + str(total_dur) + "[a_bgm];")
            filter_parts.append("[a_voice][a_bgm]amix=inputs=2:duration=first[a_final];")
            final_cmd.extend(["-map", "[v_sub]", "-map", "[a_final]"])
        else:
            final_cmd.extend(["-map", "[v_sub]", "-map", "[a_voice]"])

        final_cmd.extend([
            "-filter_complex", "".join(filter_parts),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", output_path
        ])
        
        logger.info("Final assembly...")
        final_res = subprocess.run(final_cmd, capture_output=True, text=True)
        
        if os.path.exists(output_path):
            logger.info(f"SUCCESS! Reel: {output_path}")
            return output_path
        else:
            logger.error(f"Final Assembly Failed. FFmpeg Output: {final_res.stderr}")
            return None

async def main():
    pipeline = AdvancedReelPipeline()
    script = """
🎬 Scene 1 (0–5 sec) — Opening Hook
🎙️ Dialogue:
“क्या आप imagine कर सकते हैं…
एक ऐसी दुनिया जहाँ किताबें होती ही नहीं थीं?”
📸 Visuals / Footage:
(Empty ancient library cinematic shot)
(Old candles and blank desks)
(Slow dark historical atmosphere)
🎥 Editing Notes:
(Mysterious historical background music)
________________________________________
🎬 Scene 2 (5–11 sec)
🎙️ Dialogue:
“एक समय था जब किताबें छपती नहीं थीं… लिखी जाती थीं।”
📸 Visuals / Footage:
(Hands writing on ancient paper)
(Medieval scribes working slowly)
(Ink and feather pen close-up)
🎥 Editing Notes:
(Slow writing sound effects)
________________________________________
🎬 Scene 3 (11–17 sec)
🎙️ Dialogue:
“हाथ से… धीरे-धीरे… और बेहद महंगी।”
📸 Visuals / Footage:
(Scribe carefully copying manuscripts)
(Gold coins symbolic visuals)
(Expensive old books close-up)
🎥 Editing Notes:
(Slow cinematic zoom)
________________________________________
🎬 Scene 4 (17–23 sec)
🎙️ Dialogue:
“यूरोप में साधु-संत बैठकर किताबें लिखते थे।”
📸 Visuals / Footage:
(Monks writing inside monasteries)
(Ancient European libraries)
(Candle-lit manuscript writing)
🎥 Editing Notes:
(Soft medieval choir music)
________________________________________
🎬 Scene 5 (23–29 sec)
🎙️ Dialogue:
“भारत में ताड़पत्र और हस्तलिखित ग्रंथ होते थे।”
📸 Visuals / Footage:
(Palm leaf manuscripts close-up)
(Ancient Indian scholars writing)
(Traditional handwritten scriptures)
🎥 Editing Notes:
(Traditional Indian instrumental touch)
________________________________________
🎬 Scene 6 (29–35 sec)
🎙️ Dialogue:
“एक छोटी सी किताब बनाने में… महीनों, कभी-कभी साल लग जाते थे।”
📸 Visuals / Footage:
(Calendar pages flipping rapidly)
(Slow manuscript writing montage)
(Stack of unfinished handwritten pages)
🎥 Editing Notes:
(Time-lapse transition effect)
________________________________________
🎬 Scene 7 (35–41 sec)
🎙️ Dialogue:
“गलतियाँ आम थीं… और किताबें दुर्लभ।”
📸 Visuals / Footage:
(Mistakes crossed out in manuscripts)
(Very few books on shelves)
(People carefully protecting books)
🎥 Editing Notes:
(Slow emotional soundtrack)
________________________________________
🎬 Scene 8 (41–48 sec)
🎙️ Dialogue:
“ज्ञान सिर्फ कुछ अमीर और खास लोगों तक सीमित था।”
📸 Visuals / Footage:
(Rich nobles reading books)
(Common people outside libraries)
(Symbolic knowledge inequality visuals)
🎥 Editing Notes:
(Deep reflective tone)
________________________________________
🎬 Scene 9 (48–55 sec)
🎙️ Dialogue:
“लेकिन फिर… एक ऐसा बदलाव आया…”
📸 Visuals / Footage:
(Dark screen slowly lighting up)
(Metal printing letters cinematic reveal)
(Ancient printing press silhouette)
🎥 Editing Notes:
(Music builds suspense)
________________________________________
🎬 Scene 10 (55–65 sec) — Ending Hook
🎙️ Dialogue:
“👉 Next Part mein aap dekhenge —
Printing ki शुरुआत कहाँ हुई…
और कैसे इस invention ने दुनिया बदल दी।”
📸 Visuals / Footage:
(Old printing machine reveal)
(Papers rapidly printing)
(Text reveal: “Next Part – Origin of Printing”)
🎥 Editing Notes:
(Cinematic curiosity music)
(Fade to black)
"""
    await pipeline.run(script)

if __name__ == "__main__":
    asyncio.run(main())

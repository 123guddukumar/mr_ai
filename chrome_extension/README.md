# MR AI Reel Generator - Chrome Extension

## Installation

1. Open Chrome → `chrome://extensions/`
2. Enable **Developer Mode** (top right toggle)
3. Click **Load unpacked**
4. Select this `chrome_extension` folder
5. Extension will appear in toolbar — pin it!

## Icons Setup
Replace these placeholder files with actual PNG icons:
- `icon16.png` — 16×16 pixels
- `icon48.png` — 48×48 pixels  
- `icon128.png` — 128×128 pixels

You can use any image editor or online tool to create simple colored square icons.

## How to Use

### Step 1: Dashboard
1. Go to your dashboard → Classroom → Select a Subtopic
2. Click **🔌 Extension Reel** button
3. A Job ID will be generated — **copy it**

### Step 2: Extension Popup
1. Click the extension icon in Chrome toolbar
2. Enter your **Backend URL** (default: `http://localhost:8000`)
3. Enter your **X-App-Token** (from dashboard login)
4. Paste the **Job ID**
5. Click **🚀 Start Pipeline**

### Step 3: Automatic Pipeline
The extension will automatically:
1. Open **Google Flow** → inject all image prompts → generate & download images
2. Open **Meta AI** → upload each image → generate video with animation → download
3. Notify backend → assemble final reel with voice + BGM + subtitles

### Step 4: Done!
- Reel URL appears in extension popup
- Also visible in dashboard Reels panel

## Pipeline Flow
```
Dashboard → Create Job → Job ID
    ↓
Extension → Fetch Scenes from Backend
    ↓
Google Flow → Generate 12 images (9:16) → Auto download
    ↓
Meta AI → Generate 12 videos (animated) → Auto download
    ↓
Backend → Voice (ElevenLabs) + BGM + Subtitles → Final Reel MP4
```

## Troubleshooting

**Images not generating?**
- Make sure you're logged into Google Flow
- The extension needs the page to fully load before injecting prompts

**Videos not generating?**
- Make sure you're logged into Meta AI
- Meta AI may require manual image selection from Downloads folder

**Backend not connecting?**
- Make sure backend is running on `http://localhost:8000`
- Check that CORS is enabled (it is by default)

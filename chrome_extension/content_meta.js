// ── Meta AI Content Script ─────────────────────────────────────────────────────
// Generates BOTH image and video from Meta AI for each scene.
// Flow per scene: type image prompt → generate → download image → 4s wait →
//                 type video prompt → generate → download video → 4s wait → next scene

let metaSceneIdx = 0;
let metaJobId = null;
let metaSubtopicName = "";
let metaScene = null;   // { image_prompt, animation_prompt, dialogue }
let metaIsProcessing = false;

// Track last seen generated content to detect new ones
window._lastImgSrcs = new Set();
window._lastVideoSrcs = new Set();

// ── Message listener ──────────────────────────────────────────────────────────
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === 'GENERATE_VIDEO') {
    if (metaIsProcessing) { sendResponse({ ok: false, reason: 'busy' }); return true; }
    metaSceneIdx = msg.sceneIdx;
    metaJobId = msg.jobId;
    metaSubtopicName = msg.subtopicName || '';
    metaScene = {
      image_prompt: msg.imagePrompt || msg.animationPrompt || '',
      animation_prompt: msg.animationPrompt || '',
      dialogue: msg.dialogue || ''
    };
    log(`Scene ${metaSceneIdx + 1}: starting`);
    processScene();
    sendResponse({ ok: true });
  }
  // ── Single Library Asset Generation ────────────────────────────────────────
  if (msg.type === 'GENERATE_SINGLE_ASSET') {
    if (metaIsProcessing) { sendResponse({ ok: false, reason: 'busy' }); return true; }
    metaIsProcessing = true;
    log(`Single asset: type=${msg.mediaType}, prompt=${msg.prompt?.slice(0, 60)}`);
    generateSingleAsset(msg.prompt, msg.mediaType, msg.filename).then(() => {
      metaIsProcessing = false;
      sendResponse({ ok: true });
    }).catch((e) => {
      metaIsProcessing = false;
      log(`Single asset error: ${e.message}`);
      sendResponse({ ok: false, error: e.message });
    });
    return true; // keep channel open for async response
  }
  return true;
});

// ── Main scene processor ──────────────────────────────────────────────────────
async function processScene() {
  metaIsProcessing = true;
  try {
    await sleep(1500);
    // Auto-close any pre-existing preview modal before starting
    await closeMetaAIPreviewModal();

    // Step 1: Generate IMAGE from Meta AI
    log(`Scene ${metaSceneIdx + 1}: generating image...`);
    const imgFile = await generateImage();
    log(`Scene ${metaSceneIdx + 1}: image done → ${imgFile}`);

    // Notify background: image downloaded and wait for sync verification response
    log("Waiting for dashboard to sync the image...");
    const imgSyncResp = await new Promise((resolve) => {
      safeSendMessage({ type: 'IMAGE_DOWNLOADED', filename: imgFile }, (resp) => {
        resolve(resp);
      });
    });
    if (imgSyncResp && imgSyncResp.ok) {
      log("Image dashboard sync verified successfully!");
    } else {
      log("Warning: Image sync verification timed out or failed, continuing.");
    }

    // 4 second wait
    log('Waiting 4s before video...');
    await sleep(4000);

    // Step 2: Generate VIDEO from Meta AI
    log(`Scene ${metaSceneIdx + 1}: generating video...`);
    const vidFile = await generateVideo();
    log(`Scene ${metaSceneIdx + 1}: video done → ${vidFile}`);

    // Notify background: video downloaded and wait for sync verification response
    log("Waiting for dashboard to sync the video...");
    const vidSyncResp = await new Promise((resolve) => {
      safeSendMessage({ type: 'VIDEO_DOWNLOADED', filename: vidFile }, (resp) => {
        resolve(resp);
      });
    });
    if (vidSyncResp && vidSyncResp.ok) {
      log("Video dashboard sync verified successfully!");
    } else {
      log("Warning: Video sync verification timed out or failed, continuing.");
    }

    // 10 second wait before next scene to let the dashboard poll and render the generated assets
    log('Waiting 10 seconds so that the dashboard displays the scene image & video properly before starting the next scene...');
    await sleep(10000);

  } catch (e) {
    log(`Scene ${metaSceneIdx + 1} ERROR: ${e.message}`);
    // Still notify so pipeline doesn't hang
    let subtopicFirstWord = 'reel';
    if (metaSubtopicName) {
      const cleanSubtopic = metaSubtopicName.trim().replace(/[^a-zA-Z0-9\s-_]/g, '');
      const parts = cleanSubtopic.split(/\s+/);
      if (parts.length > 0 && parts[0]) {
        subtopicFirstWord = parts[0];
      }
    }
    safeSendMessage({ type: 'IMAGE_DOWNLOADED', filename: `meta-img-${metaSceneIdx + 1}-${subtopicFirstWord}-error.jpg` });
    safeSendMessage({ type: 'VIDEO_DOWNLOADED', filename: `meta-vid-${metaSceneIdx + 1}-${subtopicFirstWord}-error.mp4` });
  }
  metaIsProcessing = false;
  log(`Scene ${metaSceneIdx + 1} complete. Notifying background script.`);
  safeSendMessage({ type: 'SCENE_COMPLETED' });
}

// ── Generate Image ────────────────────────────────────────────────────────────
async function generateImage() {
  const initialCardsCount = getAssistantMessageCards().length;
  log(`Initial assistant message cards count before image generation: ${initialCardsCount}`);

  // Snapshot current images before generating
  snapshotCurrentImages();
  snapshotCurrentDownloadButtons();
  snapshotLastAssistantMessage();

  const prompt = metaScene.image_prompt || metaScene.animation_prompt;
  const fullPrompt = `Generate a high quality photorealistic image: ${prompt}. Vertical 9:16 portrait format, cinematic lighting, 4K quality, no text.`;

  await typeInChat(fullPrompt);
  await sleep(500);
  await clickSend();

  // Wait for new image to appear
  const imgSrc = await waitForNewImage(90, initialCardsCount);
  if (!imgSrc) throw new Error('Image generation timeout');

  // Download it
  let subtopicFirstWord = 'reel';
  if (metaSubtopicName) {
    const cleanSubtopic = metaSubtopicName.trim().replace(/[^a-zA-Z0-9\s-_]/g, '');
    const parts = cleanSubtopic.split(/\s+/);
    if (parts.length > 0 && parts[0]) {
      subtopicFirstWord = parts[0];
    }
  }
  const filename = `meta-img-${metaSceneIdx + 1}-${metaJobId || 'nojob'}-${subtopicFirstWord}.jpg`;
  await downloadFile(imgSrc, filename);
  await sleep(1000);
  return filename;
}

// ── Generate Video ────────────────────────────────────────────────────────────
async function generateVideo() {
  // Snapshot current videos
  snapshotCurrentVideos();
  snapshotCurrentDownloadButtons();
  snapshotLastAssistantMessage();

  const prompt = metaScene.animation_prompt || metaScene.image_prompt;
  const fullPrompt = `Animate the previously generated image. Create a smooth 5-second cinematic video animation: ${prompt}. Vertical 9:16 format, smooth camera movement, high quality.`;

  await typeInChat(fullPrompt);
  await sleep(500);
  await clickSend();

  // Wait for new video to appear
  const vidSrc = await waitForNewVideo(120);
  if (!vidSrc) {
    // Fallback: try download button
    const dlBtn = findDownloadButton();
    if (dlBtn) {
      let subtopicFirstWord = 'reel';
      if (metaSubtopicName) {
        const cleanSubtopic = metaSubtopicName.trim().replace(/[^a-zA-Z0-9\s-_]/g, '');
        const parts = cleanSubtopic.split(/\s+/);
        if (parts.length > 0 && parts[0]) {
          subtopicFirstWord = parts[0];
        }
      }
      const filename = `meta-vid-${metaSceneIdx + 1}-${metaJobId || 'nojob'}-${subtopicFirstWord}.mp4`;
      await downloadFileViaClick(dlBtn, filename);
      return filename;
    }
    throw new Error('Video generation timeout');
  }

  let subtopicFirstWord = 'reel';
  if (metaSubtopicName) {
    const cleanSubtopic = metaSubtopicName.trim().replace(/[^a-zA-Z0-9\s-_]/g, '');
    const parts = cleanSubtopic.split(/\s+/);
    if (parts.length > 0 && parts[0]) {
      subtopicFirstWord = parts[0];
    }
  }
  const filename = `meta-vid-${metaSceneIdx + 1}-${metaJobId || 'nojob'}-${subtopicFirstWord}.mp4`;
  await downloadFile(vidSrc, filename);
  await sleep(1000);
  return filename;
}

// ── Type in Meta AI chat input ────────────────────────────────────────────────
async function typeInChat(text) {
  // Ensure preview is closed so chat input is visible and editable
  await closeMetaAIPreviewModal();
  const selectors = [
    'div[contenteditable="true"][data-lexical-editor]',
    'div[contenteditable="true"][role="textbox"]',
    'div[contenteditable="true"]',
    'textarea[placeholder]',
    'div[role="textbox"]'
  ];

  let input = null;
  for (const sel of selectors) {
    input = await waitForElement(sel, 5000);
    if (input) { log(`Found input with: ${sel}`); break; }
  }
  if (!input) throw new Error('Chat input not found');

  input.focus();
  await sleep(300);

  // Clear existing
  input.innerHTML = '';
  document.execCommand('selectAll', false, null);
  document.execCommand('delete', false, null);
  await sleep(200);

  // Insert text
  if (input.tagName === 'TEXTAREA') {
    const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value');
    if (setter) setter.set.call(input, text);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  } else {
    document.execCommand('insertText', false, text);
    if (!input.textContent.trim()) {
      input.textContent = text;
      input.dispatchEvent(new InputEvent('input', { bubbles: true, data: text }));
    }
  }

  await sleep(400);
  log(`Typed: ${text.substring(0, 60)}...`);
}

// ── Click send button ─────────────────────────────────────────────────────────
async function clickSend() {
  // Try specific aria-label selectors first (Meta AI current UI)
  const specificSelectors = [
    'button[aria-label*="Send" i]',
    'button[aria-label*="send" i]',
    'button[data-testid*="send" i]',
    'button[type="submit"]',
  ];
  for (const sel of specificSelectors) {
    const btn = document.querySelector(sel);
    if (btn && !btn.disabled) { btn.click(); log(`Clicked send via: ${sel}`); return; }
  }

  // Generic search
  const btns = document.querySelectorAll('button, [role="button"]');
  for (const btn of btns) {
    const label = (btn.getAttribute('aria-label') || btn.getAttribute('title') || '').toLowerCase();
    const txt = (btn.textContent || '').toLowerCase().trim();
    if ((label.includes('send') || label.includes('submit') || txt === 'send' || txt === 'generate') && !btn.disabled) {
      btn.click(); log('Clicked send (generic)'); return;
    }
  }

  // Fallback: Enter key
  const input = document.querySelector('div[contenteditable="true"], textarea');
  if (input) {
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', keyCode: 13, bubbles: true, composed: true }));
    await sleep(100);
    input.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', keyCode: 13, bubbles: true, composed: true }));
    log('Pressed Enter as fallback');
  }
}

// ── Snapshot helpers ──────────────────────────────────────────────────────────
window._lastDownloadBtns = new Set();

function snapshotCurrentDownloadButtons() {
  window._lastDownloadBtns = new Set();
  document.querySelectorAll('button, [role="button"], a[download], a[href*="download"]').forEach(btn => {
    const label = (
      btn.getAttribute('aria-label') || btn.getAttribute('title') ||
      btn.getAttribute('download') || btn.textContent || ''
    ).toLowerCase();
    if (label.includes('download') || label.includes('save')) {
      window._lastDownloadBtns.add(btn);
    }
  });
}

function snapshotCurrentImages() {
  window._lastImgSrcs = new Set();
  document.querySelectorAll('img').forEach(img => {
    if (img.src && img.naturalWidth > 200) window._lastImgSrcs.add(img.src);
  });
}

function snapshotCurrentVideos() {
  window._lastVideoSrcs = new Set();
  document.querySelectorAll('video').forEach(v => {
    if (v.src) window._lastVideoSrcs.add(v.src);
    v.querySelectorAll('source').forEach(s => { if (s.src) window._lastVideoSrcs.add(s.src); });
  });
}

window._lastAssistantMsg = null;

function snapshotLastAssistantMessage() {
  const msgs = document.querySelectorAll('[role="article"], .chat-message, [class*="message" i]');
  window._lastAssistantMsg = msgs.length > 0 ? msgs[msgs.length - 1] : null;
  log(`Snapshotted last assistant message: ${window._lastAssistantMsg ? window._lastAssistantMsg.textContent.substring(0, 50) + "..." : "none"}`);
}

function getAssistantMessageCards() {
  return Array.from(document.querySelectorAll('[role="article"], .chat-message, [class*="message" i]'));
}

function findDownloadButtonInCard(card) {
  if (!card) return null;
  const buttons = card.querySelectorAll('button, [role="button"], a[download], a[href*="download"]');
  for (const btn of buttons) {
    const label = (
      btn.getAttribute('aria-label') || btn.getAttribute('title') ||
      btn.getAttribute('download') || btn.textContent || ''
    ).toLowerCase();
    if (label.includes('download') || label.includes('save')) {
      return btn;
    }
  }
  return null;
}

function isValidGeneratedImageUrl(src) {
  if (!src) return false;
  const s = src.toLowerCase();
  // Must be a remote http/https URL and not a blob or data URI
  if (!src.startsWith('http://') && !src.startsWith('https://')) return false;
  if (src.startsWith('blob:') || src.startsWith('data:')) return false;
  if (
    s.includes('avatar') || 
    s.includes('icon') || 
    s.includes('logo') || 
    s.includes('profile') || 
    s.includes('placeholder') || 
    s.includes('loading') || 
    s.includes('spinner') ||
    s.includes('preloader')
  ) {
    return false;
  }
  return true;
}

async function waitForNewImage(maxSec, initialCardsCount = 0) {
  let metaRetries = 0;
  for (let i = 0; i < maxSec; i++) {
    await sleep(1000);

    const assistantMessages = getAssistantMessageCards();
    
    // Check for safety block or error text in latest chat messages
    if (assistantMessages.length > 0) {
      const lastMsg = assistantMessages[assistantMessages.length - 1];
      if (lastMsg !== window._lastAssistantMsg) {
        const txt = (lastMsg.textContent || '').toLowerCase();
        if (
          txt.includes("snag") ||
          txt.includes("didn't render") ||
          txt.includes("did not render") ||
          txt.includes("can't generate") || 
          txt.includes("can't create") || 
          txt.includes("oops! i can't") ||
          txt.includes("cannot generate") ||
          txt.includes("wasn't able to") ||
          txt.includes("was not able to") ||
          txt.includes("unable to generate") ||
          txt.includes("against our guidelines") ||
          txt.includes("safety policies") ||
          txt.includes("safety system") ||
          txt.includes("flagged by") ||
          txt.includes("different approach") ||
          txt.includes("different take") ||
          txt.includes("stopped") ||
          txt.includes("request was stopped") ||
          txt.includes("something went wrong") ||
          txt.includes("try again") ||
          txt.includes("content filter") ||
          txt.includes("filter") ||
          txt.includes("sacred symbols") ||
          txt.includes("religious") ||
          txt.includes("flagged") ||
          txt.includes("sensitive") ||
          txt.includes("vibe without the block") ||
          txt.includes("run into") ||
          txt.includes("tweak") ||
          txt.includes("guidelines") ||
          txt.includes("policy")
        ) {
          log(`⚠️ Meta AI safety block or content filter error detected (Attempt ${metaRetries + 1}/15)!`);

          if (metaRetries >= 15) {
            log("❌ Max retries reached (15). Using a completely generic safe visual.");
            const superSafePrompt = "Cinematic educational abstract background, vertical 9:16, soft ambient lighting, high quality, 4K, no text.";
            log(`✍️ Sending super safe prompt: "${superSafePrompt}"`);
            await typeInChat(superSafePrompt);
            await sleep(500);
            await clickSend();
            const msgs = getAssistantMessageCards();
            window._lastAssistantMsg = msgs.length > 0 ? msgs[msgs.length - 1] : null;
            metaRetries = 12; // Reset retries to wait for super safe prompt
            await sleep(6000);
            continue;
          }
          metaRetries++;

          // 1. Check for suggestion chips/buttons inside the last message to click as a retry
          const suggestionBtns = Array.from(lastMsg.querySelectorAll('button, [role="button"]'));
          let clickedSuggestion = false;
          for (const btn of suggestionBtns) {
            const btnTxt = (btn.textContent || '').toLowerCase().trim();
            if (btnTxt && (btnTxt.includes("style") || btnTxt.includes("artistic") || btnTxt.includes("minimalist") || btnTxt.includes("different") || btnTxt.includes("globe") || btnTxt.includes("map") || btnTxt.includes("satellite") || btnTxt.includes("painted") || btnTxt.includes("take"))) {
              log(`👉 Clicking Meta AI suggestion chip: "${btn.textContent.trim()}" to bypass block...`);
              
              window._lastAssistantMsg = lastMsg;

              btn.addEventListener('click', (e) => e.stopPropagation(), { once: true });
              btn.click();
              clickedSuggestion = true;
              await sleep(5000);
              break;
            }
          }

          if (clickedSuggestion) {
            continue;
          }

          window._lastAssistantMsg = lastMsg;

          // 2. Progressive fallback prompt simplification (highly aggressive religious/country filter cleaning)
          let fallbackPrompt = "";
          const dialogue = metaScene.dialogue || metaScene.image_prompt || "educational concept";
          const safeDialogue = dialogue.replace(/India|Asia|border|map|government|politician|sacred|symbol|religion|religious|god|deity|meditat|temple|church|mosque|shrine|text|script|writing|holy|worship/gi, "peaceful landscape");

          if (metaRetries === 1 || metaRetries === 2) {
            fallbackPrompt = `Generate a high quality photorealistic educational scene representing: ${safeDialogue.substring(0, 100)}. Vertical 9:16 portrait format, cinematic lighting, 4K quality, no text.`;
          } else if (metaRetries === 3 || metaRetries === 4) {
            fallbackPrompt = `Generate a beautiful educational concept background representing: ${safeDialogue.substring(0, 50)}. Vertical 9:16 portrait format, cinematic, 4K, no text.`;
          } else {
            fallbackPrompt = `A serene beautiful vertical 9:16 background representing learning and science, cinematic lighting, 4K quality, no text.`;
          }
          
          log(`✍️ Sending fallback prompt: "${fallbackPrompt}"`);
          await typeInChat(fallbackPrompt);
          await sleep(500);
          await clickSend();
          
          await sleep(5000);
          continue;
        }
      }
    }

    // Wait until the new assistant message card is present
    const effectiveInitialCount = initialCardsCount > 0 ? initialCardsCount : Math.max(0, assistantMessages.length - 1);
    if (assistantMessages.length > effectiveInitialCount) {
      const newCard = assistantMessages[assistantMessages.length - 1];
      const dlBtn = findDownloadButtonInCard(newCard);
      
      if (dlBtn) {
        const cardImg = newCard.querySelector('img');
        if (cardImg && isValidGeneratedImageUrl(cardImg.src) && !window._lastImgSrcs.has(cardImg.src)) {
          log(`Found clean new image src inside new message card at ${i}s: ${cardImg.src.substring(0, 60)}`);
          return cardImg.src;
        }

        // Try to get href from the button/link itself (only if it's a valid remote generated image URL)
        const href = dlBtn.getAttribute('href') || dlBtn.href;
        if (href && isValidGeneratedImageUrl(href) && !window._lastImgSrcs.has(href)) {
          log(`Found direct image download URL in new download button href at ${i}s`);
          return href;
        }
      }
    }

    if (i % 10 === 0 && i > 0) log(`Waiting for image... ${i}s`);
  }
  return null;
}

// ── Wait for new video ────────────────────────────────────────────────────────
async function waitForNewVideo(maxSec) {
  let metaRetries = 0;
  for (let i = 0; i < maxSec; i++) {
    await sleep(1000);

    // Check for new video elements
    const videos = document.querySelectorAll('video');
    for (const v of videos) {
      const src = v.src || v.querySelector('source')?.src || '';
      if (src && !window._lastVideoSrcs.has(src) && src !== 'about:blank') {
        log(`New video found at ${i}s`);
        return src;
      }
    }

    // Check for safety block or error text in latest chat messages
    const assistantMessages = Array.from(document.querySelectorAll('[role="article"], .chat-message, [class*="message" i]'));
    if (assistantMessages.length > 0) {
      const lastMsg = assistantMessages[assistantMessages.length - 1];
      if (lastMsg !== window._lastAssistantMsg) {
        const txt = (lastMsg.textContent || '').toLowerCase();
        if (
          txt.includes("snag") ||
          txt.includes("didn't render") ||
          txt.includes("did not render") ||
          txt.includes("can't generate") || 
          txt.includes("can't create") || 
          txt.includes("oops! i can't") ||
          txt.includes("cannot generate") ||
          txt.includes("wasn't able to") ||
          txt.includes("was not able to") ||
          txt.includes("unable to generate") ||
          txt.includes("against our guidelines") ||
          txt.includes("safety policies") ||
          txt.includes("safety system") ||
          txt.includes("flagged by") ||
          txt.includes("different approach") ||
          txt.includes("different take") ||
          txt.includes("stopped") ||
          txt.includes("request was stopped") ||
          txt.includes("something went wrong") ||
          txt.includes("try again") ||
          txt.includes("content filter") ||
          txt.includes("filter") ||
          txt.includes("sacred symbols") ||
          txt.includes("religious") ||
          txt.includes("flagged") ||
          txt.includes("sensitive") ||
          txt.includes("vibe without the block") ||
          txt.includes("run into") ||
          txt.includes("tweak") ||
          txt.includes("guidelines") ||
          txt.includes("policy")
        ) {
          log(`⚠️ Meta AI safety block or content filter error detected (Attempt ${metaRetries + 1}/15)!`);

          if (metaRetries >= 15) {
            log("❌ Max retries reached (15). Using a completely generic safe video prompt.");
            const superSafePrompt = "Create a smooth 5-second cinematic video animation of soft flowing abstract wave lines, vertical 9:16 format, high quality.";
            log(`✍️ Sending super safe video prompt: "${superSafePrompt}"`);
            await typeInChat(superSafePrompt);
            await sleep(500);
            await clickSend();
            const msgs = document.querySelectorAll('[role="article"], .chat-message, [class*="message" i]');
            window._lastAssistantMsg = msgs.length > 0 ? msgs[msgs.length - 1] : null;
            metaRetries = 12; // Reset retries to wait for super safe prompt
            await sleep(6000);
            continue;
          }
          metaRetries++;

          // 1. Check for suggestion chips/buttons inside the last message to click as a retry
          const suggestionBtns = Array.from(lastMsg.querySelectorAll('button, [role="button"]'));
          let clickedSuggestion = false;
          for (const btn of suggestionBtns) {
            const btnTxt = (btn.textContent || '').toLowerCase().trim();
            if (btnTxt && (btnTxt.includes("style") || btnTxt.includes("artistic") || btnTxt.includes("minimalist") || btnTxt.includes("different") || btnTxt.includes("globe") || btnTxt.includes("map") || btnTxt.includes("satellite") || btnTxt.includes("painted") || btnTxt.includes("take"))) {
              log(`👉 Clicking Meta AI suggestion chip: "${btn.textContent.trim()}" to bypass block...`);
              
              window._lastAssistantMsg = lastMsg;

              btn.addEventListener('click', (e) => e.stopPropagation(), { once: true });
              btn.click();
              clickedSuggestion = true;
              await sleep(5000);
              break;
            }
          }

          if (clickedSuggestion) {
            continue;
          }

          window._lastAssistantMsg = lastMsg;

          // 2. Progressive fallback video prompt simplification (highly aggressive religious/country filter cleaning)
          let fallbackPrompt = "";
          const dialogue = metaScene.dialogue || metaScene.animation_prompt || "educational concept";
          const safeDialogue = dialogue.replace(/India|Asia|border|map|government|politician|sacred|symbol|religion|religious|god|deity|meditat|temple|church|mosque|shrine|text|script|writing|holy|worship/gi, "peaceful landscape");

          if (metaRetries === 1 || metaRetries === 2) {
            fallbackPrompt = `Create a smooth 5-second cinematic video animation of: ${safeDialogue.substring(0, 100)}. Vertical 9:16 format, smooth camera movement.`;
          } else if (metaRetries === 3 || metaRetries === 4) {
            fallbackPrompt = `Create a smooth 5-second cinematic video animation representing: ${safeDialogue.substring(0, 50)}. Vertical 9:16 format.`;
          } else {
            fallbackPrompt = `Create a smooth 5-second cinematic video animation of abstract flowing lines of science and knowledge, vertical 9:16 format.`;
          }
          
          log(`✍️ Sending fallback prompt: "${fallbackPrompt}"`);
          await typeInChat(fallbackPrompt);
          await sleep(500);
          await clickSend();
          
          await sleep(5000);
          continue;
        }
      }
    }

    // Check download button
    const dlBtn = findDownloadButton();
    if (dlBtn && i > 8) {
      // Prioritize finding the clean video src!
      const parentCard = dlBtn.closest('[class*="chat" i], [role="article"], .chat-message') || dlBtn.parentElement?.parentElement;
      const cardVid = parentCard ? (parentCard.querySelector('video') || parentCard.querySelector('source')) : null;
      if (cardVid && cardVid.src && cardVid.src !== 'about:blank') {
        log(`Found clean video src inside message card: ${cardVid.src.substring(0, 60)}`);
        return cardVid.src;
      }
      
      // Fallback: search ALL videos on page for a new generated one
      const videos = document.querySelectorAll('video');
      for (const v of videos) {
        const src = v.src || v.querySelector('source')?.src || '';
        if (src && !window._lastVideoSrcs.has(src) && src !== 'about:blank') {
          log(`Found new video src: ${src.substring(0, 60)}`);
          return src;
        }
      }

      // Try to get href from the button/link itself
      const href = dlBtn.getAttribute('href') || dlBtn.href;
      if (href && href.startsWith('http')) {
        log("Found direct video download URL in download button href");
        return href;
      }
      
      // Wait another second to allow the video tag to load its src cleanly rather than clicking
      await sleep(1000);
    }

    if (i % 15 === 0 && i > 0) log(`Waiting for video... ${i}s`);
  }
  return null;
}

// ── Download file via click fallback ──────────────────────────────────────────
async function downloadFileViaClick(dlBtn, filename) {
  try {
    log(`Downloading file via native button click: ${filename}`);
    
    // Register the native download in the background script first
    const response = await new Promise((resolve) => {
      safeSendMessage({ type: 'DOWNLOAD_FILE', url: 'blob:click', filename: filename }, (resp) => {
        resolve(resp);
      });
    });

    if (response && response.ok && response.use_native_click) {
      // Set up the listener for download complete signal
      const downloadPromise = new Promise((resolveDownload, rejectDownload) => {
        const timeoutId = setTimeout(() => {
          chrome.runtime.onMessage.removeListener(signalListener);
          rejectDownload(new Error(`Native click download timed out after 90 seconds for ${filename}`));
        }, 90000);

        function signalListener(msg) {
          if (msg.type === "DOWNLOAD_COMPLETE_SIGNAL" && msg.filename === filename) {
            clearTimeout(timeoutId);
            chrome.runtime.onMessage.removeListener(signalListener);
            if (msg.ok) {
              resolveDownload();
            } else {
              rejectDownload(new Error(msg.error || "Native click download failed"));
            }
          }
        }

        chrome.runtime.onMessage.addListener(signalListener);
      });

      // Click the page's download button
      dlBtn.click();

      // Wait until the download finishes 100%
      await downloadPromise;
      log(`Native click download completed successfully for: ${filename}`);
    }
  } catch (e) {
    log(`Native click download error: ${e.message}. Trying standard click fallback...`);
    dlBtn.click();
    await sleep(4000);
  }
}

// ── Download file ─────────────────────────────────────────────────────────────
async function downloadFile(src, filename) {
  if (!src || (!src.startsWith('http') && !src.startsWith('blob:') && !src.startsWith('data:'))) {
    log(`Source is not a direct URL (possibly already downloaded via click): ${src}`);
    return;
  }
  try {
    log(`Downloading file via background: ${filename}`);
    const response = await new Promise((resolve) => {
      safeSendMessage({ type: 'DOWNLOAD_FILE', url: src, filename: filename }, (resp) => {
        resolve(resp);
      });
    });

    if (response && response.ok) {
      if (response.use_native_click) {
        log(`Background requested native download click for blob URL. Triggering now...`);
        
        // Wait for background signal of completion
        const downloadPromise = new Promise((resolveDownload, rejectDownload) => {
          const timeoutId = setTimeout(() => {
            chrome.runtime.onMessage.removeListener(signalListener);
            rejectDownload(new Error(`Native download timed out after 90 seconds for ${filename}`));
          }, 90000);

          function signalListener(msg) {
            if (msg.type === "DOWNLOAD_COMPLETE_SIGNAL" && msg.filename === filename) {
              clearTimeout(timeoutId);
              chrome.runtime.onMessage.removeListener(signalListener);
              if (msg.ok) {
                resolveDownload();
              } else {
                rejectDownload(new Error(msg.error || "Native download failed"));
              }
            }
          }

          chrome.runtime.onMessage.addListener(signalListener);
        });

        // Trigger the click in page context
        const a = document.createElement('a');
        a.href = src;
        a.download = filename;
        a.style.display = 'none';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);

        // Wait until download finishes 100%
        await downloadPromise;
        log(`Native download completed successfully for: ${filename}`);
      } else {
        log(`Download successful: ${filename}`);
      }
    } else {
      throw new Error(response ? response.error : 'No response from background');
    }
  } catch (e) {
    log(`Download error: ${e.message}. Falling back to standard link...`);
    // Fallback just in case (will trigger download but won't block, as last resort)
    const a = document.createElement('a');
    a.href = src;
    a.download = filename;
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    await sleep(4000); // Give it some time
  }
  await sleep(1500);
}

// ── Find download button ──────────────────────────────────────────────────────
function findDownloadButton() {
  // Search from the end to find the most recent button and ignore pre-existing ones
  const all = Array.from(document.querySelectorAll('button, [role="button"], a[download], a[href*="download"]'));
  for (let i = all.length - 1; i >= 0; i--) {
    const el = all[i];
    if (window._lastDownloadBtns && window._lastDownloadBtns.has(el)) continue;
    
    const label = (
      el.getAttribute('aria-label') || el.getAttribute('title') ||
      el.getAttribute('download') || el.textContent || ''
    ).toLowerCase();
    if (label.includes('download') || label.includes('save')) return el;
  }
  return null;
}

// ── Auto-close preview modal helper ──────────────────────────────────────────
async function closeMetaAIPreviewModal() {
  // 1. Try common close buttons
  const closeSelectors = [
    'button[aria-label*="Close" i]',
    'button[aria-label*="close" i]',
    'div[aria-label*="Close" i]',
    'div[role="button"][aria-label*="Close" i]',
    '.close-button',
    '[class*="close" i]',
    '[class*="lightbox" i] [class*="close" i]',
    '[class*="modal" i] [class*="close" i]'
  ];
  for (const sel of closeSelectors) {
    const btn = document.querySelector(sel);
    if (btn && !btn.disabled) {
      btn.click();
      log("Closed a preview modal/lightbox overlay via click");
      await sleep(800);
      return true;
    }
  }

  // 2. Fallback: Send Escape key to close any modal/lightbox
  log("Attempting Escape key fallback to close any open modal/preview");
  document.body.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', keyCode: 27, bubbles: true }));
  document.body.dispatchEvent(new KeyboardEvent('keyup', { key: 'Escape', code: 'Escape', keyCode: 27, bubbles: true }));
  await sleep(500);
  return false;
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function waitForElement(selector, timeout = 10000) {
  return new Promise(resolve => {
    const el = document.querySelector(selector);
    if (el) return resolve(el);
    const obs = new MutationObserver(() => {
      const el = document.querySelector(selector);
      if (el) { obs.disconnect(); resolve(el); }
    });
    obs.observe(document.body, { childList: true, subtree: true });
    setTimeout(() => { obs.disconnect(); resolve(null); }, timeout);
  });
}

function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

function safeSendMessage(msg, callback) {
  try {
    if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.id) {
      if (callback) {
        chrome.runtime.sendMessage(msg, callback);
      } else {
        chrome.runtime.sendMessage(msg);
      }
      return true;
    }
  } catch (e) {
    console.warn("[Meta] Extension context is invalidated. Message dropped:", msg);
  }
  if (callback) {
    try { callback({ ok: false, error: "Extension context invalidated" }); } catch (err) {}
  }
  return false;
}

function log(msg) {
  console.log(`[MR AI Meta] ${msg}`);
  safeSendMessage({ type: 'LOG', message: `[Meta] ${msg}` });
}

// ── Single Asset Generator (Library Edit & Generate) ─────────────────────────
async function generateSingleAsset(prompt, mediaType, filename) {
  snapshotCurrentImages();
  snapshotCurrentVideos();
  snapshotCurrentDownloadButtons();
  snapshotLastAssistantMessage();

  // Close any open preview modal first
  await closeMetaAIPreviewModal();

  if (mediaType === 'image') {
    const initialCardsCount = getAssistantMessageCards().length;
    log(`Initial assistant message cards count before single image generation: ${initialCardsCount}`);

    const fullPrompt = `Generate a high quality photorealistic image: ${prompt}. Vertical 9:16 portrait format, cinematic lighting, 4K quality, no text.`;
    await typeInChat(fullPrompt);
    await sleep(500);
    await clickSend();
    const imgSrc = await waitForNewImage(90, initialCardsCount);
    if (!imgSrc) throw new Error('Single image generation timeout');
    await downloadFile(imgSrc, filename);
    await sleep(1000);
  } else {
    // video
    const fullPrompt = `Create a smooth 5-second cinematic video animation: ${prompt}. Vertical 9:16 format, smooth camera movement, high quality.`;
    await typeInChat(fullPrompt);
    await sleep(500);
    await clickSend();
    const vidSrc = await waitForNewVideo(120);
    if (!vidSrc) {
      const dlBtn = findDownloadButton();
      if (dlBtn) {
        await downloadFileViaClick(dlBtn, filename);
        return;
      }
      throw new Error('Single video generation timeout');
    }
    await downloadFile(vidSrc, filename);
    await sleep(1000);
  }
}

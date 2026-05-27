// ── Meta AI Content Script ─────────────────────────────────────────────────────
// Generates BOTH image and video from Meta AI for each scene.
// Flow per scene: type image prompt → generate → download image → 4s wait →
//                 type video prompt → generate → download video → 4s wait → next scene

let metaSceneIdx = 0;
let metaJobId = null;
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
    metaScene = {
      image_prompt: msg.imagePrompt || msg.animationPrompt || '',
      animation_prompt: msg.animationPrompt || '',
      dialogue: msg.dialogue || ''
    };
    log(`Scene ${metaSceneIdx + 1}: starting`);
    processScene();
    sendResponse({ ok: true });
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

    // Notify background: image downloaded
    chrome.runtime.sendMessage({ type: 'IMAGE_DOWNLOADED', filename: imgFile });

    // 4 second wait
    log('Waiting 4s before video...');
    await sleep(4000);

    // Step 2: Generate VIDEO from Meta AI
    log(`Scene ${metaSceneIdx + 1}: generating video...`);
    const vidFile = await generateVideo();
    log(`Scene ${metaSceneIdx + 1}: video done → ${vidFile}`);

    // Notify background: video downloaded
    chrome.runtime.sendMessage({ type: 'VIDEO_DOWNLOADED', filename: vidFile });

    // 4 second wait before next scene
    log('Waiting 4s before next scene...');
    await sleep(4000);

  } catch (e) {
    log(`Scene ${metaSceneIdx + 1} ERROR: ${e.message}`);
    // Still notify so pipeline doesn't hang
    chrome.runtime.sendMessage({ type: 'IMAGE_DOWNLOADED', filename: `meta-img-${metaSceneIdx + 1}-${metaJobId || 'nojob'}-error.jpg` });
    chrome.runtime.sendMessage({ type: 'VIDEO_DOWNLOADED', filename: `meta-vid-${metaSceneIdx + 1}-${metaJobId || 'nojob'}-error.mp4` });
  }
  metaIsProcessing = false;
}

// ── Generate Image ────────────────────────────────────────────────────────────
async function generateImage() {
  // Snapshot current images before generating
  snapshotCurrentImages();
  snapshotCurrentDownloadButtons();

  const prompt = metaScene.image_prompt || metaScene.animation_prompt;
  const fullPrompt = `Generate a high quality photorealistic image: ${prompt}. Vertical 9:16 portrait format, cinematic lighting, 4K quality, no text.`;

  await typeInChat(fullPrompt);
  await sleep(500);
  await clickSend();

  // Wait for new image to appear
  const imgSrc = await waitForNewImage(90);
  if (!imgSrc) throw new Error('Image generation timeout');

  // Download it
  const filename = `meta-img-${metaSceneIdx + 1}-${metaJobId || 'nojob'}-${Date.now()}.jpg`;
  await downloadFile(imgSrc, filename);
  await sleep(1000);
  return filename;
}

// ── Generate Video ────────────────────────────────────────────────────────────
async function generateVideo() {
  // Snapshot current videos
  snapshotCurrentVideos();
  snapshotCurrentDownloadButtons();

  const prompt = metaScene.animation_prompt || metaScene.image_prompt;
  const fullPrompt = `Create a smooth 5-second cinematic video animation: ${prompt}. Vertical 9:16 format, smooth camera movement, high quality.`;

  await typeInChat(fullPrompt);
  await sleep(500);
  await clickSend();

  // Wait for new video to appear
  const vidSrc = await waitForNewVideo(120);
  if (!vidSrc) {
    // Fallback: try download button
    const dlBtn = findDownloadButton();
    if (dlBtn) {
      dlBtn.click();
      await sleep(2000);
      return `meta-vid-${metaSceneIdx + 1}-${metaJobId || 'nojob'}-${Date.now()}.mp4`;
    }
    throw new Error('Video generation timeout');
  }

  const filename = `meta-vid-${metaSceneIdx + 1}-${metaJobId || 'nojob'}-${Date.now()}.mp4`;
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

// ── Wait for new image ────────────────────────────────────────────────────────
// ── Wait for new image ────────────────────────────────────────────────────────
async function waitForNewImage(maxSec) {
  let metaRetries = 0;
  for (let i = 0; i < maxSec; i++) {
    await sleep(1000);

    // Check for new large images not in snapshot
    const imgs = document.querySelectorAll('img');
    for (const img of imgs) {
      if (!img.src || window._lastImgSrcs.has(img.src)) continue;
      if (img.naturalWidth >= 200 && img.naturalHeight >= 200) {
        // Make sure it's a generated image (not icon/avatar)
        if (!img.src.includes('avatar') && !img.src.includes('icon') && !img.src.includes('logo')) {
          log(`New image found at ${i}s: ${img.src.substring(0, 60)}`);
          return img.src;
        }
      }
    }

    // Check for safety block or error text in latest chat messages
    const assistantMessages = Array.from(document.querySelectorAll('[role="article"], .chat-message, [class*="message" i]'));
    if (assistantMessages.length > 0) {
      const lastMsg = assistantMessages[assistantMessages.length - 1];
      const txt = (lastMsg.textContent || '').toLowerCase();
      if (
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

    // Also check for download button (means generation complete)
    const dlBtn = findDownloadButton();
    if (dlBtn && i > 5) {
      // Try to get the image src from nearby img
      const nearImg = dlBtn.closest('[class]')?.querySelector('img') ||
                      dlBtn.parentElement?.querySelector('img');
      if (nearImg && nearImg.src) return nearImg.src;

      // Try to get href from the button/link itself
      const href = dlBtn.getAttribute('href') || dlBtn.href;
      if (href && href.startsWith('http')) {
        log("Found direct image download URL in download button href");
        return href;
      }

      // Add propagation stopper to prevent opening the preview lightbox modal
      dlBtn.addEventListener('click', (e) => {
        e.stopPropagation();
      }, { once: true });

      // Click download directly
      dlBtn.click();
      await sleep(1500);
      return `meta-img-${metaSceneIdx + 1}-${metaJobId || 'nojob'}-dl-${Date.now()}.jpg`;
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
      const txt = (lastMsg.textContent || '').toLowerCase();
      if (
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

    // Check download button
    const dlBtn = findDownloadButton();
    if (dlBtn && i > 8) {
      // Try to get href from the button/link itself
      const href = dlBtn.getAttribute('href') || dlBtn.href;
      if (href && href.startsWith('http')) {
        log("Found direct video download URL in download button href");
        return href;
      }

      // Add propagation stopper to prevent opening the preview lightbox modal
      dlBtn.addEventListener('click', (e) => {
        e.stopPropagation();
      }, { once: true });

      dlBtn.click();
      log('Clicked download button for video');
      await sleep(2000);
      return `meta-vid-${metaSceneIdx + 1}-${metaJobId || 'nojob'}-dl-${Date.now()}.mp4`;
    }

    if (i % 15 === 0 && i > 0) log(`Waiting for video... ${i}s`);
  }
  return null;
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
      chrome.runtime.sendMessage({ type: 'DOWNLOAD_FILE', url: src, filename: filename }, (resp) => {
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

function log(msg) {
  console.log(`[MR AI Meta] ${msg}`);
  chrome.runtime.sendMessage({ type: 'LOG', message: `[Meta] ${msg}` }).catch(() => {});
}

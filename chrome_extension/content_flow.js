// ── Google Flow Image FX Content Script ───────────────────────────────────────
// Runs on: https://labs.google/fx/tools/image-fx

let flowJobId = null;
let flowPrompts = [];
let flowSceneCount = 0;
let flowCurrentIdx = 0;
let flowDownloadCount = 0;
let flowObserver = null;

// Listen for messages from background
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "INJECT_IMAGE_PROMPTS") {
    flowJobId = msg.jobId;
    flowPrompts = msg.prompts.split("\n").filter(p => p.trim());
    flowSceneCount = msg.sceneCount;
    flowCurrentIdx = 0;
    flowDownloadCount = 0;
    log(`Starting image generation: ${flowPrompts.length} prompts`);
    startFlowGeneration();
    sendResponse({ ok: true });
  }
  return true;
});

async function startFlowGeneration() {
  await sleep(2000);
  // Find the prompt textarea
  const textarea = await waitForElement('textarea, [contenteditable="true"], input[placeholder*="prompt" i], input[placeholder*="describe" i]', 15000);
  if (!textarea) {
    log("ERROR: Could not find prompt input on Google Flow");
    return;
  }

  // Set aspect ratio to 9:16 first
  await setAspectRatio916();
  await sleep(1000);

  // Start generating one by one
  await generateNextImage(textarea);
}

async function setAspectRatio916() {
  // Look for aspect ratio selector
  const ratioButtons = document.querySelectorAll('button, [role="button"], [role="option"]');
  for (const btn of ratioButtons) {
    const text = btn.textContent || btn.getAttribute('aria-label') || '';
    if (text.includes('9:16') || text.includes('Portrait') || text.includes('portrait')) {
      btn.click();
      log("Set aspect ratio to 9:16");
      await sleep(500);
      return;
    }
  }

  // Try dropdown approach
  const selects = document.querySelectorAll('select');
  for (const sel of selects) {
    for (const opt of sel.options) {
      if (opt.text.includes('9:16') || opt.text.includes('Portrait')) {
        sel.value = opt.value;
        sel.dispatchEvent(new Event('change', { bubbles: true }));
        log("Set aspect ratio via select");
        await sleep(500);
        return;
      }
    }
  }
  log("Could not find 9:16 ratio selector, continuing anyway");
}

async function generateNextImage(textarea) {
  if (flowCurrentIdx >= flowPrompts.length) {
    log("All images generated!");
    return;
  }

  const prompt = flowPrompts[flowCurrentIdx];
  log(`Generating image ${flowCurrentIdx + 1}/${flowPrompts.length}: ${prompt.substring(0, 50)}...`);

  // Clear and set prompt
  textarea.focus();
  textarea.value = '';
  textarea.dispatchEvent(new Event('input', { bubbles: true }));
  await sleep(300);

  // Type the prompt
  setNativeValue(textarea, prompt);
  textarea.dispatchEvent(new Event('input', { bubbles: true }));
  textarea.dispatchEvent(new Event('change', { bubbles: true }));
  await sleep(500);

  // Click Generate button
  const generateBtn = findGenerateButton();
  if (!generateBtn) {
    log("ERROR: Generate button not found");
    return;
  }
  generateBtn.click();
  log(`Clicked generate for scene ${flowCurrentIdx + 1}`);

  // Wait for image to appear and download it
  await waitForImageAndDownload();
}

async function waitForImageAndDownload() {
  log("Waiting for image generation...");
  let attempts = 0;
  const maxAttempts = 60; // 60 seconds max

  while (attempts < maxAttempts) {
    await sleep(1000);
    attempts++;

    // Check if generation is complete (look for download button or generated image)
    const downloadBtn = findDownloadButton();
    const generatedImg = findGeneratedImage();

    if (downloadBtn || generatedImg) {
      log("Image generated! Downloading...");
      await sleep(500);

      if (downloadBtn) {
        downloadBtn.click();
      } else if (generatedImg) {
        // Download via URL
        await downloadImageFromSrc(generatedImg.src);
      }

      await sleep(2000); // Wait for download to start

      // Notify background
      const filename = `flow-image-${flowCurrentIdx + 1}-${flowJobId || 'nojob'}-${Date.now()}.jpg`;
      chrome.runtime.sendMessage({
        type: "IMAGE_DOWNLOADED",
        filename: filename
      });

      flowCurrentIdx++;
      flowDownloadCount++;

      // Wait a bit then generate next
      await sleep(1500);
      const textarea = document.querySelector('textarea, [contenteditable="true"]');
      if (textarea) {
        await generateNextImage(textarea);
      }
      return;
    }

    // Check for loading indicator
    const isLoading = document.querySelector('[aria-label*="loading" i], .loading, [class*="loading"], [class*="spinner"]');
    if (isLoading && attempts % 5 === 0) {
      log(`Still generating... (${attempts}s)`);
    }
  }

  log("Timeout waiting for image, skipping to next");
  flowCurrentIdx++;
  const textarea = document.querySelector('textarea, [contenteditable="true"]');
  if (textarea) await generateNextImage(textarea);
}

function findGenerateButton() {
  const buttons = document.querySelectorAll('button, [role="button"]');
  for (const btn of buttons) {
    const text = (btn.textContent || btn.getAttribute('aria-label') || '').toLowerCase();
    if (text.includes('generate') || text.includes('create') || text.includes('imagine')) {
      if (!btn.disabled) return btn;
    }
  }
  return null;
}

function findDownloadButton() {
  const buttons = document.querySelectorAll('button, [role="button"], a');
  for (const btn of buttons) {
    const text = (btn.textContent || btn.getAttribute('aria-label') || btn.getAttribute('title') || '').toLowerCase();
    if (text.includes('download') || text.includes('save')) {
      return btn;
    }
  }
  return null;
}

function findGeneratedImage() {
  // Look for newly generated images (usually in a result container)
  const imgs = document.querySelectorAll('img[src*="blob:"], img[src*="data:"], img[src*="generated"], img[src*="output"]');
  if (imgs.length > 0) return imgs[imgs.length - 1];

  // Fallback: look for large images that appeared recently
  const allImgs = document.querySelectorAll('img');
  for (const img of allImgs) {
    if (img.naturalWidth >= 512 && img.naturalHeight >= 512 && img.src && !img.src.includes('icon') && !img.src.includes('logo')) {
      return img;
    }
  }
  return null;
}

async function downloadImageFromSrc(src) {
  const a = document.createElement('a');
  a.href = src;
  a.download = `flow-image-${flowCurrentIdx + 1}-${flowJobId || 'nojob'}-${Date.now()}.jpg`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function setNativeValue(element, value) {
  const nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value') ||
    Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
  if (nativeInputValueSetter) {
    nativeInputValueSetter.set.call(element, value);
  } else {
    element.value = value;
  }
}

function waitForElement(selector, timeout = 10000) {
  return new Promise(resolve => {
    const el = document.querySelector(selector);
    if (el) return resolve(el);

    const observer = new MutationObserver(() => {
      const el = document.querySelector(selector);
      if (el) {
        observer.disconnect();
        resolve(el);
      }
    });
    observer.observe(document.body, { childList: true, subtree: true });
    setTimeout(() => { observer.disconnect(); resolve(null); }, timeout);
  });
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function log(msg) {
  console.log(`[MR AI Flow] ${msg}`);
  chrome.runtime.sendMessage({ type: "LOG", message: `[Flow] ${msg}` }).catch(() => {});
}

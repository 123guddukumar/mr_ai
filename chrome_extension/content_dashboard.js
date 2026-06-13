// ── MR AI Dashboard Link Content Script ─────────────────────────────────────────
console.log("[MR AI Content Dashboard] Loaded on dashboard page:", window.location.href);

function safeSendMessage(msg) {
  try {
    if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.sendMessage) {
      chrome.runtime.sendMessage(msg);
      return true;
    }
  } catch (e) {
    console.warn("[MR AI Content Dashboard] Extension context is invalidated. Please reload the dashboard page.");
    window.postMessage({ type: "MR_AI_EXTENSION_INVALIDATED" }, "*");
  }
  return false;
}

// Listen to messages from the dashboard page window object
window.addEventListener("message", (event) => {
  // Only accept messages from ourselves
  if (event.source !== window) return;

  if (event.data && event.data.type === "MR_AI_START_JOB") {
    console.log("[MR AI Content Dashboard] Received MR_AI_START_JOB from dashboard:", event.data);
    const sent = safeSendMessage({
      type: "START_JOB_FROM_DASHBOARD",
      jobId: event.data.jobId,
      token: event.data.token,
      backendUrl: event.data.backendUrl
    });
    if (!sent) {
      console.warn("[MR AI Content Dashboard] Extension context is invalidated or disabled. Please reload the dashboard page.");
      window.postMessage({ type: "MR_AI_EXTENSION_INVALIDATED" }, "*");
    }
  }

  if (event.data && event.data.type === "MR_AI_REGEN_SCENE") {
    console.log("[MR AI Content Dashboard] Received MR_AI_REGEN_SCENE from dashboard:", event.data);
    const sent = safeSendMessage({
      type: "REGEN_SCENE_FROM_DASHBOARD",
      jobId: event.data.jobId,
      sceneIdx: event.data.sceneIdx
    });
    if (!sent) {
      console.warn("[MR AI Content Dashboard] Extension context is invalidated or disabled. Please reload the dashboard page.");
      window.postMessage({ type: "MR_AI_EXTENSION_INVALIDATED" }, "*");
    }
  }

  // ── Single Library Asset Generation Request ──────────────────────────────────
  if (event.data && event.data.type === "MR_AI_GENERATE_SINGLE_ASSET") {
    console.log("[MR AI Content Dashboard] Received MR_AI_GENERATE_SINGLE_ASSET:", event.data);
    const sent = safeSendMessage({
      type: "GENERATE_SINGLE_ASSET",
      prompt: event.data.prompt,
      mediaType: event.data.mediaType,   // "image" | "video"
      assetId: event.data.assetId,       // library item id to replace
      token: event.data.token,
      backendUrl: event.data.backendUrl
    });
    if (!sent) {
      window.postMessage({ type: "MR_AI_EXTENSION_INVALIDATED" }, "*");
    }
  }
  // ── Single Asset Replaced Confirmation ───────────────────────────────────────
  if (event.data && event.data.type === "MR_AI_SINGLE_ASSET_REPLACED_CONFIRMED") {
    console.log("[MR AI Content Dashboard] Received MR_AI_SINGLE_ASSET_REPLACED_CONFIRMED from page.");
    safeSendMessage({
      type: "SINGLE_ASSET_REPLACED_CONFIRMED"
    });
  }
});

// ── Relay background → page messages ─────────────────────────────────────────
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "FETCH_LOCAL_BLOB") {
    fetch(msg.url)
      .then(res => res.blob())
      .then(blob => {
        const reader = new FileReader();
        reader.onloadend = () => {
          sendResponse({ ok: true, base64: reader.result });
        };
        reader.readAsDataURL(blob);
      })
      .catch(err => {
        sendResponse({ ok: false, error: err.message });
      });
    return true; // Keep channel open for async response
  }
  if (msg.type === "MR_AI_SINGLE_ASSET_DOWNLOADED") {
    // Forward to the page's window so the dashboard JS can pick it up
    window.postMessage(msg, "*");
  }
});

// ── MR AI Reel Generator - Background Service Worker ──────────────────────────

let state = {
  jobId: null,
  scenes: [],
  imagesDone: [],
  videosDone: [],
  phase: "idle",
  currentSceneIdx: 0,
  token: null,
  backendUrl: "https://test.3rdai.co",
  metaTabId: null,
};

// Global registry for tracking native (blob) downloads triggered via click in content scripts
let pendingNativeDownloads = {};

// ── Persist state to storage (survives service worker restarts) ───────────────
async function saveState() {
  await chrome.storage.local.set({ sw_state: state });
}

async function loadState() {
  const cfg = await chrome.storage.local.get(["sw_state", "token", "backendUrl"]);
  if (cfg.sw_state) {
    state = cfg.sw_state;
    // Reset to idle if SW starts/restarts in any non-idle state (e.g. done, error, or mid-job)
    // to ensure we are always ready to poll and process new jobs!
    if (state.phase !== "idle") {
      log(`SW loaded in phase: ${state.phase}, resetting to idle`);
      notifyPopup(`🔄 Resetting extension phase from '${state.phase}' to 'idle'`, "info");
      state.phase = "idle";
      state.jobId = null;
      await saveState();
    }
  }
  if (cfg.token) state.token = cfg.token;
  if (cfg.backendUrl) state.backendUrl = cfg.backendUrl;

  // Notify status and start the fast polling loop automatically
  notifyPopup("🔌 Active and polling for dashboard...", "info");
  startFastPoll();
}

// ── Fast Polling Loop ────────────────────────────────────────────────────────
let fastPollTimeout = null;

function startFastPoll() {
  if (fastPollTimeout) {
    clearTimeout(fastPollTimeout);
  }
  
  // Inform the popup that the extension is active and polling
  notifyPopup("🔌 Active and polling for jobs...", "info");
  
  async function tick() {
    if (state.phase === "idle" && state.token) {
      try {
        const res = await fetch(`${state.backendUrl}/api/extension/pending-job`, {
          headers: { "X-App-Token": state.token }
        });
        if (res.ok) {
          const data = await res.json();
          if (data.job_id && data.status === "waiting_extension") {
            log(`New job detected via fast-poll: ${data.job_id}`);
            notifyPopup(`🔌 New job detected: ${data.job_id}! Starting...`, "success");
            await startJob(data.job_id);
            return; // startJob will change the phase and handle everything
          }
        }
      } catch (e) {
        log(`Fast-poll error: ${e.message}`);
      }
    }
    
    // Continue polling if still idle
    if (state.phase === "idle") {
      fastPollTimeout = setTimeout(tick, 3000);
    }
  }
  
  tick();
}

// ── Keep service worker alive with a keepalive alarm ─────────────────────────
function setupAlarms() {
  chrome.alarms.create("auto_poll", { periodInMinutes: 0.05 });
  chrome.alarms.create("keepalive", { periodInMinutes: 0.4 });
}

chrome.runtime.onInstalled.addListener(async () => {
  setupAlarms();
  await loadState();
  log("Extension installed. Auto-poller started.");
});

chrome.runtime.onStartup.addListener(async () => {
  setupAlarms();
  await loadState();
});

// Load state immediately when SW starts
loadState();

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name === "keepalive") return; // just keeps SW alive

  if (alarm.name !== "auto_poll") return;

  // Reload token from storage each time (in case SW restarted)
  const cfg = await chrome.storage.local.get(["token", "backendUrl", "sw_state"]);
  if (!cfg.token) return;

  state.token = cfg.token;
  state.backendUrl = cfg.backendUrl || "https://test.3rdai.co";

  // Sync phase from storage
  if (cfg.sw_state && cfg.sw_state.phase) {
    state.phase = cfg.sw_state.phase;
  }

  // Ensure we start polling if SW restarted
  if (state.phase === "idle") {
    startFastPoll();
  }
});

// ── Messages ──────────────────────────────────────────────────────────────────
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "SAVE_CONFIG") {
    chrome.storage.local.set({ token: msg.token, backendUrl: msg.backendUrl });
    state.token = msg.token;
    state.backendUrl = msg.backendUrl;
    state.phase = "idle"; // Explicitly ensure state is idle
    state.jobId = null;
    saveState().then(() => {
      notifyPopup("🔌 Config saved. Waiting for dashboard...", "success");
      sendResponse({ ok: true });
    });
    return true; // Keep message channel open
  }
  if (msg.type === "DOWNLOAD_FILE") {
    if (msg.url && msg.url.startsWith("blob:")) {
      log(`Detected blob URL download request for: ${msg.filename}. Delegating to native page context click.`);
      if (sender.tab && sender.tab.id) {
        pendingNativeDownloads[msg.filename] = {
          tabId: sender.tab.id,
          downloadId: null
        };
        sendResponse({ ok: true, use_native_click: true });
      } else {
        log(`Error: sender.tab.id not found for blob download.`);
        sendResponse({ ok: false, error: "Sender tab ID not found" });
      }
      return false; // Handled synchronously
    }

    // Standard download flow for non-blob URLs
    chrome.downloads.download({
      url: msg.url,
      filename: msg.filename,
      conflictAction: "overwrite",
      saveAs: false
    }, (downloadId) => {
      if (chrome.runtime.lastError) {
        log(`Download failed for ${msg.filename}: ${chrome.runtime.lastError.message}`);
        sendResponse({ ok: false, error: chrome.runtime.lastError.message });
      } else {
        log(`Download started: ${msg.filename} (ID: ${downloadId}), waiting for completion...`);
        waitForDownloadComplete(downloadId).then((status) => {
          if (status.ok) {
            log(`Download complete: ${msg.filename}`);
            sendResponse({ ok: true, downloadId });
          } else {
            log(`Download failed or interrupted: ${msg.filename} - ${status.error}`);
            sendResponse({ ok: false, error: status.error });
          }
        });
      }
    });
    return true; // Keep channel open for async response
  }
  if (msg.type === "START_JOB_FROM_DASHBOARD") {
    log(`Job triggered from dashboard: ${msg.jobId}`);
    if (msg.token) {
      state.token = msg.token;
      chrome.storage.local.set({ token: msg.token });
    }
    if (msg.backendUrl) {
      state.backendUrl = msg.backendUrl;
      chrome.storage.local.set({ backendUrl: msg.backendUrl });
    }
    startJob(msg.jobId);
    sendResponse({ ok: true });
    return true;
  }
  if (msg.type === "GET_STATE") {
    sendResponse({ state });
  }
  if (msg.type === "IMAGE_DOWNLOADED") {
    handleImageDownloaded(msg.filename);
    sendResponse({ ok: true });
  }
  if (msg.type === "VIDEO_DOWNLOADED") {
    handleVideoDownloaded(msg.filename);
    sendResponse({ ok: true });
  }
  if (msg.type === "RESET") {
    resetState();
    sendResponse({ ok: true });
  }
  return true;
});

// ── Start Job ─────────────────────────────────────────────────────────────────
async function startJob(jobId) {
  state.jobId = jobId;
  state.phase = "fetching";
  state.imagesDone = [];
  state.videosDone = [];
  state.currentSceneIdx = 0;
  await saveState();

  notifyPopup(`🚀 Job detected! Fetching scenes...`);

  try {
    // Pickup
    const pickupRes = await fetch(`${state.backendUrl}/api/extension/job/${jobId}/pickup`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-App-Token": state.token }
    });
    if (!pickupRes.ok) {
      const err = await pickupRes.text();
      throw new Error(`Pickup failed: ${err}`);
    }

    // Fetch scenes
    const res = await fetch(`${state.backendUrl}/api/extension/job/${jobId}`, {
      headers: { "X-App-Token": state.token }
    });
    const data = await res.json();
    if (!data.success) throw new Error(data.detail || "Failed to fetch job");

    state.scenes = data.scenes;
    state.phase = "generating_images";
    await saveState();
    notifyPopup(`📋 ${state.scenes.length} scenes loaded. Opening Meta AI...`);

    await openMetaAI();
  } catch (e) {
    notifyPopup(`❌ ${e.message}`, "error");
    log(`startJob error: ${e.message}`);
    state.phase = "idle"; // reset so next poll can retry
    await saveState();
    await reportError(e.message);
    startFastPoll();
  }
}

// ── Open Meta AI ──────────────────────────────────────────────────────────────
async function openMetaAI() {
  const tab = await chrome.tabs.create({ url: "https://www.meta.ai", active: true });
  state.metaTabId = tab.id;
  await saveState();

  notifyPopup(`🤖 Meta AI opened. Waiting for page load...`);
  await waitForTabLoad(tab.id, 25000);
  await sleep(4000); // extra buffer for React/JS to init

  await sendNextScene(tab.id);
}

function waitForTabLoad(tabId, timeout = 25000) {
  return new Promise(resolve => {
    const start = Date.now();
    function check() {
      chrome.tabs.get(tabId, (tab) => {
        if (chrome.runtime.lastError || !tab) return resolve();
        if (tab.status === "complete") return resolve();
        if (Date.now() - start > timeout) return resolve();
        setTimeout(check, 500);
      });
    }
    check();
  });
}

// ── Send scene to Meta AI content script ─────────────────────────────────────
async function sendNextScene(tabId) {
  if (state.currentSceneIdx >= state.scenes.length) {
    await allDone();
    return;
  }

  const scene = state.scenes[state.currentSceneIdx];
  notifyPopup(`🎬 Scene ${state.currentSceneIdx + 1}/${state.scenes.length}: generating...`);

  const msg = {
    type: "GENERATE_VIDEO",
    sceneIdx: state.currentSceneIdx,
    imagePrompt: scene.image_prompt || "",
    animationPrompt: scene.animation_prompt || "",
    dialogue: scene.dialogue || "",
    jobId: state.jobId
  };

  for (let attempt = 0; attempt < 8; attempt++) {
    try {
      const resp = await chrome.tabs.sendMessage(tabId, msg);
      if (resp && resp.ok) {
        log(`Scene ${state.currentSceneIdx + 1} sent OK`);
        return;
      }
      if (resp && resp.reason === "busy") {
        notifyPopup(`⏳ Content script busy, waiting...`);
        await sleep(5000);
        continue;
      }
    } catch (e) { /* content script not ready yet */ }
    notifyPopup(`⏳ Waiting for Meta AI... (${attempt + 1}/8)`);
    await sleep(3000);
  }

  notifyPopup(`❌ Could not reach Meta AI tab`, "error");
  state.phase = "idle";
  await saveState();
  await reportError("Content script unreachable after 8 attempts");
  startFastPoll();
}

// ── Image downloaded ──────────────────────────────────────────────────────────
async function handleImageDownloaded(filename) {
  if (!state.imagesDone.includes(filename)) state.imagesDone.push(filename);
  notifyPopup(`🖼️ Image ${state.imagesDone.length}/${state.scenes.length} done`);
  await saveState();

  await fetch(`${state.backendUrl}/api/extension/job/${state.jobId}/image-done`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-App-Token": state.token },
    body: JSON.stringify({ filename, index: state.imagesDone.length - 1 })
  }).catch(() => {});
}

// ── Video downloaded ──────────────────────────────────────────────────────────
async function handleVideoDownloaded(filename) {
  if (!state.videosDone.includes(filename)) state.videosDone.push(filename);
  notifyPopup(`✅ Video ${state.videosDone.length}/${state.scenes.length} done`);

  await fetch(`${state.backendUrl}/api/extension/job/${state.jobId}/video-done`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-App-Token": state.token },
    body: JSON.stringify({ filename, index: state.videosDone.length - 1 })
  }).catch(() => {});

  state.currentSceneIdx++;
  await saveState();

  if (state.currentSceneIdx >= state.scenes.length) {
    await allDone();
    return;
  }

  const tabs = await chrome.tabs.query({ url: ["https://www.meta.ai/*", "https://meta.ai/*"] });
  const tabId = tabs.length > 0 ? tabs[0].id : state.metaTabId;
  if (tabId) await sendNextScene(tabId);
}

// ── All done → assemble ───────────────────────────────────────────────────────
async function allDone() {
  state.phase = "assembling";
  await saveState();
  notifyPopup(`⚙️ All scenes done! Assembling reel...`);

  if (state.metaTabId) {
    chrome.tabs.remove(state.metaTabId).catch(() => {});
    state.metaTabId = null;
  }

  try {
    const res = await fetch(`${state.backendUrl}/api/extension/job/${state.jobId}/assemble`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-App-Token": state.token },
      body: JSON.stringify({ videos: state.videosDone, images: state.imagesDone })
    });
    const data = await res.json();
    if (data.success) {
      state.phase = "done";
      await saveState();
      notifyPopup(`🎉 Reel ready! ${data.video_url}`, "success");
      chrome.notifications.create({
        type: "basic", iconUrl: "icon48.png",
        title: "MR AI Reel Ready! 🎬",
        message: "Your educational reel has been generated!"
      });
      setTimeout(resetState, 5000);
    } else {
      throw new Error(data.detail || "Assembly failed");
    }
  } catch (e) {
    notifyPopup(`❌ Assembly error: ${e.message}`, "error");
    state.phase = "idle";
    await saveState();
    await reportError(e.message);
    startFastPoll();
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────
async function resetState() {
  state = {
    jobId: null, scenes: [], imagesDone: [], videosDone: [],
    phase: "idle", currentSceneIdx: 0,
    token: state.token, backendUrl: state.backendUrl,
    metaTabId: null
  };
  await saveState();
  notifyPopup("🔌 Active and waiting for dashboard...", "info");
}

async function reportError(msg) {
  if (!state.jobId || !state.token) return;
  await fetch(`${state.backendUrl}/api/extension/job/${state.jobId}/error`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-App-Token": state.token },
    body: JSON.stringify({ error: msg })
  }).catch(() => {});
}

function notifyPopup(message, type = "info") {
  chrome.runtime.sendMessage({ type: "LOG", message, logType: type }).catch(() => {});
}

function log(msg) { console.log(`[MR AI BG] ${msg}`); }
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function waitForDownloadComplete(downloadId) {
  return new Promise((resolve) => {
    function listener(delta) {
      if (delta.id === downloadId) {
        if (delta.state && delta.state.current === "complete") {
          chrome.downloads.onChanged.removeListener(listener);
          resolve({ ok: true });
        } else if (delta.state && delta.state.current === "interrupted") {
          chrome.downloads.onChanged.removeListener(listener);
          resolve({ ok: false, error: "Download interrupted" });
        }
      }
    }
    chrome.downloads.onChanged.addListener(listener);
    
    // Safety check in case it completed immediately
    chrome.downloads.search({ id: downloadId }, (results) => {
      if (results && results[0]) {
        const item = results[0];
        if (item.state === "complete") {
          chrome.downloads.onChanged.removeListener(listener);
          resolve({ ok: true });
        } else if (item.state === "interrupted") {
          chrome.downloads.onChanged.removeListener(listener);
          resolve({ ok: false, error: item.error || "Download interrupted" });
        }
      }
    });
  });
}

// ── Native (blob) downloads tracking & signal listeners ────────────────────────
chrome.downloads.onCreated.addListener((item) => {
  checkAndMatchDownload(item);
});

chrome.downloads.onChanged.addListener((delta) => {
  chrome.downloads.search({ id: delta.id }, (results) => {
    if (results && results[0]) {
      const item = results[0];
      checkAndMatchDownload(item);
    }
  });
});

function checkAndMatchDownload(item) {
  if (!item.filename) return;
  
  const baseFilename = getBaseFilename(item.filename);
  for (const filename of Object.keys(pendingNativeDownloads)) {
    const cleanFn = filename.replace(".mp4", "").replace(".jpg", "");
    if (baseFilename === filename || item.filename.endsWith(filename) || baseFilename.includes(cleanFn)) {
      const pending = pendingNativeDownloads[filename];
      pending.downloadId = item.id;
      
      if (item.state === "complete") {
        log(`Native download completed: ${filename}`);
        chrome.tabs.sendMessage(pending.tabId, {
          type: "DOWNLOAD_COMPLETE_SIGNAL",
          filename: filename,
          ok: true
        }).catch((err) => {
          log(`Failed to send DOWNLOAD_COMPLETE_SIGNAL to tab ${pending.tabId}: ${err.message}`);
        });
        delete pendingNativeDownloads[filename];
      } else if (item.state === "interrupted") {
        log(`Native download interrupted: ${filename} - ${item.error || "unknown"}`);
        chrome.tabs.sendMessage(pending.tabId, {
          type: "DOWNLOAD_COMPLETE_SIGNAL",
          filename: filename,
          ok: false,
          error: item.error || "Download interrupted"
        }).catch((err) => {
          log(`Failed to send DOWNLOAD_COMPLETE_SIGNAL to tab ${pending.tabId}: ${err.message}`);
        });
        delete pendingNativeDownloads[filename];
      }
      break;
    }
  }
}

function getBaseFilename(path) {
  return path.replace(/^.*[\\\/]/, '');
}

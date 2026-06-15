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
  singleAsset: null,   // { assetId, mediaType, prompt, tabId, dashboardTabId }
  bgmTabId: null,
  bgmUrl: null,
  bgmPrompt: null
};

// Global registry for tracking native (blob) downloads triggered via click in content scripts
let pendingNativeDownloads = {};

// ── Persist state to storage (survives service worker restarts) ───────────────
async function saveState() {
  await chrome.storage.local.set({ sw_state: state });
}

async function loadState(retryCount = 0) {
  try {
    const cfg = await chrome.storage.local.get(["sw_state", "token", "backendUrl"]);
    if (cfg.sw_state) {
      state = cfg.sw_state;
    }
    if (cfg.token) state.token = cfg.token;
    if (cfg.backendUrl) state.backendUrl = cfg.backendUrl;

    // Notify status and start the fast polling loop if idle
    notifyPopup("🔌 Active and polling for dashboard...", "info");
    if (state.phase === "idle") {
      startFastPoll();
    }
  } catch (e) {
    log(`loadState error: ${e.message}`);
    // If it's a startup or SW-invalidated error, retry after 500ms
    if (retryCount < 5) {
      log(`Temporary context error. Retrying loadState in 500ms (attempt ${retryCount + 1}/5)...`);
      setTimeout(() => loadState(retryCount + 1), 500);
    }
  }
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
  if (msg.type === "REGEN_SCENE_FROM_DASHBOARD") {
    log(`Regen scene requested from dashboard: jobId=${msg.jobId}, sceneIdx=${msg.sceneIdx}`);
    regenScene(msg.jobId, msg.sceneIdx);
    sendResponse({ ok: true });
    return true;
  }
  if (msg.type === "GET_STATE") {
    sendResponse({ state });
    return true;
  }
  if (msg.type === "IMAGE_DOWNLOADED") {
    log(`Received IMAGE_DOWNLOADED: ${msg.filename}, src=${msg.src}`);
    const tabId = sender.tab ? sender.tab.id : null;
    uploadFileToBackend(msg.src, msg.filename, msg.index, "image", false, tabId)
      .then(() => {
        log(`Successfully uploaded image. Verifying receipt on backend...`);
        return handleImageDownloaded(msg.filename);
      })
      .then((ok) => {
        sendResponse({ ok });
      })
      .catch((err) => {
        log(`Failed to upload/verify image: ${err.message}. Trying backup handleImageDownloaded...`);
        handleImageDownloaded(msg.filename).then((ok) => {
          sendResponse({ ok });
        });
      });
    return true; // Keep message channel open for async response
  }
  if (msg.type === "VIDEO_DOWNLOADED") {
    log(`Received VIDEO_DOWNLOADED: ${msg.filename}, src=${msg.src}`);
    const tabId = sender.tab ? sender.tab.id : null;
    uploadFileToBackend(msg.src, msg.filename, msg.index, "video", false, tabId)
      .then(() => {
        log(`Successfully uploaded video. Verifying receipt on backend...`);
        return handleVideoDownloaded(msg.filename);
      })
      .then((ok) => {
        sendResponse({ ok });
      })
      .catch((err) => {
        log(`Failed to upload/verify video: ${err.message}. Trying backup handleVideoDownloaded...`);
        handleVideoDownloaded(msg.filename).then((ok) => {
          sendResponse({ ok });
        });
      });
    return true; // Keep message channel open for async response
  }
  if (msg.type === "UPLOAD_SINGLE_ASSET") {
    log(`Received UPLOAD_SINGLE_ASSET: filename=${msg.filename}, src=${msg.src}`);
    const tabId = sender.tab ? sender.tab.id : null;
    uploadFileToBackend(msg.src, msg.filename, msg.assetId, msg.mediaType, true, tabId)
      .then((data) => {
        log(`UPLOAD_SINGLE_ASSET completed successfully for ${msg.filename}`);
        if (state.singleAsset) {
          state.singleAsset.uploaded = true;
          state.singleAsset.uploadedUrl = data.url || "";
          state.singleAsset.uploadedThumb = data.thumb || data.url || "";
          saveState().then(() => {
            sendResponse({ ok: true, data });
          });
        } else {
          sendResponse({ ok: true, data });
        }
      })
      .catch((err) => {
        log(`UPLOAD_SINGLE_ASSET failed for ${msg.filename}: ${err.message}`);
        sendResponse({ ok: false, error: err.message });
      });
    return true; // Keep message channel open for async response
  }
  if (msg.type === "SCENE_COMPLETED") {
    handleSceneCompleted().then(() => {
      sendResponse({ ok: true });
    });
    return true;
  }
  if (msg.type === "RESET") {
    resetState();
    sendResponse({ ok: true });
    return true;
  }

  // ── Single Library Asset Generation ─────────────────────────────────────────
  if (msg.type === "GENERATE_SINGLE_ASSET") {
    log(`Single asset request: type=${msg.mediaType}, assetId=${msg.assetId}`);
    if (msg.token) {
      state.token = msg.token;
      chrome.storage.local.set({ token: msg.token });
    }
    if (msg.backendUrl) {
      state.backendUrl = msg.backendUrl;
      chrome.storage.local.set({ backendUrl: msg.backendUrl });
    }
    // Store request in state so onDeterminingFilename can name the file correctly
    state.singleAsset = {
      assetId: msg.assetId,
      mediaType: msg.mediaType,
      prompt: msg.prompt,
      tabId: null,
      dashboardTabId: sender.tab ? sender.tab.id : null
    };
    saveState().then(() => startSingleAssetGeneration());
    sendResponse({ ok: true });
    return true;
  }
  if (msg.type === "SINGLE_ASSET_REPLACED_CONFIRMED") {
    log("Received SINGLE_ASSET_REPLACED_CONFIRMED from page.");
    if (state.singleAsset && state.singleAsset.tabId) {
      chrome.tabs.remove(state.singleAsset.tabId).catch(() => {});
    }
    state.singleAsset = null;
    saveState();
    sendResponse({ ok: true });
    return true;
  }
  if (msg.type === "BGM_LINK_ACQUIRED") {
    log(`BGM link acquired from Epidemic Sound: ${msg.url}`);
    state.bgmUrl = msg.url || null;
    const tabToRemove = state.bgmTabId;
    state.bgmTabId = null;
    if (tabToRemove) {
      chrome.tabs.remove(tabToRemove).catch(() => {});
    }
    saveState().then(() => allDone());
    sendResponse({ ok: true });
    return true;
  }
  if (msg.type === "SINGLE_ASSET_UPLOAD_COMPLETE") {
    log(`Single asset upload complete message received: ${msg.filename}`);
    if (state.singleAsset) {
      const dashTabId = state.singleAsset.dashboardTabId;
      if (dashTabId) {
        chrome.tabs.sendMessage(dashTabId, {
          type: "MR_AI_SINGLE_ASSET_DOWNLOADED",
          assetId: state.singleAsset.assetId,
          mediaType: state.singleAsset.mediaType,
          url: msg.url,
          thumb: msg.thumb || msg.url
        }).catch((err) => {
          log(`Failed to send MR_AI_SINGLE_ASSET_DOWNLOADED to dashboard tab: ${err.message}`);
        });
      }
      if (state.singleAsset.tabId) {
        chrome.tabs.remove(state.singleAsset.tabId).catch(() => {});
      }
      state.singleAsset = null;
      saveState();
    }
    sendResponse({ ok: true });
    return true;
  }
  if (msg.type === "JOB_ERROR") {
    log(`Job error received: ${msg.error}`);
    state.phase = "idle";
    saveState().then(() => {
      notifyPopup(`❌ Error: ${msg.error}`, "error");
      reportError(msg.error);
    });
    sendResponse({ ok: true });
    return true;
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
    state.subtopicName = data.subtopic_name || "";
    state.bgmPrompt = data.bgm_prompt || `Upbeat cinematic background music for a short educational video about ${state.subtopicName || 'learning'}`;
    state.bgmUrl = null;
    state.bgmTabId = null;
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

// ── Single Scene Regeneration ────────────────────────────────────────────────
async function regenScene(jobId, sceneIdx) {
  state.jobId = jobId;
  state.currentSceneIdx = sceneIdx;
  state.singleSceneRegen = true;
  state.phase = "generating_images";
  await saveState();

  notifyPopup(`🔄 Regenerating scene ${sceneIdx + 1}...`);

  try {
    const res = await fetch(`${state.backendUrl}/api/extension/job/${jobId}`, {
      headers: { "X-App-Token": state.token }
    });
    const data = await res.json();
    if (!data.success) throw new Error(data.detail || "Failed to fetch job");

    state.scenes = data.scenes;
    state.subtopicName = data.subtopic_name || "";
    await saveState();

    const tabId = await getMetaTabId();
    if (tabId) {
      await sendNextScene(tabId);
    } else {
      await openMetaAI();
    }
  } catch (e) {
    notifyPopup(`❌ Regen failed: ${e.message}`, "error");
    state.phase = "idle";
    state.singleSceneRegen = false;
    await saveState();
    await reportError(e.message);
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
    jobId: state.jobId,
    subtopicName: state.subtopicName || ""
  };

  for (let attempt = 0; attempt < 12; attempt++) {
    if (msg.jobId !== state.jobId) {
      log(`Stale loop detected. Aborting sendNextScene for job ${msg.jobId}`);
      return;
    }
    try {
      await ensureContentScriptInjected(tabId, "content_meta.js");
      const resp = await chrome.tabs.sendMessage(tabId, msg);
      if (resp && resp.ok) {
        log(`Scene ${state.currentSceneIdx + 1} sent OK`);
        return;
      }
      if (resp && resp.reason === "busy") {
        if (resp.jobId === msg.jobId && resp.currentSceneIdx === msg.sceneIdx) {
          log(`Scene ${state.currentSceneIdx + 1} is already being generated by the content script (busy with same scene). Treating as OK.`);
          return;
        }
        notifyPopup(`⏳ Content script busy, waiting...`);
        await sleep(5000);
        continue;
      }
    } catch (e) { /* content script not ready yet */ }
    notifyPopup(`⏳ Waiting for Meta AI... (${attempt + 1}/12)`);
    await sleep(3000);
  }

  // If the job ID has changed since this loop started, ignore the failure!
  if (msg.jobId !== state.jobId) {
    log(`sendNextScene: Job ID changed from ${msg.jobId} to ${state.jobId}. Ignoring stale loop timeout.`);
    return;
  }

  notifyPopup(`❌ Could not reach Meta AI tab`, "error");
  state.phase = "idle";
  await saveState();
  await reportError("Content script unreachable after 12 attempts");
  startFastPoll();
}

// ── Image downloaded ──────────────────────────────────────────────────────────
async function handleImageDownloaded(filename) {
  if (!state.imagesDone.includes(filename)) state.imagesDone.push(filename);
  notifyPopup(`🖼️ Image ${state.imagesDone.length}/${state.scenes.length} done`);
  await saveState();

  // Polling verification loop to ensure image is successfully saved and copied on backend
  let verified = false;
  for (let attempt = 0; attempt < 25; attempt++) {
    try {
      log(`Verifying image on backend (attempt ${attempt + 1}/25)...`);
      const res = await fetch(`${state.backendUrl}/api/extension/job/${state.jobId}/image-done`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-App-Token": state.token },
        body: JSON.stringify({ filename, index: state.imagesDone.length - 1 })
      });
      if (res.ok) {
        const data = await res.json();
        if (data.copied) {
          verified = true;
          log(`Successfully verified and copied image on backend: ${filename}`);
          notifyPopup(`🖼️ Image ${state.imagesDone.length}/${state.scenes.length} verified on dashboard!`);
          break;
        }
      }
    } catch (e) {
      log(`Image verification connection error: ${e.message}`);
    }
    notifyPopup(`⏳ Syncing Image ${state.imagesDone.length} on dashboard... (${attempt + 1}/5)`);
    await sleep(2000);
  }
  return verified;
}

// ── Video downloaded ──────────────────────────────────────────────────────────
async function handleVideoDownloaded(filename) {
  if (!state.videosDone.includes(filename)) state.videosDone.push(filename);
  notifyPopup(`✅ Video ${state.videosDone.length}/${state.scenes.length} done`);

  // Polling verification loop to ensure video is successfully saved and copied on backend
  let verified = false;
  for (let attempt = 0; attempt < 25; attempt++) {
    try {
      log(`Verifying video on backend (attempt ${attempt + 1}/25)...`);
      const res = await fetch(`${state.backendUrl}/api/extension/job/${state.jobId}/video-done`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-App-Token": state.token },
        body: JSON.stringify({ filename, index: state.videosDone.length - 1 })
      });
      if (res.ok) {
        const data = await res.json();
        if (data.copied) {
          verified = true;
          log(`Successfully verified and copied video on backend: ${filename}`);
          notifyPopup(`✅ Video ${state.videosDone.length}/${state.scenes.length} verified on dashboard!`);
          break;
        }
      }
    } catch (e) {
      log(`Video verification connection error: ${e.message}`);
    }
    notifyPopup(`⏳ Syncing Video ${state.videosDone.length} on dashboard... (${attempt + 1}/5)`);
    await sleep(2000);
  }
  return verified;
}

// ── Single Asset Generation (Library Edit) ───────────────────────────────────
async function startSingleAssetGeneration() {
  const sa = state.singleAsset;
  if (!sa) return;

  notifyPopup(`🖼️ Single asset generation started (${sa.mediaType})...`);

  // Reuse existing Meta AI tab if available, otherwise open a new one
  const tabs = await chrome.tabs.query({ url: ["https://www.meta.ai/*", "https://meta.ai/*"] });
  let tabId;
  if (tabs.length > 0) {
    tabId = tabs[0].id;
    chrome.tabs.update(tabId, { active: true });
  } else {
    const tab = await chrome.tabs.create({ url: "https://www.meta.ai", active: true });
    tabId = tab.id;
    await waitForTabLoad(tabId, 25000);
    await sleep(4000);
  }

  // Guard: state.singleAsset may have been cleared during async tab operations
  if (!state.singleAsset) {
    log('startSingleAssetGeneration: state.singleAsset is null after tab ops, aborting.');
    return;
  }
  state.singleAsset.tabId = tabId;
  await saveState();

  // Send GENERATE_SINGLE_ASSET command to the Meta AI content script
  const assetId = sa.assetId;
  const mediaType = sa.mediaType;
  const fileExt = mediaType === 'video' ? 'mp4' : 'jpg';
  const filename = `single-gen-${assetId}.${fileExt}`;

  // Register the pending native download first
  if (sa.dashboardTabId) {
    pendingNativeDownloads[filename] = {
      tabId: sa.dashboardTabId,
      downloadId: null,
      isSingleAsset: true,
      assetId,
      mediaType
    };
  }

  for (let attempt = 0; attempt < 8; attempt++) {
    try {
      await ensureContentScriptInjected(tabId, "content_meta.js");
      const resp = await chrome.tabs.sendMessage(tabId, {
        type: "GENERATE_SINGLE_ASSET",
        prompt: sa.prompt,
        mediaType: sa.mediaType,
        filename
      });
      if (resp && resp.ok) {
        log(`Single asset generation started on Meta AI tab ${tabId}`);
        return;
      }
      if (resp && resp.reason === "busy") {
        await sleep(5000);
        continue;
      }
    } catch (e) { /* content script not ready */ }
    await sleep(3000);
  }

  notifyPopup(`❌ Could not reach Meta AI tab for single asset`, "error");
  state.singleAsset = null;
  await saveState();
}

async function finishSingleAsset(filename) {
  const sa = state.singleAsset;
  if (!sa) return;

  if (sa.uploaded) {
    log(`Single asset already uploaded via UPLOAD_SINGLE_ASSET. Skipping API sync in finishSingleAsset.`);
    const dashTabId = sa.dashboardTabId;
    if (dashTabId && sa.uploadedUrl) {
      chrome.tabs.sendMessage(dashTabId, {
        type: "MR_AI_SINGLE_ASSET_DOWNLOADED",
        assetId: sa.assetId,
        mediaType: sa.mediaType,
        url: sa.uploadedUrl,
        thumb: sa.uploadedThumb || sa.uploadedUrl
      }).catch((err) => {
        log(`Failed to send MR_AI_SINGLE_ASSET_DOWNLOADED to dashboard tab: ${err.message}`);
      });
    }
    
    // Close tab if applicable
    if (sa.tabId) {
      chrome.tabs.remove(sa.tabId).catch(() => {});
    }
    state.singleAsset = null;
    await saveState();
    return;
  }

  notifyPopup(`✅ Single asset downloaded: ${filename}. Syncing to backend...`);

  // Post to backend to register the single asset and replace in library
  const token = state.token;
  const backendUrl = state.backendUrl;
  const assetId = sa.assetId;
  const mediaType = sa.mediaType;

  try {
    const res = await fetch(`${backendUrl}/api/extension/single-asset-done`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-App-Token": token },
      body: JSON.stringify({ filename, asset_id: assetId, media_type: mediaType })
    });
    const data = await res.json();
    log(`Backend single-asset-done: ${JSON.stringify(data)}`);

    // Notify the dashboard content script to update the library UI
    const dashTabId = sa.dashboardTabId;
    if (dashTabId) {
      chrome.tabs.sendMessage(dashTabId, {
        type: "MR_AI_SINGLE_ASSET_DOWNLOADED",
        assetId,
        mediaType,
        url: data.url || "",
        thumb: data.thumb || data.url || ""
      }).catch(() => {});
    }
  } catch (e) {
    log(`Error syncing single asset: ${e.message}`);
  }

  // Keep singleAsset state alive until confirmed by the page window
  await saveState();
}

// ── Handle Scene Completed ───────────────────────────────────────────────────
async function handleSceneCompleted() {
  if (state.singleSceneRegen) {
    state.singleSceneRegen = false;
    state.phase = "idle";
    await saveState();
    notifyPopup(`🎉 Single scene ${state.currentSceneIdx + 1} regenerated successfully!`, "success");
    return;
  }

  state.currentSceneIdx++;
  await saveState();

  if (state.currentSceneIdx >= state.scenes.length) {
    state.phase = "generating_bgm";
    await saveState();
    notifyPopup("🎬 All video scenes generated! Opening Epidemic Sound for BGM...");
    await openEpidemicSound();
    return;
  }

  const tabId = await getMetaTabId();
  if (tabId) {
    await sendNextScene(tabId);
  } else {
    await openMetaAI();
  }
}

// ── All done → assemble ───────────────────────────────────────────────────────
async function allDone() {
  state.phase = "assembling";
  await saveState();
  notifyPopup(`⚙️ All scenes done! Assembling reel...`);

  // Wait for any active downloads related to this job to fully finish
  try {
    await waitForActiveDownloadsToComplete(state.jobId);
  } catch (e) {
    log(`Error waiting for active downloads: ${e.message}`);
  }

  // Delay closing the Meta AI tab to ensure the last scene is fully written/flushed to disk
  const tabToRemove = state.metaTabId;
  state.metaTabId = null;
  if (tabToRemove) {
    log(`Scheduling closure of Meta AI tab ${tabToRemove} in 15 seconds...`);
    setTimeout(() => {
      chrome.tabs.remove(tabToRemove).catch(() => {});
      log(`Closed Meta AI tab ${tabToRemove} successfully.`);
    }, 15000);
  }

  try {
    const res = await fetch(`${state.backendUrl}/api/extension/job/${state.jobId}/assemble`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-App-Token": state.token },
      body: JSON.stringify({
        videos: state.videosDone,
        images: state.imagesDone,
        bgm_url: state.bgmUrl || null
      })
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

// ── Open Epidemic Sound ──────────────────────────────────────────────────────
async function openEpidemicSound() {
  try {
    const tab = await chrome.tabs.create({ url: "https://www.epidemicsound.com/assistant/", active: true });
    state.bgmTabId = tab.id;
    await saveState();

    notifyPopup("🎵 Epidemic Sound opened. Waiting for page load...");
    await waitForTabLoad(tab.id, 25000);
    await sleep(4000); // extra buffer

    const msg = {
      type: "GENERATE_BGM",
      bgmPrompt: state.bgmPrompt || `Upbeat background music for an educational video about ${state.subtopicName || 'learning'}`
    };

    for (let attempt = 0; attempt < 8; attempt++) {
      try {
        await ensureContentScriptInjected(tab.id, "content_epidemic.js");
        const resp = await chrome.tabs.sendMessage(tab.id, msg);
        if (resp && resp.ok) {
          log("BGM generation instruction sent successfully to Epidemic Sound");
          return;
        }
      } catch (e) { /* content script not ready yet */ }
      notifyPopup(`⏳ Waiting for Epidemic Sound tab... (${attempt + 1}/8)`);
      await sleep(3000);
    }
    
    throw new Error("Could not contact content script after 8 attempts");
  } catch (e) {
    notifyPopup("⚠️ Epidemic Sound failed, skipping to assembly with default BGM", "error");
    log(`openEpidemicSound error: ${e.message}`);
    state.bgmUrl = null;
    await allDone();
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────
async function getMetaTabId() {
  if (state.metaTabId) {
    try {
      const tab = await chrome.tabs.get(state.metaTabId);
      if (tab && tab.url && (tab.url.includes("meta.ai") || tab.pendingUrl?.includes("meta.ai"))) {
        return state.metaTabId;
      }
    } catch (e) {
      log(`Stored metaTabId ${state.metaTabId} is no longer valid: ${e.message}`);
    }
  }

  // Fallback: query for any open meta.ai tab
  const tabs = await chrome.tabs.query({ url: ["https://www.meta.ai/*", "https://meta.ai/*"] });
  if (tabs.length > 0) {
    const activeTab = tabs.find(t => t.active);
    const chosenTab = activeTab || tabs[0];
    state.metaTabId = chosenTab.id;
    await saveState();
    return chosenTab.id;
  }

  return null;
}

async function ensureContentScriptInjected(tabId, scriptName) {
  try {
    const resp = await chrome.tabs.sendMessage(tabId, { type: "PING" });
    if (resp && resp.ok) {
      log(`Content script ${scriptName} is already responsive on tab ${tabId}`);
      return true;
    }
  } catch (e) {
    log(`Content script ${scriptName} not responding on tab ${tabId}: ${e.message}`);
  }

  log(`Attempting to dynamically inject ${scriptName} into tab ${tabId}...`);
  try {
    await chrome.scripting.executeScript({
      target: { tabId: tabId },
      files: [scriptName]
    });
    log(`Successfully injected ${scriptName} dynamically into tab ${tabId}`);
    await sleep(1000); // Wait 1 second for initialization
    return true;
  } catch (err) {
    log(`Failed to inject content script ${scriptName} dynamically: ${err.message}`);
    return false;
  }
}

async function resetState() {
  state = {
    jobId: null, scenes: [], imagesDone: [], videosDone: [],
    phase: "idle", currentSceneIdx: 0,
    token: state.token, backendUrl: state.backendUrl,
    metaTabId: null,
    bgmTabId: null, bgmUrl: null, bgmPrompt: null
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
  try {
    if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.id && chrome.runtime.sendMessage) {
      const p = chrome.runtime.sendMessage({ type: "LOG", message, logType: type });
      if (p && typeof p.catch === 'function') {
        p.catch(() => {});
      }
    }
  } catch (e) {
    // Ignore SW context errors during startup/invalidation
  }
}

async function getBlobFromUrl(src, senderTabId) {
  if (src.startsWith("blob:") || src.startsWith("data:")) {
    const tabId = senderTabId || state.metaTabId || (state.singleAsset ? state.singleAsset.tabId : null);
    if (!tabId) {
      throw new Error("No active tab to fetch local blob/data URL");
    }
    log(`Requesting tab ${tabId} to fetch local URL: ${src.substring(0, 80)}`);
    const res = await chrome.tabs.sendMessage(tabId, { type: "FETCH_LOCAL_BLOB", url: src });
    if (!res || !res.ok) {
      throw new Error(`Content script failed to fetch blob: ${res ? res.error : "no response"}`);
    }
    // Convert base64 data URL back to a Blob
    const dataUrl = res.base64;
    const arr = dataUrl.split(',');
    const mime = arr[0].match(/:(.*?);/)[1];
    const bstr = atob(arr[1]);
    let n = bstr.length;
    const u8arr = new Uint8Array(n);
    while (n--) {
      u8arr[n] = bstr.charCodeAt(n);
    }
    return new Blob([u8arr], { type: mime });
  } else {
    const fetchRes = await fetch(src);
    if (!fetchRes.ok) {
      throw new Error(`Failed to fetch media from CDN: status ${fetchRes.status}`);
    }
    return await fetchRes.blob();
  }
}

async function uploadFileToBackend(src, filename, indexOrAssetId, mediaType, isSingleAsset = false, senderTabId = null) {
  try {
    log(`uploadFileToBackend started: filename=${filename}, indexOrAssetId=${indexOrAssetId}, mediaType=${mediaType}, isSingleAsset=${isSingleAsset}`);
    const blob = await getBlobFromUrl(src, senderTabId);
    log(`Successfully retrieved blob: ${blob.size} bytes, mime=${blob.type}`);

    const formData = new FormData();
    formData.append("file", blob, filename);
    formData.append("filename", filename);
    formData.append("media_type", mediaType);

    let uploadUrl = "";
    if (isSingleAsset) {
      formData.append("asset_id", indexOrAssetId);
      uploadUrl = `${state.backendUrl}/api/extension/single-asset-done`;
    } else {
      formData.append("index", indexOrAssetId.toString());
      uploadUrl = `${state.backendUrl}/api/extension/job/${state.jobId}/upload-file`;
    }

    log(`Posting form data to: ${uploadUrl}`);
    const uploadRes = await fetch(uploadUrl, {
      method: "POST",
      headers: {
        "X-App-Token": state.token
      },
      body: formData
    });

    if (!uploadRes.ok) {
      const errText = await uploadRes.text();
      throw new Error(`Upload failed with status ${uploadRes.status}: ${errText}`);
    }

    const data = await uploadRes.json();
    log(`Successfully uploaded file to backend: ${JSON.stringify(data)}`);
    return data;
  } catch (e) {
    log(`uploadFileToBackend error: ${e.message}`);
    throw e;
  }
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

async function waitForActiveDownloadsToComplete(jobId, maxWaitMs = 60000) {
  const start = Date.now();
  log(`Checking for active downloads related to job ${jobId}...`);
  while (Date.now() - start < maxWaitMs) {
    const active = await new Promise((resolve) => {
      chrome.downloads.search({ state: "in_progress" }, (items) => {
        const hasJobDownload = items.some(item => {
          const base = (item.filename || "").toLowerCase();
          const url = (item.url || "").toLowerCase();
          return jobId && (base.includes(jobId.toLowerCase()) || url.includes(jobId.toLowerCase()));
        });
        resolve(hasJobDownload);
      });
    });
    if (!active) {
      log("All active downloads for this job are complete.");
      return;
    }
    log("Waiting for active downloads of this job to finish...");
    await sleep(3000);
  }
  log("Warning: Active downloads wait timed out.");
}

// ── Intercept and rename downloads triggered natively by page context click ──
chrome.downloads.onDeterminingFilename.addListener((item, suggest) => {
  // If the filename is already formatted by our extension, let it through unchanged!
  const isAlreadyRenamed = item.filename && (
    item.filename.startsWith("meta-vid") || 
    item.filename.startsWith("meta-img") || 
    item.filename.startsWith("flow-image") ||
    item.filename.startsWith("single-gen-")
  );
  if (isAlreadyRenamed) {
    suggest();
    return;
  }

  // Use the in-memory state synchronously to prevent MV3 async suggest crashes!
  const isVideo = item.mimeType?.includes("video") || item.filename?.endsWith(".mp4") || item.url?.includes(".mp4");
  const isImage = item.mimeType?.includes("image") || item.filename?.endsWith(".jpg") || item.filename?.endsWith(".jpeg") || item.url?.includes(".jpg") || item.url?.includes(".jpeg");

  // Single-asset generation takes priority — rename and let it flow through
  if (state.singleAsset && (isVideo || isImage)) {
    const sa = state.singleAsset;
    const fileExt = sa.mediaType === 'video' ? 'mp4' : 'jpg';
    const newFilename = `single-gen-${sa.assetId}.${fileExt}`;
    log(`Renaming single-asset download → ${newFilename}`);
    suggest({ filename: newFilename, conflictAction: "overwrite" });
    return;
  }

  const isTargetDownload = state.jobId && (isVideo || isImage);

  if (isTargetDownload) {
    let subtopicFirstWord = 'reel';
    if (state.subtopicName) {
      const cleanSubtopic = state.subtopicName.trim().replace(/[^a-zA-Z0-9\s-_]/g, '');
      const parts = cleanSubtopic.split(/\s+/);
      if (parts.length > 0 && parts[0]) {
        subtopicFirstWord = parts[0];
      }
    }

    const sceneNum = state.currentSceneIdx + 1;
    const ext = isVideo ? "mp4" : "jpg";
    const prefix = isVideo ? "meta-vid" : "meta-img";
    const newFilename = `${prefix}-${sceneNum}-${state.jobId || 'nojob'}-${subtopicFirstWord}.${ext}`;

    log(`Determining custom filename synchronously for target download: ${newFilename}`);
    suggest({ filename: newFilename, conflictAction: "overwrite" });
  } else {
    suggest();
  }
});

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
        // If this is a single-asset generation, call the dedicated handler
        if (pending.isSingleAsset) {
          delete pendingNativeDownloads[filename];
          finishSingleAsset(filename);
        } else {
          chrome.tabs.sendMessage(pending.tabId, {
            type: "DOWNLOAD_COMPLETE_SIGNAL",
            filename: filename,
            ok: true
          }).catch((err) => {
            log(`Failed to send DOWNLOAD_COMPLETE_SIGNAL to tab ${pending.tabId}: ${err.message}`);
          });
          delete pendingNativeDownloads[filename];
        }
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

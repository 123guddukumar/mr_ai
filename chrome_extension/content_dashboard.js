// ── MR AI Dashboard Link Content Script ─────────────────────────────────────────
console.log("[MR AI Content Dashboard] Loaded on dashboard page:", window.location.href);

// Listen to messages from the dashboard page window object
window.addEventListener("message", (event) => {
  // Only accept messages from ourselves
  if (event.source !== window) return;

  if (event.data && event.data.type === "MR_AI_START_JOB") {
    console.log("[MR AI Content Dashboard] Received MR_AI_START_JOB from dashboard:", event.data);
    chrome.runtime.sendMessage({
      type: "START_JOB_FROM_DASHBOARD",
      jobId: event.data.jobId,
      token: event.data.token,
      backendUrl: event.data.backendUrl
    });
  }
});

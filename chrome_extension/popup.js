// popup.js - MR AI Reel Generator Extension

// Load saved config on open
chrome.storage.local.get(['token', 'backendUrl'], (data) => {
  if (data.token) document.getElementById('token').value = data.token;
  document.getElementById('backend-url').value = data.backendUrl || 'https://test.3rdai.co';
});

// Listen for logs from background
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === 'LOG') addLog(msg.message, msg.logType);
});

// Poll state every 1.5 sec
setInterval(pollState, 1500);

// ── Save Config ───────────────────────────────────────────────────────────────
function saveConfig() {
  const token = document.getElementById('token').value.trim();
  const backendUrl = document.getElementById('backend-url').value.trim() || 'https://test.3rdai.co';
  if (!token) { addLog('Please enter your App Token', 'error'); return; }

  chrome.runtime.sendMessage({ type: 'SAVE_CONFIG', token, backendUrl });
  const badge = document.getElementById('saved-badge');
  badge.style.display = 'inline';
  setTimeout(() => badge.style.display = 'none', 2000);
  addLog('Config saved! Extension will auto-detect jobs.', 'success');
}

// ── Reset ─────────────────────────────────────────────────────────────────────
function resetJob() {
  chrome.runtime.sendMessage({ type: 'RESET' });
  document.getElementById('log-box').innerHTML = '<div class="log-line">Reset. Waiting for next job...</div>';
  document.getElementById('s-scenes').textContent = '—';
  document.getElementById('s-images').textContent = '0';
  document.getElementById('s-videos').textContent = '0';
  document.getElementById('phase-label').textContent = 'Waiting for job...';
  ['p1','p2','p3','p4'].forEach(id => document.getElementById(id).className = 'phase-step');
}

// ── Poll State ────────────────────────────────────────────────────────────────
function pollState() {
  chrome.runtime.sendMessage({ type: 'GET_STATE' }, (res) => {
    if (!res || !res.state) return;
    const s = res.state;

    document.getElementById('s-scenes').textContent = s.scenes?.length || '—';
    document.getElementById('s-images').textContent = s.imagesDone?.length || 0;
    document.getElementById('s-videos').textContent = s.videosDone?.length || 0;

    const phaseMap = { idle: 0, fetching: 1, generating_images: 2, generating_videos: 3, assembling: 4, done: 4, error: 0 };
    const phaseLabels = {
      idle: 'Waiting for job...',
      fetching: '📥 Fetching scenes...',
      generating_images: '🖼️ Generating images...',
      generating_videos: '🎬 Generating videos...',
      assembling: '⚙️ Assembling reel...',
      done: '✅ Done!',
      error: '❌ Error'
    };

    const cur = phaseMap[s.phase] || 0;
    document.getElementById('phase-label').textContent = phaseLabels[s.phase] || '';

    for (let i = 1; i <= 4; i++) {
      const el = document.getElementById(`p${i}`);
      if (i < cur) el.className = 'phase-step done';
      else if (i === cur) el.className = 'phase-step active';
      else el.className = 'phase-step';
    }
  });
}

// ── Add Log Line ──────────────────────────────────────────────────────────────
function addLog(msg, type) {
  const box = document.getElementById('log-box');
  const line = document.createElement('div');
  line.className = 'log-line' + (type === 'error' ? ' error' : type === 'success' ? ' success' : '');
  const time = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  line.textContent = `[${time}] ${msg}`;
  box.appendChild(line);
  box.scrollTop = box.scrollHeight;
  while (box.children.length > 50) box.removeChild(box.firstChild);
}

// ── Wire up buttons after DOM ready ──────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('btn-save').addEventListener('click', saveConfig);
  document.getElementById('btn-reset').addEventListener('click', resetJob);
});

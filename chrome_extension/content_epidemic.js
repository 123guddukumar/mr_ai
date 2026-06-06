// ── Epidemic Sound AI Search Automation Content Script ────────────────────────
// Runs on: https://www.epidemicsound.com/*

let epidemicIsProcessing = false;

function log(msg) {
  console.log(`[MR AI Epidemic] ${msg}`);
  chrome.runtime.sendMessage({ type: "LOG", message: `[Epidemic] ${msg}` }).catch(() => {});
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function extractLqMp3FromDOM() {
  const html = document.documentElement.innerHTML;
  const patterns = [
    /"lqMp3Url"\s*:\s*"(https?:?[\/\\]+audiocdn\.epidemicsound\.com[\/\\]+lqmp3[\/\\]+[^"]+\.mp3)"/i,
    /https?:?[\/\\]+audiocdn\.epidemicsound\.com[\/\\]+lqmp3[\/\\]+[^"\s\\/]+\.mp3/i
  ];
  
  for (const pattern of patterns) {
    const match = html.match(pattern);
    if (match) {
      const directUrl = (match[1] || match[0]).replace(/\\/g, '');
      log(`Found direct lqMp3Url in DOM: ${directUrl}`);
      return directUrl;
    }
  }
  return null;
}

function extractLqMp3ForTrack(trackUrl) {
  if (!trackUrl) return null;
  const match = trackUrl.match(/\/track\/([a-zA-Z0-9\-]+)/);
  if (!match) return null;
  
  const html = document.documentElement.innerHTML;
  const regex = /"lqMp3Url"\s*:\s*"(https?:?[\/\\]+audiocdn\.epidemicsound\.com[\/\\]+lqmp3[\/\\]+[^"]+\.mp3)"/gi;
  let m;
  const urls = [];
  while ((m = regex.exec(html)) !== null) {
    urls.push(m[1].replace(/\\/g, ''));
  }
  
  if (urls.length > 0) {
    log(`Found ${urls.length} lqMp3Urls in DOM. Returning the first one: ${urls[0]}`);
    return urls[0];
  }
  return null;
}

function getFirstTrackLinkFromDOM() {
  const rowSelectors = [
    '[class*="TrackRow"]',
    '[class*="track-row"]',
    '[class*="trackRow"]',
    '[class*="Track_row"]',
    'tr',
    '[role="row"]',
    '[class*="track" i]',
    '[class*="row" i]'
  ];
  
  for (const selector of rowSelectors) {
    try {
      const rows = Array.from(document.querySelectorAll(selector)).filter(r => r.offsetWidth > 100 && r.offsetHeight > 20);
      if (rows.length > 0) {
        const firstRow = rows[0];
        const trackLink = firstRow.querySelector('a[href*="/track/"]');
        if (trackLink && trackLink.href) {
          const fullUrl = new URL(trackLink.href, window.location.origin).href;
          log(`Found track link from first row DOM: ${fullUrl}`);
          return fullUrl;
        }
      }
    } catch (e) {
      log(`Error finding first row track link via ${selector}: ${e.message}`);
    }
  }
  
  // Fallback to global search for first track link
  const link = getDirectTrackLink();
  if (link) {
    log(`Found track link via global direct track link helper: ${link}`);
    return link;
  }
  return null;
}

async function resolveTrackUrlToMp3(trackUrl) {
  if (!trackUrl) return null;
  if (trackUrl.includes("audiocdn.epidemicsound.com") && trackUrl.endsWith(".mp3")) {
    return trackUrl;
  }
  
  // Try to find it in the current DOM first
  let direct = extractLqMp3ForTrack(trackUrl) || extractLqMp3FromDOM();
  if (direct) {
    log(`Resolved BGM link from DOM: ${direct}`);
    return direct;
  }
  
  // Fetch the track page from browser context to bypass Cloudflare
  log(`BGM link not found in DOM. Fetching track page same-origin: ${trackUrl}`);
  try {
    const response = await fetch(trackUrl);
    if (response.ok) {
      const text = await response.text();
      const patterns = [
        /"lqMp3Url"\s*:\s*"(https?:?[\/\\]+audiocdn\.epidemicsound\.com[\/\\]+lqmp3[\/\\]+[^"]+\.mp3)"/i,
        /https?:?[\/\\]+audiocdn\.epidemicsound\.com[\/\\]+lqmp3[\/\\]+[^"\s\\/]+\.mp3/i
      ];
      for (const pattern of patterns) {
        const match = text.match(pattern);
        if (match) {
          const mp3Url = (match[1] || match[0]).replace(/\\/g, '');
          log(`Successfully resolved direct BGM link from fetched page HTML: ${mp3Url}`);
          return mp3Url;
        }
      }
      log("Could not find any lqMp3Url pattern in fetched track page HTML.");
    } else {
      log(`Failed to fetch track page: status ${response.status}`);
    }
  } catch (e) {
    log(`Error fetching track page: ${e.message}`);
  }
  
  return trackUrl; // Fallback to original URL
}

// Listen for window message from injected clipboard hook
window.addEventListener('message', async (event) => {
  if (event.data && event.data.type === 'EPIDEMIC_COPIED_LINK') {
    const url = event.data.text;
    if (url && (url.includes('epidemicsound.com/track/') || url.includes('epidemicsound.com'))) {
      log(`Intercepted copied track link from clipboard: ${url}`);
      try {
        const directUrl = await resolveTrackUrlToMp3(url);
        log(`BGM URL to send: ${directUrl}`);
        chrome.runtime.sendMessage({ type: "BGM_LINK_ACQUIRED", url: directUrl }).catch(() => {});
      } catch (e) {
        log(`Error resolving track link: ${e.message}`);
        chrome.runtime.sendMessage({ type: "BGM_LINK_ACQUIRED", url: url }).catch(() => {});
      }
    }
  }
});

// Listen for messages from background script
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "GENERATE_BGM") {
    if (epidemicIsProcessing) {
      sendResponse({ ok: false, reason: "busy" });
      return true;
    }
    epidemicIsProcessing = true;
    log(`Starting BGM generation for prompt: "${msg.bgmPrompt}"`);
    processBGM(msg.bgmPrompt).then(() => {
      epidemicIsProcessing = false;
      sendResponse({ ok: true });
    }).catch(err => {
      epidemicIsProcessing = false;
      log(`BGM Error: ${err.message}`);
      sendResponse({ ok: false, error: err.message });
      // Notify background to assemble anyway (fallback to default BGM)
      chrome.runtime.sendMessage({ type: "BGM_LINK_ACQUIRED", url: null }).catch(() => {});
    });
    return true;
  }
  return true;
});

async function tryReadClipboard() {
  try {
    const text = await navigator.clipboard.readText();
    if (text && (text.includes('epidemicsound.com/track/') || text.includes('epidemicsound.com'))) {
      return text.trim();
    }
  } catch (e) {
    log(`Could not read clipboard directly: ${e.message}`);
  }
  return null;
}

async function waitForTracksToGenerate(minWaitMs = 30000, maxWaitMs = 90000) {
  log(`Waiting for tracks to generate... Min wait: ${minWaitMs}ms, Max wait: ${maxWaitMs}ms`);
  const startTime = Date.now();
  
  // Sleep the minimum wait time first (30 seconds) to allow initial generation to complete
  await sleep(minWaitMs);
  
  while (Date.now() - startTime < maxWaitMs) {
    const trackLinks = Array.from(document.querySelectorAll('a[href*="/track/"]')).filter(el => el.offsetWidth > 0);
    const playButtons = Array.from(document.querySelectorAll('button[aria-label*="play" i], button[aria-label*="Play" i]')).filter(el => el.offsetWidth > 0);
    
    let rowFound = false;
    const rowSelectors = ['[class*="TrackRow"]', '[class*="track-row"]', '[class*="trackRow"]', '[class*="Track_row"]'];
    for (const selector of rowSelectors) {
      const rows = Array.from(document.querySelectorAll(selector)).filter(r => r.offsetWidth > 100 && r.offsetHeight > 20);
      if (rows.length > 0) {
        rowFound = true;
        break;
      }
    }
    
    if (trackLinks.length > 0 || playButtons.length > 0 || rowFound) {
      log(`Detected generated tracks! Proceeding to copy link.`);
      return true;
    }
    
    log(`Still waiting for tracks to generate... (elapsed: ${Math.floor((Date.now() - startTime)/1000)}s)`);
    await sleep(2000);
  }
  
  log(`Reached maximum wait time. Proceeding anyway.`);
  return false;
}

async function processBGM(promptText) {
  log("BGM pipeline initiated. Processing overlays...");
  await sleep(3000);
  
  // 1. Accept cookies / popups
  await acceptCookiesPopup();
  await closeModals();
  await sleep(1000);
  
  // 2. Navigate to Assistant via Sidebar link click
  await navigateToAssistant();
  await sleep(3000);
  
  // 3. Locate and enter prompt into assistant/search input
  await enterSearchPrompt(promptText);
  
  // 4. Wait for BGM tracks to generate/load (min 30s, max 90s)
  log("Waiting for AI-generated music tracks...");
  await waitForTracksToGenerate(30000, 90000);
  
  // 5. Try direct DOM-based extraction of the first track row's link
  log("Attempting direct DOM track link extraction from the first visible row...");
  try {
    const firstTrackUrl = getFirstTrackLinkFromDOM();
    if (firstTrackUrl) {
      const resolvedUrl = await resolveTrackUrlToMp3(firstTrackUrl);
      if (resolvedUrl && resolvedUrl.includes("audiocdn.epidemicsound.com")) {
        log(`Success! Resolved direct BGM URL from DOM first row: ${resolvedUrl}`);
        chrome.runtime.sendMessage({ type: "BGM_LINK_ACQUIRED", url: resolvedUrl }).catch(() => {});
        return;
      }
    }
  } catch (domErr) {
    log(`DOM link discovery error: ${domErr.message}`);
  }
  
  // 6. Fallback: Automate clicking 3-dots and copy link
  log("Direct DOM extraction unsuccessful or did not yield a CDN link. Falling back to click-to-copy menu automation...");
  await clickThreeDots();
  await sleep(1200);
  await clickCopyLink();
  
  // Wait for the clipboard hook or direct clipboard read to capture the URL
  let waitStart = Date.now();
  let acquiredUrl = null;
  
  const messageListener = async (event) => {
    if (event.data && event.data.type === 'EPIDEMIC_COPIED_LINK') {
      const url = event.data.text;
      acquiredUrl = await resolveTrackUrlToMp3(url);
    }
  };
  window.addEventListener('message', messageListener);
  
  try {
    // Try immediate DOM extraction first
    const immediateCdn = extractLqMp3FromDOM();
    if (immediateCdn) {
      log(`Acquired immediate direct CDN URL from DOM: ${immediateCdn}`);
      chrome.runtime.sendMessage({ type: "BGM_LINK_ACQUIRED", url: immediateCdn }).catch(() => {});
      return;
    }

    while (Date.now() - waitStart < 15000) {
      if (acquiredUrl) {
        log(`Acquired URL via clipboard hook: ${acquiredUrl}`);
        chrome.runtime.sendMessage({ type: "BGM_LINK_ACQUIRED", url: acquiredUrl }).catch(() => {});
        return;
      }
      
      const clipText = await tryReadClipboard();
      if (clipText) {
        const directUrl = await resolveTrackUrlToMp3(clipText);
        log(`Acquired URL via direct clipboard read: ${directUrl}`);
        chrome.runtime.sendMessage({ type: "BGM_LINK_ACQUIRED", url: directUrl }).catch(() => {});
        return;
      }
      
      await sleep(1000);
    }
  } finally {
    window.removeEventListener('message', messageListener);
  }
  
  throw new Error("Timeout waiting for clipboard link acquisition");
}

async function acceptCookiesPopup() {
  const selectors = [
    '#onetrust-accept-btn-handler',
    '.onetrust-close-btn-handler',
    'button[id*="cookie" i]',
    'button[class*="cookie" i]',
    'button[aria-label*="accept" i]',
    '[id*="accept" i]',
    '[class*="accept" i]'
  ];
  
  for (const sel of selectors) {
    try {
      const btn = document.querySelector(sel);
      if (btn && btn.offsetWidth > 0 && btn.offsetHeight > 0) {
        btn.click();
        log(`Clicked cookie accept button via: ${sel}`);
        await sleep(1000);
        return;
      }
    } catch(e) {}
  }
  
  // Text content check
  const buttons = Array.from(document.querySelectorAll('button'));
  for (const btn of buttons) {
    const txt = (btn.textContent || '').toLowerCase().trim();
    if (txt === 'accept' || txt === 'accept all' || txt === 'allow cookies' || txt === 'accept cookies' || txt === 'agree' || txt === 'allow all') {
      btn.click();
      log(`Clicked cookie accept button via text match: "${btn.textContent.trim()}"`);
      await sleep(1000);
      return;
    }
  }
}

async function closeModals() {
  const closeSelectors = [
    'button[aria-label*="close" i]',
    'button[class*="close" i]',
    '[class*="modal" i] button[class*="close" i]',
    '.modal-close',
    '[class*="overlay" i] button'
  ];
  for (const sel of closeSelectors) {
    try {
      const btn = document.querySelector(sel);
      if (btn && btn.offsetWidth > 0) {
        btn.click();
        log(`Closed modal overlay via: ${sel}`);
        await sleep(500);
      }
    } catch(e) {}
  }
}

async function navigateToAssistant() {
  if (window.location.href.toLowerCase().includes('/assistant')) {
    log("Already on Assistant page. Skipping sidebar navigation.");
    return;
  }
  log("Locating 'Assistant' navigation link...");
  // Look for any links or buttons on the sidebar
  const elements = Array.from(document.querySelectorAll('a, button, [role="link"], li, span'));
  
  // Exact match first
  let assistantEl = elements.find(el => {
    const txt = (el.textContent || '').toLowerCase().trim();
    return txt === 'assistant' || txt === 'soundmatch';
  });
  
  // Partial match next
  if (!assistantEl) {
    assistantEl = elements.find(el => {
      const txt = (el.textContent || '').toLowerCase().trim();
      return txt.includes('assistant') || txt.includes('soundmatch') || txt.includes('sound match') || txt.includes('ai search');
    });
  }
  
  if (assistantEl) {
    log(`Navigating to Assistant page via element click: "${assistantEl.textContent.trim()}"`);
    assistantEl.click();
    return;
  }
  
  log("Sidebar 'Assistant' element not found. Attempting direct navigation to Search Page as fallback...");
  // If we can't find the assistant sidebar button, check if we're on the music search page
  if (!window.location.href.includes('/music/search')) {
    window.location.href = "https://www.epidemicsound.com/music/search";
  }
}

async function enterSearchPrompt(promptText) {
  // Common selectors for Epidemic Sound search bar/assistant input inside search or assistant panel
  const selectors = [
    'textarea[placeholder*="describe" i]',
    'textarea[placeholder*="assistant" i]',
    'input[placeholder*="describe" i]',
    'input[placeholder*="assistant" i]',
    'input[placeholder*="soundtrack" i]',
    'input[type="search"]',
    'input[placeholder*="search" i]',
    'textarea[placeholder*="search" i]',
    'input[type="text"]',
    'textarea'
  ];
  
  let input = null;
  for (const sel of selectors) {
    input = document.querySelector(sel);
    if (input && input.offsetWidth > 0) {
      log(`Found search input with selector: ${sel}`);
      break;
    }
  }
  
  if (!input) {
    throw new Error("Could not find prompt/search input field on page");
  }
  
  input.focus();
  await sleep(300);
  
  // React-safe value setter
  setReactValue(input, promptText);
  await sleep(300);
  
  // Press Enter key to trigger search
  input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true }));
  input.dispatchEvent(new KeyboardEvent('keypress', { key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true }));
  input.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true }));
  
  // Check if there is a form submit or a search submit button nearby
  const form = input.closest('form');
  if (form) {
    form.dispatchEvent(new Event('submit', { bubbles: true }));
  } else {
    // Look for button inside input container
    const container = input.parentElement;
    const searchBtn = container?.querySelector('button');
    if (searchBtn) {
      searchBtn.click();
    }
  }
  log(`Typed BGM search prompt and submitted: "${promptText}"`);
}

function setReactValue(element, value) {
  const nativeInputValueSetter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value') ||
    Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
  if (nativeInputValueSetter) {
    nativeInputValueSetter.set.call(element, value);
  } else {
    element.value = value;
  }
  element.dispatchEvent(new Event('input', { bubbles: true }));
  element.dispatchEvent(new Event('change', { bubbles: true }));
}

function getDirectTrackLink() {
  const links = Array.from(document.querySelectorAll('a[href*="/track/"]'));
  for (const link of links) {
    const href = link.href;
    if (href && !href.includes('/similar') && !href.includes('/artists/')) {
      const fullUrl = new URL(href, window.location.origin).href;
      return fullUrl;
    }
  }
  return null;
}

async function clickThreeDots() {
  log("Locating the 3-dot menu button for the first track...");
  
  // 1. Try to find track rows first to avoid clicking global/header menu buttons
  const rowSelectors = [
    '[class*="TrackRow"]',
    '[class*="track-row"]',
    '[class*="trackRow"]',
    '[class*="Track_row"]',
    'tr',
    '[role="row"]',
    '[class*="track" i]',
    '[class*="row" i]'
  ];
  
  for (const selector of rowSelectors) {
    try {
      const rows = Array.from(document.querySelectorAll(selector));
      const visibleRows = rows.filter(r => r.offsetWidth > 100 && r.offsetHeight > 20);
      if (visibleRows.length > 0) {
        log(`Found track rows using selector: ${selector}`);
        const firstRow = visibleRows[0];
        
        // Find buttons inside the first visible row
        const buttons = Array.from(firstRow.querySelectorAll('button, [role="button"]'));
        const dotsBtn = buttons.find(btn => {
          const label = (btn.getAttribute('aria-label') || btn.getAttribute('title') || '').toLowerCase();
          const txt = (btn.textContent || '').trim();
          return label.includes('more') || label.includes('menu') || label.includes('option') || label.includes('action') || txt === '...' || txt.includes('•••');
        }) || buttons[buttons.length - 1]; // Fallback to last button in the row
        
        if (dotsBtn) {
          log("Clicking the 3-dot action button in the first track row.");
          dotsBtn.click();
          return;
        }
      }
    } catch (e) {
      log(`Error scanning rows with selector ${selector}: ${e.message}`);
    }
  }
  
  // 2. Global fallback, excluding header/profile menus
  const buttons = Array.from(document.querySelectorAll('button, [role="button"]'));
  const actionBtns = buttons.filter(btn => {
    const label = (btn.getAttribute('aria-label') || btn.getAttribute('title') || '').toLowerCase();
    const txt = (btn.textContent || '').trim();
    // Filter out common header/profile/search buttons
    if (label.includes('profile') || label.includes('user') || label.includes('navigation') || label.includes('search')) {
      return false;
    }
    return label.includes('more') || label.includes('action') || label.includes('menu') || label.includes('options') || txt === '...' || txt.includes('•••') || label === 'context-menu';
  });
  
  if (actionBtns.length > 0) {
    log("Clicking first track's action menu button from global list...");
    actionBtns[0].click();
    return;
  }
  
  throw new Error("Could not find any track action menu button");
}

async function clickCopyLink() {
  log("Locating the Copy Link or Share option in the context menu...");
  // Scan all potential text container elements including divs
  const items = Array.from(document.querySelectorAll('button, [role="menuitem"], a, li, span, div'));
  
  // Look for exact/partial copy link text
  for (const item of items) {
    if (item.offsetWidth === 0 || item.offsetHeight === 0) continue; // Skip hidden elements
    const txt = (item.textContent || '').toLowerCase().trim();
    if (txt.includes('copy link') || txt.includes('copy track link') || txt === 'copy' || txt.includes('share')) {
      log(`Found menu item: "${item.textContent.trim()}"`);
      item.click();
      
      if (txt.includes('share')) {
        await sleep(1000);
        // Find copy link in share submenu
        const shareItems = Array.from(document.querySelectorAll('button, [role="menuitem"], a, li, span, div'));
        for (const shareItem of shareItems) {
          if (shareItem.offsetWidth === 0 || shareItem.offsetHeight === 0) continue;
          const shareTxt = (shareItem.textContent || '').toLowerCase().trim();
          if (shareTxt.includes('copy link') || shareTxt.includes('copy track link') || shareTxt === 'copy') {
            log(`Clicking copy link in share menu: "${shareItem.textContent.trim()}"`);
            shareItem.click();
            return;
          }
        }
      }
      return;
    }
  }
  throw new Error("Could not find Copy Link option in context menu");
}

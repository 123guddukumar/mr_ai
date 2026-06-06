// Hook navigator.clipboard.writeText inside the page (MAIN world) context to capture Epidemic Sound copy track link actions without CSP errors.
if (navigator.clipboard && !navigator.clipboard._mr_hooked) {
  navigator.clipboard._mr_hooked = true;
  const originalWriteText = navigator.clipboard.writeText;
  navigator.clipboard.writeText = function(text) {
    window.postMessage({ type: 'EPIDEMIC_COPIED_LINK', text: text }, '*');
    return originalWriteText.apply(this, arguments);
  };
  console.log("[MR AI Hook] Clipboard writeText hooked successfully in MAIN world.");
}

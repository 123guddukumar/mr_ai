# Calling Project - Daily Progress Report (14-Aug-2026)

This file contains two versions of the progress report.
1. **WhatsApp Version**: Placed in the code block below, formatted with `*bold*` stars for easy copy-pasting on WhatsApp.
2. **Markdown Version**: Stored below for documentation.

---

## 📱 WHATSAPP COPY-PASTE VERSION (COPY & SEND)

```text
*📢 CALLING PROJECT - DAILY PROGRESS REPORT (14-AUG-2026)*

Sir, today we successfully resolved all major calling issues, fixed agent redirection, and optimized the voice quality. Below is the summary of today's progress:

*1. Call Disconnection & Webhook Fix*
- Resolved the immediate call drop issue. We identified that the webhook redirect URL was pointing to a wrong path (`/telephony/vobiz/inbound`).
- Corrected the URL to the actual endpoint `/api/v1/telephony/inbound/run` and changed the transfer method to `POST`.
- Transferred calls now route to Dograh successfully without any drop.

*2. Welcome Menu Voice Optimization (Indian Female Voice)*
- Replaced the default robotic/American voice.
- Integrated *Sarvam AI's "Anushka" speaker*. The welcome instructions ("press 0 for IIP...") are now read in a highly realistic, clear, and professional Indian girl voice.

*3. Cloudflare R2 Propagation Lag Bypass*
- Previously, Vobiz would hang up immediately if R2 storage took too long to serve the welcome audio.
- Bypassed R2 for the welcome menu by serving the audio file directly from our local backend static server over ngrok. This ensures instant welcome playback and zero hang-ups.

*4. Active Call Persistence (Heartbeat Keep-Alive)*
- Fixed a bug where a caller would switch back to the default agent (UPAVP) after 2-3 responses.
- Implemented a *keep-alive heartbeat mechanism* in our completions endpoint. On every response turn, the selected agent's database session timer is automatically reset. The call now stays locked onto the chosen agent (e.g., IIP) for the entire duration of the call.

*5. Voice Response Clarity & Formatting (Stuttering Fix)*
- Added *Telephony Rules* to the LLM system prompt:
  - Instructed the LLM to strip all markdown formatting (stars, bullet points, hashes) and emojis. This ensures Dograh's TTS reads text smoothly without any stuttering.
  - Instructed the LLM to add spaces between phone number digits (e.g. `9 8 5 5...`), making the TTS read phone/contact numbers digit-by-digit clearly instead of saying billions/millions.

*6. Backend Bugs Fixed*
- Resolved a runtime `NameError` in `agents.py` where the `AgentPublicSession` model was not imported.
- Cleaned up duplicate agent names (e.g. "Vijay Ai Assistant" vs "Vijay AI Assistant") in the IVR menu description.

*Current Status:* Inbound IVR menu, DTMF agent selection, RAG agent completions, and call handoffs to Dograh are working perfectly with extremely low latency and premium voice quality. Ready for production!
```

---

## 📄 MARKDOWN VERSION (FOR DOCUMENTATION)

### 1. Inbound Webhook Path Correction
- **Issue**: Vobiz disconnected immediately after agent selection because it got a `404 Not Found` response.
- **Resolution**: Queried the Vobiz Application API config directly to find the actual webhook path configured in Dograh: `/api/v1/telephony/inbound/run`. Updated `.env` and modified the redirect method to `POST` to match Dograh requirements.

### 2. Welcome Menu Indian Female Accent (Sarvam AI Integration)
- **Feature**: Replaced the default system voice fallback with **Sarvam AI (Anushka)**.
- **Result**: The welcoming prompt is spoken in a professional and crystal-clear Indian English/Hindi female voice.

### 3. Welcome Playback Zero-Lag Fallback
- **Issue**: CDN replication lag on Cloudflare R2 resulted in temporary `404` errors when Vobiz queried the audio file immediately, terminating the call.
- **Resolution**: Configured `bypass_r2=True` for the IVR menu to serve the file directly from the local FastAPI disk over ngrok, ensuring instant playback.

### 4. Completions Heartbeat keep-alive
- **Issue**: Due to stateless completions requests from Dograh, the backend relied on a 40-second selection timeout. Once the timeout expired, the call switched back to UPAVP.
- **Resolution**: Programmed a keep-alive heartbeat that updates the `updated_at` database timestamp on every chat completions turn, resetting the 40-second expiration. The call stays mapped to the selected agent for its entire active duration.

### 5. Stutter-Free Response & Digit-by-Digit Numbers
- **Formatting**: LLM now automatically strips all special markdown characters (`*`, `-`, `#`, emojis) so Dograh's TTS engine doesn't stutter.
- **Phone Numbers**: LLM automatically injects spaces between numbers (e.g., `+9 1 8 0...`) so they are read clearly digit-by-digit.

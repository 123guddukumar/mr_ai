# MR AI RAG — Inbound Telephony Developer API Documentation 📞

This document outlines the professional developer integration guidelines and API references for building inbound voice assistant flows. It covers how to route incoming calls to specific AI agents (like routing `08065354041` to the **Vijay AI Assistant**), how to integrate a Call Button into your application, and how to retrieve call logs, text transcripts, and audio recordings.

---

## 🏗️ Dynamic Inbound Call Flow Architecture

```mermaid
sequenceDiagram
    autonumber
    actor User as User Mobile Phone
    participant Button as Web/Mobile App (Call Button)
    participant Carrier as Telephony Provider (Vobiz)
    participant FastAPI as FastAPI Backend (:8000)
    database DB as PostgreSQL Database

    User->>Button: Click "Call Assistant"
    Button->>User: Initiates phone dialer to +918065354041
    User->>Carrier: Places call to DID: +918065354041
    Carrier->>FastAPI: Webhook: POST /api/telephony/inbound-call
    Note over FastAPI: Matches dialed number (To) to Agent in DB
    FastAPI->>DB: Query Agent by phone_number matching +918065354041
    DB-->>FastAPI: Returns Vijay AI Assistant Config
    FastAPI->>DB: Initialize Call Session (AgentPublicSession)
    FastAPI-->>Carrier: Returns XML: <Play>greeting.mp3</Play><Record action="/api/telephony/call/{agent_id}/transcribe"/>
    Note over User, Carrier: User talks, voice loop processes transcription & TTS audio
```

---

## ☎️ Dynamic DID Routing & Click-to-Call Button

### 1. How the DID Number (`08065354041`) Works
When a user calls the public DID number `08065354041`, the telephony carrier makes a POST request to your `/api/telephony/inbound-call` webhook. The backend matches the dialed number (`To` parameter) to route the call to the configured agent.

The matching is performed in the following order of priority:
1. **Direct Match:** An agent has `phone_number` set to `+918065354041` or `08065354041`.
2. **Suffix Match:** Matches the last 10 digits (`8065354041`) of the agent's configured number.
3. **Customization JSON Match:** Looks inside `customization_json` for a key named `"call_number"` containing `8065354041`.

> [!NOTE]
> Currently, the DID `08065354041` is bound to the **Vijay AI Assistant** agent. Any call placed to this number will automatically load Vijay's RAG knowledge base, prompt parameters, and ElevenLabs voice configuration.

### 2. Developer Click-to-Call Button Integration
To allow users to call the AI agent directly from your web page or mobile app, you can implement a Call Button.

#### **A. Native Browser Dialer (Recommended for Mobile web & Apps)**
This triggers the native dialer on the user's phone. When they click the button, their phone rings the DID number, initiating the AI agent conversation.

```html
<!-- Sleek Tailwind-styled Call Button -->
<a href="tel:+918065354041" 
   class="inline-flex items-center gap-2 px-6 py-3 bg-gradient-to-r from-emerald-500 to-teal-600 hover:from-emerald-600 hover:to-teal-700 text-white font-medium rounded-xl shadow-lg transition-all transform hover:-translate-y-0.5 active:translate-y-0 duration-200">
    <svg xmlns="http://www.w3.org/2000/svg" class="w-5 h-5 animate-pulse" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
        <path stroke-linecap="round" stroke-linejoin="round" d="M3 5a2 2 0 012-2h3.28a1 1 0 01.94.725l.548 2.2a1 1 0 01-.321.988l-1.305.98a10.582 10.582 0 004.872 4.872l.98-1.305a1 1 0 01.988-.321l2.2.548a1 1 0 01.725.94V19a2 2 0 01-2 2h-1C9.716 21 3 14.284 3 6V5z" />
    </svg>
    Talk to Vijay AI Assistant
</a>
```

#### **B. In-App WebRTC VoIP Dialer (Direct Browser Audio)**
If you want the call to occur directly inside the web browser without using cellular calling, you must embed a SIP/WebRTC client (e.g. JsSIP or Sip.js) pointing to your FreePBX Extension (`8001` or similar as specified in the local setup guide).

---

## ⚡ Developer REST APIs (Inbound Only)

All client-facing operations require your application token passed in the headers:
`X-App-Token: <your_client_token>`

---

### 1. Fetch Call Logs (Sessions List)
Retrieves a list of all inbound call sessions for a specific AI agent, sorted chronologically (newest first).

* **Endpoint:** `GET /api/agents/{agent_id}/sessions`
* **Headers:**
  ```http
  X-App-Token: your-app-token-here
  ```
* **Path Parameters:**
  * `agent_id` (string): The unique identifier of the target AI agent (e.g., Vijay's agent ID).

* **Success Response (200 OK):**
  ```json
  [
    {
      "id": 12,
      "session_id": "tel_918084661813",
      "agent_id": "d6b54c1e63290e77",
      "device_id": "918084661813",
      "device_name": "Voice Call",
      "user_name": "Voice Caller 1813",
      "phone_number": "918084661813",
      "analysis": {
        "intent": "Wants to query pricing details",
        "summary": "Customer called asking about plan prices, AI answered using RAG context."
      },
      "action_button": null,
      "created_at": "2026-08-22T12:19:24.634000",
      "updated_at": "2026-08-22T12:20:10.150000"
    }
  ]
  ```

---

### 2. Fetch Call Details (Recording & Transcript)
Retrieves the complete list of dialogue turns (transcriptions and audio recordings) of a specific call session.

* **Endpoint:** `GET /api/agents/sessions/{session_id}/history`
* **Headers:**
  ```http
  X-App-Token: your-app-token-here
  ```
* **Path Parameters:**
  * `session_id` (string): The unique session ID (obtained from the Call Logs API, e.g., `tel_918084661813` or Vobiz CallSid).

* **Success Response (200 OK):**
  ```json
  [
    {
      "role": "user",
      "content": "Hello Vijay, can you explain the pricing plan?",
      "created_at": "2026-08-22T12:19:28.100000",
      "file_url": "https://api.vobiz.ai/recordings/rec_aff3f75d.mp3",
      "file_name": "",
      "file_type": ""
    },
    {
      "role": "assistant",
      "content": "Namaste! Our standard pricing plan starts from 499 Rupees per month which includes unlimited query resolutions.",
      "created_at": "2026-08-22T12:19:32.400000",
      "file_url": "",
      "file_name": "",
      "file_type": ""
    }
  ]
  ```

> [!TIP]
> The `file_url` on the `user` role contains the public HTTP URL of the **recorded audio** file of the caller's specific statement, which you can stream or play in your own dashboard.

---

### 3. Fetch Session Status
Retrieves current session status information for an agent.

* **Endpoint:** `GET /api/agents/{agent_id}/session-status`
* **Query Parameters:**
  * `device_id` (string, optional): The caller's phone number (without plus sign, e.g. `918084661813`).
  * `session_id` (string, optional): The specific session ID.

* **Success Response (200 OK):**
  ```json
  {
    "session": {
      "id": 12,
      "session_id": "tel_918084661813",
      "agent_id": "d6b54c1e63290e77",
      "device_id": "918084661813",
      "device_name": "Voice Call",
      "user_name": "Voice Caller 1813",
      "phone_number": "918084661813",
      "analysis": null,
      "action_button": null,
      "created_at": "2026-08-22T12:19:24.634000",
      "updated_at": "2026-08-22T12:20:10.150000"
    }
  }
  ```

---

## 🗄️ Database Schemas Reference

Developers can directly query these PostgreSQL tables if executing queries inside the shared database.

### 1. `agent_public_sessions` (Call Log & Details)
Each call has exactly one session record containing caller details and metadata.

| Column | Type | Description |
| :--- | :--- | :--- |
| `session_id` | `VARCHAR(64)` | Unique session ID. Set to `tel_<phone_number>` for inbound calls. |
| `agent_id` | `VARCHAR(64)` | The AI Agent ID linked to the call. |
| `device_id` | `VARCHAR(64)` | The identifier of the device (set to caller's phone number). |
| `device_name`| `VARCHAR(200)`| Fixed as `"Voice Call"` for telephony sessions. |
| `phone_number`| `VARCHAR(50)` | Caller's cleaned phone number (e.g. `918084661813`). |
| `user_name` | `VARCHAR(200)`| Formatted caller display name (e.g. `"Voice Caller 1813"`). |
| `analysis_json`| `TEXT` | LLM parsed call intent/sentiment summary. |
| `created_at` | `TIMESTAMP` | Call initiation timestamp. |

### 2. `agent_public_messages` (Call Dialogue Turns & Audio)
Contains each chat block/turn (transcript and audio URL).

| Column | Type | Description |
| :--- | :--- | :--- |
| `session_id` | `VARCHAR(64)` | FK referencing `agent_public_sessions.session_id`. |
| `role` | `VARCHAR(20)` | Who spoke (`"user"` or `"assistant"`). |
| `content` | `TEXT` | Clean text transcription (for user) or generated response (for AI). |
| `file_url` | `VARCHAR(500)`| Public URL of the user's recorded audio file from Vobiz. |
| `created_at` | `TIMESTAMP` | When the sentence was spoken. |

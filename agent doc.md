# MR AI RAG v2 - Agent REST API Integration Guide 🤖📖

Welcome to the **Agent Integration Guide**! This document provides comprehensive documentation for developers looking to integrate AI Agents into their own projects. 

It covers Agent CRUD operations (Create/Read/Edit/Delete), Knowledge Base (KB) Ingestions (PDF & Web Crawling), Public/Private Chat interfaces, Visitor Sessions logs, and the **AI Conversation Analysis** feature.

---

## 🔒 Authentication & Base URL

All REST API requests require authentication. Pass the client API token in the HTTP Headers:

| Header Name | Value | Description |
| :--- | :--- | :--- |
| `X-App-Token` | `your_auth_token_here` | Client token obtained on successful login. |

* **Base API Path:** `http://<server-ip-or-domain>:<port>/api` (e.g. `http://localhost:8000/api`)

---

## 🧭 Agent Management APIs (CRUD)

### 1. Create a New Agent
Register a highly configurable AI agent with custom personality, voice, and system configurations.
* **HTTP Method:** `POST`
* **Endpoint:** `/api/agents`
* **Request Body (`application/json`):**
  ```json
  {
    "name": "Property Support Assistant",
    "description": "Handles inbound leads for residential properties",
    "category": "calling",
    "personality": "Helpful, friendly, and expert in real estate sales.",
    "starting_message": "Hello! I am your Property Support assistant. How can I help you find your dream home today?",
    "voice_config": {
      "provider": "elevenlabs",
      "voice_name": "pNInz6obpgDQGcFmaJgB",
      "api_key": "your_elevenlabs_key_here"
    },
    "system_config": {
      "provider": "gemini",
      "model": "gemini-3.5-flash",
      "api_key": "your_gemini_key_here",
      "system_prompt": "You are a senior real estate advisor. Help the user find apartments in Delhi NCR."
    },
    "customization": {
      "logo_url": "https://example.com/logo.png",
      "color": "#4f46e5",
      "chat_link": "https://example.com/chat/property-agent",
      "author_image_url": "https://example.com/avatar.jpg",
      "qa_pairs": [
        {
          "q": "What is the price of 3BHK?",
          "a": "The starting price for 3BHK in Noida Sector 150 is ₹1.2 Crore."
        }
      ]
    },
    "datastores": []
  }
  ```
* **Response Example (`200 OK`):**
  ```json
  {
    "agent_id": "da3243d9babb9387",
    "name": "Property Support Assistant",
    "description": "Handles inbound leads for residential properties",
    "category": "calling",
    "personality": "Helpful, friendly, and expert in real estate sales.",
    "starting_message": "Hello! I am your Property Support assistant. How can I help you find your dream home today?",
    "voice_config": { ... },
    "system_config": { ... },
    "customization": { ... },
    "datastores": [],
    "is_active": true,
    "created_at": "2026-07-18T12:00:00"
  }
  ```

---

### 2. Update/Edit an Agent
Modify settings of an existing agent. Send only the fields that need updating.
* **HTTP Method:** `PATCH`
* **Endpoint:** `/api/agents/{agent_id}`
* **Request Body (`application/json`):**
  ```json
  {
    "name": "Premium Real Estate Assistant",
    "starting_message": "Namaste! Welcome to Premium Real Estate Support.",
    "is_active": true
  }
  ```
* **Response Example (`200 OK`):**
  Matches the agent response format containing modified settings.

---

### 3. List All Agents
Fetch all agents created under your client account.
* **HTTP Method:** `GET`
* **Endpoint:** `/api/agents`
* **Response Example (`200 OK`):**
  ```json
  [
    {
      "agent_id": "da3243d9babb9387",
      "name": "Premium Real Estate Assistant",
      "category": "calling",
      "kb_source_count": 2,
      "is_active": true
    }
  ]
  ```

---

### 4. Get Agent Detail
Retrieve full details of a specific agent, including associated datastores and its uploaded knowledge base (KB) sources.
* **HTTP Method:** `GET`
* **Endpoint:** `/api/agents/{agent_id}`
* **Response Example (`200 OK`):**
  ```json
  {
    "agent_id": "da3243d9babb9387",
    "name": "Premium Real Estate Assistant",
    "description": "...",
    "category": "calling",
    "personality": "...",
    "starting_message": "...",
    "voice_config": { ... },
    "system_config": { ... },
    "customization": { ... },
    "datastores": [],
    "is_active": true,
    "created_at": "2026-07-18T12:00:00",
    "kb_source_count": 1,
    "sources": [
      {
        "id": 1,
        "source_type": "url",
        "source_name": "Example Domain",
        "chunk_count": 1,
        "raw_text": "Example Domain ...",
        "indexed_at": "2026-07-18T12:05:00"
      }
    ],
    "kb_sources": [
      {
        "id": 1,
        "source_type": "url",
        "source_name": "Example Domain",
        "chunk_count": 1,
        "raw_text": "Example Domain ...",
        "indexed_at": "2026-07-18T12:05:00"
      }
    ]
  }
  ```

---

### 5. Delete an Agent
Permanently delete an AI agent and its local vector embeddings.
* **HTTP Method:** `DELETE`
* **Endpoint:** `/api/agents/{agent_id}`
* **Response Example (`200 OK`):**
  ```json
  {
    "success": true
  }
  ```

---

## 🗂️ Knowledge Base (KB) Management

Agents can ingest custom PDF files or URLs to answer questions based on your custom files (RAG).

### 1. Upload PDF Document to Agent
* **HTTP Method:** `POST`
* **Endpoint:** `/api/agents/{agent_id}/upload-pdf`
* **Content-Type:** `multipart/form-data`
* **Form Data:**
  - `file`: `[Your Binary PDF File]`
* **Response Example (`200 OK`):**
  ```json
  {
    "success": true,
    "total_chunks": 14
  }
  ```

### 2. Ingest Website URL to Agent
Crawl a public website URL, extract its content, chunk it, and index it into the agent's vector space.
* **HTTP Method:** `POST`
* **Endpoint:** `/api/agents/{agent_id}/ingest-url`
* **Request Body (`application/json`):**
  ```json
  {
    "url": "https://example.com/about-us"
  }
  ```
* **Response Example (`200 OK`):**
  ```json
  {
    "success": true,
    "total_chunks": 4
  }
  ```

### 3. Remove a Source from Agent's KB
Delete an ingested PDF or URL source from the agent's database and FAISS vector index.
* **HTTP Method:** `DELETE`
* **Endpoint:** `/api/agents/{agent_id}/sources/{source_id}`
* **Response Example (`200 OK`):**
  ```json
  {
    "success": true
  }
  ```

---

## 📱 QR Code Integration

To integrate QR code scanning:
1. Generate a QR code in your project pointing to the public chat URL of the agent:
   `http://<your-frontend-domain>/agent-chat?id={agent_id}`
2. When visitors scan the QR code, they are redirected to a beautiful conversational interface where they can chat with the agent (which automatically logs visitor lead information and creates a session).

---

## 👥 Visitor Sessions & Log History

When external visitors scan the QR code or chat with your agent, a session is automatically created, capturing visitor info (Device Name, User Name, Phone Number).

> 💡 **Session & Visitor Multi-Session Tracking**: While visitor info is identified by their `device_id` (enabling you to group multiple sessions by visitor), each conversation is tracked under a unique `session_id`. Clicking the **Trash/Clear Chat** icon in the chat screen automatically spawns a **new session ID** for that visitor, allowing you to count and view separate chat histories for the same user.
> 
> 👤 **Automatic Name & Phone Persistence**: Once a visitor shares their name or mobile number in *any* chat session, these details are permanently saved under their `device_id`. Any subsequent sessions created on the same device will automatically inherit and pre-populate these details. The visitor will not be prompted to share them again.

### 1. Get Agent Visitor Sessions
Get a list of all logged visitor sessions for a specific agent.
* **HTTP Method:** `GET`
* **Endpoint:** `/api/agents/{agent_id}/sessions`
* **Response Example (`200 OK`):**
  ```json
  [
    {
      "id": 1,
      "session_id": "sess-5ee28080ec",
      "agent_id": "da3243d9babb9387",
      "device_id": "dev-f38b25d06b",
      "device_name": "Chrome/Windows",
      "user_name": "Aditya Sharma",
      "phone_number": "9876543210",
      "analysis": {
        "category": "meeting",
        "intent": "Wants to book a site visit on Sunday.",
        "meaning": "Highly interested hot lead looking for a 3BHK flat in Noida Sector 150.",
        "next_steps": "Call the client on Saturday evening to confirm timing and arrange transport."
      },
      "action_button": {
        "action_type": "call",
        "phone_number": "9876543210",
        "message": "Call Us Now",
        "created_at": "2026-07-20T17:10:00"
      },
      "created_at": "2026-07-18T12:05:44",
      "updated_at": "2026-07-18T12:15:30"
    }
  ]
  ```

> ⚙️ **Dashboard Frontend Processing**:
> To render the upgraded nested view, group these sessions by visitor identifier (e.g. `device_id` or `phone_number`). Calculate the total sessions count per visitor (e.g., `👤 Guest Visitor [💬 3 Chats]`) and render interactive dropdowns allowing the owner to select and load individual session chat logs using `/api/agents/sessions/{session_id}/history`.


---

### 2. Get Individual Session Chat Log
Retrieve all messages exchanged in a specific visitor session.
* **HTTP Method:** `GET`
* **Endpoint:** `/api/agents/sessions/{session_id}/history`
* **Response Example (`200 OK`):**
  ```json
  [
    {
      "role": "user",
      "content": "Hi, I am looking to book a meeting for site visit.",
      "created_at": "2026-07-18T12:05:44"
    },
    {
      "role": "assistant",
      "content": "Hi Aditya! I can help you schedule a site visit. Which Sunday would you prefer?",
      "created_at": "2026-07-18T12:06:02"
    }
  ]
  ```

---

### 3. Get Public Visitor Chat History (Client Side)
Fetch past messages and session details for a public visitor using their `device_id` to restore chat history on page load.
* **HTTP Method:** `GET`
* **Endpoint:** `/api/agents/{agent_id}/public-history?device_id={device_id}&session_id={session_id}`
* **Response Example (`200 OK`):**
  ```json
  {
    "session": {
      "session_id": "sess-5ee28080ec",
      "user_name": "Aditya Sharma",
      "phone_number": "9876543210",
      "action_button": null
    },
    "messages": [
      {
        "role": "user",
        "content": "What are your services?"
      },
      {
        "role": "assistant",
        "content": "We offer residential property consulting and site visits."
      }
    ]
  }
  ```

---

### 4. Get Public Session Status (Client Polling)
Fetch real-time session status and active creator action buttons for a public visitor.
* **HTTP Method:** `GET`
* **Endpoint:** `/api/agents/{agent_id}/session-status?device_id={device_id}&session_id={session_id}`
* **Response Example (`200 OK`):**
  ```json
  {
    "session": {
      "session_id": "sess-5ee28080ec",
      "action_button": {
        "action_type": "whatsapp",
        "phone_number": "9876543210",
        "message": "Connect on WhatsApp",
        "created_at": "2026-07-20T17:15:00"
      }
    }
  }
  ```

---

### 5. Send Creator Action Button (Call Now / WhatsApp Connect)
Allows the Agent Creator to send a **Call Now** or **WhatsApp Connect** action button to a specific visitor session from the dashboard.
* **HTTP Method:** `POST`
* **Endpoint:** `/api/agents/sessions/{session_id}/send-action`
* **Header:** `X-App-Token: your_auth_token_here`
* **Request Body (`application/json`):**
  ```json
  {
    "action_type": "call",       // "call" | "whatsapp"
    "phone_number": "9876543210",
    "message": "Call Us Now"     // Optional custom message
  }
  ```
* **Response Example (`200 OK`):**
  ```json
  {
    "status": "success",
    "action_button": {
      "action_type": "call",
      "phone_number": "9876543210",
      "message": "Call Us Now",
      "created_at": "2026-07-20T17:10:00"
    }
  }
  ```

---

### 6. Clear Session Action Button
Clear/dismiss an active action button from a visitor's session.
* **HTTP Method:** `DELETE`
* **Endpoint:** `/api/agents/sessions/{session_id}/clear-action`
* **Response Example (`200 OK`):**
  ```json
  {
    "status": "success"
  }
  ```

---

### 7. AI Conversation Analysis

You can analyze visitor conversation histories using the agent's own configured LLM settings. This is available at two levels: **Session-level** and **Visitor (Device)-level**.

#### A. Single Session Analysis
Analyze a visitor's history in a single specific session.
* **HTTP Method:** `POST`
* **Endpoint:** `/api/agents/sessions/{session_id}/analyze`
* **Response Example (`200 OK`):**
  ```json
  {
    "category": "meeting",
    "intent": "Wants to book a site visit on Sunday.",
    "meaning": "Highly interested hot lead looking for a 3BHK flat in Noida Sector 150.",
    "next_steps": "Call the client on Saturday evening to confirm timing and arrange transport."
  }
  ```
  *(Note: The result is automatically cached in the `agent_public_sessions` table under the `analysis_json` column, so subsequent fetches return instantly.)*

#### B. Holistic Visitor Analysis (Device-Level)
Merge all chat sessions from a specific device (representing a single visitor's entire return history) and perform a comprehensive holistic analysis with key bullet insights.
* **HTTP Method:** `POST`
* **Endpoint:** `/api/agents/sessions/analyze-device`
* **Request Body (`application/json`):**
  ```json
  {
    "device_id": "dev-f38b25d06b",
    "agent_id": "da3243d9babb9387"
  }
  ```
* **Response Example (`200 OK`):**
  ```json
  {
    "category": "support",
    "intent": "Visitor is enquiring about UPAVP housing schemes registration dates and pricing details.",
    "meaning": "Hot prospect who has returned across 3 separate sessions to check eligibility criteria and pricing.",
    "next_steps": "Contact visitor to help with UPAVP application process directly.",
    "key_points": [
      "Customer is looking for 2BHK/3BHK flats.",
      "Has visited the UPAVP site physically once.",
      "Wants to know if senior citizen discounts are applicable."
    ],
    "session_count": 3,
    "total_messages": 18
  }
  ```

---

## 💬 Chat (RAG Ask) APIs

### 1. Inbound Ask API (Private/Workspace chat)
Main chat endpoint used within your private workspace.
* **HTTP Method:** `POST`
* **Endpoint:** `/api/agents/{agent_id}/ask`
* **Request Body (`application/json`):**
  ```json
  {
    "question": "What is the price of a 3BHK apartment?",
    "history": [
      {"role": "user", "content": "Hi"},
      {"role": "assistant", "content": "Hello! How can I help you?"}
    ],
    "is_voice": false
  }
  ```
* **Response Example (`200 OK`):**
  ```json
  {
    "answer": "The starting price for a 3BHK in Noida Sector 150 is ₹1.2 Crore based on our knowledge documents.",
    "sources": [
      {
        "source_file": "Example Domain",
        "page_number": 1
      }
    ],
    "is_rag": true
  }
  ```

---

### 2. Public Ask API (Visitor Chat via QR scan)
Chat endpoint used by public users scanning QR codes. It maps messages to a specific `session_id` and captures visitor details.
* **HTTP Method:** `POST`
* **Endpoint:** `/api/agents/{agent_id}/public-ask`
* **Request Body (`application/json`):**
  ```json
  {
    "question": "Please extract information from this document.",
    "session_id": "sess-5ee28080ec",
    "device_id": "dev-f38b25d06b",
    "device_name": "Chrome/Windows",
    "user_name": "Aditya Sharma",
    "phone_number": "9876543210",
    "file_context": "Extracted text or summary from a user uploaded file here..." // Optional
  }
  ```
* **Response Example (`200 OK`):**
  ```json
  {
    "answer": "Great, Aditya! I can arrange a meeting for you. Let me know your preferred day.",
    "sources": [],
    "is_rag": false,
    "session_id": "sess-5ee28080ec",
    "action_button": null
  }
  ```

---

### 3. Upload Chat File (Images, PDFs, Videos, Documents)
Upload files directly inside the chat interface (ChatGPT/Gemini style) to be sent as context to the AI model. Max file size is **20MB**.
* **HTTP Method:** `POST`
* **Endpoint:** `/api/agents/{agent_id}/upload-chat-file`
* **Content-Type:** `multipart/form-data`
* **Request Form:**
  - `file`: `[Binary File: jpg, png, gif, webp, pdf, docx, txt, csv, mp4, etc.]`
* **Response Example (`200 OK`):**
  ```json
  {
    "success": true,
    "file_type": "pdf",                                    // "image" | "pdf" | "document" | "text" | "video" | "file"
    "display_name": "resume.pdf",
    "extracted_text": "Extracted contents of the PDF...",   // For images, descriptions from Vision AI (Gemini Vision) are returned here
    "preview_data_url": null,                              // Base64 data URL for images (for chat UI display)
    "size_bytes": 1048576
  }
  ```
  *(Note: To send this file in the conversation, first call this endpoint to obtain `extracted_text`, then pass it in the `file_context` parameter of the `public-ask` API.)*

---

## 🎙️ Voice Mode & Real-time TTS/STT APIs

The agent system has built-in low-latency voice capabilities using standard AI models and TTS APIs (ElevenLabs, Sarvam, or default MR_AI).

### 1. Inbound Voice Request (Ask API)
To chat in voice mode, set `"is_voice": true` in the request body when calling the Ask API. The backend will automatically instruct the LLM to write replies that are ultra-short (1-2 sentences), natural, conversational, and exclude markdown bullet points or formatting.
* **HTTP Method:** `POST`
* **Endpoint:** `/api/agents/{agent_id}/ask`
* **Request Parameter:** `"is_voice": true` inside the JSON payload.

---

### 2. Stream Audio Response (Text-to-Speech)
Convert any text output from the agent into a playable binary audio stream (MP3/WAV) using the agent's configured TTS provider.
* **HTTP Method:** `GET`
* **Endpoint:** `/api/agents/{agent_id}/speak`
* **Query Parameters:**
  - `text` (URL-encoded string): The response text to convert to speech.
* **Response:**
  - Binary audio data stream (`audio/mpeg` for ElevenLabs, or `audio/wav` for Sarvam).

---

### 3. Test TTS Voice Settings
Test voice rendering before saving agent voice configuration.
* **HTTP Method:** `POST`
* **Endpoint:** `/api/agents/test-voice`
* **Request Body (`application/json`):**
  ```json
  {
    "provider": "elevenlabs",
    "voice_id": "pNInz6obpgDQGcFmaJgB",
    "api_key": "your_elevenlabs_api_key_here",
    "text": "Hello, this is a voice test."
  }
  ```
* **Response:**
  - Binary audio data response.

---

### 4. Real-time Streaming STT (WebSockets)
For real-time streaming speech-to-text (user speaks, and text is transcribed dynamically with sub-second latency), connect to the server's WebSocket:
* **Protocol:** `ws` or `wss`
* **Endpoint:** `/api/agents/ws/transcribe`
* **Workflow:**
  1. Open WebSocket connection.
  2. Stream binary audio buffer (e.g. raw microphone PCM/wav bytes) to the socket.
  3. The server pipes these bytes to Deepgram Nova-3 (optimised for multilingual English/Hindi) and streams back JSON transcription frames in real-time.
  4. Send a text message `{"type": "stop"}` or close the socket to terminate.


---

## 🚩 Agent Report & Feedback APIs

These endpoints allow external developers to integrate Report and Feedback collection directly inside their own client application where they have embedded or imported the agent.

### 1. Submit Feedback or Report (Public)
Submit user feedback (with ratings) or a report (complaint/issue) regarding an agent.
* **HTTP Method:** `POST`
* **Endpoint:** `/api/agents/{agent_id}/feedback`
* **Authentication:** None (Public endpoint, can be called by anyone using the chatbot)
* **Request Body (`application/json`):**
  ```json
  {
    "user_name": "John Doe",            // Optional (String, default is "Anonymous")
    "user_email": "john@example.com",   // Optional (String)
    "feedback_type": "feedback",        // Required: "feedback" | "report"
    "rating": 5,                        // Optional: 1-5 (Integer, only for "feedback" type)
    "comment": "This agent answered all my questions perfectly!", // Required (Text)
    "device_id": "dev-f38b25d06b",      // Optional (String, tracking device of the visitor)
    "session_id": "sess-5ee28080ec"     // Optional (String, session of the visitor)
  }
  ```
* **Response Example (`200 OK`):**
  ```json
  {
    "status": "ok",
    "feedback": {
      "id": 12,
      "agent_id": "da3243d9babb9387",
      "user_name": "John Doe",
      "user_email": "john@example.com",
      "feedback_type": "feedback",
      "rating": 5,
      "comment": "This agent answered all my questions perfectly!",
      "device_id": "dev-f38b25d06b",
      "session_id": "sess-5ee28080ec",
      "created_at": "2026-07-20T11:25:00"
    }
  }
  ```

### 2. Retrieve Feedback and Reports list (Protected)
Fetch all reports and feedback submitted for a specific agent.
* **HTTP Method:** `GET`
* **Endpoint:** `/api/agents/{agent_id}/feedback`
* **Authentication:** Required (`X-App-Token` passed in request header)
* **Response Example (`200 OK`):**
  ```json
  [
    {
      "id": 12,
      "agent_id": "da3243d9babb9387",
      "user_name": "John Doe",
      "user_email": "john@example.com",
      "feedback_type": "feedback",
      "rating": 5,
      "comment": "This agent answered all my questions perfectly!",
      "device_id": "dev-f38b25d06b",
      "session_id": "sess-5ee28080ec",
      "created_at": "2026-07-20T11:25:00"
    }
  ]
  ```



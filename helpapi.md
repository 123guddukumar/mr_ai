# 📖 API Help & Integration Guide (`helpapi.md`)

This guide explains how to use the newly added APIs for **Custom Meeting Durations** and **Sub-Agent Page Customizations** (including brand names, landing page templates, logo uploads, and preview layouts).

---

## 1. 📅 Daily Planner, Meeting Duration & Recurrence API

When creating or modifying daily plans of category `meeting`, you can pass:
- A custom duration in minutes (`duration_mins`). This automatically syncs to the underlying meeting scheduler.
- A recurrence flag (`is_recurring`). If set to `true`, completing the plan (manually or via scheduler auto-completion) automatically schedules the same meeting for the next day at the exact same time.

### A. Create a Daily Plan / Meeting
* **Endpoint**: `POST /api/root-agent/plans`
* **Headers**:
  * `X-App-Token`: `<token>` (Authentication)
  * `Content-Type`: `application/json`
* **JSON Body**:
```json
{
  "title": "Strategy Sync with Guddu",
  "description": "Discussing branding and new landing page layouts",
  "category": "meeting",
  "plan_date": "2026-08-06",
  "plan_time": "14:30",
  "duration_mins": 45,
  "is_recurring": true
}
```
> [!NOTE]
> `duration_mins` defaults to `30` if omitted.
> `is_recurring` defaults to `false`. Set to `true` to enable daily repeating.

### B. Edit / Update a Daily Plan / Meeting
* **Endpoint**: `PUT /api/root-agent/plans/{plan_id}`
* **Headers**:
  * `X-App-Token`: `<token>`
  * `Content-Type`: `application/json`
* **JSON Body**:
```json
{
  "title": "Strategy Sync with Guddu (Updated)",
  "description": "Discussing branding and new landing page layouts",
  "category": "meeting",
  "plan_date": "2026-08-06",
  "plan_time": "15:00",
  "duration_mins": 90,
  "is_recurring": false
}
```

### C. Fetch Today's Plans
* **Endpoint**: `GET /api/root-agent/plans/today`
* **Headers**:
  * `X-App-Token`: `<token>`
* **Response**: Returns a JSON list of plans. Meetings will return with `duration_mins`:
```json
[
  {
    "plan_id": "plan_abc123",
    "title": "Strategy Sync with Guddu",
    "category": "meeting",
    "plan_date": "2026-08-06",
    "plan_time": "15:00",
    "duration_mins": 90,
    "status": "upcoming"
  }
]
```

---

## 2. 🤖 Sub-Agent Customization & Branding API

To customize sub-agent chat interfaces and landing pages with custom logos, templates, button positions, sizes, and layout preview configurations.

### Step 1: Upload Brand Images (Logo, Avatar, Background)
Before setting customization configs, upload the image files (Brand Logo, Landing Page Avatar, Custom Background) to get public URLs.

* **Endpoint**: `POST /api/clients/upload-image`
* **Headers**:
  * `X-App-Token`: `<token>`
* **Request (Multipart Form-Data)**:
  * `file`: `[Select File]` (PNG, JPG, WebP)
* **Response**:
```json
{
  "success": true,
  "url": "/uploads/image_abc12345.png"
}
```
Use the returned `url` to populate the `logo_url`, `author_image_url`, or `custom_bg_url` in the agent schema.

---

### Step 2: Create or Edit Agent Customizations
Create or update your agent details. The `customization` block contains layout rules for custom landing pages and buttons.

* **Endpoints**:
  * Create Agent: `POST /api/agents`
  * Update Agent: `PATCH /api/agents/{agent_id}`
* **Headers**:
  * `X-App-Token`: `<token>`
  * `Content-Type`: `application/json`
* **Request JSON Body**:
```json
{
  "name": "Support Agent",
  "description": "Handles client queries and books consultations",
  "category": "Customer Support",
  "personality": "Professional and polite",
  "starting_message": "Hello! Welcome to our custom brand page.",
  "system_config": {
    "provider": "gemini",
    "model": "gemini-2.5-flash",
    "api_key": "YOUR_API_KEY",
    "system_prompt": "You are a helpful customer service representative.",
    "response_language": "default"
  },
  "voice_config": {
    "voice_name": "en-US-Standard-C",
    "provider": "google",
    "api_key": ""
  },
  "datastores": [],
  "custom_slug": "support-agent-custom-page",
  "customization": {
    "brand_name": "My Premium Brand",
    "logo_url": "/uploads/logo_abc123.png",
    "color": "#ff7a00",
    "chat_link": "https://magnifi.in/agent-chat?id=support-agent-custom-page",
    "author_image_url": "/uploads/avatar_xyz456.png",
    "whatsapp_number": "919876543210",
    "call_number": "919876543210",
    "template": "custom",
    "custom_bg_url": "/uploads/bg_image_789.png",
    "custom_btn_layout": "stacked",
    "custom_btn_position": 25,
    "custom_btn_size": 1.2,
    "qa_pairs": [
      {
        "q": "What are your services?",
        "a": "We provide premium AI automation and web solutions."
      }
    ]
  }
}
```

#### Customization Payload Breakdown

| Parameter | Type | Description / Valid Values |
| :--- | :--- | :--- |
| **`brand_name`** | `string` | Display name of the brand. |
| **`logo_url`** | `string` | URL to the brand's logo image. |
| **`color`** | `string` | HEX code for brand theme (e.g. `#ff7a00`), used in QR codes & chat highlights. |
| **`chat_link`** | `string` | Custom Webhook / Redirect chat URL. |
| **`author_image_url`**| `string` | Profile image / Avatar URL shown on the template's landing page. |
| **`whatsapp_number`** | `string` | Phone number with country code (no `+`) for direct WhatsApp contact. |
| **`call_number`** | `string` | Phone number with country code (no `+`) for direct cellular phone calls. |
| **`template`** | `string` | Chosen Landing page theme template: `""` (Standard), `"template1"`, `"template2"`, `"template3"`, `"custom"`. |
| **`custom_bg_url`** | `string` | Background image URL (active when `template` is `"custom"`). |
| **`custom_btn_layout`**| `string` | Direct layout position style of action buttons: `"row"` (side-by-side), `"stacked"` (vertical columns), `"small_side"`. |
| **`custom_btn_position`**| `integer`| Bottom offset spacing of buttons (in pixels, e.g. `2` to `250`). |
| **`custom_btn_size`** | `float` | Button scale multiplier (e.g., `0.5` to `2.0`). |
| **`qa_pairs`** | `array` | Structured custom training/FAQ pairs: `[ { "q": "Question", "a": "Answer" } ]`. |

---

### Step 3: Previewing Customized Templates

To preview templates and check layouts:
- In the frontend dashboard, clicking **"👁️ Preview"** opens `/share-agent-chat.html?id={agent_id}&preview=true`.
- The preview page parses customization configuration values instantly from state and loads the designated layout template styling (`#landing-template-2` etc.) before persisting modifications.
- Live public shared links are loaded directly via URL Slug routing at: `/share-agent-chat.html?id={agent_id}` or using custom domain slugs.

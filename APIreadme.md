# MR AI RAG v2 - Classroom REST API Documentation 📖

Welcome to the modular Classroom API documentation! This guide details how to navigate the classroom structure step-by-step to avoid excessive data payload sizes, how to perform administrative CRUD operations (Add/Edit/Delete), and how to generate/access premium study assets (Notes, Descriptions, Reels, and Quizzes) at the subtopic level.

---

## 🔒 Authentication

All authenticated endpoints require the client token passed via the HTTP Header:

| Header Name | Value | Description |
| :--- | :--- | :--- |
| `X-App-Token` | `your_auth_token_here` | Client session token obtained on successful login. |

---

## 🧭 Step-by-Step Hierarchy Navigation APIs

Instead of fetching the entire hierarchical tree at once (which generates massive 20,000+ line payloads), navigate sequentially using the following high-performance REST APIs:

### 1. Get All Exams
Get all exams associated with your client account.
* **HTTP Method:** `GET`
* **Endpoint:** `/api/classroom/exams`
* **Response Example:**
  ```json
  {
    "success": true,
    "exams": [
      {
        "exam_id": "exam-2eede497ef248a95",
        "name": "IIT JEE Main",
        "category": "Engineering",
        "image_url": "https://pub-4766722e137c4258a9233495746c4f5a.r2.dev/assets/jee.png",
        "description": "Joint Entrance Examination",
        "created_at": "2026-06-01T12:00:00"
      }
    ]
  }
  ```

### 2. Get Papers under an Exam
Get all question papers / syllabus versions mapped to a specific exam.
* **HTTP Method:** `GET`
* **Endpoint:** `/api/classroom/exams/{exam_id}/papers`
* **Response Example:**
  ```json
  {
    "success": true,
    "papers": [
      {
        "paper_id": "paper-83cfd10f",
        "exam_id": "exam-2eede497ef248a95",
        "name": "Paper 1 (Physics, Chemistry & Maths)",
        "created_at": "2026-06-01T12:05:00"
      }
    ]
  }
  ```

### 3. Get Subjects under a Paper
Get all subjects related to a specific exam paper.
* **HTTP Method:** `GET`
* **Endpoint:** `/api/classroom/papers/{paper_id}/subjects`
* **Response Example:**
  ```json
  {
    "success": true,
    "subjects": [
      {
        "subject_id": "subject-ab45f210",
        "exam_id": "exam-2eede497ef248a95",
        "paper_id": "paper-83cfd10f",
        "name": "Physics",
        "color": "#ff5722",
        "chapter_count": 12,
        "topic_count": 48,
        "subtopic_count": 142,
        "created_at": "2026-06-01T12:10:00"
      }
    ]
  }
  ```

### 4. Get Chapters under a Subject
Get all chapters matching a given subject.
* **HTTP Method:** `GET`
* **Endpoint:** `/api/classroom/subjects/{subject_id}/chapters`
* **Response Example:**
  ```json
  {
    "success": true,
    "chapters": [
      {
        "chapter_id": "chapter-de88f01b",
        "subject_id": "subject-ab45f210",
        "name": "Kinematics",
        "created_at": "2026-06-01T12:15:00"
      }
    ]
  }
  ```

### 5. Get Topics under a Chapter
Get all topics inside a chosen chapter.
* **HTTP Method:** `GET`
* **Endpoint:** `/api/classroom/chapters/{chapter_id}/topics`
* **Response Example:**
  ```json
  {
    "success": true,
    "topics": [
      {
        "topic_id": "topic-77aef14c",
        "chapter_id": "chapter-de88f01b",
        "name": "Projectile Motion",
        "created_at": "2026-06-01T12:20:00"
      }
    ]
  }
  ```

### 6. Get Subtopics under a Topic
Get all granular subtopics listed under a specific topic.
* **HTTP Method:** `GET`
* **Endpoint:** `/api/classroom/topics/{topic_id}/subtopics`
* **Response Example:**
  ```json
  {
    "success": true,
    "subtopics": [
      {
        "subtopic_id": "subtopic-321ab908",
        "topic_id": "topic-77aef14c",
        "name": "Horizontal Range & Maximum Height",
        "description": "Calculations and formulas of trajectory range...",
        "notes": "# Study Notes on Projectile Range...",
        "created_at": "2026-06-01T12:25:00"
      }
    ]
  }
  ```

### 7. Get Subtopic Detailed Profile
Get full information about a single subtopic (including study description and generated revision notes).
* **HTTP Method:** `GET`
* **Endpoint:** `/api/classroom/subtopics/{subtopic_id}`
* **Response Example:**
  ```json
  {
    "success": true,
    "subtopic": {
      "subtopic_id": "subtopic-321ab908",
      "topic_id": "topic-77aef14c",
      "name": "Horizontal Range & Maximum Height",
      "description": "Full explanatory overview of horizontal range...",
      "notes": "# 📖 Detailed Physics Notes...",
      "created_at": "2026-06-01T12:25:00"
    }
  }
  ```

---

## 💡 Accessing & Generating Subtopic Study Assets

Each subtopic acts as a repository for learning resources. Use these endpoints to trigger AI generation and fetch notes, descriptions, quizzes, and educational reel videos.

### 📝 Notes & Descriptions
Notes and descriptions are returned directly in the response of the **Subtopic Detailed Profile** (`GET /api/classroom/subtopics/{subtopic_id}`). 

If they are empty, you can trigger their AI generation:

#### Generate/Regenerate Description
Triggers Groq/OpenAI to generate a comprehensive, highly-styled conceptual description in markdown format.
* **HTTP Method:** `POST`
* **Endpoint:** `/api/classroom/subtopics/{subtopic_id}/generate-description`
* **Response:** Returns the newly generated markdown text and saves it automatically to the database.

#### Generate/Regenerate Notes (Multi-Language)
Triggers the AI to write highly extensive, diagram-integrated, premium study notes.
* **HTTP Method:** `POST`
* **Endpoint:** `/api/classroom/subtopics/{subtopic_id}/generate-notes`
* **Form Parameters:**
  - `language` (Optional): Target language name (e.g., `English`, `Hindi`, `Spanish`). Default is `English`.
* **Response:** Returns the detailed markdown notes with embedded pollinations AI diagrams.

---

### 📝 Interactive Practice Quizzes
Quizzes are generated dynamically on-the-fly based on the subtopic's study notes or description.

#### Generate Subtopic MCQ Quiz
* **HTTP Method:** `POST`
* **Endpoint:** `/api/classroom/subtopics/{subtopic_id}/quiz/generate`
* **Response Payload Example:**
  ```json
  {
    "success": true,
    "quiz": [
      {
        "question": "What angle of projection yields the maximum horizontal range?",
        "options": ["30 degrees", "45 degrees", "60 degrees", "90 degrees"],
        "answer": "45 degrees"
      }
    ]
  }
  ```

---

### 🎬 Educational Reels & Short Videos
Generate and stream professional short-form educational vertical videos directly mapped to the subtopic.

#### 1. Generate/Assemble a New Reel
Compiles visual elements, TTS voice-over, background cinematic music, and highlighted subtitles into an educational reel. 
All completed reels are **automatically saved to your Cloudflare R2 bucket** using your high-speed public link!
* **HTTP Method:** `POST`
* **Endpoint:** `/api/classroom/subtopics/{subtopic_id}/generate-reel`
* **JSON Body Parameters:**
  - `language` (Optional): Narration language (e.g. `English`, `Hindi`). Default is `English`.
  - `voice_id` (Optional): ElevenLabs Voice ID hash for narration.
* **Response Payload Example:**
  ```json
  {
    "success": true,
    "content_id": "8fa8bc7d10eef2a3",
    "video_url": "https://pub-4766722e137c4258a9233495746c4f5a.r2.dev/reels/subtopic_subtopic-321ab908/reel_8fa8bc7d10eef2a3.mp4",
    "scenes": [...],
    "script": "Narration screenplay script text..."
  }
  ```

#### 2. Get All Reels for a Subtopic (Authenticated)
Get all previously compiled reels for a specific subtopic.
* **HTTP Method:** `GET`
* **Endpoint:** `/api/classroom/subtopics/{subtopic_id}/reels`
* **Header Required:** `X-App-Token`

#### 3. GET All Reels for a Subtopic (PUBLIC — Zero Auth Needed)
Perfect for cross-project integration, third-party apps, or static external APIs. Anyone can access this public endpoint without header authorization!
* **HTTP Method:** `GET`
* **Endpoint:** `/api/classroom/public/subtopics/{subtopic_id}/reels`
* **Header Required:** None

---

## 🛠️ Syllabus Content Management (CRUD: Add & Edit)

Build or modify your classroom curriculum tree using standard JSON REST requests.

### 1. Subject Operations

#### Create a Subject
* **HTTP Method:** `POST`
* **Endpoint:** `/api/classroom/papers/{paper_id}/subjects`
* **JSON Request Body:**
  ```json
  {
    "name": "Organic Chemistry",
    "color": "#4caf50"
  }
  ```

#### Edit a Subject
* **HTTP Method:** `PUT`
* **Endpoint:** `/api/classroom/subjects/{subject_id}`
* **JSON Request Body:**
  ```json
  {
    "name": "Advanced Organic Chemistry",
    "color": "#2e7d32"
  }
  ```

---

### 2. Chapter Operations

#### Create a Chapter
* **HTTP Method:** `POST`
* **Endpoint:** `/api/classroom/subjects/{subject_id}/chapters`
* **JSON Request Body:**
  ```json
  {
    "name": "Hydrocarbons"
  }
  ```

#### Edit a Chapter
* **HTTP Method:** `PUT`
* **Endpoint:** `/api/classroom/chapters/{chapter_id}`
* **JSON Request Body:**
  ```json
  {
    "name": "Aliphatic Hydrocarbons"
  }
  ```

---

### 3. Topic Operations

#### Create a Topic
* **HTTP Method:** `POST`
* **Endpoint:** `/api/classroom/chapters/{chapter_id}/topics`
* **JSON Request Body:**
  ```json
  {
    "name": "Alkanes & Alkenes"
  }
  ```

#### Edit a Topic
* **HTTP Method:** `PUT`
* **Endpoint:** `/api/classroom/topics/{topic_id}`
* **JSON Request Body:**
  ```json
  {
    "name": "Structure of Alkanes"
  }
  ```

---

### 4. Subtopic Operations

#### Create a Subtopic
* **HTTP Method:** `POST`
* **Endpoint:** `/api/classroom/topics/{topic_id}/subtopics`
* **JSON Request Body:**
  ```json
  {
    "name": "Isomerism in Alkanes",
    "description": "Initial study description overview..."
  }
  ```

#### Edit a Subtopic
* **HTTP Method:** `PUT`
* **Endpoint:** `/api/classroom/subtopics/{subtopic_id}`
* **JSON Request Body:**
  ```json
  {
    "name": "Structural Isomerism in Alkanes",
    "description": "Updated detail notes..."
  }
  ```

---

## 📰 Current Affairs APIs

Manage Current Affairs topics, upload source materials, generate news scripts, and compile short-form vertical news reels.

### 1. List Current Affairs Topics
Get all Current Affairs topics associated with your client account.
* **HTTP Method:** `GET`
* **Endpoint:** `/api/classroom/current-affairs`
* **Response Example:**
  ```json
  {
    "success": true,
    "topics": [
      {
        "ca_topic_id": "ca-7b19de8a3b01a2f4",
        "name": "India Launches New Space Mission",
        "script": "Custom news narration script text...",
        "created_at": "2026-06-06T12:00:00"
      }
    ]
  }
  ```

### 2. Create Current Affairs Topic (with Option to Add Script)
Create a new Current Affairs topic. You can optionally pass a custom news script in the body.
* **HTTP Method:** `POST`
* **Endpoint:** `/api/classroom/current-affairs`
* **JSON Request Body:**
  ```json
  {
    "name": "G7 Summit 2026 Updates",
    "script": "In today's headlines, world leaders gathered for the G7 summit..."
  }
  ```

### 3. Update Current Affairs Topic
Edit the topic name or script content.
* **HTTP Method:** `PUT`
* **Endpoint:** `/api/classroom/current-affairs/{ca_topic_id}`
* **JSON Request Body:**
  ```json
  {
    "name": "G7 Summit 2026 Key Takeaways",
    "script": "Updated narration script focusing on climate resolutions..."
  }
  ```

### 4. Upload PDF Source for Current Affairs
Upload a news document or PDF source to extract context for this topic.
* **HTTP Method:** `POST`
* **Endpoint:** `/api/classroom/current-affairs/{ca_topic_id}/upload-pdf`
* **Multipart Form Parameter:**
  - `file`: The PDF file.

### 5. Generate Current Affairs Narration Transcript (Groq)
Generates a 1-minute news script from the uploaded facts in your target language.
* **HTTP Method:** `POST`
* **Endpoint:** `/api/classroom/current-affairs/{ca_topic_id}/generate-transcript`
* **JSON Request Body:**
  ```json
  {
    "language": "Hindi"
  }
  ```

### 6. Get Generated Reels for a Current Affairs Topic
Retrieve a list of all reels previously generated for a topic.
* **HTTP Method:** `GET`
* **Endpoint:** `/api/classroom/current-affairs/{ca_topic_id}/reels`
* **Response Example:**
  ```json
  {
    "success": true,
    "reels": [
      {
        "reel_id": "job-f1e2d3c4b5a6",
        "ca_topic_id": "ca-1a2b3c4d5e6f7g8h",
        "media_url": "https://pub-4766722e137c4258a9233495746c4f5a.r2.dev/reels/ca_ca-1a2b3c4d5e6f7g8h/reel_job-f1e2d3c4b5a6.mp4",
        "script": "Full voiceover script...",
        "created_at": "2026-06-06T15:50:00"
      }
    ]
  }
  ```

---

## 📝 PYQ (Previous Year Questions) Sets APIs

Organize exams by sets (e.g. "BPSC 70th Prelims"), upload question lists, verify parsed questions, and compile video reels.

### 1. List PYQ Sets
Retrieve all PYQ sets created under your client account.
* **HTTP Method:** `GET`
* **Endpoint:** `/api/classroom/pyq-sets`
* **Response Example:**
  ```json
  {
    "success": true,
    "pyq_sets": [
      {
        "pyq_set_id": "pyq-9e8d7c6b5a4",
        "name": "BPSC 70th Prelims Paper",
        "created_at": "2026-06-06T14:00:00"
      }
    ]
  }
  ```

### 2. Create PYQ Set (Add Set Name)
Create a new empty set to hold question lists.
* **HTTP Method:** `POST`
* **Endpoint:** `/api/classroom/pyq-sets`
* **JSON Request Body:**
  ```json
  {
    "name": "UPSC Prelims 2025 GS1"
  }
  ```

### 3. Upload Questions PDF/Excel for a PYQ Set
Upload a PDF or Excel spreadsheet containing exam questions. 
- **Excel Schema:** Expected columns are `Question | Option A | Option B | Option C | Option D | Correct Answer`
- **PDF Schema:** The backend automatically uses AI to extract and structure the questions into MCQ blocks.
* **HTTP Method:** `POST`
* **Endpoint:** `/api/classroom/pyq-sets/{pyq_set_id}/upload-pdf`
* **Multipart Form Parameter:**
  - `file`: The PDF or Excel file.

### 4. Retrieve Extracted Questions
Get the parsed list of questions inside the set.
* **HTTP Method:** `GET`
* **Endpoint:** `/api/classroom/pyq-sets/{pyq_set_id}/questions`
* **Response Example:**
  ```json
  {
    "success": true,
    "questions": [
      {
        "question_id": "q-12345",
        "question_text": "Who is the head of the Union Council of Ministers in India?",
        "options": ["President", "Prime Minister", "Speaker", "Chief Justice"],
        "correct_answer": "Prime Minister"
      }
    ]
  }
  ```

### 5. Generate PYQ Narrative Script (Groq)
Generates a detailed 3-minute narration tutorial script summarizing up to 15 questions from the set.
* **HTTP Method:** `POST`
* **Endpoint:** `/api/classroom/pyqs/{pyq_set_id}/generate-transcript`
* **JSON Request Body:**
  ```json
  {
    "language": "English"
  }
  ```

### 6. Get Generated Reels for a PYQ Set
Retrieve all extension reels previously generated for this PYQ set (stored in `SocialContent` table with `pyq_set_id` in metadata).
* **HTTP Method:** `GET`
* **Endpoint:** `/api/classroom/pyq-sets/{pyq_set_id}/reels`
* **Header Required:** `X-App-Token`
* **Response Example:**
  ```json
  {
    "success": true,
    "reels": [
      {
        "reel_id": "job-f1e2d3c4b5a6",
        "title": "BPSC 72 — PYQ Set",
        "media_url": "https://pub-xxxx.r2.dev/reels/pyq_pyq-9e8d7c6b5a4/reel_job-f1.mp4",
        "created_at": "2026-06-06T15:50:00"
      }
    ]
  }
  ```

> **UI Access:** In the dashboard, open any PYQ Set → click the **🎞️ Reels** button to see all generated reels for that set. Reels are also auto-shown after a new reel is generated via the 🎬 Extension Reel wizard.

### 7. Delete a PYQ Reel
Remove a specific PYQ reel from the database.
* **HTTP Method:** `DELETE`
* **Endpoint:** `/api/classroom/pyq-sets/reels/{reel_id}`
* **Header Required:** `X-App-Token`
* **Response Example:**
  ```json
  { "success": true, "message": "Reel deleted" }
  ```

> **Static File Access (Legacy):** Reel video files are also saved to local/R2 storage:
> * **Latest Reel:** `/uploads/reels/pyq_{pyq_set_id}/latest.mp4`
> * **Unique Reel:** `/uploads/reels/pyq_{pyq_set_id}/reel_{job_id}.mp4`

---

## ⚡ API Best Practices & Performance Tips
1. **Paging/Lazy Navigation:** For maximum responsiveness on the frontend, always load chapters only when a subject is clicked, and topics only when a chapter is expanded.
2. **Public CDN Streaming:** All video files returned from `/generate-reel` leverage Cloudflare's edge CDN, enabling global buffering-free playback on web interfaces.
3. **Public Reels Fetch:** Retrieve generated subtopic reels without authentication headers via:
   `GET /api/classroom/public/subtopics/{subtopic_id}/reels`

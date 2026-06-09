# VectorizeAI Classroom API — Developer Reference Documentation

Welcome to the **VectorizeAI Classroom API** reference manual. This document contains all the endpoints, request schemas, parameters, and response structures needed to seamlessly integrate VectorizeAI's structured hierarchical curriculum system and AI RAG generation engines into your own application.

---

## 🔑 1. Authentication & Base URL

All requests are made to your server's root address. For local development, the default base URL is:
`http://localhost:8000`

### Authentication Headers
For secure, authorized access, include the following headers in your HTTP request:
- **`X-App-Token`**: Your unique developer/client authentication token (obtainable from your Dashboard → API Settings).
- **`Content-Type`**: `application/json` (for all POST/PUT requests).

---

## 📐 2. The Curriculum Hierarchy

The Classroom data structure is organized as a strict parent-child relational tree:
$$\text{Exam} \longrightarrow \text{Paper} \longrightarrow \text{Subject} \longrightarrow \text{Chapter} \longrightarrow \text{Topic} \longrightarrow \text{Subtopic}$$

Each node is linked to its parent via a specific foreign key relationship (e.g. Chapters belong to a `subject_id`, Subtopics belong to a `topic_id`). All AI notes, quizzes, and reels are compiled and generated at the **Subtopic** level.

---

## 🏛️ 3. Curriculum Structure & CRUD Endpoints

Below is a reference of the full CRUD (Create, Read, Update, Delete) endpoints for building and managing the syllabus structure.

### 3.1. Exams
Exams represent the highest level of curriculum (e.g. "UPSC Civil Services", "CBSE Grade 10").

- **`GET /api/classroom/exams`**
  - **Description:** Retrieve a full nested list of all exams, papers, subjects, chapters, topics, and subtopics.
  - **Response:** `200 OK` (JSON Array)

- **`POST /api/classroom/exams`**
  - **Description:** Create a new exam category.
  - **Body (JSON):**
    ```json
    {
      "name": "Exam Title String",
      "description": "Optional description string"
    }
    ```

- **`GET /api/classroom/exams/{exam_id}`**
  - **Description:** Get nested syllabus branches starting from a specific Exam ID.

- **`PUT /api/classroom/exams/{exam_id}`**
  - **Description:** Update exam details.

- **`DELETE /api/classroom/exams/{exam_id}`**
  - **Description:** Delete the exam category and all cascade children.

---

### 3.2. Papers
Papers belong to Exams (e.g. "General Studies I", "Mathematics").

- **`POST /api/classroom/exams/{exam_id}/papers`**
  - **Description:** Create a syllabus paper.
  - **Body (JSON):**
    ```json
    {
      "name": "Paper Name String"
    }
    ```

- **`PUT /api/classroom/papers/{paper_id}`** | **`DELETE /api/classroom/papers/{paper_id}`**

- **`POST /api/classroom/papers/{paper_id}/auto-generate`**
  - **Description:** **[AI Engine]** Given a blank paper title, automatically brainstorms, indexes, and builds a comprehensive nested syllabus structure of chapters, topics, and subtopics.
  - **Response:**
    ```json
    {
      "success": true,
      "chapters_created": 8,
      "topics_created": 32
    }
    ```

---

### 3.3. Subjects
Subjects belong to Papers (e.g. "Indian Polity", "Calculus").

- **`POST /api/classroom/papers/{paper_id}/subjects`**
  - **Body:** `{ "name": "Subject Title" }`

- **`PUT /api/classroom/subjects/{subject_id}`** | **`DELETE /api/classroom/subjects/{subject_id}`**

---

### 3.4. Chapters
Chapters belong to Subjects (e.g. "Federal Structure", "Limits & Continuity").

- **`POST /api/classroom/subjects/{subject_id}/chapters`**
  - **Body:** `{ "name": "Chapter Title" }`

- **`PUT /api/classroom/chapters/{chapter_id}`** | **`DELETE /api/classroom/chapters/{chapter_id}`**

---

### 3.5. Topics
Topics belong to Chapters (e.g. "Center-State Relations", "L'Hopital's Rule").

- **`POST /api/classroom/chapters/{chapter_id}/topics`**
  - **Body:** `{ "name": "Topic Title" }`

- **`PUT /api/classroom/topics/{topic_id}`** | **`DELETE /api/classroom/topics/{topic_id}`**

---

### 3.6. Subtopics
Subtopics are the final leaf nodes of the syllabus (e.g. "Sarkaria Commission", "Limits of Trigonometric Functions").

- **`POST /api/classroom/topics/{topic_id}/subtopics`**
  - **Body:** `{ "name": "Subtopic Title" }`

- **`PUT /api/classroom/subtopics/{subtopic_id}`** | **`DELETE /api/classroom/subtopics/{subtopic_id}`**

---

### 3.7. Subject & Chapter Cover Image Generation

- **`POST /api/classroom/generate-image`**
  - **Description:** Automatically generates a premium textbook-style cover illustration locally on the server (using Pillow and Wikimedia Commons for topic matching) with title name overlays.
  - **Body (JSON):**
    ```json
    {
      "name": "Subject/Chapter Title String",
      "type": "subject", // "subject" or "chapter"
      "context": "Optional parent context string (e.g. paper or subject name)"
    }
    ```
  - **Response Example:**
    ```json
    {
      "success": true,
      "image_url": "/uploads/images/classroom_502e96e8d429ef45.jpg"
    }
    ```

---

## 🖼️ Accessing Subject & Chapter Images

All generated subject and chapter cover images are saved locally on the server under the `/uploads/images/` directory.

### Accessing Image Assets via HTTP
To render/display an image in your application, append the `image_url` path returned in the Subject or Chapter JSON payloads to your server's host domain.
- **Local Development Example:** If the server is running on `http://localhost:8000` and the API returns `"image_url": "/uploads/images/classroom_502e96e8d429ef45.jpg"`, you can access the file directly at:
  `http://localhost:8000/uploads/images/classroom_502e96e8d429ef45.jpg`

---

## 🧠 4. Educational AI & RAG Generation Endpoints

These endpoints perform contextual retrieval against vectorized knowledge stores (PDFs, URLs) and synthesize advanced study aids.

### 4.1. Generate AI Description
Generates a short, high-level summary overview of the subtopic concept.
- **Path:** `POST /api/classroom/subtopics/{subtopic_id}/generate-description`
- **Response:**
  ```json
  {
    "success": true,
    "description": "Short AI synthesized subtopic description string."
  }
  ```

---

### 4.2. Compile RAG Study Notes
Aggregates and formats extensive, beautifully detailed learning materials in markdown format, grounded in your uploaded vectors.
- **Path:** `POST /api/classroom/subtopics/{subtopic_id}/generate-notes`
- **Response:**
  ```json
  {
    "success": true,
    "notes": "# Indian Federalism\n- **Core Structure:** Federalism is a governance model...\n### Center-State Relations..."
  }
  ```

---

### 4.3. Download High-Quality Notes PDF
Compiles the study notes dynamically into a gorgeous, print-ready educational PDF complete with page numbers and headers.
- **Path:** `GET /api/classroom/subtopics/{subtopic_id}/download-notes-pdf`
- **Response:** A streaming HTTP attachment of the `.pdf` file.

---

### 4.4. Auto-Generate AI Custom Quiz
Creates a multiple-choice questions (MCQ) quiz mapped to different cognitive levels based on subtopic reference documents.
- **Path:** `POST /api/classroom/subtopics/{subtopic_id}/quiz/generate`
- **Response:**
  ```json
  {
    "success": true,
    "quiz": [
      {
        "question": "What is the primary recommendation of the Sarkaria Commission?",
        "options": [
          "Establishment of an Inter-State Council",
          "Abolition of the office of Governor",
          "Complete devolution of taxation powers",
          "Reorganization of judicial territories"
        ],
        "answer": "Establishment of an Inter-State Council",
        "explanation": "The Sarkaria Commission strongly advocated setting up a permanent Inter-State Council under Article 263 of the Constitution to streamline Center-State discussions."
      }
    ]
  }
  ```

---

### 4.5. Generate Lecture Transcript
Brainstorms a structured spoken narrative lecture script to prep voiceover audio generation.
- **Path:** `POST /api/classroom/subtopics/{subtopic_id}/generate-transcript`
- **Response:**
  ```json
  {
    "success": true,
    "transcript": "Hello and welcome. Today we are diving deep into Article 263..."
  }
  ```

---

### 4.6. Trigger Automated Classroom Reel Generation
Sends a background job to generate an educational 9:16 video reel. It maps 12 photorealistic visual scenes, generates scene voiceovers via ElevenLabs V2, synchronizes subtitles, downloads and trims page-context videos, and mixed them into a finished output video.
- **Path:** `POST /api/classroom/subtopics/{subtopic_id}/generate-reel`
- **Body (JSON):**
  ```json
  {
    "subtopic_id": "subtopic-...",
    "language": "English", 
    "voice_id": "C2S5J6WvmHnrQWjUu6Rg",
    "transcript": "Optional custom transcript to drive the scripting..."
  }
  ```
- **Response:**
  ```json
  {
    "success": true,
    "job_id": "job-e2bd3664bcc8907d",
    "scene_count": 12,
    "scenes": [
      {
        "scene_num": 1,
        "dialogue": "Scene narration dialogue script...",
        "image_prompt": "Cinematic visual description for AI graphics...",
        "animation_prompt": "Camera movement instructions..."
      }
    ]
  }
  ```

---

### 4.7. Get Extension Reel Generation Status
Polls the active extension reel job status. Returns live assembling steps so your web frontend can display detailed progress.
- **Path:** `GET /api/extension/job/{job_id}/status`
- **Response:**
  ```json
  {
    "success": true,
    "status": "assembling", // "waiting_extension" | "generating_videos" | "assembling" | "done" | "error"
    "images_done": 12,
    "videos_done": 12,
    "total_scenes": 12,
    "video_url": null, // Available once status is "done"
    "progress_msg": "Generating premium AI narration voiceover for Scene 3 of 12..."
  }
  ```

---

## 💻 5. Integration Code Snippets (Copy & Paste Ready)

### 5.1. JavaScript (Fetch) Example
```javascript
const BASE_URL = 'http://localhost:8000';
const APP_TOKEN = 'your_client_token_here';
const SUBTOPIC_ID = 'subtopic-8d96323ac5';

// 1. Generate Grounded Study Notes
async function fetchNotes() {
    try {
        const response = await fetch(`${BASE_URL}/api/classroom/subtopics/${SUBTOPIC_ID}/generate-notes`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-App-Token': APP_TOKEN
            }
        });
        const data = await response.json();
        if (data.success) {
            console.log("Markdown notes:\n", data.notes);
        } else {
            console.error("Notes generation failed");
        }
    } catch (err) {
        console.error("Network error:", err);
    }
}

// 2. Trigger AI Video Reel Generation
async function triggerReel() {
    try {
        const response = await fetch(`${BASE_URL}/api/classroom/subtopics/${SUBTOPIC_ID}/generate-reel`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-App-Token': APP_TOKEN
            },
            body: JSON.stringify({
                language: "Hindi",
                voice_id: "C2S5J6WvmHnrQWjUu6Rg" // ElevenLabs Voice ID
            })
        });
        const data = await response.json();
        console.log("Job created successfully, Job ID:", data.job_id);
    } catch (err) {
        console.error("Failed to trigger video generation:", err);
    }
}
```

### 5.2. Python (Requests) Example
```python
import requests
import json

BASE_URL = "http://localhost:8000"
APP_TOKEN = "your_client_token_here"
SUBTOPIC_ID = "subtopic-8d96323ac5"

headers = {
    "X-App-Token": APP_TOKEN,
    "Content-Type": "application/json"
}

# 1. Fetch AI Quiz
def get_custom_quiz():
    url = f"{BASE_URL}/api/classroom/subtopics/{SUBTOPIC_ID}/quiz/generate"
    try:
        response = requests.post(url, headers=headers)
        if response.status_code == 200:
            quiz_data = response.json()
            print("AI Grounded Quiz:", json.dumps(quiz_data["quiz"], indent=2))
        else:
            print("Failed to fetch quiz:", response.text)
    except Exception as e:
        print("Error connecting to server:", e)

# 2. Download Curriculum Syllabus
def fetch_complete_syllabus():
    url = f"{BASE_URL}/api/classroom/exams"
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            syllabus = response.json()
            print("Successfully loaded", len(syllabus), "exams from syllabus database.")
        else:
            print("Failed to get syllabus:", response.text)
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    get_custom_quiz()
```

---
*VectorizeAI Curriculum & AI Media Infrastructure Reference Manual · Year 2026*

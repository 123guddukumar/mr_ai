# 📚 MR AI Classroom — Full API Reference

> **Base URL:** `https://your-domain.com/api`  
> **Authentication:** All endpoints require `X-App-Token` header  
> **Content-Type:** `application/json` (unless specified otherwise)

---

## 🔐 Authentication

Every request must include:
```
X-App-Token: YOUR_TOKEN_HERE
```

Get your token from the admin dashboard → Settings → API Token.

---

## 📋 Table of Contents

1. [Exams](#1-exams)
2. [Papers](#2-papers)
3. [Subjects](#3-subjects)
4. [Chapters](#4-chapters)
5. [Topics](#5-topics)
6. [Subtopics](#6-subtopics)
7. [AI — Descriptions & Notes](#7-ai--descriptions--notes)
8. [AI — Quiz Generation](#8-ai--quiz-generation)
9. [AI — Transcripts](#9-ai--transcripts)
10. [Current Affairs](#10-current-affairs)
11. [PYQ Sets (Previous Year Questions)](#11-pyq-sets)
12. [Classroom Chatbot](#12-classroom-chatbot)
13. [Text-to-Speech (TTS)](#13-text-to-speech-tts)
14. [Auto-Generate Structure](#14-auto-generate-structure)
15. [Upload Index / Vectorize](#15-upload-index--vectorize)
16. [Study History](#16-study-history)
17. [Public APIs (No Auth)](#17-public-apis-no-auth)

---

## 1. Exams

### 1.1 List All Exams
```
GET /api/classroom/exams
```
**Response:**
```json
{
  "success": true,
  "exams": [
    {
      "exam_id": "exam-abc123",
      "name": "BPSC Prelims",
      "category": "State PSC",
      "description": "Bihar Public Service Commission",
      "image_url": "https://...",
      "created_at": "2024-01-01T00:00:00"
    }
  ]
}
```

---

### 1.2 Get Full Exam Tree (with all papers → subjects → chapters → topics → subtopics)
```
GET /api/classroom/exams/{exam_id}
```
**Response:**
```json
{
  "success": true,
  "exam": {
    "exam_id": "exam-abc123",
    "name": "BPSC Prelims",
    "papers": [
      {
        "paper_id": "paper-xyz",
        "name": "General Studies Paper 1",
        "subjects": [
          {
            "subject_id": "subject-001",
            "name": "History",
            "image_url": "https://...",
            "chapters": [
              {
                "chapter_id": "chapter-001",
                "name": "Ancient India",
                "topics": [
                  {
                    "topic_id": "topic-001",
                    "name": "Indus Valley Civilization",
                    "subtopics": []
                  }
                ]
              }
            ]
          }
        ]
      }
    ]
  }
}
```

---

### 1.3 Create Exam
```
POST /api/classroom/exams
```
**Body:**
```json
{
  "name": "BPSC Prelims",
  "category": "State PSC",
  "description": "Bihar Public Service Commission Exam",
  "image_url": "https://example.com/image.jpg"
}
```
**Response:**
```json
{
  "success": true,
  "exam": { "exam_id": "exam-abc123", "name": "BPSC Prelims", ... }
}
```

---

### 1.4 Update Exam
```
PUT /api/classroom/exams/{exam_id}
```
**Body:** (same as Create Exam)
```json
{
  "name": "BPSC Prelims 2025",
  "category": "State PSC",
  "description": "Updated description",
  "image_url": "https://..."
}
```

---

### 1.5 Delete Exam
```
DELETE /api/classroom/exams/{exam_id}
```
**Response:**
```json
{ "success": true, "message": "Exam deleted" }
```

---

### 1.6 Get Exam Papers List
```
GET /api/classroom/exams/{exam_id}/papers
```
**Response:**
```json
{
  "success": true,
  "papers": [
    { "paper_id": "paper-xyz", "name": "General Studies Paper 1" }
  ]
}
```

---

### 1.7 Study History for Exam
```
GET /api/classroom/exams/{exam_id}/history
```
Returns all study sessions and progress for this exam.

---

## 2. Papers

### 2.1 Create Paper under Exam
```
POST /api/classroom/exams/{exam_id}/papers
```
**Body:**
```json
{
  "name": "General Studies Paper 1"
}
```
**Response:**
```json
{
  "success": true,
  "paper": { "paper_id": "paper-xyz", "name": "General Studies Paper 1" }
}
```

---

### 2.2 Update Paper
```
PUT /api/classroom/papers/{paper_id}
```
**Body:**
```json
{ "name": "General Studies Paper 2" }
```

---

### 2.3 Delete Paper
```
DELETE /api/classroom/papers/{paper_id}
```
**Response:**
```json
{ "success": true, "message": "Paper deleted" }
```

---

### 2.4 Get Subjects under Paper
```
GET /api/classroom/papers/{paper_id}/subjects
```
**Response:**
```json
{
  "success": true,
  "subjects": [
    { "subject_id": "subject-001", "name": "History", "image_url": "https://..." }
  ]
}
```

---

### 2.5 Auto-Generate Full Structure
```
POST /api/classroom/papers/{paper_id}/auto-generate
```
Uses AI to auto-generate subjects, chapters, topics, and subtopics based on the paper/exam context.

**Body:**
```json
{
  "exam_name": "BPSC Prelims",
  "paper_name": "General Studies"
}
```

---

### 2.6 Vectorize Paper for Chatbot
```
POST /api/classroom/papers/{paper_id}/vectorize
```
Indexes all content under this paper for AI chatbot Q&A.

**Response:**
```json
{ "success": true, "message": "Paper vectorized successfully" }
```

---

### 2.7 Chat with Paper (AI Chatbot)
```
POST /api/classroom/papers/{paper_id}/chat
```
**Body:**
```json
{
  "message": "What are the main topics in Ancient India?",
  "session_id": "optional-session-id"
}
```
**Response:**
```json
{
  "success": true,
  "reply": "Ancient India covers...",
  "session_id": "abc123"
}
```

---

### 2.8 Get Chat History
```
GET /api/classroom/papers/{paper_id}/chat/history
```

---

### 2.9 Clear Chat History
```
DELETE /api/classroom/papers/{paper_id}/chat/history
```

---

## 3. Subjects

### 3.1 Create Subject under Paper
```
POST /api/classroom/papers/{paper_id}/subjects
```
**Body:**
```json
{
  "name": "History",
  "image_url": "https://example.com/history.jpg"
}
```
**Response:**
```json
{
  "success": true,
  "subject": {
    "subject_id": "subject-001",
    "name": "History",
    "image_url": "https://..."
  }
}
```

---

### 3.2 Update Subject
```
PUT /api/classroom/subjects/{subject_id}
```
**Body:**
```json
{
  "name": "History & Culture",
  "image_url": "https://new-image.jpg",
  "color": "#FF6B35"
}
```

---

### 3.3 Delete Subject
```
DELETE /api/classroom/subjects/{subject_id}
```
**Response:**
```json
{ "success": true, "message": "Subject deleted" }
```

---

### 3.4 Get Chapters under Subject
```
GET /api/classroom/subjects/{subject_id}/chapters
```
**Response:**
```json
{
  "success": true,
  "chapters": [
    { "chapter_id": "chapter-001", "name": "Ancient India", "image_url": "..." }
  ]
}
```

---

### 3.5 Upload Subject Index (PDF/Text for AI)
```
POST /api/classroom/subjects/{subject_id}/upload-index
```
**Content-Type:** `multipart/form-data`  
**Body:** `file` (PDF or text file with syllabus/index)

AI will parse this and auto-create chapters, topics, and subtopics.

---

## 4. Chapters

### 4.1 Create Chapter under Subject
```
POST /api/classroom/subjects/{subject_id}/chapters
```
**Body:**
```json
{
  "name": "Ancient India",
  "image_url": "https://example.com/ancient.jpg"
}
```
**Response:**
```json
{
  "success": true,
  "chapter": {
    "chapter_id": "chapter-001",
    "name": "Ancient India",
    "image_url": "https://..."
  }
}
```

---

### 4.2 Update Chapter
```
PUT /api/classroom/chapters/{chapter_id}
```
**Body:**
```json
{
  "name": "Ancient India & Civilization",
  "image_url": "https://new.jpg"
}
```

---

### 4.3 Delete Chapter
```
DELETE /api/classroom/chapters/{chapter_id}
```

---

### 4.4 Get Topics under Chapter
```
GET /api/classroom/chapters/{chapter_id}/topics
```
**Response:**
```json
{
  "success": true,
  "topics": [
    {
      "topic_id": "topic-001",
      "name": "Indus Valley Civilization",
      "image_url": "...",
      "banner_url": "...",
      "subtopics": []
    }
  ]
}
```

---

## 5. Topics

### 5.1 Create Topic under Chapter
```
POST /api/classroom/chapters/{chapter_id}/topics
```
**Body:**
```json
{
  "name": "Indus Valley Civilization",
  "image_url": "https://example.com/indus.jpg"
}
```
**Response:**
```json
{
  "success": true,
  "topic": {
    "topic_id": "topic-001",
    "name": "Indus Valley Civilization",
    "image_url": "...",
    "banner_url": null
  }
}
```

---

### 5.2 Update Topic
```
PUT /api/classroom/topics/{topic_id}
```
**Body:**
```json
{
  "name": "Indus Valley & Harappan Civilization",
  "image_url": "https://new.jpg"
}
```

---

### 5.3 Delete Topic
```
DELETE /api/classroom/topics/{topic_id}
```

---

### 5.4 Get Subtopics under Topic
```
GET /api/classroom/topics/{topic_id}/subtopics
```
**Response:**
```json
{
  "success": true,
  "subtopics": [
    {
      "subtopic_id": "sub-001",
      "name": "Town Planning of Harappa",
      "description": "...",
      "image_url": "...",
      "banner_url": "...",
      "notes": "...",
      "transcript": "..."
    }
  ]
}
```

---

### 5.5 AI: Generate Topic Description
```
POST /api/classroom/topics/{topic_id}/generate-description
```
**Body:** *(optional)*
```json
{ "force": true }
```
**Response:**
```json
{
  "success": true,
  "description": "Indus Valley Civilization was one of the world's earliest urban civilizations..."
}
```

---

### 5.6 AI: Generate Topic Notes
```
POST /api/classroom/topics/{topic_id}/generate-notes
```
**Body:** *(optional)*
```json
{ "force": true }
```
**Response:**
```json
{
  "success": true,
  "notes": "# Indus Valley Civilization\n\n## Key Points\n- Started around 3300 BCE..."
}
```

---

### 5.7 Download Topic Notes as PDF
```
GET /api/classroom/topics/{topic_id}/download-notes-pdf
```
**Response:** PDF file download (binary)

---

### 5.8 AI: Generate Topic Quiz
```
POST /api/classroom/topics/{topic_id}/quiz/generate
```
**Body:**
```json
{
  "num_questions": 10,
  "difficulty": "medium"
}
```
**Response:**
```json
{
  "success": true,
  "questions": [
    {
      "question": "Which city was the largest in Indus Valley Civilization?",
      "options": ["Harappa", "Mohenjo-daro", "Lothal", "Dholavira"],
      "correct": "Mohenjo-daro",
      "explanation": "..."
    }
  ]
}
```

---

### 5.9 AI: Generate Topic Transcript (for video/audio)
```
POST /api/classroom/topics/{topic_id}/generate-transcript
```
**Body:**
```json
{ "style": "educational", "language": "hi" }
```
**Response:**
```json
{
  "success": true,
  "transcript": "Aaj hum Indus Valley Civilization ke baare mein jaanenge..."
}
```

---

## 6. Subtopics

### 6.1 Create Subtopic under Topic
```
POST /api/classroom/topics/{topic_id}/subtopics
```
**Body:**
```json
{
  "name": "Town Planning of Harappa",
  "description": "Urban planning features of Harappan cities",
  "image_url": "https://example.com/harappa.jpg",
  "banner_url": "https://example.com/harappa-banner.jpg"
}
```
**Response:**
```json
{
  "success": true,
  "subtopic": {
    "subtopic_id": "sub-001",
    "name": "Town Planning of Harappa",
    "description": "...",
    "image_url": "...",
    "banner_url": "..."
  }
}
```

---

### 6.2 Get Subtopic Details
```
GET /api/classroom/subtopics/{subtopic_id}
```
**Response:**
```json
{
  "success": true,
  "subtopic": {
    "subtopic_id": "sub-001",
    "name": "Town Planning of Harappa",
    "description": "...",
    "image_url": "...",
    "banner_url": "...",
    "notes": "...",
    "transcript": "...",
    "created_at": "2024-01-01T00:00:00"
  }
}
```

---

### 6.3 Update Subtopic
```
PUT /api/classroom/subtopics/{subtopic_id}
```
**Body:**
```json
{
  "name": "Town Planning & Architecture of Harappa",
  "description": "Updated description",
  "image_url": "https://new.jpg",
  "banner_url": "https://new-banner.jpg",
  "notes": "Custom notes text..."
}
```

---

### 6.4 Delete Subtopic
```
DELETE /api/classroom/subtopics/{subtopic_id}
```

---

### 6.5 AI: Generate Subtopic Description
```
POST /api/classroom/subtopics/{subtopic_id}/generate-description
```
**Body:** *(optional)*
```json
{ "force": true }
```
**Response:**
```json
{
  "success": true,
  "description": "Town planning of Harappa featured grid-based layout..."
}
```

---

### 6.6 AI: Generate Subtopic Notes
```
POST /api/classroom/subtopics/{subtopic_id}/generate-notes
```
**Body:** *(optional)*
```json
{ "force": true }
```
**Response:**
```json
{
  "success": true,
  "notes": "# Town Planning of Harappa\n\n## Grid System\n- Streets at right angles..."
}
```

---

### 6.7 Download Subtopic Notes as PDF
```
GET /api/classroom/subtopics/{subtopic_id}/download-notes-pdf
```
**Response:** PDF file download (binary)

---

### 6.8 AI: Generate Subtopic Quiz
```
POST /api/classroom/subtopics/{subtopic_id}/quiz/generate
```
**Body:**
```json
{
  "num_questions": 5,
  "difficulty": "hard"
}
```
**Response:**
```json
{
  "success": true,
  "questions": [
    {
      "question": "What is the significance of the Great Bath at Mohenjo-daro?",
      "options": ["Drinking water", "Religious bathing", "Fire worship", "Trade"],
      "correct": "Religious bathing",
      "explanation": "The Great Bath was likely used for ritual purification..."
    }
  ]
}
```

---

### 6.9 AI: Generate Subtopic Transcript
```
POST /api/classroom/subtopics/{subtopic_id}/generate-transcript
```
**Body:**
```json
{ "style": "educational", "language": "hi" }
```

---

### 6.10 Get Subtopic Reels
```
GET /api/classroom/subtopics/{subtopic_id}/reels
```
**Response:**
```json
{
  "success": true,
  "reels": [
    {
      "reel_id": "reel-001",
      "video_url": "https://r2.dev/reel.mp4",
      "thumbnail_url": "...",
      "created_at": "2024-01-01T00:00:00"
    }
  ]
}
```

---

### 6.11 Get Topic Reels
```
GET /api/classroom/topics/{topic_id}/reels
```
Same response format as subtopic reels.

---

## 7. AI — Descriptions & Notes

> Quick reference for all AI generation endpoints

| Action | Method | Endpoint |
|--------|--------|----------|
| Generate Topic Description | POST | `/api/classroom/topics/{id}/generate-description` |
| Generate Topic Notes | POST | `/api/classroom/topics/{id}/generate-notes` |
| Download Topic Notes PDF | GET | `/api/classroom/topics/{id}/download-notes-pdf` |
| Generate Subtopic Description | POST | `/api/classroom/subtopics/{id}/generate-description` |
| Generate Subtopic Notes | POST | `/api/classroom/subtopics/{id}/generate-notes` |
| Download Subtopic Notes PDF | GET | `/api/classroom/subtopics/{id}/download-notes-pdf` |

**Request Body (all generate endpoints):**
```json
{ "force": false }
```
- `force: false` → Returns cached if exists
- `force: true` → Always regenerates fresh

---

## 8. AI — Quiz Generation

### Generate Quiz for Topic
```
POST /api/classroom/topics/{topic_id}/quiz/generate
```

### Generate Quiz for Subtopic
```
POST /api/classroom/subtopics/{subtopic_id}/quiz/generate
```

**Request Body:**
```json
{
  "num_questions": 10,
  "difficulty": "medium"
}
```

| Field | Type | Options |
|-------|------|---------|
| `num_questions` | int | 5, 10, 15, 20 |
| `difficulty` | string | `"easy"`, `"medium"`, `"hard"` |

**Response:**
```json
{
  "success": true,
  "questions": [
    {
      "question": "Question text here?",
      "options": ["A", "B", "C", "D"],
      "correct": "A",
      "explanation": "Because..."
    }
  ]
}
```

---

## 9. AI — Transcripts

### Generate Transcript for Subtopic
```
POST /api/classroom/subtopics/{subtopic_id}/generate-transcript
```

### Generate Transcript for Topic
```
POST /api/classroom/topics/{topic_id}/generate-transcript
```

### Generate Transcript for PYQ Set
```
POST /api/classroom/pyqs/{pyq_set_id}/generate-transcript
```

### Generate Transcript for Current Affairs
```
POST /api/classroom/current-affairs/{ca_topic_id}/generate-transcript
```

**Request Body:**
```json
{
  "style": "educational",
  "language": "hi"
}
```

| Field | Values |
|-------|--------|
| `style` | `"educational"`, `"story"`, `"news"` |
| `language` | `"hi"` (Hindi), `"en"` (English) |

---

## 10. Current Affairs

### 10.1 List Current Affairs
```
GET /api/classroom/current-affairs
```
**Response:**
```json
{
  "success": true,
  "topics": [
    {
      "ca_topic_id": "ca-001",
      "title": "India-China Border Update",
      "category": "International",
      "created_at": "2024-06-10T00:00:00"
    }
  ]
}
```

---

### 10.2 Create Current Affairs Topic
```
POST /api/classroom/current-affairs
```
**Body:**
```json
{
  "title": "India-China Border Update",
  "category": "International",
  "content": "Optional initial content..."
}
```

---

### 10.3 Update Current Affairs
```
PUT /api/classroom/current-affairs/{ca_topic_id}
```
**Body:**
```json
{
  "title": "Updated Title",
  "category": "Domestic",
  "content": "Updated content..."
}
```

---

### 10.4 Delete Current Affairs Topic
```
DELETE /api/classroom/current-affairs/{ca_topic_id}
```

---

### 10.5 Upload PDF for Current Affairs
```
POST /api/classroom/current-affairs/{ca_topic_id}/upload-pdf
```
**Content-Type:** `multipart/form-data`  
**Body:** `file` (PDF)

AI will extract content and generate notes automatically.

---

### 10.6 Get Current Affairs Reels
```
GET /api/classroom/current-affairs/{ca_topic_id}/reels
```

---

### 10.7 Delete Current Affairs Reel
```
DELETE /api/classroom/current-affairs/reels/{reel_id}
```

---

## 11. PYQ Sets

### 11.1 List All PYQ Sets
```
GET /api/classroom/pyq-sets
```
**Response:**
```json
{
  "success": true,
  "pyq_sets": [
    {
      "pyq_set_id": "pyq-001",
      "name": "BPSC 2023 Prelims",
      "year": 2023,
      "question_count": 150
    }
  ]
}
```

---

### 11.2 Create PYQ Set
```
POST /api/classroom/pyq-sets
```
**Body:**
```json
{
  "name": "BPSC 2023 Prelims",
  "year": 2023,
  "description": "Bihar PSC 2023 Preliminary Exam"
}
```

---

### 11.3 Update PYQ Set
```
PUT /api/classroom/pyq-sets/{pyq_set_id}
```
**Body:**
```json
{
  "name": "BPSC 2023 Prelims - Updated",
  "year": 2023,
  "description": "Updated description"
}
```

---

### 11.4 Delete PYQ Set
```
DELETE /api/classroom/pyq-sets/{pyq_set_id}
```

---

### 11.5 Reset PYQ Set
```
POST /api/classroom/pyq-sets/{pyq_set_id}/reset
```
Clears all extracted questions and resets to empty.

---

### 11.6 Upload PDF for PYQ Set
```
POST /api/classroom/pyq-sets/{pyq_set_id}/upload-pdf
```
**Content-Type:** `multipart/form-data`  
**Body:** `file` (PDF of previous year questions)

AI will extract all Q&A pairs automatically.

---

### 11.7 Get Questions from PYQ Set
```
GET /api/classroom/pyq-sets/{pyq_set_id}/questions
```
**Response:**
```json
{
  "success": true,
  "questions": [
    {
      "question_id": "q-001",
      "question": "Who was the first President of India?",
      "options": ["Gandhi", "Nehru", "Prasad", "Patel"],
      "correct": "Prasad",
      "explanation": "Dr. Rajendra Prasad was...",
      "year": 2023
    }
  ]
}
```

---

### 11.8 Delete a Question
```
DELETE /api/classroom/pyq-sets/questions/{question_id}
```

---

### 11.9 Generate PYQ Overview (AI Analysis)
```
POST /api/classroom/pyq-sets/{pyq_set_id}/generate-overview
```
AI generates a topic-wise analysis of the PYQ paper.

---

### 11.10 Get PYQ Reels
```
GET /api/classroom/pyq-sets/{pyq_set_id}/reels
```

---

### 11.11 Delete PYQ Reel
```
DELETE /api/classroom/pyq-sets/reels/{reel_id}
```

---

### 11.12 Vectorize PYQ Set for Chatbot
```
POST /api/classroom/pyq-sets/{pyq_set_id}/vectorize
```

---

### 11.13 Chat with PYQ Set
```
POST /api/classroom/pyq-sets/{pyq_set_id}/chat
```
**Body:**
```json
{
  "message": "Which topics appear most frequently in BPSC?",
  "session_id": "optional"
}
```

---

### 11.14 Get PYQ Chat History
```
GET /api/classroom/pyq-sets/{pyq_set_id}/chat/history
```

---

### 11.15 Clear PYQ Chat History
```
DELETE /api/classroom/pyq-sets/{pyq_set_id}/chat/history
```

---

## 12. Classroom Chatbot

### Chat with Paper Content
```
POST /api/classroom/papers/{paper_id}/chat
```

### Chat with PYQ Content
```
POST /api/classroom/pyq-sets/{pyq_set_id}/chat
```

**Request Body (both):**
```json
{
  "message": "Your question here",
  "session_id": "optional-uuid"
}
```

**Response:**
```json
{
  "success": true,
  "reply": "AI answer here...",
  "sources": ["Ancient India Chapter 1", "Indus Valley Subtopic"],
  "session_id": "abc123"
}
```

**Tips:**
- Use same `session_id` to maintain conversation context
- Leave `session_id` blank to start a fresh conversation
- Vectorize content first before chatting (see 2.6 and 11.12)

---

## 13. Text-to-Speech (TTS)

### 13.1 Generate TTS Audio (POST)
```
POST /api/classroom/tts/speak
```
**Body:**
```json
{
  "text": "Indus Valley Civilization was one of the world's oldest...",
  "voice": "hi-IN-SwaraNeural",
  "speed": 1.0
}
```
**Response:**
```json
{
  "success": true,
  "audio_url": "/uploads/tts/abc123.mp3"
}
```

---

### 13.2 Stream TTS Audio (GET)
```
GET /api/classroom/tts/speak?text=Your+text+here&voice=hi-IN-SwaraNeural
```
**Response:** Audio stream (direct playback)

---

**Available Voices:**

| Voice ID | Language | Gender |
|----------|----------|--------|
| `hi-IN-SwaraNeural` | Hindi | Female |
| `hi-IN-MadhurNeural` | Hindi | Male |
| `en-IN-NeerjaNeural` | English (India) | Female |
| `en-IN-PrabhatNeural` | English (India) | Male |

---

## 14. Auto-Generate Structure

### Auto-Generate Subjects, Chapters, Topics
```
POST /api/classroom/papers/{paper_id}/auto-generate
```
AI will automatically create the full hierarchy of subjects → chapters → topics → subtopics based on the exam/paper context.

**Body:**
```json
{
  "exam_name": "BPSC Prelims",
  "paper_name": "General Studies Paper 1",
  "instructions": "Include all standard topics for State PSC"
}
```

**Response:**
```json
{
  "success": true,
  "message": "Auto-generation started",
  "created": {
    "subjects": 5,
    "chapters": 24,
    "topics": 120,
    "subtopics": 480
  }
}
```

---

## 15. Upload Index / Vectorize

### Upload Syllabus Index to Subject
```
POST /api/classroom/subjects/{subject_id}/upload-index
```
**Content-Type:** `multipart/form-data`  
**Body:** `file` (PDF or TXT with syllabus/chapter list)

---

### Vectorize Paper for Chatbot
```
POST /api/classroom/papers/{paper_id}/vectorize
```

### Vectorize PYQ Set for Chatbot
```
POST /api/classroom/pyq-sets/{pyq_set_id}/vectorize
```

---

## 16. Study History

### Get Study History for Exam
```
GET /api/classroom/exams/{exam_id}/history
```
**Response:**
```json
{
  "success": true,
  "history": [
    {
      "date": "2024-06-10",
      "subtopic_id": "sub-001",
      "subtopic_name": "Town Planning of Harappa",
      "time_spent_minutes": 15,
      "quiz_score": 80
    }
  ]
}
```

---

## 17. Public APIs (No Auth)

### Get Public Subtopic Reels
```
GET /api/classroom/public/subtopics/{subtopic_id}/reels
```
No authentication required. Returns published reels for a subtopic.

---

## 🚀 Step-by-Step Developer Guide

### Setting Up a Complete Exam

```
Step 1: Create Exam
  POST /api/classroom/exams
  → Get exam_id

Step 2: Create Paper under Exam
  POST /api/classroom/exams/{exam_id}/papers
  → Get paper_id

Step 3: Create Subjects under Paper
  POST /api/classroom/papers/{paper_id}/subjects
  → Get subject_id (repeat for each subject)

Step 4: Create Chapters under Subject
  POST /api/classroom/subjects/{subject_id}/chapters
  → Get chapter_id

Step 5: Create Topics under Chapter
  POST /api/classroom/chapters/{chapter_id}/topics
  → Get topic_id

Step 6: Create Subtopics under Topic
  POST /api/classroom/topics/{topic_id}/subtopics
  → Get subtopic_id

Step 7: Generate AI Content
  POST /api/classroom/subtopics/{subtopic_id}/generate-description
  POST /api/classroom/subtopics/{subtopic_id}/generate-notes
  POST /api/classroom/subtopics/{subtopic_id}/quiz/generate
```

---

### Setting Up Chatbot

```
Step 1: Vectorize the paper
  POST /api/classroom/papers/{paper_id}/vectorize

Step 2: Start chatting
  POST /api/classroom/papers/{paper_id}/chat
  Body: { "message": "What is Harappan Civilization?" }

Step 3: Continue conversation (use same session_id)
  POST /api/classroom/papers/{paper_id}/chat
  Body: { "message": "Tell me more about town planning", "session_id": "abc123" }
```

---

### Setting Up PYQ Practice

```
Step 1: Create PYQ Set
  POST /api/classroom/pyq-sets
  Body: { "name": "BPSC 2023", "year": 2023 }

Step 2: Upload previous year paper (PDF)
  POST /api/classroom/pyq-sets/{pyq_set_id}/upload-pdf
  Content-Type: multipart/form-data
  Body: file = <your PDF>

Step 3: Get extracted questions
  GET /api/classroom/pyq-sets/{pyq_set_id}/questions

Step 4: Generate analysis
  POST /api/classroom/pyq-sets/{pyq_set_id}/generate-overview

Step 5: Chat with PYQ content
  POST /api/classroom/pyq-sets/{pyq_set_id}/vectorize  (first time only)
  POST /api/classroom/pyq-sets/{pyq_set_id}/chat
```

---

## ⚠️ Error Responses

All errors follow this format:
```json
{
  "detail": "Error message here"
}
```

| Status Code | Meaning |
|-------------|---------|
| `200` | Success |
| `400` | Bad Request (invalid input) |
| `401` | Unauthorized (invalid/missing token) |
| `403` | Forbidden (no access to this resource) |
| `404` | Not Found |
| `422` | Validation Error (check request body) |
| `500` | Server Error |

---

## 📝 Notes for Deployment

1. **AWS Deployment:** Before pushing to production, ensure `.env` has:
   - `DATABASE_URL` pointing to production DB
   - `R2_*` keys for Cloudflare R2 storage
   - `GROQ_API_KEY` or `OPENAI_API_KEY` for AI features
   - `JWT_SECRET` set for token security

2. **R2 Storage:** All images and generated content are stored in Cloudflare R2. Ensure R2 bucket CORS is configured for your domain.

3. **File Uploads:** Uploaded PDFs are stored temporarily in `uploads/` directory. Ensure this directory has write permissions on the server.

4. **Rate Limiting:** AI generation endpoints (notes, description, quiz) may have per-client rate limits.

---

*API Version: 2.0 | Last Updated: June 2025*

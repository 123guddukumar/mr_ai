"""
MR AI RAG - Classroom & Exam Management Routes
"""

import secrets
import logging
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Header, BackgroundTasks, Form
import asyncio
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, selectinload

from app.core.database import get_db
from app.core.clients import validate_client_token
from app.core.models import Exam, PaperClassroom, Subject, ChapterClassroom, TopicClassroom, SubtopicClassroom

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Auth dependency ───────────────────────────────────────────────────────────

def _require_client(
    x_app_token: Optional[str] = Header(None, alias="X-App-Token"),
    db: Session = Depends(get_db),
) -> dict:
    if not x_app_token:
        raise HTTPException(401, "Missing X-App-Token header.")
    record = validate_client_token(x_app_token, db=db)
    if not record:
        raise HTTPException(401, "Invalid or expired token. Please login again.")
    return record


# ── Pydantic Request Models ───────────────────────────────────────────────────

class CreateExamReq(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    image_url: Optional[str] = ""
    description: Optional[str] = ""
    category: Optional[str] = ""

class CreatePaperReq(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)

class CreateSubjectReq(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    color: Optional[str] = "#4f46e5"

class CreateChapterReq(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)

class CreateTopicReq(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)

class CreateSubtopicReq(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = ""


# ── Exam Endpoints ────────────────────────────────────────────────────────────

@router.get("/classroom/exams", tags=["Classroom"])
async def list_exams(client: dict = Depends(_require_client), db: Session = Depends(get_db)):
    exams = db.query(Exam).filter(Exam.client_id == client["client_id"]).order_by(Exam.created_at.desc()).all()
    return {"success": True, "exams": [e.to_dict() for e in exams]}


@router.post("/classroom/exams", tags=["Classroom"])
async def create_exam(req: CreateExamReq, client: dict = Depends(_require_client), db: Session = Depends(get_db)):
    exam_id = "exam-" + secrets.token_hex(8)
    exam = Exam(
        exam_id=exam_id,
        client_id=client["client_id"],
        name=req.name,
        category=req.category,
        image_url=req.image_url,
        description=req.description,
        created_at=datetime.utcnow()
    )
    db.add(exam)
    db.commit()
    db.refresh(exam)
    return {"success": True, "exam": exam.to_dict()}


@router.put("/classroom/exams/{exam_id}", tags=["Classroom"])
async def update_exam(exam_id: str, req: CreateExamReq, client: dict = Depends(_require_client), db: Session = Depends(get_db)):
    exam = db.query(Exam).filter(Exam.exam_id == exam_id, Exam.client_id == client["client_id"]).first()
    if not exam:
        raise HTTPException(404, "Exam not found")
    exam.name = req.name
    exam.category = req.category
    exam.image_url = req.image_url
    exam.description = req.description
    db.commit()
    db.refresh(exam)
    return {"success": True, "exam": exam.to_dict()}


@router.delete("/classroom/exams/{exam_id}", tags=["Classroom"])
async def delete_exam(exam_id: str, client: dict = Depends(_require_client), db: Session = Depends(get_db)):
    exam = db.query(Exam).filter(Exam.exam_id == exam_id, Exam.client_id == client["client_id"]).first()
    if not exam:
        raise HTTPException(404, "Exam not found")
    db.delete(exam)
    db.commit()
    return {"success": True, "message": "Exam deleted"}


@router.get("/classroom/exams/{exam_id}", tags=["Classroom"])
async def get_exam_details(exam_id: str, client: dict = Depends(_require_client), db: Session = Depends(get_db)):
    exam = db.query(Exam).options(
        selectinload(Exam.papers)
        .selectinload(PaperClassroom.subjects)
        .selectinload(Subject.chapters)
        .selectinload(ChapterClassroom.topics)
        .selectinload(TopicClassroom.subtopics)
    ).filter(Exam.exam_id == exam_id, Exam.client_id == client["client_id"]).first()
    if not exam:
        raise HTTPException(404, "Exam not found")
    
    # Build hierarchical tree: Papers -> Subjects -> Chapters -> Topics -> Subtopics
    papers_data = []
    sorted_papers = sorted(exam.papers, key=lambda p: p.created_at or datetime.min, reverse=True)
    for paper in sorted_papers:
        p_dict = paper.to_dict()
        subjects_data = []
        for subject in paper.subjects:
            sub_dict = subject.to_dict()
            chapters_data = []
            for chapter in subject.chapters:
                ch_dict = chapter.to_dict()
                topics_data = []
                for topic in chapter.topics:
                    tp_dict = topic.to_dict()
                    subtopics_data = [st.to_dict() for st in topic.subtopics]
                    tp_dict["subtopics"] = subtopics_data
                    topics_data.append(tp_dict)
                ch_dict["topics"] = topics_data
                chapters_data.append(ch_dict)
            sub_dict["chapters"] = chapters_data
            subjects_data.append(sub_dict)
        p_dict["subjects"] = subjects_data
        papers_data.append(p_dict)
    
    return {
        "success": True,
        "exam": exam.to_dict(),
        "tree": papers_data
    }


# ── Paper Endpoints ───────────────────────────────────────────────────────────

@router.post("/classroom/exams/{exam_id}/papers", tags=["Classroom"])
async def create_paper(exam_id: str, req: CreatePaperReq, client: dict = Depends(_require_client), db: Session = Depends(get_db)):
    exam = db.query(Exam).filter(Exam.exam_id == exam_id, Exam.client_id == client["client_id"]).first()
    if not exam:
        raise HTTPException(404, "Exam not found")
    paper_id = "paper-" + secrets.token_hex(8)
    paper = PaperClassroom(
        paper_id=paper_id,
        exam_id=exam_id,
        name=req.name,
        created_at=datetime.utcnow()
    )
    db.add(paper)
    db.commit()
    db.refresh(paper)
    return {"success": True, "paper": paper.to_dict()}


@router.put("/classroom/papers/{paper_id}", tags=["Classroom"])
async def update_paper(paper_id: str, req: CreatePaperReq, client: dict = Depends(_require_client), db: Session = Depends(get_db)):
    paper = db.query(PaperClassroom).join(Exam).filter(PaperClassroom.paper_id == paper_id, Exam.client_id == client["client_id"]).first()
    if not paper:
        raise HTTPException(404, "Paper not found or access denied")
    paper.name = req.name
    db.commit()
    db.refresh(paper)
    return {"success": True, "paper": paper.to_dict()}


@router.delete("/classroom/papers/{paper_id}", tags=["Classroom"])
async def delete_paper(paper_id: str, client: dict = Depends(_require_client), db: Session = Depends(get_db)):
    paper = db.query(PaperClassroom).join(Exam).filter(PaperClassroom.paper_id == paper_id, Exam.client_id == client["client_id"]).first()
    if not paper:
        raise HTTPException(404, "Paper not found or access denied")
    db.delete(paper)
    db.commit()
    return {"success": True, "message": "Paper deleted"}


# ── Subject Endpoints ─────────────────────────────────────────────────────────

@router.post("/classroom/papers/{paper_id}/subjects", tags=["Classroom"])
async def create_subject(paper_id: str, req: CreateSubjectReq, client: dict = Depends(_require_client), db: Session = Depends(get_db)):
    paper = db.query(PaperClassroom).join(Exam).filter(PaperClassroom.paper_id == paper_id, Exam.client_id == client["client_id"]).first()
    if not paper:
        raise HTTPException(404, "Paper not found or access denied")
    subject_id = "subject-" + secrets.token_hex(8)
    subject = Subject(
        subject_id=subject_id,
        paper_id=paper_id,
        exam_id=paper.exam_id,
        name=req.name,
        color=req.color,
        created_at=datetime.utcnow()
    )
    db.add(subject)
    db.commit()
    db.refresh(subject)
    return {"success": True, "subject": subject.to_dict()}


@router.put("/classroom/subjects/{subject_id}", tags=["Classroom"])
async def update_subject(subject_id: str, req: CreateSubjectReq, client: dict = Depends(_require_client), db: Session = Depends(get_db)):
    subject = db.query(Subject).join(PaperClassroom).join(Exam).filter(Subject.subject_id == subject_id, Exam.client_id == client["client_id"]).first()
    if not subject:
        raise HTTPException(404, "Subject not found or access denied")
    subject.name = req.name
    subject.color = req.color
    db.commit()
    db.refresh(subject)
    return {"success": True, "subject": subject.to_dict()}


@router.delete("/classroom/subjects/{subject_id}", tags=["Classroom"])
async def delete_subject(subject_id: str, client: dict = Depends(_require_client), db: Session = Depends(get_db)):
    subject = db.query(Subject).join(PaperClassroom).join(Exam).filter(Subject.subject_id == subject_id, Exam.client_id == client["client_id"]).first()
    if not subject:
        raise HTTPException(404, "Subject not found or access denied")
    db.delete(subject)
    db.commit()
    return {"success": True, "message": "Subject deleted"}


# ── Chapter Endpoints ─────────────────────────────────────────────────────────

@router.post("/classroom/subjects/{subject_id}/chapters", tags=["Classroom"])
async def create_chapter(subject_id: str, req: CreateChapterReq, client: dict = Depends(_require_client), db: Session = Depends(get_db)):
    subject = db.query(Subject).join(PaperClassroom).join(Exam).filter(Subject.subject_id == subject_id, Exam.client_id == client["client_id"]).first()
    if not subject:
        raise HTTPException(404, "Subject not found or access denied")
    chapter_id = "chapter-" + secrets.token_hex(8)
    chapter = ChapterClassroom(
        chapter_id=chapter_id,
        subject_id=subject_id,
        name=req.name,
        created_at=datetime.utcnow()
    )
    db.add(chapter)
    db.commit()
    db.refresh(chapter)
    return {"success": True, "chapter": chapter.to_dict()}


@router.put("/classroom/chapters/{chapter_id}", tags=["Classroom"])
async def update_chapter(chapter_id: str, req: CreateChapterReq, client: dict = Depends(_require_client), db: Session = Depends(get_db)):
    chapter = db.query(ChapterClassroom).join(Subject).join(PaperClassroom).join(Exam).filter(ChapterClassroom.chapter_id == chapter_id, Exam.client_id == client["client_id"]).first()
    if not chapter:
        raise HTTPException(404, "Chapter not found or access denied")
    chapter.name = req.name
    db.commit()
    db.refresh(chapter)
    return {"success": True, "chapter": chapter.to_dict()}


@router.delete("/classroom/chapters/{chapter_id}", tags=["Classroom"])
async def delete_chapter(chapter_id: str, client: dict = Depends(_require_client), db: Session = Depends(get_db)):
    chapter = db.query(ChapterClassroom).join(Subject).join(PaperClassroom).join(Exam).filter(ChapterClassroom.chapter_id == chapter_id, Exam.client_id == client["client_id"]).first()
    if not chapter:
        raise HTTPException(404, "Chapter not found or access denied")
    db.delete(chapter)
    db.commit()
    return {"success": True, "message": "Chapter deleted"}


# ── Topic Endpoints ───────────────────────────────────────────────────────────

@router.post("/classroom/chapters/{chapter_id}/topics", tags=["Classroom"])
async def create_topic(chapter_id: str, req: CreateTopicReq, client: dict = Depends(_require_client), db: Session = Depends(get_db)):
    chapter = db.query(ChapterClassroom).join(Subject).join(PaperClassroom).join(Exam).filter(ChapterClassroom.chapter_id == chapter_id, Exam.client_id == client["client_id"]).first()
    if not chapter:
        raise HTTPException(404, "Chapter not found or access denied")
    topic_id = "topic-" + secrets.token_hex(8)
    topic = TopicClassroom(
        topic_id=topic_id,
        chapter_id=chapter_id,
        name=req.name,
        created_at=datetime.utcnow()
    )
    db.add(topic)
    db.commit()
    db.refresh(topic)
    return {"success": True, "topic": topic.to_dict()}


@router.put("/classroom/topics/{topic_id}", tags=["Classroom"])
async def update_topic(topic_id: str, req: CreateTopicReq, client: dict = Depends(_require_client), db: Session = Depends(get_db)):
    topic = db.query(TopicClassroom).join(ChapterClassroom).join(Subject).join(PaperClassroom).join(Exam).filter(TopicClassroom.topic_id == topic_id, Exam.client_id == client["client_id"]).first()
    if not topic:
        raise HTTPException(404, "Topic not found or access denied")
    topic.name = req.name
    db.commit()
    db.refresh(topic)
    return {"success": True, "topic": topic.to_dict()}


@router.delete("/classroom/topics/{topic_id}", tags=["Classroom"])
async def delete_topic(topic_id: str, client: dict = Depends(_require_client), db: Session = Depends(get_db)):
    topic = db.query(TopicClassroom).join(ChapterClassroom).join(Subject).join(PaperClassroom).join(Exam).filter(TopicClassroom.topic_id == topic_id, Exam.client_id == client["client_id"]).first()
    if not topic:
        raise HTTPException(404, "Topic not found or access denied")
    db.delete(topic)
    db.commit()
    return {"success": True, "message": "Topic deleted"}


# ── Subtopic Endpoints ────────────────────────────────────────────────────────

@router.post("/classroom/topics/{topic_id}/subtopics", tags=["Classroom"])
async def create_subtopic(topic_id: str, req: CreateSubtopicReq, client: dict = Depends(_require_client), db: Session = Depends(get_db)):
    topic = db.query(TopicClassroom).join(ChapterClassroom).join(Subject).join(PaperClassroom).join(Exam).filter(TopicClassroom.topic_id == topic_id, Exam.client_id == client["client_id"]).first()
    if not topic:
        raise HTTPException(404, "Topic not found or access denied")
    subtopic_id = "subtopic-" + secrets.token_hex(8)
    subtopic = SubtopicClassroom(
        subtopic_id=subtopic_id,
        topic_id=topic_id,
        name=req.name,
        description=req.description,
        created_at=datetime.utcnow()
    )
    db.add(subtopic)
    db.commit()
    db.refresh(subtopic)
    return {"success": True, "subtopic": subtopic.to_dict()}


@router.put("/classroom/subtopics/{subtopic_id}", tags=["Classroom"])
async def update_subtopic(subtopic_id: str, req: CreateSubtopicReq, client: dict = Depends(_require_client), db: Session = Depends(get_db)):
    subtopic = db.query(SubtopicClassroom).join(TopicClassroom).join(ChapterClassroom).join(Subject).join(PaperClassroom).join(Exam).filter(SubtopicClassroom.subtopic_id == subtopic_id, Exam.client_id == client["client_id"]).first()
    if not subtopic:
        raise HTTPException(404, "Subtopic not found or access denied")
    subtopic.name = req.name
    subtopic.description = req.description
    db.commit()
    db.refresh(subtopic)
    return {"success": True, "subtopic": subtopic.to_dict()}


@router.delete("/classroom/subtopics/{subtopic_id}", tags=["Classroom"])
async def delete_subtopic(subtopic_id: str, client: dict = Depends(_require_client), db: Session = Depends(get_db)):
    subtopic = db.query(SubtopicClassroom).join(TopicClassroom).join(ChapterClassroom).join(Subject).join(PaperClassroom).join(Exam).filter(SubtopicClassroom.subtopic_id == subtopic_id, Exam.client_id == client["client_id"]).first()
    if not subtopic:
        raise HTTPException(404, "Subtopic not found or access denied")
    db.delete(subtopic)
    db.commit()
    return {"success": True, "message": "Subtopic deleted"}


@router.post("/classroom/subtopics/{subtopic_id}/generate-description", tags=["Classroom"])
async def generate_subtopic_description(subtopic_id: str, client: dict = Depends(_require_client), db: Session = Depends(get_db)):
    subtopic = db.query(SubtopicClassroom).join(TopicClassroom).join(ChapterClassroom).join(Subject).join(PaperClassroom).join(Exam).filter(
        SubtopicClassroom.subtopic_id == subtopic_id, 
        Exam.client_id == client["client_id"]
    ).first()
    if not subtopic:
        raise HTTPException(404, "Subtopic not found or access denied")
        
    topic = subtopic.topic
    chapter = topic.chapter
    subject = chapter.subject
    
    prompt = f"""Subject: {subject.name}, Chapter: {chapter.name}, Topic: {topic.name}.
Generate a detailed, premium study description and conceptual explanation for the subtopic: "{subtopic.name}".
Write a comprehensive explanation formatted beautifully with Markdown:
- Use heading hierarchy (e.g. ### Key Concepts, ### Practical Examples, etc.)
- Explain terms clearly.
- Provide examples/illustrations.
Return ONLY the formatted markdown text. Do not wrap in a markdown block like ```markdown, do not add metadata, just return the text."""

    try:
        from app.services.llm import generate_simple_response
        desc_text = await generate_simple_response(prompt, system_prompt="You are an expert educator who writes highly informative, clean study materials.")
        subtopic.description = desc_text
        db.commit()
        db.refresh(subtopic)
        return {"success": True, "description": desc_text, "subtopic": subtopic.to_dict()}
    except Exception as e:
        logger.error(f"Error generating subtopic description: {e}")
        raise HTTPException(500, f"Failed to generate description: {str(e)}")


import urllib.parse
import re

def process_markdown_images(text: str) -> str:
    if not text:
        return text
    # Pattern to find [IMAGE: descriptive prompt]
    pattern = re.compile(r'\[IMAGE:\s*([^\]]+)\]')
    def replacer(match):
        prompt = match.group(1).strip()
        encoded = urllib.parse.quote(prompt)
        url = f"https://image.pollinations.ai/prompt/{encoded}?width=1024&height=1024&nologo=true&model=flux"
        return f"![{prompt}]({url})"
    return pattern.sub(replacer, text)


@router.post("/classroom/subtopics/{subtopic_id}/generate-notes", tags=["Classroom"])
async def generate_subtopic_notes(subtopic_id: str, language: str = Form("English"), client: dict = Depends(_require_client), db: Session = Depends(get_db)):
    subtopic = db.query(SubtopicClassroom).join(TopicClassroom).join(ChapterClassroom).join(Subject).join(PaperClassroom).join(Exam).filter(
        SubtopicClassroom.subtopic_id == subtopic_id, 
        Exam.client_id == client["client_id"]
    ).first()
    if not subtopic:
        raise HTTPException(404, "Subtopic not found or access denied")
        
    topic = subtopic.topic
    chapter = topic.chapter
    subject = chapter.subject
    
    prompt = f"""Subject: {subject.name}, Chapter: {chapter.name}, Topic: {topic.name}.
Generate extremely detailed, comprehensive, and structured study notes for the subtopic: "{subtopic.name}".
Language: {language}. Write all explanations, headings, and content in {language}.
Provide exhaustive coverage of all concepts, core definitions, underlying principles, step-by-step explanations, math formulas, code snippets, key takeaways, and practical examples.
Do not summarize briefly; ensure that every important aspect is fully explained in depth so a student can learn the subtopic thoroughly from these notes.

Formatting requirements:
1. Use ONLY pure Markdown — no HTML tags, no <div>, no inline styles.
2. Use blockquotes for callouts:
   - Tips:       > 💡 **Tip:** ...
   - Warnings:   > ⚠️ **Warning:** ...
   - Key Points: > ✅ **Key Point:** ...
   - Examples:   > 📌 **Example:** ...
3. Include at least 2-3 high-quality educational diagrams using the tag `[IMAGE: descriptive prompt for educational diagram]`.
4. Structure sections:
   # 📖 Introduction & Concept Overview
   # 📌 Key Formulas, Rules & Syntax
   # 💡 In-Depth Analysis & Practical Examples
   # ⚡ Critical Takeaways & Common Pitfalls
   # 🔍 Quick Revision & Summary Points

Return ONLY the formatted markdown text. Do not wrap in ```markdown blocks."""

    try:
        from app.services.llm import generate_simple_response
        notes_text = await generate_simple_response(prompt, system_prompt="You are an expert academic tutor specializing in writing comprehensive and extremely high-quality, in-depth study notes.")
        processed_notes = process_markdown_images(notes_text)
        subtopic.notes = processed_notes
        db.commit()
        db.refresh(subtopic)
        return {"success": True, "notes": processed_notes, "markdown": processed_notes, "subtopic": subtopic.to_dict()}
    except Exception as e:
        logger.error(f"Error generating subtopic notes: {e}")
        raise HTTPException(500, f"Failed to generate notes: {str(e)}")


@router.get("/classroom/subtopics/{subtopic_id}/download-notes-pdf", tags=["Classroom"])
async def download_subtopic_notes_pdf(subtopic_id: str, client: dict = Depends(_require_client), db: Session = Depends(get_db)):
    subtopic = db.query(SubtopicClassroom).join(TopicClassroom).join(ChapterClassroom).join(Subject).join(PaperClassroom).join(Exam).filter(
        SubtopicClassroom.subtopic_id == subtopic_id,
        Exam.client_id == client["client_id"]
    ).first()
    if not subtopic:
        raise HTTPException(404, "Subtopic not found or access denied")

    if not subtopic.notes and not subtopic.description:
        raise HTTPException(400, "No notes or description have been generated for this subtopic yet.")

    topic = subtopic.topic
    chapter = topic.chapter if topic else None
    subject = chapter.subject if chapter else None
    paper = subject.paper if subject else None
    exam = paper.exam if paper else None

    exam_name = exam.name if exam else "General Exam"
    subject_name = subject.name if subject else "General"
    chapter_name = chapter.name if chapter else "General"
    topic_name = topic.name if topic else "General"
    subtopic_name = subtopic.name

    try:
        from app.services.pdf_generator import generate_notes_pdf_bytes
        from fastapi.responses import Response

        pdf_bytes = generate_notes_pdf_bytes(
            subtopic_name=subtopic_name,
            topic_name=topic_name,
            chapter_name=chapter_name,
            subject_name=subject_name,
            exam_name=exam_name,
            description_text=subtopic.description or "",
            notes_text=subtopic.notes or "",
        )

        safe_filename = "".join(
            [c if c.isalnum() or c in ("_", ".") else "_" for c in f"{subtopic_name}_notes.pdf"]
        )

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{safe_filename}"'},
        )
    except Exception as e:
        logger.error(f"Error generating notes PDF: {e}")
        raise HTTPException(500, f"Failed to generate PDF: {str(e)}")


# ── Subtopic Reels History Endpoint ─────────────────────────────────────────────

@router.get("/classroom/subtopics/{subtopic_id}/reels", tags=["Classroom"])
async def get_subtopic_reels(subtopic_id: str, client: dict = Depends(_require_client), db: Session = Depends(get_db)):
    subtopic = db.query(SubtopicClassroom).join(TopicClassroom).join(ChapterClassroom).join(Subject).join(PaperClassroom).join(Exam).filter(
        SubtopicClassroom.subtopic_id == subtopic_id,
        Exam.client_id == client["client_id"]
    ).first()
    if not subtopic:
        raise HTTPException(404, "Subtopic not found")
    
    from app.core.models import SocialContent
    reels = db.query(SocialContent).filter(
        SocialContent.client_id == client["client_id"],
        SocialContent.content_type == "reel"
    ).order_by(SocialContent.created_at.desc()).all()
    
    subtopic_reels = []
    for r in reels:
        try:
            meta = r.metadata_info
            if meta and meta.get("subtopic_id") == subtopic_id:
                subtopic_reels.append(r.to_dict())
        except Exception as e:
            logger.warning(f"Error parsing metadata for social content {r.content_id}: {e}")
    
    return {"success": True, "reels": subtopic_reels}


# ── Public Subtopic Reels Endpoint (No Auth Needed for other projects) ──

@router.get("/classroom/public/subtopics/{subtopic_id}/reels", tags=["Classroom Public"])
async def get_public_subtopic_reels(subtopic_id: str, db: Session = Depends(get_db)):
    """
    Publicly fetch all generated reels for a specific subtopic.
    Requires no X-App-Token, making it extremely easy for other projects to consume.
    """
    subtopic = db.query(SubtopicClassroom).filter(SubtopicClassroom.subtopic_id == subtopic_id).first()
    if not subtopic:
        raise HTTPException(404, "Subtopic not found")
        
    from app.core.models import SocialContent
    
    # Fetch all reels without filtering by client_id to allow simple cross-project integration
    reels = db.query(SocialContent).filter(
        SocialContent.content_type == "reel"
    ).order_by(SocialContent.created_at.desc()).all()
    
    subtopic_reels = []
    for r in reels:
        try:
            meta = r.metadata_info
            if meta and meta.get("subtopic_id") == subtopic_id:
                subtopic_reels.append(r.to_dict())
        except Exception as e:
            logger.warning(f"Error parsing metadata for social content {r.content_id}: {e}")
            
    return {"success": True, "reels": subtopic_reels}


# ── Exam History (Reels) Endpoint ─────────────────────────────────────────────

@router.get("/classroom/exams/{exam_id}/history", tags=["Classroom"])
async def get_exam_history(exam_id: str, client: dict = Depends(_require_client), db: Session = Depends(get_db)):
    exam = db.query(Exam).filter(Exam.exam_id == exam_id, Exam.client_id == client["client_id"]).first()
    if not exam:
        raise HTTPException(404, "Exam not found")
        
    from app.core.models import SocialContent
    
    # Query client's reels
    reels = db.query(SocialContent).filter(
        SocialContent.client_id == client["client_id"],
        SocialContent.content_type == "reel"
    ).order_by(SocialContent.created_at.desc()).all()
    
    # Filter by exam_id inside metadata_json
    exam_reels = []
    for r in reels:
        try:
            meta = r.metadata_info
            if meta and meta.get("exam_id") == exam_id:
                exam_reels.append(r.to_dict())
        except Exception as e:
            logger.warning(f"Error parsing metadata for social content {r.content_id}: {e}")
            
    return {"success": True, "reels": exam_reels}

# ── Quiz Generation Endpoint ──────────────────────────────────────────────────

@router.post("/classroom/subtopics/{subtopic_id}/quiz/generate", tags=["Classroom"])
async def generate_subtopic_quiz(subtopic_id: str, client: dict = Depends(_require_client), db: Session = Depends(get_db)):
    subtopic = db.query(SubtopicClassroom).join(TopicClassroom).join(ChapterClassroom).join(Subject).join(PaperClassroom).join(Exam).filter(
        SubtopicClassroom.subtopic_id == subtopic_id,
        Exam.client_id == client["client_id"]
    ).first()
    if not subtopic:
        raise HTTPException(404, "Subtopic not found or access denied")
        
    topic = subtopic.topic
    chapter = topic.chapter if topic else None
    subject = chapter.subject if chapter else None
    paper = subject.paper if subject else None
    exam = paper.exam if paper else None
    
    exam_name = exam.name if exam else "General Exam"
    subject_name = subject.name if subject else "General"
    chapter_name = chapter.name if chapter else "General"
    topic_name = topic.name if topic else "General"
    subtopic_name = subtopic.name
    
    study_material = subtopic.description or subtopic.notes or subtopic.name
    
    prompt = f"""You are an expert academic examiner creating exam questions for the exam: "{exam_name}".
Generate exactly 5 multiple-choice questions based on the provided study material.
The questions must be highly professional and tailored specifically to the standards and difficulty level of the "{exam_name}" exam.
Subject: {subject_name}
Chapter: {chapter_name}
Topic: {topic_name}
Subtopic: {subtopic_name}

Return the response as a strict JSON array.
Format example:
[
  {{
    "question": "What is the capital of France?",
    "options": ["Paris", "London", "Berlin", "Rome"],
    "answer": "Paris"
  }}
]
IMPORTANT: Return ONLY the JSON array, no markdown blocks, no extra text.
"""
    try:
        from app.services.llm import generate_answer
        import json
        import re
        
        raw_response = await generate_answer(prompt, study_material)
        
        # Clean markdown code blocks if any
        clean_json = re.sub(r'```json\s*', '', raw_response)
        clean_json = re.sub(r'\s*```', '', clean_json).strip()
        
        quiz_data = json.loads(clean_json)
        if not isinstance(quiz_data, list):
            raise ValueError("Response is not a JSON array")
            
        return {"success": True, "quiz": quiz_data}
    except Exception as e:
        logger.error(f"Error generating quiz: {e}")
        raise HTTPException(500, f"Failed to generate quiz: {str(e)}")

# ── Bulk Auto-Generate Course Endpoint ────────────────────────────────────────

async def run_generation_pipeline(paper_id: str, exam_id: str, exam_category: str, exam_name: str, paper_name: str):
    from app.core.database import get_session_local
    from app.core.config import settings
    from openai import AsyncOpenAI
    import json
    import re
    import secrets
    from datetime import datetime
    import asyncio
    
    SessionLocal = get_session_local()
    db = SessionLocal()
    
    # ── Use Groq API (Free: 30 RPM, 1000 RPD, 100K TPD) ──
    groq_key = settings.GROQ_API_KEY
    groq_model = settings.GROQ_MODEL or "llama-3.3-70b-versatile"
    
    groq_client = AsyncOpenAI(
        api_key=groq_key,
        base_url="https://api.groq.com/openai/v1",
        timeout=120.0
    )
    
    call_count = 0  # Track total API calls
    
    async def ask_llm(prompt_text, max_retries=3):
        """Call Groq with retry on 429 rate-limit errors."""
        nonlocal call_count
        
        for attempt in range(max_retries):
            try:
                response = await groq_client.chat.completions.create(
                    model=groq_model,
                    messages=[
                        {"role": "system", "content": "You are a specialized curriculum generator. Return strictly valid JSON containing exactly what is requested, no markdown wrappers, no extra text."},
                        {"role": "user", "content": prompt_text}
                    ],
                    max_tokens=2000,
                    temperature=0.7
                )
                raw = response.choices[0].message.content
                clean = re.sub(r'```json\s*', '', raw, flags=re.IGNORECASE)
                clean = re.sub(r'\s*```', '', clean).strip()
                call_count += 1
                logger.info(f"📡 Groq call #{call_count} success (attempt {attempt+1})")
                return json.loads(clean)
            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "rate_limit" in err_str.lower():
                    # Default fallback wait time
                    wait_time = (attempt + 1) * 20
                    
                    # Try to extract the exact wait time from response headers if available
                    try:
                        if hasattr(e, "response") and e.response is not None:
                            retry_after = e.response.headers.get("retry-after") or e.response.headers.get("x-ratelimit-reset")
                            if retry_after:
                                if retry_after.isdigit():
                                    wait_time = int(retry_after) + 2
                                else:
                                    # Parse duration like "1m55s" or similar
                                    m_match = re.search(r"(\d+)m", retry_after)
                                    s_match = re.search(r"(\d+)s", retry_after)
                                    parsed_time = 0
                                    if m_match:
                                        parsed_time += int(m_match.group(1)) * 60
                                    if s_match:
                                        parsed_time += int(s_match.group(1))
                                    if parsed_time > 0:
                                        wait_time = parsed_time + 2
                    except Exception as parse_err:
                        logger.debug(f"Failed to parse retry-after header: {parse_err}")
                    
                    # If headers didn't work, try to extract wait time from the exception message using regex
                    # e.g., "Please try again in 155.084s." or "try again in 155 seconds"
                    try:
                        match = re.search(r"try again in (\d+(?:\.\d+)?)\s*s", err_str)
                        if not match:
                            match = re.search(r"try again in (\d+(?:\.\d+)?)\s*second", err_str)
                        if match:
                            wait_time = int(float(match.group(1))) + 2
                    except Exception as regex_err:
                        logger.debug(f"Failed to parse wait time via regex: {regex_err}")
                        
                    logger.warning(f"⚠️ Groq 429 rate limit hit. Waiting {wait_time}s... (attempt {attempt+1}/{max_retries})")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"❌ Groq/JSON Error: {e}")
                    return None
        
        logger.error(f"❌ Max retries exhausted for prompt")
        return None

    try:
        logger.info(f"🚀 Started Groq Syllabus Pipeline for '{paper_name}' (model: {groq_model})")
        
        # ── STEP 1: Generate Subjects ──
        prompt_subjs = f"""Exam: {exam_name} ({exam_category}), Paper: {paper_name}. 
Generate the standard subjects (5-10 max) for this paper. 
Return JSON: {{"subjects": [{{"name": "Subject Name", "color": "#HEX"}}]}}"""
        
        res_subjs = await ask_llm(prompt_subjs)
        if not res_subjs or 'subjects' not in res_subjs:
            logger.error("Failed to generate subjects. Aborting.")
            return
            
        subject_records = []
        for s in res_subjs['subjects']:
            subj_id = "subject-" + secrets.token_hex(8)
            new_subject = Subject(subject_id=subj_id, exam_id=exam_id, paper_id=paper_id, name=s['name'], color=s.get('color', '#4f46e5'))
            db.add(new_subject)
            subject_records.append((subj_id, s['name']))
        db.commit()
        logger.info(f"✅ Step 1 done: {len(subject_records)} subjects created")
        await asyncio.sleep(3)
        
        # ── STEP 2: Generate Chapters (per subject) ──
        all_chapters = []
        for subj_id, subj_name in subject_records:
            prompt = f"""Exam: {exam_name}, Subject: {subj_name}. 
Generate comprehensive chapters (5-15 max). 
Return JSON: {{"chapters": [{{"name": "Chapter Name"}}]}}"""
            
            res = await ask_llm(prompt)
            if res and 'chapters' in res:
                for c in res['chapters']:
                    chap_id = "chapter-" + secrets.token_hex(8)
                    db.add(ChapterClassroom(chapter_id=chap_id, subject_id=subj_id, name=c['name']))
                    all_chapters.append((chap_id, c['name'], subj_name))
                db.commit()
            await asyncio.sleep(3)  # Respect 30 RPM
        
        logger.info(f"✅ Step 2 done: {len(all_chapters)} chapters created")
        
        # ── STEP 3: Generate Topics + Subtopics together (saves API calls) ──
        all_topics = []
        all_subtopics = []
        for chap_id, cname, sname in all_chapters:
            prompt = f"""Subject: {sname}, Chapter: {cname}. 
Generate all topics (3-8) with their subtopics (2-4 each).
Return JSON: {{"topics": [{{"name": "Topic Name", "subtopics": [{{"name": "Subtopic Name"}}]}}]}}"""
            
            res = await ask_llm(prompt)
            if res and 'topics' in res:
                for t in res['topics']:
                    top_id = "topic-" + secrets.token_hex(8)
                    db.add(TopicClassroom(topic_id=top_id, chapter_id=chap_id, name=t['name']))
                    all_topics.append((top_id, t['name'], cname, sname))
                    
                    # Also create subtopics from the same response
                    for st in t.get('subtopics', []):
                        sub_id = "subtopic-" + secrets.token_hex(8)
                        db.add(SubtopicClassroom(subtopic_id=sub_id, topic_id=top_id, name=st['name'], description=""))
                        all_subtopics.append((sub_id, st['name'], t['name'], cname, sname))
                db.commit()
            await asyncio.sleep(3)  # Respect 30 RPM
        
        logger.info(f"✅ Step 3 done: {len(all_topics)} topics, {len(all_subtopics)} subtopics created")
        
        # ── STEP 4: Generate Descriptions (batch 3 subtopics per call to save quota) ──
        batch_size = 3
        for i in range(0, len(all_subtopics), batch_size):
            batch = all_subtopics[i:i+batch_size]
            
            subtopic_list = ", ".join([f'"{st[1]} (Topic: {st[2]})"' for st in batch])
            prompt = f"""Subject: {batch[0][4]}, Chapter: {batch[0][3]}.
Generate detailed study descriptions for these subtopics: [{subtopic_list}].
For each subtopic, write: Intro, Body, and Conclusion.
Return JSON: {{"descriptions": [{{"subtopic_name": "...", "description": "Intro: ...\\n\\nBody: ...\\n\\nConclusion: ..."}}]}}"""
            
            res = await ask_llm(prompt)
            if res and 'descriptions' in res:
                for desc_item in res['descriptions']:
                    desc_name = desc_item.get('subtopic_name', '')
                    desc_text = desc_item.get('description', '')
                    # Match by name to the batch
                    for sub_id, sname, tname, cname, sjname in batch:
                        if sname.lower() in desc_name.lower() or desc_name.lower() in sname.lower():
                            sub_obj = db.query(SubtopicClassroom).filter(SubtopicClassroom.subtopic_id == sub_id).first()
                            if sub_obj and not sub_obj.description:
                                sub_obj.description = desc_text
                                break
                db.commit()
            await asyncio.sleep(3)  # Respect 30 RPM
        
        logger.info(f"✅ Step 4 done: descriptions generated")
        logger.info(f"🎉 Pipeline COMPLETED for '{paper_name}' — Total Groq calls: {call_count}")
        
    except Exception as e:
        logger.error(f"Pipeline error: {e}")
        db.rollback()
    finally:
        db.close()

# ── Bulk Auto-Generate Course Endpoint ────────────────────────────────────────

@router.post("/classroom/papers/{paper_id}/auto-generate", tags=["Classroom"])
async def auto_generate_course(paper_id: str, background_tasks: BackgroundTasks, client: dict = Depends(_require_client), db: Session = Depends(get_db)):
    paper = db.query(PaperClassroom).filter(PaperClassroom.paper_id == paper_id).first()
    if not paper:
        raise HTTPException(404, "Paper not found")
        
    exam = db.query(Exam).filter(Exam.exam_id == paper.exam_id).first()
    if not exam:
        raise HTTPException(404, "Exam not found")

    background_tasks.add_task(
        run_generation_pipeline,
        paper_id=paper_id,
        exam_id=exam.exam_id,
        exam_category=exam.category,
        exam_name=exam.name,
        paper_name=paper.name
    )
    
    return {"success": True, "message": "Generation started in the background. Please wait a few minutes and refresh."}


# ── Classroom Reel Generation Endpoint ───────────────────────────────────────

class TranscriptReq(BaseModel):
    language: Optional[str] = "English"

@router.post("/classroom/subtopics/{subtopic_id}/generate-transcript", tags=["Classroom"])
async def generate_subtopic_transcript(subtopic_id: str, req: TranscriptReq, client: dict = Depends(_require_client), db: Session = Depends(get_db)):
    subtopic = db.query(SubtopicClassroom).join(TopicClassroom).join(ChapterClassroom).join(Subject).join(PaperClassroom).join(Exam).filter(
        SubtopicClassroom.subtopic_id == subtopic_id,
        Exam.client_id == client["client_id"]
    ).first()
    if not subtopic:
        raise HTTPException(404, "Subtopic not found or access denied")

    topic = subtopic.topic
    chapter = topic.chapter if topic else None
    subject = chapter.subject if chapter else None

    subject_name = subject.name if subject else "General"
    chapter_name = chapter.name if chapter else "General"
    topic_name = topic.name if topic else subtopic.name
    lang = req.language or "English"

    raw = subtopic.description or subtopic.notes or subtopic.name
    import re as _re
    plain = _re.sub(r'<[^>]+>', '', raw)
    plain = _re.sub(r'\[IMAGE:[^\]]+\]', '', plain)
    plain = _re.sub(r'[#*`>_~]', '', plain).strip()[:2000]

    prompt = f"""You are an expert educational content creator.
Write a detailed, engaging 1-minute educational video narration script in {lang} for the following topic.

Subject: {subject_name} | Chapter: {chapter_name} | Topic: {topic_name}
Subtopic: {subtopic.name}

Source material:
{plain}

Requirements:
- Write in {lang} language only.
- CRITICAL: If the language is Hindi, write the dialogue strictly in Devanagari Unicode script (e.g. "भारत", "विज्ञान"). NEVER write in Hinglish (Hindi written using English/Latin alphabet, e.g. "Bharat", "vigyan"), as TTS engines pronounce Hinglish with a highly robotic/incorrect accent.
- CRITICAL: Spell out all numbers, place names, acronyms, and math symbols fully in spoken words of the target language (e.g. write "उन्नीस सौ सैंतालीस" instead of "1947", "प्रतिशत" / "percent" instead of "%", "किलोमीटर" instead of "km") so that ElevenLabs reads them with perfect professional pronunciation.
- Length: approximately 150-180 words (enough for 60 seconds of natural speech)
- Style: conversational, educational, engaging — like a knowledgeable teacher explaining to students
- Cover: introduction, key concepts, important facts, and a brief conclusion
- Use simple clear sentences, no bullet points, no scene labels — just flowing narration text
- Mix technical terms with simple explanations (like the reference style: use English terms with {lang} explanations)

Return ONLY the narration text, nothing else."""

    try:
        from app.services.llm import generate_simple_response
        transcript = await generate_simple_response(prompt, system_prompt=f"You are an expert educational narrator. Write natural, flowing narration in {lang}.")
        return {"success": True, "transcript": transcript.strip()}
    except Exception as e:
        logger.error(f"Error generating transcript: {e}")
        raise HTTPException(500, f"Failed to generate transcript: {str(e)}")


class ClassroomReelReq(BaseModel):
    language: Optional[str] = "English"
    voice_id: Optional[str] = None
    topic_name: Optional[str] = ""
    transcript: Optional[str] = ""

@router.post("/classroom/subtopics/{subtopic_id}/generate-reel", tags=["Classroom"])
async def generate_classroom_reel(subtopic_id: str, req: ClassroomReelReq, client: dict = Depends(_require_client), db: Session = Depends(get_db)):
    subtopic = db.query(SubtopicClassroom).join(TopicClassroom).join(ChapterClassroom).join(Subject).join(PaperClassroom).join(Exam).filter(
        SubtopicClassroom.subtopic_id == subtopic_id,
        Exam.client_id == client["client_id"]
    ).first()
    if not subtopic:
        raise HTTPException(404, "Subtopic not found or access denied")

    topic = subtopic.topic
    chapter = topic.chapter if topic else None
    subject = chapter.subject if chapter else None
    paper = subject.paper if subject else None
    exam = paper.exam if paper else None

    exam_name = exam.name if exam else "General"
    subject_name = subject.name if subject else "General"
    chapter_name = chapter.name if chapter else "General"
    topic_name = topic.name if topic else subtopic.name
    lang = req.language or "English"

    study_material = subtopic.description or subtopic.notes or subtopic.name

    # Generate structured scene-by-scene script
    from app.services.llm import generate_simple_response
    struct_prompt = f"""Convert the following study material into a structured scene-by-scene educational video reel script.

Subject: {subject_name}, Chapter: {chapter_name}, Topic: {topic_name}, Subtopic: {subtopic.name}
Exam: {exam_name}

STUDY MATERIAL:
{study_material[:3000]}

TARGET LANGUAGE FOR DIALOGUE: {lang}

🎙️ Dialogue Pronunciation Rules:
1. If the target language is Hindi, you MUST write the dialogue strictly in proper Devanagari Unicode script (e.g. "भारत", "प्रौद्योगिकी", "इतिहास"). NEVER write in Hinglish (Hindi written using English/Latin alphabet, e.g. "Bharat", "vigyan"), as TTS engines pronounce Hinglish with a highly robotic/incorrect accent.
2. CRITICAL: Spell out all numbers, place names, acronyms, and math symbols fully in spoken words of the target language (e.g. write "उन्नीस सौ सैंतालीस" instead of "1947", "प्रतिशत" / "percent" instead of "%", "किलोमीटर" instead of "km") so that ElevenLabs reads them with perfect professional pronunciation.

FORMAT FOR EACH SCENE (exactly this format):

🎬 Scene 1 (0-5 sec)
🎙️ Dialogue: [Narration in {lang}, 15-25 words, natural spoken sentence]
📸 Visuals / Footage: [Detailed English description for AI image generation]
🎥 Editing Notes: [Camera movement]

Each scene is 5 seconds.
Do NOT include any intro, outro, headers, or markdown wrappers. Only output the scene blocks."""

    structured_script = await generate_simple_response(struct_prompt, "You are a professional educational video script writer. Output only the scene blocks as requested.")

    from app.services.video_engine import assemble_advanced_reel
    import json
    from app.core.models import SocialContent

    res = await assemble_advanced_reel(
        structured_script,
        language=lang,
        voice_id=req.voice_id,
        bgm_style="cinematic"
    )

    video_url = res.get("video_url") if res else None
    scenes_data = res.get("scenes", []) if res else []

    if not video_url:
        raise HTTPException(500, "Reel generation failed")

    content_id = secrets.token_hex(8)

    # ── CLOUDFLARE R2 UPLOAD ──
    try:
        from app.services.r2_storage import upload_to_r2
        local_video_path = os.path.join(os.getcwd(), "uploads", "social", os.path.basename(video_url))
        
        # Key: reels/subtopic_{subtopic_id}/reel_{content_id}.mp4
        r2_key_unique = f"reels/subtopic_{subtopic_id}/reel_{content_id}.mp4"
        r2_key_latest = f"reels/subtopic_{subtopic_id}/latest.mp4"
        
        # Upload unique reel
        r2_url = upload_to_r2(local_video_path, r2_key_unique, "video/mp4")
        
        # Upload latest.mp4 for static referencing
        upload_to_r2(local_video_path, r2_key_latest, "video/mp4")
        
        if r2_url:
            logger.info(f"R2 Storage: Successfully saved classroom reel in Cloudflare R2! URL={r2_url}")
            video_url = r2_url
    except Exception as r2_err:
        logger.error(f"Failed to upload to Cloudflare R2 (falling back to local storage): {r2_err}")

    db_item = SocialContent(
        content_id=content_id,
        client_id=client["client_id"],
        content_type="reel",
        title=f"{subtopic.name} — {subject_name}",
        body=structured_script[:1000],
        media_url=video_url,
        scenes_json=json.dumps(scenes_data),
        metadata_json=json.dumps({
            "subtopic_id": subtopic_id,
            "exam_id": exam.exam_id if exam else "",
            "voice_id": req.voice_id,
            "language": lang,
            "script": structured_script
        })
    )
    db.add(db_item)
    db.commit()

    # Copy to subtopic-specific permanent directory for easy integration with other projects
    try:
        import shutil
        orig_filename = os.path.basename(res.get("video_url"))
        local_video_path = os.path.join(os.getcwd(), "uploads", "social", orig_filename)
        if os.path.exists(local_video_path):
            subtopic_reels_dir = os.path.join(os.getcwd(), "uploads", "reels", f"subtopic_{subtopic_id}")
            os.makedirs(subtopic_reels_dir, exist_ok=True)
            
            # Copy as a unique file
            dest_unique = os.path.join(subtopic_reels_dir, f"reel_{content_id}.mp4")
            shutil.copy2(local_video_path, dest_unique)
            
            # Copy as latest.mp4 for static integration
            dest_latest = os.path.join(subtopic_reels_dir, "latest.mp4")
            if os.path.exists(dest_latest):
                try: os.remove(dest_latest)
                except: pass
            shutil.copy2(local_video_path, dest_latest)
            
            logger.info(f"Classroom Reel physically saved to: {dest_unique} and {dest_latest}")
    except Exception as copy_err:
        logger.error(f"Failed to copy classroom reel to subtopic directory: {copy_err}")

    return {
        "success": True,
        "content_id": content_id,
        "video_url": video_url,
        "scenes": scenes_data,
        "script": structured_script
    }


# ── Granular Fetch Endpoints for Step-by-Step UI ─────────────────────────────

@router.get("/classroom/exams/{exam_id}/papers", tags=["Classroom"])
async def list_exam_papers(exam_id: str, client: dict = Depends(_require_client), db: Session = Depends(get_db)):
    exam = db.query(Exam).filter(Exam.exam_id == exam_id, Exam.client_id == client["client_id"]).first()
    if not exam:
        raise HTTPException(404, "Exam not found or access denied")
    papers = db.query(PaperClassroom).filter(PaperClassroom.exam_id == exam_id).order_by(PaperClassroom.created_at.desc()).all()
    return {"success": True, "papers": [p.to_dict() for p in papers]}


@router.get("/classroom/papers/{paper_id}/subjects", tags=["Classroom"])
async def list_paper_subjects(paper_id: str, client: dict = Depends(_require_client), db: Session = Depends(get_db)):
    paper = db.query(PaperClassroom).join(Exam).filter(PaperClassroom.paper_id == paper_id, Exam.client_id == client["client_id"]).first()
    if not paper:
        raise HTTPException(404, "Paper not found or access denied")
    subjects = db.query(Subject).filter(Subject.paper_id == paper_id).order_by(Subject.created_at.desc()).all()
    return {"success": True, "subjects": [s.to_dict() for s in subjects]}


@router.get("/classroom/subjects/{subject_id}/chapters", tags=["Classroom"])
async def list_subject_chapters(subject_id: str, client: dict = Depends(_require_client), db: Session = Depends(get_db)):
    subject = db.query(Subject).join(PaperClassroom).join(Exam).filter(Subject.subject_id == subject_id, Exam.client_id == client["client_id"]).first()
    if not subject:
        raise HTTPException(404, "Subject not found or access denied")
    chapters = db.query(ChapterClassroom).filter(ChapterClassroom.subject_id == subject_id).order_by(ChapterClassroom.created_at.asc()).all()
    return {"success": True, "chapters": [c.to_dict() for c in chapters]}


@router.get("/classroom/chapters/{chapter_id}/topics", tags=["Classroom"])
async def list_chapter_topics(chapter_id: str, client: dict = Depends(_require_client), db: Session = Depends(get_db)):
    chapter = db.query(ChapterClassroom).join(Subject).join(PaperClassroom).join(Exam).filter(ChapterClassroom.chapter_id == chapter_id, Exam.client_id == client["client_id"]).first()
    if not chapter:
        raise HTTPException(404, "Chapter not found or access denied")
    topics = db.query(TopicClassroom).filter(TopicClassroom.chapter_id == chapter_id).order_by(TopicClassroom.created_at.asc()).all()
    return {"success": True, "topics": [t.to_dict() for t in topics]}


@router.get("/classroom/topics/{topic_id}/subtopics", tags=["Classroom"])
async def list_topic_subtopics(topic_id: str, client: dict = Depends(_require_client), db: Session = Depends(get_db)):
    topic = db.query(TopicClassroom).join(ChapterClassroom).join(Subject).join(PaperClassroom).join(Exam).filter(TopicClassroom.topic_id == topic_id, Exam.client_id == client["client_id"]).first()
    if not topic:
        raise HTTPException(404, "Topic not found or access denied")
    subtopics = db.query(SubtopicClassroom).filter(SubtopicClassroom.topic_id == topic_id).order_by(SubtopicClassroom.created_at.asc()).all()
    return {"success": True, "subtopics": [s.to_dict() for s in subtopics]}


@router.get("/classroom/subtopics/{subtopic_id}", tags=["Classroom"])
async def get_subtopic_details(subtopic_id: str, client: dict = Depends(_require_client), db: Session = Depends(get_db)):
    subtopic = db.query(SubtopicClassroom).join(TopicClassroom).join(ChapterClassroom).join(Subject).join(PaperClassroom).join(Exam).filter(SubtopicClassroom.subtopic_id == subtopic_id, Exam.client_id == client["client_id"]).first()
    if not subtopic:
        raise HTTPException(404, "Subtopic not found or access denied")
    return {"success": True, "subtopic": subtopic.to_dict()}

from app.core.database import get_session_local
from app.core.models import Exam, PaperClassroom, Subject, ChapterClassroom, TopicClassroom, SubtopicClassroom
from sqlalchemy.orm import selectinload

SessionLocal = get_session_local()
db = SessionLocal()
try:
    print("Running optimized query...")
    exam = db.query(Exam).options(
        selectinload(Exam.papers)
        .selectinload(PaperClassroom.subjects)
        .selectinload(Subject.chapters)
        .selectinload(ChapterClassroom.topics)
        .selectinload(TopicClassroom.subtopics)
    ).first()
    if exam:
        print(f"Exam found: {exam.name}")
        print(f"Papers count: {len(exam.papers)}")
    else:
        print("No exam found in database.")
except Exception as e:
    import traceback
    traceback.print_exc()
finally:
    db.close()

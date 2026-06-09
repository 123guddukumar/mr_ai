import sys
import os
import urllib.parse
import random

# Setup path to import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import get_session_local
from app.core.models import Subject, ChapterClassroom, PaperClassroom
from app.routes.classroom import generate_premium_image_locally

def main():
    SessionLocal = get_session_local()
    db = SessionLocal()
    
    try:
        # 1. Update all subjects
        subjects = db.query(Subject).all()
        print(f"Updating {len(subjects)} subjects...")
        for s in subjects:
            paper = db.query(PaperClassroom).filter(PaperClassroom.paper_id == s.paper_id).first()
            paper_context = f" for {paper.name}" if paper else ""
            
            s.image_url = generate_premium_image_locally(s.name, subtitle="Subject")
            print(f"  - Subject '{s.name}' updated to: {s.image_url}")
            
        # 2. Update all chapters
        chapters = db.query(ChapterClassroom).all()
        print(f"Updating {len(chapters)} chapters...")
        for c in chapters:
            subject = db.query(Subject).filter(Subject.subject_id == c.subject_id).first()
            subject_context = f" of {subject.name}" if subject else ""
            
            c.image_url = generate_premium_image_locally(c.name, subtitle="Chapter")
            print(f"  - Chapter '{c.name}' updated to: {c.image_url}")
            
        db.commit()
        print("Successfully updated database with local premium cover images.")
    except Exception as e:
        db.rollback()
        print(f"Error during migration: {e}")
        sys.exit(1)
    finally:
        db.close()

if __name__ == '__main__':
    main()

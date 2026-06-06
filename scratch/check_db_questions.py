import sys
import os

sys.path.insert(0, os.getcwd())

from app.core.database import get_session_local
from app.core.models import PYQQuestion, PYQSet

def inspect_questions():
    SessionLocal = get_session_local()
    db = SessionLocal()
    try:
        # Get the latest PYQ set
        pyq_set = db.query(PYQSet).order_by(PYQSet.created_at.desc()).first()
        if not pyq_set:
            print("No PYQ set found.")
            return

        print(f"Latest PYQ Set: {pyq_set.name} (ID: {pyq_set.pyq_set_id})")
        print(f"Overview Generated Flag: {pyq_set.overview_generated}")
        
        questions = db.query(PYQQuestion).filter(PYQQuestion.pyq_set_id == pyq_set.pyq_set_id).all()
        print(f"Total Questions in DB: {len(questions)}")
        
        explained = [q for q in questions if q.explanation and q.explanation.strip()]
        print(f"Questions with explanation: {len(explained)}")
        
        if explained:
            print("\nSample Explanations:")
            for idx, q in enumerate(explained[:3]):
                print(f"Q: {q.question_text[:100]}...")
                print(f"Ans: {q.correct_answer}")
                print(f"Exp: {q.explanation[:150]}...\n")
                
    finally:
        db.close()

if __name__ == "__main__":
    inspect_questions()

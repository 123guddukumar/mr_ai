import json
from app.core.database import get_session_local
from app.core.models import SocialContent

db = get_session_local()()
items = db.query(SocialContent).filter(SocialContent.content_type == 'reel').order_by(SocialContent.created_at.desc()).limit(15).all()

res = []
for r in items:
    res.append({
        "content_id": r.content_id,
        "title": r.title,
        "media_url": r.media_url,
        "metadata": r.metadata_info
    })

with open("scratch/db_reels.json", "w", encoding="utf-8") as f:
    json.dump(res, f, indent=2, ensure_ascii=False)

print("Dumped", len(res), "reels to scratch/db_reels.json")

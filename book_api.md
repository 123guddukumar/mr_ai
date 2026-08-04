# Personal Book Library API Guidance

Book library endpoints ko access karne, auto-scrape karne, manual book add karne aur manage karne ki detailed guide.

---

## Base URL
Sabhi endpoints `/api` prefix ke sath starting point ko consume karte hain.
E.g., `http://localhost:8000/api`

---

## API Endpoints List

### 1. Get All Saved Books
Library me save kiye gaye sabhi books ki list fetch karne ke liye.
- **Endpoint**: `GET /api/books`
- **Response Structure (JSON)**:
  ```json
  [
    {
      "id": 4,
      "title": "The Alchemist",
      "author": "Paulo Coelho",
      "rating": "4.4",
      "rating_count": "244",
      "cover_image_url": "https://covers.openlibrary.org/b/id/7414780-L.jpg",
      "bookmark_quote": "The secret of life, though, is to fall seven times and to get up eight times.",
      "summary": "### Jeevan Ka Sar\nSantiago ek charwaha hai jo apne sapne ke peeche bhagta hai...\n\n### Book Review\nSantiago ka safar humein inspire karta hai...\n\n### Main Takeaway\nHumme apne Personal Legend ko follow karna chahiye.",
      "url": "https://fourminutebooks.com/the-alchemist-summary/",
      "video_url": "https://www.youtube.com/embed/h43aQ8hbugs",
      "audio_url": "https://fourminutebooks.com/2-minute-pep-talks/",
      "read_time": "4 minutes",
      "category": "Self-Improvement, Fiction",
      "target_audience": "Dreamers and anyone seeking life goals.",
      "key_lessons": [
        {
          "title": "Lesson 1",
          "description": "If you want to reach your biggest goals and feel fulfilled, you must follow your Personal Legend."
        },
        {
          "title": "Lesson 2",
          "description": "Stop being afraid if you want to remove the barriers that keep you from progressing."
        },
        {
          "title": "Lesson 3",
          "description": "Rise more times than you fall and you will never fail."
        }
      ],
      "created_at": "2026-08-04T12:00:00.123456"
    }
  ]
  ```

---

### 2. Auto-Scrape & Add Book
Four Minute Books, Goodreads, ya Wikipedia ke book summary link se automatic details extract aur add karne ke liye.
- **Endpoint**: `POST /api/books`
- **Request Body (JSON)**:
  ```json
  {
    "url": "https://fourminutebooks.com/the-alchemist-summary/"
  }
  ```
- **Response Structure (JSON)**:
  Returns the saved `BookResponse` object same as above (with auto-fetched OpenLibrary covers, parsed audio link, YouTube summary video embed, and AI-structured Hinglish summaries).

---

### 3. Add Book Manually (Manual Addition)
Manually titles, authors, aur detailed notes type karke library me book add karne ke liye. 
> [!NOTE]
> Agar `cover_image_url` ya `video_url` field khali chhodte hain, toh server background me automatically inhe **OpenLibrary API** aur **YouTube search** se fetch karke auto-fill kar dega.

- **Endpoint**: `POST /api/books/manual`
- **Request Body (JSON)**:
  ```json
  {
    "title": "Think and Grow Rich",
    "author": "Napoleon Hill",
    "category": "Self-Improvement, Finance",
    "read_time": "6 Min Read",
    "rating": "4.8",
    "rating_count": "5.5M",
    "cover_image_url": "", 
    "bookmark_quote": "Whatever the mind can conceive and believe, it can achieve.",
    "target_audience": "Entrepreneurs and goal-seekers.",
    "summary": "### Jeevan Ka Sar\nYeh book ameer hone ke psychological principles ko explain karti hai...\n\n### Main Takeaway\nStrong desire aur consistent action hi success ki key hai.",
    "key_lessons": [
      {
        "title": "Desire",
        "description": "The starting point of all achievement."
      },
      {
        "title": "Faith",
        "description": "Visualization of, and belief in attainment of desire."
      }
    ],
    "video_url": "", 
    "audio_url": ""
  }
  ```
- **Response Structure (JSON)**:
  Returns the saved `BookResponse` object.

---

### 4. Delete Book
Library se kisi book record ko remove karne ke liye.
- **Endpoint**: `DELETE /api/books/{book_id}`
- **Path Parameter**:
  - `book_id` (Integer): Delete karne wale book ki ID. (E.g., `/api/books/4`)
- **Response Structure (JSON)**:
  ```json
  {
    "success": true,
    "message": "Book deleted successfully"
  }
  ```

---

## DB Migration & Backend Integration Details
- Database table name is `books`.
- SQLAlchemy class maps to `Book` model inside [app/core/models.py](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/app/core/models.py).
- Tables columns automatically alter on backend start using [app/core/database.py](file:///c:/Users/LENOVO/Downloads/mr_ai_rag_v2/mr_ai_rag_v2/app/core/database.py).

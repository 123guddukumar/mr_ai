# Daily Planner API Documentation 📅

Daily Planner API ka use sirf **Root Personal Assistant Agent 👑** ke plans and tasks schedule karne, conflict check karne, aur dynamic updates perform karne ke liye kiya jata hai. 

Is document me saare endpoints, unke Request/Response schemas aur frontend integration steps details me bataye gaye hain.

---

## 🔒 Authentication & Headers
Saari requests ko authenticate karne ke liye headers me `X-App-Token` bhejna compulsory hai.

```http
Content-Type: application/json
X-App-Token: <YOUR_SESSION_TOKEN>
```

---

## 🚀 Endpoints Reference

### 1. Get Today's Plans
Aaj ke din ke saare schedule plans fetch karne ke liye (sliding carousel ya today checklist ke liye).
- **Endpoint**: `GET /api/root-agent/plans/today`
- **Response Example**:
  ```json
  [
    {
      "plan_id": "8b5a7c29d0f41a3e",
      "title": "Morning Team Standup",
      "description": "Discuss project sprint deliverables.",
      "category": "work",
      "plan_date": "2026-07-24",
      "plan_time": "10:30",
      "status": "pending",
      "is_completed": false,
      "from_meeting": false,
      "created_at": "2026-07-24T05:00:00"
    }
  ]
  ```

---

### 2. List All Plans (With Filtering)
User ke total list of plans ko status filter aur date-time sorting ke sath retrieve karne ke liye.
> [!NOTE]
> Agar `filter` query parameter `all` hai ya omit kiya gaya hai, to response me **completed plans ko exclude** kar diya jata hai taaki main list clutter-free rahe. Completed plans ko dekhne ke liye explicitly `filter=completed` pass karna hoga.

- **Endpoint**: `GET /api/root-agent/plans`
- **Query Parameters**:
  - `filter` (Optional): `all` (returns pending & upcoming only) | `pending` | `upcoming` | `completed` (Done tab)
- **Response Example**:
  ```json
  [
    {
      "plan_id": "8b5a7c29d0f41a3e",
      "title": "Morning Team Standup",
      "description": "Discuss project sprint deliverables.",
      "category": "work",
      "plan_date": "2026-07-24",
      "plan_time": "10:30",
      "status": "pending",
      "is_completed": false,
      "from_meeting": false
    }
  ]
  ```

---

### 3. Create Daily Plan
Naya plan create karne ke liye.
> [!IMPORTANT]
> **Backend Time Validation Logic**: 
> - Input dates ko server ke local timezone (`datetime.now()`) ke according check kiya jata hai taaki machine timezone mismatches na ho.
> - Form bharne me lagne wale delay aur clock deviations ko avoid karne ke liye **12-hour buffer** (`now_local - timedelta(hours=12)`) lagaya gaya hai. Isse users current day ke kisi bhi past time ke liye task schedule kar sakte hain, par kal ya usse purane din ke tasks block hote hain.
>
> [!TIP]
> **Typo Tolerance & Fallback Sync**:
> - Parser Hinglish/Hindi typos ko tolerant level par support karta hai (e.g., `meetiang`, `metting` to `meeting`).
> - Agar schedule request me kiske sath meeting karni hai ya timing missing hai, to response JSON me `status: "incomplete"` and specific targeted clarification text (`ask_clarification`) diya jata hai.
> - Backend automated regex flow me schedule meetings ko directly `RootMeeting` aur `RootDailyPlan` tables dono me register karta hai.

- **Endpoint**: `POST /api/root-agent/plans`
- **Request Body (JSON)**:
  ```json
  {
    "title": "GYM Cardio Session",
    "description": "Burn 400 calories today.",
    "category": "health", // work | personal | health | meeting | reminder | other
    "plan_date": "2026-07-25",
    "plan_time": "07:00"
  }
  ```
- **Response Example**:
  ```json
  {
    "success": true,
    "plan": {
      "plan_id": "a92e10f3c4b9d8a1",
      "title": "GYM Cardio Session",
      "description": "Burn 400 calories today.",
      "category": "health",
      "plan_date": "2026-07-25",
      "plan_time": "07:00",
      "status": "upcoming",
      "is_completed": false,
      "from_meeting": false
    }
  }
  ```

---

### 4. Toggle Plan Completion
Kisi plan ko complete/pending mark karne ke liye switch toggling perform karta hai.
- **Endpoint**: `PATCH /api/root-agent/plans/{plan_id}/complete`
- **Response Example**:
  ```json
  {
    "success": true,
    "plan": {
      "plan_id": "8b5a7c29d0f41a3e",
      "title": "Morning Team Standup",
      "is_completed": true,
      "status": "completed",
      "completed_at": "2026-07-24T11:45:00"
    }
  }
  ```

---

### 5. Check Time Conflicts (Conflict Warning)
Naya plan create karne se pehle ye check karne ke liye ki us time-frame (±30 minutes window) me koi dusra active plan to scheduled nahi hai.
- **Endpoint**: `GET /api/root-agent/plans/check-conflict`
- **Query Parameters**:
  - `plan_date` (Required): `YYYY-MM-DD`
  - `plan_time` (Required): `HH:MM`
  - `exclude_plan_id` (Optional): `plan_id` (agar existing plan update kar rahe hain to usko skip karne ke liye)
- **Response Example (With Conflict)**:
  ```json
  {
    "has_conflict": true,
    "conflicts": [
      {
        "plan_id": "8b5a7c29d0f41a3e",
        "title": "Morning Team Standup",
        "plan_time": "10:30",
        "category": "work",
        "diff_minutes": 15
      }
    ]
  }
  ```

---

### 6. Auto-Complete Past Plans
Jo plans past/history date-time ke ho chuke hain aur pending reh gaye the unhe batch me completed mark karne ke liye server side trigger helper.
- **Endpoint**: `POST /api/root-agent/plans/auto-complete`
- **Response Example**:
  ```json
  {
    "auto_completed": 3
  }
  ```

---

### 7. Create Plan from Meeting (RAG Trigger)
RAG chat interaction ke dauran jab assistant kisi meeting to scheduler ko recognize karta hai tab is endpoint se programmatically automated plan insert ho jata hai.
- **Endpoint**: `POST /api/root-agent/plans/from-meeting`
- **Request Body (JSON)**:
  ```json
  {
    "title": "Client Pitch Call",
    "description": "Scheduled from Assistant interaction.",
    "plan_date": "2026-07-27",
    "plan_time": "15:00",
    "source_agent_id": "sub_agent_002"
  }
  ```
- **Response Example**:
  ```json
  {
    "success": true,
    "plan": {
      "plan_id": "e931b29a01f56d4c",
      "from_meeting": true
    }
  }
  ```

---

### 8. Book Meeting via Sub-Agent (With AI analysis & Auto-Planner Sync)
Sub-agent chat window me meeting book karne ke liye aur unhe Root Agent ke Daily Planner table me register karne ke liye is endpoint ka use kiya jata hai.
- **Endpoint**: `POST /api/agents/{agent_id}/book-meeting`
- **Request Body (JSON)**:
  ```json
  {
    "name": "Amit Kumar",
    "meeting_time": "2026-07-26T14:00:00",
    "session_id": "session_abc123",
    "device_id": "device_xyz789"
  }
  ```
- **Backend Behavior**:
  1. Ye call pehle database me **RootMeeting** entry schedule karegi.
  2. Phir sub-agent ke current session chat log ko read karegi aur sub-agent ke AI model se dynamically analyze karwa ke custom **Title** aur **Description** summary generate karegi.
  3. In dynamic AI details ko merge karke **RootDailyPlan** table me **`category: "meeting"`** ke sath plan create kar degi jo ki Root Agent ke planner me with Name, Device ID, aur Session ID directly show ho jayega.
  4. Uske baad chat history me automated user aur AI-assistant confirm logs append ho jayenge.

- **Response Example**:
  ```json
  {
    "success": true,
    "meeting_id": "cf12b39a88e512c1"
  }
  ```

---

### 9. Get Booked Dates & Planner Slots
Sub-agent calendar modal open hone se pehle booked slots ko disable/red karne ke liye list retrieve karta hai. Ye endpoint ab simple meeting slots ke sath-sath **Root Agent ke active daily plans** ke slots ko bhi merge karke return karta hai.
- **Endpoint**: `GET /api/agents/{agent_id}/booked-dates`
- **Response Example**:
  ```json
  {
    "booked_dates": [
      "2026-07-26T14:00:00",
      "2026-07-24T10:30:00"
    ]
  }
  ```

---

### 10. Get Daily Plan AI Analysis
Target date ke plans ki cached AI analysis response fetch karne ke liye.
- **Endpoint**: `GET /api/root-agent/plans/analyze`
- **Query Parameters**:
  - `plan_date` (Required): `YYYY-MM-DD`
- **Response Example**:
  ```json
  {
    "analysis_id": "3aef90d8c112abef",
    "client_id": "client_abc123",
    "plan_date": "2026-07-30",
    "summary": "Today's schedule has 5 plans in total (4 completed, 1 pending). Key focus is on the client meeting in the afternoon.",
    "feedback": "You've successfully completed 80% of your tasks today. Excellent time management! For your pending work slot, ensure you limit distractions.",
    "analysis": "High work density in the morning, followed by a lighter afternoon. The schedule is well-balanced with personal goals.",
    "key_points": [
      "Prepare slides for client consultation.",
      "Take a 15-minute screen break before the final session.",
      "Track gym target metrics tonight."
    ],
    "created_at": "2026-07-30T11:00:40Z",
    "updated_at": "2026-07-30T11:00:40Z"
  }
  ```

---

### 11. Run Daily Plan AI Analysis
Target date ke plans ko AI se analyze karne ke liye aur database me update/save karne ke liye.
- **Endpoint**: `POST /api/root-agent/plans/analyze`
- **Request Body (JSON)**:
  ```json
  {
    "plan_date": "2026-07-30"
  }
  ```
- **Backend Behavior**:
  1. Target date ke sabhi plans fetch karta hai.
  2. Completion metrics calculate karta hai (Total plans count, Completed plans, Pending/not completed plans).
  3. In metrics aur plans details ko Root Agent ke AI configuration (provider, API key, model) ke context me pass karta hai.
  4. Output ko database table `root_daily_plan_analyses` me cache/persist karta hai.
  5. JSON analysis structure return karta hai.
- **Response Example**:
  ```json
  {
    "analysis_id": "3aef90d8c112abef",
    "client_id": "client_abc123",
    "plan_date": "2026-07-30",
    "summary": "Today's schedule has 5 plans in total (4 completed, 1 pending).",
    "feedback": "Excellent work completing 4 out of 5 plans today!",
    "analysis": "High productivity level observed. Keep up the momentum!",
    "key_points": [
      "Prepare slides for client consultation."
    ],
    "created_at": "2026-07-30T11:00:40Z",
    "updated_at": "2026-07-30T11:00:40Z"
  }
  ```

---

### 12. Edit Daily Plan
Existing plan, meeting, or reminder ko edit/update karne ke liye.
- **Endpoint**: `PUT /api/root-agent/plans/{plan_id}`
- **Request Body (JSON)**:
  ```json
  {
    "title": "Pushups Session (Optional)",
    "description": "Updated description (Optional)",
    "category": "health (Optional)",
    "plan_date": "2026-08-01 (Optional)",
    "plan_time": "19:30 (Optional)",
    "status": "completed (Optional)"
  }
  ```
- **Response Example**:
  ```json
  {
    "success": true,
    "plan": {
      "plan_id": "a92e10f3c4b9d8a1",
      "title": "Pushups Session",
      "description": "Updated description",
      "category": "health",
      "plan_date": "2026-08-01",
      "plan_time": "19:30",
      "status": "completed",
      "is_completed": true,
      "from_meeting": false
    }
  }
  ```

---

## 🛠️ Frontend Integration Code Example

Aap is code structure ko copy-paste karke apne kisi bhi custom page me integrate kar sakte hain:

### 1. Headers Configuration Helper
```javascript
// Global Session config token mapping helper
const hdrs = () => ({
  'Content-Type': 'application/json',
  'X-App-Token': localStorage.getItem('user_token') || ''
});
```

### 2. Fetch Plans function
```javascript
// Filter standard options: 'all', 'pending', 'upcoming', 'completed'
async function loadPlans(filterType = 'all') {
  try {
    const res = await fetch(`/api/root-agent/plans?filter=${filterType}`, {
      method: 'GET',
      headers: hdrs()
    });
    
    if (!res.ok) throw new Error("Failed to fetch plans");
    const plans = await res.json();
    console.log("Loaded plans list:", plans);
    
    // UI mapping operations
    renderPlansUI(plans);
  } catch (error) {
    console.error("Error loading plans:", error);
  }
}
```

### 3. Create Plan with Conflict Detection
```javascript
async function submitPlan(title, desc, dateVal, timeVal, category = 'work') {
  // Step A: Check for time-slot conflict before creating
  try {
    const conflictRes = await fetch(`/api/root-agent/plans/check-conflict?plan_date=${dateVal}&plan_time=${timeVal}`, {
      method: 'GET',
      headers: hdrs()
    });
    const conflictData = await conflictRes.json();
    
    if (conflictData.has_conflict) {
      const existing = conflictData.conflicts[0];
      const proceed = confirm(`⚠️ Conflict Warning: "${existing.title}" is already scheduled at ${existing.plan_time}. Do you still want to proceed?`);
      if (!proceed) return;
    }
  } catch (e) {
    console.warn("Conflict check bypassed", e);
  }

  // Step B: Submit Plan creation payload
  try {
    const res = await fetch('/api/root-agent/plans', {
      method: 'POST',
      headers: hdrs(),
      body: JSON.stringify({
        title,
        description: desc,
        category,
        plan_date: dateVal,
        plan_time: timeVal
      })
    });
    
    if (!res.ok) {
      const errData = await res.json();
      throw new Error(errData.detail || "Creation failed");
    }
    
    alert("📅 Plan created successfully!");
    loadPlans(); // Reload UI list
  } catch (error) {
    alert("Error creating plan: " + error.message);
  }
}
```

### 4. Toggle Completion Checkbox
```javascript
async function togglePlanCompletion(planId) {
  try {
    const res = await fetch(`/api/root-agent/plans/${planId}/complete`, {
      method: 'PATCH',
      headers: hdrs()
    });
    
    if (!res.ok) throw new Error("Update status failed");
    const data = await res.json();
    console.log("Updated Plan state:", data.plan);
    
    loadPlans(); // UI refresh
  } catch (error) {
    console.error("Error updating plan:", error);
  }
}
```

### 5. Edit Daily Plan Function
```javascript
async function editPlan(planId, updatedFields) {
  try {
    const res = await fetch(`/api/root-agent/plans/${planId}`, {
      method: 'PUT',
      headers: hdrs(),
      body: JSON.stringify(updatedFields)
    });
    
    if (!res.ok) throw new Error("Update plan failed");
    const data = await res.json();
    console.log("Updated Plan details:", data.plan);
    
    loadPlans(); // UI refresh
  } catch (error) {
    console.error("Error editing plan:", error);
  }
}
```

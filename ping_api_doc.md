# 👑 Ping Analytics API Documentation

This document describes the API configuration, parameters, payload responses, and usage details for the **Ping Analytics Dashboard** endpoint. Other developers can integrate this endpoint to query real-time message pings, channel sources, lead outcomes, and comparative growth metrics.

---

## 📡 Endpoint Overview

- **Endpoint URL**: `/api/root-agent/pings/stats`
- **HTTP Method**: `GET`
- **Description**: Computes comparative statistics for user message pings, conversations, source distribution channels, leading customer outcomes, and sub-agent visitor activity.
- **Access Control**: Restricted to the system owner. Requires client verification headers.

### 🔑 Authentication Headers

| Header Name | Type | Description | Required |
| :--- | :--- | :--- | :--- |
| `X-App-Token` | `String` | Secure client app token validated by the server. | Yes |

---

## 📥 Request Parameters (Query)

You can filter stats using preset timeframes or specify custom date ranges. Custom date ranges override presets.

| Parameter | Type | Allowed Values | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `period` | `String` | `today`, `yesterday`, `this_week`, `all` | `today` | Aggregation preset interval. Comparisons are computed vs the previous matching interval. |
| `start_date` | `String` | Format: `YYYY-MM-DD` | *None* | Start boundary for custom stats window (e.g. `2026-08-01`). |
| `end_date` | `String` | Format: `YYYY-MM-DD` | *None* | End boundary for custom stats window (e.g. `2026-08-07`). |

---

## 📤 Response Structure

The endpoint returns a structured JSON object containing metrics with comparative indicators.

### 1. `total_pings` / `conversations`
Contains aggregated totals and comparison values:
- `count` (`Integer`): Current total count in the queried timeframe.
- `growth` (`String`): Percentage change with comparative sign (`↑` / `↓`).
- `growth_text` (`String`): Readable text for display (e.g. `↑ 18% vs Yesterday`).
- `is_positive` (`Boolean`): `true` if growth is zero or positive, `false` if it is negative (used for UI text coloring).

### 2. `sources`
Object containing channel distribution for `whatsapp`, `chats` (web interface), `calls` (voice telephony), and `widgets` (embed elements).
- `count` (`Integer`): Session counts on this channel.
- `growth` (`String`): Percentage change indicator (e.g., `25% ↑` or `15% ↓`).
- `is_positive` (`Boolean`): `true` if growth is positive, `false` if negative.

### 3. `outcomes`
Object containing categorized lead intents or actions:
- `meetings`: Scheduled calendar meetings or sessions classified under meeting request.
- `enquiry`: Sessions classified as sales/marketing leads or product inquiries.
- `support`: Help tickets, debugging, or client support requests.
- `feedback`: Customer experience ratings and comments.
- `others`: Conversations that do not map to standard intents.

### 4. `agents`
A list of objects containing the active sub-agents:
- `agent_id` (`String`): Sub-agent UUID.
- `name` (`String`): Display name of the agent.
- `category` (`String`): Personality type/category.
- `is_active` (`Boolean`): Activity status.
- `is_root` (`Boolean`): True if it is the root executive assistant.
- `total_visitors` (`Integer`): Session visitor count for the selected period.

---

## 📝 Example JSON Response

```json
{
  "total_pings": {
    "count": 147,
    "growth": "↑ 18%",
    "growth_text": "↑ 18% vs Yesterday",
    "is_positive": true
  },
  "conversations": {
    "count": 42,
    "growth": "↑ 12%",
    "growth_text": "↑ 12% vs Yesterday",
    "is_positive": true
  },
  "sources": {
    "whatsapp": {
      "count": 20,
      "growth": "25% ↑",
      "is_positive": true
    },
    "chats": {
      "count": 12,
      "growth": "18% ↓",
      "is_positive": false
    },
    "calls": {
      "count": 8,
      "growth": "25% ↑",
      "is_positive": true
    },
    "widgets": {
      "count": 2,
      "growth": "15% ↓",
      "is_positive": false
    }
  },
  "outcomes": {
    "meetings": {
      "count": 5,
      "growth": "25% ↑",
      "is_positive": true
    },
    "enquiry": {
      "count": 18,
      "growth": "25% ↓",
      "is_positive": false
    },
    "support": {
      "count": 10,
      "growth": "25% ↑",
      "is_positive": true
    },
    "feedback": {
      "count": 4,
      "growth": "25% ↑",
      "is_positive": true
    },
    "others": {
      "count": 5,
      "growth": "25% ↑",
      "is_positive": true
    }
  },
  "agents": [
    {
      "agent_id": "ba0dbe5c60ef0007",
      "name": "Personal Assistant 👑",
      "category": "root_assistant",
      "is_active": true,
      "is_root": true,
      "total_visitors": 2
    },
    {
      "agent_id": "sa_support_agent_1",
      "name": "Customer Support Hero 🤖",
      "category": "support_agent",
      "is_active": true,
      "is_root": false,
      "total_visitors": 40
    }
  ]
}
```

---

## 💻 Client Integration Examples

### Python Example
```python
import requests

url = "http://localhost:8000/api/root-agent/pings/stats"
headers = {
    "X-App-Token": "your_secure_client_app_token_here"
}
params = {
    "period": "this_week" # or specify custom range with start_date / end_date
}

response = requests.get(url, headers=headers, params=params)
if response.status_code == 200:
    stats = response.json()
    print(f"Total Weekly Pings: {stats['total_pings']['count']} ({stats['total_pings']['growth_text']})")
else:
    print(f"Error querying statistics: {response.status_code} - {response.text}")
```

### JavaScript Fetch Example
```javascript
const getPingStats = async (period = 'today') => {
  const url = `/api/root-agent/pings/stats?period=${period}`;
  
  try {
    const response = await fetch(url, {
      method: 'GET',
      headers: {
        'X-App-Token': 'your_secure_client_app_token_here',
        'Accept': 'application/json'
      }
    });
    
    if (response.ok) {
      const stats = await response.json();
      console.log('Overall Conversations:', stats.conversations.count, stats.conversations.growth_text);
      return stats;
    } else {
      console.error('Failed to load stats:', response.status, response.statusText);
    }
  } catch (error) {
    console.error('Network error requesting stats:', error);
  }
};
```

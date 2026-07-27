# MR AI RAG — Reseller API & Sub-User Agent Integration Guide

Welcome to the MR AI RAG API documentation. This guide explains how to leverage the **Reseller Sub-User Architecture** to manage sub-users (clients) and provision AI agents programmatically using developer App Tokens.

---

## 1. Architecture Overview

MR AI RAG supports a hierarchical namespace isolation structure:

```
+--------------------------------------------------+
|          Parent Developer / App Owner            |
|       (X-App-Token: Parent App Token)            |
+------------------------+-------------------------+
                         |
         +---------------+---------------+
         |                               |
         v                               v
+------------------+             +------------------+
|   Sub-User A     |             |   Sub-User B     |
| (X-App-Token A)  |             | (X-App-Token B)  |
+--------+---------+             +--------+---------+
         |                                |
         v                                v
+------------------+             +------------------+
|   Agent A        |             |   Agent B        |
|  (User A Scope)  |             |  (User B Scope)  |
+------------------+             +------------------+
```

- **Parent App (Reseller)**: Identified by the primary `X-App-Token`. The parent can create agents in their own space, or register multiple sub-users (clients) under their namespace.
- **Sub-Users (Clients)**: Independent accounts created by the parent. Once registered, a sub-user can log in to obtain their own session token and create/manage their own isolated agents.

---

## 2. API Endpoints

### A. Register a Sub-User
Use this endpoint with the Parent App Token to create a sub-user. The sub-user is automatically verified and created.

- **Method**: `POST`
- **Path**: `/api/clients/sub-users`
- **Headers**:
  - `Content-Type: application/json`
  - `X-App-Token: <PARENT_APP_TOKEN>`
- **Request Body**:
  ```json
  {
    "name": "John Doe",
    "email": "johndoe@example.com",
    "password": "securepassword123",
    "business_name": "Doe Photography Ltd",
    "website_url": "https://doephotography.com",
    "gst_number": "27AAACD1234E1Z1",
    "pan_number": "ABCDE1234F",
    "user_type": "Prime",
    "mobile_number": "919876543210",
    "city": "Mumbai",
    "pin_code": "400001",
    "address": "123 Creative Studio Street",
    "dob": "1990-01-01",
    "profession": "Photographer",
    "logo_url": "https://doephotography.com/logo.png"
  }
  ```
- **Response (200 OK)**:
  ```json
  {
    "success": true,
    "message": "User created successfully.",
    "user": {
      "client_id": "cli_a8f90bc2...",
      "name": "John Doe",
      "email": "johndoe@example.com",
      "token": "usr_tok_82fa19bc...",
      "is_verified": true,
      "business_name": "Doe Photography Ltd",
      "website_url": "https://doephotography.com",
      "user_type": "Prime",
      ...
    }
  }
  ```
  *Note: The sub-user's session token is returned immediately in the response as `user.token`. You can save this token to authenticate requests on behalf of this sub-user directly.*

---

### B. Sub-User Login
If you need to retrieve a sub-user's token later, you can log them in programmatically using their email and password.

- **Method**: `POST`
- **Path**: `/api/clients/login`
- **Headers**:
  - `Content-Type: application/json`
- **Request Body**:
  ```json
  {
    "email": "johndoe@example.com",
    "password": "securepassword123"
  }
  ```
- **Response (200 OK)**:
  ```json
  {
    "client_id": "cli_a8f90bc2...",
    "token": "usr_tok_82fa19bc...",
    "name": "John Doe",
    "email": "johndoe@example.com",
    "created_at": "2026-07-27T11:49:07",
    "logo_url": "https://doephotography.com/logo.png",
    "message": "Welcome back, John Doe!"
  }
  ```

---

### C. Create an Agent (Parent Scope vs. Sub-User Scope)
To create an agent, call `POST /api/agents`. The namespace in which the agent is created depends on the `X-App-Token` header provided:
- Use the **Parent App Token** to create the agent under the parent account.
- Use the **Sub-User Token** (retrieved from step A or B) to create the agent under that specific sub-user's account.

- **Method**: `POST`
- **Path**: `/api/agents`
- **Headers**:
  - `Content-Type: application/json`
  - `X-App-Token: <AUTHENTICATION_TOKEN>`
- **Request Body**:
  ```json
  {
    "name": "Wedding AI Assistant",
    "description": "Helps users select wedding photography slots and view pricing.",
    "category": "Sales & Booking",
    "personality": "Friendly, professional and creative.",
    "starting_message": "Hello! I am your Wedding Photography Assistant. How can I help you?",
    "voice_config": {
      "provider": "mrai",
      "voice_name": "Friendly"
    },
    "system_config": {
      "provider": "gemini",
      "model": "gemini-3.5-flash",
      "system_prompt": "You are a wedding photography sales assistant. Guide users and suggest they book meetings."
    },
    "customization": {
      "logo_url": "https://doephotography.com/logo.png",
      "author_image_url": "https://doephotography.com/avatar.png",
      "color": "#7f1d1d",
      "template": "template1",
      "whatsapp_number": "919876543210"
    },
    "datastores": []
  }
  ```
- **Response (200 OK)**:
  ```json
  {
    "agent_id": "agent_wedding_123",
    "name": "Wedding AI Assistant",
    "description": "Helps users select wedding photography slots and view pricing.",
    "category": "Sales & Booking",
    ...
  }
  ```

---

## 3. Step-by-Step Integration Tutorial

### Step 1: Create a Sub-User (using Parent Token)
Run the following Python script to create a sub-user under your parent application scope.

```python
import requests

PARENT_TOKEN = "your_parent_app_token_here"
BASE_URL = "http://localhost:8000"  # Update with your deployment URL

headers = {
    "Content-Type": "application/json",
    "X-App-Token": PARENT_TOKEN
}

sub_user_payload = {
    "name": "Jane Designer",
    "email": "jane@example.com",
    "password": "designersuperpass",
    "business_name": "Jane Designs",
    "user_type": "Prime",
    "mobile_number": "919876543211"
}

response = requests.post(f"{BASE_URL}/api/clients/sub-users", json=sub_user_payload, headers=headers)
data = response.json()

if response.status_code == 200:
    sub_user_token = data["user"]["token"]
    sub_user_id = data["user"]["client_id"]
    print(f"✅ Sub-user created successfully!")
    print(f"Client ID: {sub_user_id}")
    print(f"Sub-User Token: {sub_user_token}")
else:
    print(f"❌ Failed to create sub-user: {data.get('detail', response.text)}")
```

### Step 2: Create an Agent as the Sub-User (using Sub-User Token)
Run the following script to create an agent inside the newly registered sub-user's account.

```python
import requests

SUB_USER_TOKEN = "usr_tok_sub_user_token_from_step_1"
BASE_URL = "http://localhost:8000"

headers = {
    "Content-Type": "application/json",
    "X-App-Token": SUB_USER_TOKEN
}

agent_payload = {
    "name": "Jane Assistant",
    "description": "Design Assistant for Jane Designs.",
    "starting_message": "Hello! Let's talk interior design.",
    "voice_config": {
        "provider": "mrai",
        "voice_name": "Friendly"
    },
    "system_config": {
        "provider": "gemini",
        "model": "gemini-3.5-flash",
        "system_prompt": "You are a professional interior design assistant."
    },
    "customization": {
        "color": "#0d5c3a",
        "template": "template2"
    }
}

response = requests.post(f"{BASE_URL}/api/agents", json=agent_payload, headers=headers)
print("Response:", response.json())
```

### Step 3: Create an Agent as the Parent (using Parent Token)
If you want to create an agent in your own application scope (not owned by any sub-user):

```python
import requests

PARENT_TOKEN = "your_parent_app_token_here"
BASE_URL = "http://localhost:8000"

headers = {
    "Content-Type": "application/json",
    "X-App-Token": PARENT_TOKEN
}

agent_payload = {
    "name": "App Level AI Assistant",
    "description": "Global assistant for parent app.",
    "starting_message": "Welcome!",
    "voice_config": {"provider": "mrai", "voice_name": "Friendly"},
    "system_config": {
        "provider": "gemini",
        "model": "gemini-3.5-flash",
        "system_prompt": "Help users query global services."
    },
    "customization": {}
}

response = requests.post(f"{BASE_URL}/api/agents", json=agent_payload, headers=headers)
print("Response:", response.json())
```

---

## 4. Quick Curl Commands

### Create Sub-User
```bash
curl -X POST "https://yourdomain.com/api/clients/sub-users" \
     -H "Content-Type: application/json" \
     -H "X-App-Token: PARENT_APP_TOKEN" \
     -d '{
       "name": "Test User",
       "email": "testuser@domain.com",
       "password": "password123"
     }'
```

### Sub-User Login
```bash
curl -X POST "https://yourdomain.com/api/clients/login" \
     -H "Content-Type: application/json" \
     -d '{
       "email": "testuser@domain.com",
       "password": "password123"
     }'
```

### Create Agent under Sub-User
```bash
curl -X POST "https://yourdomain.com/api/agents" \
     -H "Content-Type: application/json" \
     -H "X-App-Token: SUB_USER_TOKEN" \
     -d '{
       "name": "Support Agent",
       "description": "Customer support",
       "starting_message": "Hi, how can I help?",
       "voice_config": {"voice_name": "Natural", "provider": "mrai"},
       "system_config": {"provider": "gemini", "model": "gemini-3.5-flash", "system_prompt": "Help users."}
     }'
```

# MR AI RAG — Chat Widget & Webhook Integration Guide

This document explains how to integrate the dynamic chat widget (`share-agent-chat.html`) or connect the FastAPI backend endpoints natively into other projects (HTML & React/Vite).

---

## 🚀 1. Standard HTML / Static Webpage Integration

If the other project is a standard HTML website, you have two quick integration options:

### Option A: Embed via iframe (Recommended)
You can host `share-agent-chat.html` on your server/Vercel (e.g., `https://magnifai.diintech.com/share-agent-chat.html`) and embed it in any HTML page using a responsive iframe block.

Add the following code to the target HTML page where you want the chat widget to appear:

```html
<!-- Floating Chat Widget Container -->
<div id="mr-ai-widget-container" style="position:fixed; bottom:20px; right:20px; z-index:999999; width:90px; height:90px; transition: all 0.3s ease;">
  <!-- Toggle Floater Button -->
  <button id="mr-ai-toggle-btn" onclick="toggleMRAIWidget()" style="width:60px; height:60px; border-radius:50%; background:#ff7a00; color:#fff; border:none; box-shadow:0 8px 24px rgba(255,122,0,0.3); cursor:pointer; display:flex; align-items:center; justify-content:center; font-size:24px;">
    🤖
  </button>
  
  <!-- Iframe Chat Window (Hidden by default) -->
  <iframe id="mr-ai-chat-iframe" src="https://magnifai.diintech.com/share-agent-chat.html?id=da3243d9babb9387" 
          style="display:none; width:380px; height:600px; border:none; border-radius:24px; box-shadow:0 12px 40px rgba(0,0,0,0.25); background:#0d0f17; position:absolute; bottom:80px; right:0;">
  </iframe>
</div>

<script>
  let isChatOpen = false;
  function toggleMRAIWidget() {
    const container = document.getElementById('mr-ai-widget-container');
    const iframe = document.getElementById('mr-ai-chat-iframe');
    const btn = document.getElementById('mr-ai-toggle-btn');
    
    isChatOpen = !isChatOpen;
    if (isChatOpen) {
      container.style.width = '380px';
      container.style.height = '680px';
      iframe.style.display = 'block';
      btn.innerHTML = '✕';
    } else {
      container.style.width = '90px';
      container.style.height = '90px';
      iframe.style.display = 'none';
      btn.innerHTML = '🤖';
    }
  }
</script>
```

---

## ⚛️ 2. React / Next.js / Vite Integration

If the target project is built with React, you can integrate it in two ways:

### Option A: React Iframe Component (Fastest)
Create a reusable floating widget component in React:

```jsx
import React, { useState } from 'react';

export default function ChatWidget() {
  const [isOpen, setIsOpen] = useState(false);
  
  // Replace with your Vercel frontend URL and Agent ID
  const chatUrl = "https://magnifai.diintech.com/share-agent-chat.html?id=da3243d9babb9387";

  return (
    <div style={{
      position: 'fixed',
      bottom: '20px',
      right: '20px',
      zIndex: 999999,
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'flex-end'
    }}>
      {/* Chat Window */}
      {isOpen && (
        <iframe
          src={chatUrl}
          title="AI Assistant Chat"
          style={{
            width: '380px',
            height: '580px',
            border: 'none',
            borderRadius: '24px',
            boxShadow: '0 12px 40px rgba(0, 0, 0, 0.25)',
            background: '#0d0f17',
            marginBottom: '15px'
          }}
        />
      )}

      {/* Floater toggle button */}
      <button
        onClick={() => setIsOpen(!isOpen)}
        style={{
          width: '60px',
          height: '60px',
          borderRadius: '50%',
          backgroundColor: '#ff7a00',
          color: '#fff',
          border: 'none',
          boxShadow: '0 8px 24px rgba(255, 122, 0, 0.35)',
          cursor: 'pointer',
          fontSize: '24px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          outline: 'none',
          transition: 'transform 0.2s ease'
        }}
      >
        {isOpen ? '✕' : '🤖'}
      </button>
    </div>
  );
}
```

---

### Option B: Native React UI Chat Component (Integrating via APIs)
If you want to build a completely custom, native React UI instead of an iframe, you can query your FastAPI backend APIs directly.

Here is a fully functional component template in React:

```jsx
import React, { useState, useEffect, useRef } from 'react';

const BACKEND_URL = "http://127.0.0.1:8000"; // Replace with your backend API domain
const AGENT_ID = "da3243d9babb9387";        // Replace with your Agent ID

export default function NativeChat() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [sessionId] = useState(() => 'sess_' + Math.random().toString(36).substring(2, 15));
  const messagesEndRef = useRef(null);

  // Auto-scroll to bottom of conversation
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleSend = async (e) => {
    e.preventDefault();
    if (!input.trim() || isLoading) return;

    const userText = input.trim();
    setInput('');
    setIsLoading(true);

    // 1. Add user message locally
    setMessages(prev => [...prev, { role: 'user', content: userText }]);

    try {
      // 2. Query backend agent endpoint
      const response = await fetch(`${BACKEND_URL}/api/agents/${AGENT_ID}/public-ask`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          question: userText,
          session_id: sessionId,
          device_id: 'react_web_client',
          device_name: 'React Browser App'
        })
      });

      if (!response.ok) throw new Error("API call failed");
      const data = await response.json();

      // 3. Add bot reply to state
      setMessages(prev => [...prev, { role: 'assistant', content: data.answer }]);
    } catch (err) {
      console.error(err);
      setMessages(prev => [...prev, { role: 'assistant', content: "⚠️ Connection error. Please try again." }]);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div style={{ maxWidth: '600px', margin: '50px auto', background: '#13161f', borderRadius: '20px', padding: '20px', color: '#fff', fontFamily: 'sans-serif' }}>
      <h2 style={{ textAlign: 'center', borderBottom: '1px solid #232836', paddingBottom: '15px' }}>AI Assistant</h2>
      
      {/* Scrollable Message Box */}
      <div style={{ height: '400px', overflowY: 'auto', padding: '10px 5px', display: 'flex', flexDirection: 'column', gap: '15px' }}>
        {messages.map((msg, i) => (
          <div key={i} style={{
            alignSelf: msg.role === 'user' ? 'flex-end' : 'flex-start',
            background: msg.role === 'user' ? '#ff7a00' : '#1e2235',
            color: msg.role === 'user' ? '#000' : '#fff',
            padding: '12px 18px',
            borderRadius: msg.role === 'user' ? '20px 20px 4px 20px' : '20px 20px 20px 4px',
            maxWidth: '80%',
            whiteSpace: 'pre-wrap'
          }}>
            {msg.content}
          </div>
        ))}
        {isLoading && <div style={{ alignSelf: 'flex-start', color: '#888', fontStyle: 'italic' }}>typing...</div>}
        <div ref={messagesEndRef} />
      </div>

      {/* Input Dock */}
      <form onSubmit={handleSend} style={{ display: 'flex', gap: '10px', marginTop: '15px' }}>
        <input 
          type="text" 
          value={input} 
          onChange={(e) => setInput(e.target.value)} 
          placeholder="Ask me anything..." 
          style={{ flex: 1, background: '#1e2235', border: '1px solid #232836', borderRadius: '12px', padding: '12px 15px', color: '#fff', outline: 'none' }}
        />
        <button type="submit" style={{ background: '#ff7a00', color: '#000', border: 'none', borderRadius: '12px', padding: '0 20px', fontWeight: 'bold', cursor: 'pointer' }}>
          Send
        </button>
      </form>
    </div>
  );
}
```

---

## ⚙️ 3. Shareable Page Configuration

Ensure you do the following configuration updates in **`share-agent-chat.html`** before uploading it:

1. Open `share-agent-chat.html` in an editor.
2. Locate the configuration setting at the top of the `<script>` tag:
   ```javascript
   const BACKEND_URL = "https://your-backend-api.com";
   ```
3. Replace `"https://your-backend-api.com"` with your actual live FastAPI backend server URL (e.g., `https://test.3rdai.co` or similar). Do NOT add a trailing slash `/`.
4. Upload `share-agent-chat.html` to Vercel/hosting.
5. In your custom iframe src urls, make sure to add `?id=YOUR_AGENT_ID` (for example, `?id=da3243d9babb9387`) to open the exact agent widget page!

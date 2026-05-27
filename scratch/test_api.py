import json
import os
from sqlalchemy.orm import Session
from app.core.database import get_session_local
from app.routes.extension import _jobs, get_pending_job

def test_api():
    token = "clt-2db63e7fbb785339128218bac891c01c35f09e23d28a018e"
    
    # Let's mock a client dict that _require_client would return
    client = {
        "client_id": "client-22838e0042",
        "name": "John Singh",
        "email": "support@vcgurukul.com",
        "token": token
    }
    
    import asyncio
    
    async def run():
        # Call pending job
        res = await get_pending_job(client=client)
        print("API Response for get_pending_job:", res)
        
    asyncio.run(run())

if __name__ == "__main__":
    test_api()

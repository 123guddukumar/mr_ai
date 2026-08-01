"""
MR AI RAG - Vobiz Cloud Telephony Service
"""

import logging
import httpx
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

async def trigger_outbound_call(to_phone: str, callback_url: str) -> dict:
    """
    Triggers an outbound call via Vobiz Voice API.
    Once the recipient answers, Vobiz makes a GET/POST request to `callback_url`
    for XML instructions on what to do next.
    """
    auth_id = settings.VOBIZ_AUTH_ID
    auth_token = settings.VOBIZ_AUTH_TOKEN
    from_phone = settings.VOBIZ_PHONE_NUMBER

    if not auth_id or not auth_token or not from_phone:
        logger.error("Vobiz configuration (Auth ID, Token, or Phone Number) is missing.")
        return {"status": "error", "message": "Vobiz credentials or caller number missing in configuration."}

    # Format the destination phone number (ensure + prefix if not present)
    target_phone = to_phone.strip()
    if not target_phone.startswith("+"):
        # Default to India if no prefix is present (based on +91 context)
        if len(target_phone) == 10:
            target_phone = "+91" + target_phone
        else:
            target_phone = "+" + target_phone

    url = f"https://api.vobiz.ai/api/v1/Account/{auth_id}/Call/"
    headers = {
        "X-Auth-ID": auth_id,
        "X-Auth-Token": auth_token,
        "Content-Type": "application/json"
    }
    payload = {
        "from": from_phone,
        "to": target_phone,
        "answer_url": callback_url
    }

    logger.info(f"Triggering outbound call via Vobiz: from={from_phone} to={target_phone} callback={callback_url}")

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(url, json=payload, headers=headers, timeout=15.0)
            if resp.status_code in (200, 201, 202):
                logger.info(f"Outbound call triggered successfully: Status={resp.status_code}, Body={resp.text}")
                try:
                    detail = resp.json()
                except:
                    detail = {"raw_response": resp.text}
                return {"status": "success", "detail": detail}
            else:
                logger.error(f"Vobiz API returned error status {resp.status_code}: {resp.text}")
                return {"status": "error", "code": resp.status_code, "message": resp.text}
        except Exception as e:
            logger.error(f"Exception while calling Vobiz Call API: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}

from fastapi import FastAPI, Request, HTTPException, status
import uvicorn
import os

app = FastAPI()

# Configuration (Store these in environment variables later)
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "my_secret_token_123")


@app.get("/webhook")
async def verify_webhook(request: Request):
    """
    Handles the Webhook Verification Challenge from Meta.
    """
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode and token:
        if mode == "subscribe" and token == VERIFY_TOKEN:
            print("WEBHOOK_VERIFIED")
            # Return the challenge string as a plain integer/text, not JSON
            return int(challenge)
        else:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Verification token mismatch",
            )

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST, detail="Missing parameters"
    )


import json
from datetime import datetime


@app.post("/webhook")
async def receive_message(request: Request):

    # Get raw body first
    raw_body = await request.body()

    # Convert to JSON
    try:
        data = json.loads(raw_body)
    except Exception:
        print("⚠️ Failed to parse JSON")
        print(raw_body)
        return {"status": "BAD_JSON"}

    # 🔥 FULL RAW LOG
    print("\n================ WEBHOOK EVENT ================")
    print("Timestamp:", datetime.utcnow().isoformat())
    print(json.dumps(data, indent=2))
    print("==============================================\n")

    # optional filtering later
    if data.get("object") == "instagram":
        return {"status": "EVENT_RECEIVED"}

    return {"status": "IGNORED"}


if __name__ == "__main__":
    uvicorn.run(
        "main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)), reload=True
    )

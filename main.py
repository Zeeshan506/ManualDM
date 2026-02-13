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


@app.post("/webhook")
async def receive_message(request: Request):
    """
    Handles incoming webhook events (messages, etc.)
    """
    data = await request.json()

    # Check if this is an Instagram event
    if data.get("object") == "instagram":
        # Process the entries (you usually get a batch)
        for entry in data.get("entry", []):
            messaging_events = entry.get("messaging", [])
            for event in messaging_events:
                print(f"Received event: {event}")
                # TODO: Add logic to save to DB or trigger Stripe flow

        return {"status": "EVENT_RECEIVED"}

    raise HTTPException(status_code=404, detail="Not an Instagram event")


if __name__ == "__main__":
    uvicorn.run(
        "main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)), reload=True
    )

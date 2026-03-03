import json
import asyncio
import os
from fastapi import WebSocket
import redis.asyncio as redis
from typing import Dict, List
from dotenv import load_dotenv
from app.core.redis_config import get_redis_base_url, redis_url_with_db

load_dotenv()  # Load environment variables from .env file

REDIS_URL = redis_url_with_db(get_redis_base_url(), 0)

class ConnectionManager:
    def __init__(self, redis_url: str):
        # Stores connections for THIS specific server worker
        # Format: { lead_id: [websocket1, websocket2] }
        self.active_connections: Dict[int, List[WebSocket]] = {}
        
        # Async Redis client for Pub/Sub
        self.redis = redis.from_url(redis_url, decode_responses=True)
        self.pubsub = self.redis.pubsub()
        self.listen_task = None

    async def start_redis_listener(self):
        """Starts a background task to listen for broadcasts from other workers."""
        await self.pubsub.subscribe("chat_broadcasts")
        self.listen_task = asyncio.create_task(self._listen())

    async def stop_redis_listener(self):
        if self.listen_task:
            self.listen_task.cancel()
            try:
                await self.listen_task
            except asyncio.CancelledError:
                pass
            self.listen_task = None

        await self.pubsub.unsubscribe("chat_broadcasts")
        await self.pubsub.close()

    async def _listen(self):
        """Continuously pulls messages from Redis and routes them to local WebSockets."""
        async for message in self.pubsub.listen():
            if message["type"] == "message":
                data = json.loads(message["data"])
                lead_id = data.get("lead_id")
                
                # If this specific worker has users looking at this lead, send it!
                if lead_id in self.active_connections:
                    for connection in list(self.active_connections[lead_id]):
                        try:
                            await connection.send_json(data["payload"])
                        except Exception:
                            # Handle disconnected clients gracefully
                            self.disconnect(connection, lead_id)

    async def connect(self, websocket: WebSocket, lead_id: int):
        await websocket.accept()
        if lead_id not in self.active_connections:
            self.active_connections[lead_id] = []
        self.active_connections[lead_id].append(websocket)

    def disconnect(self, websocket: WebSocket, lead_id: int):
        if lead_id in self.active_connections:
            if websocket in self.active_connections[lead_id]:
                self.active_connections[lead_id].remove(websocket)
            if not self.active_connections[lead_id]:
                del self.active_connections[lead_id]

    async def publish_message(self, lead_id: int, payload: dict):
        """Called by your REST APIs/Webhooks to announce a new message."""
        message_data = {
            "lead_id": lead_id,
            "payload": payload
        }
        # Publish to Redis so ALL workers hear it
        await self.redis.publish("chat_broadcasts", json.dumps(message_data))

# Create a global instance to be used across your app
manager = ConnectionManager(redis_url=REDIS_URL or "redis://localhost:6379/0")
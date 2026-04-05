#!/usr/bin/env python3
"""
Full E2E test: connect a real WebSocket, publish an event, verify the WS receives it.
Run inside Docker: docker exec trading_bot_app python scripts/test_ws_e2e.py
"""
import asyncio
import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

async def main():
    from core.config import settings
    from core.utils import publish_user_event
    from core.security import create_access_token
    from datetime import timedelta
    import websockets

    # Create a test token for user 1
    token = create_access_token(subject=1, expires_delta=timedelta(minutes=5))
    ws_url = f"ws://127.0.0.1:8000/api/realtime/ws?token={token}"

    print(f"Connecting to {ws_url[:60]}...")

    received_events = []
    
    try:
        async with websockets.connect(ws_url, open_timeout=5) as ws:
            print("✅ WebSocket connected")

            # Give the server a moment to set up Redis subscription
            await asyncio.sleep(1)

            # Publish a test event for user 1
            print("Publishing session:login_request for user 1...")
            await publish_user_event(1, "session:login_request", {
                "request_id": "e2e-test-123",
                "device_name": "E2E Test Device",
                "device_ip": "10.0.0.1",
                "expires_at": "2026-04-06T00:00:00",
            })

            # Also publish session:revoked
            print("Publishing session:revoked for user 1...")
            await publish_user_event(1, "session:revoked", {"action": "check_session"})

            # Wait for messages (timeout 5s)
            for _ in range(50):
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=0.1)
                    data = json.loads(msg)
                    if data.get("type") == "heartbeat":
                        continue
                    received_events.append(data)
                    print(f"  Received: type={data.get('type')}")
                    if len(received_events) >= 2:
                        break
                except asyncio.TimeoutError:
                    continue

    except Exception as e:
        print(f"❌ WebSocket E2E test error: {e}")
        sys.exit(1)

    # Verify
    types = [e.get("type") for e in received_events]
    
    if "session:login_request" not in types:
        print("❌ FAILED: session:login_request not received via WebSocket")
        sys.exit(1)
    
    if "session:revoked" not in types:
        print("❌ FAILED: session:revoked not received via WebSocket")
        sys.exit(1)

    # Check payload
    login_event = next(e for e in received_events if e["type"] == "session:login_request")
    if login_event["data"].get("request_id") != "e2e-test-123":
        print(f"❌ FAILED: wrong payload: {login_event}")
        sys.exit(1)

    print("=" * 50)
    print("✅ ALL E2E TESTS PASSED")
    print("  - session:login_request delivered via WebSocket")
    print("  - session:revoked delivered via WebSocket")
    print("  - Payload integrity verified")
    sys.exit(0)

if __name__ == "__main__":
    asyncio.run(main())

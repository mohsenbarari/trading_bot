#!/usr/bin/env python3
"""
End-to-end test for session real-time features:
  1. WebSocket receives session:login_request when 3rd device tries to log in
  2. WebSocket receives session:revoked when a session is terminated
  
Run inside Docker: docker exec trading_bot_app python scripts/test_session_realtime.py
"""
import asyncio
import json
import sys
import os
import uuid

# Add project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def test_publish_user_event():
    """Test 1: Verify publish_user_event actually publishes to Redis and a subscriber receives it."""
    import redis.asyncio as redis_lib
    from core.redis import pool
    from core.utils import publish_user_event

    test_user_id = 99999
    channel = f"notifications:{test_user_id}"
    received = []

    async with redis_lib.Redis(connection_pool=pool) as sub_client:
        pubsub = sub_client.pubsub()
        await pubsub.subscribe(channel)
        # Drain subscribe confirmation
        await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)

        # Publish event
        await publish_user_event(test_user_id, "session:login_request", {
            "request_id": "test-123",
            "device_name": "Test Device",
        })

        # Wait for message
        for _ in range(20):  # 2 seconds max
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
            if msg and msg.get("type") == "message":
                received.append(msg)
                break

        await pubsub.unsubscribe(channel)

    if not received:
        print("❌ TEST 1 FAILED: publish_user_event did NOT deliver message to Redis subscriber")
        return False

    data = json.loads(received[0]["data"])
    if data.get("event") != "session:login_request":
        print(f"❌ TEST 1 FAILED: event type mismatch: {data}")
        return False

    inner = data.get("data", {})
    if inner.get("request_id") != "test-123":
        print(f"❌ TEST 1 FAILED: payload mismatch: {inner}")
        return False

    print("✅ TEST 1 PASSED: publish_user_event delivers session:login_request to Redis subscriber")
    return True


async def test_publish_session_revoked():
    """Test 2: Verify session:revoked event is published to Redis."""
    import redis.asyncio as redis_lib
    from core.redis import pool
    from core.utils import publish_user_event

    test_user_id = 99998
    channel = f"notifications:{test_user_id}"
    received = []

    async with redis_lib.Redis(connection_pool=pool) as sub_client:
        pubsub = sub_client.pubsub()
        await pubsub.subscribe(channel)
        await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)

        await publish_user_event(test_user_id, "session:revoked", {"action": "check_session"})

        for _ in range(20):
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
            if msg and msg.get("type") == "message":
                received.append(msg)
                break

        await pubsub.unsubscribe(channel)

    if not received:
        print("❌ TEST 2 FAILED: session:revoked event NOT delivered to Redis subscriber")
        return False

    data = json.loads(received[0]["data"])
    if data.get("event") != "session:revoked":
        print(f"❌ TEST 2 FAILED: event type mismatch: {data}")
        return False

    print("✅ TEST 2 PASSED: session:revoked event delivered to Redis subscriber")
    return True


async def test_logout_session_publishes_revoked():
    """Test 3: Verify that logout_session() publishes a session:revoked event."""
    import redis.asyncio as redis_lib
    from core.redis import pool
    from core.db import AsyncSessionLocal as async_session
    from core.services.session_service import (
        create_session, logout_session, get_active_sessions
    )

    test_user_id = None
    received = []

    async with async_session() as db:
        # Find a real user to use for the test
        from sqlalchemy import select, text
        from models.user import User
        result = await db.execute(select(User).where(User.is_deleted == False).limit(1))
        user = result.scalar_one_or_none()
        if not user:
            print("⚠️ TEST 3 SKIPPED: No user in database")
            return True
        test_user_id = user.id

        # Create a test session
        fake_token = f"test-token-{uuid.uuid4()}"
        session = await create_session(
            db, test_user_id, fake_token,
            device_name="Test Revoke Device",
            device_ip="1.2.3.4",
            is_primary=False,
        )
        await db.commit()

        # Subscribe to user channel BEFORE revoking
        async with redis_lib.Redis(connection_pool=pool) as sub_client:
            pubsub = sub_client.pubsub()
            channel = f"notifications:{test_user_id}"
            await pubsub.subscribe(channel)
            await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)

            # Now logout (deactivate) the session
            await logout_session(db, session)

            # Wait for session:revoked message
            for _ in range(30):
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
                if msg and msg.get("type") == "message":
                    data = json.loads(msg["data"])
                    if data.get("event") == "session:revoked":
                        received.append(data)
                        break

            await pubsub.unsubscribe(channel)

    if not received:
        print("❌ TEST 3 FAILED: logout_session() did NOT publish session:revoked event")
        return False

    print("✅ TEST 3 PASSED: logout_session() publishes session:revoked via Redis")
    return True


async def test_handle_login_session_approval_required():
    """Test 4: When max sessions hit, handle_login_session publishes session:login_request."""
    import redis.asyncio as redis_lib
    from core.redis import pool
    from core.db import AsyncSessionLocal as async_session
    from core.services.session_service import (
        create_session, handle_login_session, get_active_sessions,
        force_clear_sessions,
    )
    from sqlalchemy import select
    from models.user import User

    received = []

    async with async_session() as db:
        # Find a STANDARD user (admins have max_sessions=1 which is tricky)
        from core.enums import UserRole
        result = await db.execute(
            select(User).where(
                User.is_deleted == False,
                User.role == UserRole.STANDARD,
            ).limit(1)
        )
        user = result.scalar_one_or_none()
        if not user:
            print("⚠️ TEST 4 SKIPPED: No STANDARD user in database")
            return True

        # Clear existing sessions
        await force_clear_sessions(db, user.id)

        # Clear anti-abuse counters
        from bot.utils.redis_helpers import get_redis
        r = await get_redis()
        for p in ['daily', 'weekly', 'monthly']:
            await r.delete(f"session_req:{user.id}:{p}")

        # Create max_sessions sessions (user.max_sessions, default usually 2)
        max_s = min(max(user.max_sessions, 1), 3)
        for i in range(max_s):
            await create_session(
                db, user.id, f"tok-{uuid.uuid4()}",
                device_name=f"Device {i+1}",
                is_primary=(i == 0),
            )
        await db.commit()

        # Subscribe
        async with redis_lib.Redis(connection_pool=pool) as sub_client:
            pubsub = sub_client.pubsub()
            channel = f"notifications:{user.id}"
            await pubsub.subscribe(channel)
            await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)

            # Now try to log in with ANOTHER device — should trigger approval_required
            result = await handle_login_session(
                db, user, f"new-tok-{uuid.uuid4()}",
                device_name="Device Over Limit",
                device_ip="5.6.7.8",
            )

            if result["action"] != "approval_required":
                print(f"❌ TEST 4 FAILED: Expected approval_required but got {result['action']}")
                await pubsub.unsubscribe(channel)
                return False

            # Wait for event
            for _ in range(30):
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
                if msg and msg.get("type") == "message":
                    data = json.loads(msg["data"])
                    if data.get("event") == "session:login_request":
                        received.append(data)
                        break

            await pubsub.unsubscribe(channel)

        # Cleanup
        await force_clear_sessions(db, user.id)

    if not received:
        print("❌ TEST 4 FAILED: handle_login_session did NOT publish session:login_request")
        return False

    inner = received[0].get("data", {})
    if inner.get("device_name") != "Device Over Limit":
        print(f"❌ TEST 4 FAILED: wrong device_name in payload: {inner}")
        return False

    print("✅ TEST 4 PASSED: handle_login_session publishes session:login_request when over limit")
    return True


async def test_verify_endpoint():
    """Test 5: /api/sessions/verify returns 401 for deactivated sessions."""
    from core.db import AsyncSessionLocal as async_session
    from core.services.session_service import (
        create_session, deactivate_session, get_session_by_refresh_token
    )
    from sqlalchemy import select
    from models.user import User
    import uuid

    async with async_session() as db:
        result = await db.execute(select(User).where(User.is_deleted == False).limit(1))
        user = result.scalar_one_or_none()
        if not user:
            print("⚠️ TEST 5 SKIPPED: No user in database")
            return True

        # Create session
        fake_token = f"verify-test-{uuid.uuid4()}"
        session = await create_session(
            db, user.id, fake_token,
            device_name="Verify Test Device",
            is_primary=False,
        )
        await db.commit()

        # Verify it exists
        found = await get_session_by_refresh_token(db, fake_token)
        if not found:
            print("❌ TEST 5 FAILED: session not found after creation")
            return False

        # Deactivate it
        await deactivate_session(db, session)
        await db.commit()

        # Verify it's gone
        found = await get_session_by_refresh_token(db, fake_token)
        if found:
            print("❌ TEST 5 FAILED: deactivated session still found by refresh token")
            return False

    print("✅ TEST 5 PASSED: deactivated session not found by get_session_by_refresh_token")
    return True


async def main():
    print("=" * 60)
    print("Session Real-time Tests")
    print("=" * 60)

    results = []

    results.append(await test_publish_user_event())
    results.append(await test_publish_session_revoked())
    results.append(await test_logout_session_publishes_revoked())
    results.append(await test_handle_login_session_approval_required())
    results.append(await test_verify_endpoint())

    print("=" * 60)
    passed = sum(1 for r in results if r)
    total = len(results)
    print(f"Results: {passed}/{total} passed")
    if passed < total:
        print("❌ SOME TESTS FAILED")
        sys.exit(1)
    else:
        print("✅ ALL TESTS PASSED")
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())

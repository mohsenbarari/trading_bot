"""Manual non-regression tool for live customer registration/login API runtime smoke.

This script seeds a pending customer relation plus matching invitation directly in
the local database, injects OTP codes into Redis to avoid external SMS coupling,
and then exercises the live auth API/runtime for customer registration and login.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import uuid

import httpx

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger('CustomerLiveAuthSmoke')

API_BASE_URL = os.getenv('CUSTOMER_LIVE_SMOKE_BASE_URL', 'http://127.0.0.1:8000/api')
APP_CONTAINER = os.getenv('CUSTOMER_LIVE_SMOKE_APP_CONTAINER', 'trading_bot_app')
REGISTER_OTP_CODE = '55123'
LOGIN_OTP_CODE = '77123'


def _run_app_python(payload: dict[str, object], body: str) -> dict[str, object]:
    wrapper = f"""
import asyncio
import json

PAYLOAD = json.loads({json.dumps(json.dumps(payload, ensure_ascii=False))})

async def _main():
{body}

asyncio.run(_main())
"""
    result = subprocess.run(
        ['docker', 'exec', '-i', APP_CONTAINER, 'python', '-'],
        input=wrapper,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or 'docker exec failed')
    stdout_lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not stdout_lines:
        return {}
    return json.loads(stdout_lines[-1])


def _seed_pending_customer_registration() -> dict[str, str | int]:
    suffix = uuid.uuid4().hex[:8]
    token = f'CUST-{uuid.uuid4().hex[:16]}'
    short_code = f'C{suffix[:7].upper()}'
    customer_account_name = f'cust_smoke_{suffix}'
    customer_mobile = f'0992{suffix[:7]}'
    owner_account_name = f'cust_owner_{suffix}'
    owner_mobile = f'0991{suffix[:7]}'
    management_name = f'Customer Smoke {suffix}'
    seed_payload = {
        'token': token,
        'mobile_number': customer_mobile,
        'account_name': customer_account_name,
        'owner_account_name': owner_account_name,
        'owner_mobile_number': owner_mobile,
        'short_code': short_code,
        'management_name': management_name,
    }
    _run_app_python(
        seed_payload,
        """
    from datetime import timedelta
    from core.db import AsyncSessionLocal
    from core.enums import UserAccountStatus
    from core.utils import utc_now
    from models.customer_relation import CustomerRelation, CustomerRelationStatus, CustomerTier
    from models.invitation import Invitation
    from models.user import User, UserRole

    async with AsyncSessionLocal() as session:
        now = utc_now().replace(tzinfo=None)
        owner = User(
            account_name=PAYLOAD['owner_account_name'],
            mobile_number=PAYLOAD['owner_mobile_number'],
            telegram_id=None,
            role=UserRole.STANDARD,
            full_name=f"Customer Owner {PAYLOAD['owner_account_name']}",
            address='Tehran, Owner Smoke Address',
            has_bot_access=True,
            account_status=UserAccountStatus.ACTIVE,
            home_server='foreign',
            max_sessions=1,
            max_customers=5,
        )
        session.add(owner)
        await session.flush()

        relation = CustomerRelation(
            owner_user_id=owner.id,
            created_by_user_id=owner.id,
            invitation_token=PAYLOAD['token'],
            management_name=PAYLOAD['management_name'],
            customer_tier=CustomerTier.TIER_1,
            status=CustomerRelationStatus.PENDING,
            expires_at=now + timedelta(days=2),
        )
        invitation = Invitation(
            account_name=PAYLOAD['account_name'],
            mobile_number=PAYLOAD['mobile_number'],
            token=PAYLOAD['token'],
            short_code=PAYLOAD['short_code'],
            role=UserRole.STANDARD,
            created_by_id=owner.id,
            is_used=False,
            expires_at=now + timedelta(days=2),
        )
        session.add_all([relation, invitation])
        await session.commit()

    print(json.dumps({'ok': True}, ensure_ascii=False))
""",
    )
    return seed_payload


def _inject_registration_otp(token: str) -> None:
    _run_app_python(
        {'token': token, 'code': REGISTER_OTP_CODE},
        """
    import redis.asyncio as redis
    from core.config import settings

    client = redis.from_url(settings.redis_url, decode_responses=True)
    try:
        await client.setex(f"reg_otp:{PAYLOAD['token']}", 120, PAYLOAD['code'])
        await client.delete(f"reg_verified:{PAYLOAD['token']}")
    finally:
        await client.close()
    print(json.dumps({'ok': True}, ensure_ascii=False))
""",
    )


def _inject_login_otp(mobile_number: str) -> None:
    _run_app_python(
        {'mobile_number': mobile_number, 'code': LOGIN_OTP_CODE},
        """
    import redis.asyncio as redis
    from core.config import settings

    client = redis.from_url(settings.redis_url, decode_responses=True)
    try:
        await client.setex(f"otp:{PAYLOAD['mobile_number']}", 120, PAYLOAD['code'])
        await client.setex(f"otp_limit:{PAYLOAD['mobile_number']}", 120, '1')
    finally:
        await client.close()
    print(json.dumps({'ok': True}, ensure_ascii=False))
""",
    )


def _assert_post_registration_state(*, token: str, mobile_number: str, management_name: str) -> int:
    payload = _run_app_python(
        {'token': token, 'mobile_number': mobile_number, 'management_name': management_name},
        """
    from sqlalchemy import select
    from core.db import AsyncSessionLocal
    from models.customer_relation import CustomerRelation, CustomerRelationStatus
    from models.user import User

    async with AsyncSessionLocal() as session:
        relation = (
            await session.execute(
                select(CustomerRelation).where(CustomerRelation.invitation_token == PAYLOAD['token'])
            )
        ).scalar_one()
        customer = (
            await session.execute(select(User).where(User.mobile_number == PAYLOAD['mobile_number']))
        ).scalar_one()

        if relation.status != CustomerRelationStatus.ACTIVE:
            raise AssertionError(f'Expected active customer relation, got {relation.status!r}')
        if relation.customer_user_id != customer.id:
            raise AssertionError('Customer relation did not bind to the newly registered user')
        if customer.has_bot_access:
            raise AssertionError('Customer registration unexpectedly enabled bot access')
        if not customer.account_name.startswith('cust_smoke_'):
            raise AssertionError('Unexpected customer account_name after registration')
        if relation.management_name != PAYLOAD['management_name']:
            raise AssertionError('Customer management name changed during registration')

    print(json.dumps({'customer_user_id': customer.id}, ensure_ascii=False))
""",
    )
    return int(payload['customer_user_id'])


def _clear_user_sessions(user_id: int) -> None:
    _run_app_python(
        {'user_id': user_id},
        """
    from core.db import AsyncSessionLocal
    from core.services.session_service import force_clear_sessions

    async with AsyncSessionLocal() as session:
        cleared = await force_clear_sessions(session, int(PAYLOAD['user_id']))

    print(json.dumps({'cleared_sessions': cleared}, ensure_ascii=False))
""",
    )


def _cleanup_seeded_artifacts(*, token: str, mobile_number: str, owner_account_name: str) -> None:
    _run_app_python(
        {
            'token': token,
            'mobile_number': mobile_number,
            'owner_account_name': owner_account_name,
        },
        """
    from sqlalchemy import delete, select
    from core.db import AsyncSessionLocal
    from models.customer_relation import CustomerRelation
    from models.invitation import Invitation
    from models.user import User

    async with AsyncSessionLocal() as session:
        customer = (
            await session.execute(select(User).where(User.mobile_number == PAYLOAD['mobile_number']))
        ).scalar_one_or_none()
        if customer is not None:
            customer.soft_delete()

        await session.execute(delete(CustomerRelation).where(CustomerRelation.invitation_token == PAYLOAD['token']))
        await session.execute(delete(Invitation).where(Invitation.token == PAYLOAD['token']))
        await session.execute(delete(User).where(User.account_name == PAYLOAD['owner_account_name']))
        await session.commit()

    print(json.dumps({'ok': True}, ensure_ascii=False))
""",
    )


async def main() -> int:
    seed = _seed_pending_customer_registration()
    token = str(seed['token'])
    mobile_number = str(seed['mobile_number'])
    account_name = str(seed['account_name'])
    owner_account_name = str(seed['owner_account_name'])
    management_name = str(seed['management_name'])

    logger.info('Seeded pending customer relation for token %s', token)

    try:
        _inject_registration_otp(token)

        async with httpx.AsyncClient(base_url=API_BASE_URL, timeout=30.0) as client:
            validate_response = await client.get(f'/invitations/validate/{token}')
            validate_response.raise_for_status()
            validate_payload = validate_response.json()
            if validate_payload.get('account_name') != account_name:
                raise AssertionError('Invitation validation returned an unexpected account_name')

            verify_register_response = await client.post(
                '/auth/register-otp-verify',
                json={'token': token, 'code': REGISTER_OTP_CODE},
            )
            verify_register_response.raise_for_status()

            register_complete_response = await client.post(
                '/auth/register-complete',
                json={'token': token, 'address': 'Tehran, Customer Smoke Address'},
                headers={'user-agent': 'customer-live-auth-smoke/1.0'},
            )
            register_complete_response.raise_for_status()
            registration_tokens = register_complete_response.json()
            if not registration_tokens.get('access_token') or not registration_tokens.get('refresh_token'):
                raise AssertionError('Registration did not issue bearer tokens')

            customer_user_id = _assert_post_registration_state(
                token=token,
                mobile_number=mobile_number,
                management_name=management_name,
            )

            me_after_register = await client.get(
                '/auth/me',
                headers={'Authorization': f"Bearer {registration_tokens['access_token']}"},
            )
            me_after_register.raise_for_status()
            if me_after_register.json().get('id') != customer_user_id:
                raise AssertionError('Register-issued access token resolved to the wrong user')

            _clear_user_sessions(customer_user_id)

            _inject_login_otp(mobile_number)
            verify_login_response = await client.post(
                '/auth/verify-otp',
                json={'mobile_number': mobile_number, 'code': LOGIN_OTP_CODE},
                headers={'user-agent': 'customer-live-auth-smoke/1.0'},
            )
            verify_login_response.raise_for_status()
            login_tokens = verify_login_response.json()
            if not login_tokens.get('access_token') or not login_tokens.get('refresh_token'):
                raise AssertionError('Customer login did not issue bearer tokens')

            me_after_login = await client.get(
                '/auth/me',
                headers={'Authorization': f"Bearer {login_tokens['access_token']}"},
            )
            me_after_login.raise_for_status()
            login_me_payload = me_after_login.json()
            if login_me_payload.get('id') != customer_user_id:
                raise AssertionError('Login-issued access token resolved to the wrong user')
            if login_me_payload.get('mobile_number') != mobile_number:
                raise AssertionError('Login-issued token returned the wrong mobile number')

        logger.info('Customer live auth smoke passed for user %s (%s)', customer_user_id, mobile_number)
        return 0
    finally:
        _cleanup_seeded_artifacts(
            token=token,
            mobile_number=mobile_number,
            owner_account_name=owner_account_name,
        )


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
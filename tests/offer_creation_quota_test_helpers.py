from types import SimpleNamespace


async def bypass_local_offer_quota(db, command, _policy):
    """Keep caller tests focused; PostgreSQL tests exercise the real quota lock."""
    active_count = int(getattr(db, "last_scalar_value", 0) or 0)
    shared_count = getattr(db, "shared_count", None)
    if isinstance(shared_count, dict):
        active_count = int(shared_count.get("value", active_count) or 0)
    owner = SimpleNamespace(
        id=command.owner_user_id,
        channel_messages_count=0,
    )
    return owner, None, active_count

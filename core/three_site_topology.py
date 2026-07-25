"""Canonical non-secret bindings for the production three-site topology.

This module deliberately contains only role identities and public infrastructure
addresses.  Credentials, certificates, and mutable routing decisions remain in
owner-only runtime artifacts.  Keeping these bindings in one importable source
prevents a failover tool, a manifest verifier, and a staging-isolation gate
from silently using different Witness identities.
"""

from __future__ import annotations

from core.runtime_sites import SITE_BOT_FI, SITE_WEBAPP_FI, SITE_WEBAPP_IR, SITE_WITNESS


BOT_FI_HOST = "65.109.216.187"
WEBAPP_FI_HOST = "65.109.220.59"
WEBAPP_IR_HOST = "95.38.164.29"
PRODUCTION_WITNESS_HOST = "37.152.191.11"
# Kept only as an immutable rollback/reference boundary; it is not a writer.
ROLLBACK_WITNESS_HOST = "185.231.182.6"
RETIRED_WITNESS_HOST = "185.206.95.94"

NORMAL_WRITER_SITE = SITE_WEBAPP_FI
STANDBY_SITE = SITE_WEBAPP_IR
WITNESS_SITE = SITE_WITNESS

ROLE_HOSTS = {
    SITE_BOT_FI: BOT_FI_HOST,
    SITE_WEBAPP_FI: WEBAPP_FI_HOST,
    SITE_WEBAPP_IR: WEBAPP_IR_HOST,
    SITE_WITNESS: PRODUCTION_WITNESS_HOST,
}

PRODUCTION_BOUNDARY_HOSTS = frozenset({
    *ROLE_HOSTS.values(),
    RETIRED_WITNESS_HOST,
    ROLLBACK_WITNESS_HOST,
})

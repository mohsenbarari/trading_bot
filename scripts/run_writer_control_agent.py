#!/usr/bin/env python3
"""Run the isolated WebApp writer-lease control process."""

from __future__ import annotations

import asyncio
import logging
import sys

from core.config import settings
from core.db import verify_three_site_database_role_bindings
from core.logging_config import configure_logging
from core.runtime_identity import resolve_runtime_identity
from core.writer_witness_client import (
    writer_witness_client_configuration_reasons,
    writer_witness_renewal_loop,
)


logger = logging.getLogger(__name__)


class WriterControlAgentConfigurationError(RuntimeError):
    """Raised when the isolated control process is wired unsafely."""


def validate_writer_control_agent_configuration() -> None:
    service = str(settings.trading_bot_service or "").strip().lower()
    if service != "writer_control_agent":
        raise WriterControlAgentConfigurationError(
            "writer control agent requires TRADING_BOT_SERVICE=writer_control_agent"
        )
    identity = resolve_runtime_identity(settings)
    if not identity.is_webapp_site:
        raise WriterControlAgentConfigurationError(
            "writer control agent must run on a WebApp physical site"
        )
    if not settings.writer_witness_required:
        raise WriterControlAgentConfigurationError("writer witness enforcement is required")
    if not settings.writer_witness_auto_renew_enabled:
        raise WriterControlAgentConfigurationError("automatic witness renewal is required")
    if not str(settings.dr_control_database_url or "").strip():
        raise WriterControlAgentConfigurationError("control database URL is required")
    reasons = writer_witness_client_configuration_reasons(identity)
    if reasons:
        raise WriterControlAgentConfigurationError(
            "unsafe writer witness client configuration: " + ",".join(reasons)
        )


async def main() -> None:
    validate_writer_control_agent_configuration()
    await verify_three_site_database_role_bindings()
    await writer_witness_renewal_loop()


if __name__ == "__main__":
    configure_logging("writer-control-agent")
    try:
        asyncio.run(main())
    except WriterControlAgentConfigurationError as exc:
        logger.critical("Writer control agent configuration rejected: %s", exc)
        raise SystemExit(78) from exc
    except KeyboardInterrupt:
        raise SystemExit(0)
    except Exception as exc:
        logger.critical("Writer control agent stopped", exc_info=True)
        raise SystemExit(1) from exc

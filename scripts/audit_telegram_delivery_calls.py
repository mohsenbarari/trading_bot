#!/usr/bin/env python3
"""Build a deterministic inventory of runtime Telegram delivery callsites.

The audit is intentionally static and conservative.  It does not claim that a
callsite is reachable in a particular deployment; it records every syntactic
delivery boundary under api/, bot/, and core/ and classifies the ownership
contract that must make the call safe.
"""
from __future__ import annotations

import argparse
import ast
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sys
from typing import Iterable, Sequence


ROOT_NAMES = ("api", "bot", "core")

BOT_API_METHODS = frozenset(
    {
        "answer_callback_query",
        "ban_chat_member",
        "copy_message",
        "delete_message",
        "edit_message_reply_markup",
        "edit_message_text",
        "forward_message",
        "send_animation",
        "send_document",
        "send_message",
        "send_photo",
        "send_sticker",
        "send_video",
        "unban_chat_member",
    }
)
GATEWAY_METHODS = BOT_API_METHODS | frozenset(
    {
        "post_telegram_method",
        "post_telegram_method_sync",
        "send_message_sync",
    }
)
AIROGRAM_CONVENIENCE_METHODS = frozenset(
    {
        "answer",
        "delete",
        "edit_reply_markup",
        "edit_text",
        "reply",
    }
)
CALLABLE_GATEWAY_NAMES = frozenset(
    {
        "gateway_send",
        "send_offer_to_channel",
    }
)
MEMORY_TIMER_HINTS = (
    "cleanup",
    "delete",
    "delayed_removal",
    "notify_remote_trade_success",
    "safe_delete",
    "suggestion_pending_reset",
)

QUEUE_NATIVE_FILES = frozenset(
    {
        "core/telegram_delivery_credentials.py",
    }
)
TRANSPORT_FILES = frozenset({"core/telegram_gateway.py"})
OTP_EXEMPT_FILES = frozenset({"core/services/telegram_otp_delivery_service.py"})
LEGACY_OWNER_FILES = frozenset(
    {
        "core/offer_publication_worker.py",
        "core/telegram_admin_broadcast_worker.py",
        "core/telegram_notification_outbox_worker.py",
        "core/trade_delivery_worker.py",
    }
)

# Stage 3 is not yet at zero direct callsites.  These ceilings make the
# current inventory monotonic: conversions may reduce a category, while a new
# bypass must update the reviewed baseline deliberately.
REMAINING_DISPOSITION_BUDGETS = {
    "remaining_business_direct": 0,
    "remaining_callback_direct": 0,
    "remaining_cleanup_direct": 13,
    "remaining_interactive_direct": 272,
    "remaining_memory_timer": 5,
}


@dataclass(frozen=True, slots=True)
class TelegramDeliveryCallsite:
    path: str
    line: int
    column: int
    scope: str
    callee: str
    kind: str
    disposition: str
    evidence: str

    @property
    def identity(self) -> str:
        return f"{self.path}:{self.line}:{self.column}:{self.callee}"


def _dotted_name(node: ast.AST | None) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _terminal_name(callee: str) -> str:
    return callee.rsplit(".", 1)[-1]


def _is_aiogram_convenience_call(path: str, callee: str) -> bool:
    if not path.startswith("bot/"):
        return False
    terminal = _terminal_name(callee)
    if terminal not in AIROGRAM_CONVENIENCE_METHODS:
        return False
    parts = set(callee.split("."))
    return bool(
        parts
        & {
            "callback",
            "event",
            "join_request",
            "message",
            "query",
        }
    ) or terminal in {"answer", "edit_reply_markup", "edit_text", "reply"}


def _call_kind(path: str, callee: str) -> str | None:
    terminal = _terminal_name(callee)
    if terminal in {"send_telegram_message", "send_telegram_notification"}:
        return "notification_helper"
    if terminal in CALLABLE_GATEWAY_NAMES:
        return "callable_gateway"
    if terminal in BOT_API_METHODS:
        if terminal in {"ban_chat_member", "unban_chat_member"}:
            return "membership_control"
        if "telegram_gateway" in callee:
            return "gateway"
        return "bot_api"
    if "telegram_gateway" in callee and terminal in GATEWAY_METHODS:
        return "gateway"
    if _is_aiogram_convenience_call(path, callee):
        parts = callee.split(".")
        if terminal == "answer" and len(parts) >= 2 and parts[-2] in {
            "callback",
            "query",
        }:
            return "callback_answer"
        return "interactive_message"
    return None


def _scope_name(nodes: Sequence[ast.AST]) -> str:
    names = [
        node.name
        for node in nodes
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
    ]
    return ".".join(names) if names else "<module>"


def _scope_text(source_lines: Sequence[str], nodes: Sequence[ast.AST]) -> str:
    function_nodes = [
        node
        for node in nodes
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
    ]
    if not function_nodes:
        return ""
    outer = function_nodes[0]
    return "".join(source_lines[outer.lineno - 1 : outer.end_lineno])


def _has_queue_mode_guard(scope_text: str) -> bool:
    return (
        "TelegramDeliveryRuntimeMode.QUEUE_V1" in scope_text
        and "configured_telegram_delivery_runtime" in scope_text
    )


def _classify(
    *,
    path: str,
    callee: str,
    kind: str,
    scope: str,
    scope_text: str,
) -> tuple[str, str]:
    terminal = _terminal_name(callee)
    if path in TRANSPORT_FILES:
        return "transport_wrapper", "Bot API transport definition"
    if path in QUEUE_NATIVE_FILES:
        return "queue_execution", "shared queue credential-bound gateway"
    if path in OTP_EXEMPT_FILES:
        return "durable_exempt", "OTP stays on the signed short-lived transport"
    if (
        path == "core/notifications.py"
        and scope.endswith("send_telegram_message")
        and "validate_legacy_telegram_otp_relay" in scope_text
    ):
        return "durable_exempt", "legacy sender accepts only the strict OTP envelope"
    if path == "api/routers/auth.py" and scope.endswith("request_otp"):
        return "durable_exempt", "registration OTP uses the signed short-lived transport"
    if (
        path == "api/routers/sync.py"
        and scope.endswith("receive_sync_data")
        and kind == "notification_helper"
        and "validate_legacy_telegram_otp_relay" in scope_text
    ):
        return "durable_exempt", "sync ingress accepts only the strict legacy OTP envelope"
    if kind == "membership_control":
        return "non_message_control", "channel membership mutation is not delivery pacing"
    if path in LEGACY_OWNER_FILES:
        return "legacy_owner_guarded", "legacy worker is excluded by runtime ownership"
    if "_assert_legacy" in scope_text:
        return "legacy_owner_guarded", "final legacy side-effect boundary asserts runtime ownership"
    if _has_queue_mode_guard(scope_text):
        return "legacy_mode_guarded", "queue-v1 branch exits before direct Telegram call"
    if "include_telegram" in scope_text and "if include_telegram" in scope_text:
        return "legacy_parameter_guarded", "queue-v1 caller disables the Telegram branch explicitly"
    if path.startswith("bot/"):
        if terminal in {"delete", "delete_message"}:
            return "remaining_cleanup_direct", "direct bot cleanup still bypasses shared queue"
        if kind == "callback_answer":
            return "remaining_callback_direct", "deadline callback still uses aiogram directly"
        return "remaining_interactive_direct", "interactive Bot API call still bypasses shared queue"
    if kind in {
        "bot_api",
        "callable_gateway",
        "gateway",
        "notification_helper",
    }:
        return "remaining_business_direct", "domain delivery still bypasses shared queue"
    return "unclassified", "no ownership rule matched"


class _CallsiteVisitor(ast.NodeVisitor):
    def __init__(self, *, path: str, source_lines: Sequence[str]) -> None:
        self.path = path
        self.source_lines = source_lines
        self.stack: list[ast.AST] = []
        self.callsites: list[TelegramDeliveryCallsite] = []

    def _visit_scope(self, node: ast.AST) -> None:
        self.stack.append(node)
        self.generic_visit(node)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._visit_scope(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._visit_scope(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self._visit_scope(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        callee = _dotted_name(node.func)
        kind = _call_kind(self.path, callee)
        if kind is not None:
            scope_text = _scope_text(self.source_lines, self.stack)
            disposition, evidence = _classify(
                path=self.path,
                callee=callee,
                kind=kind,
                scope=_scope_name(self.stack),
                scope_text=scope_text,
            )
            self.callsites.append(
                TelegramDeliveryCallsite(
                    path=self.path,
                    line=node.lineno,
                    column=node.col_offset,
                    scope=_scope_name(self.stack),
                    callee=callee,
                    kind=kind,
                    disposition=disposition,
                    evidence=evidence,
                )
            )
        elif callee == "asyncio.create_task":
            rendered = ast.unparse(node.args[0]) if node.args else ""
            if any(hint in rendered.lower() for hint in MEMORY_TIMER_HINTS):
                scope_text = _scope_text(self.source_lines, self.stack)
                if "_assert_legacy" in scope_text:
                    disposition = "legacy_owner_guarded"
                    evidence = "legacy timer boundary asserts runtime ownership"
                elif _has_queue_mode_guard(scope_text):
                    disposition = "legacy_mode_guarded"
                    evidence = "queue-v1 branch does not schedule a Telegram timer"
                elif "include_telegram=not queue_mode" in rendered:
                    disposition = "non_delivery_timer"
                    evidence = "timer retains only the WebApp notification in queue-v1"
                elif (
                    self.path == "bot/handlers/trade_execute.py"
                    and "_notify_remote_trade_success_when_recovered" in rendered
                ):
                    disposition = "non_delivery_timer"
                    evidence = "recovery waits for receipt-backed delivery and does not call Telegram"
                else:
                    disposition = "remaining_memory_timer"
                    evidence = "in-memory Telegram-related task is not a durable source"
                self.callsites.append(
                    TelegramDeliveryCallsite(
                        path=self.path,
                        line=node.lineno,
                        column=node.col_offset,
                        scope=_scope_name(self.stack),
                        callee=f"asyncio.create_task({rendered})",
                        kind="memory_timer",
                        disposition=disposition,
                        evidence=evidence,
                    )
                )
        self.generic_visit(node)


def iter_python_paths(repo_root: Path) -> Iterable[Path]:
    for root_name in ROOT_NAMES:
        root = repo_root / root_name
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" not in path.parts:
                yield path


def build_inventory(repo_root: Path) -> list[TelegramDeliveryCallsite]:
    inventory: list[TelegramDeliveryCallsite] = []
    for path in iter_python_paths(repo_root):
        relative_path = path.relative_to(repo_root).as_posix()
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=relative_path)
        visitor = _CallsiteVisitor(
            path=relative_path,
            source_lines=source.splitlines(keepends=True),
        )
        visitor.visit(tree)
        inventory.extend(visitor.callsites)
    return sorted(
        inventory,
        key=lambda item: (item.path, item.line, item.column, item.callee),
    )


def disposition_counts(
    inventory: Sequence[TelegramDeliveryCallsite],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in inventory:
        counts[item.disposition] = counts.get(item.disposition, 0) + 1
    return dict(sorted(counts.items()))


def inventory_check_failures(
    inventory: Sequence[TelegramDeliveryCallsite],
) -> list[str]:
    failures = [
        f"unclassified:{item.identity}"
        for item in inventory
        if item.disposition == "unclassified"
    ]
    counts = disposition_counts(inventory)
    for disposition, budget in REMAINING_DISPOSITION_BUDGETS.items():
        actual = counts.get(disposition, 0)
        if actual > budget:
            failures.append(
                f"remaining_budget_exceeded:{disposition}:{actual}>{budget}"
            )
    return failures


def _json_report(inventory: Sequence[TelegramDeliveryCallsite]) -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "total": len(inventory),
            "disposition_counts": disposition_counts(inventory),
            "callsites": [
                {"identity": item.identity, **asdict(item)} for item in inventory
            ],
        },
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )


def _summary_report(inventory: Sequence[TelegramDeliveryCallsite]) -> str:
    lines = [f"total={len(inventory)}"]
    lines.extend(
        f"{disposition}={count}"
        for disposition, count in disposition_counts(inventory).items()
    )
    return "\n".join(lines)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument(
        "--format",
        choices=("json", "summary"),
        default="summary",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail on unclassified calls or growth beyond the reviewed baseline",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    inventory = build_inventory(args.repo_root.resolve())
    print(_json_report(inventory) if args.format == "json" else _summary_report(inventory))
    if args.check:
        failures = inventory_check_failures(inventory)
        if failures:
            print("\n".join(failures), file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

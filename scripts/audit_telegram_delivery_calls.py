#!/usr/bin/env python3
"""Build a deterministic inventory of runtime Telegram delivery callsites.

The audit is intentionally static and conservative.  It does not claim that a
callsite is reachable in a particular deployment; it records every syntactic
delivery boundary under the runtime Python roots and classifies the ownership
contract that must make the call safe.
"""
from __future__ import annotations

import argparse
import ast
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import sys
from typing import Iterable, Sequence


ROOT_NAMES = ("api", "bot", "core", "src")
ROOT_FILES = ("main.py", "manage.py", "run_bot.py")

BOT_API_METHODS = frozenset(
    {
        "answer_callback_query",
        "ban_chat_member",
        "copy_message",
        "delete_message",
        "delete_messages",
        "edit_message_caption",
        "edit_message_live_location",
        "edit_message_media",
        "edit_message_reply_markup",
        "edit_message_text",
        "forward_message",
        "send_animation",
        "send_audio",
        "send_contact",
        "send_dice",
        "send_document",
        "send_location",
        "send_media_group",
        "send_message",
        "send_paid_media",
        "send_poll",
        "send_photo",
        "send_sticker",
        "send_venue",
        "send_video",
        "send_video_note",
        "send_voice",
        "stop_message_live_location",
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
        "edit_caption",
        "edit_live_location",
        "edit_media",
        "edit_reply_markup",
        "edit_text",
        "forward",
        "reply",
        "stop_live_location",
    }
)
AIROGRAM_CONVENIENCE_PREFIXES = (
    "answer_",
    "reply_",
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
DETACHED_TASK_METHODS = frozenset({"create_task", "ensure_future"})

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

# Stage 3 owns every runtime message-delivery boundary.  The zero budgets and
# exact reviewed inventory below make the audit fail closed: adding, removing,
# moving, or reclassifying a callsite requires an intentional baseline review.
REMAINING_DISPOSITION_BUDGETS = {
    "remaining_business_direct": 0,
    "remaining_callback_direct": 0,
    "remaining_cleanup_direct": 0,
    "remaining_interactive_direct": 0,
    "remaining_memory_timer": 0,
}
EXPECTED_RUNTIME_INVENTORY_COUNTS = {
    "durable_exempt": 4,
    "legacy_mode_guarded": 66,
    "legacy_owner_guarded": 15,
    "legacy_parameter_guarded": 2,
    "non_delivery_timer": 5,
    "non_message_control": 2,
    "queue_execution": 1,
}
EXPECTED_RUNTIME_INVENTORY_SHA256 = (
    "5086e0cb47bc7c34a0c22e43e3df40b4e535c68b89e5e0f2178f0e823154dc11"
)


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


def _resolved_dotted_name(
    node: ast.AST | None,
    *,
    aliases: dict[str, str],
) -> str:
    """Resolve conservative import/callable aliases and literal ``getattr``.

    Resolution is deliberately one-way: uncertain dynamic expressions stay
    visible as a synthetic ``dynamic_getattr`` boundary instead of being
    silently ignored.
    """

    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        prefix = _resolved_dotted_name(node.value, aliases=aliases)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
    ):
        owner = _resolved_dotted_name(node.args[0], aliases=aliases)
        attribute = node.args[1]
        if isinstance(attribute, ast.Constant) and isinstance(attribute.value, str):
            return f"{owner}.{attribute.value}" if owner else attribute.value
        return f"{owner}.dynamic_getattr" if owner else "dynamic_getattr"
    return ""


def _collect_callable_aliases(tree: ast.AST) -> dict[str, str]:
    """Collect aliases conservatively across a module for bypass discovery."""

    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for imported in node.names:
                local = imported.asname or imported.name.split(".", 1)[0]
                aliases[local] = imported.name
        elif isinstance(node, ast.ImportFrom):
            module = str(node.module or "")
            for imported in node.names:
                if imported.name == "*":
                    continue
                local = imported.asname or imported.name
                aliases[local] = (
                    f"{module}.{imported.name}" if module else imported.name
                )

    # Iterate because one local alias may point to another alias assigned
    # earlier or later in the same module.  Scope collisions intentionally
    # over-report rather than hide a possible transport boundary.
    assignments: list[tuple[str, ast.AST]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
            value = node.value
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                assignments.append((target.id, value))

    for _ in range(max(1, len(assignments) + 1)):
        changed = False
        for local, value in assignments:
            resolved = _resolved_dotted_name(value, aliases=aliases)
            terminal = _terminal_name(resolved)
            callable_candidate = (
                terminal in BOT_API_METHODS
                or terminal in GATEWAY_METHODS
                or terminal in AIROGRAM_CONVENIENCE_METHODS
                or terminal.startswith(AIROGRAM_CONVENIENCE_PREFIXES)
                or terminal in CALLABLE_GATEWAY_NAMES
                or terminal
                in {"send_telegram_message", "send_telegram_notification"}
                or terminal == "dynamic_getattr"
            )
            if callable_candidate and aliases.get(local) != resolved:
                aliases[local] = resolved
                changed = True
        if not changed:
            break
    return aliases


def _terminal_name(callee: str) -> str:
    return callee.rsplit(".", 1)[-1]


def _is_aiogram_convenience_call(path: str, callee: str) -> bool:
    terminal = _terminal_name(callee)
    if (
        terminal not in AIROGRAM_CONVENIENCE_METHODS
        and not terminal.startswith(AIROGRAM_CONVENIENCE_PREFIXES)
    ):
        return False
    parts = set(callee.split("."))
    if terminal.startswith(AIROGRAM_CONVENIENCE_PREFIXES):
        return bool(
            parts
            & {
                "callback",
                "event",
                "join_request",
                "message",
                "query",
            }
        )
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
    if terminal == "dynamic_getattr" and (
        path.startswith("bot/")
        or "telegram" in callee.lower()
        or {"bot", "callback", "message", "query"} & set(callee.split("."))
    ):
        return "dynamic_delivery_lookup"
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


_RUNTIME_OBJECT_ALIAS = 2


def _queue_mode_condition_polarity(
    node: ast.AST,
    *,
    aliases: dict[str, int],
) -> int:
    """Return 1 when true means queue, -1 when true means legacy, else 0.

    Only exact predicates are accepted.  Merely mentioning the runtime mode
    somewhere in a function is not control-flow evidence.
    """

    if isinstance(node, ast.Name):
        polarity = aliases.get(node.id, 0)
        return polarity if polarity in {-1, 1} else 0
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return -_queue_mode_condition_polarity(node.operand, aliases=aliases)
    if not isinstance(node, ast.Compare) or len(node.ops) != 1 or len(node.comparators) != 1:
        return 0

    left = ast.unparse(node.left)
    right = ast.unparse(node.comparators[0])
    names = {left, right}
    if not any(name.endswith("TelegramDeliveryRuntimeMode.QUEUE_V1") for name in names):
        return 0
    runtime_side = right if left.endswith("TelegramDeliveryRuntimeMode.QUEUE_V1") else left
    runtime_base = runtime_side.removesuffix(".mode")
    if not (
        "configured_telegram_delivery_runtime" in runtime_side
        or "configured_telegram_delivery_producer_mode" in runtime_side
        or aliases.get(runtime_base) == _RUNTIME_OBJECT_ALIAS
        or aliases.get(runtime_side) in {-1, 1}
    ):
        return 0

    operator = node.ops[0]
    if isinstance(operator, (ast.Eq, ast.Is)):
        return 1
    if isinstance(operator, (ast.NotEq, ast.IsNot)):
        return -1
    return 0


def _iter_scope_nodes(
    node: ast.AST,
    *,
    scope_root: ast.AST,
    max_line: int,
) -> Iterable[ast.AST]:
    """Yield nodes in source order without crossing a nested lexical scope."""

    if getattr(node, "lineno", 0) > max_line:
        return
    yield node
    if (
        node is not scope_root
        and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef))
    ):
        return
    for child in ast.iter_child_nodes(node):
        yield from _iter_scope_nodes(child, scope_root=scope_root, max_line=max_line)


def _function_mode_aliases(
    function_node: ast.AST | None,
    *,
    inherited: dict[str, int] | None = None,
    max_line: int,
) -> dict[str, int]:
    if function_node is None:
        return dict(inherited or {})
    aliases: dict[str, int] = dict(inherited or {})
    for node in _iter_scope_nodes(
        function_node,
        scope_root=function_node,
        max_line=max_line,
    ):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = getattr(node, "value", None)
            if value is None:
                continue
            polarity = _queue_mode_condition_polarity(value, aliases=aliases)
            if not polarity:
                rendered = ast.unparse(value)
                if rendered in {
                    "configured_telegram_delivery_runtime()",
                    "configured_telegram_delivery_runtime",
                    "configured_telegram_delivery_producer_mode()",
                    "configured_telegram_delivery_producer_mode",
                }:
                    polarity = (
                        1
                        if "producer_mode" in rendered
                        else _RUNTIME_OBJECT_ALIAS
                    )
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and polarity:
                    aliases[target.id] = polarity
    return aliases


def _condition_mode_implication(
    node: ast.AST,
    *,
    when_true: bool,
    aliases: dict[str, int],
) -> int:
    polarity = _queue_mode_condition_polarity(node, aliases=aliases)
    if polarity:
        return polarity if when_true else -polarity
    if isinstance(node, ast.BoolOp):
        # All operands of AND are true on its true branch; all operands of OR
        # are false on its false branch.  One exact mode predicate therefore
        # dominates that branch even when the other operands are unrelated.
        all_selected = (
            isinstance(node.op, ast.And) and when_true
        ) or (
            isinstance(node.op, ast.Or) and not when_true
        )
        if not all_selected:
            return 0
        implications = {
            implication
            for value in node.values
            if (
                implication := _condition_mode_implication(
                    value,
                    when_true=when_true,
                    aliases=aliases,
                )
            )
        }
        if len(implications) == 1:
            return implications.pop()
    return 0


def _block_always_exits(statements: Sequence[ast.stmt]) -> bool:
    if not statements:
        return False
    for statement in statements:
        if isinstance(statement, (ast.Return, ast.Raise)):
            return True
        if isinstance(statement, ast.If) and _block_always_exits(
            statement.body
        ) and _block_always_exits(statement.orelse):
            return True
        if isinstance(statement, (ast.With, ast.AsyncWith)) and _block_always_exits(
            statement.body
        ):
            return True
        if isinstance(statement, ast.Try):
            if _block_always_exits(statement.finalbody):
                return True
            if (
                _block_always_exits(statement.body)
                and all(_block_always_exits(handler.body) for handler in statement.handlers)
                and (not statement.orelse or _block_always_exits(statement.orelse))
            ):
                return True
    return False


def _statement_contains_legacy_assert(statement: ast.AST) -> bool:
    for node in _iter_scope_nodes(
        statement,
        scope_root=statement,
        max_line=getattr(statement, "end_lineno", getattr(statement, "lineno", 0)),
    ):
        if isinstance(node, ast.Call) and "_assert_legacy" in _dotted_name(node.func):
            return True
    return False


def _legacy_guard_evidence(
    *,
    ancestors: Sequence[ast.AST],
    call: ast.Call,
) -> str | None:
    function_nodes = [
        node
        for node in ancestors
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
    ]
    aliases: dict[str, int] = {}
    for function_node in function_nodes:
        aliases = _function_mode_aliases(
            function_node,
            inherited=aliases,
            max_line=call.lineno,
        )
    chain = [*ancestors, call]

    for parent, child in zip(chain, chain[1:]):
        if not isinstance(parent, ast.If):
            continue
        if (
            child in parent.body
            and _condition_mode_implication(
                parent.test,
                when_true=True,
                aliases=aliases,
            )
            == -1
        ):
            return f"legacy-only true branch at line {parent.lineno}"
        if (
            child in parent.orelse
            and _condition_mode_implication(
                parent.test,
                when_true=False,
                aliases=aliases,
            )
            == -1
        ):
            return f"legacy-only else branch at line {parent.lineno}"

    for parent, child in zip(chain, chain[1:]):
        for _field, value in ast.iter_fields(parent):
            if not isinstance(value, list) or child not in value:
                continue
            for previous in value[: value.index(child)]:
                if _statement_contains_legacy_assert(previous):
                    return f"dominating legacy assertion at line {previous.lineno}"
                if not isinstance(previous, ast.If):
                    continue
                if (
                    _condition_mode_implication(
                        previous.test,
                        when_true=True,
                        aliases=aliases,
                    )
                    == 1
                    and _block_always_exits(previous.body)
                ):
                    return f"queue branch exits at line {previous.lineno}"
                if (
                    _condition_mode_implication(
                        previous.test,
                        when_true=False,
                        aliases=aliases,
                    )
                    == 1
                    and _block_always_exits(previous.orelse)
                ):
                    return f"queue else branch exits at line {previous.lineno}"
    return None


def _queue_branch_evidence(
    *,
    ancestors: Sequence[ast.AST],
    call: ast.Call,
) -> str | None:
    """Return local proof that the call exists only in the queue branch."""

    function_nodes = [
        node
        for node in ancestors
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
    ]
    aliases: dict[str, int] = {}
    for function_node in function_nodes:
        aliases = _function_mode_aliases(
            function_node,
            inherited=aliases,
            max_line=call.lineno,
        )
    chain = [*ancestors, call]
    for parent, child in zip(chain, chain[1:]):
        if not isinstance(parent, ast.If):
            continue
        if (
            child in parent.body
            and _condition_mode_implication(
                parent.test,
                when_true=True,
                aliases=aliases,
            )
            == 1
        ):
            return f"queue-only true branch at line {parent.lineno}"
        if (
            child in parent.orelse
            and _condition_mode_implication(
                parent.test,
                when_true=False,
                aliases=aliases,
            )
            == 1
        ):
            return f"queue-only else branch at line {parent.lineno}"
    return None


def _detached_delivery_callable_names(
    tree: ast.AST,
    path: str,
    *,
    aliases: dict[str, str],
) -> set[str]:
    """Find local callables that directly or transitively perform delivery."""

    definitions: dict[str, ast.AST] = {}
    direct: set[str] = set()
    callees: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        definitions[node.name] = node
        local_callees: set[str] = set()
        for child in _iter_scope_nodes(
            node,
            scope_root=node,
            max_line=getattr(node, "end_lineno", node.lineno),
        ):
            if not isinstance(child, ast.Call):
                continue
            callee = _resolved_dotted_name(child.func, aliases=aliases)
            if _call_kind(path, callee) is not None:
                direct.add(node.name)
            local_callees.add(_terminal_name(callee))
        callees[node.name] = local_callees

    related = set(direct)
    changed = True
    while changed:
        changed = False
        for name in definitions:
            if name not in related and callees.get(name, set()) & related:
                related.add(name)
                changed = True
    return related


def _classify(
    *,
    path: str,
    callee: str,
    kind: str,
    scope: str,
    scope_text: str,
    legacy_guard_evidence: str | None,
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
    if legacy_guard_evidence is not None:
        if "assertion" in legacy_guard_evidence:
            return "legacy_owner_guarded", legacy_guard_evidence
        return "legacy_mode_guarded", legacy_guard_evidence
    if "include_telegram" in scope_text and "if include_telegram" in scope_text:
        return "legacy_parameter_guarded", "queue-v1 caller disables the Telegram branch explicitly"
    if terminal in {"delete", "delete_message"} and kind in {
        "bot_api",
        "interactive_message",
    }:
        return "remaining_cleanup_direct", "direct bot cleanup still bypasses shared queue"
    if kind == "callback_answer":
        return "remaining_callback_direct", "deadline callback still uses aiogram directly"
    if kind in {"interactive_message", "dynamic_delivery_lookup"}:
        return "remaining_interactive_direct", "interactive Bot API call still bypasses shared queue"
    if path.startswith("bot/"):
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
    def __init__(
        self,
        *,
        path: str,
        source_lines: Sequence[str],
        detached_delivery_callables: set[str],
        callable_aliases: dict[str, str],
    ) -> None:
        self.path = path
        self.source_lines = source_lines
        self.stack: list[ast.AST] = []
        self.ancestors: list[ast.AST] = []
        self.callsites: list[TelegramDeliveryCallsite] = []
        self.detached_delivery_callables = detached_delivery_callables
        self.callable_aliases = callable_aliases

    def visit(self, node: ast.AST):  # type: ignore[override]
        self.ancestors.append(node)
        try:
            return super().visit(node)
        finally:
            self.ancestors.pop()

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
        callee = _resolved_dotted_name(node.func, aliases=self.callable_aliases)
        kind = _call_kind(self.path, callee)
        if kind is not None:
            scope_text = _scope_text(self.source_lines, self.stack)
            disposition, evidence = _classify(
                path=self.path,
                callee=callee,
                kind=kind,
                scope=_scope_name(self.stack),
                scope_text=scope_text,
                legacy_guard_evidence=_legacy_guard_evidence(
                    ancestors=self.ancestors[:-1],
                    call=node,
                ),
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
        elif _terminal_name(callee) in DETACHED_TASK_METHODS:
            rendered = ast.unparse(node.args[0]) if node.args else ""
            target_name = _terminal_name(
                _resolved_dotted_name(
                    node.args[0].func,
                    aliases=self.callable_aliases,
                )
                if node.args and isinstance(node.args[0], ast.Call)
                else ""
            )
            trade_suggestion_detached = (
                self.path == "bot/utils/trade_suggestion_messages.py"
                and rendered in {"_enqueue()", "_runner()"}
            )
            if (
                any(hint in rendered.lower() for hint in MEMORY_TIMER_HINTS)
                or trade_suggestion_detached
                or target_name in self.detached_delivery_callables
            ):
                scope_text = _scope_text(self.source_lines, self.stack)
                guard_evidence = _legacy_guard_evidence(
                    ancestors=self.ancestors[:-1],
                    call=node,
                )
                queue_evidence = _queue_branch_evidence(
                    ancestors=self.ancestors[:-1],
                    call=node,
                )
                if self.path == "core/telegram_delivery_queue_worker.py":
                    disposition = "queue_execution"
                    evidence = "owned queue supervisor task"
                elif guard_evidence and "assertion" in guard_evidence:
                    disposition = "legacy_owner_guarded"
                    evidence = guard_evidence
                elif guard_evidence:
                    disposition = "legacy_mode_guarded"
                    evidence = guard_evidence
                elif queue_evidence and target_name not in self.detached_delivery_callables:
                    disposition = "non_delivery_timer"
                    evidence = f"{queue_evidence}; detached task persists durable work only"
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
                        callee=f"{callee}({rendered})",
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
    for filename in ROOT_FILES:
        path = repo_root / filename
        if path.is_file():
            yield path


def build_inventory(repo_root: Path) -> list[TelegramDeliveryCallsite]:
    inventory: list[TelegramDeliveryCallsite] = []
    for path in iter_python_paths(repo_root):
        relative_path = path.relative_to(repo_root).as_posix()
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=relative_path)
        callable_aliases = _collect_callable_aliases(tree)
        visitor = _CallsiteVisitor(
            path=relative_path,
            source_lines=source.splitlines(keepends=True),
            detached_delivery_callables=_detached_delivery_callable_names(
                tree,
                relative_path,
                aliases=callable_aliases,
            ),
            callable_aliases=callable_aliases,
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


def inventory_fingerprint(
    inventory: Sequence[TelegramDeliveryCallsite],
) -> str:
    canonical = "\n".join(
        f"{item.identity}|{item.kind}|{item.disposition}"
        for item in inventory
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
    if counts != EXPECTED_RUNTIME_INVENTORY_COUNTS:
        failures.append("runtime_inventory_counts_changed")
    if inventory_fingerprint(inventory) != EXPECTED_RUNTIME_INVENTORY_SHA256:
        failures.append("runtime_inventory_identity_changed")
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

"""Run one schedule-bound Full Matrix Writer transition without FI→IR SSH.

This is the only live Matrix entry point allowed to build a JIT failover plan.
It obtains an action-bound Witness relay receipt, persists the immutable plan,
stages it solely on WA-FI over pinned SSH, and delegates every WA-IR mutation
to the encrypted versioned Object-Storage pull agent.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
from typing import Any

from core.dr_command_orchestration_adapter import (
    TYPED_OPERATIONS,
    TypedOrchestrationAdapter,
)
from core.dr_failover_orchestrator import (
    DrOrchestrationError,
    parse_plan,
    run_orchestration,
    verify_human_failover_approval,
)
from core.dr_operation_ledger import WitnessOperationLedger
from core.dr_staging_operation_backend import load_staging_backend_config
from core.secure_file_io import (
    read_secure_text,
    verify_hash_chained_jsonl,
    write_secure_atomic_bytes,
)
from scripts.build_three_site_full_matrix_failover_plan import (
    finalize_plan,
    prepare_plan,
)
from scripts.full_matrix_live.common import (
    FAILOVER_CONTROL_SCHEMA,
    LiveMatrixError,
    ROLE_OBSERVER_SERVICE,
    run_compose_role_service,
    run_role_agent_operation,
    safe_read,
    strict_object,
)
from scripts.full_matrix_live.pull_failover_backend import PullFailoverBackend
from scripts.request_three_site_human_approval_relay import request_receipt
from core.dr_full_matrix_failover_schedule import scheduled_entry, verify_scheduled_plan


class FullMatrixFailoverCoordinatorError(LiveMatrixError):
    """The closed JIT transition cannot be safely prepared or resumed."""


_CONTROL_FIELDS = {
    "schema",
    "campaign_id",
    "gate_group_id",
    "execution_class",
    "release_sha",
    "backend_config",
    "relay_credentials",
    "witness_relay_public_key_file",
    "journal_root",
}
_WRITER_LEASE_FIELDS = {
    "active_site",
    "writer_epoch",
    "control_state",
    "transition_id",
    "witness_lease_id_sha256",
    "witness_lease_issued_at",
    "witness_lease_expires_at",
    "witness_proof_hash",
    "lease_refresh_count_for_epoch",
    "database_now",
    "local_active_with_witness_lease",
    "local_active_reasons",
}


def _owner_directory(path: Path, *, label: str) -> Path:
    if not path.is_absolute() or path.is_symlink():
        raise FullMatrixFailoverCoordinatorError(f"{label} path is unsafe")
    try:
        metadata = path.stat()
    except OSError as exc:
        raise FullMatrixFailoverCoordinatorError(f"{label} is unavailable") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise FullMatrixFailoverCoordinatorError(f"{label} is not owner-only")
    return path.resolve()


def _private_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            read_secure_text(path, label=label, max_size=2 * 1024 * 1024),
            object_pairs_hook=strict_object,
        )
    except Exception as exc:
        raise FullMatrixFailoverCoordinatorError(f"{label} is invalid") from exc
    if not isinstance(value, dict):
        raise FullMatrixFailoverCoordinatorError(f"{label} must be an object")
    return value


def _write_private_json(path: Path, payload: dict[str, Any], *, label: str) -> None:
    write_secure_atomic_bytes(
        path,
        (
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8"),
        label=label,
        mode=0o600,
        max_size=2 * 1024 * 1024,
    )


def _control(plan: dict[str, Any]) -> dict[str, Path]:
    value = plan.get("_failover_control")
    if (
        not isinstance(value, dict)
        or set(value) != _CONTROL_FIELDS
        or value.get("schema") != FAILOVER_CONTROL_SCHEMA
        or any(value.get(name) != plan.get(name) for name in (
            "campaign_id", "gate_group_id", "execution_class", "release_sha"
        ))
    ):
        raise FullMatrixFailoverCoordinatorError("failover control binding differs from campaign")
    paths = {
        name: Path(str(value[name]))
        for name in (
            "backend_config",
            "relay_credentials",
            "witness_relay_public_key_file",
            "journal_root",
        )
    }
    for name in (
        "backend_config",
        "relay_credentials",
        "witness_relay_public_key_file",
    ):
        # Check ownership/mode without exposing credential contents.
        safe_read(paths[name], label=name.replace("_", " "), owner_only=True, max_size=512 * 1024)
    paths["journal_root"] = _owner_directory(paths["journal_root"], label="failover journal root")
    return paths


def _classification(backend) -> dict[str, Any]:  # noqa: ANN001
    return backend.preflight_static()


def _source_epoch(plan: dict[str, Any], *, source_site: str) -> int:
    role = plan["_roles"].get(source_site)
    if source_site not in {"webapp_fi", "webapp_ir"} or not isinstance(role, dict):
        raise FullMatrixFailoverCoordinatorError("transition source role is invalid")
    response = run_compose_role_service(
        source_site,
        role,
        service=ROLE_OBSERVER_SERVICE[source_site],
        command=[
            "/app/scripts/full_matrix_live/site_probe.py",
            "--operation",
            "writer_lease_state",
        ],
        timeout=180,
    )
    try:
        envelope = json.loads(str(response["stdout"]), object_pairs_hook=strict_object)
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise FullMatrixFailoverCoordinatorError("source Writer lease observation is invalid") from exc
    state = envelope.get("result") if isinstance(envelope, dict) else None
    if (
        not isinstance(envelope, dict)
        or envelope.get("schema") != "three-site-full-matrix-site-probe-v1"
        or envelope.get("status") != "passed"
        or envelope.get("operation") != "writer_lease_state"
        or envelope.get("role") != source_site
        or not isinstance(state, dict)
        or set(state) != _WRITER_LEASE_FIELDS
        or state.get("active_site") != source_site
        or state.get("control_state") != "active"
        or state.get("local_active_with_witness_lease") is not True
        or type(state.get("writer_epoch")) is not int
        or int(state["writer_epoch"]) < 1
    ):
        raise FullMatrixFailoverCoordinatorError("source is not the active Witness-leased Writer")
    return int(state["writer_epoch"])


def _operation_directory(root: Path, operation_id: str) -> Path:
    target = root / operation_id
    if target.exists():
        return _owner_directory(target, label="JIT failover operation directory")
    target.mkdir(mode=0o700)
    return _owner_directory(target, label="JIT failover operation directory")


def _journal_started(path: Path, plan) -> bool:  # noqa: ANN001
    if not path.exists():
        return False
    try:
        records = verify_hash_chained_jsonl(path, label="Full Matrix failover journal")
    except Exception as exc:
        raise FullMatrixFailoverCoordinatorError("JIT failover journal is invalid") from exc
    return any(
        record.get("event") == "dr.orchestration.operation_reserved"
        and record.get("operation_id") == plan.operation_id
        and record.get("plan_hash") == plan.plan_hash
        for record in records
    )


def _pull_operation(plan: dict[str, Any]):
    """Return a closure that can issue only the reviewed WA-IR site operation."""

    role = plan["_roles"].get("webapp_ir")
    if not isinstance(role, dict) or role.get("transport") != "object-storage-agent":
        raise FullMatrixFailoverCoordinatorError("WA-IR has no approved pull transport")

    def invoke(
        parsed_plan,
        plan_document: dict[str, Any],
        action: str,
        source_tail_boundary: dict[str, Any] | None,
        readiness_evidence: dict[str, Any] | None,
        previous_proof_hash: str | None,
    ) -> dict[str, Any]:
        control = run_role_agent_operation(
            "webapp_ir",
            role,
            operation="failover_site_operation",
            context={
                "action": action,
                "plan": plan_document,
                "source_tail_boundary": source_tail_boundary,
                "readiness_evidence": readiness_evidence,
                "previous_proof_hash": previous_proof_hash,
            },
            attempt=1,
            timeout=900,
        )
        envelope = control.get("result")
        operation = envelope.get("result") if isinstance(envelope, dict) else None
        evidence = operation.get("evidence") if isinstance(operation, dict) else None
        if (
            not isinstance(envelope, dict)
            or envelope.get("schema") != "three-site-full-matrix-site-agent-result-v1"
            or envelope.get("status") != "passed"
            or envelope.get("role") != "webapp_ir"
            or envelope.get("release_sha") != parsed_plan.release_sha
            or envelope.get("operation") != "failover_site_operation"
            or not isinstance(operation, dict)
            or operation.get("status") != "passed"
            or operation.get("action") != action
            or not isinstance(evidence, dict)
            or evidence.get("operation_id") != parsed_plan.operation_id
        ):
            raise FullMatrixFailoverCoordinatorError("WA-IR pull response is not operation-bound")
        return dict(evidence)

    return invoke


def _new_plan(
    *,
    plan: dict[str, Any],
    paths: dict[str, Path],
    backend,
    scenario_id: str,
    iteration: int,
    action: str,
) -> tuple[Any, dict[str, Any], Path, Path]:
    expected_epoch = _source_epoch(plan, source_site=("webapp_fi" if action == "promote_ir" else "webapp_ir"))
    draft, subject, manifest = prepare_plan(
        schedule=plan["_bindings"]["failover_schedule"]["payload"],
        inventory=plan["_inventory"],
        classification=_classification(backend),
        policy_payload=plan["_bindings"]["human_approval_policy"]["payload"],
        scenario_id=scenario_id,
        iteration=iteration,
        action=action,
        expected_epoch=expected_epoch,
        generated_at=datetime.now(timezone.utc),
    )
    operation = _operation_directory(paths["journal_root"], str(draft["operation_id"]))
    draft_path = operation / "draft.json"
    subject_path = operation / "approval-subject.json"
    manifest_path = operation / "typed-operation-manifest.json"
    receipt_path = operation / "approval-receipt.json"
    final_path = operation / "approved-plan.json"
    journal_path = operation / "journal.jsonl"
    if any(path.exists() for path in (draft_path, subject_path, manifest_path, receipt_path, final_path)):
        raise FullMatrixFailoverCoordinatorError("unexpected pre-existing JIT failover artifact")
    _write_private_json(draft_path, draft, label="JIT failover draft")
    _write_private_json(subject_path, subject, label="JIT failover approval subject")
    _write_private_json(manifest_path, manifest, label="JIT failover typed manifest")
    try:
        request_receipt(
            type("RelayArgs", (), {
                "action": action,
                "subject": subject_path,
                "policy": Path(plan["_bindings"]["human_approval_policy"]["path"]),
                "credentials": paths["relay_credentials"],
                "output": receipt_path,
                "timeout_seconds": 10.0,
            })()
        )
    except Exception as exc:
        raise FullMatrixFailoverCoordinatorError("Witness did not issue JIT failover relay receipt") from exc
    final = finalize_plan(
        draft=draft,
        approval=_private_json(receipt_path, label="JIT failover approval receipt"),
        schedule=plan["_bindings"]["failover_schedule"]["payload"],
        policy_payload=plan["_bindings"]["human_approval_policy"]["payload"],
        scenario_id=scenario_id,
        iteration=iteration,
        witness_relay_public_key=read_secure_text(
            paths["witness_relay_public_key_file"],
            label="Witness relay public key",
            max_size=16 * 1024,
        ).strip(),
        now=datetime.now(timezone.utc),
    )
    parsed = parse_plan(final)
    _write_private_json(final_path, final, label="approved JIT failover plan")
    return parsed, final, journal_path, manifest_path


def _existing_plan(
    *,
    plan: dict[str, Any],
    paths: dict[str, Path],
    scenario_id: str,
    iteration: int,
    action: str,
) -> tuple[Any, dict[str, Any], Path] | None:
    """Load only an already-finalized plan for crash-safe saga resumption."""

    schedule = plan["_bindings"]["failover_schedule"]["payload"]
    matches = [
        item
        for item in schedule["entries"]
        if item.get("scenario_id") == scenario_id
        and item.get("iteration") == iteration
        and item.get("action") == action
    ]
    if len(matches) != 1:
        raise FullMatrixFailoverCoordinatorError("JIT transition is absent from the signed schedule")
    # ``scheduled_entry`` also protects this lookup from a malformed or
    # duplicated schedule if this module is called outside the normal loader.
    entry = scheduled_entry(
        schedule,
        operation_id=str(matches[0].get("operation_id") or ""),
        scenario_id=scenario_id,
        iteration=iteration,
        action=action,
    )
    operation = _operation_directory(paths["journal_root"], str(entry["operation_id"]))
    final_path = operation / "approved-plan.json"
    journal_path = operation / "journal.jsonl"
    if not final_path.exists():
        retained = [
            name
            for name in (
                "draft.json",
                "approval-subject.json",
                "typed-operation-manifest.json",
                "approval-receipt.json",
            )
            if (operation / name).exists()
        ]
        if retained or journal_path.exists():
            raise FullMatrixFailoverCoordinatorError(
                "incomplete JIT failover artifacts require explicit safe recovery"
            )
        return None
    final = _private_json(final_path, label="approved JIT failover plan")
    try:
        parsed = parse_plan(final)
        verify_scheduled_plan(
            schedule,
            plan=parsed,
            scenario_id=scenario_id,
            iteration=iteration,
        )
    except Exception as exc:
        raise FullMatrixFailoverCoordinatorError("retained JIT failover plan differs from schedule") from exc
    return parsed, final, journal_path


def _assert_empty_jit_journal_root(root: Path) -> None:
    """Require a new campaign's dedicated JIT root to be genuinely unused.

    A full campaign preflight must never normalize, delete, or silently reuse
    a failed transition.  A non-empty root means that an operator has to
    retain and inspect that operation's hash-chained journal before a fresh
    campaign can be authorized.
    """

    try:
        entries = list(root.iterdir())
    except OSError as exc:
        raise FullMatrixFailoverCoordinatorError(
            "JIT failover journal root cannot be inspected"
        ) from exc
    if entries:
        raise FullMatrixFailoverCoordinatorError(
            "JIT failover journal root is not empty for a new Full Matrix campaign"
        )


def _validate_future_schedule(
    *,
    plan: dict[str, Any],
    classification: dict[str, Any],
    source_epoch: int,
) -> int:
    """Build every future transition in memory, without writing a receipt.

    The dynamic epoch of every later action is an expected progression from
    the freshly attested FI Writer epoch.  It is not a claim that those later
    actions are currently permitted; the coordinator obtains a new source
    lease, fresh classification, and short-lived Witness receipt immediately
    before each one.
    """

    schedule = plan["_bindings"]["failover_schedule"]["payload"]
    entries = schedule.get("entries")
    if not isinstance(entries, list) or not entries:
        raise FullMatrixFailoverCoordinatorError("Full Matrix failover schedule is empty")
    if any(not isinstance(entry, dict) for entry in entries):
        raise FullMatrixFailoverCoordinatorError("Full Matrix schedule entry is invalid")
    ordered = sorted(entries, key=lambda entry: int(entry.get("sequence") or 0))
    if ordered[0].get("action") != "promote_ir" or ordered[0].get("source_site") != "webapp_fi":
        raise FullMatrixFailoverCoordinatorError(
            "Full Matrix schedule does not begin at the FI Witness Writer"
        )
    for offset, entry in enumerate(ordered):
        try:
            draft, _subject, _manifest = prepare_plan(
                schedule=schedule,
                inventory=plan["_inventory"],
                classification=classification,
                policy_payload=plan["_bindings"]["human_approval_policy"]["payload"],
                scenario_id=str(entry["scenario_id"]),
                iteration=int(entry["iteration"]),
                action=str(entry["action"]),
                expected_epoch=source_epoch + offset,
                generated_at=datetime.now(timezone.utc),
            )
        except Exception as exc:
            raise FullMatrixFailoverCoordinatorError(
                "a scheduled JIT transition cannot be prepared in memory"
            ) from exc
        if (
            draft.get("operation_id") != entry.get("operation_id")
            or draft.get("operation_nonce") != entry.get("operation_nonce")
            or draft.get("source_site") != entry.get("source_site")
            or draft.get("target_site") != entry.get("target_site")
            or draft.get("expected_epoch") != source_epoch + offset
            or draft.get("target_epoch") != source_epoch + offset + 1
        ):
            raise FullMatrixFailoverCoordinatorError(
                "scheduled JIT transition has an inconsistent derived plan"
            )
    return len(ordered)


def preflight_transition_system(plan: dict[str, Any]) -> dict[str, Any]:
    """Read-only proof that JIT Writer transitions can safely be started.

    This is intentionally separate from :func:`execute_transition`: it does
    not request a Witness receipt, create a plan/journal artifact, stage an
    FI file, call a site/object-storage agent, or perform an Arvan mutation.
    Its only remote operation is the closed FI Writer-lease observation.
    """

    paths = _control(plan)
    backend_config = load_staging_backend_config(
        paths["backend_config"],
        inventory=plan["_inventory"],
        inventory_approval=plan["_bindings"]["inventory_approval"]["payload"],
        inventory_approval_policy=plan["_bindings"]["human_approval_policy"]["payload"],
    )
    backend = PullFailoverBackend(
        backend_config,
        plan_document={},
        pull_operation=lambda *_args: (_ for _ in ()).throw(
            FullMatrixFailoverCoordinatorError(
                "pull operation is unavailable during coordinator preflight"
            )
        ),
    )
    classification = _classification(backend)
    source_epoch = _source_epoch(plan, source_site="webapp_fi")
    transition_count = _validate_future_schedule(
        plan=plan,
        classification=classification,
        source_epoch=source_epoch,
    )
    _assert_empty_jit_journal_root(paths["journal_root"])
    return {
        "status": "passed",
        "initial_active_writer": "webapp_fi",
        "initial_writer_epoch": source_epoch,
        "fresh_connectivity": classification,
        "scheduled_transition_count": transition_count,
        "jit_journal_root_empty": True,
        "wa_ir_mutation_transport": "encrypted-object-storage-pull-only",
        "witness_receipt_requested": False,
        "site_mutation_performed": False,
    }


def execute_transition(
    plan: dict[str, Any],
    *,
    scenario_id: str,
    iteration: int,
    action: str,
    pause_after_source_drain_for_power_loss: bool = False,
) -> dict[str, Any]:
    """Prepare and execute one scheduled failover action.

    A caller chooses only an exact catalog scenario, iteration and scheduled
    action.  It cannot pass a command, path, hostname, artifact, epoch or
    Iran transport.  Any existing JIT artifacts are resumed only through the
    hash-chained saga; an unstarted stale artifact is never overwritten.
    """

    if (
        action not in {"promote_ir", "failback_fi"}
        or iteration not in {1, 2}
        or type(pause_after_source_drain_for_power_loss) is not bool
    ):
        raise FullMatrixFailoverCoordinatorError("JIT transition action or iteration is invalid")
    if pause_after_source_drain_for_power_loss and (
        scenario_id != "power_loss_between_fence_and_enable"
        or action != "promote_ir"
    ):
        raise FullMatrixFailoverCoordinatorError(
            "JIT power-loss cutpoint is outside its exact promotion scenario"
        )
    paths = _control(plan)
    backend_config = load_staging_backend_config(
        paths["backend_config"],
        inventory=plan["_inventory"],
        inventory_approval=plan["_bindings"]["inventory_approval"]["payload"],
        inventory_approval_policy=plan["_bindings"]["human_approval_policy"]["payload"],
    )
    # Construct the plan before any site mutation.  The backend is rebuilt
    # from the same hash-bound configuration for every action.
    provisional = PullFailoverBackend(
        backend_config,
        plan_document={},
        pull_operation=lambda *_args: (_ for _ in ()).throw(
            FullMatrixFailoverCoordinatorError("pull operation is unavailable during JIT planning")
        ),
    )
    existing = _existing_plan(
        plan=plan,
        paths=paths,
        scenario_id=scenario_id,
        iteration=iteration,
        action=action,
    )
    if existing is None:
        parsed, final_document, journal_path, _manifest_path = _new_plan(
            plan=plan,
            paths=paths,
            backend=provisional,
            scenario_id=scenario_id,
            iteration=iteration,
            action=action,
        )
    else:
        parsed, final_document, journal_path = existing
    operation_started = _journal_started(journal_path, parsed)
    verify_human_failover_approval(
        parsed,
        plan["_bindings"]["human_approval_policy"]["payload"],
        require_fresh=not operation_started,
        witness_relay_public_key=read_secure_text(
            paths["witness_relay_public_key_file"],
            label="Witness relay public key",
            max_size=16 * 1024,
        ).strip(),
    )
    backend = PullFailoverBackend(
        backend_config,
        plan_document=final_document,
        pull_operation=_pull_operation(plan),
    )
    backend.preflight(parsed)
    # This is the sole direct artifact path and it targets WA-FI only.  Once
    # a hash-chained saga has passed the dedicated power-loss cutpoint, WA-FI
    # may intentionally be unavailable.  Its immutable plan/manifest were
    # staged before source fencing, so retrying SCP here would both fail and
    # incorrectly turn a target-only resume into a source dependency.
    if not operation_started:
        backend.materialize_webapp_fi_inputs(parsed)
    adapter = TypedOrchestrationAdapter(dict(TYPED_OPERATIONS), backend=backend)
    ledger = WitnessOperationLedger(
        backend.config.witness_config,
        witness_public_key=backend.config.witness_public_key,
    )
    try:
        result = asyncio.run(
            run_orchestration(
                parsed,
                adapter=adapter,
                ledger=ledger,
                journal_path=journal_path,
                pause_after_step=(
                    "source_connections_drained"
                    if pause_after_source_drain_for_power_loss
                    else None
                ),
            )
        )
    except DrOrchestrationError as exc:
        raise FullMatrixFailoverCoordinatorError("JIT failover saga failed closed") from exc
    if result.get("status") not in {"completed", "rolled_back", "paused"}:
        raise FullMatrixFailoverCoordinatorError("JIT failover saga returned an invalid terminal state")
    response = {
        "status": result["status"],
        "operation_id": parsed.operation_id,
        "plan_hash": parsed.plan_hash,
        "action": parsed.action,
        "source_site": parsed.source_site,
        "target_site": parsed.target_site,
        "writer_epoch_before": parsed.expected_epoch,
        "writer_epoch_after": parsed.target_epoch,
        "connectivity_mode": str(parsed.classification["mode"]),
        "connectivity_consecutive_rounds": int(
            parsed.classification["consecutive_rounds"]
        ),
        "journal": str(journal_path),
    }
    if result["status"] == "paused":
        if result.get("paused_after_step") != "source_connections_drained":
            raise FullMatrixFailoverCoordinatorError("JIT power-loss saga paused at an invalid step")
        response["paused_after_step"] = "source_connections_drained"
    return response

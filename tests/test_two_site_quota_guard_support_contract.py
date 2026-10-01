"""Contract tests for reusable private boundary-guard support."""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from typing import Any
import pytest

import scripts.two_site_provider_broker as broker
import scripts.two_site_quota_continuation_terminal_storage as terminal_storage
from tests.test_two_site_quota_guard_support import (
    TARGET_NAMES,
    BoundaryTrap,
    boundary_guards,
)

TARGET_SPECS = (
    ("submit", broker, "submit"),
    ("submit_maintenance", broker, "submit_maintenance"),
    ("execute_pipeline", broker, "execute_pipeline"),
    ("publish", terminal_storage, "publish_terminal_continuation_record"),
)


def test_all_four_identities_and_zero_calls_on_normal_lifecycle() -> None:
    before_broker_submit = broker.submit
    before_broker_sub_maint = broker.submit_maintenance
    before_broker_exec = broker.execute_pipeline
    before_publish = terminal_storage.publish_terminal_continuation_record

    with boundary_guards() as trap:
        assert isinstance(trap, BoundaryTrap)
        trap.assert_active()
        for name, mod, attr in TARGET_SPECS:
            current = getattr(mod, attr)
            assert current is trap.traps[name]
            assert current is not trap.originals[name]
            assert trap.calls[name] == 0
            assert trap.originals[name] is not None

    assert broker.submit is before_broker_submit
    assert broker.submit_maintenance is before_broker_sub_maint
    assert broker.execute_pipeline is before_broker_exec
    assert (
        terminal_storage.publish_terminal_continuation_record
        is before_publish
    )
    for name in TARGET_NAMES:
        assert trap.calls[name] == 0


def test_nested_fault_monkeypatch_undo_does_not_remove_owner_traps() -> None:
    with boundary_guards() as trap:
        nested_mp = pytest.MonkeyPatch()
        dummy_called = False

        def dummy_os_stat(*args: Any, **kwargs: Any) -> Any:
            nonlocal dummy_called
            dummy_called = True
            raise OSError("nested fault stat denied")

        nested_mp.setattr(os, "stat", dummy_os_stat)
        with pytest.raises(OSError, match="nested fault stat denied"):
            os.stat(".")
        assert dummy_called is True

        nested_mp.setattr(
            broker, "submit", lambda *args, **kwargs: "nested_override"
        )
        assert broker.submit is not trap.traps["submit"]

        nested_mp.undo()

        trap.assert_active()
        for name, mod, attr in TARGET_SPECS:
            assert getattr(mod, attr) is trap.traps[name]
            assert trap.calls[name] == 0


@pytest.mark.parametrize("target_name,mod,attr", TARGET_SPECS)
def test_boundary_restore_original_temporary_and_exit_failure(
    target_name: str, mod: Any, attr: str
) -> None:
    orig = getattr(mod, attr)

    with boundary_guards() as trap:
        trap.assert_active()
        trap_fn = trap.traps[target_name]
        original_fn = trap.originals[target_name]

        setattr(mod, attr, original_fn)
        with pytest.raises(
            AssertionError, match=f"Trap {target_name} identity mismatch"
        ):
            trap.assert_active()

        setattr(mod, attr, trap_fn)
        trap.assert_active()

    with pytest.raises(
        AssertionError, match=f"Trap {target_name} identity mismatch"
    ):
        with boundary_guards() as trap2:
            trap2.assert_active()
            setattr(mod, attr, trap2.originals[target_name])

    assert getattr(mod, attr) is orig


@pytest.mark.parametrize("target_name,mod,attr", TARGET_SPECS)
def test_independently_trip_each_installed_boundary(
    target_name: str, mod: Any, attr: str
) -> None:
    owner_mp = pytest.MonkeyPatch()
    try:
        trap = BoundaryTrap(owner_mp)
        trap.assert_active()
        assert trap.calls[target_name] == 0

        fn = getattr(mod, attr)
        with pytest.raises(
            AssertionError,
            match=f"Boundary trap tripped: {target_name} was called",
        ):
            fn("harmless_dummy_arg", dummy_kw="dummy_val")

        assert trap.calls[target_name] == 1
        for other, _, _ in TARGET_SPECS:
            if other != target_name:
                assert trap.calls[other] == 0

        with pytest.raises(
            AssertionError, match=f"Trap {target_name} was called 1 times"
        ):
            trap.assert_active()
    finally:
        trap.restore()


@pytest.mark.parametrize("target_name,mod,attr", TARGET_SPECS)
def test_boundary_guards_context_tripped_failure_on_exit(
    target_name: str, mod: Any, attr: str
) -> None:
    orig = getattr(mod, attr)
    with pytest.raises(
        AssertionError, match=f"Trap {target_name} was called 1 times"
    ):
        with boundary_guards() as trap:
            fn = getattr(mod, attr)
            try:
                fn("harmless_arg")
            except AssertionError as trip_exc:
                assert (
                    f"Boundary trap tripped: {target_name} was called"
                    in str(trip_exc)
                )
            assert trap.calls[target_name] == 1

    assert getattr(mod, attr) is orig


def test_true_removal_sensitivity() -> None:
    with boundary_guards() as trap:
        trap.assert_active()
        rogue_fn = lambda *args, **kwargs: None

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(broker, "submit", rogue_fn)
            with pytest.raises(
                AssertionError, match="Trap submit identity mismatch"
            ):
                trap.assert_active()
            assert trap.calls["submit"] == 0

        trap.assert_active()


def test_body_error_restores_all_four_functions_and_preserves_exception() -> None:
    orig_submit = broker.submit
    orig_maint = broker.submit_maintenance
    orig_exec = broker.execute_pipeline
    orig_pub = terminal_storage.publish_terminal_continuation_record

    custom_err = RuntimeError("primary body error")
    with pytest.raises(RuntimeError) as exc_info:
        with boundary_guards() as trap:
            trap.assert_active()
            raise custom_err

    assert exc_info.value is custom_err
    assert broker.submit is orig_submit
    assert broker.submit_maintenance is orig_maint
    assert broker.execute_pipeline is orig_exec
    assert (
        terminal_storage.publish_terminal_continuation_record
        is orig_pub
    )


@pytest.mark.parametrize(
    "cancel_exc", [KeyboardInterrupt("cancel"), SystemExit(2)]
)
def test_body_cancel_restores_all_four_functions_and_preserves_exception(
    cancel_exc: BaseException,
) -> None:
    orig_submit = broker.submit
    orig_maint = broker.submit_maintenance
    orig_exec = broker.execute_pipeline
    orig_pub = terminal_storage.publish_terminal_continuation_record

    with pytest.raises(type(cancel_exc)) as exc_info:
        with boundary_guards() as trap:
            trap.assert_active()
            raise cancel_exc

    assert exc_info.value is cancel_exc
    assert broker.submit is orig_submit
    assert broker.submit_maintenance is orig_maint
    assert broker.execute_pipeline is orig_exec
    assert (
        terminal_storage.publish_terminal_continuation_record
        is orig_pub
    )


def test_multiple_sequential_contexts_fresh_identities_no_leaks() -> None:
    orig_submit = broker.submit
    orig_maint = broker.submit_maintenance
    orig_exec = broker.execute_pipeline
    orig_pub = terminal_storage.publish_terminal_continuation_record

    traps_first: dict[str, Any] = {}
    with boundary_guards() as trap1:
        trap1.assert_active()
        traps_first = dict(trap1.traps)

    assert broker.submit is orig_submit
    assert broker.submit_maintenance is orig_maint
    assert broker.execute_pipeline is orig_exec
    assert (
        terminal_storage.publish_terminal_continuation_record
        is orig_pub
    )

    traps_second: dict[str, Any] = {}
    with boundary_guards() as trap2:
        trap2.assert_active()
        traps_second = dict(trap2.traps)
        for name in TARGET_NAMES:
            assert traps_second[name] is not traps_first[name]

    assert broker.submit is orig_submit
    assert broker.submit_maintenance is orig_maint
    assert broker.execute_pipeline is orig_exec
    assert (
        terminal_storage.publish_terminal_continuation_record
        is orig_pub
    )

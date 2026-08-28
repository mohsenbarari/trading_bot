from __future__ import annotations

import argparse, json, stat, tempfile
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from unittest import mock

import pytest

from scripts import cutover_telegram_delivery_queue_production as queue
from scripts import promote_production_private_primary_product as p
from scripts import update_production_coin_inference_source as updater
from scripts import verify_production_private_primary_promotion as verifier

SHA, TREE = "1" * 40, "2" * 40
digest = lambda path: sha256(Path(path).read_bytes()).hexdigest()
_HARNESS_TEMPORARIES: list[tempfile.TemporaryDirectory[str]] = []


@pytest.fixture(autouse=True)
def _cleanup_harness_temporaries():
    """Close every synthetic promotion root before pytest collects it."""

    yield
    while _HARNESS_TEMPORARIES:
        _HARNESS_TEMPORARIES.pop().cleanup()

class SyntheticSigkill(BaseException):
    pass

def write_json(path, value):
    path = Path(path); path.write_text(json.dumps(value, sort_keys=True)+"\n"); path.chmod(0o600)

class Harness:
    def __init__(self):
        self.temp=tempfile.TemporaryDirectory(prefix="promote-queue-"); _HARNESS_TEMPORARIES.append(self.temp); self.root=Path(self.temp.name)/"secure"; self.root.mkdir(mode=0o700)
        self.release_checkout=self.root/"release-checkout"; self.release_checkout.mkdir(mode=0o700)
        self.control=self.root/"release-control"; self.control.mkdir(mode=0o700); self.artifacts=self.root/"artifacts"; self.artifacts.mkdir(mode=0o700)
        self.runtime=self.root/"production-runtime-source.env"
        self.runtime.write_text("SECRET=must-not-leak\nPRODUCTION_PRODUCT_ESTIMATOR_SNAPSHOT_MODE=LEGACY\nTELEGRAM_DELIVERY_PRODUCER_MODE=queue-v1\nTELEGRAM_DELIVERY_EXPECTED_EXECUTION_OWNER=queue-v1\nTELEGRAM_DELIVERY_EXECUTION_OWNER=queue-v1\nTELEGRAM_DELIVERY_QUEUE_WORKER_ENABLED=1\nTELEGRAM_DELIVERY_QUEUE_CUTOVER_READY=1\nTELEGRAM_MULTI_PUBLISHER_ENABLED=1\nTELEGRAM_B2B_DISPATCH_ENABLED=1\n"); self.runtime.chmod(0o600)
        self.source=self.control/"production-source-manifest.env"
        self.source.write_text(f"RUNTIME_ENV_SOURCE_PATH={self.runtime}\nMANIFEST_SECRET=manifest-must-not-leak\nPRODUCTION_MARKET_PIPELINE_RELEASE_EVIDENCE_ENABLED=1\nPRODUCTION_MARKET_PIPELINE_HOST_PREFLIGHT_ENABLED=1\nPRODUCTION_MARKET_PIPELINE_MIGRATION_ENABLED=1\nPRODUCTION_MARKET_PIPELINE_SHADOW_ROLLOUT_ENABLED=1\nPRODUCTION_MARKET_PIPELINE_CAPTURE_CUTOVER_ENABLED=1\nPRODUCTION_COIN_INFERENCE_RELAY_ENABLED=1\nPRODUCTION_COIN_INFERENCE_RELAY_CONFIRM=publish-production-coin-inference-snapshot\n"); self.source.chmod(0o600)
        derived, changed=p._derive_private_manifest(self.source.read_bytes()); self.private=self.control/"private.env"; self.private.write_bytes(derived); self.private.chmod(0o600)
        self.prep=self.control/"private.json"
        write_json(self.prep,{"schema":p.PREPARATION_RECEIPT_SCHEMA,"status":"PASS","action":"PREPARE_PRIVATE_PRIMARY_DEPLOY_MANIFEST","source_sha256":digest(self.source),"output_sha256":digest(self.private),"source_path_sha256":sha256(str(self.source).encode()).hexdigest(),"output_path_sha256":sha256(str(self.private).encode()).hexdigest(),"receipt_path_sha256":sha256(str(self.prep).encode()).hexdigest(),"manifest_schema_sha256":digest(p.MANIFEST_SCHEMA_SOURCE),"tool_sha256":digest(p.MANIFEST_PREPARER_SCRIPT),"changed_keys":changed,"normalized_keys":sorted(p.PRIVATE_MANIFEST_UPDATES),"source_preserved_by_tool":True,"secrets_disclosed":False})
        self.catchup=self.root/"production-catchup.json"
        verified=datetime.now(timezone.utc)
        evidence={label:{"sha256":character*64,"observed_at_utc":((verified-timedelta(seconds=30)) if label.startswith("previous") else verified).isoformat().replace("+00:00","Z")} for label,character in (("previous_web","1"),("previous_bot","2"),("web","3"),("bot","4"))}
        evidence_binding=sha256((json.dumps(evidence,ensure_ascii=True,sort_keys=True,separators=(",",":"))+"\n").encode("ascii")).hexdigest()
        write_json(self.catchup,{"schema":p.CATCHUP_RECEIPT_SCHEMA,"status":"PASS","verified_at_utc":verified.isoformat().replace("+00:00","Z"),"release_sha":SHA,"cutoff_utc":p.AUTHORIZED_BACKFILL_NOT_BEFORE_UTC,"backfill_sources":list(p.AUTHORIZED_CATCHUP_BACKFILL_SOURCES),"live_source_inventory":list(p.AUTHORIZED_CATCHUP_SOURCE_INVENTORY),"live_tail_observed":True,"live_advanced_sources":["GROUP_1"],"live_parser_output_advanced_sources":["GROUP_1"],"evidence_artifacts":evidence,"evidence_binding_sha256":evidence_binding,"upstream_time_gaps_allowed":True,"internal_sequence_gaps":0,"unresolved_quarantines":0,"unresolved_rejections":0,"secrets_disclosed":False})
        self.promotion=self.root/"production-promotion.json"
        write_json(self.promotion,{"schema":p.PROMOTION_RECEIPT_SCHEMA,"status":"PASS","created_at_utc":datetime.now(timezone.utc).isoformat().replace("+00:00","Z"),"release_sha":SHA,"release_tree":TREE,"image_ids":{"bot":"sha256:"+"c"*64,"web":"sha256:"+"d"*64},"maximum_age_seconds":120,"checks":list(p.PROMOTION_REQUIRED_CHECKS),"catchup_verification":{"receipt_sha256":digest(self.catchup),"age_seconds":0},"capture_backfill":{"not_before_utc":p.AUTHORIZED_BACKFILL_NOT_BEFORE_UTC,"source_codes":list(p.AUTHORIZED_BACKFILL_SOURCE_CODES),"max_messages":100000},"snapshot":{"contract":p.PROMOTION_SNAPSHOT_CONTRACT,"lane":"PRIVATE_PRIMARY","status":"OK","snapshot_hash":"a"*64,"snapshot_version":1,"estimated_rate_count":14,"file_sha256":"b"*64,"snapshot_age_seconds":1,"publication_age_seconds":1,"maximum_effective_underlying_age_seconds":1},"artifacts":{"bot_snapshot_sha256":"b"*64,"web_snapshot_sha256":"b"*64},"read_only_runtime_verification":True,"product_or_runtime_mutated":False,"payload_values_included":False,"pii_included":False,"secrets_disclosed":False})
        authority={"bluegreen_journal_path_sha256":"1"*64,"prepared_bluegreen_journal_sha256":"2"*64,"authorization_bluegreen_journal_sha256":"3"*64,"marker_authority_sha256":"4"*64}
        self.maintenance=self.root/"production-market-maintenance.json";write_json(self.maintenance,{"schema":"production_legacy_market_collector_handoff/1.1","status":"PRIMARY_COMMITTED","host_role":"bot","release_sha":SHA,"maintenance_lock":{},"secrets_disclosed":False})
        self.web_maintenance=self.root/"production-web-market-maintenance.json";write_json(self.web_maintenance,{"schema":"production_legacy_market_collector_handoff/1.1","status":"PRIMARY_COMMITTED","host_role":"web","release_sha":SHA,"verified_at_utc":datetime.now(timezone.utc).isoformat().replace("+00:00","Z"),"primary_verification_sha256":digest(self.promotion),"primary_rollback_sha256":None,"state_deleted":False,"maintenance_lock":{},"authority_transfer":authority,"secrets_disclosed":False})
        self.tx=self.root/"production-transactions"; self.final=self.tx/"final.json"; self.events=[]; self.authorities=[]; self.deploys=[]; self.attestations=[]; self.run_held=self.source_held=False; self.fail_private=self.fail_legacy=self.fail_rollback=False; self.fail_terminal_legacy=False; self.fail_terminal_snapshot=False; self.advance_clock_after_private_deploy=False; self.clock_offset=0; self.fail_applied_journal_after_persist=False; self.fail_preflight=False; self.fail_maintenance_check_at=None; self.maintenance_checks=0; self.kill_before_private_deploy=False; self.kill_after_private_deploy=False; self.kill_final_receipt=False; self.kill_fired=False
    def args(self):
        return argparse.Namespace(source_manifest=str(self.source),expected_source_manifest_sha256=digest(self.source),private_manifest=str(self.private),expected_private_manifest_sha256=digest(self.private),private_manifest_receipt=str(self.prep),expected_private_manifest_receipt_sha256=digest(self.prep),promotion_receipt=str(self.promotion),expected_promotion_receipt_sha256=digest(self.promotion),catchup_receipt=str(self.catchup),expected_catchup_receipt_sha256=digest(self.catchup),expected_source_sha256=digest(self.runtime),expected_release_sha=SHA,expected_release_tree=TREE,release_checkout=str(self.release_checkout),maintenance_journal=str(self.maintenance),expected_maintenance_journal_sha256=digest(self.maintenance),web_maintenance_journal=str(self.web_maintenance),expected_web_maintenance_journal_sha256=digest(self.web_maintenance),transaction_root=str(self.tx),queue_artifact_dir=str(self.artifacts),transaction_id="primary-20260828-001",receipt=str(self.final),confirm=p.CONFIRMATION)
    def recovery_args(self, action):
        args=self.args(); journal=self.artifacts/"journal.json"; args.recovery_phase_journal=str(journal); args.expected_phase_journal_sha256=digest(journal); args.recovery_action=action; args.recovery_confirm=p.RECOVERY_CONFIRMATION; return args
    def command(self, argv, **kw):
        argv=list(argv)
        if argv[:2]==["git","rev-parse"]:
            if argv[2:4]==["--abbrev-ref","HEAD"]: value="main"
            elif argv[2] in {"HEAD","origin/main"}: value=SHA
            elif argv[2]=="HEAD^{tree}": value=TREE
            else: raise AssertionError(f"unexpected git identity query: {argv}")
            if self.fail_preflight and argv[2]=="HEAD": value="0"*40
            return p.CommandResult(0,(value+"\n").encode())
        if argv[:2]==["git","status"]: return p.CommandResult(0,b"")
        raise AssertionError(f"raw child mutation: {argv}")
    def activate(self,args,source,*,source_lock_descriptor):
        assert self.run_held and self.source_held and source_lock_descriptor==22; self.events.append("activate"); before=source.read_bytes(); before_sha=sha256(before).hexdigest(); backup=Path(args.backup_dir)/f"production-runtime-source.20260828T000000Z.{before_sha[:12]}.env"; backup.write_bytes(before); backup.chmod(0o600); after=before.replace(b"=LEGACY\n",b"=PRIVATE_PRIMARY\n"); source.write_bytes(after); source.chmod(0o600)
        write_json(args.receipt,{"schema_version":1,"action":"ACTIVATE_PRIVATE_PRIMARY_PRODUCT_SNAPSHOTS","status":"APPLIED","source_sha256_before":before_sha,"source_sha256_after":sha256(after).hexdigest(),"backup_sha256":before_sha,"backup_file":backup.name,"manifest_sha256":args.expected_manifest_sha256,"changed_keys":["PRODUCTION_PRODUCT_ESTIMATOR_SNAPSHOT_MODE"],"promotion_receipt_sha256":args.expected_promotion_receipt_sha256,"release_sha":args.expected_release_sha,"release_tree":args.expected_release_tree,"secrets_disclosed":False}); return 0
    def rollback(self,args,source,*,source_lock_descriptor):
        assert self.run_held and self.source_held and source_lock_descriptor==22; self.events.append("rollback")
        if self.fail_rollback:return 9
        activation=json.loads(Path(args.activation_receipt).read_text()); restored=(Path(args.backup_dir)/activation["backup_file"]).read_bytes(); before=digest(source); source.write_bytes(restored); source.chmod(0o600)
        write_json(args.receipt,{"schema_version":1,"action":"RESTORE_EXACT_PRE_ACTIVATION_SOURCE","status":"APPLIED","source_sha256_before":before,"source_sha256_after":sha256(restored).hexdigest(),"activation_receipt_sha256":args.expected_activation_receipt_sha256,"manifest_sha256":args.expected_manifest_sha256,"backup_sha256":sha256(restored).hexdigest(),"secrets_disclosed":False}); return 0
    def validate_maintenance(self,**kwargs):
        self.maintenance_checks+=1
        if self.maintenance_checks==self.fail_maintenance_check_at:raise queue.market_handoff.CollectorHandoffError("synthetic_live_legacy_restart")
        return {}
    def execute(self,args=None):
        h=self
        class RunLock:
            def __init__(s,path):s.held=False;s.descriptor=None
            def adopt_market_pipeline_maintenance(s,**kwargs):assert h.source_held and digest(kwargs["journal"])==kwargs["expected_journal_sha256"] and kwargs["release_sha"]==SHA;h.events.append("run_acquire");h.run_held=True;s.held=True;s.descriptor=21
            def binding(s):assert h.run_held;return {"nonce_sha256":"c"*64,"device":1,"inode":2}
            def restore_adopted_market_pipeline_maintenance(s):h.events.append("run_restore");h.run_held=False;s.held=False;s.descriptor=None
            def release(s):h.events.append("run_release");h.run_held=False;s.held=False;s.descriptor=None
        class SourceLock:
            descriptor=None
            def __init__(s,path):pass
            def acquire(s):h.events.append("source_acquire");h.source_held=True;s.descriptor=22
            def release(s):h.events.append("source_release");h.source_held=False;s.descriptor=None
        class Journal:
            def __init__(s,artifact_dir,**facts):facts.pop("run_lock",None);s.path=Path(artifact_dir)/"journal.json";s.payload={"status":"prepared","secrets_disclosed":False,**facts};write_json(s.path,s.payload)
            def update(s,status,**facts):
                assert h.run_held and h.source_held;h.events.append("journal:"+status);s.payload.update(facts);s.payload["status"]=status;write_json(s.path,s.payload)
                if status=="applied" and h.fail_applied_journal_after_persist:raise OSError("synthetic terminal journal failure")
            @classmethod
            def adopt(cls,path,*,run_lock):
                s=cls.__new__(cls);s.path=Path(path);s.payload=json.loads(s.path.read_text());s.payload["run_lock"]=run_lock.binding();s.update("interrupted_recovery_acquired");return s
        class Ops:
            def __init__(s,manifest,*,release_root=None):
                assert Path(release_root)==h.release_checkout
                s.manifest=Path(manifest);s.release_root=Path(release_root)
            def executor_inventory(s):return {"count":1,"owner":"queue-v1","overlap":False}
            def runtime_contract(s,values,*,expected_owner):return {"owner":expected_owner}
            def private_primary_legacy_inputs_off(s):
                if h.fail_terminal_legacy:raise queue.ProductionCutoverError("synthetic_legacy_restart")
                return {"status":"verified","legacy_input_units_active":0,"legacy_input_timers_enabled":0,"unit_count":6}
            def private_primary_snapshot_identity(s,*,expected_digest):
                if h.fail_terminal_snapshot:raise queue.ProductionCutoverError("synthetic_missing_snapshot")
                return {"status":"verified","snapshot_digest":expected_digest,"consumer_artifact_count":3}
            def private_primary_publication_outbox_zero(s):return {"status":"verified","open_outbox":0}
            def deploy_official(s,path,dig,**kw):
                assert h.run_held and h.source_held and digest(path)==dig;h.events.append("deploy:"+s.manifest.name);h.deploys.append(s.manifest);h.attestations.append(kw.get("private_primary_attestation"))
                if s.manifest==h.private and h.kill_before_private_deploy and not h.kill_fired:h.kill_fired=True;raise SyntheticSigkill()
                if (s.manifest==h.private and h.fail_private) or (s.manifest!=h.private and h.fail_legacy):raise queue.ProductionCutoverError("failed")
                if s.manifest==h.private and h.kill_after_private_deploy and not h.kill_fired:h.kill_fired=True;raise SyntheticSigkill()
                if s.manifest==h.private and h.advance_clock_after_private_deploy:h.clock_offset=121
                return {"status":"passed","product_readiness":{"consumer_count":3,"snapshot_digest":"b"*64,"snapshot_hash":"a"*64,"snapshot_version":1,"maximum_snapshot_age_seconds":1,"required_source_input_trace_count":9,"source_input_trace_sha256":"e"*64}}
        def authority(artifact_dir,*args,**kw):
            assert h.run_held and h.source_held;path=Path(artifact_dir)/f"authority-{len(h.authorities)+1}.json";write_json(path,{"n":len(h.authorities)+1});h.authorities.append(path);h.events.append("authority");return path,digest(path)
        real_write_final=p._write_final_receipt
        def write_final(path,payload):
            if Path(path)==h.final and h.kill_final_receipt and not h.kill_fired:h.kill_fired=True;raise SyntheticSigkill()
            return real_write_final(path,payload)
        real_datetime=datetime
        class HarnessDateTime(datetime):
            @classmethod
            def now(cls,tz=None):return real_datetime.now(timezone.utc)+timedelta(seconds=h.clock_offset)
        patches=[(p,"APPROVED_SECURE_ROOT",self.root),(p,"_command",self.command),(p,"_write_final_receipt",write_final),(queue,"ExclusiveRunLock",RunLock),(queue,"ImmutableSourceLock",SourceLock),(queue,"PhaseJournal",Journal),(queue,"ProductionOperations",Ops),(queue,"create_deploy_authority",authority),(queue,"reconcile_deploy_child_fence",lambda **kwargs:None),(queue,"git_binding",lambda:{"branch":"main","worktree":"clean","head":SHA,"origin_main":SHA}),(queue.market_handoff,"validate_committed_handoff",self.validate_maintenance),(updater,"_verify_inherited_source_lock",lambda *a,**k:None),(updater,"activate_private_primary_with_held_source_lock",self.activate),(updater,"rollback_private_primary_with_held_source_lock",self.rollback)]
        if h.advance_clock_after_private_deploy:patches.append((p,"datetime",HarnessDateTime))
        with ExitStack() as stack:
            for obj,name,value in patches:stack.enter_context(mock.patch.object(obj,name,value))
            return p.execute(args or self.args())

def test_contract_identity():
    assert p.PROMOTION_SNAPSHOT_CONTRACT==verifier.WEB_VIEW_CONTRACT and p.PROMOTION_REQUIRED_CHECKS==verifier.CHECKS

def test_success_lock_lifetime_in_process_and_fresh_authority():
    h=Harness();payload,code=h.execute();assert(code,payload["status"])==(0,"PASS");assert h.events[:2]==["source_acquire","run_acquire"] and h.events[-2:]==["run_release","source_release"];assert h.events.index("activate")<h.events.index("authority")<h.events.index("deploy:private.env");assert len(h.authorities)==1 and h.deploys==[h.private] and h.attestations[0] is not None;assert stat.S_IMODE(h.final.stat().st_mode)==0o600 and "must-not-leak" not in h.final.read_text()

def test_failure_exact_rollback_normalized_legacy_and_fresh_authority():
    h=Harness();original=h.runtime.read_bytes();h.fail_private=True;payload,code=h.execute();assert(code,payload["status"])==(3,"ROLLED_BACK");assert h.runtime.read_bytes()==original and len(h.authorities)==2 and h.authorities[0]!=h.authorities[1];assert h.deploys[0]==h.private and h.deploys[1].name=="legacy-product-only.env" and h.deploys[1]!=h.source;values=p._read_env(h.deploys[1].read_bytes(),label="legacy");assert all(values[k]==v for k,v in p.LEGACY_PRODUCT_MANIFEST_UPDATES.items());assert values["PRODUCTION_COIN_INFERENCE_RELAY_ENABLED"]=="0" and values["PRODUCTION_COIN_INFERENCE_PREVIEW_ENABLED"]=="false" and values["PRODUCTION_COIN_INFERENCE_SELECTION_ENABLED"]=="false" and values["PRODUCTION_OFFER_MODEL_PRICE_GUARD_ENABLED"]=="false";assert h.attestations[0] is not None and h.attestations[1] is None;assert h.events[-2:]==["run_restore","source_release"]

def test_failed_rollback_blocks_without_legacy_deploy():
    h=Harness();h.fail_private=h.fail_rollback=True;payload,code=h.execute();assert(code,payload["status"])==(4,"BLOCKED_MANUAL");assert len(h.authorities)==1 and h.deploys==[h.private]

def test_failed_legacy_redeploy_blocks_after_restore():
    h=Harness();original=h.runtime.read_bytes();h.fail_private=h.fail_legacy=True;payload,code=h.execute();assert(code,payload["status"])==(4,"BLOCKED_MANUAL");assert h.runtime.read_bytes()==original and len(h.authorities)==2

def test_no_raw_deploy_or_updater_subprocess():
    source=Path(p.__file__).read_text();assert "production_deploy_online.sh" not in source;assert "[sys.executable, str(UPDATER_SCRIPT)" not in source;assert "deploy_official(" in source

def test_persisted_applied_journal_failure_never_rolls_back_or_exposes_stale_pass_receipt():
    h=Harness();h.fail_applied_journal_after_persist=True;payload,code=h.execute();assert(code,payload["status"])==(0,"PASS");assert "PRODUCTION_PRODUCT_ESTIMATOR_SNAPSHOT_MODE=PRIVATE_PRIMARY" in h.runtime.read_text();assert "rollback" not in h.events;assert h.final.is_file();assert digest(h.final)==json.loads((h.artifacts/"journal.json").read_text())["receipt_sha256"];assert json.loads((h.artifacts/"journal.json").read_text())["status"]=="applied";assert h.events[-2:]==["run_release","source_release"]

def test_preflight_failure_restores_maintenance_lock_for_retry():
    h=Harness();h.fail_preflight=True;payload,code=h.execute();assert(code,payload["status"])==(4,"BLOCKED_MANUAL");assert payload["reason_code"]=="release_git_identity_mismatch";assert h.events==["source_acquire","run_acquire","run_restore","source_release"]

def test_live_legacy_restart_before_deploy_aborts_and_recovers_product_source():
    h=Harness();h.fail_maintenance_check_at=2;original=h.runtime.read_bytes();payload,code=h.execute();assert(code,payload["status"])==(3,"ROLLED_BACK");assert payload["reason_code"]=="market_maintenance_revalidation_failed";assert h.runtime.read_bytes()==original;assert h.private not in h.deploys;assert h.deploys and h.deploys[-1].name=="legacy-product-only.env";assert h.events[-2:]==["run_restore","source_release"]

def test_sigkill_between_terminal_journal_and_receipt_completes_pass_and_never_rolls_back():
    h=Harness();h.kill_final_receipt=True
    with pytest.raises(SyntheticSigkill):h.execute()
    assert json.loads((h.artifacts/"journal.json").read_text())["status"]=="applied" and not h.final.exists() and "rollback" not in h.events
    h.kill_final_receipt=False;before=list(h.deploys);payload,code=h.execute(h.recovery_args("rollback"));assert(code,payload["status"])==(0,"PASS");assert h.final.is_file() and h.deploys==before and "rollback" not in h.events;assert h.events[-2:]==["run_release","source_release"]

def test_terminal_pass_recovery_blocks_if_legacy_input_restarts():
    h=Harness();h.kill_final_receipt=True
    with pytest.raises(SyntheticSigkill):h.execute()
    h.kill_final_receipt=False;h.fail_terminal_legacy=True
    with pytest.raises(p.PromotionError,match="postdeploy_legacy_runtime_recheck_failed"):
        h.execute(h.recovery_args("rollback"))
    assert not h.final.exists() and "rollback" not in h.events

def test_terminal_pass_recovery_requires_bound_postdeploy_receipt():
    h=Harness();h.kill_final_receipt=True
    with pytest.raises(SyntheticSigkill):h.execute()
    (h.tx/"primary-20260828-001"/"post-deploy-verification.json").unlink()
    h.kill_final_receipt=False
    with pytest.raises(p.PromotionError,match="postdeploy_receipt"):
        h.execute(h.recovery_args("rollback"))
    assert not h.final.exists() and "rollback" not in h.events

def test_terminal_pass_recovery_blocks_when_current_snapshot_is_missing():
    h=Harness();h.kill_final_receipt=True
    with pytest.raises(SyntheticSigkill):h.execute()
    h.kill_final_receipt=False;h.fail_terminal_snapshot=True
    with pytest.raises(p.PromotionError,match="postdeploy_snapshot_runtime_recheck_failed"):
        h.execute(h.recovery_args("rollback"))
    assert not h.final.exists() and "rollback" not in h.events

def test_deploy_delay_past_freshness_window_cannot_issue_pass():
    h=Harness();h.advance_clock_after_private_deploy=True
    payload,code=h.execute()
    assert (code,payload["status"])==(3,"ROLLED_BACK")
    assert payload["failed_stage"] in {"maintenance", "postdeploy"}
    assert payload["reason_code"] in {
        "promotion_receipt_stale_or_future",
        "catchup_receipt_stale_or_future",
        "web_maintenance_journal_contract_invalid",
    }
    assert not (h.tx/"primary-20260828-001"/"post-deploy-verification.json").exists()

@pytest.mark.parametrize("boundary",["before","after"])
def test_sigkill_around_private_deploy_can_resume_idempotently(boundary):
    h=Harness();setattr(h,f"kill_{boundary}_private_deploy",True)
    with pytest.raises(SyntheticSigkill):h.execute()
    setattr(h,f"kill_{boundary}_private_deploy",False);payload,code=h.execute(h.recovery_args("resume"));assert(code,payload["status"])==(0,"PASS");assert h.final.is_file();assert "rollback" not in h.events;assert h.deploys[-1]==h.private

def test_stale_promotion_blocks_resume_but_does_not_block_exact_rollback():
    h=Harness();h.kill_before_private_deploy=True;original=h.runtime.read_bytes()
    with pytest.raises(SyntheticSigkill):h.execute()
    h.kill_before_private_deploy=False
    class FutureDateTime(datetime):
        @classmethod
        def now(cls,tz=None):return datetime.now(timezone.utc)+timedelta(seconds=121)
    args=h.recovery_args("resume")
    with mock.patch.object(p,"datetime",FutureDateTime):
        with pytest.raises(p.PromotionError,match="stale_or_future"):h.execute(args)
    args=h.recovery_args("rollback");payload,code=h.execute(args);assert(code,payload["status"])==(3,"ROLLED_BACK");assert h.runtime.read_bytes()==original and h.deploys[-1].name=="legacy-product-only.env"

def test_recovery_requires_exact_phase_journal_digest_before_any_mutation():
    h=Harness();h.kill_before_private_deploy=True
    with pytest.raises(SyntheticSigkill):h.execute()
    before=h.runtime.read_bytes();events=list(h.events);args=h.recovery_args("rollback");args.expected_phase_journal_sha256="0"*64
    with pytest.raises(p.PromotionError,match="recovery_phase_journal_cas_mismatch"):h.execute(args)
    assert h.runtime.read_bytes()==before and h.events==events

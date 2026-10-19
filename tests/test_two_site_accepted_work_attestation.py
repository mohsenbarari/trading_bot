"""Independent retained-proof fixtures: real Git, original bytes, no producer calls."""
from __future__ import annotations

import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

import copy
import hashlib
import json
import re
import subprocess

import pytest

from scripts import two_site_accepted_work_attestation as attest
from scripts import two_site_accepted_work_proofs as subject
from scripts import two_site_accepted_work_snapshot as custody
from tests.test_two_site_accepted_work_snapshot import GIT_ENV, REFS, read_counter


LEDGER = "docs/refactor/two-server/section-1/00-stage-ledger.md"
INVENTORY = "docs/refactor/two-server/section-1/inventory/"
GAP_PATH = INVENTORY + "scenario-records.json"
GAP_ID = "P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE"
GAP = GAP_PATH + "#" + GAP_ID
METRICS = ".local/two-site-refactor/autopilot/metrics.json"
USAGE = dict.fromkeys(("cache_read_tokens", "input_tokens", "output_tokens", "thinking_tokens"), 0)
PRIVATE = "private-fixture-740N-not-a-proof-or-error"
FIELDS = ["seed_id", "canonical_symbol", "scenario_key", "selector", "source_commit",
          "source_file_sha256", "receipt_path", "receipt_sha256", "handler_pointer",
          "evidence_limits_path", "evidence_limits_sha256", "evidence_limits_pointer"]
FIXED_LIMITS = ["LIFETIME_UNVERIFIED", "NO_BILLING_VALUE_STAGE_AUTHORITY", "NOT_FULL_HISTORY", "RETAINED_LEDGER_ONLY",
                "PRIVATE_PROOF_PHASE_ONLY", "PUBLIC_COST_REPORT_PENDING", "RETAINED_HISTORY_ONLY"]


def digest(body):
    return hashlib.sha256(body).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def raw(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode()


def blob_oid(body):
    return hashlib.sha1(b"blob " + str(len(body)).encode() + b"\0" + body).hexdigest()


def fixed(code, operation):
    with pytest.raises(subject._AcceptedWorkProofError) as caught:
        operation()
    assert caught.value.code == code and str(caught.value) == code
    assert PRIVATE not in str(caught.value)
    return caught.value


class ProofRepository:
    """Independently authored bytes committed in disposable Git."""

    def __init__(self, root, form="inline", linked=False, root_receipts=True):
        self.root, self.form = root, form
        root.mkdir(mode=0o700)
        self.run("init", "--initial-branch=fixture", "--template=")
        self.commit_bodies, self.artifacts = {}, {}
        self.source = {f"src/context{i}.py": f"constant_{i} = {i}\n".encode() for i in range(8)}
        self.source["src/market.py"] = (
            b"import math\r\n\r\ndef marker(fn): return fn\r\n@marker\r\n"
            b"def handle_buy(value):\r\n    return value + math.floor(0)\r\n\r\n"
            b"@marker\r\ndef handle_sell(value):\r\n    return value - math.floor(0)\r\n")
        for i in range(4):
            newline = (b"\n", b"\r\n", b"\r", b"\n")[i]
            self.source[f"src/helper{i}.py"] = newline.join([
                b"import math", b"", f"def helper_{i}(value):".encode(),
                ("    return value + len('line\u2028separator')" if i == 3 else
                 "    return value + math.floor(0)").encode(), b""])
        self.gap = {"schema_version": "two-server-refactor.scenario-records.v2",
                    "blocker_registry": {"items": [{"blocker_id": GAP_ID,
                    "resolution_stage": "P1-00b", "severity": "HIGH",
                    "owner": "CODEX_FINAL_REVIEWER", "required_evidence": ["literal witness"]}]}}
        self.source[GAP_PATH] = raw(self.gap)
        for path, body in self.source.items():
            self.put(path, body)
        self.parent = self.commit("fixture generation parent", list(self.source))
        self.accepted_path = "tests/test_retained_proof.py"
        self.after = (b"class TestLiteral:\n    def test_buy(self):\n        assert 2 + 2 == 4\n"
                      b"\n    def test_sell(self):\n        assert 7 - 3 == 4\n")
        self.put(self.accepted_path, self.after)
        self.accepted = self.commit("fixture accepted Source", [self.accepted_path])
        self.refs = dict(zip(REFS, (self.parent, self.accepted, self.parent)))
        for ref, commit in self.refs.items():
            self.run("update-ref", ref, commit)
        if linked:
            linked_root = root / "linked-checkout"
            self.run("worktree", "add", "--detach", str(linked_root), self.accepted)
            self.root = linked_root
        self.context = [f"src/context{i}.py" for i in range(8)]
        self.excerpts = []
        symbols = [("src/market.py", ["handle_buy", "handle_sell"], [(1, 1), (4, 6), (8, 10)])]
        symbols += [(f"src/helper{i}.py", [f"helper_{i}"], [(1, 1), (3, 4)]) for i in range(4)]
        for path, names, ranges in symbols:
            # Physical CRLF/CR/LF only. U+2028 inside a literal is not a source line.
            lines = re.findall(rb".*?(?:\r\n|\r|\n|$)", self.source[path])
            if lines[-1:] == [b""]:
                lines.pop()
            self.excerpts.append({"path": path, "sha256": digest(self.source[path]), "symbols": names,
                                  "ranges": [{"start": first, "end": last,
                                  "text": b"".join(lines[first - 1:last]).decode()}
                                  for first, last in ranges]})
        groups = sorted([{"path": e["path"], "symbols": sorted(e["symbols"])} for e in self.excerpts],
                        key=lambda item: item["path"])
        manifest = {path: digest(body) for path, body in self.source.items()}
        manifest[self.accepted_path] = None
        self.identity = {"schema_version": 1, "stage_id": "P1-00b", "gap_reference": GAP,
                         "scenario_key": "literal-batch", "symbol_groups": groups,
                         "source_sha256": digest(canonical(manifest)), "source_manifest": manifest}
        projection = {key: self.identity[key] for key in
                      ("stage_id", "gap_reference", "scenario_key", "symbol_groups", "source_sha256")}
        self.identity["fingerprint"] = digest(canonical(projection))
        self.work = digest(canonical({"kind": "two-site-logical-work", "schema_version": 1,
                          **{key: self.identity[key] for key in
                             ("stage_id", "gap_reference", "scenario_key", "symbol_groups")}}))
        self.task_id, self.controller = "retained-task-1", "retained-controller-1"
        self.prefix = "retained-proof-1"
        self.worker, self.reviewer = self.prefix + "-worker-0", self.prefix + "-reviewer-0"
        self.selectors = [self.accepted_path + "::TestLiteral::test_buy",
                          self.accepted_path + "::TestLiteral::test_sell"]
        bundle = {"head": self.parent, "gap_reference": GAP, "excerpts": self.excerpts,
                  "semantic_identity": self.identity}
        self.task = {"task_id": self.task_id, "goal": "Literal fixture, not business acceptance.\n"
                     "CANONICAL_SOURCE_EXCERPTS_JSON:\n" + canonical(bundle).decode(),
                     "context_files": self.context, "allowed_files": [self.accepted_path],
                     "requirements": ["Keep literal behavior", "Exercise both handlers"],
                     "test_selectors": self.selectors}
        self.sources = {path: {"sha256": digest(self.source[path]), "content": self.source[path].decode()}
                        for path in self.context}
        self.sources[self.accepted_path] = {"sha256": None, "content": None}
        self.worker_snapshot = {"task": self.task, "source_head": self.parent, "sources": self.sources}
        self.proposal = {"changes": [{"path": self.accepted_path, "original_sha256": None,
                                     "content": self.after.decode()}], "covered_requirements": [1, 2],
                         "findings": [], "status": "PROPOSAL_READY", "summary": "Two literal witnesses"}
        self.proposal_bytes = raw(self.proposal)
        self.native = {"state": "PASSED", "proposal_sha256": digest(self.proposal_bytes),
                       "selectors": self.selectors, "unsupported_specs": [], "returncode": 0,
                       "stdout_tail": "2 passed", "stderr_tail": "", "snapshot_files": 10,
                       "snapshot_bytes": 900, "isolation": "bwrap:ro-source,empty-home,clearenv,unshare-all"}
        self.review = {"changes": [], "covered_requirements": [1, 2], "findings": [],
                       "status": "VERIFIED", "summary": "Independent literal review"}
        self.worker_status = {"run_id": self.worker, "task_id": self.task_id, "head": self.parent,
            "role": "worker", "state": "PROPOSAL_READY", "result_sha256": digest(self.proposal_bytes),
            "conversation_id": "worker-conversation", "controller_run_id": self.controller,
            "authority": "PROPOSAL_ONLY_NOT_ACCEPTANCE"}
        self.reviewer_status = {"run_id": self.reviewer, "task_id": self.task_id, "head": self.parent,
            "role": "verifier", "state": "VERIFIED", "result_sha256": digest(raw(self.review)),
            "conversation_id": "reviewer-conversation", "controller_run_id": self.controller,
            "authority": "PROPOSAL_ONLY_NOT_ACCEPTANCE", "input_proposal_sha256": digest(self.proposal_bytes),
            "input_test_evidence_sha256": digest(raw(self.native)),
            "input_worker_conversation_id": "worker-conversation", "input_worker_run_id": self.worker,
            "review_transport_sha256": "1" * 64}
        self.reviewer_snapshot = {**copy.deepcopy(self.worker_snapshot), "proposal": self.proposal_bytes.decode(),
                                  "test_evidence": copy.deepcopy(self.native)}
        self.root_result = {"schema_version": "1.0", "state": "CONTINUE", "disposition": "NONE",
            "category": "NONE", "summary": "Accepted fixture Source only", "commit": self.accepted,
            "next_action": "Continue fixture", "blockers": [], "gemini_worker_run_id": self.worker,
            "gemini_reviewer_run_id": self.reviewer, "proposal_sha256": digest(self.proposal_bytes)}
        self.root_status = {"state": "CONTINUE", "head_before": self.parent, "head_after": self.accepted,
            "gemini_worker_run_id": self.worker, "gemini_reviewer_run_id": self.reviewer,
            "proposal_sha256": digest(self.proposal_bytes), "blockers": [], "protected_refs": self.refs}
        self.root_receipts = root_receipts and form == "inline"
        self.write_retained()
        self.publish()

    def run(self, *args, input=None):
        env = dict(GIT_ENV, GIT_AUTHOR_NAME="Fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
                   GIT_COMMITTER_NAME="Fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid",
                   GIT_AUTHOR_DATE="2000-01-01T00:00:00+0000",
                   GIT_COMMITTER_DATE="2000-01-01T00:00:00+0000")
        args = ["/usr/bin/git", "--no-replace-objects", "--literal-pathspecs", "-C", str(self.root),
                "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "protocol.allow=never", *args]
        result = subprocess.run(args, input=input, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=10, check=False)
        assert result.returncode == 0, (result.returncode, result.stderr)
        return result.stdout

    def put(self, path, body):
        leaf = self.root / path
        leaf.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        leaf.write_bytes(body)
        leaf.chmod(0o600)
        return leaf

    def commit(self, message, paths):
        self.run("add", "--", *paths)
        self.run("commit", "--allow-empty", "-m", message)
        sha = self.run("rev-parse", "HEAD").strip().decode()
        self.commit_bodies[sha] = self.run("cat-file", "commit", sha)
        return sha

    def artifact(self, path, value):
        body = value if type(value) is bytes else raw(value)
        self.artifacts[path] = body
        self.put(path, body)
        return digest(body)

    def write_retained(self):
        self.task_path = f".local/two-site-refactor/tasks/{self.task_id}.json"
        self.worker_dir = f".local/two-site-refactor/proposals/{self.worker}/"
        self.reviewer_dir = f".local/two-site-refactor/proposals/{self.reviewer}/"
        self.native_path = f".local/two-site-refactor/pipeline/{self.prefix}/pre-review-tests-0.json"
        self.artifact(self.task_path, self.task)
        self.artifact(self.worker_dir + "task.json", self.worker_snapshot)
        self.artifact(self.worker_dir + "result.json", self.proposal_bytes)
        self.artifact(self.worker_dir + "status.json", self.worker_status)
        self.artifact(self.native_path, self.native)
        self.artifact(self.reviewer_dir + "task.json", self.reviewer_snapshot)
        self.artifact(self.reviewer_dir + "result.json", self.review)
        self.artifact(self.reviewer_dir + "status.json", self.reviewer_status)
        self.root_dir = f".local/two-site-refactor/autopilot/runs/{self.controller}/"
        if self.root_receipts:
            self.artifact(self.root_dir + "result.json", self.root_result)
            self.artifact(self.root_dir + "status.json", self.root_status)
        self.metric = {"run_id": self.controller, "state": "CONTINUE", "head_before": self.parent,
            "head_after": self.accepted, "gemini_worker_run_id": self.worker,
            "gemini_reviewer_run_id": self.reviewer, "proposal_sha256": digest(self.proposal_bytes),
            "task_id": self.task_id, "task_sha256": digest(json.dumps(self.task, sort_keys=True).encode()),
            "gap_reference": GAP, "semantic_identity": {key: self.identity[key] for key in
                ("fingerprint", "gap_reference", "scenario_key", "source_sha256", "stage_id")},
            "work_lineage": {"schema_version": 1, "fingerprint": self.work}}
        self.metrics = [self.metric]
        self.artifact(METRICS, self.metrics)

    def publish(self, transform=None):
        """Recommit legitimate locators after a targeted fixture correction, not mock proof."""
        accepted_file = {"path": self.accepted_path, "mode": "100644",
                         "sha256": digest(self.after), "utf8_bytes": len(self.after)}
        accepted = {"accepted_file": accepted_file, "accepted_parent": self.parent,
                    "accepted_source_commit": self.accepted}
        self.source_section = {**accepted, "source_head": self.parent,
            "source_task_id": self.task_id, "source_task_snapshot_sha256": digest(raw(self.worker_snapshot)),
            "source_proposal_sha256": digest(self.proposal_bytes)}
        self.actor = {"controller_run_id": self.controller,
            "worker": {"run_id": self.worker, "conversation_id": "worker-conversation", "state": "PROPOSAL_READY"},
            "reviewer": {"run_id": self.reviewer, "conversation_id": "reviewer-conversation", "state": "VERIFIED",
                         "role": "verifier", "findings": [], "result_sha256": digest(raw(self.review))}}
        self.git = {"accepted_parent": self.parent, "accepted_source_commit": self.accepted,
                    "single_accepted_file": self.accepted_path, "current_file_sha256": digest(self.after),
                    "file_mode": "100644", "current_file_utf8_bytes": len(self.after)}
        self.scope = {"scenarios_covered": ["buy-literal", "sell-literal"],
            "source_assignment": {"assignment_id": "fixture-source", "sha256": "2" * 64},
            "registration_assignment": {"assignment_id": "fixture-register", "scope_digest": "3" * 64, "stage": "P1-00b"}}
        self.handlers = [{"seed_id": f"literal-{name}", "canonical_symbol": "src/market.py:handle_" + name,
            "scenario_key": name + "-literal", "selector": self.selectors[index],
            "source_commit": self.accepted, "source_file_sha256": digest(self.after),
            "evidence_limits": {"scope": "fixture literal only"}} for index, name in enumerate(("buy", "sell"))]
        self.receipt_path = INVENTORY + "fixture-" + self.form + ".json"
        self.registration_path = INVENTORY + "fixture-registration.json"
        self.custody_path = INVENTORY + "fixture-custody.json"
        self.registration, self.custody_doc = None, None
        provenance = {}
        if self.form == "inline":
            self.source_section["generation_source_manifest"] = self.identity["source_manifest"]
            self.actor.update(generation_task_id=self.task_id, generation_task_sha256=digest(raw(self.task)),
                              proposal_sha256=digest(self.proposal_bytes))
            self.actor["supervisor_acceptance"] = {"cycle_id": self.controller, "state": "CONTINUE",
                "head_before": self.parent, "head_after": self.accepted,
                "result_sha256": digest(raw(self.root_result)), "status_sha256": digest(raw(self.root_status))}
        else:
            self.source_section["source_manifest_sha256"] = {
                path: value for path, value in self.identity["source_manifest"].items() if value is not None}
            if self.form == "recovery":
                self.custody_doc = {"schema_version": 1, "source": {
                    "accepted_parent": self.parent, "accepted_source_commit": self.accepted,
                    "source_head": self.parent, "source_task_id": self.task_id,
                    "source_task_snapshot_sha256": digest(raw(self.worker_snapshot)),
                    "accepted_test_file": {"path": self.accepted_path, "sha256": digest(self.after)}}}
                self.put(self.custody_path, raw(self.custody_doc))
                self.custody_commit = self.commit("fixture nested custody", [self.custody_path])
                provenance["custody"] = {"path": self.custody_path, "commit": self.custody_commit,
                                         "sha256": digest(raw(self.custody_doc))}
                self.source_section["generation_custody"] = dict(provenance["custody"])
            self.registration = {"schema_version": 1, "stage": "P1-00b", "canonical_gap": GAP_ID,
                "status": "PENDING_RECONCILIATION", "source": copy.deepcopy(self.source_section),
                "actor": copy.deepcopy(self.actor), "scope": copy.deepcopy(self.scope),
                "git": copy.deepcopy(self.git), "registered_handlers": copy.deepcopy(self.handlers)}
            if self.form == "login":
                self.registration["source"]["accepted_commit"] = self.registration["source"].pop("accepted_source_commit")
                self.registration["git"]["accepted_commit"] = self.registration["git"].pop("accepted_source_commit")
            self.put(self.registration_path, raw(self.registration))
            self.registration_commit = self.commit("fixture registration", [self.registration_path])
            anchor = {"path": self.registration_path, "commit": self.registration_commit,
                      "sha256": digest(raw(self.registration))}
            if self.form == "recovery":
                provenance["registration"] = anchor
                self.source_section = {**accepted, "registration_source_pointer": "/source"}
                self.actor = {"controller_run_id": self.controller, "registration_actor_pointer": "/actor"}
                self.scope = {"registration_scope_pointer": "/scope"}
                self.git = {"registration_git_pointer": "/git"}
            else:
                provenance["accepted_login_actions_registration"] = anchor
                self.source_section = {**accepted,
                    "registration_source_manifest_ref": self.registration_path + "#/source/source_manifest_sha256"}
                self.actor = {"controller_run_id": self.controller,
                    "registration_actor_ref": self.registration_path + "#/actor",
                    "worker_conversation_ref": self.registration_path + "#/actor/worker",
                    "reviewer_conversation_ref": self.registration_path + "#/actor/reviewer",
                    "source_proposal_ref": self.registration_path + "#/source/source_proposal_sha256"}
                self.scope = {"source_assignment_ref": self.registration_path + "#/scope/source_assignment",
                              "registration_assignment_ref": self.registration_path + "#/scope/registration_assignment"}
        self.receipt = {"schema_version": 1, "stage": "P1-00b", "canonical_gap": GAP_ID,
            "status": "PARTIAL_RECONCILIATION", "source": self.source_section, "actor": self.actor,
            "scope": self.scope, "git": self.git, "registered_handlers": self.handlers,
            "provenance_references": provenance}
        if transform:
            transform(self.receipt)
        self.put(self.receipt_path, raw(self.receipt))
        self.receipt_commit = self.commit("fixture reconciliation", [self.receipt_path])
        self.rows = []
        for index, handler in enumerate(self.handlers):
            limits_path = self.receipt_path if self.form != "recovery" else self.registration_path
            limits_sha = digest(raw(self.receipt if self.form != "recovery" else self.registration))
            self.rows.append([handler[key] for key in FIELDS[:6]] + [self.receipt_path,
                digest(raw(self.receipt)), f"/registered_handlers/{index}", limits_path,
                limits_sha, f"/registered_handlers/{index}/evidence_limits"])
        self.index = {"common_receipt_pointers": ["/source", "/actor", "/scope", "/git"],
                      "field_order": FIELDS, "rows": self.rows}
        self.write_index()

    def write_index(self):
        self.ledger = b"# Disposable proof ledger\nReviewed current seven-scenario Source index (data):\n```json\n"
        self.ledger += canonical(self.index) + b"\n```\n"
        self.put(LEDGER, self.ledger)
        self.head = self.commit("fixture canonical index", [LEDGER])


def test_attestation_three_committed_forms_and_exact_pointers(tmp_path):
    for form in ("inline", "recovery", "login"):
        repo = ProofRepository(tmp_path / form, form=form)
        with custody._proof_snapshot(repo.root) as snapshot:
            resolver = attest._Attestations(snapshot)
            rows = resolver.index()
            assert rows == [{**dict(zip(FIELDS, row)), "index_ordinal": index}
                            for index, row in enumerate(repo.rows)]
            item = resolver.attestation(rows[0])
            assert set(item) == {"row", "receipt", "documents", "source", "actor", "scope", "git"}
            assert item["receipt"] == repo.receipt and item["row"] == rows[0]
            expected_docs = [(repo.receipt_path, digest(raw(repo.receipt)), repo.receipt)]
            if form != "inline":
                expected_docs += [(repo.registration_path, digest(raw(repo.registration)), repo.registration)]
            if form == "recovery":
                expected_docs += [(repo.custody_path, digest(raw(repo.custody_doc)), repo.custody_doc)]
            assert item["documents"] == expected_docs
            assert resolver.pointer({"a/b": {"~": [7]}}, "/a~1b/~0/0", repo.receipt_path,
                                    digest(raw(repo.receipt))) == 7
            for pointer in ("/registered_handlers/00", "/registered_handlers/-1", "/bad~2", "/missing"):
                with pytest.raises(attest._ProofIssue) as caught:
                    resolver.pointer(repo.receipt, pointer, repo.receipt_path, digest(raw(repo.receipt)))
                assert caught.value.code == "REFERENCE_CONFLICT"
            same = resolver.proof_refs()
            assert same == sorted(same, key=lambda r: (r["path"], r["sha256"], r["pointer"]))
            assert {tuple(r.values()) for r in same} == {tuple(r.values()) for r in resolver.proof_refs()}

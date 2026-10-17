from __future__ import annotations
import copy
import hashlib
import json
import math
import re
import sys
import pytest
from scripts import two_site_proposal_runner as sdk
from tests import two_site_historical_lab_image as uut
from tests import test_two_site_work_cost_witness_helpers as h
from tests.test_two_site_work_cost_retained_call_frame import _inspect_graph

REGISTRY = ".local/two-site-refactor/worktrees.yaml"
FIXED_ROOT = "/tmp/ae-historical-reference/LAB"
ROOT_TASK_SHA = "b4a25c85f7b141d129ccf1ce6dda70b731ceca83472e2a3ae177f30bf5d370c0"
ROOT_ATTESTATION_SHA = "465b303935d941ef33bd7d903125005a225c559c8dbf1274d4127a252bf48172"
ROOT_TABLE_SHA = "a07392b69dff42c1d415488e892e66f1bdc38a8ce184425cba9cc6b5e6c00a78"
ROOT_DATA_SHA = "efda3b668ed33f671da7c270fd480d4394c548dbc2fb572bcad588652c31566c"
DATA_NAMES = ("CASES", "BEFORE", "AFTER", "TREES", "PINS", "SUPPORT", "ORIGINS",
              "HANDLER", "ANCHOR", "TEST", "GAP", "ANCHOR_TEXT", "PROBE", "NATIVE")
ARMED = False
IO_EVENTS = []
SUBJECT_CALLS = 0

def audit(event, arguments):
    if ARMED and (event == "open" or event.startswith(("os.", "socket.", "subprocess."))):
        IO_EVENTS.append(event)
        raise AssertionError("image renderer performed IO")

sys.addaudithook(audit)

PLAN_SHA = {'unchanged403-CODEX': 'f55c1ab8f7bbd4850baac1da3c5a63a50ce6f0bc23f120a4275e82e38a7ec920', 'changed404-CODEX': 'f1d096cdc289fb20e46f8a15a0280ba2d7ed530dc08163ce44607939826a6849', 'alias403-CODEX': 'db6109fca2a86c1a8912fd40721aec3c8fd9d19d1d6c0a5ecf06244693c2adb8', 'batch403404-CODEX': 'b527c8c477961dc86eacf9eb3a7981633397cf4ec7f44e3b5569de75db20e3a5', 'unchanged403-GEMINI-0': 'a074d9905ec14405541b9e9dfdc254fd37370ac30260970ccc6caa598b630af8', 'unchanged403-GEMINI-1': '42fbc2bb9e2a51bd4c68bc22ee523db74f21e107aa8f24c787b197a01a220fa7', 'unchanged403-GEMINI-2': 'dc35546e57b6965bac0b2d555b13e5a023576d2e1ce2ef745dacb9ad3932f71f', 'changed404-GEMINI-0': '78e6aa7fd0499193945ae71497a755f508de55c8e98804d81d680525b0156ac9', 'changed404-GEMINI-1': 'f962b56c7c2a2c0cde60b97562507449bc1d356a7397a349548ba03d4c7decce', 'changed404-GEMINI-2': 'd753149944a68dfa847170aa627f1d2cb379ebfb5d75217571cbcdbbd5f0051d', 'alias403-GEMINI-0': '2698455c9206387467f63874c2b44a80cf605e5609fdf7703f2a58f74e6a36b8', 'alias403-GEMINI-1': '5e618093c203750cf325b52eda3561b6034e808630f57daf98778ca6b889d238', 'alias403-GEMINI-2': 'bce0982fa3974c38619f459fdce9448e3e1226fa77ff467d7c2788edbcfb7e2a', 'batch403404-GEMINI-0': '28c31e0beb2931bf9384350a2ded5067e160c968682f09c88f54b75ee79fd002', 'batch403404-GEMINI-1': 'f2bfc6ca03f3dce5f371e065f46fb5fe892c624ddcae65550af7e8b8f8e672bd', 'batch403404-GEMINI-2': '9be8dce089556959f604c59d623be81fe61fa35849f17505f61527bba1118c49'}
CASES = ('unchanged403-CODEX', 'changed404-CODEX', 'alias403-CODEX', 'batch403404-CODEX', 'unchanged403-GEMINI-0', 'unchanged403-GEMINI-1', 'unchanged403-GEMINI-2', 'changed404-GEMINI-0', 'changed404-GEMINI-1', 'changed404-GEMINI-2', 'alias403-GEMINI-0', 'alias403-GEMINI-1', 'alias403-GEMINI-2', 'batch403404-GEMINI-0', 'batch403404-GEMINI-1', 'batch403404-GEMINI-2')
SUPPORT = {'config/unit-test.env.example': '801525a75e9d6d9310311cd7b9ba80141551e7b86cd0e720ddb41a37a6f3a770', 'scripts/two_site_pre_review_pytest_gate.py': '525c300668da01a8b82bd8488de708ee79db63ae0665b07828e29c74105b8535', 'tests/test_p1_00_characterization_network_isolation.py': 'e1aee47359a9f72eac838b000537992434c66eaf903d30cce40634da01da5b69'}

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def native(value, tuples=False):
    visited = 0
    def walk(node, depth):
        nonlocal visited
        visited += 1
        assert visited <= 2048 and depth <= 16
        kind = type(node)
        if kind is dict:
            if not all(type(key) is str for key in node):
                raise AssertionError("non-native historical image key")
            return {key: walk(child, depth + 1) for key, child in node.items()}
        if kind is list or tuples and kind is tuple:
            return [walk(child, depth + 1) for child in node]
        if not (node is None or kind is str or kind is int or kind is bool or kind is float):
            raise AssertionError("non-native historical image value")
        assert kind is not float or math.isfinite(node)
        return node
    return walk(value, 0)


def data_projection(owner):
    # Independent Root Task/Source/native/Git DATA pin, not template selfhash.
    values = {name: vars(owner.data)[name] for name in DATA_NAMES}
    plain = {name: native(value, tuples=True) for name, value in values.items()}
    def type_tree(value):
        kind = type(value)
        if kind is dict:
            return ["dict", [[key, type_tree(value[key])] for key in sorted(value)]]
        if kind is tuple:
            return ["tuple", [type_tree(v) for v in value]]
        if kind is list:
            return ["list", [type_tree(v) for v in value]]
        if kind is str:
            return "str"
        if kind is int:
            return "int"
        if kind is bool:
            return "bool"
        assert value is None
        return "NoneType"
    return [{name: type_tree(value) for name, value in values.items()}, plain]


def ready(owner):
    native(PLAN_SHA)
    assert type(PLAN_SHA) is dict and set(PLAN_SHA) == set(CASES)
    assert all(type(k) is str and type(v) is str and re.fullmatch("[0-9a-f]{64}", v)
               for k, v in PLAN_SHA.items())
    assert sha(canonical(PLAN_SHA)) == ROOT_TABLE_SHA
    assert re.fullmatch("[0-9a-f]{64}", ROOT_DATA_SHA), "independent Root DATA pin pending"
    assert sha(canonical(data_projection(owner))) == ROOT_DATA_SHA
    assert type(owner.data.NATIVE) is tuple and len(owner.data.NATIVE) == 4
    raw_bytes = 0
    for index, pair in enumerate(owner.data.NATIVE):
        assert type(pair) is tuple and len(pair) == 2
        for phase, text in enumerate(pair):
            assert type(text) is str
            raw_bytes += len(text.encode())
            assert sha(text.encode()) == owner.data.PINS[index][3 + phase]
            body = sdk.unique_json(text)
            fields = ({"state", "proposal_sha256", "selectors", "unsupported_specs", "returncode",
                       "stdout_tail", "stderr_tail", "snapshot_files", "snapshot_bytes", "isolation"}
                      if phase == 0 else {"returncode", "stdout", "stderr", "snapshot_files", "snapshot_bytes",
                                           "git_ancestry", "isolation"})
            assert type(body) is dict and set(body) == fields
            assert type(body["returncode"]) is int and body["returncode"] == 0
            assert type(body["snapshot_files"]) is int and type(body["snapshot_bytes"]) is int
            if phase == 0:
                assert body["state"] == "PASSED" and body["proposal_sha256"] == owner.data.PINS[index][2]
                assert type(body["selectors"]) is list and all(type(v) is str for v in body["selectors"])
                assert body["unsupported_specs"] == [] and type(body["unsupported_specs"]) is list
                assert type(body["stdout_tail"]) is str and type(body["stderr_tail"]) is str
            else:
                assert body["git_ancestry"] is None and type(body["stdout"]) is str and type(body["stderr"]) is str
            assert type(body["isolation"]) is str
    assert raw_bytes == 3317


def assert_image(image, root, case):
    native(image)
    _inspect_graph(image, reject_duplicates=True)
    assert type(image) is dict and set(image) == {"schema_version", "state", "case_id", "root",
        "head_before", "head_after", "before_files", "files", "public_return", "task", "sources"}
    assert type(image["schema_version"]) is int and image["schema_version"] == 1
    assert image["state"] == "LABORATORY_IMAGE_ONLY" and image["case_id"] == case
    public = image["public_return"]
    assert type(public) is dict and set(public) == {"schema_version", "state", "case_id", "root",
                                                  "entries", "native_refs", "provenance"}
    assert image["root"] == public["root"] == root
    assert image["head_before"] == public["provenance"]["head_before"]
    assert image["head_after"] == public["provenance"]["head_after"]
    assert set(image["task"]) == {"task_id", "goal", "context_files", "allowed_files", "requirements", "test_selectors"}
    assert set(image["sources"]) == set(image["task"]["context_files"] + image["task"]["allowed_files"])
    for source in image["sources"].values():
        assert type(source) is dict and set(source) == {"sha256", "content"}
        assert (type(source["sha256"]) is str and type(source["content"]) is str
                or source["sha256"] is None and source["content"] is None)
        if source["content"] is not None:
            assert source["sha256"] == sha(source["content"].encode())
    for field in ("files", "before_files"):
        specs = image[field]
        assert type(specs) is dict and set(SUPPORT) <= set(specs)
        for path, spec in specs.items():
            assert type(path) is str and not path.startswith("/") and "\\" not in path
            assert not any(part in ("", ".", "..") for part in path.split("/"))
            assert path.split("/")[0] != ".git" and type(spec) is dict
            assert type(spec["mode"]) is int and spec["mode"] in (0o600, 0o644)
            assert type(spec["sha256"]) is str and re.fullmatch("[0-9a-f]{64}", spec["sha256"])
            if path in SUPPORT:
                assert spec == {"kind": "ROOT_SOURCE", "sha256": SUPPORT[path], "mode": 0o644}
            else:
                assert set(spec) == {"kind", "sha256", "mode", "text"}
                assert spec["kind"] == "UTF8" and type(spec["text"]) is str
                assert sha(spec["text"].encode()) == spec["sha256"]
    assert public["provenance"]["artifact_sha256"] == {p: s["sha256"] for p, s in image["files"].items()}
    assert REGISTRY not in image["before_files"]
    label = case.split("-")[0]
    assignment = "WA-P1-00B-FREEZE-20260904-001" if label == "batch403404" else "WA-AE686-LAB-" + label
    registry = {"integration": {"path": root, "branch": "main", "active_assignment_id": assignment}}
    exact_text = sdk.serialize_json(registry).decode()
    spec = image["files"][REGISTRY]
    assert spec == {"kind": "UTF8", "text": exact_text, "sha256": sha(exact_text.encode()), "mode": 0o644}
    assert public["provenance"]["artifact_sha256"][REGISTRY] == spec["sha256"]
    # Exactly five guarded slots normalize, while raw image identities stay kept.
    normalized = copy.deepcopy(image)
    registry["integration"]["path"] = FIXED_ROOT
    fixed_text = sdk.serialize_json(registry).decode()
    fixed_sha = sha(fixed_text.encode())
    normalized["root"] = normalized["public_return"]["root"] = FIXED_ROOT
    normalized["files"][REGISTRY]["text"] = fixed_text
    normalized["files"][REGISTRY]["sha256"] = fixed_sha
    normalized["public_return"]["provenance"]["artifact_sha256"][REGISTRY] = fixed_sha
    assert sha(canonical(normalized)) == PLAN_SHA[case]


def assert_retained(retained):
    for raw, snapshot, fingerprint, ids in retained:
        h.assert_custody_unchanged(raw, snapshot)
        assert _inspect_graph(raw)[0] == fingerprint


def call_image(owner, root, case, retained):
    global ARMED, SUBJECT_CALLS
    assert len(retained) < 64
    constants, inputs, prior = [], [root, case], tuple(retained)
    input_snapshot = golden_snapshot = golden_fingerprint = None
    raw = snapshot = fingerprint = ids = None
    assert_retained(prior)
    def custody():
        failures = []
        def check(label, fn):
            try:
                fn()
            except BaseException:
                failures.append(label)
        def object_check(value, snap, fp):
            h.assert_custody_unchanged(value, snap)
            assert _inspect_graph(value)[0] == fp
        def binding(name, value):
            assert vars(owner.data)[name] is value
        def collection():
            assert len(retained) == len(prior)
            assert all(a is b for a, b in zip(retained, prior))
        if input_snapshot is not None:
            check("inputs", lambda: h.assert_custody_unchanged(inputs, input_snapshot))
        for name, value, snap, fp in constants:
            check("binding:" + name, lambda n=name, v=value: binding(n, v))
            check("constant:" + name, lambda v=value, s=snap, f=fp: object_check(v, s, f))
        check("retained-list", collection)
        for number, (value, snap, fp, _) in enumerate(prior):
            check("prior:" + str(number), lambda v=value, s=snap, f=fp: object_check(v, s, f))
        if golden_snapshot is not None:
            check("golden", lambda: object_check(PLAN_SHA, golden_snapshot, golden_fingerprint))
        if snapshot is not None:
            check("raw", lambda: object_check(raw, snapshot, fingerprint))
        assert not failures, "custody violation: " + ",".join(failures)
    try:
        input_snapshot = h.capture_custody(inputs)
        golden_snapshot, golden_fingerprint = h.capture_custody(PLAN_SHA), _inspect_graph(PLAN_SHA)[0]
        for name in DATA_NAMES:
            value = vars(owner.data)[name]
            constants.append((name, value, h.capture_custody(value), _inspect_graph(value)[0]))
        forbidden = _inspect_graph(inputs)[1] | _inspect_graph(PLAN_SHA)[1]
        for _, value, _, _ in constants:
            forbidden.update(_inspect_graph(value)[1])
        for _, _, _, prior_ids in prior:
            forbidden.update(prior_ids)
        ready(owner)
        SUBJECT_CALLS += 1
        ARMED = True
        try:
            raw = owner.render_image(root, case)
        finally:
            ARMED = False
        custody()
        raw_snapshot = h.capture_custody(raw)
        fingerprint = _inspect_graph(raw)[0]
        snapshot = raw_snapshot
        _, ids = _inspect_graph(raw, forbidden_ids=forbidden, reject_duplicates=True)
        assert_image(raw, root, case)
    finally:
        ARMED = False
        custody()
    retained.append((raw, snapshot, fingerprint, ids))
    return raw


def test_all_sixteen_and64_whole_actual_raw_returns():
    retained = []
    for number in range(4):
        for case in CASES:
            root = "/tmp/ae-image-custody-" + str(number) + "/" + case
            call_image(uut, root, case, retained)
    assert len(retained) == 64
    assert_retained(retained)
    before = SUBJECT_CALLS
    with pytest.raises(AssertionError):
        call_image(uut, "/tmp/ae-image-bound/LAB", CASES[0], retained)
    assert SUBJECT_CALLS == before and IO_EVENTS == []


def test_foreign_invalid_native_inputs_no_hooks_or_io():
    foreign = [h.ForeignDict(), h.ForeignList(), h.ForeignOpaqueLeaf(), h.ForeignClassAllHooks()]
    roots = [None, True, 3, b"/tmp/LAB", "relative", "/", "/tmp/../LAB", "/tmp//LAB", "/tmp/LAB/",
             "/tmp/a\\b", "/tmp/" + chr(0), "/tmp/" + chr(0xd800), "/tmp/" + "x" * 1024, h.ForeignStrKey("/tmp/LAB")] + foreign
    cases = [None, True, 1, "unknown", "unchanged403-GEMINI-3", h.ForeignStrKey(CASES[0])] + foreign
    retained, inputs = [], [roots, cases]
    snapshot = h.capture_custody(inputs)
    with h.armed_hooks():
        for root in roots:
            with pytest.raises(ValueError, match="^invalid historical lab image$"):
                call_image(uut, root, CASES[0], retained)
        for case in cases:
            with pytest.raises(ValueError, match="^invalid historical lab image$"):
                call_image(uut, "/tmp/ae-image-input/LAB", case, retained)
        h.assert_custody_unchanged(inputs, snapshot)
        assert h.get_total_hook_calls() == 0
    assert retained == [] and IO_EVENTS == []


@pytest.mark.parametrize("error", (ValueError("ordinary"), KeyboardInterrupt("identity"), SystemExit(7)))
def test_actual_helper_baseexception_identity_and_raw_custody(error):
    global ready
    for stage in ("preflight", "subject"):
        for mutate in (False, True, "replace"):
            retained = []
            call_image(uut, "/tmp/ae-image-exception/healthy", CASES[0], retained)
            prior = retained[0][0]
            support = uut.data.SUPPORT
            key = next(iter(support))
            old_support, old_state, old_sha = support[key], prior["state"], PLAN_SHA[CASES[0]]
            original_ready, original_prescribe, reached = ready, uut.prescribe, []
            before = SUBJECT_CALLS
            def fail(*args):
                reached.append(error)
                if mutate:
                    if mutate == "replace":
                        uut.data.SUPPORT = dict(support)
                    else:
                        support[key] = "0" * 64
                    prior["state"] = "BROKEN"
                    PLAN_SHA[CASES[0]] = "0" * 64
                raise error
            if stage == "preflight":
                ready = fail
            else:
                uut.prescribe = fail
            try:
                with pytest.raises(AssertionError if mutate else type(error)) as caught:
                    call_image(uut, "/tmp/ae-image-exception/failure", CASES[0], retained)
                if mutate:
                    label = "binding:SUPPORT" if mutate == "replace" else "constant:SUPPORT"
                    for label in (label, "prior:0", "golden"):
                        assert label in str(caught.value)
                else:
                    assert caught.value is error
                assert reached == [error]
                assert SUBJECT_CALLS == before + (stage == "subject")
            finally:
                ready, uut.prescribe = original_ready, original_prescribe
                uut.data.SUPPORT = support
                support[key], prior["state"], PLAN_SHA[CASES[0]] = old_support, old_state, old_sha
            assert_retained(retained)
            call_image(uut, "/tmp/ae-image-exception/restored", CASES[0], retained)


@pytest.mark.parametrize("fault", ("data-type", "data-probe", "data-native", "golden", "joint"))
def test_data_golden_and_joint_zero_subject_calls(fault):
    retained = []
    call_image(uut, "/tmp/ae-image-joint/healthy", CASES[0], retained)
    original_probe, original_native, original_sha = uut.data.PROBE, uut.data.NATIVE, PLAN_SHA[CASES[0]]
    before = SUBJECT_CALLS
    try:
        if fault == "data-type":
            uut.data.NATIVE = list(original_native)
        elif fault == "data-native":
            uut.data.NATIVE = ((original_native[0][0] + " ", original_native[0][1]),) + original_native[1:]
        elif fault == "data-probe" or fault == "joint":
            uut.data.PROBE = original_probe + "# coherent DATA corruption\n"
        if fault == "golden" or fault == "joint":
            PLAN_SHA[CASES[0]] = "0" * 64
        with pytest.raises(AssertionError):
            call_image(uut, "/tmp/ae-image-joint/forbidden", CASES[0], retained)
        assert SUBJECT_CALLS == before
        assert_retained(retained)
    finally:
        uut.data.PROBE = original_probe
        uut.data.NATIVE = original_native
        PLAN_SHA[CASES[0]] = original_sha
    call_image(uut, "/tmp/ae-image-joint/restored", CASES[0], retained)


@pytest.mark.parametrize("fault", ("missing", "extra", "bool", "root", "registry-mode", "registry-text",
    "registry-sha", "registry-public-sha", "source-union", "native", "actor", "alias", "foreign",
    "constant-share", "prior-reuse", "prior-mutate", "cycle"))
def test_whole_raw_image_mutants(fault):
    root, case = "/tmp/ae-image-fault/LAB", "batch403404-GEMINI-2"
    retained = []
    prior = call_image(uut, root, case, retained)
    original, reached, old_state = uut.render_image, [], prior["state"]
    def faulty(label, case_id):
        wrong = original(label, case_id)
        reached.append(wrong)
        if fault == "missing":
            del wrong["sources"]
        elif fault == "extra":
            wrong["accepted"] = True
        elif fault == "bool":
            wrong["schema_version"] = True
        elif fault == "root":
            wrong["public_return"]["root"] = "/tmp/different/LAB"
        elif fault.startswith("registry"):
            spec = wrong["files"][REGISTRY]
            if fault == "registry-public-sha":
                wrong["public_return"]["provenance"]["artifact_sha256"][REGISTRY] = "0" * 64
            else:
                field = {"registry-mode": "mode", "registry-text": "text", "registry-sha": "sha256"}[fault]
                spec[field] = 0o600 if field == "mode" else "wrong"
        elif fault == "source-union":
            wrong["sources"]["tests/test_p1_00b_characterization_acceptance.py"]["content"] = "mixed null/string"
        elif fault == "native":
            wrong["files"][".local/pre.json"]["text"] += " "
        elif fault == "actor":
            wrong["public_return"]["entries"][0]["actor"]["controller_run_id"] = "wrong"
        elif fault == "alias":
            wrong["public_return"]["entries"][1]["native"] = wrong["public_return"]["entries"][0]["native"]
        elif fault == "constant-share":
            wrong["public_return"]["provenance"]["data_origins"] = uut.data.ORIGINS
        elif fault == "prior-reuse":
            return prior
        elif fault == "prior-mutate":
            prior["state"] = "BROKEN"
        elif fault == "cycle":
            wrong["cycle"] = wrong
        else:
            wrong["public_return"]["entries"] = h.ForeignList(wrong["public_return"]["entries"])
        return wrong
    uut.render_image = faulty
    try:
        with h.armed_hooks():
            with pytest.raises((AssertionError, ValueError, KeyError)):
                call_image(uut, root, case, retained)
            assert h.get_total_hook_calls() == 0
        assert len(reached) == 1 and len(retained) == 1
    finally:
        uut.render_image = original
        prior["state"] = old_state
    assert_retained(retained)
    call_image(uut, root, case, retained)


def test_strict_native_bounds_before_json_or_foreign_hooks():
    specimens = [h.ForeignDict(), h.ForeignList(), h.ForeignOpaqueLeaf(), {h.ForeignStrKey("x"): 1},
                 {"x": h.ForeignDuration()}, {"x": float("nan")}, {"x": float("inf")}, [None] * 2049]
    deep = None
    for _ in range(17):
        deep = [deep]
    specimens.append(deep)
    with h.armed_hooks():
        for specimen in specimens:
            with pytest.raises(AssertionError):
                native(specimen)
        assert h.get_total_hook_calls() == 0


def test_reached_validator_mutation_baseexception_raw_custody():
    global assert_image
    retained, reached = [], []
    root, case = "/tmp/ae-image-validator/LAB", CASES[0]
    call_image(uut, root, case, retained)
    original, error, before = assert_image, KeyboardInterrupt("validator"), SUBJECT_CALLS
    def faulty(image, label, case_id):
        original(image, label, case_id)
        reached.append((image, image["state"], h.capture_custody(image)))
        image["state"] = "BROKEN"
        raise error
    assert_image = faulty
    try:
        with pytest.raises(AssertionError, match="custody violation: raw") as caught:
            call_image(uut, root, case, retained)
        assert caught.value.__context__ is error
        assert len(reached) == 1 and len(retained) == 1 and SUBJECT_CALLS == before + 1
    finally:
        assert_image = original
        if reached:
            reached[0][0]["state"] = reached[0][1]
    h.assert_custody_unchanged(reached[0][0], reached[0][2])
    assert_retained(retained)
    call_image(uut, root, case, retained)

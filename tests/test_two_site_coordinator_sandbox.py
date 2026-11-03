"""Filesystem boundary tests; live Qoder/provider calls are never made here."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch

import pytest
import yaml

from scripts import two_site_assignment_scope as assignment_scope

from scripts import two_site_coordinator_sandbox as sandbox


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    common = root / ".git"
    common.mkdir()
    run = root / "run"
    run.mkdir()
    auth = tmp_path / "auth"
    auth.mkdir()
    for name in ("user", "machine_id"):
        (auth / name).write_text("SYNTHETIC_PUBLIC_FIXTURE")
    with patch.object(sandbox, "_AUTH_ROOT", auth), \
            patch.object(sandbox, "_commit_signing_required", return_value=False), \
            patch.object(sandbox, "_git_path", side_effect=lambda _root, flag:
                         root if flag == "--show-toplevel" else common):
        yield root, run, common


def bindings(command, flag):
    return [(command[i + 1], command[i + 2])
            for i, value in enumerate(command[:-2]) if value == flag]


def test_sparse_mounts_capabilities_and_public_system_files(workspace):
    root, run, common = workspace
    command = sandbox.buildwrap_coordinator_command(root, run, ["/usr/bin/true"], environment={})
    assert "--unshare-all" in command and "--share-net" in command
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert "--clearenv" in command and "--proc" in command and "--dev" in command
    assert bindings(command, "--bind") == [(str(root), str(root)),
                                           (str(run / "coordinator-qoder-state"), "/root/.qoder")]
    assert ("/usr", "/usr") in bindings(command, "--ro-bind")
    for source, target in bindings(command, "--ro-bind"):
        assert source not in {"/", "/root", "/srv", "/etc", "/root/.ssh", "/root/.aws"}
        assert target != "/etc/shadow" and target != "/var/run/docker.sock"
    assert not any("settings.json" in value or "mcp.json" in value for value in command)
    assert command[-2:] == ["--", str(Path("/usr/bin/true").resolve())]


def test_masks_project_live_environment_and_credential_directories(workspace):
    root, run, _ = workspace
    for name in (".env", ".env.local", "live.env"):
        (root / name).write_text("SYNTHETIC_PUBLIC_FIXTURE")
    for name in (".aws", ".ssh", "data", "uploads", "production-data"):
        (root / name).mkdir()
    command = sandbox.buildwrap_coordinator_command(root, run, ["/usr/bin/true"], environment={})
    for name in (".env", ".env.local", "live.env"):
        assert ("/dev/null", str(root / name)) in bindings(command, "--ro-bind")
    for name in (".aws", ".ssh", "data", "uploads", "production-data"):
        assert ["--tmpfs", str(root / name), "--remount-ro", str(root / name)] == command[
            command.index(str(root / name)) - 1:command.index(str(root / name)) + 3]


def test_environment_comes_from_controller_allowlist_only(workspace):
    root, run, _ = workspace
    environment = {
        "TWO_SITE_ASSIGNMENT_ID": "fixture-assignment", "TWO_SITE_AUTOPILOT_RUN_ID": "fixture-run",
        "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "AWS_SECRET_ACCESS_KEY": "SYNTHETIC_FORBIDDEN", "SSH_AUTH_SOCK": "/forbidden",
        "QODER_PERSONAL_ACCESS_TOKEN": "SYNTHETIC_FORBIDDEN", "TWO_SITE_API_TOKEN": "SYNTHETIC_FORBIDDEN",
        "LD_PRELOAD": "/forbidden", "PYTHONPATH": "/forbidden",
        "TWO_SITE_COORDINATOR_SANDBOX": "untrusted-value",
    }
    command = sandbox.buildwrap_coordinator_command(root, run, ["/usr/bin/true"], environment=environment)
    forwarded = dict(bindings(command, "--setenv"))
    assert forwarded["TWO_SITE_ASSIGNMENT_ID"] == "fixture-assignment"
    assert forwarded["GIT_AUTHOR_EMAIL"] == "fixture@example.invalid"
    assert forwarded["APP_ENV_FILE"] == "config/unit-test.env.example"
    assert forwarded["PYTHONPATH"] == str(root)
    assert forwarded["TWO_SITE_COORDINATOR_SANDBOX"] == "1"
    assert not set(forwarded) & {"AWS_SECRET_ACCESS_KEY", "SSH_AUTH_SOCK", "QODER_PERSONAL_ACCESS_TOKEN",
                                 "TWO_SITE_API_TOKEN", "LD_PRELOAD"}


def test_pinned_executable_is_read_only_even_inside_worktree(workspace):
    root, run, _ = workspace
    tool = root / "pinned-cli"
    tool.write_text("#!/bin/sh\nexit 0\n")
    tool.chmod(0o500)
    command = sandbox.buildwrap_coordinator_command(root, run, [str(tool)], environment={})
    assert (str(tool), str(tool)) in bindings(command, "--ro-bind")


def test_required_signing_policy_is_preserved_without_inherited_git_overrides(workspace):
    root, run, _ = workspace
    with patch.object(sandbox, "_commit_signing_required", return_value=True):
        command = sandbox.buildwrap_coordinator_command(
            root, run, ["/usr/bin/true"],
            environment={"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "commit.gpgsign",
                         "GIT_CONFIG_VALUE_0": "false"},
        )
    forwarded = dict(bindings(command, "--setenv"))
    assert forwarded["GIT_CONFIG_COUNT"] == "1"
    assert forwarded["GIT_CONFIG_KEY_0"] == "commit.gpgsign"
    assert forwarded["GIT_CONFIG_VALUE_0"] == "true"


@pytest.mark.parametrize("variant", ["outside-run", "symlink-root", "traversal-root", "common-escape",
                                      "cache-symlink", "protected-symlink", "relative-executable"])
def test_malformed_paths_and_root_escapes_are_rejected(workspace, tmp_path, variant):
    root, run, common = workspace
    argv = ["/usr/bin/true"]
    if variant == "outside-run":
        run = tmp_path
    elif variant == "symlink-root":
        linked = tmp_path / "linked"
        linked.symlink_to(root, target_is_directory=True)
        root = linked
    elif variant == "traversal-root":
        root = root / ".." / "repo"
    elif variant == "common-escape":
        other = tmp_path / "other" / ".git"
        other.mkdir(parents=True)
        with patch.object(sandbox, "_git_path", side_effect=[root, other]):
            with pytest.raises(sandbox.CoordinatorSandboxError):
                sandbox.buildwrap_coordinator_command(root, run, argv, environment={})
        return
    elif variant == "cache-symlink":
        (run / "coordinator-qoder-state").symlink_to(tmp_path, target_is_directory=True)
    elif variant == "protected-symlink":
        (root / ".env").symlink_to(tmp_path / "missing")
    else:
        argv = ["true"]
    with pytest.raises(sandbox.CoordinatorSandboxError):
        sandbox.buildwrap_coordinator_command(root, run, argv, environment={})


@pytest.mark.skipif(os.environ.get("RUN_REAL_COORDINATOR_SANDBOX_CANARY") != "1",
                    reason="Native namespace canary is explicitly run outside the tool sandbox")
def test_real_offline_sandbox_hides_host_root_and_allows_workspace_write(tmp_path):
    root = Path(sandbox.__file__).resolve().parents[1]
    active_scope = assignment_scope.load_active_assignment_scope(root)
    # Public synthetic sentinel only; no host credential bytes are accessed.
    with tempfile.TemporaryDirectory(prefix="coordinator-sandbox-forbidden-", dir="/root") as outside, \
            tempfile.TemporaryDirectory(prefix="coordinator-sandbox-canary-", dir=root / ".local") as run:
        sentinel = Path(outside) / "public-sentinel"
        sentinel.write_text("SYNTHETIC_PUBLIC_SENTINEL")
        auth = tmp_path / "auth"
        auth.mkdir()
        (auth / "user").write_text("SYNTHETIC_PUBLIC_AUTH_FIXTURE")
        (auth / "machine_id").write_text("SYNTHETIC_PUBLIC_MACHINE_FIXTURE")
        output = Path(run) / "workspace-write"
        script = '''import errno,json,os,pathlib,subprocess,sys
sentinel,output=sys.argv[1:]
assert not pathlib.Path(sentinel).exists()
for name in ['/srv','/root/.ssh','/root/secure-envs','/var/run/docker.sock','/etc/shadow']:
 assert not pathlib.Path(name).exists(),name
assert 'QODER_PERSONAL_ACCESS_TOKEN' not in os.environ
assert 'AWS_SECRET_ACCESS_KEY' not in os.environ
from scripts.two_site_assignment_scope import load_active_assignment_scope
scope=load_active_assignment_scope(pathlib.Path(os.environ['PYTHONPATH']))
assert scope.assignment_id==os.environ['TWO_SITE_ASSIGNMENT_ID']
assert scope.sha256==os.environ['TWO_SITE_ASSIGNMENT_SHA256']
assert scope.allowed_paths
root=pathlib.Path(os.environ['PYTHONPATH'])
common=pathlib.Path(subprocess.check_output(['/usr/bin/git','-C',str(root),'rev-parse','--path-format=absolute','--git-common-dir'],text=True).strip())
registry=common.parent/'.local/two-site-refactor/worktrees.yaml'
assignment=registry.parent/'assignments'/(scope.assignment_id+'.md')
assert not (common.parent/'AGENTS.md').exists()
assert list(assignment.parent.iterdir())==[assignment]
assert os.environ['APP_ENV_FILE']=='config/unit-test.env.example'
status=pathlib.Path('/proc/self/status').read_text()
assert 'CapEff:\\t0000000000000000' in status
assert 'NoNewPrivs:\\t1' in status
for filename in ['/usr/bin/true','/root/.qoder/.auth/user',str(registry),str(assignment)]:
 try:
  with open(filename,'ab'):pass
 except OSError as error:
  assert error.errno in (errno.EROFS,errno.EACCES,errno.EPERM)
 else:raise AssertionError('read-only mount was writable')
pathlib.Path(output).write_text('sandbox-write-ok')
print(json.dumps({'outside_hidden':True,'workspace_write':True,'readonly_mounts':True,'zero_capabilities':True,'no_new_privileges':True,'active_scope_loaded':True,'scope_controls_readonly':True}))
'''
        with patch.object(sandbox, "_AUTH_ROOT", auth):
            command = sandbox.buildwrap_coordinator_command(
                root, Path(run), ["/usr/bin/python3", "-B", "-c", script,
                                  str(sentinel), str(output)],
                environment={"TWO_SITE_ASSIGNMENT_ID": active_scope.assignment_id,
                             "TWO_SITE_ASSIGNMENT_SHA256": active_scope.sha256,
                             "QODER_PERSONAL_ACCESS_TOKEN": "SYNTHETIC_FORBIDDEN",
                             "AWS_SECRET_ACCESS_KEY": "SYNTHETIC_FORBIDDEN"},
            )
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=15, check=False)
        assert result.returncode == 0, result.stderr.decode(errors="replace")[:1000]
        assert json.loads(result.stdout)["outside_hidden"] is True
        assert output.read_text() == "sandbox-write-ok"


@pytest.fixture
def registered(tmp_path, monkeypatch):
    shared = tmp_path / "main"
    common = shared / ".git"
    common.mkdir(parents=True)
    controls = shared / ".local/two-site-refactor"
    root = controls / "integration"
    root.mkdir(parents=True)
    run = root / "run"
    run.mkdir()
    registry = controls / "worktrees.yaml"
    registry.write_text(yaml.safe_dump({"integration": {
        "path": str(root), "branch": "refactor/two-site-architecture",
        "active_assignment_id": "OFFLINE-ASSIGNMENT-001",
    }}))
    document = controls / "assignments/OFFLINE-ASSIGNMENT-001.md"
    document.parent.mkdir()
    document.write_text("# Public synthetic Assignment\n```yaml\n" + yaml.safe_dump({
        "id": "OFFLINE-ASSIGNMENT-001", "status": "READY", "role": "WORKER",
        "integration_branch": "refactor/two-site-architecture", "external_access": "NONE",
        "expires_at": "2099-01-01T00:00:00Z", "stage_id": "P1-00b",
        "allowed_paths": ["tests/test_new.py"], "forbidden_paths": [".env*"],
    }) + "```\n")
    order = root / "docs/refactor/two-server/execution-order.yaml"
    order.parent.mkdir(parents=True)
    order.write_text("stages:\n  P1-00b: {state: IN_PROGRESS}\n")
    auth = tmp_path / "auth"
    auth.mkdir()
    monkeypatch.setattr(sandbox, "_AUTH_ROOT", auth)
    monkeypatch.setattr(sandbox, "_commit_signing_required", lambda _root: False)
    monkeypatch.setattr(sandbox, "_git_path", lambda _root, flag:
                        root if flag == "--show-toplevel" else common)
    calls = []

    def public_git(args, cwd, **_kwargs):
        assert cwd == str(root)
        calls.append(tuple(args))
        if args == ["rev-parse", "--path-format=absolute", "--git-common-dir"]:
            return str(common)
        if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
            return "refactor/two-site-architecture"
        assert args == ["ls-files", "--error-unmatch", "docs/refactor/two-server/execution-order.yaml"]
        return "docs/refactor/two-server/execution-order.yaml"

    monkeypatch.setattr(assignment_scope, "_run_git_cmd", public_git)
    return root, run, common, registry, document, calls


def command(registered, environment=None):
    root, run, *_ = registered
    return sandbox.buildwrap_coordinator_command(root, run, ["/usr/bin/true"], environment=environment or {})


def test_only_two_actual_validated_scope_controls_are_readonly(registered):
    root, run, common, registry, document, calls = registered
    scope = assignment_scope.load_active_assignment_scope(root)
    argv = command(registered, {"TWO_SITE_ASSIGNMENT_ID": scope.assignment_id,
                                "TWO_SITE_ASSIGNMENT_SHA256": scope.sha256})
    assert (str(registry), str(registry)) in bindings(argv, "--ro-bind")
    assert (str(document), str(document)) in bindings(argv, "--ro-bind")
    assert bindings(argv, "--bind") == [
        (str(root), str(root)), (str(common), str(common)),
        (str(run / "coordinator-qoder-state"), "/root/.qoder"),
    ]
    exposed = {source for source, _target in bindings(argv, "--bind") + bindings(argv, "--ro-bind")}
    assert str(common.parent) not in exposed
    assert str(registry.parent) not in exposed
    assert str(document.parent) not in exposed
    assert not any(v in exposed for v in ("/srv", "/root/.ssh", "/root/secure-envs"))
    assert "--share-net" in argv and "--clearenv" in argv
    assert argv[argv.index("--cap-drop") + 1] == "ALL"
    assert dict(bindings(argv, "--setenv"))["TWO_SITE_COORDINATOR_SANDBOX"] == "1"
    assert len([v for v in calls if v == ("rev-parse", "--abbrev-ref", "HEAD")]) == 3


@pytest.mark.parametrize("which", ["registry", "assignment"])
@pytest.mark.parametrize("bad", ["symlink", "hardlink", "oversize", "writable", "wrong-owner"])
def test_unsafe_scope_control_rejected(registered, tmp_path, monkeypatch, which, bad):
    _root, _run, _common, registry, document, _calls = registered
    target = registry if which == "registry" else document
    if bad == "symlink":
        original = tmp_path / "original"
        target.replace(original)
        target.symlink_to(original)
    elif bad == "hardlink":
        os.link(target, tmp_path / "second-link")
    elif bad == "oversize":
        target.write_bytes(b"x" * (assignment_scope.MAX_CONTROL_FILE_BYTES + 1))
    elif bad == "writable":
        target.chmod(0o666)
    else:
        monkeypatch.setattr(sandbox.os, "geteuid", lambda: target.stat().st_uid + 1)
    with pytest.raises(sandbox.CoordinatorSandboxError):
        command(registered)


@pytest.mark.parametrize("bad", ["registry-root", "registry-branch", "traversal-id", "expired", "stage"])
def test_real_loader_rejects_invalid_registered_contract(registered, bad):
    _root, _run, _common, registry, document, _calls = registered
    if bad in {"registry-root", "registry-branch", "traversal-id"}:
        data = yaml.safe_load(registry.read_text())
        key, value = {"registry-root": ("path", "/tmp"),
                      "registry-branch": ("branch", "other"),
                      "traversal-id": ("active_assignment_id", "../escape")}[bad]
        data["integration"][key] = value
        registry.write_text(yaml.safe_dump(data))
    else:
        text = document.read_text()
        document.write_text(text.replace("2099-01-01", "2000-01-01") if bad == "expired"
                            else text.replace("P1-00b", "P9-99"))
    with pytest.raises(sandbox.CoordinatorSandboxError, match="SANDBOX_ASSIGNMENT_SCOPE"):
        command(registered)


@pytest.mark.parametrize("environment", [
    {"TWO_SITE_ASSIGNMENT_ID": "WRONG"},
    {"TWO_SITE_ASSIGNMENT_SHA256": "0" * 64},
    {"TWO_SITE_ASSIGNMENT_ID": "OFFLINE-ASSIGNMENT-001", "TWO_SITE_ASSIGNMENT_SHA256": "0" * 64},
])
def test_controller_scope_binding_remains_exact(registered, environment):
    with pytest.raises(sandbox.CoordinatorSandboxError, match="SANDBOX_ASSIGNMENT_SCOPE_BINDING"):
        command(registered, environment)


@pytest.mark.parametrize("which", ["registry", "assignment"])
def test_control_change_during_actual_loader_replay_is_rejected(registered, monkeypatch, which):
    _root, _run, _common, registry, document, _calls = registered
    actual = assignment_scope.load_expected_assignment_scope

    def changing(*args):
        result = actual(*args)
        target = registry if which == "registry" else document
        with target.open("a") as handle:
            handle.write("\n# changed during selection\n")
        return result

    monkeypatch.setattr(assignment_scope, "load_expected_assignment_scope", changing)
    with pytest.raises(sandbox.CoordinatorSandboxError, match="SANDBOX_ASSIGNMENT_CONTROL_CHANGED"):
        command(registered)

#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

try:
    from deploy_config import DEFAULTS, parse_env_file
except ModuleNotFoundError:  # pragma: no cover - used when imported as scripts.check_deployment_surface_guard
    from scripts.deploy_config import DEFAULTS, parse_env_file


RUNTIME_PATHS = (
    "main.py",
    "api",
    "core",
    "frontend/src",
)

OPERATOR_ENTRYPOINTS = (
    "Makefile",
    "deploy.sh",
    "scripts/recover_cross_server_sync.sh",
    "scripts/sample_sync_health.py",
    "scripts/setup_iran_nginx.sh",
)

IDENTITY_KEYS = (
    "FOREIGN_PUBLIC_IP",
    "FOREIGN_PUBLIC_DOMAIN",
    "FOREIGN_SERVER_URL",
    "FOREIGN_SERVER_DOMAIN",
    "IRAN_HOST",
    "IRAN_PUBLIC_IP",
    "IRAN_PUBLIC_DOMAIN",
    "IRAN_APP_DOMAIN",
    "IRAN_SERVER_URL",
    "IRAN_SERVER_DOMAIN",
    "FOREIGN_FRONTEND_URL",
    "IRAN_FRONTEND_URL",
    "IRAN_HEALTHCHECK_URL",
)

RUNTIME_IDENTITY_KEYS = (
    "SERVER_MODE",
    "FOREIGN_SERVER_URL",
    "FOREIGN_SERVER_DOMAIN",
    "IRAN_SERVER_URL",
    "IRAN_SERVER_DOMAIN",
    "FRONTEND_URL",
    "SYNC_VERIFY_TLS",
    "SYNC_CA_BUNDLE",
)

RETIRED_IDENTITIES = (
    "87.107.110.68",
    "kharej.362514.ir",
    "iran.362514.ir",
)

DEFAULT_DRIFT_KEYS = (
    "IRAN_HOST",
    "IRAN_SSH_USER",
    "IRAN_SSH_PORT",
    "IRAN_PROJECT_DIR",
)

IGNORED_PARTS = {
    "__pycache__",
    "node_modules",
    "dist",
}


@dataclass(frozen=True)
class Finding:
    path: str
    detail: str


def deployment_identities(manifest: dict[str, str]) -> set[str]:
    identities = {manifest[key] for key in IDENTITY_KEYS if manifest.get(key)}
    identities.update(RETIRED_IDENTITIES)
    return {value for value in identities if value}


def should_scan_runtime_path(path: Path) -> bool:
    parts = set(path.parts)
    if parts & IGNORED_PARTS:
        return False
    name = path.name
    if name.endswith((".pyc", ".map")):
        return False
    if ".test." in name or name.startswith("test_"):
        return False
    if "tests" in parts:
        return False
    return path.suffix in {".py", ".ts", ".tsx", ".vue", ".js", ".mjs", ".cjs"}


def iter_runtime_files(repo_root: Path):
    for raw_path in RUNTIME_PATHS:
        path = repo_root / raw_path
        if path.is_file():
            if should_scan_runtime_path(path):
                yield path
            continue
        if path.is_dir():
            for child in path.rglob("*"):
                if child.is_file() and should_scan_runtime_path(child):
                    yield child


def scan_file_for_values(path: Path, values: set[str], repo_root: Path) -> list[Finding]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    rel = str(path.relative_to(repo_root))
    findings: list[Finding] = []
    for value in sorted(values, key=len, reverse=True):
        if value and value in text:
            findings.append(Finding(rel, f"contains deployment identity {value!r}"))
    return findings


def check_runtime_code(repo_root: Path, values: set[str]) -> list[Finding]:
    findings: list[Finding] = []
    for path in iter_runtime_files(repo_root):
        findings.extend(scan_file_for_values(path, values, repo_root))
    return findings


def check_operator_entrypoints(repo_root: Path, values: set[str]) -> list[Finding]:
    findings: list[Finding] = []
    for raw_path in OPERATOR_ENTRYPOINTS:
        path = repo_root / raw_path
        if not path.exists():
            findings.append(Finding(raw_path, "operator entrypoint is missing"))
            continue
        findings.extend(scan_file_for_values(path, values, repo_root))
    return findings


def check_default_drift(manifest: dict[str, str]) -> list[Finding]:
    findings: list[Finding] = []
    for key in DEFAULT_DRIFT_KEYS:
        expected = manifest.get(key)
        actual = DEFAULTS.get(key)
        if expected and actual != expected:
            findings.append(
                Finding(
                    "scripts/deploy_config.py",
                    f"DEFAULTS[{key!r}]={actual!r} does not match deploy/production/online.env.example {expected!r}",
                )
            )
    return findings


def _host_from_url(value: str) -> str:
    parsed = urlparse(value)
    return parsed.hostname or ""


def _expected_runtime_identity(manifest: dict[str, str], role: str) -> dict[str, str]:
    common = {
        "FOREIGN_SERVER_URL": manifest.get("FOREIGN_SERVER_URL", ""),
        "FOREIGN_SERVER_DOMAIN": manifest.get("FOREIGN_SERVER_DOMAIN", ""),
        "IRAN_SERVER_URL": manifest.get("IRAN_SERVER_URL", ""),
        "IRAN_SERVER_DOMAIN": manifest.get("IRAN_SERVER_DOMAIN", ""),
    }
    if role == "foreign":
        return {
            "SERVER_MODE": "foreign",
            "FRONTEND_URL": manifest.get("FOREIGN_FRONTEND_URL") or manifest.get("FOREIGN_SERVER_URL", ""),
            **common,
        }
    if role == "iran":
        return {
            "SERVER_MODE": "iran",
            "FRONTEND_URL": manifest.get("IRAN_FRONTEND_URL") or manifest.get("IRAN_SERVER_URL", ""),
            **common,
        }
    raise ValueError(f"unsupported runtime role: {role}")


def _identity_runtime_values(values: dict[str, str]) -> dict[str, str]:
    return {key: values[key] for key in RUNTIME_IDENTITY_KEYS if values.get(key)}


def _is_project_env_source(env_path: Path, repo_root: Path) -> bool:
    try:
        resolved = env_path.resolve()
        project_env = (repo_root / ".env").resolve()
        project_iran_env = (repo_root / ".env.iran").resolve()
    except OSError:
        return False
    return resolved in {project_env, project_iran_env}


def check_runtime_env_identity(
    *,
    manifest: dict[str, str],
    role: str,
    env_path: Path,
    repo_root: Path,
    allow_project_env_source: bool = False,
) -> list[Finding]:
    findings: list[Finding] = []
    display_path = str(env_path)
    if role not in {"foreign", "iran"}:
        return [Finding(display_path, f"unsupported runtime role {role!r}")]
    if not allow_project_env_source and _is_project_env_source(env_path, repo_root):
        findings.append(
            Finding(
                display_path,
                "production runtime env source must not be project-root .env or .env.iran",
            )
        )
    if not env_path.exists():
        findings.append(Finding(display_path, "runtime env file is missing"))
        return findings

    values = parse_env_file(env_path)
    expected = _expected_runtime_identity(manifest, role)
    runtime_values = _identity_runtime_values(values)

    for key, expected_value in expected.items():
        actual = values.get(key)
        if not actual:
            findings.append(Finding(display_path, f"missing required runtime identity field {key}"))
            continue
        if expected_value and actual != expected_value:
            findings.append(
                Finding(
                    display_path,
                    f"{role} runtime identity field {key} does not match production manifest",
                )
            )

    for key, actual in runtime_values.items():
        for retired in RETIRED_IDENTITIES:
            if retired and retired in actual:
                findings.append(
                    Finding(
                        display_path,
                        f"runtime identity field {key} contains retired deployment identity {retired!r}",
                    )
                )

    for url_key, domain_key in (
        ("FOREIGN_SERVER_URL", "FOREIGN_SERVER_DOMAIN"),
        ("IRAN_SERVER_URL", "IRAN_SERVER_DOMAIN"),
    ):
        url_value = values.get(url_key, "")
        domain_value = values.get(domain_key, "")
        if url_value and domain_value and _host_from_url(url_value) != domain_value:
            findings.append(
                Finding(
                    display_path,
                    f"{url_key} host does not match {domain_key}",
                )
            )

    frontend_url = values.get("FRONTEND_URL", "")
    expected_frontend_url = expected.get("FRONTEND_URL", "")
    if frontend_url and expected_frontend_url and _host_from_url(frontend_url) != _host_from_url(expected_frontend_url):
        findings.append(Finding(display_path, f"{role} FRONTEND_URL host does not match production manifest"))

    sync_verify_tls = values.get("SYNC_VERIFY_TLS", "true").strip().lower()
    sync_ca_bundle = values.get("SYNC_CA_BUNDLE", "").strip()
    if sync_verify_tls in {"0", "false", "no", "off"} and not sync_ca_bundle:
        findings.append(Finding(display_path, "SYNC_VERIFY_TLS=false is forbidden without SYNC_CA_BUNDLE"))

    return findings


def _parse_runtime_env_arg(raw_value: str) -> tuple[str, Path]:
    if "=" not in raw_value:
        raise ValueError("runtime env argument must use ROLE=PATH")
    role, path = raw_value.split("=", 1)
    role = role.strip().lower()
    if not role or not path:
        raise ValueError("runtime env argument must use ROLE=PATH")
    return role, Path(path)


def run_guard(
    repo_root: Path,
    *,
    manifest_path: Path | None = None,
    runtime_envs: tuple[tuple[str, Path], ...] = (),
    allow_project_env_source: bool = False,
) -> list[Finding]:
    manifest_path = manifest_path or repo_root / "deploy/production/online.env.example"
    manifest = parse_env_file(manifest_path)
    values = deployment_identities(manifest)
    findings: list[Finding] = []
    findings.extend(check_runtime_code(repo_root, values))
    findings.extend(check_operator_entrypoints(repo_root, values))
    findings.extend(check_default_drift(manifest))
    for role, env_path in runtime_envs:
        findings.extend(
            check_runtime_env_identity(
                manifest=manifest,
                role=role,
                env_path=env_path,
                repo_root=repo_root,
                allow_project_env_source=allow_project_env_source,
            )
        )
    return findings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prevent production deployment identity hardcodes from leaking back in.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--manifest-path", default=None)
    parser.add_argument(
        "--runtime-env",
        action="append",
        default=[],
        help="Validate a rendered production runtime env file using ROLE=PATH. Repeat for foreign and iran.",
    )
    parser.add_argument(
        "--allow-project-env-source",
        action="store_true",
        help="Emergency-only: allow project-root .env/.env.iran as source while still enforcing identity validation.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    try:
        runtime_envs = tuple(_parse_runtime_env_arg(raw_value) for raw_value in args.runtime_env)
    except ValueError as exc:
        print(f"invalid --runtime-env: {exc}")
        return 2
    manifest_path = Path(args.manifest_path).resolve() if args.manifest_path else None
    findings = run_guard(
        repo_root,
        manifest_path=manifest_path,
        runtime_envs=runtime_envs,
        allow_project_env_source=bool(args.allow_project_env_source),
    )
    if findings:
        for finding in findings:
            print(f"{finding.path}: {finding.detail}")
        return 1
    print("deployment surface guard passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

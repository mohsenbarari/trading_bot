#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

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


def run_guard(repo_root: Path) -> list[Finding]:
    manifest_path = repo_root / "deploy/production/online.env.example"
    manifest = parse_env_file(manifest_path)
    values = deployment_identities(manifest)
    findings: list[Finding] = []
    findings.extend(check_runtime_code(repo_root, values))
    findings.extend(check_operator_entrypoints(repo_root, values))
    findings.extend(check_default_drift(manifest))
    return findings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prevent production deployment identity hardcodes from leaking back in.")
    parser.add_argument("--repo-root", default=".")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    findings = run_guard(repo_root)
    if findings:
        for finding in findings:
            print(f"{finding.path}: {finding.detail}")
        return 1
    print("deployment surface guard passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

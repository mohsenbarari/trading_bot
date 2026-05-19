from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]

MANUAL_NON_REGRESSION_TOOLS = (
    'tests/api_load_test.py',
    'tests/customer_live_auth_smoke.py',
    'tests/load_test.py',
    'tests/live_simulation.py',
    'tests/debug_trade.py',
)

BREADTH_THRESHOLDS = {
    'python_unittest_files': 200,
    'frontend_unit_files': 10,
    'frontend_e2e_files': 7,
    'manual_non_regression_tools': len(MANUAL_NON_REGRESSION_TOOLS),
}

PRODUCT_PREFIXES = (
    'api/',
    'bot/',
    'core/',
    'models/',
    'frontend/src/',
    'scripts/',
    'alembic/',
    'migrations/',
)

PRODUCT_FILES = {
    'main.py',
    'run_bot.py',
    'manage.py',
    'schemas.py',
    'deploy.sh',
    'Makefile',
    'Dockerfile',
    'Dockerfile.iran',
    'docker-compose.yml',
    'docker-compose.iran.yml',
    'nginx.conf',
}


@dataclass(frozen=True)
class TestMatrixSummary:
    python_unittest_files: int
    frontend_unit_files: int
    frontend_e2e_files: int
    manual_non_regression_tools: int


@dataclass(frozen=True)
class DiffGateResult:
    passed: bool
    message: str
    product_changes: list[str]
    test_changes: list[str]


def count_files(root: Path, pattern: str) -> int:
    return sum(1 for _ in root.glob(pattern))


def build_summary(repo_root: Path = REPO_ROOT) -> TestMatrixSummary:
    return TestMatrixSummary(
        python_unittest_files=count_files(repo_root, 'tests/test_*.py'),
        frontend_unit_files=count_files(repo_root, 'frontend/src/**/*.test.ts'),
        frontend_e2e_files=count_files(repo_root, 'frontend/e2e/*.spec.ts'),
        manual_non_regression_tools=sum(
            1 for rel_path in MANUAL_NON_REGRESSION_TOOLS if (repo_root / rel_path).exists()
        ),
    )


def evaluate_breadth(summary: TestMatrixSummary) -> list[str]:
    failures: list[str] = []
    summary_dict = asdict(summary)
    for key, minimum in BREADTH_THRESHOLDS.items():
        actual = summary_dict[key]
        if actual < minimum:
            failures.append(f'{key}={actual} is below minimum {minimum}')
    return failures


def is_test_path(rel_path: str) -> bool:
    normalized = rel_path.replace('\\', '/')
    if normalized.startswith('tests/test_') and normalized.endswith('.py'):
        return True
    if normalized.startswith('frontend/src/') and normalized.endswith('.test.ts'):
        return True
    if normalized.startswith('frontend/e2e/') and normalized.endswith('.spec.ts'):
        return True
    return False


def is_product_path(rel_path: str) -> bool:
    normalized = rel_path.replace('\\', '/')
    if is_test_path(normalized):
        return False
    if normalized in PRODUCT_FILES:
        return True
    return normalized.startswith(PRODUCT_PREFIXES)


def evaluate_diff_gate(changed_paths: Iterable[str]) -> DiffGateResult:
    normalized_paths = [path.replace('\\', '/') for path in changed_paths if path.strip()]
    product_changes = sorted(path for path in normalized_paths if is_product_path(path))
    test_changes = sorted(path for path in normalized_paths if is_test_path(path))

    if not product_changes:
        return DiffGateResult(
            passed=True,
            message='No product-surface changes detected in the diff.',
            product_changes=[],
            test_changes=test_changes,
        )

    if test_changes:
        return DiffGateResult(
            passed=True,
            message='Product changes are accompanied by automated test changes.',
            product_changes=product_changes,
            test_changes=test_changes,
        )

    return DiffGateResult(
        passed=False,
        message='Product-surface changes require at least one automated test change in the same diff.',
        product_changes=product_changes,
        test_changes=[],
    )


def get_changed_paths(
    base_ref: str = 'HEAD~1',
    head_ref: str = 'HEAD',
    repo_root: Path = REPO_ROOT,
) -> list[str]:
    result = subprocess.run(
        ['git', 'diff', '--name-only', f'{base_ref}..{head_ref}'],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or 'git diff failed')
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def build_report_payload(
    summary: TestMatrixSummary,
    breadth_failures: list[str],
    diff_gate_result: DiffGateResult | None,
    base_ref: str,
    head_ref: str,
) -> dict[str, object]:
    payload: dict[str, object] = {
        'summary': asdict(summary),
        'thresholds': BREADTH_THRESHOLDS,
        'breadth_failures': breadth_failures,
    }
    if diff_gate_result is not None:
        payload['diff_gate'] = {
            'base_ref': base_ref,
            'head_ref': head_ref,
            'passed': diff_gate_result.passed,
            'message': diff_gate_result.message,
            'product_changes': diff_gate_result.product_changes,
            'test_changes': diff_gate_result.test_changes,
        }
    return payload


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Report and gate repository-wide automated test breadth.')
    parser.add_argument('--json', action='store_true', help='Emit the report as JSON.')
    parser.add_argument('--check-breadth', action='store_true', help='Fail when breadth thresholds fall below the current baseline.')
    parser.add_argument('--check-diff', action='store_true', help='Fail when product-surface changes land without test changes in the same diff.')
    parser.add_argument('--base-ref', default='HEAD~1', help='Base git ref for --check-diff (default: HEAD~1).')
    parser.add_argument('--head-ref', default='HEAD', help='Head git ref for --check-diff (default: HEAD).')
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    summary = build_summary()
    breadth_failures = evaluate_breadth(summary) if args.check_breadth else []
    diff_gate_result: DiffGateResult | None = None

    if args.check_diff:
        try:
            changed_paths = get_changed_paths(args.base_ref, args.head_ref)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        diff_gate_result = evaluate_diff_gate(changed_paths)

    payload = build_report_payload(
        summary,
        breadth_failures,
        diff_gate_result,
        args.base_ref,
        args.head_ref,
    )

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print('Repository Test Matrix')
        print(f"- Python unittest modules: {summary.python_unittest_files}")
        print(f"- Frontend Vitest files: {summary.frontend_unit_files}")
        print(f"- Frontend Playwright specs: {summary.frontend_e2e_files}")
        print(f"- Manual non-regression tools: {summary.manual_non_regression_tools}")

        if args.check_breadth:
            if breadth_failures:
                print('- Breadth gate: FAILED')
                for failure in breadth_failures:
                    print(f'  - {failure}')
            else:
                print('- Breadth gate: PASSED')

        if diff_gate_result is not None:
            status = 'PASSED' if diff_gate_result.passed else 'FAILED'
            print(f'- Diff gate ({args.base_ref}..{args.head_ref}): {status}')
            print(f'  - {diff_gate_result.message}')
            if diff_gate_result.product_changes:
                print(f'  - Product changes: {", ".join(diff_gate_result.product_changes)}')
            if diff_gate_result.test_changes:
                print(f'  - Test changes: {", ".join(diff_gate_result.test_changes)}')

    exit_code = 0
    if args.check_breadth and breadth_failures:
        exit_code = 1
    if diff_gate_result is not None and not diff_gate_result.passed:
        exit_code = 1
    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
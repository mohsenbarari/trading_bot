from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT_FILES = {'main.py', 'manage.py', 'run_bot.py', 'schemas.py'}

BACKEND_SECTIONS = (
    ('Backend · api', ('api/',)),
    ('Backend · bot', ('bot/',)),
    ('Backend · core', ('core/',)),
    ('Backend · models', ('models/',)),
    ('Backend · root files', tuple(BACKEND_ROOT_FILES)),
)

FRONTEND_SECTIONS = (
    ('Frontend · components', ('frontend/src/components/',)),
    ('Frontend · composables', ('frontend/src/composables/',)),
    ('Frontend · services', ('frontend/src/services/',)),
    ('Frontend · views', ('frontend/src/views/',)),
    ('Frontend · stores', ('frontend/src/stores/',)),
    ('Frontend · router', ('frontend/src/router/',)),
    ('Frontend · utils', ('frontend/src/utils/',)),
    ('Frontend · types', ('frontend/src/types/',)),
)


@dataclass
class CoverageBucket:
    covered: int = 0
    total: int = 0

    def add(self, covered: int, total: int) -> None:
        self.covered += covered
        self.total += total

    @property
    def pct(self) -> float:
        if self.total <= 0:
            return 0.0
        return round((self.covered / self.total) * 100, 1)

    @property
    def exact_pct(self) -> float:
        if self.total <= 0:
            return 0.0
        return (self.covered / self.total) * 100

    def as_dict(self) -> dict[str, float | int]:
        return {
            'covered': self.covered,
            'total': self.total,
            'pct': self.pct,
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Build a GitHub-friendly repository coverage summary.')
    parser.add_argument('--backend-json', default='tmp/backend-coverage.json')
    parser.add_argument('--frontend-summary', default='frontend/coverage/coverage-summary.json')
    parser.add_argument('--markdown-out', default='tmp/repo-coverage-summary.md')
    parser.add_argument('--json-out', default='tmp/repo-coverage-summary.json')
    parser.add_argument('--min-weighted-pct', type=float, default=None)
    parser.add_argument('--min-backend-pct', type=float, default=None)
    parser.add_argument('--min-frontend-pct', type=float, default=None)
    parser.add_argument('--weighted-threshold-exclusive', action='store_true')
    parser.add_argument('--backend-threshold-exclusive', action='store_true')
    parser.add_argument('--frontend-threshold-exclusive', action='store_true')
    return parser.parse_args()


def load_json(path_str: str) -> tuple[dict[str, object] | None, Path]:
    path = REPO_ROOT / path_str
    if not path.exists():
        return None, path
    return json.loads(path.read_text(encoding='utf-8')), path


def normalize_path(raw_path: str) -> str:
    normalized = raw_path.replace('\\', '/').strip()
    repo_root = str(REPO_ROOT).replace('\\', '/')
    if normalized.startswith(repo_root + '/'):
        return normalized[len(repo_root) + 1:]

    for prefix in ('/frontend/src/', '/api/', '/bot/', '/core/', '/models/'):
        marker_index = normalized.find(prefix)
        if marker_index != -1:
            return normalized[marker_index + 1:]

    for root_file in BACKEND_ROOT_FILES:
        if normalized.endswith('/' + root_file):
            return root_file

    return normalized.lstrip('./')


def match_section(path: str, definitions: tuple[tuple[str, tuple[str, ...]], ...], fallback: str) -> str:
    for label, prefixes in definitions:
        for prefix in prefixes:
            if path == prefix or path.startswith(prefix):
                return label
    return fallback


def summarize_backend(payload: dict[str, object] | None, notes: list[str]) -> tuple[CoverageBucket, dict[str, CoverageBucket]]:
    sections = {label: CoverageBucket() for label, _ in BACKEND_SECTIONS}
    sections['Backend · other'] = CoverageBucket()
    overall = CoverageBucket()

    if payload is None:
        notes.append('Backend coverage JSON was not found.')
        return overall, sections

    files = payload.get('files', {})
    if not isinstance(files, dict):
        notes.append('Backend coverage JSON does not contain a valid files map.')
        return overall, sections

    for raw_path, file_payload in files.items():
        if not isinstance(raw_path, str) or not isinstance(file_payload, dict):
            continue
        summary = file_payload.get('summary', {})
        if not isinstance(summary, dict):
            continue
        covered = int(summary.get('covered_lines', 0) or 0)
        total = int(summary.get('num_statements', 0) or 0)
        if total <= 0:
            continue
        normalized_path = normalize_path(raw_path)
        section = match_section(normalized_path, BACKEND_SECTIONS, 'Backend · other')
        sections[section].add(covered, total)
        overall.add(covered, total)

    return overall, sections


def summarize_frontend(payload: dict[str, object] | None, notes: list[str]) -> tuple[CoverageBucket, dict[str, CoverageBucket]]:
    sections = {label: CoverageBucket() for label, _ in FRONTEND_SECTIONS}
    sections['Frontend · other'] = CoverageBucket()
    overall = CoverageBucket()

    if payload is None:
        notes.append('Frontend coverage summary JSON was not found.')
        return overall, sections

    total_entry = payload.get('total', {})
    if isinstance(total_entry, dict):
        total_lines = total_entry.get('lines', {})
        if isinstance(total_lines, dict):
            overall.add(int(total_lines.get('covered', 0) or 0), int(total_lines.get('total', 0) or 0))

    for raw_path, file_payload in payload.items():
        if raw_path == 'total' or not isinstance(raw_path, str) or not isinstance(file_payload, dict):
            continue
        normalized_path = normalize_path(raw_path)
        if not normalized_path.startswith('frontend/src/'):
            continue
        lines = file_payload.get('lines', {})
        if not isinstance(lines, dict):
            continue
        covered = int(lines.get('covered', 0) or 0)
        total = int(lines.get('total', 0) or 0)
        if total <= 0:
            continue
        section = match_section(normalized_path, FRONTEND_SECTIONS, 'Frontend · other')
        sections[section].add(covered, total)

    if overall.total == 0:
        overall = CoverageBucket(
            covered=sum(bucket.covered for bucket in sections.values()),
            total=sum(bucket.total for bucket in sections.values()),
        )

    return overall, sections


def render_table(title: str, sections: dict[str, CoverageBucket]) -> list[str]:
    lines = [f'## {title}', '', '| Section | Covered | Total | Coverage |', '| --- | ---: | ---: | ---: |']
    for label, bucket in sections.items():
        if bucket.total <= 0:
            continue
        lines.append(f'| {label} | {bucket.covered} | {bucket.total} | {bucket.pct:.1f}% |')
    if len(lines) == 4:
        lines.append('| No data | 0 | 0 | 0.0% |')
    lines.append('')
    return lines


def evaluate_threshold(
    label: str,
    bucket: CoverageBucket,
    threshold: float | None,
    *,
    exclusive: bool,
    notes: list[str],
) -> bool:
    if threshold is None:
        return False

    comparator = '>' if exclusive else '>='
    passes = bucket.exact_pct > threshold if exclusive else bucket.exact_pct >= threshold
    verdict = 'passed' if passes else 'failed'
    notes.append(
        f'{label} threshold {verdict}: {bucket.exact_pct:.6f}% {comparator} {threshold:.6f}%.'
    )
    return not passes


def main() -> int:
    args = parse_args()
    notes: list[str] = []

    backend_payload, backend_path = load_json(args.backend_json)
    frontend_payload, frontend_path = load_json(args.frontend_summary)

    backend_overall, backend_sections = summarize_backend(backend_payload, notes)
    frontend_overall, frontend_sections = summarize_frontend(frontend_payload, notes)
    repo_overall = CoverageBucket(
        covered=backend_overall.covered + frontend_overall.covered,
        total=backend_overall.total + frontend_overall.total,
    )

    threshold_failures = [
        evaluate_threshold(
            'Repository weighted coverage',
            repo_overall,
            args.min_weighted_pct,
            exclusive=args.weighted_threshold_exclusive,
            notes=notes,
        ),
        evaluate_threshold(
            'Backend coverage',
            backend_overall,
            args.min_backend_pct,
            exclusive=args.backend_threshold_exclusive,
            notes=notes,
        ),
        evaluate_threshold(
            'Frontend coverage',
            frontend_overall,
            args.min_frontend_pct,
            exclusive=args.frontend_threshold_exclusive,
            notes=notes,
        ),
    ]

    markdown_lines = [
        '# Repository Coverage Summary',
        '',
        '> Weighted repository coverage combines backend Python statement coverage with frontend unit-test line coverage.',
        '',
        '## Overall',
        '',
        '| Scope | Covered | Total | Coverage |',
        '| --- | ---: | ---: | ---: |',
        f'| Repository · weighted total | {repo_overall.covered} | {repo_overall.total} | {repo_overall.pct:.1f}% |',
        f'| Backend Python | {backend_overall.covered} | {backend_overall.total} | {backend_overall.pct:.1f}% |',
        f'| Frontend unit | {frontend_overall.covered} | {frontend_overall.total} | {frontend_overall.pct:.1f}% |',
        '',
    ]
    markdown_lines.extend(render_table('Backend Coverage By Section', backend_sections))
    markdown_lines.extend(render_table('Frontend Coverage By Section', frontend_sections))
    markdown_lines.extend([
        '## Source Files',
        '',
        f'- Backend JSON: `{backend_path.relative_to(REPO_ROOT)}`',
        f'- Frontend summary JSON: `{frontend_path.relative_to(REPO_ROOT)}`',
        '',
    ])
    if notes:
        markdown_lines.extend(['## Notes', ''])
        markdown_lines.extend([f'- {note}' for note in notes])
        markdown_lines.append('')

    payload = {
        'overall': {
            'repository_weighted': repo_overall.as_dict(),
            'backend': backend_overall.as_dict(),
            'frontend_unit': frontend_overall.as_dict(),
        },
        'backend_sections': {label: bucket.as_dict() for label, bucket in backend_sections.items()},
        'frontend_sections': {label: bucket.as_dict() for label, bucket in frontend_sections.items()},
        'notes': notes,
        'inputs': {
            'backend_json': str(backend_path.relative_to(REPO_ROOT)),
            'frontend_summary_json': str(frontend_path.relative_to(REPO_ROOT)),
            'thresholds': {
                'min_weighted_pct': args.min_weighted_pct,
                'min_backend_pct': args.min_backend_pct,
                'min_frontend_pct': args.min_frontend_pct,
                'weighted_threshold_exclusive': args.weighted_threshold_exclusive,
                'backend_threshold_exclusive': args.backend_threshold_exclusive,
                'frontend_threshold_exclusive': args.frontend_threshold_exclusive,
            },
        },
    }

    markdown_path = REPO_ROOT / args.markdown_out
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text('\n'.join(markdown_lines), encoding='utf-8')

    json_path = REPO_ROOT / args.json_out
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')

    print('\n'.join(markdown_lines))
    return 1 if any(threshold_failures) else 0


if __name__ == '__main__':
    raise SystemExit(main())
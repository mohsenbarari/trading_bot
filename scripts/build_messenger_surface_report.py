from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Build a human-readable Messenger surface report from the manifest.')
    parser.add_argument('--config', default='scripts/messenger_benchmark_config.json')
    parser.add_argument('--manifest', default=None)
    parser.add_argument('--output', default=None)
    return parser.parse_args()


def resolve_path(raw_path: str) -> Path:
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate
    return (REPO_ROOT / candidate).resolve()


def load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding='utf-8'))


def render_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    output = [f"| {' | '.join(headers)} |", f"| {' | '.join(['---'] * len(headers))} |"]
    for row in rows:
        output.append(f"| {' | '.join(row)} |")
    return output


def main() -> int:
    args = parse_args()
    config = load_json(resolve_path(args.config))

    manifest_path = resolve_path(args.manifest or str(config['manifest_path']))
    output_path = resolve_path(args.output or str(config['surface_report_path']))
    manifest = load_json(manifest_path)

    comparison_modes = manifest.get('comparison_modes', [])
    test_layers = manifest.get('test_layers', [])
    scenario_packs = manifest.get('scenario_packs', [])
    surfaces = manifest.get('surfaces', [])

    lines: list[str] = [
        '# Messenger Surface Report',
        '',
        f'- Generated at: {datetime.now(timezone.utc).isoformat()}',
        f'- Manifest: {manifest_path.relative_to(REPO_ROOT)}',
        f"- Surface count: {manifest.get('surface_count', len(surfaces))}",
        f"- Manifest status: {manifest.get('status', 'unknown')}",
        '',
        '## Comparison Modes',
        '',
    ]

    mode_rows = []
    for mode in comparison_modes:
        mode_rows.append([
            str(mode.get('id', '')),
            str(mode.get('label', '')),
            ' vs '.join(mode.get('compare', [])),
            str(mode.get('purpose', '')),
        ])
    lines.extend(render_table(['ID', 'Label', 'Compare', 'Purpose'], mode_rows))
    lines.extend(['', '## Test Layers', ''])

    layer_rows = []
    for layer in test_layers:
        layer_rows.append([
            str(layer.get('id', '')),
            str(layer.get('label', '')),
            ', '.join(layer.get('outputs', [])),
        ])
    lines.extend(render_table(['Layer', 'Label', 'Outputs'], layer_rows))
    lines.extend(['', '## Scenario Packs', ''])

    scenario_rows = []
    for scenario in scenario_packs:
        scenario_rows.append([
            str(scenario.get('id', '')),
            str(scenario.get('label', '')),
            ', '.join(scenario.get('primary_surfaces', [])),
            str(scenario.get('seed_profile', '')),
        ])
    lines.extend(render_table(['Scenario', 'Label', 'Primary Surfaces', 'Seed Profile'], scenario_rows))
    lines.extend(['', '## Surface Index', ''])

    surface_rows = []
    for surface in surfaces:
        surface_rows.append([
            str(surface.get('id', '')),
            str(surface.get('area', '')),
            str(surface.get('release_severity', '')),
            ', '.join(surface.get('primary_scenarios', [])),
            ', '.join(surface.get('test_layers', [])),
            str(len(surface.get('existing_evidence', []))),
            str(len(surface.get('required_new_work', []))),
        ])
    lines.extend(
        render_table(
            ['ID', 'Area', 'Severity', 'Primary Scenarios', 'Layers', 'Evidence', 'New Work'],
            surface_rows,
        )
    )

    for surface in surfaces:
        lines.extend(
            [
                '',
                f"## {surface.get('id', '')} — {surface.get('area', '')}",
                '',
                f"- Release owner: {surface.get('release_owner', '')}",
                f"- Integration owners: {', '.join(surface.get('integration_owners', []))}",
                f"- Comparison modes: {', '.join(surface.get('comparison_modes', []))}",
                f"- Severity: {surface.get('release_severity', '')}",
                f"- Primary scenarios: {', '.join(surface.get('primary_scenarios', []))}",
                f"- Test layers: {', '.join(surface.get('test_layers', []))}",
                '',
                'Must include:',
            ]
        )
        for item in surface.get('must_include', []):
            lines.append(f'- {item}')
        lines.extend(['', 'Existing evidence:'])
        for item in surface.get('existing_evidence', []):
            lines.append(f'- {item}')
        lines.extend(['', 'Required new work:'])
        for item in surface.get('required_new_work', []):
            lines.append(f'- {item}')
        lines.extend(['', 'Key metrics:'])
        for item in surface.get('key_metrics', []):
            lines.append(f'- {item}')

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f'Wrote {output_path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
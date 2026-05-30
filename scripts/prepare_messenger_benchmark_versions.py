from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Prepare reproducible Messenger benchmark versions.')
    parser.add_argument('--config', default='scripts/messenger_benchmark_config.json')
    parser.add_argument('--skip-current-build', action='store_true')
    parser.add_argument('--skip-pre-build', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    return parser.parse_args()


def load_config(path_str: str) -> dict[str, object]:
    path = (REPO_ROOT / path_str).resolve()
    return json.loads(path.read_text(encoding='utf-8'))


def resolve_path(raw_path: str) -> Path:
    candidate = Path(raw_path)
    if candidate.is_absolute():
      return candidate
    return (REPO_ROOT / candidate).resolve()


def run(command: list[str], *, cwd: Path | None = None, dry_run: bool = False) -> None:
    printable_cwd = str(cwd or REPO_ROOT)
    printable_cmd = ' '.join(command)
    print(f'[{printable_cwd}]$ {printable_cmd}')
    if dry_run:
        return
    subprocess.run(command, cwd=str(cwd or REPO_ROOT), check=True)


def ensure_worktree(worktree_path: Path, commit: str, *, dry_run: bool) -> None:
    if not worktree_path.exists():
        run(['git', 'worktree', 'add', '--detach', str(worktree_path), commit], dry_run=dry_run)
        return

    git_dir = worktree_path / '.git'
    if not git_dir.exists():
        raise RuntimeError(f'Benchmark worktree path exists but is not a git worktree: {worktree_path}')

    current_commit = subprocess.run(
        ['git', '-C', str(worktree_path), 'rev-parse', 'HEAD'],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if current_commit != commit:
        run(['git', '-C', str(worktree_path), 'checkout', '--detach', commit], dry_run=dry_run)


def ensure_node_modules_link(worktree_path: Path, *, dry_run: bool) -> None:
    source = REPO_ROOT / 'frontend' / 'node_modules'
    target = worktree_path / 'frontend' / 'node_modules'

    if target.is_symlink() and target.resolve() == source.resolve():
        return
    if target.exists() and not target.is_symlink():
        return

    if dry_run:
        print(f'Would link {target} -> {source}')
        return

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        target.unlink()
    os.symlink(source, target, target_is_directory=True)


def ensure_dist_exists(root: Path, *, dry_run: bool) -> None:
    dist_dir = root / 'mini_app_dist'
    if dist_dir.exists() or dry_run:
        return
    print(f'Warning: {dist_dir} does not exist yet; build step should create it.')


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    performance = config['performance']
    if not isinstance(performance, dict):
        raise RuntimeError('Benchmark config is missing the performance section.')

    worktree = performance['worktree']
    if not isinstance(worktree, dict):
        raise RuntimeError('Benchmark config is missing worktree settings.')

    worktree_path = resolve_path(str(worktree['path']))
    commit = str(worktree['commit'])

    ensure_worktree(worktree_path, commit, dry_run=args.dry_run)
    ensure_node_modules_link(worktree_path, dry_run=args.dry_run)

    if not args.skip_pre_build:
        run(['npm', 'run', 'build'], cwd=worktree_path / 'frontend', dry_run=args.dry_run)
    if not args.skip_current_build:
        run(['npm', 'run', 'build'], cwd=REPO_ROOT / 'frontend', dry_run=args.dry_run)

    ensure_dist_exists(worktree_path, dry_run=args.dry_run)
    ensure_dist_exists(REPO_ROOT, dry_run=args.dry_run)

    print('Messenger benchmark version preparation complete.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
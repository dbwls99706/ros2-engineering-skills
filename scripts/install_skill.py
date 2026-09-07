#!/usr/bin/env python3
"""Copy a knowledge-only skill into a client discovery directory.

Does not install Claude plugin hooks, grant permissions, or edit client settings.
For the full Claude plugin use the marketplace instructions in README.md.
"""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

NAME = 'ros2-engineering-skills'
CLIENT_DIRS = {'portable': '.agents', 'codex': '.agents', 'claude': '.claude',
               'cursor': '.cursor', 'gemini': '.gemini'}
BUNDLE = ('SKILL.md', 'LICENSE', 'README.md', 'requirements.txt',
          'references', 'scripts', 'agents', 'docs', 'evals', 'examples',
          'CONTRIBUTING.md', 'SECURITY.md', 'ROADMAP.md', 'CHANGELOG.md')


def destination(client, project=None, home=None):
    base = Path(project) if project is not None else Path(home or Path.home())
    return base / CLIENT_DIRS[client] / 'skills' / NAME


def install(source, target, force=False, dry_run=False):
    source = Path(source).resolve()
    target = Path(target).expanduser().absolute()
    resolved = target.resolve()
    if (target.name != NAME or resolved == source
            or resolved.is_relative_to(source) or source.is_relative_to(resolved)):
        raise ValueError('Target must be a separate skill directory named ' + NAME)
    if target.is_symlink():
        raise ValueError('Refusing to replace a symlink; remove it explicitly first')
    if target.exists():
        if not force:
            raise ValueError('Target exists; use --force to replace a skill installation')
        if not target.is_dir() or not (target / 'SKILL.md').is_file():
            raise ValueError('Refusing to replace a target that is not a skill installation')
    for relative in BUNDLE:
        entry = source / relative
        if not entry.exists():
            raise ValueError('Incomplete source bundle: ' + relative)
        candidates = [entry, *entry.rglob('*')] if entry.is_dir() else [entry]
        if any(item.is_symlink() for item in candidates):
            raise ValueError('Copy installation requires a bundle without nested symlinks')
    if dry_run:
        return {'status': 'dry-run', 'target': str(target), 'layout': 'knowledge-only',
                'files': list(BUNDLE)}
    target.parent.mkdir(parents=True, exist_ok=True)
    # Validate in a sibling staging directory before touching an existing install.
    # Backup rename allows restoration if the final rename fails.
    with tempfile.TemporaryDirectory(prefix='.skill-install-', dir=target.parent) as tmp:
        staging = Path(tmp) / NAME
        staging.mkdir()
        for relative in BUNDLE:
            entry, dest = source / relative, staging / relative
            if entry.is_dir():
                shutil.copytree(entry, dest, ignore=shutil.ignore_patterns(
                    '__pycache__', '*.pyc', '.pytest_cache', '.coverage', '.DS_Store'))
            else:
                shutil.copy2(entry, dest)
        validator = source / 'scripts/validate_skill.py'
        result = subprocess.run(
            [sys.executable, str(validator), '--root', str(staging), '--installed', '--portable'],
            text=True, capture_output=True, timeout=20,
        )
        if result.returncode != 0:
            raise ValueError('Staged skill failed validation: ' + result.stdout + result.stderr)
        report = json.loads(result.stdout)
        if not isinstance(report, dict) or report.get('status') != 'pass':
            raise ValueError('Staged skill failed validation: unrecognized report')
        backup = Path(tmp) / 'previous'
        if target.exists():
            target.rename(backup)
        try:
            staging.rename(target)
        except OSError:
            if backup.exists():
                backup.rename(target)
            raise
    return {'status': 'installed', 'target': str(target), 'layout': 'knowledge-only',
            'hooks_installed': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client', choices=tuple(CLIENT_DIRS), default='portable')
    parser.add_argument('--project', type=Path, help='project root; omitted means user scope')
    parser.add_argument('--target', type=Path, help='explicit final skill directory')
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    if args.target and args.project:
        parser.error('--target and --project are mutually exclusive')
    target = args.target or destination(args.client, args.project)
    try:
        result = install(Path(__file__).resolve().parents[1], target, args.force, args.dry_run)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({'status': 'error', 'error': str(exc)}))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())

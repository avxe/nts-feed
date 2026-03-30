"""Validate or recover ``shows.json`` from its backup copy."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


def _load_json(path: Path):
    with path.open('r', encoding='utf-8') as handle:
        return json.load(handle)


def recover_shows(
    shows_path: str = 'shows.json',
    backup_path: str = 'shows.json.backup',
    *,
    force_backup: bool = False,
    dry_run: bool = False,
):
    shows_file = Path(shows_path)
    backup_file = Path(backup_path)

    if not force_backup and shows_file.exists():
        try:
            data = _load_json(shows_file)
            return {
                'success': True,
                'action': 'validated',
                'shows_path': str(shows_file),
                'count': len(data) if isinstance(data, dict) else None,
            }
        except Exception as exc:
            validation_error = str(exc)
        else:
            validation_error = None
    else:
        validation_error = 'forced backup restore'

    if not backup_file.exists():
        return {
            'success': False,
            'action': 'unrecoverable',
            'shows_path': str(shows_file),
            'backup_path': str(backup_file),
            'message': validation_error or 'Backup file not found',
        }

    try:
        backup_data = _load_json(backup_file)
    except Exception as exc:
        return {
            'success': False,
            'action': 'unrecoverable',
            'shows_path': str(shows_file),
            'backup_path': str(backup_file),
            'message': f'Backup is invalid: {exc}',
        }

    if not dry_run:
        shutil.copy2(backup_file, shows_file)

    return {
        'success': True,
        'action': 'restored',
        'shows_path': str(shows_file),
        'backup_path': str(backup_file),
        'count': len(backup_data) if isinstance(backup_data, dict) else None,
        'dry_run': dry_run,
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description='Validate or restore shows.json from shows.json.backup.')
    parser.add_argument('--shows-path', default='shows.json', help='Path to the primary shows.json file.')
    parser.add_argument('--backup-path', default='shows.json.backup', help='Path to the backup file.')
    parser.add_argument('--force-backup', action='store_true', help='Restore from backup even if shows.json parses.')
    parser.add_argument('--dry-run', action='store_true', help='Report the recovery plan without copying files.')
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    result = recover_shows(
        shows_path=args.shows_path,
        backup_path=args.backup_path,
        force_backup=args.force_backup,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get('success') else 1


if __name__ == '__main__':
    raise SystemExit(main())

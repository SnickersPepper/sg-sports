#!/usr/bin/env python3
"""Offline backup tooling for the settings store: create, verify, restore (with an automatic rollback bundle).

  python3 scripts/ops_backup.py create  DATA_DIR OUT.json
  python3 scripts/ops_backup.py verify  BACKUP.json
  python3 scripts/ops_backup.py restore DATA_DIR BACKUP.json ROLLBACK_OUT.json [SECRETS.json]

Bundles never contain secret values. SECRETS.json maps "section/id/field" to the value and is supplied only at restore time.
Run against a stopped backend or a copy of the data directory; the tool makes no network calls.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ops
import settings


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    command = argv[1]
    if command == 'verify':
        problems = ops.verify_backup(json.loads(Path(argv[2]).read_text()))
        print(json.dumps({'ok': not problems, 'problems': problems}, indent=2))
        return 1 if problems else 0
    if len(argv) < 4:
        print(__doc__)
        return 2
    store = settings.SettingsStore(Path(argv[2]) / 'settings.sqlite')
    if command == 'create':
        Path(argv[3]).write_text(json.dumps(ops.make_backup(store), indent=2, sort_keys=True))
        os.chmod(argv[3], 0o600)
        print('backup written; revision', store.revision())
        return 0
    if command == 'restore' and len(argv) >= 5:
        supplied = json.loads(Path(argv[5]).read_text()) if len(argv) > 5 else {}
        try:
            result = ops.restore_backup(store, json.loads(Path(argv[3]).read_text()), supplied)
        except (ops.BackupError, settings.SettingsError) as error:
            print(json.dumps({'ok': False, 'problems': getattr(error, 'problems', getattr(error, 'errors', []))}, indent=2))
            return 1
        Path(argv[4]).write_text(json.dumps(result['rollback'], indent=2, sort_keys=True))
        os.chmod(argv[4], 0o600)
        print('restored; revision', result['revision'], '; rollback bundle written')
        return 0
    print(__doc__)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv))

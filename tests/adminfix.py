"""Test helper: sign in as a ready administrator (password already changed) against a data directory."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import adminauth

PASSWORD = 'test-password-1'
_CACHE = {}


def admin_headers(data_dir):
    """Cookie + CSRF headers of a live administrator session for data_dir. Cached per directory."""
    key = str(data_dir)
    if key not in _CACHE or not (Path(key) / adminauth.AUTH_FILE).exists():
        adminauth.reset(Path(data_dir), PASSWORD)
        outcome, issued = adminauth.login(Path(data_dir), Path(data_dir), 'admin', PASSWORD, None)
        assert outcome == 'ok', outcome
        _CACHE[key] = {'Cookie': adminauth.COOKIE + '=' + issued['sid'], 'X-SG-CSRF': issued['csrf']}
    return dict(_CACHE[key])

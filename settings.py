"""Versioned, persistent SG Sports settings (Phase 7A M1).

One SQLite file holds a single configuration document, its monotonically increasing revision and a separate
write-only secret table. Every update is an atomic, revision-checked transaction: validation failure or a stale
revision leaves the last-good document untouched. Secrets (and credential-bearing URLs) are stored only in the
`secrets` table and are never returned by read, client or export views; the document holds opaque references.
"""
import copy
import hashlib
import json
import os
import re
import secrets as _secrets
import sqlite3
import threading
import time
import urllib.parse

import catalog
import dispatcharr

SCHEMA_VERSION = 4
ARTWORK_MODES = ('espn', 'url', 'pack', 'off')   # keep in step with artwork.MODES (settings must not import artwork)
SNAPSHOT_LIMIT = 20
ID_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{2,63}$')
SECRET_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')
URLISH = re.compile(r'[a-z][a-z0-9+.-]*://|[^\s/@]+:[^\s/@]+@', re.I)

# section -> id prefix, allowed fields (non-secret), secret fields (write-only, stored as store:<id> refs)
SECTIONS = {
    'profiles': {'prefix': 'prof', 'fields': {'name': str, 'preferences': dict}, 'secrets': ()},
    'accounts': {'prefix': 'acct', 'fields': {'name': str, 'max_connections': int, 'enabled': bool}, 'secrets': ('credential',)},
    'sources': {'prefix': 'src', 'fields': {'name': str, 'kind': str, 'account_id': (str, type(None)), 'enabled': bool, 'location_type': (str, type(None)),
                                            'format': (str, type(None)), 'merged': bool, 'account_ids': list, 'middleware': (str, type(None)),
                                            'integration_id': (str, type(None))}, 'secrets': ('location',)},
    'guide_sources': {'prefix': 'guide', 'fields': {'name': str, 'kind': str, 'enabled': bool, 'priority': int, 'location_type': (str, type(None)),
                                                   'format': (str, type(None)), 'middleware': (str, type(None)), 'integration_id': (str, type(None))}, 'secrets': ('location',)},
    'guide_mappings': {'prefix': 'gmap', 'fields': {'guide_source_id': str, 'source_id': (str, type(None)), 'source_channel': str, 'guide_channel': str}, 'secrets': ()},
    'routes': {'prefix': 'route', 'fields': {'name': str, 'kind': str, 'account_ids': list, 'enabled': bool, 'integration_id': (str, type(None)),
                                            'source_ids': list}, 'secrets': ()},
    'devices': {'prefix': 'dev', 'fields': {'name': str, 'role': str, 'profile_id': (str, type(None))}, 'secrets': ()},
    'integrations': {'prefix': 'int', 'fields': {'name': str, 'kind': str, 'enabled': bool, 'options': dict, 'auth_variant': (str, type(None))},
                     'secrets': ('credential', 'base_url', 'api_key')},
    'channel_mappings': {'prefix': 'cmap', 'fields': {'canonical_channel_id': str, 'source_id': str, 'integration_id': str, 'dispatcharr_channel': str,
                                                     'dispatcharr_stream': (str, type(None)), 'account_id': (str, type(None))}, 'secrets': ()},
}
# Server-stamped on create/change (never writable): a per-item generation, creation and last-change time.
STAMPS = ('generation', 'created_at', 'updated_at')
STAMPED = ('sources', 'guide_sources', 'integrations', 'routes', 'channel_mappings')
# A change to any of these (or to a secret) invalidates the item's stored health/assessment observation.
OBSERVATION_FIELDS = ('auth_variant', 'location_type', 'format', 'integration_id', 'middleware', 'kind')
MIDDLEWARES = (None, 'dispatcharr')
CHANNEL_ID_RE = re.compile(r'^channel\.[a-z0-9][a-z0-9._-]{0,80}$')
UPSTREAM_ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9-]{0,63}$')
SWITCH_LIMIT = 20
SOURCE_KINDS = ('m3u', 'xtream', 'iptvboss_export', 'external')
GUIDE_KINDS = ('xmltv',)
LOCATION_TYPES = ('url', 'file')  # a file location is a bare name inside the operator-mounted import directory, never a path
SOURCE_FORMATS = ('m3u', 'm3u8')
GUIDE_FORMATS = ('xml', 'xml.gz')
FILE_EXTENSIONS = {'sources': ('.m3u', '.m3u8'), 'guide_sources': ('.xml', '.xml.gz')}
FILE_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')
EXPORT_KEYS = ('schema_version', 'revision', 'secrets_required', 'exported_at', 'capability_snapshots')
RESTORE_OP_LIMIT = 5000
ROUTE_KINDS = ('direct', 'external', 'dispatcharr')
DEVICE_ROLES = ('admin', 'tv')
PREFERENCE_KEYS = {'language': str, 'time_zone': str, 'spoilers': bool, 'favorites': list, 'leagues': list, 'playback': dict, 'recording': dict}
# Preferences a paired TV may read and write for its own profile (time_zone stays administrator-only).
TV_PREFERENCE_KEYS = ('language', 'spoilers', 'favorites', 'leagues', 'playback', 'recording')
CANONICAL_ID_RE = re.compile(r'^[a-z0-9][a-z0-9._:-]{0,95}$')
LIST_PREFERENCE_LIMIT = 200
LANGUAGE_RE = re.compile(r'^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$')
PLAYBACK_QUALITIES = ('auto', 'high', 'low')
# nested preference -> {key: validator(value) -> bool}
NESTED_PREFERENCES = {
    'playback': {'start_from_beginning': lambda v: isinstance(v, bool), 'audio_language': lambda v: isinstance(v, str) and bool(LANGUAGE_RE.match(v)),
                 'quality': lambda v: v in PLAYBACK_QUALITIES},
    'recording': {'enabled': lambda v: isinstance(v, bool), 'favorites_only': lambda v: isinstance(v, bool),
                  'keep_latest': lambda v: isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 50},
}
# Fields that may carry their own reference (not a secret value) so migrated file secrets keep working.
REF_FIELDS = ('credential_ref', 'location_ref', 'base_url_ref', 'api_key_ref')
# Server-derived markers (never writable through the API: they are not in any section's field list).
PROVENANCE_LEGACY = 'legacy_import'
PROVENANCE_PAIRING = 'pairing'
DEVICE_KEY_RE = re.compile(r'^[0-9a-f]{64}$')


class SettingsError(Exception):
    def __init__(self, errors):
        super().__init__('invalid_settings')
        self.errors = errors


class Conflict(Exception):
    def __init__(self, revision):
        super().__init__('revision_conflict')
        self.revision = revision


class SchemaTooNew(Exception):
    pass


class Forbidden(Exception):
    """The caller is not an active, profile-assigned paired TV."""
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def empty_document():
    return {section: [] for section in SECTIONS}


def _migration_1(db, ctx):
    """Create the first schema and import the legacy provider/pairing state when supplied."""
    db.execute('CREATE TABLE config (id INTEGER PRIMARY KEY CHECK (id=1), revision INTEGER NOT NULL, doc TEXT NOT NULL, updated_at INTEGER NOT NULL)')
    db.execute('CREATE TABLE snapshots (revision INTEGER PRIMARY KEY, doc TEXT NOT NULL, created_at INTEGER NOT NULL)')
    db.execute('CREATE TABLE secrets (id TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at INTEGER NOT NULL)')
    doc = empty_document()
    doc['profiles'].append({'id': 'prof-household', 'name': 'Household', 'preferences': {}})
    # Transitional, read-only import of the pre-settings provider files. A clean install passes no providers.
    used = set()
    seen_legacy = set()
    for provider in ctx.get('providers', ()):
        legacy_id = provider.get('id') if isinstance(provider, dict) else None
        if not isinstance(legacy_id, str) or not legacy_id or legacy_id in seen_legacy:
            continue  # fail closed: an unusable or duplicate legacy key is not imported
        seen_legacy.add(legacy_id)
        slug = re.sub(r'[^a-z0-9_-]', '-', legacy_id.lower()).strip('-_')[:40]
        # Deterministic disambiguation: a lossy or colliding mapping gets a stable hash of the original key.
        if not slug or slug != legacy_id or slug in used:
            slug = (slug[:30] + '-' if slug else 'p-') + hashlib.sha256(legacy_id.encode()).hexdigest()[:8]
        used.add(slug)
        try:
            budget = min(100, max(1, int(provider.get('max_connections', 1))))
        except (TypeError, ValueError):
            budget = 1
        name = provider['name'] if isinstance(provider.get('name'), str) and provider['name'].strip() else slug
        account = {'id': 'acct-' + slug, 'name': name, 'max_connections': budget, 'enabled': True, 'provenance': PROVENANCE_LEGACY, 'read_only': True}
        if re.match(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,58}$', legacy_id):
            account['credential_ref'] = 'file:' + legacy_id + '.json'
        doc['accounts'].append(account)
        doc['sources'].append({'id': 'src-' + slug, 'name': name, 'kind': provider.get('type') if provider.get('type') in SOURCE_KINDS else 'external',
                               'account_id': 'acct-' + slug, 'enabled': True, 'provenance': PROVENANCE_LEGACY, 'read_only': True})
    for key in sorted((ctx.get('paired_devices') or {})):
        _add_device(doc, key)
    db.execute('INSERT INTO config(id,revision,doc,updated_at) VALUES(1,1,?,?)', (json.dumps(doc, sort_keys=True), int(time.time())))


def _add_device(doc, key):
    """Registry entry for one paired TV, keyed by a prefix of the existing token digest (never the token)."""
    if not isinstance(key, str) or not DEVICE_KEY_RE.match(key):
        return
    dev_id = 'dev-' + key[:16]
    if any(i['id'] == dev_id for items in doc.values() for i in items):
        return
    household = any(p['id'] == 'prof-household' for p in doc['profiles'])
    doc['devices'].append({'id': dev_id, 'name': 'Paired TV', 'role': 'tv', 'profile_id': 'prof-household' if household else None,
                           'provenance': PROVENANCE_PAIRING, 'state': 'active', 'expires': 0})


def _sync_devices(doc, paired, now):
    """Reconcile derived device state with the pairing store (tv-devices), which stays the authority."""
    paired = {k: (v if isinstance(v, dict) else {}) for k, v in (paired or {}).items() if isinstance(k, str) and DEVICE_KEY_RE.match(k)}
    for key in sorted(paired):
        _add_device(doc, key)
    by_id = {'dev-' + k[:16]: v for k, v in paired.items()}
    for item in doc['devices']:
        if item.get('provenance') != PROVENANCE_PAIRING:
            continue
        item.pop('revoked', None)
        entry = by_id.get(item['id'])
        if entry is None:
            item['state'], item['expires'] = 'unpaired', 0
        else:
            expires = entry.get('expires', 0)
            expires = expires if isinstance(expires, int) and not isinstance(expires, bool) else 0
            item['expires'] = expires
            item['state'] = 'active' if expires > now else 'expired'


def _migration_2(db, ctx):
    """Explicitly translate label-derived favorite/league IDs saved by earlier builds to the canonical catalog IDs.

    Only the catalog's explicit legacy table is used (never the display name or list position). Unknown IDs and
    ambiguous legacy IDs are preserved untouched. The pre-migration document is stored as a snapshot of the previous
    revision, so the change is recoverable through the normal snapshot history.
    """
    row = db.execute('SELECT revision, doc FROM config WHERE id=1').fetchone()
    revision, doc = row[0], json.loads(row[1])
    before = copy.deepcopy(doc)
    for profile in doc.get('profiles', []):
        prefs = profile.get('preferences')
        if not isinstance(prefs, dict):
            continue
        for key, kind in (('favorites', 'team'), ('leagues', 'league')):
            if isinstance(prefs.get(key), list) and all(isinstance(v, str) for v in prefs[key]):
                prefs[key] = catalog.migrate_ids(kind, prefs[key])
    if doc != before:
        now = int(time.time())
        db.execute('INSERT OR REPLACE INTO snapshots(revision,doc,created_at) VALUES(?,?,?)', (revision, json.dumps(before, sort_keys=True), now))
        db.execute('UPDATE config SET revision=?, doc=?, updated_at=? WHERE id=1', (revision + 1, json.dumps(doc, sort_keys=True), now))


def _migration_3(db, ctx):
    """Optional-middleware schema: mapping section in the document plus server-side observation and switch tables.

    Observations (health/assessment snapshots) live outside the revisioned document so a periodic probe cannot bump
    the revision or push recoverable user edits out of the bounded snapshot history.
    """
    row = db.execute('SELECT doc FROM config WHERE id=1').fetchone()
    doc = json.loads(row[0])
    for section in SECTIONS:
        doc.setdefault(section, [])
    db.execute('UPDATE config SET doc=? WHERE id=1', (json.dumps(doc, sort_keys=True),))
    db.execute('CREATE TABLE observations (subject TEXT NOT NULL, subject_id TEXT NOT NULL, subject_generation INTEGER NOT NULL, observed_at INTEGER NOT NULL, '
               'body TEXT NOT NULL, generation INTEGER NOT NULL, PRIMARY KEY (subject, subject_id))')
    db.execute('CREATE TABLE switches (id TEXT PRIMARY KEY, state TEXT NOT NULL, created_revision INTEGER NOT NULL, plan TEXT NOT NULL, rollback TEXT, '
               'created_at INTEGER NOT NULL, committed_at INTEGER)')


def _migration_4(db, ctx):
    """Team logos default to Off. An installation that already existed keeps the ESPN retrieval it had: that is recorded as a
    meta default (no document revision change) used only while no artwork setting has been saved. A fresh install records nothing."""
    if not ctx.get('fresh'):
        db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('artwork_default','espn')")


MIGRATIONS = {1: _migration_1, 2: _migration_2, 3: _migration_3, 4: _migration_4}


class SettingsStore:
    def __init__(self, path, providers=(), paired_devices=None, migrations=None):
        self.path = str(path)
        self.migrations = migrations or MIGRATIONS
        self.lock = threading.RLock()
        os.makedirs(os.path.dirname(self.path) or '.', exist_ok=True)
        os.close(os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600))
        with self.lock:
            self._migrate({'providers': list(providers), 'paired_devices': paired_devices or {}})

    def _open(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA foreign_keys=ON')
        return db

    def _migrate(self, ctx):
        db = self._open()
        try:
            db.execute('BEGIN IMMEDIATE')
            try:
                db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
                row = db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
                current = int(row[0]) if row else 0
                ctx['fresh'] = current == 0 and not ctx.get('providers') and not ctx.get('paired_devices')
                target = max(self.migrations)
                if current > target:
                    raise SchemaTooNew('settings database schema %d is newer than supported %d' % (current, target))
                for version in sorted(v for v in self.migrations if v > current):
                    self.migrations[version](db, ctx)
                    db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version',?)", (str(version),))
                db.execute('COMMIT')
            except BaseException:
                db.execute('ROLLBACK')
                raise
        finally:
            db.close()

    # --- reads --------------------------------------------------------------------------------
    def _load(self, db):
        row = db.execute('SELECT revision, doc FROM config WHERE id=1').fetchone()
        return row[0], json.loads(row[1])

    def revision(self):
        """Current document revision (one cheap query; used to cache derived views)."""
        db = self._open()
        try:
            return db.execute('SELECT revision FROM config WHERE id=1').fetchone()[0]
        finally:
            db.close()

    def schema_version(self):
        db = self._open()
        try:
            return int(db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0])
        finally:
            db.close()

    def read(self):
        """Redacted administrator view: {schema_version, revision, ...sections}."""
        with self.lock:
            db = self._open()
            try:
                revision, doc = self._load(db)
                configured = {r[0] for r in db.execute('SELECT id FROM secrets')}
                try:
                    observations = db.execute('SELECT subject, subject_id, subject_generation, observed_at, body, generation FROM observations ORDER BY subject, subject_id').fetchall()
                except sqlite3.OperationalError:
                    observations = []  # a database held at an older schema (migration-test fixtures) has no observation table yet
            finally:
                db.close()
        view = {'schema_version': self.schema_version(), 'revision': revision}
        for section, items in doc.items():
            view[section] = [_redact(section, item, configured) for item in items]
        view['capability_snapshots'] = [dict(json.loads(r[4]), id='cap-' + r[1], subject=r[0], subject_id=r[1], subject_generation=r[2], observed_at=r[3], generation=r[5])
                                        for r in observations]
        return view

    def client_view(self):
        """What a paired TV/browser client may read: profiles and preferences only. No accounts, sources or devices."""
        full = self.read()
        return {'schema_version': full['schema_version'], 'revision': full['revision'], 'profiles': full['profiles']}

    def export(self):
        """Portable export: references and placeholders, never secret values."""
        full = self.read()
        full.pop('capability_snapshots', None)  # runtime observations, not configuration: re-observed after restore
        needed = []
        for section, spec in SECTIONS.items():
            for item in full[section]:
                for field in spec['secrets']:
                    if item.get(field + '_configured'):
                        needed.append({'section': section, 'id': item['id'], 'field': field})
        full['secrets_required'] = needed
        full['exported_at'] = int(time.time())
        return full

    def budgets(self):
        """Per-account connection budget. Every source mapped to one account shares that one allowance.

        A source is resolved only by an explicit account_id (or, for a merged source, a complete account_ids
        list). Names and layouts are never used. Unresolved sources get no allowance of their own: they are
        listed with allowance 0 until an operator maps them.
        """
        view = self.read()
        accounts = {a['id']: {'account_id': a['id'], 'name': a['name'], 'max_connections': a['max_connections'], 'source_ids': []} for a in view['accounts']}
        unresolved = []
        for source in view['sources']:
            mapped = _source_accounts(source)
            if mapped and all(a in accounts for a in mapped) and not (source.get('merged') and not source.get('account_ids')):
                for account_id in mapped:
                    accounts[account_id]['source_ids'].append(source['id'])
            else:
                unresolved.append(source['id'])
        return {'revision': view['revision'], 'accounts': list(accounts.values()), 'unresolved_source_ids': unresolved,
                'unresolved_allowance': 0, 'unresolved_policy': 'no_independent_connection_allowance'}

    def setup_status(self):
        """Separate setup states per source. Only 'configured' and 'guide_mapped' derive from configuration; catalog
        import, transport test and game confirmation are not run by this scope and stay 'not_started'."""
        view = self.read()
        unresolved = set(self.budgets()['unresolved_source_ids'])
        mapped = {m.get('source_id') for m in view['guide_mappings']}
        assessed = {o['subject_id']: o for o in view['capability_snapshots'] if o['subject'] == 'source'}
        rows = []
        for source in view['sources']:
            has_location = source.get('location_configured') or source['kind'] in ('xtream', 'external')
            resolved = source['id'] not in unresolved
            seen = assessed.get(source['id'])
            row = {'source_id': source['id'], 'name': source['name'], 'kind': source['kind'], 'budget': 'resolved' if resolved else 'unresolved', 'states': {
                'configured': 'done' if has_location and resolved else ('unresolved' if not resolved else 'not_started'),
                'catalog_imported': 'done' if seen and seen.get('catalog') == 'imported' else 'not_started',
                'guide_mapped': 'done' if source['id'] in mapped else 'not_started',
                'transport_tested': 'not_started', 'game_confirmed': 'not_started'}}
            if source.get('middleware'):
                # Playlist metadata only: never a claim about who holds upstream capacity (that needs D3 evidence).
                row['delivery'] = {'middleware': source['middleware'], 'profile': (seen or {}).get('delivery_profile', 'unassessed'), 'budget_assurance': 'not_assured',
                                   'reasons': (seen or {}).get('assurance_reasons', ['not_assessed']), 'real_gate': dispatcharr.REAL_GATE,
                                   'transport_health_is_game_verification': False, 'direct_fallback': False}
            rows.append(row)
        guides = [{'guide_source_id': g['id'], 'name': g['name'], 'configured': bool(g.get('location_configured'))} for g in view['guide_sources']]
        return {'revision': view['revision'], 'sources': rows, 'guide_sources': guides, 'unresolved_source_ids': sorted(unresolved)}

    # --- writes -------------------------------------------------------------------------------
    def sync_devices(self, paired, now=None):
        """Bring the device registry in line with the pairing store. Idempotent; bumps the revision only on change.

        Pairing (tv-devices) stays the authority for validity. Revocation today is the TV's own /tv/unpair or
        expiry; both appear here as state 'unpaired'/'expired'. An administrator revoke operation is M2.
        """
        now = int(time.time()) if now is None else now
        with self.lock:
            db = self._open()
            try:
                db.execute('BEGIN IMMEDIATE')
                try:
                    revision, doc = self._load(db)
                    before = copy.deepcopy(doc)
                    _sync_devices(doc, paired, now)
                    if doc != before:
                        stamp = int(time.time())
                        db.execute('INSERT INTO snapshots(revision,doc,created_at) VALUES(?,?,?)', (revision, json.dumps(before, sort_keys=True), stamp))
                        db.execute('DELETE FROM snapshots WHERE revision <= ?', (revision - SNAPSHOT_LIMIT,))
                        db.execute('UPDATE config SET revision=?, doc=?, updated_at=? WHERE id=1', (revision + 1, json.dumps(doc, sort_keys=True), stamp))
                    db.execute('COMMIT')
                except BaseException:
                    db.execute('ROLLBACK')
                    raise
            finally:
                db.close()

    def update(self, expected_revision, ops):
        return self._update(expected_revision, ops, 200)

    # --- paired-TV scope ------------------------------------------------------------------------
    def _tv_profile(self, doc, device_id):
        """The profile a device may act on; fail closed when the device is unknown, not a TV, not active or unassigned."""
        device = next((d for d in doc['devices'] if d['id'] == device_id), None)
        if device is None or device.get('role') != 'tv':
            raise Forbidden('unknown_device')
        if device.get('provenance') == PROVENANCE_PAIRING and device.get('state') != 'active':
            raise Forbidden('device_' + str(device.get('state')))
        profile = next((p for p in doc['profiles'] if p['id'] == device.get('profile_id')), None)
        if profile is None:
            raise Forbidden('no_profile_assigned')
        return profile

    def tv_settings(self, device_id):
        """The paired TV's whole permitted view: its own profile's allowed preferences and the shared revision."""
        with self.lock:
            db = self._open()
            try:
                revision, doc = self._load(db)
            finally:
                db.close()
        return _tv_view(revision, self._tv_profile(doc, device_id))

    def update_tv_settings(self, device_id, expected_revision, changes):
        """Replace the named allowed preferences of the device's own profile at the expected revision.

        Anything outside TV_PREFERENCE_KEYS is rejected before any write. The revision is the shared document
        revision, so a browser edit and a TV edit made from the same revision conflict explicitly.
        """
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool):
            raise SettingsError([{'path': 'revision', 'error': 'integer_required'}])
        if not isinstance(changes, dict) or not changes:
            raise SettingsError([{'path': 'preferences', 'error': 'non_empty_object_required'}])
        errors = [{'path': 'preferences.' + str(k)[:40], 'error': 'not_permitted_for_tv'} for k in changes if k not in TV_PREFERENCE_KEYS]
        if errors:
            raise SettingsError(errors)
        with self.lock:
            db = self._open()
            try:
                revision, doc = self._load(db)
            finally:
                db.close()
            profile = self._tv_profile(doc, device_id)
            if revision != expected_revision:
                raise Conflict(revision)
            merged = copy.deepcopy(profile.get('preferences', {}))
            merged.update(copy.deepcopy(changes))
            view = self._update(expected_revision, [{'op': 'upsert', 'section': 'profiles', 'item': {'id': profile['id'], 'preferences': merged}}], 1)
        return _tv_view(view['revision'], next(p for p in view['profiles'] if p['id'] == profile['id']))

    def restore(self, expected_revision, export, supplied):
        """Replace every user-owned item from a redacted export plus separately supplied secret values.

        All-or-nothing: the generated operation set runs in one revision-checked transaction, so any schema,
        reference, ID, format or missing-secret problem leaves the last-good document and secrets untouched.
        Pairing-derived and legacy read-only records are never replaced. supplied maps 'section/id/field' to a value.
        """
        errors = []
        if not isinstance(export, dict):
            raise SettingsError([{'path': 'export', 'error': 'object_required'}])
        version = export.get('schema_version')
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            errors.append({'path': 'export.schema_version', 'error': 'invalid_schema_version'})
        elif version > SCHEMA_VERSION:
            errors.append({'path': 'export.schema_version', 'error': 'incompatible_schema_newer', 'supported': SCHEMA_VERSION})
        if not isinstance(supplied, dict):
            errors.append({'path': 'secrets', 'error': 'object_required'})
            supplied = {}
        if errors:
            raise SettingsError(errors)
        if version < 2 and isinstance(export.get("profiles"), list):
            export = copy.deepcopy(export)  # version-1 exports carry label-derived IDs: translate them as migration 2 does
            for profile in export["profiles"]:
                prefs = profile.get("preferences") if isinstance(profile, dict) else None
                if isinstance(prefs, dict):
                    for key, kind in (("favorites", "team"), ("leagues", "league")):
                        if isinstance(prefs.get(key), list) and all(isinstance(v, str) for v in prefs[key]):
                            prefs[key] = catalog.migrate_ids(kind, prefs[key])
        for key in export:
            if key not in SECTIONS and key not in EXPORT_KEYS:
                errors.append({'path': 'export.' + str(key)[:40], 'error': 'unknown_section'})
        upserts, needed, seen = [], set(), set()
        for section, spec in SECTIONS.items():
            items = export.get(section, [])
            if not isinstance(items, list):
                errors.append({'path': 'export.' + section, 'error': 'list_required'})
                continue
            derived = [f + suffix for f in spec['secrets'] for suffix in ('_configured', '_source')]
            for n, item in enumerate(items):
                where = 'export.%s[%d]' % (section, n)
                if not isinstance(item, dict) or not (isinstance(item.get('id'), str) and ID_RE.match(item['id'])):
                    errors.append({'path': where, 'error': 'invalid_id'})
                    continue
                if item.get('read_only') or item.get('provenance'):
                    continue  # derived on this server (pairing, legacy import)
                if item['id'] in seen:
                    errors.append({'path': where, 'error': 'duplicate_id'})
                    continue
                seen.add(item['id'])
                clean = {k: copy.deepcopy(v) for k, v in item.items() if k not in derived and k not in STAMPS}
                for field in spec['secrets']:
                    if item.get(field + '_configured') is True:
                        key = '%s/%s/%s' % (section, item['id'], field)
                        needed.add(key)
                        value = supplied.get(key)
                        if isinstance(value, str) and value:
                            clean[field] = value
                        else:
                            errors.append({'path': 'secrets.' + key, 'error': 'missing_secret'})
                upserts.append({'op': 'upsert', 'section': section, 'item': clean})
        for key in supplied:
            if key not in needed:
                errors.append({'path': 'secrets.' + str(key)[:80], 'error': 'unexpected_secret'})
        if errors:
            raise SettingsError(errors)
        with self.lock:
            current = self.read()
            if current['revision'] != expected_revision:
                raise Conflict(current['revision'])
            deletes = [{'op': 'delete', 'section': section, 'id': item['id']} for section in SECTIONS for item in current[section]
                       if not item.get('read_only') and not item.get('provenance')]
            return self._update(expected_revision, deletes + upserts, RESTORE_OP_LIMIT)

    def _update(self, expected_revision, ops, limit, hook=None):
        """Apply ops atomically if the document is still at expected_revision.

        op: {'op':'upsert','section':S,'item':{...}} | {'op':'delete','section':S,'id':ID}
        Item field semantics for secret fields: absent keeps, null clears, string replaces.
        Returns the redacted view. Raises Conflict or SettingsError; in both cases nothing was written.
        """
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool):
            raise SettingsError([{'path': 'revision', 'error': 'integer_required'}])
        if not isinstance(ops, list) or not ops or len(ops) > limit:
            raise SettingsError([{'path': 'ops', 'error': 'one_to_%d_operations_required' % limit}])
        with self.lock:
            db = self._open()
            try:
                db.execute('BEGIN IMMEDIATE')
                try:
                    revision, doc = self._load(db)
                    if revision != expected_revision:
                        raise Conflict(revision)
                    before = copy.deepcopy(doc)
                    pending = {}
                    errors = []
                    if any(isinstance(op, dict) and op.get('op') == 'delete' for op in ops) and self._staged_switch(db, revision):
                        raise SettingsError([{'path': 'ops', 'error': 'destructive_change_paused_while_switch_staged'}])
                    for index, op in enumerate(ops):
                        _apply(doc, op, index, errors, pending)
                    now = int(time.time())
                    reset = _stamp(doc, before, pending, now)
                    if not errors:
                        errors.extend(_validate(doc))
                    if not errors:
                        errors.extend(_check_secret_values(doc, pending, db))
                    if errors:
                        raise SettingsError(errors)
                    for subject_id in reset:
                        try:
                            db.execute('DELETE FROM observations WHERE subject_id=?', (subject_id,))
                        except sqlite3.OperationalError:
                            break  # no observation table at this schema
                    for sid, value in pending.items():
                        if value is None:
                            db.execute('DELETE FROM secrets WHERE id=?', (sid,))
                        else:
                            db.execute('INSERT OR REPLACE INTO secrets(id,value,updated_at) VALUES(?,?,?)', (sid, value, now))
                    live = _store_refs(doc)
                    for (sid,) in db.execute('SELECT id FROM secrets').fetchall():
                        if 'store:' + sid not in live:
                            db.execute('DELETE FROM secrets WHERE id=?', (sid,))
                    db.execute('INSERT INTO snapshots(revision,doc,created_at) VALUES(?,?,?)', (revision, json.dumps(before, sort_keys=True), now))
                    db.execute('DELETE FROM snapshots WHERE revision <= ?', (revision - SNAPSHOT_LIMIT,))
                    db.execute('UPDATE config SET revision=?, doc=?, updated_at=? WHERE id=1', (revision + 1, json.dumps(doc, sort_keys=True), now))
                    if hook:
                        hook(db, doc, now)
                    db.execute('COMMIT')
                except BaseException:
                    db.execute('ROLLBACK')
                    raise
            finally:
                db.close()
        return self.read()

    # --- optional middleware: observations, credentials, switching ---------------------------------
    def _staged_switch(self, db, revision):
        """A staged switch that is still valid for the current revision pauses destructive operations."""
        try:
            return db.execute("SELECT id FROM switches WHERE state='staged' AND created_revision=?", (revision,)).fetchone()
        except sqlite3.OperationalError:
            return None

    def integration_credentials(self, integration_id):
        """Backend-internal: resolved connection for one Dispatcharr integration plus its generation. Never exposed."""
        with self.lock:
            db = self._open()
            try:
                revision, doc = self._load(db)
                item = next((i for i in doc['integrations'] if i['id'] == integration_id and i.get('kind') == 'dispatcharr'), None)
                if item is None:
                    raise SettingsError([{'path': 'integration_id', 'error': 'unknown_dispatcharr_integration'}])
                values = {}
                for field in ('base_url', 'api_key'):
                    ref = item.get(field + '_ref') or ''
                    row = db.execute('SELECT value FROM secrets WHERE id=?', (ref[6:],)).fetchone() if ref.startswith('store:') else None
                    values[field] = row[0] if row else None
            finally:
                db.close()
        values['auth_variant'] = item.get('auth_variant')
        return values, item.get('generation', 1)

    def input_location(self, section, item_id):
        """Backend-internal: the configured URL of a source/guide source and its generation (url locations only)."""
        if section not in ('sources', 'guide_sources'):
            raise SettingsError([{'path': 'section', 'error': 'unknown_section'}])
        with self.lock:
            db = self._open()
            try:
                revision, doc = self._load(db)
                item = next((i for i in doc[section] if i['id'] == item_id), None)
                if item is None:
                    raise SettingsError([{'path': section + '.' + str(item_id)[:40], 'error': 'not_found'}])
                ref = item.get('location_ref') or ''
                row = db.execute('SELECT value FROM secrets WHERE id=?', (ref[6:],)).fetchone() if ref.startswith('store:') else None
            finally:
                db.close()
        if row is None or (item.get('location_type') or 'url') != 'url':
            raise SettingsError([{'path': '%s.%s.location' % (section, item_id), 'error': 'url_location_required'}])
        return row[0], item.get('generation', 1)

    def record_observation(self, subject, subject_id, observed_generation, body, now):
        """Store a secret-free health/assessment snapshot. Discarded (not stored) when the subject changed meanwhile.

        Does not touch the document revision or its recoverable snapshot history.
        """
        with self.lock:
            db = self._open()
            try:
                db.execute('BEGIN IMMEDIATE')
                try:
                    revision, doc = self._load(db)
                    section = {'integration': 'integrations', 'source': 'sources'}.get(subject)
                    item = next((i for i in doc.get(section, []) if i['id'] == subject_id), None) if section else None
                    if item is None:
                        raise SettingsError([{'path': 'subject', 'error': 'not_found'}])
                    if item.get('generation', 1) != observed_generation:
                        raise SettingsError([{'path': 'subject', 'error': 'stale_observation'}])
                    row = db.execute('SELECT generation FROM observations WHERE subject=? AND subject_id=?', (subject, subject_id)).fetchone()
                    db.execute('INSERT OR REPLACE INTO observations(subject,subject_id,subject_generation,observed_at,body,generation) VALUES(?,?,?,?,?,?)',
                               (subject, subject_id, observed_generation, int(now), json.dumps(body, sort_keys=True), (row[0] if row else 0) + 1))
                    db.execute('COMMIT')
                except BaseException:
                    db.execute('ROLLBACK')
                    raise
            finally:
                db.close()

    def dispatcharr_status(self, now=None):
        """Admin view of every optional integration: redacted record, health with visible staleness, and switch history."""
        now = int(time.time()) if now is None else now
        view = self.read()
        seen = {o['subject_id']: o for o in view['capability_snapshots'] if o['subject'] == 'integration'}
        integrations = []
        for item in view['integrations']:
            if item.get('kind') != 'dispatcharr':
                continue
            observation = seen.get(item['id'])
            state = dispatcharr.health({'observed_at': observation['observed_at'], 'body': observation} if observation else None, now)
            integrations.append(dict(item, health=state, delivery=dispatcharr.delivery_state(state, item, view['routes'])))
        return {'revision': view['revision'], 'integrations': integrations, 'switches': self.switches(), 'contract': dispatcharr.capability_contract()}

    def switches(self):
        with self.lock:
            db = self._open()
            try:
                rows = db.execute('SELECT id, state, created_revision, plan, created_at, committed_at FROM switches ORDER BY created_at DESC, rowid DESC LIMIT ?', (SWITCH_LIMIT,)).fetchall()
            finally:
                db.close()
        return [dict(json.loads(r[3]), plan_id=r[0], state=r[1], created_revision=r[2], created_at=r[4], committed_at=r[5]) for r in rows]

    def _switch_plan(self, doc, route_id, active_accounts):
        route = next((r for r in doc['routes'] if r['id'] == route_id), None)
        if route is None:
            raise SettingsError([{'path': 'activate_route_id', 'error': 'not_found'}])
        accounts, sources = set(route.get('account_ids', [])), set(route.get('source_ids', []))
        others = [r for r in doc['routes'] if r['id'] != route_id and r.get('enabled', True)
                  and (accounts & set(r.get('account_ids', [])) or sources & set(r.get('source_ids', [])))]
        flips = [{'route_id': r['id'], 'enabled': False, 'was': r.get('enabled', True)} for r in others]
        flips.append({'route_id': route_id, 'enabled': True, 'was': route.get('enabled', True)})
        trial = copy.deepcopy(doc)
        for flip in flips:
            next(r for r in trial['routes'] if r['id'] == flip['route_id'])['enabled'] = flip['enabled']
        affected = set(accounts)
        for r in others:
            affected |= set(r.get('account_ids', []))
        blockers = [{'code': 'managed_sessions_active', 'account_id': a} for a in sorted(affected & set(active_accounts))]
        warnings = []
        if route.get('kind') == 'dispatcharr':
            warnings.append({'code': 'budget_assurance_not_centralized', 'detail': 'standard outputs and read access do not prove who holds upstream capacity; only the managed Proxy route is assured'})
            warnings.append({'code': 'real_gate_synthetic_providers_only', 'detail': 'the pinned release ran in the isolated gate with synthetic providers only; no real provider playback or combined real Jellyfin stack; SG Sports enforces the limit, not Dispatcharr'})
            warnings.append({'code': 'stream_change_invalidates_evidence', 'detail': 'game confirmation, language and health evidence for the switched feeds is discarded until re-verified on the new managed bytes'})
        if route.get('kind') == 'dispatcharr' and not accounts:
            blockers.append({'code': 'explicit_account_mapping_required'})
        return {'activate_route_id': route_id, 'flips': flips, 'affected_account_ids': sorted(affected), 'blockers': blockers, 'warnings': warnings,
                'errors': _validate(trial)}

    def preview_switch(self, activate_route_id, active_accounts=()):
        """Stage (persist) a route switch without changing configuration. Only one plan stays staged."""
        with self.lock:
            db = self._open()
            try:
                db.execute('BEGIN IMMEDIATE')
                try:
                    revision, doc = self._load(db)
                    plan = self._switch_plan(doc, activate_route_id, active_accounts)
                    plan_id = 'sw-' + _secrets.token_hex(8)
                    db.execute("UPDATE switches SET state='superseded' WHERE state='staged'")
                    db.execute('INSERT INTO switches(id,state,created_revision,plan,created_at) VALUES(?,?,?,?,?)',
                               (plan_id, 'staged', revision, json.dumps(plan, sort_keys=True), int(time.time())))
                    db.execute('COMMIT')
                except BaseException:
                    db.execute('ROLLBACK')
                    raise
            finally:
                db.close()
        return dict(plan, plan_id=plan_id, state='staged', created_revision=revision, ready=not plan['blockers'] and not plan['errors'])

    def _switch_row(self, db, plan_id, states):
        row = db.execute('SELECT state, created_revision, plan, rollback FROM switches WHERE id=?', (plan_id,)).fetchone()
        if row is None:
            raise SettingsError([{'path': 'plan_id', 'error': 'not_found'}])
        if row[0] not in states:
            raise SettingsError([{'path': 'plan_id', 'error': 'switch_' + row[0]}])
        return row[0], row[1], json.loads(row[2]), json.loads(row[3]) if row[3] else None

    def cancel_switch(self, plan_id):
        with self.lock:
            db = self._open()
            try:
                db.execute('BEGIN IMMEDIATE')
                try:
                    self._switch_row(db, plan_id, ('staged',))
                    db.execute("UPDATE switches SET state='cancelled' WHERE id=?", (plan_id,))
                    db.execute('COMMIT')
                except BaseException:
                    db.execute('ROLLBACK')
                    raise
            finally:
                db.close()
        return self.switches()

    def commit_switch(self, plan_id, expected_revision, active_accounts=(), admit=None):
        """Atomically activate the target route and deactivate the routes it overlaps, in one revision-checked write.

        admit(affected_account_ids, enabled_route_ids) reserves the affected accounts and checks the target route before anything is
        written; it raises SettingsError when the target cannot be admitted (nothing changes) and returns a release callable otherwise."""
        with self.lock:
            db = self._open()
            try:
                _, created, plan, _ = self._switch_row(db, plan_id, ('staged',))
                revision, doc = self._load(db)
            finally:
                db.close()
            if revision != expected_revision or created != revision:
                raise Conflict(revision)
            fresh = self._switch_plan(doc, plan['activate_route_id'], active_accounts)
            if fresh['blockers'] or fresh['errors']:
                raise SettingsError([{'path': 'plan', 'error': 'switch_not_ready', 'blockers': fresh['blockers'], 'validation': fresh['errors']}])
            ops = [{'op': 'upsert', 'section': 'routes', 'item': {'id': f['route_id'], 'enabled': f['enabled']}} for f in fresh['flips']]
            release = admit(fresh['affected_account_ids'], [f['route_id'] for f in fresh['flips'] if f['enabled']]) if admit else None

            def mark(db, new_doc, now):
                after = {r['id']: r.get('generation') for r in new_doc['routes']}
                rollback = {'flips': [{'route_id': f['route_id'], 'enabled': f['was'], 'generation_after': after.get(f['route_id'])} for f in fresh['flips']]}
                db.execute("UPDATE switches SET state='committed', rollback=?, committed_at=? WHERE id=?", (json.dumps(rollback, sort_keys=True), now, plan_id))
            try:
                return self._update(expected_revision, ops, 50, hook=mark)
            finally:
                if release:
                    release()

    def rollback_switch(self, plan_id, active_accounts=(), admit=None):
        """Restore the previous route flags if nothing else has changed those routes since the switch."""
        with self.lock:
            db = self._open()
            try:
                _, _, plan, rollback = self._switch_row(db, plan_id, ('committed',))
                revision, doc = self._load(db)
            finally:
                db.close()
            routes = {r['id']: r for r in doc['routes']}
            for flip in rollback['flips']:
                if flip['route_id'] not in routes or routes[flip['route_id']].get('generation') != flip['generation_after']:
                    raise SettingsError([{'path': 'plan_id', 'error': 'rollback_blocked_by_later_edit'}])
            blockers = [{'code': 'managed_sessions_active', 'account_id': a} for a in plan['affected_account_ids'] if a in set(active_accounts)]
            if blockers:
                raise SettingsError([{'path': 'plan', 'error': 'switch_not_ready', 'blockers': blockers}])
            ops = [{'op': 'upsert', 'section': 'routes', 'item': {'id': f['route_id'], 'enabled': f['enabled']}} for f in rollback['flips']]
            release = admit(plan['affected_account_ids'], [f['route_id'] for f in rollback['flips'] if f['enabled']]) if admit else None

            def mark(db, new_doc, now):
                db.execute("UPDATE switches SET state='rolled_back' WHERE id=?", (plan_id,))
            try:
                return self._update(revision, ops, 50, hook=mark)
            finally:
                if release:
                    release()

    def disconnect_integration(self, expected_revision, integration_id):
        """Remove an integration and what only it owned. Profiles, favorites, pairing, accounts/budgets and DVR rules stay.

        Refused while an enabled mediated route still uses it: the operator must first switch to a chosen route.
        """
        with self.lock:
            db = self._open()
            try:
                revision, doc = self._load(db)
            finally:
                db.close()
            if revision != expected_revision:
                raise Conflict(revision)
            if not any(i['id'] == integration_id and i.get('kind') == 'dispatcharr' for i in doc['integrations']):
                raise SettingsError([{'path': 'integration_id', 'error': 'unknown_dispatcharr_integration'}])
            if any(r.get('integration_id') == integration_id and r.get('enabled', True) for r in doc['routes']):
                raise SettingsError([{'path': 'integration_id', 'error': 'active_route_switch_required'}])
            ops = [{'op': 'delete', 'section': 'channel_mappings', 'id': m['id']} for m in doc['channel_mappings'] if m.get('integration_id') == integration_id]
            ops += [{'op': 'delete', 'section': 'routes', 'id': r['id']} for r in doc['routes'] if r.get('integration_id') == integration_id]
            for section in ('sources', 'guide_sources'):
                ops += [{'op': 'upsert', 'section': section, 'item': {'id': i['id'], 'integration_id': None}} for i in doc[section] if i.get('integration_id') == integration_id]
            ops.append({'op': 'delete', 'section': 'integrations', 'id': integration_id})
            return self._update(expected_revision, ops, RESTORE_OP_LIMIT)

    def secret(self, ref):
        """Backend-internal resolution of a store: reference. Never exposed through the API."""
        if not str(ref).startswith('store:'):
            return None
        with self.lock:
            db = self._open()
            try:
                row = db.execute('SELECT value FROM secrets WHERE id=?', (ref[6:],)).fetchone()
            finally:
                db.close()
        return row[0] if row else None

    def artwork_config(self):
        """Backend-internal team-artwork configuration, or None for the defaults. Resolves the stored base address; never exposed by the API."""
        with self.lock:
            db = self._open()
            try:
                _, doc = self._load(db)
                row = db.execute("SELECT value FROM meta WHERE key='artwork_default'").fetchone()
                inherited = row[0] if row else None
            finally:
                db.close()
        item = next((i for i in doc['integrations'] if i.get('kind') == 'artwork'), None)
        if item is None:
            return {'mode': inherited} if inherited else None
        ref = item.get('base_url_ref')
        return {'mode': (item.get('options') or {}).get('mode', 'off'), 'enabled': item.get('enabled', True),
                'url_template': self.secret(ref) if ref else None}

    def snapshots(self):
        with self.lock:
            db = self._open()
            try:
                return [r[0] for r in db.execute('SELECT revision FROM snapshots ORDER BY revision')]
            finally:
                db.close()


# --- helpers ---------------------------------------------------------------------------------
def _tv_view(revision, profile):
    prefs = profile.get('preferences', {})
    return {'revision': revision, 'profile_id': profile['id'], 'profile_name': profile['name'],
            'preferences': {k: copy.deepcopy(prefs[k]) for k in TV_PREFERENCE_KEYS if k in prefs}}


def _new_id(section, doc):
    taken = {i['id'] for items in doc.values() for i in items}
    while True:
        candidate = SECTIONS[section]['prefix'] + '-' + _secrets.token_hex(6)
        if candidate not in taken:
            return candidate


def _strip(item):
    return {k: v for k, v in item.items() if k not in STAMPS}


def _stamp(doc, before, pending, now):
    """Server-owned generation and timestamps for stamped sections; returns subject ids whose observations are now invalid."""
    reset = set()
    for section in STAMPED:
        old = {i['id']: i for i in before.get(section, [])}
        for item in doc[section]:
            prior = old.get(item['id'])
            touched = any(item.get(f + '_ref', '')[6:] in pending for f in SECTIONS[section]['secrets'] if item.get(f + '_ref', '').startswith('store:'))
            if prior is None:
                item['generation'], item['created_at'], item['updated_at'] = 1, now, now
                continue
            if touched or _strip(prior) != _strip(item):
                item['generation'] = prior.get('generation', 1) + 1
                item['created_at'] = prior.get('created_at', now)
                item['updated_at'] = now
                if touched or any(prior.get(f) != item.get(f) for f in OBSERVATION_FIELDS):
                    reset.add(item['id'])
            else:
                for key in STAMPS:
                    if key in prior:
                        item[key] = prior[key]
        reset.update(i['id'] for i in before.get(section, []) if not any(j['id'] == i['id'] for j in doc[section]))
    return reset


def _source_accounts(source):
    mapped = [a for a in ([source.get('account_id')] + list(source.get('account_ids') or [])) if a]
    return list(dict.fromkeys(mapped))


def _check_secret_values(doc, pending, db):
    """Validate newly written secret values without ever echoing them (errors carry paths and codes only)."""
    errors = []

    def value_of(item, field):
        ref = item.get(field + '_ref')
        if not ref or not ref.startswith('store:'):
            return None
        if ref[6:] in pending:
            return pending[ref[6:]]
        row = db.execute('SELECT value FROM secrets WHERE id=?', (ref[6:],)).fetchone()
        return row[0] if row else None

    def http_url(value):
        try:
            parts = urllib.parse.urlsplit(value)
            return parts.scheme in ('http', 'https') and bool(parts.hostname) and not re.search(r'\s', value)
        except ValueError:
            return False
    for section in ('sources', 'guide_sources'):
        for item in doc[section]:
            value, where = value_of(item, 'location'), '%s[%s]' % (section, item['id'])
            if value is None:
                continue
            kind = item.get('location_type') or 'url'  # absent type is treated as a URL (older writers)
            if kind == 'url':
                if not http_url(value):
                    errors.append({'path': where + '.location', 'error': 'invalid_url'})
            elif not FILE_NAME_RE.match(value) or '..' in value or not value.lower().endswith(FILE_EXTENSIONS[section]):
                errors.append({'path': where + '.location', 'error': 'invalid_file_reference'})
    for item in doc['integrations']:
        where = 'integrations[%s]' % item['id']
        base, key = value_of(item, 'base_url'), value_of(item, 'api_key')
        if base is not None and not dispatcharr.valid_base_url(base):
            errors.append({'path': where + '.base_url', 'error': 'invalid_base_url'})
        if key is not None and not dispatcharr.valid_api_key(key):
            errors.append({'path': where + '.api_key', 'error': 'invalid_api_key'})
    for item in doc['accounts']:
        value = value_of(item, 'credential')
        if value is None:
            continue
        try:
            data = json.loads(value)
            ok = (isinstance(data, dict) and set(data) == {'host', 'username', 'password'} and all(isinstance(v, str) and v for v in data.values())
                  and http_url(data['host']))
        except ValueError:
            ok = False
        if not ok:
            errors.append({'path': 'accounts[%s].credential' % item['id'], 'error': 'invalid_xtream_credential'})
    return errors


def _store_refs(doc):
    refs = set()
    for section, items in doc.items():
        for item in items:
            for field in SECTIONS[section]['secrets']:
                ref = item.get(field + '_ref')
                if ref:
                    refs.add(ref)
    return refs


def _redact(section, item, configured):
    out = {k: copy.deepcopy(v) for k, v in item.items() if k not in REF_FIELDS}
    for field in SECTIONS[section]['secrets']:
        ref = item.get(field + '_ref')
        if ref and ref.startswith('store:'):
            out[field + '_configured'] = ref[6:] in configured
        else:
            out[field + '_configured'] = bool(ref)
        out[field + '_source'] = ('file' if ref and ref.startswith('file:') else 'store') if ref else None
    return out


def _apply(doc, op, index, errors, pending):
    path = 'ops[%d]' % index
    if not isinstance(op, dict) or op.get('section') not in SECTIONS:
        errors.append({'path': path, 'error': 'unknown_section'})
        return
    section, spec = op['section'], SECTIONS[op['section']]
    items = doc[section]
    if op.get('op') == 'delete':
        if op.get('id') not in [i['id'] for i in items]:
            errors.append({'path': path + '.id', 'error': 'not_found'})
            return
        doc[section] = [i for i in items if i['id'] != op['id']]
        return
    if op.get('op') != 'upsert' or not isinstance(op.get('item'), dict):
        errors.append({'path': path, 'error': 'unknown_operation'})
        return
    data = dict(op['item'])
    item_id = data.pop('id', None)
    existing = next((i for i in items if i['id'] == item_id), None) if item_id else None
    if item_id is not None and not (isinstance(item_id, str) and ID_RE.match(item_id)):
        errors.append({'path': path + '.item.id', 'error': 'invalid_id'})
        return
    if item_id and any(item_id == other['id'] for sec, others in doc.items() if sec != section for other in others):
        errors.append({'path': path + '.item.id', 'error': 'id_in_use'})
        return
    if existing is not None and existing.get('read_only'):
        errors.append({'path': path + '.item.id', 'error': 'read_only_legacy_import'})
        return
    item = existing if existing is not None else {'id': item_id or _new_id(section, doc)}
    for key, value in data.items():
        if key in spec['secrets']:
            ref_field = key + '_ref'
            old = item.get(ref_field)
            if value is None:
                if old and old.startswith('store:'):
                    pending[old[6:]] = None
                item.pop(ref_field, None)
            elif isinstance(value, str) and value:
                sid = old[6:] if old and old.startswith('store:') else _secrets.token_hex(12)
                pending[sid] = value
                item[ref_field] = 'store:' + sid
            else:
                errors.append({'path': '%s.item.%s' % (path, key), 'error': 'string_or_null_required'})
        elif key in REF_FIELDS:
            errors.append({'path': '%s.item.%s' % (path, key), 'error': 'read_only'})
        elif key in spec['fields']:
            expected = spec['fields'][key]
            if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
                errors.append({'path': '%s.item.%s' % (path, key), 'error': 'wrong_type'})
            else:
                item[key] = value
        else:
            errors.append({'path': '%s.item.%s' % (path, key), 'error': 'unknown_field'})
    if existing is None:
        items.append(item)


def _scan_urlish(value, path, errors):
    if isinstance(value, str):
        if URLISH.search(value):
            errors.append({'path': path, 'error': 'url_or_credential_not_allowed_in_plain_settings'})
    elif isinstance(value, dict):
        for k, v in value.items():
            _scan_urlish(v, path + '.' + str(k), errors)
    elif isinstance(value, list):
        for n, v in enumerate(value):
            _scan_urlish(v, '%s[%d]' % (path, n), errors)


def _validate(doc):
    errors = []
    ids = {section: {i['id'] for i in items} for section, items in doc.items()}
    seen = set()
    for section, items in doc.items():
        spec = SECTIONS[section]
        for item in items:
            p = '%s[%s]' % (section, item['id'])
            if item['id'] in seen:
                errors.append({'path': p, 'error': 'duplicate_id'})
            seen.add(item['id'])
            _scan_urlish({k: v for k, v in item.items() if k not in REF_FIELDS}, p, errors)
            for ref_field in REF_FIELDS:
                ref = item.get(ref_field)
                if ref is not None and not (isinstance(ref, str) and re.match(r'^(store:[0-9a-f]{8,32}|file:[A-Za-z0-9][A-Za-z0-9._-]{0,63})$', ref)):
                    errors.append({'path': p + '.' + ref_field, 'error': 'invalid_reference'})
            if 'name' in spec['fields'] and not (isinstance(item.get('name'), str) and item['name'].strip()):
                errors.append({'path': p + '.name', 'error': 'name_required'})
    for item in doc['profiles']:
        prefs = item.get('preferences', {})
        if not isinstance(prefs, dict):
            errors.append({'path': 'profiles[%s].preferences' % item['id'], 'error': 'object_required'})
            continue
        for key, value in prefs.items():
            where = 'profiles[%s].preferences.%s' % (item['id'], key)
            if key not in PREFERENCE_KEYS or not isinstance(value, PREFERENCE_KEYS[key]):
                errors.append({'path': where, 'error': 'unknown_or_wrong_type'})
            elif key in ('favorites', 'leagues'):
                if len(value) > LIST_PREFERENCE_LIMIT:
                    errors.append({'path': where, 'error': 'too_many_entries'})
                elif not all(isinstance(v, str) and CANONICAL_ID_RE.match(v) for v in value):
                    errors.append({'path': where, 'error': 'canonical_ids_required'})
                elif len(set(value)) != len(value):
                    errors.append({'path': where, 'error': 'duplicate_entries'})
            elif key == 'language' and not LANGUAGE_RE.match(value):
                errors.append({'path': where, 'error': 'invalid_language'})
            elif key in NESTED_PREFERENCES:
                for sub, sub_value in value.items():
                    check = NESTED_PREFERENCES[key].get(sub)
                    if check is None:
                        errors.append({'path': where + '.' + str(sub)[:40], 'error': 'unknown_field'})
                    elif not check(sub_value):
                        errors.append({'path': where + '.' + sub, 'error': 'invalid_value'})
    for item in doc['accounts']:
        value = item.get('max_connections')
        if not isinstance(value, int) or not 1 <= value <= 100:
            errors.append({'path': 'accounts[%s].max_connections' % item['id'], 'error': 'integer_1_to_100_required'})
    for item in doc['sources']:
        if item.get('kind') not in SOURCE_KINDS:
            errors.append({'path': 'sources[%s].kind' % item['id'], 'error': 'unsupported_kind'})
        if item.get('account_id') is not None and item['account_id'] not in ids['accounts']:
            errors.append({'path': 'sources[%s].account_id' % item['id'], 'error': 'unknown_account'})
        if not all(isinstance(a, str) and a in ids['accounts'] for a in item.get('account_ids', [])):
            errors.append({'path': 'sources[%s].account_ids' % item['id'], 'error': 'unknown_account'})
        if item.get('location_type') not in (None,) + LOCATION_TYPES:
            errors.append({'path': 'sources[%s].location_type' % item['id'], 'error': 'unsupported_location_type'})
        if item.get('format') not in (None,) + SOURCE_FORMATS:
            errors.append({'path': 'sources[%s].format' % item['id'], 'error': 'unsupported_format'})
        if item.get('kind') == 'xtream' and item.get('location_ref'):
            errors.append({'path': 'sources[%s].location' % item['id'], 'error': 'location_not_used_for_xtream'})
    for item in doc['guide_sources']:
        if item.get('kind') not in GUIDE_KINDS:
            errors.append({'path': 'guide_sources[%s].kind' % item['id'], 'error': 'unsupported_kind'})
        if item.get('location_type') not in (None,) + LOCATION_TYPES:
            errors.append({'path': 'guide_sources[%s].location_type' % item['id'], 'error': 'unsupported_location_type'})
        if item.get('format') not in (None,) + GUIDE_FORMATS:
            errors.append({'path': 'guide_sources[%s].format' % item['id'], 'error': 'unsupported_format'})
    for item in doc['guide_mappings']:
        if item.get('guide_source_id') not in ids['guide_sources']:
            errors.append({'path': 'guide_mappings[%s].guide_source_id' % item['id'], 'error': 'unknown_guide_source'})
        if item.get('source_id') is not None and item['source_id'] not in ids['sources']:
            errors.append({'path': 'guide_mappings[%s].source_id' % item['id'], 'error': 'unknown_source'})
    for item in doc['routes']:
        if item.get('kind') not in ROUTE_KINDS:
            errors.append({'path': 'routes[%s].kind' % item['id'], 'error': 'unsupported_kind'})
        for account in item.get('account_ids', []):
            if account not in ids['accounts']:
                errors.append({'path': 'routes[%s].account_ids' % item['id'], 'error': 'unknown_account'})
    for item in doc['devices']:
        if item.get('role') not in DEVICE_ROLES:
            errors.append({'path': 'devices[%s].role' % item['id'], 'error': 'unsupported_role'})
        if item.get('profile_id') is not None and item['profile_id'] not in ids['profiles']:
            errors.append({'path': 'devices[%s].profile_id' % item['id'], 'error': 'unknown_profile'})
    integrations = {i['id']: i for i in doc['integrations']}
    sources_by_id = {i['id']: i for i in doc['sources']}
    for item in doc['integrations']:
        p = 'integrations[%s]' % item['id']
        if not isinstance(item.get('kind'), str) or not item['kind']:
            errors.append({'path': p + '.kind', 'error': 'kind_required'})
        if item.get('kind') == 'dispatcharr':
            if item.get('auth_variant') not in (None,) + dispatcharr.AUTH_VARIANTS:
                errors.append({'path': p + '.auth_variant', 'error': 'unsupported_auth_variant'})
            if item.get('credential_ref'):
                errors.append({'path': p + '.credential', 'error': 'credential_not_used'})
        elif item.get('kind') == 'artwork':
            # Versioned team-artwork source (SGH-302): one item, mode in options, optional write-only base address.
            options = item.get('options', {})
            if item.get('auth_variant') is not None or item.get('api_key_ref') or item.get('credential_ref'):
                errors.append({'path': p, 'error': 'artwork_unsupported_fields'})
            if not isinstance(options, dict) or set(options) - {'mode'} or options.get('mode', 'espn') not in ARTWORK_MODES:
                errors.append({'path': p + '.options', 'error': 'invalid_artwork_options'})
            elif options.get('mode') == 'url' and not item.get('base_url_ref'):
                errors.append({'path': p + '.base_url', 'error': 'base_url_required_for_url_mode'})
            if sum(1 for other in doc['integrations'] if other.get('kind') == 'artwork') > 1:
                errors.append({'path': p, 'error': 'only_one_artwork_source'})
        elif item.get('auth_variant') is not None or item.get('base_url_ref') or item.get('api_key_ref'):
            errors.append({'path': p, 'error': 'dispatcharr_fields_on_other_integration'})
    for section in ('sources', 'guide_sources'):
        for item in doc[section]:
            p = '%s[%s]' % (section, item['id'])
            if item.get('middleware') not in MIDDLEWARES:
                errors.append({'path': p + '.middleware', 'error': 'unsupported_middleware'})
            link = item.get('integration_id')
            if link is not None and (link not in integrations or integrations[link].get('kind') != 'dispatcharr'):
                errors.append({'path': p + '.integration_id', 'error': 'unknown_dispatcharr_integration'})
            elif link is not None and item.get('middleware') != 'dispatcharr':
                errors.append({'path': p + '.middleware', 'error': 'middleware_required_with_integration'})
    for item in doc['routes']:
        p = 'routes[%s]' % item['id']
        link = item.get('integration_id')
        if item.get('kind') == 'dispatcharr':
            if link not in integrations or integrations[link].get('kind') != 'dispatcharr':
                errors.append({'path': p + '.integration_id', 'error': 'unknown_dispatcharr_integration'})
            if not item.get('account_ids'):
                errors.append({'path': p + '.account_ids', 'error': 'explicit_account_mapping_required'})
        elif link is not None:
            errors.append({'path': p + '.integration_id', 'error': 'integration_only_on_dispatcharr_route'})
        for source_id in item.get('source_ids', []):
            source = sources_by_id.get(source_id)
            if source is None:
                errors.append({'path': p + '.source_ids', 'error': 'unknown_source'})
            elif item.get('kind') == 'dispatcharr':
                covered = set(_source_accounts(source))
                if not covered or not covered <= set(item.get('account_ids', [])):
                    errors.append({'path': p + '.source_ids', 'error': 'source_account_not_covered_by_route'})
    # One account, one active delivery route: never a direct and a mediated route (or two of either) at once.
    active = [r for r in doc['routes'] if r.get('enabled', True)]
    for n, left in enumerate(active):
        for right in active[n + 1:]:
            if set(left.get('account_ids', [])) & set(right.get('account_ids', [])):
                errors.append({'path': 'routes[%s].account_ids' % right['id'], 'error': 'account_in_multiple_active_routes', 'other': left['id']})
            if set(left.get('source_ids', [])) & set(right.get('source_ids', [])):
                errors.append({'path': 'routes[%s].source_ids' % right['id'], 'error': 'source_in_multiple_active_routes', 'other': left['id']})
    seen_pairs, seen_upstream = {}, {}
    for item in doc['channel_mappings']:
        p = 'channel_mappings[%s]' % item['id']
        cid, source = item.get('canonical_channel_id'), sources_by_id.get(item.get('source_id'))
        if not (isinstance(cid, str) and CHANNEL_ID_RE.match(cid)):
            errors.append({'path': p + '.canonical_channel_id', 'error': 'channel_id_required'})
        if source is None:
            errors.append({'path': p + '.source_id', 'error': 'unknown_source'})
        link = integrations.get(item.get('integration_id'))
        if link is None or link.get('kind') != 'dispatcharr':
            errors.append({'path': p + '.integration_id', 'error': 'unknown_dispatcharr_integration'})
        elif source is not None and source.get('integration_id') != item['integration_id']:
            errors.append({'path': p + '.source_id', 'error': 'source_not_linked_to_integration'})
        for field in ('dispatcharr_channel', 'dispatcharr_stream'):
            value = item.get(field)
            if (value is None and field == 'dispatcharr_channel') or (value is not None and not (isinstance(value, str) and UPSTREAM_ID_RE.match(value))):
                errors.append({'path': p + '.' + field, 'error': 'upstream_id_required' if value is None else 'invalid_upstream_id'})
        account = item.get('account_id')
        if account is not None:
            if account not in ids['accounts']:
                errors.append({'path': p + '.account_id', 'error': 'unknown_account'})
            elif source is not None and _source_accounts(source) and account not in _source_accounts(source):
                errors.append({'path': p + '.account_id', 'error': 'account_not_in_source'})
        pair = (cid, item.get('source_id'))
        if pair in seen_pairs:
            errors.append({'path': p, 'error': 'duplicate_mapping', 'other': seen_pairs[pair]})
        seen_pairs[pair] = item['id']
        upstream = (item.get('integration_id'), item.get('dispatcharr_channel'))
        if upstream in seen_upstream and seen_upstream[upstream][0] != cid:
            errors.append({'path': p + '.dispatcharr_channel', 'error': 'upstream_channel_mapped_to_other_channel', 'other': seen_upstream[upstream][1]})
        seen_upstream.setdefault(upstream, (cid, item['id']))
    if not doc['profiles']:
        errors.append({'path': 'profiles', 'error': 'at_least_one_profile_required'})
    return errors

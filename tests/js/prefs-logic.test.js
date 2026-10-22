'use strict';
// Deterministic checks for the browser favorites / paired-device helpers. Run: node tests/js/prefs-logic.test.js
const assert = require('assert');
const P = require('../../web/prefs-logic.js');
const catalog = { teams: [
  { id: 'team.soccer.men.la-liga.fc-barcelona', name: 'FC Barcelona', sport: 'Soccer', gender: 'men', league: 'La Liga', support: { schedule: true, program_matching: true } },
  { id: 'team.soccer.women.liga-f.fc-barcelona', name: 'FC Barcelona Femení', sport: 'Soccer', gender: 'women', league: 'Liga F', support: { schedule: false, program_matching: false } },
  { id: 'team.baseball.men.mlb.seattle-mariners', name: 'Seattle Mariners', sport: 'Baseball', gender: 'men', league: 'MLB', support: { schedule: false, program_matching: false } }],
  leagues: [{ id: 'league.baseball.mlb', name: 'MLB', sport: 'Baseball', support: { schedule: false, program_matching: false } }] };
const by = P.indexCatalog(catalog);

// reorder / remove / add / empty never mutate their input and never use positions to name an entry
const ids = ['a', 'b', 'c'];
assert.deepStrictEqual(P.moveUp(ids, 2), ['a', 'c', 'b']);
assert.deepStrictEqual(P.moveDown(ids, 0), ['b', 'a', 'c']);
assert.deepStrictEqual(P.moveUp(ids, 0), ids);
assert.deepStrictEqual(P.moveDown(ids, 2), ids);
assert.deepStrictEqual(P.removeAt(ids, 1), ['a', 'c']);
assert.deepStrictEqual(P.removeAt(['x'], 0), []);
assert.deepStrictEqual(P.withAdded(ids, 'a'), ids);
assert.deepStrictEqual(P.withAdded([], 'z'), ['z']);
assert.deepStrictEqual(ids, ['a', 'b', 'c']);

// men's / women's distinction and honest support notes
assert.ok(P.describe(by, 'team.soccer.men.la-liga.fc-barcelona').label.includes("men's"));
assert.ok(P.describe(by, 'team.soccer.women.liga-f.fc-barcelona').label.includes("women's"));
assert.notStrictEqual(P.describe(by, 'team.soccer.men.la-liga.fc-barcelona').label, P.describe(by, 'team.soccer.women.liga-f.fc-barcelona').label);
assert.strictEqual(P.describe(by, 'team.soccer.men.la-liga.fc-barcelona').note, '');
assert.ok(P.describe(by, 'team.baseball.men.mlb.seattle-mariners').note.includes('no schedule'));

// unknown-but-preserved: shown as kept, never dropped or rewritten
const unknown = P.describe(by, 'team.x.y.future-entry');
assert.strictEqual(unknown.known, false);
assert.ok(unknown.label.includes('team.x.y.future-entry') && unknown.label.includes('kept'));

// catalog candidates exclude chosen entries and stay stable under a rename
assert.deepStrictEqual(P.candidates(catalog, ['team.soccer.men.la-liga.fc-barcelona']).map(t => t.id), ['team.baseball.men.mlb.seattle-mariners', 'team.soccer.women.liga-f.fc-barcelona']);
assert.strictEqual(P.candidates(catalog, catalog.teams.map(t => t.id)).length, 0);
const renamed = JSON.parse(JSON.stringify(catalog)); renamed.teams[0].name = 'Barça';
assert.ok(P.describe(P.indexCatalog(renamed), 'team.soccer.men.la-liga.fc-barcelona').label.startsWith('Barça'));  // same ID, new label

// saving replaces only favorites and keeps every other preference
const op = P.profileSave({ id: 'prof-household', preferences: { language: 'es', favorites: ['old'], leagues: ['league.baseball.mlb'], recording: { enabled: true } } }, ['team.baseball.men.mlb.seattle-mariners']);
assert.deepStrictEqual(op, { op: 'upsert', section: 'profiles', item: { id: 'prof-household', preferences: { language: 'es', favorites: ['team.baseball.men.mlb.seattle-mariners'], leagues: ['league.baseball.mlb'], recording: { enabled: true } } } });
assert.deepStrictEqual(P.profileSave({ id: 'p' }, []).item.preferences, { favorites: [] });

// devices: TVs only, no token fields, revoke only for an active pairing, profile name resolved
const rows = P.deviceRows({ profiles: [{ id: 'prof-household', name: 'Household' }], devices: [
  { id: 'dev-aaaaaaaaaaaaaaaa', name: 'Paired TV', role: 'tv', profile_id: 'prof-household', provenance: 'pairing', state: 'active', token: 'must-not-surface' },
  { id: 'dev-bbbbbbbbbbbbbbbb', name: 'Old TV', role: 'tv', profile_id: null, provenance: 'pairing', state: 'unpaired' },
  { id: 'dev-admin', name: 'Admin', role: 'admin' }] });
assert.strictEqual(rows.length, 2);
assert.deepStrictEqual(rows.map(r => r.canRevoke), [true, false]);
assert.strictEqual(rows[0].profileName, 'Household');
assert.strictEqual(rows[1].profileName, 'No profile assigned');
assert.ok(!JSON.stringify(rows).includes('must-not-surface'));
assert.deepStrictEqual(P.assignOp('dev-a', ''), { op: 'upsert', section: 'devices', item: { id: 'dev-a', profile_id: null } });

// classified outcomes
assert.strictEqual(P.classifyOutcome(409, { error: 'revision_conflict', revision: 7 }).kind, 'Conflict');
assert.ok(P.classifyOutcome(409, { error: 'revision_conflict' }).message.includes('Nothing was saved'));
assert.strictEqual(P.classifyOutcome(422, { error: 'invalid_settings', details: [{ path: 'devices.dev-x', error: 'not_paired' }] }).kind, 'Device');
assert.strictEqual(P.classifyOutcome(422, { error: 'invalid_settings', details: [{ path: 'devices[d].profile_id', error: 'unknown_profile' }] }).kind, 'Device');
assert.strictEqual(P.classifyOutcome(422, { error: 'invalid_settings', details: [{ path: 'p', error: 'canonical_ids_required' }] }).kind, 'Preference');
assert.strictEqual(P.classifyOutcome(401, { error: 'unauthorized' }).kind, 'Access');
assert.strictEqual(P.classifyOutcome(503, { error: 'settings_schema_unsupported' }).kind, 'Server');
assert.strictEqual(P.classifyOutcome(0, null).kind, 'Network');
assert.strictEqual(P.classifyOutcome(500, {}).kind, 'Other');
console.log('prefs-logic: all checks passed');

// Configuration home: a fresh backend has open required steps; a complete one is ready; attention items block "ready".
{
  const fresh = P.homeChecklist({ sources: [], guide_sources: [], devices: [], profiles: [{ id: 'p', preferences: {} }], integrations: [] }, { unresolved_source_ids: [] });
  assert.strictEqual(fresh.ready, false);
  assert.strictEqual(fresh.openRequired, 2);
  assert.strictEqual(fresh.items.find(i => i.id === 'sources').state, 'todo');
  assert.strictEqual(fresh.items.find(i => i.id === 'guide').state, 'optional');
  const ok = { sources: [{ id: 's1', kind: 'm3u', location_configured: true }], guide_sources: [{ id: 'g1', location_configured: true }],
    devices: [{ id: 'd1', role: 'tv', provenance: 'pairing', state: 'active' }], profiles: [{ id: 'p', preferences: { favorites: ['t'] } }],
    integrations: [{ kind: 'artwork', options: { mode: 'off' } }] };
  const ready = P.homeChecklist(ok, { unresolved_source_ids: [] });
  assert.strictEqual(ready.ready, true);
  assert.strictEqual(ready.items.find(i => i.id === 'artwork').state, 'optional');
  const bad = P.homeChecklist(Object.assign({}, ok, { sources: [{ id: 's1', kind: 'm3u', location_configured: false }] }), { unresolved_source_ids: [] });
  assert.strictEqual(bad.ready, false);
  assert.strictEqual(bad.items.find(i => i.id === 'sources').state, 'attention');
  const unmapped = P.homeChecklist(ok, { unresolved_source_ids: ['s1'] });
  assert.strictEqual(unmapped.items.find(i => i.id === 'sources').state, 'attention');
}

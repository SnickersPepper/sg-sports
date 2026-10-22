'use strict';
// Pure helpers for the browser's favorites and paired-device administration. No DOM, no network, no secrets, so they can be
// checked deterministically under Node (tests/js/prefs-logic.test.js) and are reused unchanged by the setup page.
(function (root) {
  var DEVICE_CLASSES = {
    Conflict: ['revision_conflict'],
    Device: ['not_paired', 'unknown_device', 'unknown_profile', 'unsupported_role'],
    Preference: ['canonical_ids_required', 'duplicate_entries', 'too_many_entries', 'unknown_or_wrong_type']
  };
  var ADVICE = {
    revision_conflict: 'Someone else (the TV or another browser) changed the settings first. Nothing was saved; the latest values are loaded, so review them and try again.',
    not_paired: 'That TV is no longer paired, so there is nothing to revoke.',
    unknown_profile: 'Choose a profile that still exists.',
    canonical_ids_required: 'Favorites must be catalog entries.',
    duplicate_entries: 'A favorite appears twice.',
    too_many_entries: 'That is more favorites than a profile can hold.'
  };

  function moveUp(list, index) { if (index <= 0 || index >= list.length) return list.slice(); var out = list.slice(); out.splice(index - 1, 0, out.splice(index, 1)[0]); return out; }
  function moveDown(list, index) { if (index < 0 || index >= list.length - 1) return list.slice(); var out = list.slice(); out.splice(index + 1, 0, out.splice(index, 1)[0]); return out; }
  function removeAt(list, index) { if (index < 0 || index >= list.length) return list.slice(); var out = list.slice(); out.splice(index, 1); return out; }
  function withAdded(list, id) { return list.indexOf(id) >= 0 ? list.slice() : list.concat([id]); }

  // The catalog is the only source of names; an ID it does not know is shown as kept, never guessed or dropped.
  function indexCatalog(catalog) {
    var by = {};
    ((catalog && catalog.teams) || []).concat((catalog && catalog.leagues) || []).forEach(function (e) { by[e.id] = e; });
    return by;
  }
  function describe(by, id) {
    var e = by[id];
    if (!e) return { id: id, known: false, label: id + ' (not in this catalog; kept as saved)' };
    var parts = [e.name, e.sport];
    if (e.gender && e.gender !== 'mixed') parts.push(e.gender === 'women' ? "women's" : "men's");
    var s = e.support || {};
    return { id: id, known: true, label: parts.join(' · '), schedule: !!s.schedule, programMatching: !!s.program_matching,
      note: s.schedule || s.program_matching ? '' : 'Selectable; no schedule or programme matching yet' };
  }
  // Teams the profile has not chosen yet, ordered by sport, then name, then gender, so >7 entries stay findable.
  function candidates(catalog, chosen) {
    return ((catalog && catalog.teams) || []).filter(function (t) { return chosen.indexOf(t.id) < 0; }).sort(function (a, b) {
      return (a.sport + '|' + a.league + '|' + a.name + '|' + (a.gender || '')).localeCompare(b.sport + '|' + b.league + '|' + b.name + '|' + (b.gender || ''));
    });
  }
  // A profile save replaces the whole preferences object, so unrelated preferences are carried over untouched.
  function profileSave(profile, favorites) {
    var prefs = JSON.parse(JSON.stringify(profile.preferences || {}));
    prefs.favorites = favorites.slice();
    return { op: 'upsert', section: 'profiles', item: { id: profile.id, preferences: prefs } };
  }
  function classifyOutcome(status, data) {
    var code = data && (data.error === 'invalid_settings' && data.details && data.details[0] ? data.details[0].error : data.error);
    var kind = 'Other';
    if (status === 409) kind = 'Conflict';
    else if (status === 401) kind = 'Access';
    else if (status === 503) kind = 'Server';
    else if (status === 0) kind = 'Network';
    else Object.keys(DEVICE_CLASSES).forEach(function (k) { if (DEVICE_CLASSES[k].indexOf(code) >= 0) kind = k; });
    var text = {
      Conflict: ADVICE.revision_conflict, Access: 'The administrator token was not accepted.', Server: 'The server cannot use its settings database right now.',
      Network: 'The server could not be reached. Nothing was changed.'
    }[kind] || ADVICE[code] || 'The request was rejected (' + status + '). Nothing was changed.';
    return { kind: kind, code: code || '', message: kind + ' problem: ' + text };
  }
  // Only fields safe to show: never the pairing token or its digest. The ID is a short registry label, not a credential.
  function deviceRows(view) {
    var names = {};
    (view.profiles || []).forEach(function (p) { names[p.id] = p.name; });
    return (view.devices || []).filter(function (d) { return d.role === 'tv'; }).map(function (d) {
      var live = d.provenance !== 'pairing' || d.state === 'active';
      return { id: d.id, name: d.name, state: d.state || 'active', profileId: d.profile_id || '', profileName: d.profile_id ? (names[d.profile_id] || d.profile_id) : 'No profile assigned',
        canAssign: true, canRevoke: d.provenance === 'pairing' && d.state === 'active', live: live };
    });
  }
  function assignOp(deviceId, profileId) { return { op: 'upsert', section: 'devices', item: { id: deviceId, profile_id: profileId || null } }; }

  // Configuration home: one entry per task, from the redacted settings view and the setup-status document. Pure, so it is unit-tested.
  function homeChecklist(view, status) {
    var sources = (view.sources || []).filter(function (s) { return !s.read_only; }), guides = view.guide_sources || [];
    var unresolved = (status && status.unresolved_source_ids) || [];
    var badSources = sources.filter(function (s) { return (s.kind !== 'xtream' && !s.location_configured) || unresolved.indexOf(s.id) >= 0; });
    var legacy = (view.sources || []).length - sources.length;
    var live = deviceRows(view).filter(function (d) { return d.live; });
    var badGuides = guides.filter(function (g) { return !g.location_configured; });
    var profile = (view.profiles || [])[0], favs = profile && profile.preferences && Array.isArray(profile.preferences.favorites) ? profile.preferences.favorites.length : 0;
    var disp = (view.integrations || []).filter(function (i) { return i.kind === 'dispatcharr'; }).length;
    var art = (view.integrations || []).filter(function (i) { return i.kind === 'artwork'; })[0];
    var items = [
      { id: 'security', title: 'Security and administrator account', required: true, state: 'done', detail: 'Signed in with your own password.', href: '#ac-h' },
      { id: 'pairing', title: 'Server identity and TV pairing', required: true, state: live.length ? 'done' : 'todo',
        detail: live.length ? live.length + ' TV' + (live.length === 1 ? '' : 's') + ' paired.' : 'No TV is paired yet. Create a pairing code and enter it on the TV.', href: '#dv-h' },
      { id: 'sources', title: 'IPTV playlist sources', required: true,
        state: sources.length + legacy === 0 ? 'todo' : badSources.length ? 'attention' : 'done',
        detail: sources.length + legacy === 0 ? 'Add an IPTVBoss export, an M3U playlist or an Xtream-style login.' : badSources.length ? badSources.length + ' source(s) need a location or an account mapping.' : (sources.length + legacy) + ' source(s) configured.', href: '#src-h' },
      { id: 'guide', title: 'Guide sources (XMLTV)', required: false, state: !guides.length ? 'optional' : badGuides.length ? 'attention' : 'done',
        detail: !guides.length ? 'Not set. Recommended so channels show what is on.' : badGuides.length ? badGuides.length + ' guide(s) have no location.' : guides.length + ' guide(s) configured.', href: '#gd-h' },
      { id: 'dispatcharr', title: 'IPTVBoss / Dispatcharr integration', required: false, state: disp ? 'done' : 'optional', detail: disp ? disp + ' connection(s).' : 'Not used. Direct playlists work without it.', href: '#dp-h' },
      { id: 'favorites', title: 'Favorites, leagues and teams', required: false, state: favs ? 'done' : 'optional', detail: favs ? favs + ' favorite team(s) saved.' : 'None chosen yet. You can also choose them on the TV.', href: '#fav-h' },
      { id: 'artwork', title: 'Artwork', required: false, state: art && art.options && art.options.mode && art.options.mode !== 'off' ? 'done' : 'optional',
        detail: art && art.options && art.options.mode && art.options.mode !== 'off' ? 'Logo source: ' + art.options.mode + '.' : 'Off: colour monograms are shown.', href: '#art-h' },
      { id: 'backup', title: 'Backup, restore, import and export', required: false, state: 'optional', detail: 'Download a redacted export once everything works.', href: '#bk-h' }
    ];
    var open = items.filter(function (i) { return i.required && i.state !== 'done'; }).length;
    var attention = items.filter(function (i) { return i.state === 'attention'; }).length;
    return { items: items, openRequired: open, attention: attention, ready: open === 0 && attention === 0 };
  }

  var api = { homeChecklist: homeChecklist, moveUp: moveUp, moveDown: moveDown, removeAt: removeAt, withAdded: withAdded, indexCatalog: indexCatalog, describe: describe, candidates: candidates,
    profileSave: profileSave, classifyOutcome: classifyOutcome, deviceRows: deviceRows, assignOp: assignOp };
  if (typeof module === 'object' && module.exports) module.exports = api; else root.SgPrefs = api;
})(typeof window === 'undefined' ? this : window);

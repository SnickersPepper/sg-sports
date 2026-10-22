'use strict';
// Setup UI. All user text is rendered with textContent. Secret inputs are write-only: the server never returns
// them and they are cleared after every save attempt. The session is an HttpOnly cookie the page cannot read; only the CSRF token lives in this closure.
(function () {
  var csrf = '', view = null, status = null, progress = null, catalog = null, favDraft = null, favProfile = '', favRevision = -1, dp = { integrations: [], switches: [] }, plan = null;
  var P = window.SgPrefs;
  var $ = function (id) { return document.getElementById(id); };
  var CLASSES = {
    Input: ['invalid_base_url', 'invalid_api_key', 'unsupported_auth_variant', 'switch_not_ready', 'active_route_switch_required', 'destructive_change_paused_while_switch_staged', 'rollback_blocked_by_later_edit', 'invalid_url', 'invalid_file_reference', 'invalid_xtream_credential', 'wrong_type', 'unknown_field', 'name_required', 'string_or_null_required', 'unsupported_format', 'unsupported_location_type', 'unsupported_kind', 'integer_1_to_100_required', 'url_or_credential_not_allowed_in_plain_settings', 'location_not_used_for_xtream'],
    Reference: ['unknown_dispatcharr_integration', 'explicit_account_mapping_required', 'source_account_not_covered_by_route', 'account_in_multiple_active_routes', 'source_in_multiple_active_routes', 'unknown_account', 'unknown_source', 'unknown_profile', 'unknown_guide_source', 'invalid_id', 'id_in_use', 'duplicate_id', 'not_found', 'read_only_legacy_import'],
    Secret: ['missing_secret', 'unexpected_secret'],
    Compatibility: ['incompatible_schema_newer', 'invalid_schema_version', 'unknown_section']
  };
  var ADVICE = {
    invalid_url: 'Use a full http:// or https:// address.', invalid_file_reference: 'Use a plain file name (letters, digits, dot, dash) ending in .m3u/.m3u8 or .xml/.xml.gz; paths are not accepted.',
invalid_xtream_credential: 'Provide server URL, username and password together.',
    invalid_base_url: 'Use the http:// or https:// address, without a user name, query or fragment.', base_url_required_for_url_mode: 'Enter the base address of your logo server, or choose another source.', only_one_artwork_source: 'Only one team logo source can be configured.', invalid_api_key: 'Use the dedicated API key (8 to 256 letters, digits or . _ ~ + / = -).',
    account_in_multiple_active_routes: 'Only one route may be active for an account; use Preview switch instead.', explicit_account_mapping_required: 'Name the account this route uses; no allowance is copied automatically.',
    active_route_switch_required: 'Switch to another route first, then disconnect.', switch_not_ready: 'A managed session is active or the result would be invalid. Try again when idle.',
    destructive_change_paused_while_switch_staged: 'Apply or cancel the staged switch first.', rollback_blocked_by_later_edit: 'The routes were edited after the switch, so it cannot be rolled back automatically.',
    unknown_account: 'Map the source to an account that exists.', missing_secret: 'Supply this secret value in the restore form.', unexpected_secret: 'Remove this value; the export does not need it.',
    incompatible_schema_newer: 'This export comes from a newer SG Sports. Upgrade this server first.', invalid_schema_version: 'The file is not a SG Sports settings export.',
    unknown_section: 'The file contains data this version does not understand.', read_only_legacy_import: 'Imported legacy records are read-only.'
  };
  function classify(code) { for (var k in CLASSES) { if (CLASSES[k].indexOf(code) >= 0) return k; } return 'Other'; }
  function say(text) { $('status').textContent = text; }
  function showErrors(list) {
    var ul = $('errors'); ul.textContent = '';
    (list || []).forEach(function (e) {
      var li = document.createElement('li');
      li.textContent = classify(e.error) + ' problem at ' + (e.path || 'request') + ': ' + e.error.replace(/_/g, ' ') + '. ' + (ADVICE[e.error] || '');
      ul.appendChild(li);
    });
  }
  function api(method, path, body) {
    var opts = { method: method, headers: { 'X-SG-CSRF': csrf }, credentials: 'same-origin', cache: 'no-store' };
    if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
    return fetch(path, opts).then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { return { status: r.status, data: d }; }); });
  }
  function load() {
    return Promise.all([api('GET', '/v1/settings'), api('GET', '/v1/settings/setup-status'), api('GET', '/v1/catalog/favorites'), api('GET', '/v1/settings/dispatcharr')]).then(function (res) {
      if (res[0].status === 401 || res[0].status === 403) { showSignedOut('Please sign in again.'); return false; }
      if (res[0].status !== 200) { say('Settings could not be loaded.'); return false; }
      view = res[0].data; status = res[1].data; dp = res[3].status === 200 ? res[3].data : { integrations: [], switches: [] }; catalog = res[2].status === 200 ? res[2].data : { teams: [], leagues: [] }; $('app').hidden = false; render(); return true;
    }, function () { say('The server could not be reached.'); return false; });
  }
  function save(ops, done) {
    showErrors([]);
    return api('POST', '/v1/settings', { revision: view.revision, ops: ops }).then(function (r) {
      if (r.status === 200) { say('Saved.'); return load().then(function () { if (done) done(); return true; }); }
      if (r.status === 409) { say('Someone else changed the settings. Your edit was not saved; the latest values are loaded.'); return load().then(function () { return false; }); }
      if (r.status === 422) { say('Nothing was saved. Fix the items below.'); showErrors(r.data.details); return false; }
      if (r.status === 503) { say('This server cannot use the settings database (' + (r.data.error || 'unsupported') + ').'); return false; }
      say('The request was rejected (' + r.status + ').'); return false;
    });
  }
  function el(tag, text, attrs) { var e = document.createElement(tag); if (text) e.textContent = text; for (var k in (attrs || {})) e.setAttribute(k, attrs[k]); return e; }
  function row(list, label, detail, buttons) {
    var li = el('li'); var box = el('span'); box.appendChild(el('strong', label)); box.appendChild(el('span', ' ' + detail)); li.appendChild(box);
    var b = el('span'); buttons.forEach(function (x) { var btn = el('button', x[0], { type: 'button' }); btn.addEventListener('click', x[1]); b.appendChild(btn); }); li.appendChild(b);
    list.appendChild(li);
  }
  function remove(section, item) { if (window.confirm('Remove "' + item.name + '"? Favorites, profiles and recordings are kept.')) save([{ op: 'delete', section: section, id: item.id }]); }
  var KIND = { iptvboss_export: 'IPTVBoss export', m3u: 'Direct M3U', xtream: 'Xtream-style', external: 'External' };
  function render() {
    var sl = $('source-list'); sl.textContent = '';
    view.sources.forEach(function (s) {
      var d = KIND[s.kind] + (s.location_configured ? ', location stored' : ', no location yet') + (s.read_only ? ', legacy (read-only)' : '');
      var buttons = s.read_only ? [] : [['Edit', function () { editSource(s); }], ['Remove', function () { remove('sources', s); }]];
      if (s.kind !== 'xtream' && !s.read_only) buttons.unshift(['Test connection', function () { assess(s); }]);
      row(sl, s.name, d + (s.middleware === 'dispatcharr' ? ', Dispatcharr output' : ''), buttons);
    });
    var gl = $('guide-list'); gl.textContent = '';
    view.guide_sources.forEach(function (g) {
      row(gl, g.name, 'XMLTV' + (g.format ? ' (' + g.format + ')' : '') + (g.location_configured ? ', location stored' : ', no location yet'),
        [['Test connection', function () { checkGuide(g); }], ['Edit', function () { editGuide(g); }], ['Remove', function () { remove('guide_sources', g); }]]);
    });
    var al = $('account-list'); al.textContent = '';
    view.accounts.forEach(function (a) {
      var users = status.sources.filter(function (s) { return view.sources.some(function (v) { return v.id === s.source_id && [v.account_id].concat(v.account_ids || []).indexOf(a.id) >= 0; }); }).length;
      row(al, a.name, a.max_connections + ' connection(s), shared by ' + users + ' source(s)' + (a.credential_configured ? ', login stored' : ''),
        a.read_only ? [] : [['Edit', function () { editAccount(a); }], ['Remove', function () { remove('accounts', a); }]]);
    });
    fillAccounts(); renderDispatcharr(); renderFavorites(); renderDevices(); loadArtwork(); renderHome(); loadDiagnostics();
    var tb = $('progress').tBodies[0]; tb.textContent = '';
    var words = { done: 'Done', not_started: 'Not started', unresolved: 'Unresolved' };
    status.sources.forEach(function (s) {
      var tr = el('tr'); tr.appendChild(el('th', s.name, { scope: 'row' }));
      ['configured', 'catalog_imported', 'guide_mapped', 'transport_tested', 'game_confirmed'].forEach(function (k) { tr.appendChild(el('td', words[s.states[k]] || s.states[k])); });
      tr.appendChild(el('td', s.budget === 'resolved' ? 'Resolved' : 'Unresolved (no allowance)')); tb.appendChild(tr);
    });
    var un = $('unresolved'); un.hidden = !status.unresolved_source_ids.length;
    un.textContent = status.unresolved_source_ids.length ? status.unresolved_source_ids.length + ' source(s) have no explicit account mapping. They get no connection allowance of their own until you map them.' : '';
    progress = (view.integrations || []).filter(function (i) { return i.id === 'int-setup-progress'; })[0];
    $('skipped').textContent = progress && progress.options && progress.options.skipped ? 'Skipped for now: ' + progress.options.skipped.join(', ') + '. You can edit later.' : '';
  }
  // --- Team logos (SGH-302): configuration is the 'artwork' integration in the settings document; status, sync and cache clearing are /v1/artwork. ---
  var ART_HINTS = {
    espn: 'The backend asks ESPN\'s public metadata for logos when a TV needs one. Those endpoints are undocumented and ESPN grants no redistribution licence; use them only if you accept ESPN\'s terms.',
    url: 'Logos are read from <address>/<key>.png (or .webp/.jpg). The address stays on the backend and is never shown again; leave it blank to keep the stored one.',
    pack: 'Put pack.json (schema_version 1, name, license, attribution, optional entries) and your image files in the artwork/pack folder of the data volume. Only PNG, JPEG and WebP are accepted; SVG is refused.',
    off: 'Nothing is fetched or served. TVs show colour monograms.'
  };
  var artState = null, artTimer = null;
  function artItem() { return ((view && view.integrations) || []).filter(function (i) { return i.kind === 'artwork'; })[0]; }
  function artModeChanged() { var m = $('art-mode').value; $('art-ack-wrap').hidden = !(m === 'espn' && !(artState && artState.configured_mode === 'espn')); $('art-url-wrap').hidden = m !== 'url'; $('art-hint').textContent = ART_HINTS[m] || ''; }
  function when(t) { return t ? new Date(t * 1000).toLocaleString() : 'never'; }
  function renderArtwork() {
    var s = artState, item = artItem(); if (!s) return;
    $('art-mode').value = s.configured_mode; $('art-url').value = ''; artModeChanged();
    if (s.mode === 'url' || s.configured_mode === 'url') $('art-url').placeholder = item && item.base_url_configured ? 'Stored - leave blank to keep' : 'http://my-server/logos';
    var c = s.cache, y = s.sync;
    $('art-state').textContent = (item ? 'Source saved. ' : 'Not saved yet - the default applies. ') + c.entries + ' logo(s) cached (' + Math.round(c.bytes / 1024) + ' KB of ' + Math.round(c.max_bytes / 1048576) + ' MB)' +
      (c.failed ? ', ' + c.failed + ' unavailable' : '') + '. Refresh: ' + (y.state === 'running' ? 'running ' + y.done + ' of ' + y.total : y.state === 'idle' ? 'not run since start' : y.state + ' ' + y.ok + ' ok, ' + y.failed + ' unavailable (' + when(y.finished_at) + ')') + '.';
    var p = s.provider; $('art-terms').textContent = s.mode === 'off' ? '' : p.name + '. ' + p.attribution + ' ' + p.license + (p.terms_url ? ' Terms: ' + p.terms_url : '');
    if (s.pack) $('art-terms').textContent += ' Pack: ' + s.pack.files + ' file(s)' + (s.pack.problems.length ? '; problems: ' + s.pack.problems.join(', ').replace(/_/g, ' ') : '') + '.';
    var ul = $('art-entries'); ul.textContent = '';
    s.entries.slice(0, 40).forEach(function (e) { ul.appendChild(el('li', e.key + ' - ' + (e.ok ? 'cached ' + when(e.retrieved_at) + (e.source_host ? ' from ' + e.source_host : '') : 'unavailable (' + String(e.last_error || 'unknown').replace(/_/g, ' ') + ')'))); });
  }
  function loadArtwork() { return api('GET', '/v1/artwork').then(function (r) { if (r.status === 200) { artState = r.data; renderArtwork(); if (artState.sync.state === 'running') pollArtwork(); } }); }
  function pollArtwork() { if (artTimer) return; artTimer = setTimeout(function () { artTimer = null; loadArtwork(); }, 2000); }
  function post(path, body) { return api('POST', path, body); }
  function isDp(i) { return i.kind === 'dispatcharr'; }
  function assess(s) {
    post('/v1/settings/dispatcharr/assess', { source_id: s.id }).then(function (r) {
      if (r.status !== 200) { say('The playlist check was rejected (' + r.status + ').'); showErrors(r.data.details); return; }
      var b = r.data;
      say(b.status === 'unavailable' ? 'The playlist could not be read (' + (b.reason || 'unavailable') + '). Nothing was changed.' :
        'Playlist read: ' + b.entries + ' entries, delivery profile ' + String(b.delivery_profile).replace(/_/g, ' ') + '. Connection limits are not verified by this check.');
      load();
    });
  }
  function checkGuide(g) {
    post('/v1/settings/dispatcharr/check-guide', { guide_source_id: g.id }).then(function (r) {
      if (r.status !== 200) { say('The guide could not be tested. Save a web address (URL) location for it first.'); showErrors(r.data.details); return; }
      var b = r.data;
      say(b.status === 'ok' ? 'The guide was read: ' + b.channels + ' channels. Nothing was imported.' :
        'The guide could not be used (' + String(b.reason || b.status).replace(/_/g, ' ') + '). The saved settings are unchanged; check the address and format.');
    });
  }
  var DIAG_NOTE = { ok: 'Healthy', unreachable: 'Unreachable' };
  function renderHome() {
    var h = P.homeChecklist(view, status), ol = $('home-list'); ol.textContent = '';
    var words = { done: 'Done', todo: 'To do', attention: 'Needs attention', optional: 'Optional' };
    h.items.forEach(function (i) {
      var li = el('li'), box = el('span'), a = el('a', i.title, { href: i.href });
      box.appendChild(el('strong', '')); box.firstChild.appendChild(a);
      box.appendChild(el('span', ' ' + (i.required ? 'Required' : 'Optional') + ' - ' + words[i.state] + '. ' + i.detail, { class: 'hint' }));
      li.appendChild(box); ol.appendChild(li);
    });
    var banner = $('home-summary');
    banner.textContent = h.ready ? 'Ready. Required steps are complete, and your TV can load this server.' :
      h.openRequired + ' required step(s) left' + (h.attention ? ' and ' + h.attention + ' item(s) need attention' : '') + '. Work through the list below, top to bottom.';
    banner.className = h.ready ? 'ready-banner ok' : 'ready-banner';
    $('server-address').textContent = window.location.origin;
  }
  function loadDiagnostics() {
    return Promise.all([api('GET', '/health'), api('GET', '/v1/ops/diagnostics')]).then(function (res) {
      var h = res[0].data || {}, d = res[1].data || {}, st = d.storage || {}, parts = [];
      parts.push('Release channel: ' + (h.release_channel || d.release_channel || 'unknown') + '.');
      if (d.settings) parts.push('Settings schema ' + d.settings.schema_version + ', revision ' + d.settings.revision + '.');
      parts.push(res[0].status === 200 ? 'This server is healthy.' : 'The health check did not answer.');
      parts.push('Updates are applied by pulling the new image and restarting Docker Compose; your data volume and paired TVs are kept. See the operator guide.');
      $('diag-state').textContent = parts.join(' ');
      $('storage-state').textContent = d.dvr ? 'Recording is enabled. Recordings by state: ' + JSON.stringify(d.dvr.recordings_by_state || {}) + '; keeping the last ' + d.dvr.retention_limit + ' per team.' :
        'Recording is not enabled. Live viewing works without it.';
      if (res[1].status !== 200) $('storage-state').textContent += ' (Detailed storage status could not be read.)';
    }, function () { $('diag-state').textContent = 'The server could not be reached.'; });
  }
  $('diag-refresh').addEventListener('click', loadDiagnostics);
  function probe(i) {
    post('/v1/settings/dispatcharr/probe', { integration_id: i.id }).then(function (r) {
      say(r.status === 200 ? 'Check finished: ' + String(r.data.status).replace(/_/g, ' ') + (r.data.reason ? ' (' + r.data.reason.replace(/_/g, ' ') + ')' : '') + '.' : 'The check was rejected (' + r.status + ').');
      load();
    });
  }
  function disconnect(i) {
    if (!window.confirm('Disconnect "' + i.name + '"? Sources, accounts, favorites, pairing and recordings are kept; its mappings and stored key are removed.')) return;
    post('/v1/settings/dispatcharr/disconnect', { revision: view.revision, integration_id: i.id }).then(function (r) {
      if (r.status === 200) { say('Disconnected.'); return load(); }
      if (r.status === 409) { say('Someone else changed the settings. Nothing was removed.'); return load(); }
      say('Nothing was removed.'); showErrors(r.data.details);
    });
  }
  function showPlan(p) {
    plan = p; $('sw-plan').hidden = !p; var ul = $('sw-detail'); ul.textContent = ''; if (!p) return;
    p.flips.forEach(function (f) { ul.appendChild(el('li', (f.enabled ? 'Activate ' : 'Deactivate ') + routeName(f.route_id))); });
    (p.warnings || []).forEach(function (w) { ul.appendChild(el('li', 'Note: ' + String(w.code).replace(/_/g, ' ') + (w.detail ? ' - ' + w.detail : ''))); });
    (p.blockers || []).forEach(function (b) { ul.appendChild(el('li', 'Blocked: ' + String(b.code).replace(/_/g, ' ') + (b.account_id ? ' (' + b.account_id + ')' : ''))); });
    (p.errors || []).forEach(function (e) { ul.appendChild(el('li', 'Invalid: ' + e.error.replace(/_/g, ' '))); });
    $('sw-commit').disabled = !p.ready;
  }
  function routeName(id) { var r = view.routes.filter(function (x) { return x.id === id; })[0]; return r ? r.name : id; }
  function renderDispatcharr() {
    var list = $('dp-list'); list.textContent = ''; var health = {};
    dp.integrations.forEach(function (i) { health[i.id] = i.health || {}; });
    var items = (view.integrations || []).filter(isDp);
    $('dp-empty').hidden = items.length > 0;
    items.forEach(function (i) {
      var h = health[i.id] || { status: 'never_checked', stale: true };
      var d = 'Status: ' + String(h.status).replace(/_/g, ' ') + (h.stale && h.age_seconds != null ? ' (last checked ' + h.age_seconds + ' s ago)' : '') + (h.version ? ', version ' + h.version : '') +
        (h.pin_status === 'unpinned' ? ', compatibility not pinned' : '') + (i.api_key_configured ? ', key stored' : ', no key') +
        '. Connection limits are enforced by SG Sports, not assured by Dispatcharr (its container test has not been run); a healthy connection is not proof of the right game.' +
        (i.delivery && !i.delivery.available ? ' Playback through it is refused until it is healthy; SG Sports never falls back to the provider directly.' : '');
      row(list, i.name, d, [['Check now', function () { probe(i); }], ['Edit', function () { editDp(i); }], ['Disconnect', function () { disconnect(i); }]]);
    });
    var rl = $('route-list'); rl.textContent = ''; $('route-empty').hidden = view.routes.length > 0;
    view.routes.forEach(function (r) {
      row(rl, r.name, (r.kind === 'dispatcharr' ? 'Dispatcharr' : 'Direct') + (r.enabled === false ? ', inactive' : ', active') + ', accounts: ' + (r.account_ids || []).length,
        r.enabled === false ? [['Preview switch', function () { post('/v1/settings/switch/preview', { activate_route_id: r.id }).then(function (x) { if (x.status === 200) showPlan(x.data); else { say('The preview was rejected.'); showErrors(x.data.details); } }); }]] : []);
    });
    var hl = $('sw-history'); hl.textContent = '';
    (dp.switches || []).filter(function (s) { return s.state === 'committed'; }).forEach(function (s) {
      row(hl, 'Switch to ' + routeName(s.activate_route_id), 'applied', [['Roll back', function () {
        post('/v1/settings/switch/rollback', { plan_id: s.plan_id }).then(function (x) { if (x.status === 200) { say('Rolled back.'); load(); } else { say('Not rolled back.'); showErrors(x.data.details); } });
      }]]);
    });
  }
  function editDp(i) {
    $('dp-form').reset(); $('dp-id').value = i ? i.id : ''; if (i) { $('dp-name').value = i.name; $('dp-auth').value = i.auth_variant || 'x-api-key'; $('dp-url').placeholder = i.base_url_configured ? 'Stored - leave blank to keep' : ''; $('dp-key').placeholder = i.api_key_configured ? 'Stored - leave blank to keep' : ''; }
    open('dp-form');
  }
  function savedFavorites(profile) { var f = profile && profile.preferences && profile.preferences.favorites; return Array.isArray(f) ? f : []; }
  function currentProfile() { return view.profiles.filter(function (p) { return p.id === favProfile; })[0] || view.profiles[0]; }
  function renderFavorites() {
    var sel = $('f-profile'); sel.textContent = '';
    view.profiles.forEach(function (p) { sel.appendChild(el('option', p.name, { value: p.id })); });
    var profile = currentProfile(); if (!profile) return;
    if (favProfile !== profile.id || favRevision !== view.revision || !favDraft) { favProfile = profile.id; favRevision = view.revision; favDraft = savedFavorites(profile).slice(); }
    sel.value = profile.id;
    var by = P.indexCatalog(catalog), list = $('fav-list'); list.textContent = '';
    favDraft.forEach(function (id, i) {
      var d = P.describe(by, id), li = el('li'), box = el('span'); box.appendChild(el('strong', (i + 1) + '. ' + d.label)); if (d.note) box.appendChild(el('span', ' ' + d.note, { class: 'hint' })); li.appendChild(box);
      var b = el('span');
      [['Earlier', function () { favDraft = P.moveUp(favDraft, i); renderFavorites(); }, i === 0], ['Later', function () { favDraft = P.moveDown(favDraft, i); renderFavorites(); }, i === favDraft.length - 1],
        ['Remove', function () { favDraft = P.removeAt(favDraft, i); renderFavorites(); }, false]].forEach(function (x) {
        var btn = el('button', x[0], { type: 'button' }); btn.disabled = x[2]; btn.addEventListener('click', x[1]); b.appendChild(btn);
      });
      li.appendChild(b); list.appendChild(li);
    });
    $('fav-empty').hidden = favDraft.length > 0;
    var add = $('f-add'); add.textContent = '';
    P.candidates(catalog, favDraft).forEach(function (t) { var d = P.describe(by, t.id); add.appendChild(el('option', d.label, { value: t.id })); });
    var dirty = JSON.stringify(favDraft) !== JSON.stringify(savedFavorites(profile));
    $('f-save').disabled = !dirty; $('f-discard').disabled = !dirty; $('f-add-btn').disabled = !add.options.length;
    $('f-note').textContent = dirty ? 'Unsaved changes.' : 'Everything shown is saved.';
  }
  function deviceOutcome(r, done) {
    var o = P.classifyOutcome(r.status, r.data);
    if (r.status === 200) { say(done); return load().then(function () { return true; }); }
    say(o.message); showErrors([]);
    return (r.status === 409 ? load() : Promise.resolve()).then(function () { return false; });
  }
  function renderDevices() {
    var rows = P.deviceRows(view), list = $('device-list'); list.textContent = '';
    $('device-empty').hidden = rows.length > 0;
    rows.forEach(function (d) {
      var li = el('li'), box = el('span'); box.appendChild(el('strong', d.name)); box.appendChild(el('span', ' ' + d.id + ' · ' + d.state + ' · ' + d.profileName, { class: 'hint' })); li.appendChild(box);
      var b = el('span'), pick = el('select', '', { 'aria-label': 'Profile for ' + d.name });
      pick.appendChild(el('option', 'No profile', { value: '' }));
      view.profiles.forEach(function (p) { pick.appendChild(el('option', p.name, { value: p.id })); });
      pick.value = d.profileId; b.appendChild(pick);
      var assign = el('button', 'Assign', { type: 'button' });
      assign.addEventListener('click', function () { api('POST', '/v1/settings', { revision: view.revision, ops: [P.assignOp(d.id, pick.value)] }).then(function (r) { return deviceOutcome(r, 'Assigned ' + d.name + '.'); }); });
      b.appendChild(assign);
      if (d.canRevoke) {
        var rev = el('button', 'Revoke', { type: 'button' });
        rev.addEventListener('click', function () {
          if (!window.confirm('Revoke "' + d.name + '"? It is signed out and must be paired again with a new code.')) return;
          api('POST', '/v1/settings/devices/' + encodeURIComponent(d.id) + '/revoke', {}).then(function (r) { return deviceOutcome(r, 'Revoked ' + d.name + '.'); });
        });
        b.appendChild(rev);
      }
      li.appendChild(b); list.appendChild(li);
    });
  }
  function fillAccounts() {
    ['s-account', 's-accounts'].forEach(function (id) {
      var sel = $(id); sel.textContent = '';
      if (id === 's-account') sel.appendChild(el('option', 'Not mapped yet (unresolved)', { value: '' }));
      view.accounts.forEach(function (a) { sel.appendChild(el('option', a.name, { value: a.id })); });
    });
  }
  function open(form, title) { ['source-form', 'guide-form', 'account-form', 'dp-form'].forEach(function (f) { $(f).hidden = f !== form; }); if (title) $('source-title').textContent = title; $(form).querySelector('input:not([type=hidden])').focus(); }
  function sourceKindChanged() { var x = $('s-kind').value === 'xtream'; $('s-loc').hidden = x; $('s-merged').closest('label').hidden = x; $('s-more-wrap').hidden = x || !$('s-merged').checked; }
  function newSource(kind) { $('source-form').reset(); $('s-id').value = ''; $('s-kind').value = kind; fillAccounts(); sourceKindChanged(); open('source-form', 'New source'); }
  function editSource(s) {
    $('source-form').reset(); fillAccounts(); $('s-id').value = s.id; $('s-name').value = s.name; $('s-kind').value = s.kind;
    $('s-loctype').value = s.location_type || 'url'; $('s-format').value = s.format || ''; $('s-account').value = s.account_id || ''; $('s-merged').checked = !!s.merged;
    Array.prototype.forEach.call($('s-accounts').options, function (o) { o.selected = (s.account_ids || []).indexOf(o.value) >= 0; });
    $('s-dispatcharr').checked = s.middleware === 'dispatcharr'; $('s-location').placeholder = s.location_configured ? 'Stored - leave blank to keep' : ''; sourceKindChanged(); open('source-form', 'Edit source');
  }
  function editGuide(g) { $('guide-form').reset(); $('g-id').value = g ? g.id : ''; if (g) { $('g-name').value = g.name; $('g-loctype').value = g.location_type || 'url'; $('g-format').value = g.format || ''; $('g-location').placeholder = g.location_configured ? 'Stored - leave blank to keep' : ''; } open('guide-form'); }
  function editAccount(a) { $('account-form').reset(); $('a-id').value = a ? a.id : ''; if (a) { $('a-name').value = a.name; $('a-max').value = a.max_connections; } open('account-form'); }
  function clearSecrets() { ['s-location', 'g-location', 'a-host', 'a-user', 'a-pass', 'r-secrets', 'dp-url', 'dp-key', 'si-pass', 'mg-token', 'mg-pass', 'mg-pass2', 'ac-current', 'ac-new', 'ac-new2'].forEach(function (id) { $(id).value = ''; }); }
  function submit(form, fn) { $(form).addEventListener('submit', function (ev) { ev.preventDefault(); var p = fn(); if (p) p.then(clearSecrets, clearSecrets); }); }

  $('pair-tv').addEventListener('click', function () {
    api('POST', '/screen-pair', {}).then(function (r) { $('pair-code').textContent = r.status === 200 ? 'Enter ' + r.data.code + ' on the TV within 5 minutes.' : 'A pairing code could not be created.'; });
  });
  function focusHeading(id) { var h = $(id); if (h) { h.focus(); } }
  function showSignedOut(message, mode) {
    csrf = ''; $('app').hidden = true; $('account').hidden = true; $('nav').hidden = true;
    $('migrate').hidden = mode !== 'migrate'; $('signin').hidden = mode === 'migrate';
    if (message) say(message);
  }
  function enter(state) {
    csrf = state.csrf; $('nav').hidden = state.must_change; $('signin').hidden = true; $('migrate').hidden = true; $('account').hidden = false;
    $('whoami').textContent = state.username; $('force-note').hidden = !state.must_change;
    $('ac-user').value = ''; $('ac-current').value = ''; $('ac-new').value = ''; $('ac-new2').value = '';
    if (state.must_change) { $('app').hidden = true; say('Choose a new password to continue.'); focusHeading('account-h'); return Promise.resolve(false); }
    return load();
  }
  var AUTH_MESSAGES = { invalid_credentials: 'The username or password was not accepted.', rate_limited: 'Too many attempts. Wait a moment and try again.', password_length: 'Use 10 to 256 characters.', password_too_weak: 'Choose a password that is not a common default or your username.', password_unchanged: 'The new password must differ from the current one.', current_password_incorrect: 'The current password was not accepted.', invalid_username: 'Use 3 to 64 letters, digits, dot, dash, underscore or @.', password_change_required: 'Choose a new password first.', nothing_to_change: 'Enter a new password or username.', csrf_rejected: 'Reload the page and try again.', auth_state_invalid: 'The administrator record on the server is damaged. Run the recovery command on the host (see the operator guide).' };
  function authFail(r) { say(AUTH_MESSAGES[r.data && r.data.error] || 'The request was not accepted (' + r.status + ').'); }
  fetch('/v1/auth/session', { credentials: 'same-origin', cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (d) {
    if (d && d.authenticated) { enter(d); } else if (d && d.mode === 'migrate') { showSignedOut('', 'migrate'); }
  }, function () {});
  submit('signin-form', function () {
    var body = { username: $('si-user').value, password: $('si-pass').value };
    $('si-pass').value = '';
    return api('POST', '/v1/auth/login', body).then(function (r) {
      if (r.status === 200) { return enter(r.data).then(function (ok) { if (ok) say('Signed in.'); }); }
      if (r.status === 409) { showSignedOut('This server still uses an administrator token. Move to a username and password below.', 'migrate'); focusHeading('migrate-h'); return; }
      authFail(r);
    });
  });
  submit('migrate-form', function () {
    if ($('mg-pass').value !== $('mg-pass2').value) { say('The two passwords differ.'); return null; }
    var body = { token: $('mg-token').value, username: $('mg-user').value, password: $('mg-pass').value };
    return api('POST', '/v1/auth/migrate', body).then(function (r) {
      if (r.status === 200) { return enter(r.data).then(function () { say('Done. The old token no longer works.'); }); }
      if (r.status === 401) { say('The token was not accepted.'); return; }
      authFail(r);
    });
  });
  submit('credentials-form', function () {
    var body = { current_password: $('ac-current').value };
    if ($('ac-new').value || $('ac-new2').value) {
      if ($('ac-new').value !== $('ac-new2').value) { say('The two new passwords differ.'); return null; }
      body.new_password = $('ac-new').value;
    }
    if ($('ac-user').value.trim()) body.new_username = $('ac-user').value.trim();
    return api('POST', '/v1/auth/credentials', body).then(function (r) {
      if (r.status === 200) { return enter(r.data).then(function () { say('Saved.'); }); }
      authFail(r);
    });
  });
  $('signout').addEventListener('click', function () {
    api('POST', '/v1/auth/logout', {}).then(function () { $('si-user').value = ''; showSignedOut('Signed out.'); focusHeading('signin-h'); });
  });
  submit('source-form', function () {
    var item = { name: $('s-name').value.trim(), kind: $('s-kind').value, account_id: $('s-account').value || null, merged: $('s-merged').checked,
      account_ids: Array.prototype.filter.call($('s-accounts').options, function (o) { return o.selected; }).map(function (o) { return o.value; }) };
    item.middleware = $('s-dispatcharr').checked && $('s-kind').value !== 'xtream' ? 'dispatcharr' : null;
    if (item.kind !== 'xtream') { item.location_type = $('s-loctype').value; item.format = $('s-format').value || null; if ($('s-location').value) item.location = $('s-location').value; }
    else { item.merged = false; item.account_ids = []; }
    if ($('s-id').value) item.id = $('s-id').value;
    return save([{ op: 'upsert', section: 'sources', item: item }], function () { $('source-form').hidden = true; });
  });
  submit('guide-form', function () {
    var item = { name: $('g-name').value.trim(), kind: 'xmltv', location_type: $('g-loctype').value, format: $('g-format').value || null };
    if ($('g-location').value) item.location = $('g-location').value; if ($('g-id').value) item.id = $('g-id').value;
    return save([{ op: 'upsert', section: 'guide_sources', item: item }], function () { $('guide-form').hidden = true; });
  });
  submit('account-form', function () {
    var item = { name: $('a-name').value.trim(), max_connections: parseInt($('a-max').value, 10) }, host = $('a-host').value, user = $('a-user').value, pass = $('a-pass').value;
    if (host || user || pass) item.credential = JSON.stringify({ host: host, username: user, password: pass });
    if ($('a-id').value) item.id = $('a-id').value;
    return save([{ op: 'upsert', section: 'accounts', item: item }], function () { $('account-form').hidden = true; });
  });
  submit('dp-form', function () {
    var item = { name: $('dp-name').value.trim(), kind: 'dispatcharr', enabled: true, auth_variant: $('dp-auth').value };
    if ($('dp-url').value) item.base_url = $('dp-url').value; if ($('dp-key').value) item.api_key = $('dp-key').value; if ($('dp-id').value) item.id = $('dp-id').value;
    return save([{ op: 'upsert', section: 'integrations', item: item }], function () { $('dp-form').hidden = true; });
  });
  $('dp-new').addEventListener('click', function () { editDp(null); });
  $('dp-std').addEventListener('click', function () { newSource('m3u'); $('s-dispatcharr').checked = true; });
  $('sw-cancel').addEventListener('click', function () { if (!plan) return; post('/v1/settings/switch/cancel', { plan_id: plan.plan_id }).then(function () { showPlan(null); say('Switch cancelled.'); }); });
  $('sw-commit').addEventListener('click', function () {
    if (!plan) return;
    post('/v1/settings/switch/commit', { plan_id: plan.plan_id, revision: view.revision }).then(function (x) {
      if (x.status === 200) { showPlan(null); say('Switch applied. You can roll it back from the history below.'); return load(); }
      if (x.status === 409) { showPlan(null); say('The settings changed since the preview. Nothing was switched; preview again.'); return load(); }
      say('Nothing was switched.'); showErrors(x.data.details);
    });
  });
  $('f-profile').addEventListener('change', function () { favProfile = $('f-profile').value; favDraft = null; renderFavorites(); });
  $('f-add-btn').addEventListener('click', function () { if ($('f-add').value) { favDraft = P.withAdded(favDraft, $('f-add').value); renderFavorites(); } });
  $('f-discard').addEventListener('click', function () { favDraft = null; renderFavorites(); });
  $('f-save').addEventListener('click', function () {
    api('POST', '/v1/settings', { revision: view.revision, ops: [P.profileSave(currentProfile(), favDraft)] }).then(function (r) {
      if (r.status === 422) { say('Nothing was saved. Fix the items below.'); showErrors(r.data.details); return false; }
      return deviceOutcome(r, 'Favorites saved.');
    });
  });
  $('art-mode').addEventListener('change', artModeChanged);
  submit('art-form', function () {
    var mode = $('art-mode').value;
    if (mode === 'espn' && !(artState && artState.configured_mode === 'espn') && !$('art-ack').checked) { say('Confirm that you accept ESPN\'s terms to use ESPN logos, or choose another source.'); return null; }
    var item = { id: 'int-artwork', name: 'Team artwork', kind: 'artwork', enabled: true, options: { mode: mode } };
    if (mode === 'url' && $('art-url').value) item.base_url = $('art-url').value;
    return save([{ op: 'upsert', section: 'integrations', item: item }]);
  });
  $('art-sync').addEventListener('click', function () {
    post('/v1/artwork/sync').then(function (r) { say(r.status === 202 ? 'Fetching logos in the background.' : r.status === 409 ? 'A refresh is already running.' : 'The refresh was rejected (' + r.status + ').'); loadArtwork(); });
  });
  $('art-clear').addEventListener('click', function () {
    post('/v1/artwork/clear').then(function (r) { say(r.status === 200 ? 'Cached logos cleared. They are fetched again when a TV asks or when you refresh.' : 'Clearing was rejected (' + r.status + ').'); loadArtwork(); });
  });
  $('skip').addEventListener('click', function () {
    var item = { name: 'Setup progress', kind: 'setup_progress', enabled: true, options: { skipped: ['sources', 'guide', 'accounts'] } };
    item.id = 'int-setup-progress'; save([{ op: 'upsert', section: 'integrations', item: item }]);
  });
  Array.prototype.forEach.call(document.querySelectorAll('[data-new-source]'), function (b) { b.addEventListener('click', function () { newSource(b.getAttribute('data-new-source')); }); });
  Array.prototype.forEach.call(document.querySelectorAll('[data-cancel]'), function (b) { b.addEventListener('click', function () { b.closest('form').hidden = true; clearSecrets(); }); });
  $('new-guide').addEventListener('click', function () { editGuide(null); });
  $('new-account').addEventListener('click', function () { editAccount(null); });
  $('s-kind').addEventListener('change', sourceKindChanged);
  $('s-merged').addEventListener('change', sourceKindChanged);
  $('export').addEventListener('click', function () {
    api('GET', '/v1/settings/export').then(function (r) {
      if (r.status !== 200) return say('Export failed.');
      var a = el('a', '', { href: URL.createObjectURL(new Blob([JSON.stringify(r.data, null, 2)], { type: 'application/json' })), download: 'sgsports-settings-export.json' });
      document.body.appendChild(a); a.click(); a.remove(); say('Export downloaded. Keep your secret values separately.');
    });
  });
  var exported = null;
  $('r-file').addEventListener('change', function () {
    var f = $('r-file').files[0]; exported = null; $('r-need').textContent = ''; if (!f) return;
    f.text().then(function (t) {
      try { exported = JSON.parse(t); } catch (e) { return showErrors([{ path: 'file', error: 'invalid_schema_version' }]); }
      var need = (exported.secrets_required || []).map(function (n) { return n.section + '/' + n.id + '/' + n.field; });
      $('r-need').textContent = need.length ? 'Secret values required for: ' + need.join(', ') : 'No secret values required.';
    });
  });
  submit('restore-form', function () {
    var secrets = {}; showErrors([]);
    if (!exported) { say('Choose an export file first.'); return null; }
    try { if ($('r-secrets').value.trim()) secrets = JSON.parse($('r-secrets').value); } catch (e) { say('The secret values are not valid JSON. Nothing was restored.'); return null; }
    return api('POST', '/v1/settings/restore', { revision: view.revision, export: exported, secrets: secrets }).then(function (r) {
      if (r.status === 200) { say('Restored.'); return load(); }
      if (r.status === 409) { say('The settings changed since you loaded this page. Nothing was restored; reload values and retry.'); return load(); }
      if (r.status === 422) { say('Nothing was restored. The current configuration is unchanged.'); showErrors(r.data.details); return; }
      say('Restore was rejected (' + r.status + '). The current configuration is unchanged.');
    });
  });
})();

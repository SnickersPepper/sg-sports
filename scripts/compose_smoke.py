#!/usr/bin/env python3
"""Clean-install smoke test over HTTP only (no real provider is ever contacted; all data is synthetic).

  compose_smoke.py setup   BASE STATE.json               first sign-in as admin/admin, forced password change, synthetic configuration, TV pairing, browse
  compose_smoke.py persist BASE STATE.json               after a restart/upgrade: credentials, sessions and configuration retained
  compose_smoke.py restore BASE_A STATE_A BASE_B STATE_B redacted export + separate secrets into an empty instance
  compose_smoke.py recover BASE STATE.json EXEC_CMD      operator recovery inside the container: new password, old sessions signed out, admin/admin reset
  compose_smoke.py migrate BASE EXEC_CMD RESTART_CMD   an upgraded volume that only has a legacy administrator token
  compose_smoke.py rollback BASE STATE.json              change configuration, then roll back to the saved export
  compose_smoke.py artwork-populate BASE STATE.json EXEC_CMD   team logos from a stand-in provider inside the container, then served from cache with it stopped
  compose_smoke.py artwork-persist BASE STATE.json       after a restart: cached logos still served (provider gone), clear + rebuild behaviour

EXEC_CMD is the shell prefix that runs a command in the backend container (e.g. "docker compose exec -T sgsports").
STATE.json holds the throwaway administrator password and TV token of the disposable test instance; delete it with the instance.
"""
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

M3U = 'https://provider-a.example.invalid/get.php?username=demo&password=synthetic-m3u'
XMLTV = 'https://provider-a.example.invalid/xmltv.php?username=demo&password=synthetic-xml'
CRED = json.dumps({'host': 'https://xc.example.invalid', 'username': 'demo-user', 'password': 'synthetic-xc'})


class Session:
    """An administrator browser session: the HttpOnly cookie plus the CSRF token the login response returned."""

    def __init__(self, cookie, csrf):
        self.cookie, self.csrf = cookie, csrf


def call(base, method, path, body=None, token=None, raw=False):
    """token: a Session (administrator) or a plain string (sent as a Bearer token, e.g. a paired TV or a deliberately wrong one)."""
    data = json.dumps(body).encode() if body is not None else (b'{}' if method == 'POST' else None)
    request = urllib.request.Request(base.rstrip('/') + path, data=data, method=method)
    if data is not None:
        request.add_header('Content-Type', 'application/json')
    if isinstance(token, Session):
        request.add_header('Cookie', token.cookie)
        if method == 'POST':
            request.add_header('X-SG-CSRF', token.csrf)
    elif token:
        request.add_header('Authorization', 'Bearer ' + token)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = response.read()
            parsed = json.loads(payload) if payload[:1] in (b'{', b'[') else payload.decode(errors='replace')
            return (response.status, parsed, response.headers) if raw else (response.status, parsed)
    except urllib.error.HTTPError as error:
        payload = error.read()
        try:
            parsed = json.loads(payload)
        except ValueError:
            parsed = payload.decode(errors='replace')
        return (error.code, parsed, error.headers) if raw else (error.code, parsed)


def login(base, username, password):
    """(status, body, Session or None)."""
    status, body, headers = call(base, 'POST', '/v1/auth/login', {'username': username, 'password': password}, raw=True)
    cookie = headers.get('Set-Cookie', '')
    return status, body, (Session(cookie.split(';')[0], body['csrf']) if status == 200 else None)


def admin_of(base, state):
    status, body, session = login(base, state['username'], state['password'])
    check('administrator signs in with the stored password', status == 200 and not body['must_change'], body)
    return session


CHECKS = []


def check(name, condition, detail=''):
    CHECKS.append((name, bool(condition)))
    print(('PASS ' if condition else 'FAIL ') + name + ((' :: ' + str(detail)[:200]) if not condition and detail else ''))
    if not condition:
        raise SystemExit(1)


def up(section, **item):
    return {'op': 'upsert', 'section': section, 'item': item}


def configure(base, token):
    _, view = call(base, 'GET', '/v1/settings', token=token)
    status, view = call(base, 'POST', '/v1/settings', {'revision': view['revision'], 'ops': [
        up('accounts', id='acct-main', name='Main subscription', max_connections=2, credential=CRED),
        up('sources', id='src-direct', name='Direct M3U', kind='m3u', location_type='url', format='m3u', location=M3U, account_id='acct-main'),
        up('sources', id='src-xc', name='Xtream-style', kind='xtream', account_id='acct-main'),
        up('guide_sources', id='guide-main', name='Guide', kind='xmltv', location_type='url', format='xml.gz', location=XMLTV)]}, token=token)
    check('synthetic configuration accepted', status == 200, view)
    return view


def load(path):
    return json.load(open(path))


NEW_PASSWORD = 'smoke-test-password-1'


def setup(base, state_path):
    check('health answers without any integration', call(base, 'GET', '/health')[0] == 200)
    check('a fresh instance asks for sign-in', call(base, 'GET', '/v1/auth/session')[1] == {'authenticated': False, 'mode': 'login'})
    check('unauthenticated administrator API refused', call(base, 'GET', '/v1/settings')[0] == 401)
    check('unauthenticated TV pairing refused', call(base, 'POST', '/screen-pair')[0] == 401)
    check('the old claim route is gone', call(base, 'POST', '/v1/claim', {'code': 'x'})[0] in (401, 404))
    check('wrong password refused with a generic message', call(base, 'POST', '/v1/auth/login', {'username': 'admin', 'password': 'nope-nope-nope'}) == (401, {'error': 'invalid_credentials'}))
    status, body, headers = call(base, 'POST', '/v1/auth/login', {'username': 'admin', 'password': 'admin'}, raw=True)
    check('the fresh-install default signs in once', status == 200 and body['must_change'] is True, body)
    cookie = headers.get('Set-Cookie', '')
    check('the session cookie is HttpOnly and SameSite', 'HttpOnly' in cookie and 'SameSite=Strict' in cookie, cookie)
    forced = Session(cookie.split(';')[0], body['csrf'])
    for method, path in (('GET', '/v1/settings'), ('GET', '/v1/settings/export'), ('GET', '/v1/ops/backup'), ('POST', '/screen-pair'), ('POST', '/v1/settings')):
        status = call(base, method, path, token=forced)[0]
        check('before the password change %s %s is refused' % (method, path), status in (401, 403), status)
    check('a wrong current password cannot change it', call(base, 'POST', '/v1/auth/credentials', {'current_password': 'x', 'new_password': NEW_PASSWORD}, token=forced)[0] == 422)
    status, body, headers = call(base, 'POST', '/v1/auth/credentials', {'current_password': 'admin', 'new_password': NEW_PASSWORD}, token=forced, raw=True)
    check('the forced change succeeds', status == 200 and body['must_change'] is False, body)
    check('the default password no longer works', call(base, 'POST', '/v1/auth/login', {'username': 'admin', 'password': 'admin'})[0] == 401)
    check('the old forced-change session is gone', call(base, 'GET', '/v1/settings', token=forced)[0] == 401)
    state = {'username': 'admin', 'password': NEW_PASSWORD}
    token = admin_of(base, state)
    check('a write without the CSRF token is refused', call(base, 'POST', '/v1/settings', {'revision': 1, 'ops': []}, token=Session(token.cookie, 'wrong'))[0] == 403)
    check('the root address serves the administration console', 'SG Sports administration' in str(call(base, 'GET', '/')[1]))
    check('/setup still reaches the console through its redirect', 'SG Sports administration' in str(call(base, 'GET', '/setup')[1]))
    check('wrong bearer token refused', call(base, 'GET', '/v1/settings', token='wrong')[0] == 401)
    view = configure(base, token)
    check('secrets are not echoed', all(s not in json.dumps(view) for s in ('synthetic-m3u', 'synthetic-xml', 'synthetic-xc', 'demo-user')))
    status, pairing = call(base, 'POST', '/screen-pair', token=token)
    check('administrator creates a TV pairing code', status == 200 and len(pairing.get('code', '')) == 6, pairing)
    status, paired = call(base, 'POST', '/tv/pair', {'code': pairing['code']})
    check('TV pairs with the code', status == 200 and paired.get('token'), paired)
    tv = paired['token']
    check('paired TV reads the catalog', call(base, 'GET', '/v1/tv/catalog', token=tv)[0] == 200)
    check('paired TV reads synthetic guide/team data', call(base, 'GET', '/tv/home', token=tv)[0] == 200)
    check('paired TV reads sports guide plumbing', call(base, 'GET', '/tv/sports', token=tv)[0] == 200)
    check('an administrator session cannot act as a TV', call(base, 'GET', '/v1/tv/catalog', token=token)[0] == 401)
    check('a TV token cannot act as the administrator', call(base, 'GET', '/v1/settings', token=tv)[0] == 401)
    # add / edit / disable / remove without any rebuild
    _, view = call(base, 'GET', '/v1/settings', token=token)
    ids = {s['id'] for s in view['sources']}
    check('sources present', {'src-direct', 'src-xc'} <= ids)
    status, view = call(base, 'POST', '/v1/settings', {'revision': view['revision'], 'ops': [up('sources', id='src-direct', name='Direct M3U (renamed)')]}, token=token)
    check('edit keeps the stored location when left blank', status == 200 and any(s['id'] == 'src-direct' and s['location_configured'] for s in view['sources']), view)
    status, view = call(base, 'POST', '/v1/settings', {'revision': view['revision'], 'ops': [up('sources', id='src-direct', enabled=False)]}, token=token)
    check('disable a source', status == 200 and any(s['id'] == 'src-direct' and s.get('enabled') is False for s in view['sources']), view)
    status, view = call(base, 'POST', '/v1/settings', {'revision': view['revision'], 'ops': [up('sources', id='src-direct', enabled=True)]}, token=token)
    check('re-enable a source', status == 200 and any(s['id'] == 'src-direct' and s.get('enabled') is True for s in view['sources']), view)
    status, view = call(base, 'POST', '/v1/settings', {'revision': view['revision'], 'ops': [{'op': 'delete', 'section': 'sources', 'id': 'src-xc'}]}, token=token)
    check('remove a source', status == 200 and all(s['id'] != 'src-xc' for s in view['sources']), view)
    status, export = call(base, 'GET', '/v1/settings/export', token=token)
    check('redacted export has no secrets', status == 200 and all(s not in json.dumps(export) for s in ('synthetic-m3u', 'synthetic-xml', 'synthetic-xc', 'demo-user')))
    check('export carries no credential material', all(w not in json.dumps(export) for w in (NEW_PASSWORD, 'scrypt', 'verifier')))
    state.update(tv=tv, export=export)
    json.dump(state, open(state_path, 'w'))


def persist(base, state_path):
    state = load(state_path)
    check('health after restart', call(base, 'GET', '/health')[0] == 200)
    status, view = call(base, 'GET', '/v1/settings', token=admin_of(base, state))
    check('administrator password survives restart', status == 200, view)
    check('the default password stays rejected after restart', call(base, 'POST', '/v1/auth/login', {'username': 'admin', 'password': 'admin'})[0] == 401)
    check('configuration survives restart', {s['id'] for s in view['sources']} == {'src-direct'} and view['accounts'])
    check('TV pairing survives restart', call(base, 'GET', '/v1/tv/catalog', token=state['tv'])[0] == 200)


def fresh_admin(base):
    """Sign in to an empty instance with the fresh default, make the forced change, return the Session."""
    status, body, headers = call(base, 'POST', '/v1/auth/login', {'username': 'admin', 'password': 'admin'}, raw=True)
    check('an empty instance signs in with the fresh default', status == 200 and body['must_change'], body)
    forced = Session(headers.get('Set-Cookie').split(';')[0], body['csrf'])
    status, body, headers = call(base, 'POST', '/v1/auth/credentials', {'current_password': 'admin', 'new_password': NEW_PASSWORD}, token=forced, raw=True)
    check('the forced change on an empty instance', status == 200, body)
    return Session(headers.get('Set-Cookie').split(';')[0], body['csrf'])


def restore(base_a, state_a, base_b, state_b):
    a = load(state_a)
    token_b = fresh_admin(base_b)
    _, empty = call(base_b, 'GET', '/v1/settings', token=token_b)
    check('second instance starts with no sources', empty['sources'] == [])
    secrets = {'sources/src-direct/location': M3U, 'accounts/acct-main/credential': CRED, 'guide_sources/guide-main/location': XMLTV}
    status, body = call(base_b, 'POST', '/v1/settings/restore', {'revision': empty['revision'], 'export': a['export'], 'secrets': secrets}, token=token_b)
    check('restore rejects when a required secret is missing', call(base_b, 'POST', '/v1/settings/restore', {'revision': empty['revision'], 'export': a['export'], 'secrets': {}}, token=token_b)[0] == 422)
    check('restore with separately supplied secrets', status == 200, body)
    _, view = call(base_b, 'GET', '/v1/settings', token=token_b)
    check('restored configuration matches', {s['id'] for s in view['sources']} == {'src-direct'} and view['accounts'][0]['credential_configured'])
    json.dump({'username': 'admin', 'password': NEW_PASSWORD}, open(state_b, 'w'))


def rollback(base, state_path):
    state = load(state_path)
    token = admin_of(base, state)
    _, view = call(base, 'GET', '/v1/settings', token=token)
    status, view = call(base, 'POST', '/v1/settings', {'revision': view['revision'], 'ops': [{'op': 'delete', 'section': 'guide_sources', 'id': 'guide-main'}]}, token=token)
    check('a destructive change is applied', status == 200 and not view['guide_sources'], view)
    status, body = call(base, 'POST', '/v1/settings/restore', {'revision': view['revision'], 'export': state['export'], 'secrets': {'sources/src-direct/location': M3U, 'accounts/acct-main/credential': CRED, 'guide_sources/guide-main/location': XMLTV}}, token=token)
    check('rollback to the saved export', status == 200, body)
    _, view = call(base, 'GET', '/v1/settings', token=token)
    check('rolled-back configuration is back', [g['id'] for g in view['guide_sources']] == ['guide-main'])


# A valid 1x1 PNG: the stand-in logo (synthetic; no real artwork is ever used here).
PNG_B64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=='
PROVIDER_KEYS = ('league.football.nfl', 'team.football.men.nfl.kansas-city-chiefs')
PROVIDER_START = ("import base64,os,subprocess;os.makedirs('/tmp/logos',exist_ok=True);"
                  "[open('/tmp/logos/%s.png'%k,'wb').write(base64.b64decode('" + PNG_B64 + "')) for k in " + repr(PROVIDER_KEYS) + "];"
                  "p=subprocess.Popen(['python','-m','http.server','9100','--bind','127.0.0.1','--directory','/tmp/logos'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True);"
                  "open('/tmp/provider.pid','w').write(str(p.pid));import time;time.sleep(1.5)")


def run_in(exec_cmd, *argv):
    import shlex
    return subprocess.run(exec_cmd + ' ' + ' '.join(shlex.quote(a) for a in argv), shell=True, check=True, capture_output=True, text=True).stdout


def run_in_stdin(exec_cmd, text, *argv):
    import shlex
    return subprocess.run(exec_cmd + ' ' + ' '.join(shlex.quote(a) for a in argv), shell=True, check=True, capture_output=True, text=True, input=text)


def recover(base, state_path, exec_cmd):
    state = load(state_path)
    old = admin_of(base, state)
    check('the administrator is signed in before recovery', call(base, 'GET', '/v1/settings', token=old)[0] == 200)
    new_password = 'recovered-smoke-password-2'
    result = run_in_stdin(exec_cmd, new_password + '\n', 'python', 'adminauth.py', 'reset')
    check('the recovery command never prints the password', new_password not in result.stdout + result.stderr, result.stdout)
    check('recovery signs out every earlier session', call(base, 'GET', '/v1/settings', token=old)[0] == 401)
    check('the previous password no longer works', call(base, 'POST', '/v1/auth/login', {'username': 'admin', 'password': state['password']})[0] == 401)
    status, body, session = login(base, 'admin', new_password)
    check('the recovered password signs in without a forced change', status == 200 and not body['must_change'], body)
    check('data is untouched by recovery', call(base, 'GET', '/v1/settings', token=session)[1]['sources'] != [])
    run_in_stdin(exec_cmd, '', 'python', 'adminauth.py', 'reset', '--default')
    check('the old session is signed out by the default reset too', call(base, 'GET', '/v1/settings', token=session)[0] == 401)
    status, body, forced = login(base, 'admin', 'admin')
    check('reset --default restores admin/admin with a forced change', status == 200 and body['must_change'] is True, body)
    check('and the forced session can do nothing else', call(base, 'GET', '/v1/settings', token=forced)[0] == 403)
    status, body = call(base, 'POST', '/v1/auth/credentials', {'current_password': 'admin', 'new_password': state['password']}, token=forced)
    check('the original password is chosen again', status == 200, body)


def legacy_migrate(base, exec_cmd, restart_cmd):
    """An upgraded volume: a legacy administrator-token digest and settings data, but no admin-auth.json."""
    import hashlib
    legacy = 'legacy-smoke-token-value'
    admin = fresh_admin(base)
    configure(base, admin)
    before = sorted(s['id'] for s in call(base, 'GET', '/v1/settings', token=admin)[1]['sources'])
    check('the instance holds configuration before it is reduced to a token-only volume', len(before) == 2, before)
    run_in(exec_cmd, 'python', '-c', "import os,hashlib;d=os.environ.get('SG_DATA','/data');open(d+'/admin_token.sha256','w').write(hashlib.sha256(b'%s').hexdigest());os.remove(d+'/admin-auth.json');os.remove(d+'/admin-sessions.json')" % legacy)
    subprocess.run(restart_cmd, shell=True, check=True, capture_output=True)
    for _ in range(40):
        try:
            if call(base, 'GET', '/health')[0] == 200:
                break
        except (ConnectionError, OSError, urllib.error.URLError):
            pass
        time.sleep(1)
    else:
        check('the upgraded instance becomes healthy after restart', False)
    check('the upgraded instance asks for migration, not admin/admin', call(base, 'GET', '/v1/auth/session')[1] == {'authenticated': False, 'mode': 'migrate'})
    check('admin/admin does not work on an upgraded instance', call(base, 'POST', '/v1/auth/login', {'username': 'admin', 'password': 'admin'})[0] == 409)
    check('the legacy token is not accepted as a request credential', call(base, 'GET', '/v1/settings', token=legacy)[0] == 401)
    check('a wrong token cannot migrate', call(base, 'POST', '/v1/auth/migrate', {'token': 'wrong', 'username': 'owner', 'password': 'migrated-smoke-password-3'})[0] == 401)
    status, body, headers = call(base, 'POST', '/v1/auth/migrate', {'token': legacy, 'username': 'owner', 'password': 'migrated-smoke-password-3'}, raw=True)
    check('the token migrates once to a username and password', status == 200 and not body['must_change'], body)
    session = Session(headers.get('Set-Cookie').split(';')[0], body['csrf'])
    check('configuration survived the migration', sorted(s['id'] for s in call(base, 'GET', '/v1/settings', token=session)[1]['sources']) == before)
    check('the old token no longer authenticates', call(base, 'GET', '/v1/settings', token=legacy)[0] == 401)
    check('the old token cannot migrate again', call(base, 'POST', '/v1/auth/migrate', {'token': legacy, 'username': 'x-owner', 'password': 'migrated-smoke-password-3'})[0] == 409)
    check('the new credentials sign in', login(base, 'owner', 'migrated-smoke-password-3')[0] == 200)


def tv_image(base, token, key):
    headers = ({'Cookie': token.cookie} if isinstance(token, Session)
               else {'Authorization': 'Bearer ' + token})
    request = urllib.request.Request(base.rstrip('/') + '/tv/artwork/' + key, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, response.headers.get('Content-Type'), response.read(), response.headers.get('X-Content-Type-Options')
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get('Content-Type'), error.read(), None


def artwork_populate(base, state_path, exec_cmd):
    state = load(state_path)
    admin = admin_of(base, state)
    status, paired = call(base, 'POST', '/tv/pair', {'code': call(base, 'POST', '/screen-pair', token=admin)[1]['code']})
    check('a TV pairs for the artwork test', status == 200, paired)
    tv = paired['token']
    status, body = call(base, 'GET', '/v1/artwork', token=admin)
    check('a clean install defaults to logos Off (ESPN is an explicit opt-in) and has no cached logos', status == 200 and body['mode'] == 'off' and body['cache']['entries'] == 0, body)
    check('the artwork route needs a paired TV', tv_image(base, 'wrong', 'league.football.nfl')[0] == 401)
    check('an administrator session cannot read TV artwork', tv_image(base, admin, 'league.football.nfl')[0] == 401)
    run_in(exec_cmd, 'python', '-c', PROVIDER_START)
    _, view = call(base, 'GET', '/v1/settings', token=admin)
    item = {'id': 'int-artwork', 'name': 'Team artwork', 'kind': 'artwork', 'enabled': True, 'options': {'mode': 'url'}, 'base_url': 'http://127.0.0.1:9100'}
    status, view = call(base, 'POST', '/v1/settings', {'revision': view['revision'], 'ops': [{'op': 'upsert', 'section': 'integrations', 'item': item}]}, token=admin)
    check('the logo source is configured in settings, no rebuild', status == 200 and any(i['id'] == 'int-artwork' and i['base_url_configured'] for i in view['integrations']), view)
    check('the base address is not echoed', '127.0.0.1:9100' not in json.dumps(view))
    status, export = call(base, 'GET', '/v1/settings/export', token=admin)
    needed = {'%s/%s/%s' % (n['section'], n['id'], n['field']) for n in export['secrets_required']}
    check('export carries the source mode but not the address, and asks for it on restore',
          status == 200 and '127.0.0.1:9100' not in json.dumps(export) and 'integrations/int-artwork/base_url' in needed, export)
    status, content_type, body, nosniff = tv_image(base, tv, 'league.football.nfl')
    check('a paired TV is served a validated image on first request', status == 200 and content_type == 'image/png' and body[:4] == b'\x89PNG' and nosniff == 'nosniff', (status, content_type))
    status, _, body, _ = tv_image(base, tv, 'team.football.men.nfl.green-bay-packers')
    check('a logo the source lacks is a plain 404 (the TV draws a monogram)', status == 404, (status, body))
    status, body = call(base, 'POST', '/v1/artwork/sync', token=admin)
    check('an explicit sync is accepted', status in (202, 409), body)
    for _ in range(60):
        state_now = call(base, 'GET', '/v1/artwork', token=admin)[1]
        if state_now['sync']['state'] != 'running':
            break
        time.sleep(1)
    check('the sync finished and cached the logos the source has', state_now['sync']['state'] == 'done' and state_now['cache']['entries'] >= len(PROVIDER_KEYS), state_now['sync'])
    check('status shows source host and retrieval time but not the address', all(e['source_host'] == '127.0.0.1' and e['retrieved_at'] for e in state_now['entries'] if e['ok'])
          and '127.0.0.1:9100' not in json.dumps(state_now))
    run_in(exec_cmd, 'sh', '-c', 'kill $(cat /tmp/provider.pid)')
    time.sleep(1)
    status, content_type, body, _ = tv_image(base, tv, 'league.football.nfl')
    check('with the provider stopped the cached logo is still served', status == 200 and body[:4] == b'\x89PNG', status)
    check('with the provider stopped an uncached logo is a clean 404, not an error page', tv_image(base, tv, 'team.football.men.nfl.chicago-bears')[0] == 404)
    state['artwork_tv'] = tv
    json.dump(state, open(state_path, 'w'))


def artwork_persist(base, state_path):
    state = load(state_path)
    admin, tv = admin_of(base, state), state['artwork_tv']
    status, content_type, body, _ = tv_image(base, tv, 'league.football.nfl')
    check('after a restart the logo is served from the data volume with the provider gone', status == 200 and body[:4] == b'\x89PNG', status)
    _, view = call(base, 'GET', '/v1/settings', token=admin)
    check('the logo source setting survived the restart', any(i['id'] == 'int-artwork' and i['options'] == {'mode': 'url'} for i in view['integrations']))
    status, body = call(base, 'POST', '/v1/artwork/clear', token=admin)
    check('the cache can be cleared', status == 200 and body['status']['cache']['entries'] == 0, body)
    check('after clearing, with no provider, the logo is a 404 (the cache was the source)', tv_image(base, tv, 'league.football.nfl')[0] == 404)


if __name__ == '__main__':
    commands = {'setup': (setup, 2), 'persist': (persist, 2), 'restore': (restore, 4), 'rollback': (rollback, 2),
                'recover': (recover, 3), 'migrate': (legacy_migrate, 3),
                'artwork-populate': (artwork_populate, 3), 'artwork-persist': (artwork_persist, 2)}
    if len(sys.argv) < 2 or sys.argv[1] not in commands or len(sys.argv) - 2 != commands[sys.argv[1]][1]:
        print(__doc__)
        sys.exit(2)
    commands[sys.argv[1]][0](*sys.argv[2:])
    print('ALL PASSED: %d checks' % len(CHECKS))

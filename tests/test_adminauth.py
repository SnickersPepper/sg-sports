"""SGH-311: username/password administrator login, forced first change, sessions, CSRF, throttling, migration, recovery."""
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
import adminauth
import tv


class AuthCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.data, self.sec = root / 'data', root / 'sec'
        self.sec.mkdir()
        for target, value in (('DATA', self.data), ('SECRETS', self.sec)):
            patcher = patch.object(app, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ('SG_ADMIN_TOKEN_FILE', 'SG_LEGACY_SCREEN_PAIRING', 'SG_TRUST_PROXY', 'SG_COOKIE_SECURE', 'SG_ADMIN_IDLE_SECONDS', 'SG_ADMIN_SESSION_SECONDS'):
            os.environ.pop(name, None)
        self.now = 1_000_000.0
        patcher = patch.object(adminauth, '_clock', lambda: self.now)
        patcher.start()
        self.addCleanup(patcher.stop)
        adminauth.reset_throttle()
        app.SETTINGS.clear()
        tv.PAIR.clear()
        self.cookie, self.csrf = '', ''

    def call(self, method, path, body=None, headers=None, bearer=None, use_session=True):
        sent = {}
        data = json.dumps(body).encode() if body is not None else (b'{}' if method == 'POST' else b'')
        hdrs = {'Content-Type': 'application/json', 'Content-Length': str(len(data))}
        if use_session and self.cookie:
            hdrs['Cookie'] = self.cookie
            if method == 'POST':
                hdrs['X-SG-CSRF'] = self.csrf
        if bearer:
            hdrs['Authorization'] = 'Bearer ' + bearer
        hdrs.update(headers or {})

        class Fake(app.Handler):
            def __init__(inner):
                inner.path, inner.headers, inner.rfile, inner.wfile = path, hdrs, io.BytesIO(data), io.BytesIO()

            def send(inner, status, payload):
                sent['status'], sent['body'], sent['extra'] = status, payload, list(getattr(inner, '_extra_headers', []))
        Fake().dispatch(method == 'POST')
        return sent['status'], sent['body'], dict(sent['extra'])

    def adopt(self, extra, body):
        cookie = extra['Set-Cookie']
        self.cookie, self.csrf = cookie.split(';')[0], body['csrf']

    def sign_in(self, user='admin', password='admin'):
        status, body, extra = self.call('POST', '/v1/auth/login', {'username': user, 'password': password}, use_session=False)
        if status == 200:
            self.adopt(extra, body)
        return status, body, extra

    def ready(self, password='a-fresh-long-password'):
        """Finish the forced change and return the password."""
        adminauth.prepare(self.data, self.sec, lambda _: None)
        self.assertEqual(self.sign_in()[0], 200)
        status, body, extra = self.call('POST', '/v1/auth/credentials', {'current_password': 'admin', 'new_password': password})
        self.assertEqual(status, 200, body)
        self.adopt(extra, body)
        return password


class FreshInstallTests(AuthCase):
    def test_fresh_instance_gets_a_private_hashed_default_and_no_plaintext(self):
        logged = []
        self.assertEqual(adminauth.prepare(self.data, self.sec, logged.append), 'login')
        path = self.data / adminauth.AUTH_FILE
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        text = path.read_text()
        record = json.loads(text)
        self.assertTrue(record['must_change'])
        self.assertTrue(record['verifier'].startswith('scrypt$32768$8$1$'))
        self.assertNotIn('"admin"', text.replace('"username": "admin"', ''))      # the password is not stored anywhere
        self.assertNotIn('password', text.replace('verifier', ''))
        self.assertEqual(adminauth.prepare(self.data, self.sec, lambda _: None), 'login')
        self.assertEqual(json.loads(path.read_text())['verifier'], record['verifier'])       # a restart keeps the account
        self.assertFalse((self.data / 'claim-code').exists())

    def test_default_login_forces_a_password_change_and_blocks_everything_else(self):
        adminauth.prepare(self.data, self.sec, lambda _: None)
        self.assertEqual(self.call('GET', '/v1/settings')[0], 401)
        status, body, extra = self.sign_in()
        self.assertEqual((status, body['must_change'], body['username']), (200, True, 'admin'))
        cookie = extra['Set-Cookie']
        for flag in ('HttpOnly', 'SameSite=Strict', 'Path=/'):
            self.assertIn(flag, cookie)
        self.assertNotIn('Secure', cookie)
        self.assertNotIn('admin', cookie.split(';')[0].split('=')[1])
        for method, path in (('GET', '/v1/settings'), ('GET', '/v1/settings/export'), ('POST', '/v1/settings'), ('GET', '/v1/ops/backup'),
                             ('GET', '/v1/catalog/favorites'), ('GET', '/v1/providers'), ('POST', '/screen-pair'), ('GET', '/v1/artwork')):
            status = self.call(method, path)[0]
            self.assertIn(status, (401, 403), path)
            self.assertNotEqual(status, 200, path)
        self.assertEqual(self.call('GET', '/v1/settings')[1], {'error': 'password_change_required'})
        self.assertEqual(self.call('GET', '/v1/auth/session')[1]['must_change'], True)
        self.assertEqual(self.call('POST', '/v1/auth/credentials', {'current_password': 'wrong', 'new_password': 'long-enough-pass'})[1], {'error': 'current_password_incorrect'})
        self.assertEqual(self.call('POST', '/v1/auth/credentials', {'current_password': 'admin', 'new_password': 'short'})[1], {'error': 'password_length'})
        self.assertEqual(self.call('POST', '/v1/auth/credentials', {'current_password': 'admin', 'new_username': 'boss'})[1], {'error': 'password_change_required'})
        self.assertEqual(self.call('GET', '/v1/settings')[0], 403)
        status, body, extra = self.call('POST', '/v1/auth/credentials', {'current_password': 'admin', 'new_password': 'a-fresh-long-password'})
        self.assertEqual((status, body['must_change']), (200, False))
        old_cookie = self.cookie
        self.adopt(extra, body)
        self.assertNotEqual(old_cookie, self.cookie)
        self.assertEqual(self.call('GET', '/v1/settings')[0], 200)
        self.assertEqual(self.call('GET', '/v1/settings', headers={'Cookie': old_cookie})[0], 401)      # the forced-change session is gone
        self.cookie = ''
        self.assertEqual(self.sign_in('admin', 'admin')[0], 401)                                          # the default no longer works
        self.assertEqual(self.sign_in('admin', 'a-fresh-long-password')[0], 200)

    def test_restart_keeps_account_and_sessions(self):
        password = self.ready()
        cookie, csrf = self.cookie, self.csrf
        adminauth.prepare(self.data, self.sec, lambda _: None)            # a restart
        self.assertEqual(self.call('GET', '/v1/settings')[0], 200)
        self.assertEqual((self.cookie, self.csrf), (cookie, csrf))
        self.cookie = ''
        self.assertEqual(self.sign_in('admin', password)[0], 200)

    def test_failures_are_generic_and_do_not_reveal_the_username(self):
        self.ready()
        self.cookie = ''
        a = self.sign_in('admin', 'wrong-password-1')
        b = self.sign_in('nobody', 'wrong-password-1')
        c = self.sign_in('admin', '')
        self.now += 5
        for result in (a, b, c):
            self.assertEqual((result[0], result[1]), (401, {'error': 'invalid_credentials'}))
        self.assertEqual(self.call('POST', '/v1/auth/login', {'username': ['x'], 'password': 5}, use_session=False)[0], 401)
        self.assertEqual(self.call('POST', '/v1/auth/login', None, headers={'Content-Type': 'text/plain'}, use_session=False)[0], 400)

    def test_bounded_backoff_then_self_heals(self):
        self.ready()
        self.cookie = ''
        statuses = [self.sign_in('admin', 'wrong-%d-password' % i)[0] for i in range(6)]
        self.assertEqual(statuses, [401, 401, 401, 429, 429, 429])
        status, body, extra = self.sign_in('admin', 'a-fresh-long-password')
        self.assertEqual(status, 429)                                           # even the right password waits
        self.assertGreaterEqual(body['retry_after'], 1)
        self.assertEqual(extra['Retry-After'], str(body['retry_after']))
        self.now += 31                                                          # the account backoff is capped at 30 s
        self.assertEqual(self.sign_in('admin', 'a-fresh-long-password')[0], 200)
        # an hour later nothing is remembered
        self.cookie = ''
        for i in range(6):
            self.sign_in('admin', 'wrong-%d-password' % i)
        self.now += 4000
        self.assertEqual(self.sign_in('admin', 'a-fresh-long-password')[0], 200)

    def test_logout_revokes_the_session(self):
        self.ready()
        self.assertEqual(self.call('GET', '/v1/settings')[0], 200)
        status, body, extra = self.call('POST', '/v1/auth/logout')
        self.assertEqual((status, body['authenticated']), (200, False))
        self.assertIn('Max-Age=0', extra['Set-Cookie'])
        self.assertEqual(self.call('GET', '/v1/settings')[0], 401)
        self.assertEqual(self.call('GET', '/v1/auth/session')[1]['authenticated'], False)

    def test_idle_and_absolute_expiry(self):
        self.ready()
        self.now += 1700
        self.assertEqual(self.call('GET', '/v1/settings')[0], 200)             # activity renews the idle window
        self.now += 1700
        self.assertEqual(self.call('GET', '/v1/settings')[0], 200)
        self.now += 1801
        self.assertEqual(self.call('GET', '/v1/settings')[0], 401)             # idle
        self.cookie = ''
        self.sign_in('admin', 'a-fresh-long-password')
        started = self.now
        for _ in range(30):                                                    # keep it busy past the 12 h absolute limit
            self.now += 1500
            status = self.call('GET', '/v1/settings')[0]
            if status == 401:
                break
        self.assertEqual(status, 401)
        self.assertGreater(self.now - started, 12 * 3600)
        self.assertLess(self.now - started, 12 * 3600 + 1600)

    def test_writes_need_the_csrf_token_and_same_origin(self):
        self.ready()
        body = {'revision': 1, 'ops': []}
        self.assertEqual(self.call('POST', '/v1/settings', body, headers={'X-SG-CSRF': ''})[1], {'error': 'csrf_rejected'})
        self.assertEqual(self.call('POST', '/v1/settings', body, headers={'X-SG-CSRF': 'guess'})[0], 403)
        self.assertEqual(self.call('POST', '/v1/settings', body, headers={'Origin': 'http://evil.example', 'Host': 'box:8099'})[0], 403)
        self.assertNotEqual(self.call('POST', '/v1/settings', body, headers={'Origin': 'http://box:8099', 'Host': 'box:8099'})[0], 403)
        self.assertEqual(self.call('POST', '/v1/auth/logout', headers={'X-SG-CSRF': 'guess'})[0], 403)
        self.assertEqual(self.call('POST', '/v1/auth/credentials', {'current_password': 'x', 'new_username': 'bob'}, headers={'X-SG-CSRF': ''})[0], 403)
        self.assertEqual(self.call('POST', '/v1/auth/login', {'username': 'a', 'password': 'b'}, headers={'Origin': 'http://evil.example', 'Host': 'box:8099'}, use_session=False)[0], 403)
        self.assertEqual(self.call('POST', '/screen-pair', headers={'X-SG-CSRF': ''})[0], 401)

    def test_secure_cookie_when_https_is_in_use(self):
        adminauth.prepare(self.data, self.sec, lambda _: None)
        forwarded = {'X-Forwarded-Proto': 'https'}
        self.assertNotIn('Secure', self.call('POST', '/v1/auth/login', {'username': 'admin', 'password': 'admin'}, headers=forwarded, use_session=False)[2]['Set-Cookie'])
        with patch.dict(os.environ, {'SG_TRUST_PROXY': '1'}):
            status, body, extra = self.call('POST', '/v1/auth/login', {'username': 'admin', 'password': 'admin'}, headers=forwarded, use_session=False)
            self.assertTrue(extra['Set-Cookie'].startswith('__Host-sg_admin='))
            self.assertIn('; Secure', extra['Set-Cookie'])
            self.assertIn('HttpOnly', extra['Set-Cookie'])
            self.cookie, self.csrf = extra['Set-Cookie'].split(';')[0], body['csrf']
            self.assertEqual(self.call('GET', '/v1/auth/session')[1]['authenticated'], True)
            self.assertEqual(self.call('POST', '/v1/auth/logout', headers=forwarded)[2]['Set-Cookie'].count('Secure'), 1)
        with patch.dict(os.environ, {'SG_COOKIE_SECURE': '1'}):
            self.assertIn('; Secure', self.call('POST', '/v1/auth/login', {'username': 'admin', 'password': 'admin'}, use_session=False)[2]['Set-Cookie'])

    def test_change_username_and_password_later(self):
        password = self.ready()
        status, body, _ = self.call('POST', '/v1/auth/credentials', {'current_password': password, 'new_username': 'Boss.Manager'})
        self.assertEqual((status, body['username']), (200, 'boss.manager'))
        self.assertEqual(self.call('POST', '/v1/auth/credentials', {'current_password': password, 'new_username': 'x'})[1], {'error': 'invalid_username'})
        self.assertEqual(self.call('POST', '/v1/auth/credentials', {'current_password': password})[1], {'error': 'nothing_to_change'})
        self.assertEqual(self.call('POST', '/v1/auth/credentials', {'current_password': password, 'new_password': password})[1], {'error': 'password_unchanged'})
        self.assertEqual(self.call('POST', '/v1/auth/credentials', {'current_password': password, 'new_password': 'Boss.Manager'})[1], {'error': 'password_too_weak'})
        first_session = self.cookie
        self.cookie = ''
        self.assertEqual(self.sign_in('admin', password)[0], 401)
        self.assertEqual(self.sign_in('BOSS.manager', password)[0], 200)
        status, body, extra = self.call('POST', '/v1/auth/credentials', {'current_password': password, 'new_password': 'another-long-password'})
        self.assertEqual(status, 200)
        self.adopt(extra, body)
        self.assertEqual(self.call('GET', '/v1/settings', headers={'Cookie': first_session}, use_session=False)[0], 401)   # a password change signs out every other session
        self.assertEqual(self.call('GET', '/v1/settings')[0], 200)

    def test_export_and_responses_never_carry_credential_material(self):
        password = self.ready()
        record = json.loads((self.data / adminauth.AUTH_FILE).read_text())
        salt_and_hash = record['verifier'].split('$')[-2:]
        seen = json.dumps([self.call('GET', '/v1/settings/export')[1], self.call('GET', '/v1/settings')[1], self.call('GET', '/v1/ops/diagnostics')[1],
                           self.call('GET', '/v1/ops/backup')[1], self.call('GET', '/v1/auth/session')[1].get('username')])
        for needle in [password, record['verifier'], 'scrypt'] + salt_and_hash + [self.cookie.split('=')[1]]:
            self.assertNotIn(needle, seen)
        self.assertNotIn(password, (self.data / adminauth.SESSIONS_FILE).read_text())
        self.assertNotIn(self.cookie.split('=')[1], (self.data / adminauth.SESSIONS_FILE).read_text())    # only the session id's digest is stored


class FailClosedTests(AuthCase):
    def corrupt(self, content):
        (self.data / adminauth.AUTH_FILE).write_text(content)

    def bad_records(self):
        good = json.loads((self.data / adminauth.AUTH_FILE).read_text())
        yield 'not json{'
        yield ''
        yield '[]'
        yield json.dumps({k: v for k, v in good.items() if k != 'verifier'})
        yield json.dumps({k: v for k, v in good.items() if k != 'epoch'})
        yield json.dumps(dict(good, version=2))
        yield json.dumps(dict(good, must_change='no'))
        yield json.dumps(dict(good, verifier='scrypt$99999999$8$1$AA==$AA=='))
        yield json.dumps(dict(good, verifier='plain-text'))
        salt, digest = good['verifier'].split('$')[-2:]
        for n, r, p, sl, dg in ((1024, 8, 1, salt, digest), (32769, 8, 1, salt, digest), (32768, 0, 1, salt, digest), (32768, 8, 99, salt, digest),
                                (32768, 8, 1, 'AAAA', digest), (32768, 8, 1, salt, 'AAAA'), (32768, 8, 1, '!!!', digest)):
            yield json.dumps(dict(good, verifier='scrypt$%d$%d$%d$%s$%s' % (n, r, p, sl, dg)))
        yield json.dumps(dict(good, username=5))

    def test_invalid_record_never_becomes_admin_admin_or_a_migration(self):
        self.ready()
        (self.sec / 'api_token').write_text('retained-legacy-token')     # an operator-supplied token that is still around
        for content in self.bad_records():
            self.corrupt(content)
            self.assertEqual(adminauth.prepare(self.data, self.sec, lambda _: None), 'invalid')
            self.assertEqual((self.data / adminauth.AUTH_FILE).read_text(), content)          # nothing was overwritten
            self.assertEqual(adminauth.mode(self.data, self.sec), 'invalid')
            self.cookie = ''
            self.assertEqual(self.sign_in('admin', 'admin')[0], 503)
            self.assertEqual(self.sign_in('admin', 'a-fresh-long-password')[0], 503)
            self.assertEqual(self.call('POST', '/v1/auth/migrate', {'token': 'retained-legacy-token', 'username': 'owner', 'password': 'a-long-new-password'}, use_session=False)[0], 503)
            self.assertEqual(self.call('GET', '/v1/auth/session')[1], {'authenticated': False, 'mode': 'invalid'})
            self.assertEqual(self.call('GET', '/v1/settings', bearer='retained-legacy-token', use_session=False)[0], 401)
            self.assertEqual(self.call('GET', '/v1/settings')[0], 401)

    def test_invalid_record_without_a_legacy_token_is_not_replaced_by_a_default(self):
        self.ready()
        self.corrupt('{broken')
        self.assertEqual(adminauth.prepare(self.data, self.sec, lambda _: None), 'invalid')
        self.assertEqual(self.sign_in('admin', 'admin')[0], 503)
        self.assertEqual((self.data / adminauth.AUTH_FILE).read_text(), '{broken')

    def test_unreadable_record_fails_closed(self):
        self.ready()
        with patch.object(Path, 'read_text', side_effect=PermissionError('denied')):
            self.assertEqual(adminauth.mode(self.data, self.sec), 'invalid')
            self.assertEqual(adminauth.login(self.data, self.sec, 'admin', 'x', None)[0], 'unavailable')

    def test_recovery_replaces_a_damaged_record_and_a_live_session_dies(self):
        self.ready()
        live = self.cookie
        self.corrupt('{broken')
        self.assertEqual(self.call('GET', '/v1/settings')[0], 401)
        out = io.StringIO()
        with patch('sys.stdin', io.StringIO('recovered-password-9\n')), patch('sys.stdout', out):
            self.assertEqual(adminauth.main(['adminauth.py', 'reset'], self.data), 0)
        self.assertEqual(self.call('GET', '/v1/settings', headers={'Cookie': live}, use_session=False)[0], 401)
        self.cookie = ''
        self.assertEqual(self.sign_in('admin', 'recovered-password-9')[0], 200)

    def test_credentials_checked_before_a_password_change_cannot_open_a_session_after_it(self):
        password = self.ready()
        real = adminauth.verify_password
        changed = []

        def verify_then_change(candidate, stored):
            result = real(candidate, stored)
            if not changed:
                changed.append(True)
                adminauth.reset(self.data, 'replacement-password-3')     # lands after the check, before the session is issued
            return result
        with patch.object(adminauth, 'verify_password', verify_then_change):
            outcome, issued = adminauth.login(self.data, self.sec, 'admin', password, None)
        self.assertEqual((outcome, issued), ('invalid', None))
        self.assertEqual(self.sign_in('admin', password)[0], 401)
        self.assertEqual(self.sign_in('admin', 'replacement-password-3')[0], 200)

    def test_same_interleaving_with_a_password_change_in_the_ui_path(self):
        password = self.ready()
        real = adminauth.verify_password
        changed = []

        def verify_then_change(candidate, stored):
            result = real(candidate, stored)
            if not changed:
                changed.append(True)
                adminauth.reset(self.data, 'replacement-password-4', 'owner')
            return result
        with patch.object(adminauth, 'verify_password', verify_then_change):
            self.assertEqual(adminauth.login(self.data, self.sec, 'admin', password, None)[0], 'invalid')
        self.assertEqual(self.call('GET', '/v1/auth/session', use_session=False)[1]['authenticated'], False)


class SeparationTests(AuthCase):
    def test_admin_session_is_never_a_device_token_and_vice_versa(self):
        self.ready()
        sid = self.cookie.split('=')[1]
        for bearer in (sid, self.csrf):
            self.assertEqual(self.call('GET', '/v1/tv/catalog', bearer=bearer, use_session=False)[0], 401)
            self.assertEqual(self.call('GET', '/v1/settings/client', bearer=bearer, use_session=False)[0], 401)
        self.assertEqual(self.call('GET', '/v1/tv/catalog')[0], 401)       # the admin cookie alone is not a TV either
        status, pairing, _ = self.call('POST', '/screen-pair')
        self.assertEqual((status, len(pairing['code'])), (200, 6))
        device = tv.pair(pairing['code'])['token']
        self.assertEqual(self.call('GET', '/v1/tv/catalog', bearer=device, use_session=False)[0], 200)
        self.assertEqual(self.call('GET', '/v1/settings/client', bearer=device, use_session=False)[0], 200)
        for path in ('/v1/settings', '/v1/settings/export', '/v1/ops/diagnostics', '/v1/catalog/favorites'):
            self.assertEqual(self.call('GET', path, bearer=device, use_session=False)[0], 401, path)
        self.assertEqual(self.call('POST', '/screen-pair', bearer=device, use_session=False)[0], 401)
        self.assertEqual(self.call('GET', '/v1/auth/session', bearer=device, use_session=False)[1]['authenticated'], False)
        # pairing is untouched by an admin sign-out or password change
        self.call('POST', '/v1/auth/logout')
        self.assertEqual(self.call('GET', '/v1/tv/catalog', bearer=device, use_session=False)[0], 200)

    def test_legacy_screen_pairing_flag_still_needs_the_screen_token_and_origin(self):
        adminauth.prepare(self.data, self.sec, lambda _: None)
        forged = {'Origin': 'http://box:8099', 'Host': 'box:8099', 'X-SG-Screen-Token': app.SCREEN_TOKEN}
        self.assertEqual(self.call('POST', '/screen-pair', headers=forged)[0], 401)

    def test_origin_check_is_same_origin_or_operator_listed(self):
        self.assertTrue(app.screen_origin_ok({'Origin': 'http://box:8099', 'Host': 'box:8099'}))
        self.assertFalse(app.screen_origin_ok({'Origin': 'http://evil.example', 'Host': 'box:8099'}))
        self.assertFalse(app.screen_origin_ok({'Host': 'box:8099'}))
        with patch.dict(os.environ, {'SG_ALLOWED_ORIGINS': 'https://sports.example.org/'}):
            self.assertTrue(app.screen_origin_ok({'Origin': 'https://sports.example.org', 'Host': 'internal:8099'}))


class MigrationTests(AuthCase):
    def seed_settings(self):
        store = app.settings_store()
        store.update(store.revision(), [{'op': 'upsert', 'section': 'accounts', 'item': {'id': 'acct-main', 'name': 'Main', 'max_connections': 2}}])

    def snapshot(self):
        export = app.settings_store().export()
        return json.dumps({k: export[k] for k in ('accounts', 'sources', 'revision')}, sort_keys=True)

    def check_migration(self, token):
        self.seed_settings()
        before = self.snapshot()
        self.assertEqual(adminauth.prepare(self.data, self.sec, lambda _: None), 'migrate')
        self.assertFalse((self.data / adminauth.AUTH_FILE).exists())                      # an upgrade is never reset to admin / admin
        self.assertEqual(self.sign_in('admin', 'admin')[:2], (409, {'error': 'migration_required'}))
        self.assertEqual(self.call('GET', '/v1/auth/session')[1], {'authenticated': False, 'mode': 'migrate'})
        self.assertEqual(self.call('GET', '/v1/settings', bearer=token)[0], 401)          # a token is not accepted as a request credential any more
        migrate = lambda **kw: self.call('POST', '/v1/auth/migrate', dict({'token': token, 'username': 'owner', 'password': 'a-long-new-password'}, **kw), use_session=False)
        self.assertEqual(migrate(token='wrong')[:2], (401, {'error': 'invalid_credentials'}))
        self.assertEqual(migrate(password='short')[1], {'error': 'password_length'})
        self.now += 400
        status, body, extra = migrate()
        self.assertEqual((status, body['must_change'], body['username']), (200, False, 'owner'))
        self.adopt(extra, body)
        self.assertEqual(self.call('GET', '/v1/settings')[0], 200)
        self.assertEqual(self.snapshot(), before)       # no data loss
        self.assertEqual(self.call('GET', '/v1/settings', bearer=token, use_session=False)[0], 401)
        self.assertEqual(self.call('POST', '/v1/auth/migrate', {'token': token, 'username': 'again', 'password': 'a-long-new-password'}, use_session=False)[:2], (409, {'error': 'nothing_to_migrate'}))
        self.assertFalse((self.data / 'admin_token.sha256').exists())
        self.cookie = ''
        self.assertEqual(self.sign_in('admin', 'admin')[0], 401)
        self.assertEqual(self.sign_in('owner', 'a-long-new-password')[0], 200)
        self.assertEqual(adminauth.prepare(self.data, self.sec, lambda _: None), 'login')   # and it stays migrated after a restart

    def test_supplied_secret_token_migrates_once_and_stops_working(self):
        (self.sec / 'api_token').write_text('legacy-supplied-token\n')
        self.check_migration('legacy-supplied-token')
        self.assertEqual(self.call('GET', '/v1/settings', bearer='legacy-supplied-token', use_session=False)[0], 401)

    def test_compose_secret_file_token_migrates(self):
        secret = Path(self.temp.name) / 'compose-secret'
        secret.write_text('compose-token')
        with patch.dict(os.environ, {'SG_ADMIN_TOKEN_FILE': str(secret)}):
            self.check_migration('compose-token')

    def test_claimed_digest_token_migrates_and_the_digest_is_removed(self):
        import hashlib
        self.data.mkdir(exist_ok=True)
        (self.data / 'admin_token.sha256').write_text(hashlib.sha256(b'old-claimed-token').hexdigest())
        self.check_migration('old-claimed-token')

    def test_wrong_tokens_are_throttled(self):
        (self.sec / 'api_token').write_text('legacy')
        adminauth.prepare(self.data, self.sec, lambda _: None)
        codes = [self.call('POST', '/v1/auth/migrate', {'token': 'g%d' % i, 'username': 'owner', 'password': 'a-long-new-password'}, use_session=False)[0] for i in range(6)]
        self.assertEqual(codes[-1], 429)


class RecoveryTests(AuthCase):
    def run_cli(self, argv, stdin=''):
        out = io.StringIO()
        with patch('sys.stdin', io.StringIO(stdin)), patch('sys.stdout', out):
            code = adminauth.main(['adminauth.py'] + argv, self.data)
        return code, out.getvalue()

    def test_reset_sets_a_new_password_without_printing_it_and_signs_everyone_out(self):
        self.ready()
        before = self.cookie
        self.assertEqual(self.call('GET', '/v1/settings')[0], 200)
        code, out = self.run_cli(['reset'], 'recovered-password-9\n')
        self.assertEqual(code, 0)
        self.assertNotIn('recovered-password-9', out)
        self.assertEqual(self.call('GET', '/v1/settings')[0], 401)                  # the live server sees the change at once
        self.cookie = ''
        self.assertEqual(self.sign_in('admin', 'a-fresh-long-password')[0], 401)
        status, body, _ = self.sign_in('admin', 'recovered-password-9')
        self.assertEqual((status, body['must_change']), (200, False))
        self.assertEqual(self.call('GET', '/v1/settings')[0], 200)
        self.assertNotEqual(before, self.cookie)

    def test_reset_default_restores_admin_with_a_forced_change(self):
        self.ready()
        self.assertEqual(self.run_cli(['reset', '--default'])[0], 0)
        self.cookie = ''
        status, body, _ = self.sign_in('admin', 'admin')
        self.assertEqual((status, body['must_change']), (200, True))
        self.assertEqual(self.call('GET', '/v1/settings')[0], 403)

    def test_reset_can_rename_and_bootstraps_a_missing_account_and_refuses_weak_input(self):
        self.assertEqual(self.run_cli(['reset', '--username', 'keeper'], 'brand-new-password\n')[0], 0)
        self.assertEqual(self.sign_in('keeper', 'brand-new-password')[0], 200)
        code, out = self.run_cli(['reset'], 'short\n')
        self.assertEqual(code, 1)
        self.assertNotIn('short', out.replace('password_length', ''))
        self.assertEqual(self.run_cli(['reset', '--username', '!!'], 'long-enough-password\n')[0], 1)
        self.assertEqual(self.run_cli(['bogus'])[0], 2)
        self.cookie = ''
        self.assertEqual(self.sign_in('keeper', 'brand-new-password')[0], 200)      # the refused attempts changed nothing

    def test_reset_also_retires_a_legacy_token(self):
        (self.sec / 'api_token').write_text('legacy')
        self.assertEqual(adminauth.mode(self.data, self.sec), 'migrate')
        self.assertEqual(self.run_cli(['reset'], 'recovered-password-9\n')[0], 0)
        self.assertEqual(adminauth.mode(self.data, self.sec), 'login')
        self.assertEqual(self.call('GET', '/v1/settings', bearer='legacy', use_session=False)[0], 401)

    def test_no_http_route_can_reset_or_bootstrap(self):
        self.ready()
        for path in ('/v1/auth/reset', '/v1/auth/recover', '/v1/auth/bootstrap', '/v1/claim'):
            self.assertIn(self.call('POST', path)[0], (404, 401), path)

    def test_backoff_escalates_to_its_cap_and_is_bounded(self):
        backoff = adminauth.Backoff(cap=30, max_keys=4)
        for _ in range(3):
            backoff.fail('k')
        self.assertEqual(backoff.wait('k'), 2)
        for _ in range(20):
            backoff.fail('k')
        self.assertLessEqual(backoff.wait('k'), 31)
        for key in 'abcdefgh':
            backoff.fail(key)
        self.assertLessEqual(len(backoff.state), 4)
        backoff.clear('h')
        self.assertEqual(backoff.wait('h'), 0)

    def test_password_hash_properties(self):
        first, second = adminauth.hash_password('same-password-1'), adminauth.hash_password('same-password-1')
        self.assertNotEqual(first, second)                                           # salted
        self.assertTrue(adminauth.verify_password('same-password-1', first))
        self.assertFalse(adminauth.verify_password('same-password-2', first))
        self.assertFalse(adminauth.verify_password('x', 'garbage'))
        self.assertFalse(adminauth.verify_password('x', 'scrypt$999999$8$1$AA==$AA=='))


if __name__ == '__main__':
    unittest.main()

"""SGH-302 contract tests: runtime team artwork (validation, fetch policy, cache, fallbacks, settings, export/restore). No network."""
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import app
import artwork
import settings
import tv

SIG = b'\x89PNG\r\n\x1a\n'


def png(width=64, height=64, pad=40):
    return SIG + struct.pack('>I', 13) + b'IHDR' + struct.pack('>II', width, height) + b'\x08\x06\x00\x00\x00' + b'\x00' * pad


def jpeg(width=64, height=48):
    return b'\xff\xd8\xff\xe0\x00\x04\x00\x00' + b'\xff\xc0\x00\x11\x08' + struct.pack('>HH', height, width) + b'\x03' + b'\x00' * 12


TEAMS = {'sports': [{'leagues': [{'teams': [
    {'team': {'id': '12', 'displayName': 'Kansas City Chiefs', 'shortDisplayName': 'Chiefs', 'name': 'Chiefs', 'location': 'Kansas City',
              'logos': [{'href': 'https://a.espncdn.com/i/teamlogos/nfl/500/kc.png'}]}},
    {'team': {'id': '99', 'displayName': 'Evil Team', 'name': 'Team', 'logos': [{'href': 'https://evil.example/x.png'}]}},
    {'team': {'id': '98', 'displayName': 'Svg Team', 'name': 'Team', 'logos': [{'href': 'https://a.espncdn.com/svg.png'}]}},
    {'team': {'id': '97', 'displayName': 'Big Team', 'name': 'Team', 'logos': [{'href': 'https://a.espncdn.com/big.png'}]}},
]}]}]}
LEAGUE = {'leagues': [{'logos': [{'href': 'https://a.espncdn.com/i/leaguelogos/nfl.png', 'rel': ['default']}]}]}
NFL_TEAMS = artwork.ESPN_API + 'football/nfl/teams?limit=1000'
NFL_BOARD = artwork.ESPN_API + 'football/nfl/scoreboard?limit=1'
KC = 'https://a.espncdn.com/i/teamlogos/nfl/500/kc.png'


class Net:
    """A fake provider. Routes map URL -> (status, headers, body); every request is recorded; `down` simulates an outage."""
    def __init__(self):
        self.routes, self.calls, self.down = {}, [], False

    def __call__(self, url, headers, max_bytes, public_only):
        self.calls.append((url, dict(headers), public_only))
        if self.down:
            raise artwork.ArtworkError('network_error')
        status, response_headers, body = self.routes.get(url, (404, {}, b''))
        return status, response_headers, body[:max_bytes + 1] if isinstance(body, bytes) else body


def espn_net():
    net = Net()
    net.routes[NFL_TEAMS] = (200, {}, json.dumps(TEAMS).encode())
    net.routes[NFL_BOARD] = (200, {}, json.dumps(LEAGUE).encode())
    net.routes[KC] = (200, {'etag': '"v1"', 'last-modified': 'Mon, 01 Jan 2024 00:00:00 GMT'}, png())
    net.routes['https://a.espncdn.com/i/leaguelogos/nfl.png'] = (200, {}, png(32, 32))
    net.routes['https://a.espncdn.com/svg.png'] = (200, {}, b'<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>' + b' ' * 20)
    net.routes['https://a.espncdn.com/big.png'] = (200, {}, png(pad=artwork.MAX_IMAGE_BYTES))
    return net


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


class ServiceBase(unittest.TestCase):
    config = None

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / 'artwork'
        self.net, self.clock, self.cfg = espn_net(), Clock(), {'mode': 'espn'}
        self.service = self.make()

    def make(self, overrides=None):
        return artwork.ArtworkService(self.root, lambda: self.cfg, hop=self.net, clock=self.clock, overrides=overrides)


class ImageValidationTests(unittest.TestCase):
    def test_accepts_png_jpeg_webp_and_reports_type_from_bytes(self):
        self.assertEqual(artwork.sniff_image(png(64, 32))[:2], ('image/png', '.png'))
        self.assertEqual(artwork.sniff_image(jpeg(80, 40)), ('image/jpeg', '.jpg', 80, 40))
        self.assertEqual(artwork.sniff_image(b'RIFF\x00\x00\x00\x00WEBPVP8 ' + b'\x00' * 16)[:2], ('image/webp', '.webp'))

    def test_refuses_svg_html_unknown_oversize_and_bad_dimensions(self):
        cases = [(b'<svg xmlns="http://www.w3.org/2000/svg"></svg>' + b' ' * 16, 'svg_not_supported'),
                 (b'<?xml version="1.0"?><svg/>' + b' ' * 16, 'svg_not_supported'),
                 (b'<!DOCTYPE html><html></html>' + b' ' * 16, 'not_an_image'),
                 (b'GIF89a' + b'\x00' * 30, 'unsupported_image_type'), (b'short', 'image_too_small'),
                 (png(pad=artwork.MAX_IMAGE_BYTES), 'image_too_large'), (png(5000, 10), 'image_dimensions'), (png(0, 10), 'image_dimensions'),
                 (b'\xff\xd8\xff\xe0\x00\x04\x00\x00' + b'\x00' * 20, 'image_corrupt')]
        for body, code in cases:
            with self.assertRaises(artwork.ArtworkError) as raised:
                artwork.sniff_image(body)
            self.assertEqual(raised.exception.code, code)


class FetchPolicyTests(unittest.TestCase):
    def hop_to(self, table):
        calls = []

        def hop(url, headers, max_bytes, public_only):
            calls.append(url)
            return table[url]
        return hop, calls

    def test_espn_policy_only_allows_https_espn_hosts(self):
        allows = artwork.espn_policy().allows
        for url in ('https://a.espncdn.com/x.png', 'https://site.api.espn.com/apis/x', 'https://b.a.espncdn.com/x'):
            self.assertTrue(allows(url), url)
        for url in ('http://a.espncdn.com/x.png', 'https://evil.example/x.png', 'https://espncdn.com.evil.example/x', 'https://a.espncdn.com:8443/x',
                    'https://user:pw@a.espncdn.com/x', 'https://notespncdn.com/x', 'file:///etc/passwd'):
            self.assertFalse(allows(url), url)
        self.assertTrue(artwork.espn_policy().public_only)

    def test_redirects_are_followed_within_the_policy_only(self):
        start, ok, bad = 'https://a.espncdn.com/a', 'https://a.espncdn.com/b', 'https://evil.example/c'
        hop, calls = self.hop_to({start: (302, {'location': '/b'}, b''), ok: (200, {}, b'done')})
        self.assertEqual(artwork.fetch(start, artwork.espn_policy(), hop=hop)[::3], (200, ok))
        hop, calls = self.hop_to({start: (302, {'location': bad}, b''), bad: (200, {}, b'x')})
        with self.assertRaises(artwork.ArtworkError) as raised:
            artwork.fetch(start, artwork.espn_policy(), hop=hop)
        self.assertEqual((raised.exception.code, calls), ('host_not_allowed', [start]))
        loop = {start: (302, {'location': start}, b'')}
        with self.assertRaises(artwork.ArtworkError) as raised:
            artwork.fetch(start, artwork.espn_policy(), hop=self.hop_to(loop)[0])
        self.assertEqual(raised.exception.code, 'too_many_redirects')

    def test_operator_base_policy_pins_scheme_host_and_port(self):
        allows = artwork.base_policy('http://logos.lan:8080/art').allows
        self.assertTrue(allows('http://logos.lan:8080/other/x.png'))
        for url in ('https://logos.lan:8080/x', 'http://logos.lan/x', 'http://other.lan:8080/x', 'http://u:p@logos.lan:8080/x'):
            self.assertFalse(allows(url), url)
        self.assertFalse(artwork.base_policy('http://logos.lan:8080').public_only)

    def test_oversized_response_is_refused(self):
        hop, _ = self.hop_to({'https://a.espncdn.com/a': (200, {}, b'x' * 11)})
        with self.assertRaises(artwork.ArtworkError) as raised:
            artwork.fetch('https://a.espncdn.com/a', artwork.espn_policy(), max_bytes=10, hop=hop)
        self.assertEqual(raised.exception.code, 'response_too_large')


class EspnServiceTests(ServiceBase):
    def test_fetch_validate_cache_and_serve_with_provenance(self):
        status, content_type, body, headers = self.service.get('espn_football_nfl_12')
        self.assertEqual((status, content_type, body), (200, 'image/png', png()))
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.assertTrue(all(public for _, _, public in self.net.calls))
        entry = self.service.cache.entry('espn_football_nfl_12')
        self.assertEqual((entry['source'], entry['source_url'], entry['etag'], entry['content_type']), ('espn', KC, '"v1"', 'image/png'))
        self.assertEqual(entry['last_modified'], 'Mon, 01 Jan 2024 00:00:00 GMT')
        self.assertEqual(entry['retrieved_at'], self.clock.t)
        before = len(self.net.calls)
        self.assertEqual(self.service.get('espn_football_nfl_12')[0], 200)
        self.assertEqual(len(self.net.calls), before)   # a fresh copy is served without touching the provider

    def test_conditional_request_gives_304_to_the_tv_and_revalidates_with_validators(self):
        _, _, _, headers = self.service.get('espn_football_nfl_12')
        self.assertEqual(self.service.get('espn_football_nfl_12', headers['ETag'])[0:3], (304, 'image/png', b''))
        self.clock.t += artwork.REVALIDATE_AFTER + 1
        self.net.routes[KC] = (304, {}, b'')
        self.assertEqual(self.service.get('espn_football_nfl_12')[0], 200)
        sent = self.net.calls[-1]
        self.assertEqual((sent[0], sent[1].get('If-None-Match'), sent[1].get('If-Modified-Since')), (KC, '"v1"', 'Mon, 01 Jan 2024 00:00:00 GMT'))
        self.assertEqual(self.service.cache.entry('espn_football_nfl_12')['retrieved_at'], self.clock.t)

    def test_cache_survives_restart_and_provider_outage(self):
        self.service.get('espn_football_nfl_12')
        self.net.down = True
        reopened = self.make()
        self.assertEqual(reopened.get('espn_football_nfl_12')[0:3], (200, 'image/png', png()))
        self.assertEqual(len(self.net.calls), 2)   # directory and logo - nothing further after the outage began

    def test_stale_copy_is_kept_when_the_provider_fails_and_retries_are_backed_off(self):
        self.service.get('espn_football_nfl_12')
        self.clock.t += artwork.REVALIDATE_AFTER + 1
        self.net.down = True
        calls = len(self.net.calls)
        self.assertEqual(self.service.get('espn_football_nfl_12')[0:3], (200, 'image/png', png()))
        entry = self.service.cache.entry('espn_football_nfl_12')
        self.assertEqual((entry['last_error'], bool(entry['file'])), ('network_error', True))
        after_first = len(self.net.calls)
        self.assertGreater(after_first, calls)
        self.service.get('espn_football_nfl_12')
        self.assertEqual(len(self.net.calls), after_first)   # inside the failure back-off: no hammering
        self.clock.t += artwork.FAIL_BACKOFF + 1
        self.net.down = False
        self.net.routes[KC] = (200, {}, png(10, 10))
        self.assertEqual(self.service.get('espn_football_nfl_12')[2], png(10, 10))   # recovers and replaces

    def test_unknown_unsafe_and_unlisted_keys_never_build_a_request(self):
        for key, status in (('espn_cricket_ipl_5', 404), ('../../etc/passwd', 400), ('espn_football_nfl_12/../x', 400), ('A', 400), ('', 400),
                            ('team.golf.men.unknown.nobody', 404), ('x' * 200, 400)):
            self.assertEqual(self.service.get(key)[0], status, key)
        self.assertEqual(self.net.calls, [])

    def test_logo_url_outside_the_allow_list_is_refused_and_not_requested(self):
        result = self.service.get('espn_football_nfl_99')
        self.assertEqual((result[0], json.loads(result[2])['error']), (404, 'host_not_allowed'))
        self.assertNotIn('https://evil.example/x.png', [c[0] for c in self.net.calls])

    def test_svg_and_oversize_provider_content_is_rejected_and_not_cached(self):
        for key, code in (('espn_football_nfl_98', 'svg_not_supported'), ('espn_football_nfl_97', 'response_too_large')):
            result = self.service.get(key)
            self.assertEqual((result[0], json.loads(result[2])['error']), (404, code), key)
            self.assertIsNone(self.service.cache.read(key))
        self.assertEqual(list(self.root.joinpath('files').glob('*')), [])

    def test_failed_key_is_remembered_so_a_page_of_misses_does_not_hammer(self):
        self.assertEqual(self.service.get('espn_football_nfl_555')[0], 404)
        calls = len(self.net.calls)
        self.assertEqual(self.service.get('espn_football_nfl_555')[0], 404)
        self.assertEqual(len(self.net.calls), calls)

    def test_league_logo_and_catalog_ids_resolve(self):
        self.assertEqual(self.service.get('league_football_nfl')[0:3], (200, 'image/png', png(32, 32)))
        self.assertEqual(self.service.get('league.football.nfl')[0], 200)
        self.assertEqual(self.service.get('team.football.men.nfl.kansas-city-chiefs')[0:3], (200, 'image/png', png()))

    def test_override_resolves_a_team_whose_name_does_not_match(self):
        service = self.make(overrides={'team.football.men.nfl.kansas-city-chiefs': '99'})
        self.assertEqual(json.loads(service.get('team.football.men.nfl.kansas-city-chiefs')[2])['error'], 'host_not_allowed')   # resolved to id 99
        self.assertEqual(service.espn_target('team.football.men.nfl.kansas-city-chiefs')['id'], '99')

    def test_switching_source_never_serves_the_old_sources_copy(self):
        self.service.get('espn_football_nfl_12')
        self.cfg = {'mode': 'off'}
        self.assertEqual(json.loads(self.service.get('espn_football_nfl_12')[2])['error'], 'artwork_disabled')
        self.cfg = {'mode': 'espn', 'enabled': False}
        self.assertEqual(self.service.get('espn_football_nfl_12')[0], 404)
        self.cfg = {'mode': 'url', 'url_template': 'http://logos.lan:8080/art'}
        self.assertEqual(self.service.get('espn_football_nfl_12')[0], 404)

    def test_no_configuration_defaults_to_off_and_espn_is_an_explicit_opt_in(self):
        # SGH-314: nothing is fetched from ESPN until the operator chooses it.
        self.cfg = None
        self.assertEqual(self.service.get('espn_football_nfl_12')[0], 404)
        self.assertEqual(self.service.status()['mode'], 'off')
        self.cfg = {'mode': 'espn', 'enabled': True}
        self.assertEqual(self.service.get('espn_football_nfl_12')[0], 200)
        self.assertEqual(self.service.status()['mode'], 'espn')

    def test_clear_removes_images_and_directories_and_rebuilds_on_demand(self):
        self.service.get('espn_football_nfl_12')
        self.assertEqual(self.service.clear()['cleared'], 1)
        self.assertEqual((list(self.root.joinpath('files').glob('*')), list(self.root.joinpath('directory').glob('*'))), ([], []))
        self.assertEqual(self.service.get('espn_football_nfl_12')[0], 200)

    def test_eviction_keeps_the_cache_within_its_entry_limit(self):
        with patch.object(artwork, 'CACHE_MAX_ENTRIES', 2):
            for n in (1, 2, 3):
                self.clock.t += 10
                self.service.cache.put('key-%d' % n, png(n + 1, 8), 'image/png', '.png', {'source': 'espn'}, self.clock.t)
        self.assertEqual(sorted(self.service.cache.entries()), ['key-2', 'key-3'])
        self.assertFalse(self.root.joinpath('files', 'key-1.png').exists())

    def test_damaged_index_is_set_aside_not_fatal(self):
        self.service.get('espn_football_nfl_12')
        (self.root / 'index.json').write_text('{not json')
        reopened = self.make()
        self.assertEqual(reopened.get('espn_football_nfl_12')[0], 200)
        self.assertTrue((self.root / 'index.damaged.json').exists())

    def test_tampered_cache_file_is_not_served(self):
        self.service.get('espn_football_nfl_12')
        (self.root / 'files' / 'espn_football_nfl_12.png').write_bytes(b'<html>swapped</html>' + b' ' * 30)
        self.net.down = True
        self.assertEqual(self.service.get('espn_football_nfl_12')[0], 404)

    def test_status_reports_provider_terms_cache_and_entries(self):
        self.service.get('espn_football_nfl_12')
        self.service.get('espn_football_nfl_555')
        status = self.service.status()
        self.assertEqual((status['mode'], status['provider']['name']), ('espn', 'ESPN public sports metadata'))
        self.assertIn('No redistribution licence', status['provider']['license'])
        self.assertEqual((status['cache']['entries'], status['cache']['failed']), (1, 1))
        rows = {r['key']: r for r in status['entries']}
        self.assertEqual((rows['espn_football_nfl_12']['ok'], rows['espn_football_nfl_555']['last_error']), (True, 'not_found'))

    def test_sync_populates_catalog_art_and_reports_progress(self):
        self.service.run_sync_blocking()
        sync = self.service.status()['sync']
        self.assertEqual((sync['state'], sync['done'] == sync['total'], sync['ok'] >= 2, sync['failed'] > 0), ('done', True, True, True))
        self.assertEqual(self.service.cache.entry('team.football.men.nfl.kansas-city-chiefs')['source_url'], KC)
        self.net.down = True
        self.assertEqual(self.service.get('team.football.men.nfl.kansas-city-chiefs')[0], 200)   # served from cache, provider unavailable

    def test_only_one_sync_runs_at_a_time(self):
        self.service._sync['state'] = 'running'
        self.assertFalse(self.service.sync())


class OperatorSourceTests(ServiceBase):
    BASE = 'http://logos.lan:8080/tok3n-path'

    def test_base_address_is_tried_per_extension_and_never_echoed(self):
        self.cfg = {'mode': 'url', 'url_template': self.BASE}
        self.net.routes[self.BASE + '/team.nfl.chiefs.webp'] = (200, {}, b'RIFF\x00\x00\x00\x00WEBPVP8 ' + b'\x00' * 16)
        result = self.service.get('team.nfl.chiefs')
        self.assertEqual(result[0:2], (200, 'image/webp'))
        self.assertEqual([c[0] for c in self.net.calls], [self.BASE + '/team.nfl.chiefs.png', self.BASE + '/team.nfl.chiefs.webp'])
        self.assertFalse(any(public for _, _, public in self.net.calls))   # a LAN address is the operator's own choice
        self.assertNotIn('tok3n-path', json.dumps(self.service.status()))
        self.assertEqual(self.service.cache.entry('team.nfl.chiefs')['source_host'], 'logos.lan')

    def test_redirect_to_another_host_is_refused(self):
        self.cfg = {'mode': 'url', 'url_template': self.BASE}
        self.net.routes[self.BASE + '/team.nfl.chiefs.png'] = (302, {'location': 'http://169.254.169.254/latest'}, b'')
        result = self.service.get('team.nfl.chiefs')
        self.assertEqual((result[0], json.loads(result[2])['error']), (404, 'host_not_allowed'))
        self.assertNotIn('http://169.254.169.254/latest', [c[0] for c in self.net.calls])

    def test_url_mode_without_an_address_is_off(self):
        self.cfg = {'mode': 'url'}
        self.assertEqual(self.service.settings()[0], 'off')


class PackTests(ServiceBase):
    def setUp(self):
        super().setUp()
        self.cfg = {'mode': 'pack'}
        self.pack = self.root / 'pack'
        self.pack.mkdir(parents=True)

    def manifest(self, **fields):
        data = dict({'schema_version': 1, 'name': 'My pack', 'license': 'CC0 (own work)', 'attribution': 'Me', 'entries': {'team.nfl.chiefs': 'kc.png'}}, **fields)
        (self.pack / 'pack.json').write_text(json.dumps(data))

    def test_files_resolve_by_manifest_or_key_name_without_network(self):
        self.manifest()
        (self.pack / 'kc.png').write_bytes(png())
        (self.pack / 'league_football_nfl.jpg').write_bytes(jpeg())
        self.assertEqual(self.service.get('team.nfl.chiefs')[0:3], (200, 'image/png', png()))
        self.assertEqual(self.service.get('league_football_nfl')[1], 'image/jpeg')
        self.assertEqual(self.service.get('team.nfl.unknown')[0], 404)
        self.assertEqual(self.net.calls, [])
        status = self.service.status()
        self.assertEqual((status['pack']['manifest']['license'], status['provider']['attribution']), ('CC0 (own work)', 'Me'))

    def test_pack_without_manifest_still_serves_key_named_files(self):
        (self.pack / 'team.nfl.chiefs.png').write_bytes(png())
        self.assertEqual(self.service.get('team.nfl.chiefs')[0], 200)

    def test_invalid_manifests_and_traversal_are_refused(self):
        for patch_ in ({'schema_version': 2}, {'name': ''}, {'license': 5}, {'entries': {'team.nfl.chiefs': '../x.png'}}, {'entries': {'Bad Key': 'a.png'}},
                       {'entries': {'team.nfl.chiefs': 'a/b.png'}}, {'entries': []}):
            self.manifest(**patch_)
            (self.pack / 'kc.png').write_bytes(png())
            result = self.service.get('team.nfl.chiefs')
            self.assertEqual((result[0], json.loads(result[2])['error']), (404, 'pack_invalid'), patch_)
            self.assertTrue(self.service.status()['pack']['problems'])

    def test_symlink_escape_and_non_images_are_ignored(self):
        outside = self.root / 'secret.png'
        outside.write_bytes(png())
        (self.pack / 'team.nfl.chiefs.png').symlink_to(outside)
        (self.pack / 'team.nfl.bears.png').write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"/>' + b' ' * 30)
        self.assertEqual(self.service.get('team.nfl.chiefs')[0], 404)
        self.assertEqual(self.service.get('team.nfl.bears')[0], 404)


class SettingsContractTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.store = settings.SettingsStore(self.dir / 's.sqlite')

    @staticmethod
    def item(**fields):
        return {'op': 'upsert', 'section': 'integrations', 'item': dict({'id': 'int-artwork', 'name': 'Team artwork', 'kind': 'artwork', 'enabled': True,
                                                                        'options': {'mode': 'espn'}}, **fields)}

    def errors(self, *ops):
        with self.assertRaises(settings.SettingsError) as raised:
            self.store.update(self.store.revision(), list(ops))
        return {e['error'] for e in raised.exception.errors}

    def test_default_has_no_configuration_and_modes_are_validated(self):
        self.assertIsNone(self.store.artwork_config())
        view = self.store.update(self.store.revision(), [self.item(options={'mode': 'off'})])
        self.assertEqual(self.store.artwork_config(), {'mode': 'off', 'enabled': True, 'url_template': None})
        self.assertEqual(view['integrations'][0]['options'], {'mode': 'off'})
        self.assertIn('invalid_artwork_options', self.errors(self.item(options={'mode': 'bogus'})))
        self.assertIn('invalid_artwork_options', self.errors(self.item(options={'mode': 'espn', 'extra': 1})))
        self.assertIn('base_url_required_for_url_mode', self.errors(self.item(options={'mode': 'url'})))
        self.assertIn('artwork_unsupported_fields', self.errors(self.item(api_key='abcdefghijklmnop')))
        self.assertIn('only_one_artwork_source', self.errors(self.item(), self.item(id='int-artwork-2')))

    def test_base_address_is_a_write_only_secret_that_is_validated(self):
        view = self.store.update(self.store.revision(), [self.item(options={'mode': 'url'}, base_url='http://logos.lan:8080/tok3n-path')])
        self.assertTrue(view['integrations'][0]['base_url_configured'])
        self.assertNotIn('tok3n-path', json.dumps([view, self.store.export(), self.store.client_view()]))
        self.assertEqual(self.store.artwork_config()['url_template'], 'http://logos.lan:8080/tok3n-path')
        self.assertIn('invalid_base_url', self.errors(self.item(options={'mode': 'url'}, base_url='ftp://nope')))

    def test_export_restore_round_trip_into_a_fresh_store(self):
        self.store.update(self.store.revision(), [self.item(options={'mode': 'url'}, base_url='http://logos.lan:8080/art')])
        export = json.loads(json.dumps(self.store.export()))
        needed = {'%s/%s/%s' % (n['section'], n['id'], n['field']) for n in export['secrets_required']}
        self.assertEqual(needed, {'integrations/int-artwork/base_url'})
        fresh = settings.SettingsStore(self.dir / 'fresh.sqlite')
        fresh.restore(fresh.revision(), export, {'integrations/int-artwork/base_url': 'http://logos.lan:8080/art'})
        self.assertEqual(fresh.artwork_config(), {'mode': 'url', 'enabled': True, 'url_template': 'http://logos.lan:8080/art'})
        with self.assertRaises(settings.SettingsError):
            settings.SettingsStore(self.dir / 'other.sqlite').restore(1, export, {})   # the address is a secret and must be supplied again

    def test_disabled_item_turns_artwork_off(self):
        self.store.update(self.store.revision(), [self.item(enabled=False)])
        service = artwork.ArtworkService(self.dir / 'art', self.store.artwork_config)
        self.assertEqual(service.settings()[0], 'off')


class RouteTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = patch.object(app, 'DATA', Path(tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        tv.PAIR.clear()
        tv.ACTIVE.clear()
        self.net = espn_net()
        self.service = artwork.ArtworkService(Path(tmp.name) / 'artwork', lambda: {'mode': 'espn'}, hop=self.net)

    def paired(self, **headers):
        token = tv.pair(tv.new_pair()['code'])['token']
        handler = MagicMock()
        handler.headers = dict({'Authorization': 'Bearer ' + token}, **headers)
        return handler

    def test_paired_tv_gets_image_bytes_with_safe_headers_and_unpaired_is_refused(self):
        with patch.object(app, 'artwork_service', return_value=self.service):
            handler = self.paired()
            self.assertTrue(tv.handle(handler, '/tv/artwork/espn_football_nfl_12', False))
            handler.send_response.assert_called_with(200)
            sent = dict(c[0] for c in handler.send_header.call_args_list)
            self.assertEqual((sent['Content-Type'], sent['X-Content-Type-Options'], sent['Content-Length']), ('image/png', 'nosniff', str(len(png()))))
            handler.wfile.write.assert_called_with(png())
            again = self.paired(**{'If-None-Match': sent['ETag']})
            tv.handle(again, '/tv/artwork/espn_football_nfl_12', False)
            again.send_response.assert_called_with(304)
            anonymous = MagicMock()
            anonymous.headers = {}
            tv.handle(anonymous, '/tv/artwork/espn_football_nfl_12', False)
            self.assertEqual(anonymous.send.call_args[0][0], 401)

    def test_admin_routes_report_sync_and_clear(self):
        with patch.object(app, 'artwork_service', return_value=self.service):
            self.assertEqual(app.artwork_route(False, [])[1]['mode'], 'espn')
            self.service.get('espn_football_nfl_12')
            status, payload = app.artwork_route(True, ['clear'])
            self.assertEqual((status, payload['cleared'], payload['status']['cache']['entries']), (200, 1, 0))
            self.service._sync['state'] = 'running'
            self.assertEqual(app.artwork_route(True, ['sync'])[0], 409)
            self.assertEqual(app.artwork_route(True, ['nope'])[0], 404)

    def test_metrics_labels_stay_bounded(self):
        import ops
        self.assertEqual(ops.route_label('/tv/artwork/espn_football_nfl_12'), 'tv_artwork')
        self.assertEqual(ops.route_label('/v1/artwork/sync'), 'artwork')


if __name__ == '__main__':
    unittest.main()

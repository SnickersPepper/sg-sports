"""Parse official first-team schedule. Fail closed if its structure changes."""
import html
import json
import re
import time
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

SOURCE = 'https://www.fcbarcelona.com/en/football/first-team/schedule'

def text_class(block, cls):
    match = re.search(r'<[^>]+class="[^"]*\b' + re.escape(cls) + r'\b[^\"]*"[^>]*>(.*?)</[^>]+>', block, re.S)
    return html.unescape(re.sub('<[^>]+>', '', match[1])).strip() if match else ''

def parse_schedule(page, now=None):
    now = int(time.time()) if now is None else now
    fixtures, unknown = {}, set()
    for block in re.findall(r'<li\b[^>]*class="fixture-result-list__fixture"[^>]*>.*?</li>', page, re.S):
        home = text_class(block, 'fixture-info__name--home')
        away = text_class(block, 'fixture-info__name--away')
        if 'FC Barcelona' not in (home, away):
            continue
        kickoff = re.search(r'data-kickoff="(\d+)"', block)
        if not kickoff or 'KO: TBA' in block or 'Date and time to be announced' in block:
            unknown.add(re.search(r'data-fixture-id="(\d+)"', block)[1])
            continue
        epoch = int(kickoff[1]) // 1000
        if not now - 86400 <= epoch <= now + 370 * 86400:
            continue
        stamp = datetime.fromtimestamp(epoch, ZoneInfo('Europe/Madrid'))
        fixture_id = re.search(r'data-fixture-id="(\d+)"', block)
        if not fixture_id:
            continue
        fixtures[fixture_id[1]] = {'id': 'barca-' + fixture_id[1], 'team_id': 'fc-barcelona',
            'sport': 'football', 'squad': 'men-first-team', 'opponent': away if home == 'FC Barcelona' else home,
            'home': home == 'FC Barcelona', 'competition': text_class(block, 'fixture-result-list__name'),
            'kickoff': epoch, 'kickoff_iso': stamp.isoformat()}
    if not fixtures or not any(f['kickoff'] > now for f in fixtures.values()):
        raise ValueError('official_schedule_has_no_timed_upcoming_fixtures')
    return {'source': SOURCE, 'checked_at': now, 'refresh_mode': 'automatic_official_schedule',
            'max_age_seconds': 259200, 'untimed_fixtures': len(unknown),
            'fixtures': sorted(fixtures.values(), key=lambda f: f['kickoff'])}

def fetch_schedule():
    request = urllib.request.Request(SOURCE, headers={'User-Agent': 'SGSports/0.2 metadata schedule'})
    with urllib.request.urlopen(request, timeout=25) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError('schedule_too_large')
    return parse_schedule(raw.decode('utf-8'))

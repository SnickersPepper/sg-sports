"""In-process stand-in for a Jellyfin server's public HTTP API, used by unit tests only.

It is NOT end-to-end evidence: the real-server gate lives in tests/jellyfin_e2e and runs against a
pinned Jellyfin. This fake only lets the unit suite exercise the adapter and ledger logic offline.
"""
import json
import re
import urllib.parse

KEY = 'TEST-KEY-PLACEHOLDER'


class FakeJellyfin:
    def __init__(self, version='10.10.7', export_url='http://sg.test', clock=lambda: 0):
        self.version, self.export_url, self.clock = version, export_url, clock
        self.reachable, self.key_ok = True, True
        self.livetv_enabled, self.tuners, self.guide = True, True, True
        self.recording_path, self.path_ok = '/rec', True
        self.channels, self.programs, self.timers, self.recordings = [], [], {}, {}
        self.requests, self.seq, self.refreshes = [], 0, 0
        self.deleted, self.cancelled = [], []

    # helper state builders --------------------------------------------------------------------
    def add_channel(self, name):
        cid = 'ch%d' % (len(self.channels) + 1)
        self.channels.append({'Id': cid, 'Name': name})
        return cid

    def add_program(self, channel_id, start_iso, end_iso, name='Game'):
        pid = 'pr%d' % (len(self.programs) + 1)
        self.programs.append({'Id': pid, 'ChannelId': channel_id, 'StartDate': start_iso, 'EndDate': end_iso, 'Name': name})
        return pid

    def add_recording(self, timer_id, channel_id, status='Completed', size=1000, ticks=36_000_000_000, path=None):
        self.seq += 1
        item = {'Id': 'it%d' % self.seq, 'TimerId': timer_id, 'ChannelId': channel_id, 'Status': status,
                'Path': path if path is not None else '/rec/Other Show/other-%d.ts' % self.seq,
                'MediaSources': [{'Size': size}], 'RunTimeTicks': ticks}
        self.recordings[item['Id']] = item
        return item

    # transport ---------------------------------------------------------------------------------
    def __call__(self, method, url, headers, body, timeout):
        parts = urllib.parse.urlsplit(url)
        assert not parts.query or KEY not in parts.query, 'API key leaked into a URL'
        assert KEY not in url
        payload = json.loads(body) if body else None
        self.requests.append((method, parts.path, parts.query, payload))
        if not self.reachable:
            raise OSError('connection refused')
        path = parts.path
        if path == '/System/Info/Public':
            return self._ok({'ProductName': 'Jellyfin Server', 'Version': self.version})
        auth = headers.get('Authorization', '')
        if not (self.key_ok and ('Token="%s"' % KEY) in auth):
            return 401, b''
        if path == '/System/Info':
            return self._ok({'Version': self.version})
        if path == '/LiveTv/Info':
            return self._ok({'IsEnabled': self.livetv_enabled})
        if path == '/System/Configuration/livetv':
            return self._ok({'TunerHosts': [{'Url': self.export_url + '/jellyfin/export/x/playlist.m3u'}] if self.tuners else [],
                             'ListingProviders': [{'Path': 'x'}] if self.guide else [], 'RecordingPath': self.recording_path})
        if path == '/Environment/ValidatePath':
            return (204, b'') if self.path_ok else (404, b'')
        if path == '/LiveTv/Channels':
            return self._ok({'Items': self.channels})
        if path == '/LiveTv/Programs':
            return self._ok({'Items': self.programs})
        if path == '/ScheduledTasks' and method == 'GET':
            return self._ok([{'Id': 'task1', 'Key': 'RefreshGuide'}])
        if path == '/ScheduledTasks/Running/task1':
            self.refreshes += 1
            return 204, b''
        if path == '/LiveTv/Timers/Defaults':
            return self._ok({'PrePaddingSeconds': 0, 'PostPaddingSeconds': 0, 'Type': 'Program'})
        if path == '/LiveTv/Timers' and method == 'GET':
            return self._ok({'Items': list(self.timers.values())})
        if path == '/LiveTv/Timers' and method == 'POST':
            self.seq += 1
            timer = {**payload, 'Id': 'tm%d' % self.seq, 'Status': 'New'}
            self.timers[timer['Id']] = timer
            return 204, b''
        match = re.fullmatch(r'/LiveTv/Timers/([^/]+)', path)
        if match:
            if match.group(1) not in self.timers:
                return 404, b''
            if method == 'DELETE':
                self.cancelled.append(match.group(1))
                del self.timers[match.group(1)]
            else:
                self.timers[match.group(1)].update(payload)
            return 204, b''
        if path == '/LiveTv/Recordings' and method == 'GET':
            return self._ok({'Items': list(self.recordings.values())})
        if path == '/Library/Refresh' and method == 'POST':
            self.scans = getattr(self, 'scans', 0) + 1
            return 204, b''
        if path == '/Items' and method == 'GET':
            ids = urllib.parse.parse_qs(parts.query).get('Ids', [''])[0].split(',')
            return self._ok({'Items': [self.recordings[i] for i in ids if i in self.recordings]})
        match = re.fullmatch(r'/Items/([^/]+)', path)
        if match and method == 'DELETE':
            item = self.recordings.get(match.group(1))
            if not item:
                return 404, b''
            self.deleted.append(item['Id'])
            del self.recordings[item['Id']]
            return 204, b''
        return 404, b''

    @staticmethod
    def _ok(value):
        return 200, json.dumps(value).encode()

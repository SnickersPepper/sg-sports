import tempfile
import threading
import unittest
from pathlib import Path

import lease


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class LeaseTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / 'leases.sqlite'
        self.clock = Clock()
        self.auth = lease.LeaseAuthority(self.path, self.clock)

    def test_one_holder_per_provider_and_independent_providers(self):
        a = self.auth.acquire('p1', 'playback:a', 'playback')
        self.assertIsNotNone(a)
        self.assertIsNone(self.auth.acquire('p1', 'dvr:x', 'dvr'))
        self.assertIsNone(self.auth.acquire('p1', 'verify:y', 'verification'))
        self.assertIsNotNone(self.auth.acquire('p2', 'dvr:x', 'dvr'))

    def test_same_owner_reacquire_renews_and_keeps_fence(self):
        a = self.auth.acquire('p1', 'o', 'dvr', ttl=10)
        self.clock.t += 5
        b = self.auth.acquire('p1', 'o', 'dvr', ttl=10)
        self.assertEqual(a.fence, b.fence)
        self.assertEqual(b['expires'], 1015)

    def test_expiry_fails_closed_and_is_not_renewable_after_reclaim(self):
        a = self.auth.acquire('p1', 'o1', 'dvr', ttl=10)
        self.clock.t += 11
        self.assertFalse(self.auth.validate(a))
        self.assertFalse(self.auth.renew(a))           # expired leases are never revived
        b = self.auth.acquire('p1', 'o2', 'playback', ttl=10)
        self.assertGreater(b.fence, a.fence)
        self.assertFalse(self.auth.renew(a))
        self.assertFalse(self.auth.release(a))          # a stale holder cannot free the new holder
        self.assertTrue(self.auth.validate(b))

    def test_preemption_only_by_strictly_higher_priority_when_requested(self):
        low = self.auth.acquire('p1', 'verify:v', 'verification')
        self.assertIsNone(self.auth.acquire('p1', 'playback:p', 'playback'))              # not requested
        high = self.auth.acquire('p1', 'playback:p', 'playback', preempt=True)
        self.assertIsNotNone(high)
        self.assertFalse(self.auth.validate(low))                                          # loser must stop
        self.assertIsNone(self.auth.acquire('p1', 'dvr:d', 'dvr', preempt=True))          # lower than holder
        self.assertIsNone(self.auth.acquire('p1', 'playback:q', 'playback', priority=100, preempt=True))  # equal

    def test_cancel_owner_invalidates_holder(self):
        a = self.auth.acquire('p1', 'dvr:x', 'dvr')
        self.assertEqual(self.auth.cancel_owner('dvr:x'), 1)
        self.assertFalse(self.auth.validate(a))
        self.assertIsNotNone(self.auth.acquire('p1', 'playback:z', 'playback'))

    def test_restart_keeps_state_and_fences_never_repeat(self):
        a = self.auth.acquire('p1', 'o', 'dvr', ttl=100)
        again = lease.LeaseAuthority(self.path, self.clock)       # a restarted process / second process
        self.assertIsNone(again.acquire('p1', 'other', 'playback'))
        self.assertTrue(again.validate(a))
        again.release(a)
        b = again.acquire('p1', 'other', 'playback')
        self.assertEqual(b.fence, a.fence + 1)

    def test_concurrent_acquire_grants_exactly_one(self):
        results = []

        def worker(n):
            results.append(lease.LeaseAuthority(self.path, self.clock).acquire('p1', 'o%d' % n, 'playback'))
        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sum(1 for r in results if r), 1)

    def test_file_is_private(self):
        self.assertEqual(self.path.stat().st_mode & 0o077, 0)


if __name__ == '__main__':
    unittest.main()

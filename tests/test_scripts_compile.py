"""The Docker-host acceptance scripts cannot run in the unit suite; at least make sure they stay valid Python and expose their subcommands."""
import importlib.util
import py_compile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class ScriptTests(unittest.TestCase):
    def test_compose_smoke_compiles_and_lists_artwork_steps(self):
        path = ROOT / 'scripts' / 'compose_smoke.py'
        with tempfile.TemporaryDirectory() as tmp:
            py_compile.compile(str(path), cfile=str(Path(tmp) / 'x.pyc'), doraise=True)
        spec = importlib.util.spec_from_file_location('compose_smoke', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertTrue(callable(module.artwork_populate) and callable(module.artwork_persist))
        self.assertEqual(module.PROVIDER_KEYS[0], 'league.football.nfl')
        import base64
        import artwork
        self.assertEqual(artwork.sniff_image(base64.b64decode(module.PNG_B64))[:2], ('image/png', '.png'))   # the stand-in logo passes the real validator


    def test_clean_install_uses_explicit_non_overlapping_subnets(self):
        spec = importlib.util.spec_from_file_location('compose_subnets', ROOT / 'scripts' / 'compose_subnets.py')
        subnets = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(subnets)
        subnets.validate(['172.30.52.0/28', '172.30.52.16/28', '172.30.52.32/28'])
        for bad in (['172.30.52.0/28', '172.30.52.0/28'], ['172.30.52.0/24', '172.30.52.16/28'], ['8.8.8.0/28'], ['172.30.52.1/28'], ['172.30.52.0/16'], ['nonsense']):
            with self.assertRaises(ValueError):
                subnets.validate(bad)
        self.assertIn('subnet: 172.30.52.16/28', subnets.override('172.30.52.16/28'))
        script = (ROOT / 'scripts' / 'compose-clean-install.sh').read_text()
        self.assertNotIn('docker compose -p', script)   # every project goes through an override that pins its subnet
        for name in ('SG_CLEAN_SUBNET_A', 'SG_CLEAN_SUBNET_B', 'SG_CLEAN_SUBNET_C', 'down -v --rmi local --remove-orphans'):
            self.assertIn(name, script)


if __name__ == '__main__':
    unittest.main()

#!/usr/bin/env python3
"""Run the existing unit suite with outbound sockets forbidden, then the offline Phase 9 soak (evidence: artifacts/phase9/soak-summary.json)."""
from pathlib import Path
import os,socket,subprocess,sys,unittest
from unittest.mock import patch
root=Path(__file__).resolve().parent.parent
os.chdir(root);sys.path.insert(0,str(root))
os.environ.update(SG_AUTO_REFRESH='0',SG_DATA=str(root/'.local-test/data'),SG_SECRETS=str(root/'.local-test/empty-secrets'))
with patch.object(socket.socket,'connect',side_effect=AssertionError('Network disabled in tests')),patch.object(socket.socket,'connect_ex',side_effect=AssertionError('Network disabled in tests')):
    result=unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.discover('tests'))
soak=subprocess.run([sys.executable,'scripts/soak_studio.py','--cycles','150'],cwd=root).returncode
sys.exit(not result.wasSuccessful() or soak!=0)

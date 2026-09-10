"""Run regression tests without production DB access or external network calls."""
import logging
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PRODUCTION_DB = (ROOT / "agv.db").resolve()


def protect_operational_systems(event, args):
    if event == "sqlite3.connect":
        database = str(args[0])
        if "agv.db" in database.lower():
            raise RuntimeError("Tests cannot open the operational agv.db")
    if event == "socket.connect":
        address = args[1]
        if not isinstance(address, tuple) or address[0] not in {"127.0.0.1", "::1", "localhost"}:
            raise RuntimeError("Tests cannot connect to external systems")


sys.addaudithook(protect_operational_systems)
# Importing the application's logging module is harmless; configuring it writes logs.
import logging_config
logging_config.configure_logging = lambda: None
logging_config.ensure_log_file = lambda: None
logging.disable(logging.CRITICAL)

if __name__ == "__main__":
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    sys.exit(not result.wasSuccessful())

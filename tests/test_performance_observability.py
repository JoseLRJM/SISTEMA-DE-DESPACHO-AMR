import asyncio
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import main
from db_diagnostics import compare_databases, compare_diagnostics, diagnose_database
from models import Base
from performance_observability import PerformanceMetrics


class PerformanceObservabilityTests(unittest.TestCase):
    def _database(self, path: Path, *, padding_bytes: int = 0):
        connection = sqlite3.connect(path)
        connection.executescript(
            """
            CREATE TABLE movement_orders (
                id INTEGER PRIMARY KEY,
                status TEXT,
                rack_id INTEGER,
                source_cell_id INTEGER,
                destination_cell_id INTEGER,
                status_query_log_json TEXT,
                dispatch_request_json TEXT,
                dispatch_response_json TEXT,
                rcs_response_json TEXT,
                override_payload_json TEXT
            );
            CREATE TABLE racks (id INTEGER PRIMARY KEY, status TEXT, material_group_id INTEGER);
            CREATE TABLE locations (id INTEGER PRIMARY KEY, status INTEGER, rack_id INTEGER, area_id INTEGER);
            CREATE TABLE scan_events (id INTEGER PRIMARY KEY, created_at TEXT);
            CREATE INDEX idx_movement_orders_status_destination_cell ON movement_orders(status, destination_cell_id);
            CREATE INDEX ix_movement_orders_created_at_id ON movement_orders(id, id);
            INSERT INTO movement_orders VALUES (1, 'completed', 1, 1, 2, '{"ok":true}', NULL, NULL, NULL, NULL);
            INSERT INTO racks VALUES (1, 'available', 1);
            INSERT INTO locations VALUES (1, 1, 1, 1);
            """
        )
        if padding_bytes:
            connection.execute("CREATE TABLE padding (value BLOB)")
            connection.execute("INSERT INTO padding VALUES (zeroblob(?))", (padding_bytes,))
        connection.commit()
        connection.close()

    def test_metrics_are_aggregated_bounded_and_secret_free(self):
        metrics = PerformanceMetrics(sample_limit=10)
        for value in range(25):
            metrics.record("http", value, label="locations", size_bytes=value * 2)
        payload = metrics.snapshot()
        series = payload["series"][0]
        self.assertEqual(series["count"], 25)
        self.assertEqual(series["sample_count"], 10)
        self.assertEqual(series["sample_limit"], 10)
        encoded = json.dumps(payload).lower()
        self.assertNotIn("password", encoded)
        self.assertNotIn("token", encoded)

    def test_diagnostic_is_read_only_and_tolerates_missing_modules(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "a.db"
            self._database(path)
            before_bytes = path.read_bytes()
            before_stat = path.stat()
            report = diagnose_database(path)
            after_stat = path.stat()
            self.assertEqual(path.read_bytes(), before_bytes)
            self.assertEqual(after_stat.st_size, before_stat.st_size)
            self.assertEqual(report["row_counts"]["movement_orders"], 1)
            self.assertEqual(report["relevant_modules"]["qr_transition_logs"], "not present")
            self.assertEqual(report["heavy_json_columns"]["status_query_log_json"]["sum_bytes"], 11)
            self.assertEqual(report["integrity"]["integrity_check"], "ok")

    def test_multi_database_comparison_and_growth_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "a.db"
            second = Path(directory) / "b.db"
            self._database(first)
            self._database(second, padding_bytes=200_000)
            payload = compare_databases([first, second])
            self.assertEqual(len(payload["reports"]), 2)
            self.assertTrue(any("grew" in warning for warning in payload["comparison"]["warnings"]))

    def test_expected_index_checker_reports_wrong_definition(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "a.db"
            self._database(path)
            report = diagnose_database(path)
            self.assertEqual(report["index_check"]["expected_indexes"]["idx_movement_orders_status_destination_cell"]["status"], "PRESENT")
            self.assertEqual(report["index_check"]["expected_indexes"]["ix_movement_orders_created_at_id"]["status"], "UNEXPECTED")

    def test_admin_health_is_authenticated_read_only_and_has_no_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "health.db"
            test_engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
            Base.metadata.create_all(test_engine)
            factory = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
            with test_engine.begin() as connection:
                connection.exec_driver_sql("CREATE INDEX idx_movement_orders_status_destination_cell ON movement_orders(status, destination_cell_id)")
            token = "health-test-token"
            main.ADMIN_TOKENS[token] = datetime.utcnow() + timedelta(minutes=5)
            try:
                before = path.read_bytes()
                with patch.object(main, "engine", test_engine), patch.object(main, "SessionLocal", factory):
                    payload = asyncio.run(main.performance_health(token))
                self.assertEqual(path.read_bytes(), before)
                self.assertIn("performance", payload)
                self.assertEqual(payload["indexes"]["expected_indexes"]["idx_movement_orders_status_destination_cell"]["status"], "PRESENT")
                encoded = json.dumps(payload).lower()
                for forbidden in ("password_hash", "auth_header", "connection_string", token):
                    self.assertNotIn(forbidden.lower(), encoded)
            finally:
                main.ADMIN_TOKENS.pop(token, None)
                test_engine.dispose()


if __name__ == "__main__":
    unittest.main()

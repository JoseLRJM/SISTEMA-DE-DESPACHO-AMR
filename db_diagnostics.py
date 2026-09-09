from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


ACTIVE_STATUSES = ("pending_dispatch", "dispatched", "in_progress", "cancel_requested_total", "cancel_requested_undo", "cancel_error")
EXPECTED_INDEXES = {
    "idx_movement_orders_status_destination_cell": ("movement_orders", ("status", "destination_cell_id")),
    "ix_movement_orders_created_at_id": ("movement_orders", ("created_at", "id")),
}
HEAVY_ORDER_COLUMNS = (
    "status_query_log_json", "dispatch_request_json", "dispatch_response_json",
    "rcs_response_json", "override_payload_json",
)
RELEVANT_TABLES = (
    "movement_orders", "locations", "racks", "scan_events", "qr_transition_logs",
    "qr_transition_rules", "qr_action_rules", "scanner_stations", "scan_terminals",
)


def _quote_identifier(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


@contextmanager
def open_read_only(path: str | os.PathLike[str]):
    resolved = Path(path).expanduser().resolve(strict=True)
    connection = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        yield connection
    finally:
        connection.close()


def _index_inventory(connection: sqlite3.Connection, tables: Iterable[str]) -> dict:
    inventory = {}
    for table in tables:
        rows = []
        for index in connection.execute(f"PRAGMA index_list({_quote_identifier(table)})"):
            name = index[1]
            rows.append({
                "name": name,
                "unique": bool(index[2]),
                "origin": index[3],
                "partial": bool(index[4]),
                "columns": [row[2] for row in connection.execute(f"PRAGMA index_info({_quote_identifier(name)})")],
            })
        inventory[table] = rows
    return inventory


def check_expected_indexes(connection: sqlite3.Connection, expected: dict = EXPECTED_INDEXES) -> dict:
    tables = {table for table, _columns in expected.values()}
    actual = _index_inventory(connection, tables)
    result = {}
    expected_names = set(expected)
    for name, (table, columns) in expected.items():
        found = next((row for row in actual.get(table, []) if row["name"] == name), None)
        if found is None:
            status = "MISSING"
        elif tuple(found["columns"]) != tuple(columns):
            status = "UNEXPECTED"
        else:
            status = "PRESENT"
        result[name] = {"status": status, "table": table, "expected_columns": list(columns), "actual_columns": found["columns"] if found else []}
    unexpected = [row["name"] for rows in actual.values() for row in rows if row["name"] not in expected_names and not row["name"].startswith("sqlite_autoindex_")]
    return {"expected_indexes": result, "unexpected_indexes": sorted(unexpected)}


def _dbstat_sizes(connection: sqlite3.Connection) -> tuple[dict, str | None]:
    try:
        rows = connection.execute("SELECT name, SUM(pgsize) AS bytes FROM dbstat GROUP BY name").fetchall()
        return {row[0]: int(row[1] or 0) for row in rows}, None
    except sqlite3.Error as exc:
        return {}, f"dbstat unavailable: {exc}"


def _logical_association_hash(connection: sqlite3.Connection, tables: set[str]) -> str:
    digest = hashlib.sha256()
    queries = {
        "movement_orders": "SELECT id, status, rack_id, source_cell_id, destination_cell_id FROM movement_orders ORDER BY id",
        "locations": "SELECT id, status, rack_id, area_id FROM locations ORDER BY id",
        "racks": "SELECT id, status, material_group_id FROM racks ORDER BY id",
    }
    for table, sql in queries.items():
        if table not in tables:
            continue
        digest.update(table.encode())
        for row in connection.execute(sql):
            digest.update(repr(tuple(row)).encode())
    return digest.hexdigest()


def diagnose_database(path: str | os.PathLike[str]) -> dict:
    resolved = Path(path).expanduser().resolve(strict=True)
    wal = Path(str(resolved) + "-wal")
    shm = Path(str(resolved) + "-shm")
    with open_read_only(resolved) as connection:
        table_names = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        table_set = set(table_names)
        columns = {
            table: [{"name": row[1], "type": row[2], "not_null": bool(row[3]), "pk": bool(row[5])} for row in connection.execute(f"PRAGMA table_info({_quote_identifier(table)})")]
            for table in table_names
        }
        row_counts = {table: int(connection.execute(f"SELECT COUNT(*) FROM {_quote_identifier(table)}").fetchone()[0]) for table in table_names}
        indexes = _index_inventory(connection, table_names)
        object_sizes, dbstat_warning = _dbstat_sizes(connection)
        heavy_columns = {}
        if "movement_orders" in table_set:
            available = {item["name"] for item in columns["movement_orders"]}
            for column in HEAVY_ORDER_COLUMNS:
                if column not in available:
                    heavy_columns[column] = {"status": "not present"}
                    continue
                quoted = _quote_identifier(column)
                row = connection.execute(
                    f"SELECT COUNT({quoted}), COALESCE(SUM(LENGTH({quoted})),0), COALESCE(AVG(LENGTH({quoted})),0), COALESCE(MAX(LENGTH({quoted})),0) FROM movement_orders"
                ).fetchone()
                heavy_columns[column] = {"non_null": int(row[0]), "sum_bytes": int(row[1]), "avg_bytes": round(float(row[2]), 2), "max_bytes": int(row[3])}
        statuses = []
        active_orders = 0
        if "movement_orders" in table_set:
            statuses = [{"status": row[0], "count": int(row[1])} for row in connection.execute("SELECT status, COUNT(*) FROM movement_orders GROUP BY status ORDER BY COUNT(*) DESC")]
            placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
            active_orders = int(connection.execute(f"SELECT COUNT(*) FROM movement_orders WHERE status IN ({placeholders})", ACTIVE_STATUSES).fetchone()[0])
        physical = {
            "locations_with_rack": int(connection.execute("SELECT COUNT(*) FROM locations WHERE rack_id IS NOT NULL").fetchone()[0]) if "locations" in table_set else None,
            "active_orders": active_orders,
        }
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = [dict(row) for row in connection.execute("PRAGMA foreign_key_check")]
        expected = check_expected_indexes(connection)
        association_hash = _logical_association_hash(connection, table_set)
    table_sizes = {table: object_sizes.get(table) for table in table_names}
    index_sizes = {row["name"]: object_sizes.get(row["name"]) for rows in indexes.values() for row in rows}
    warnings = []
    for name, item in expected["expected_indexes"].items():
        if item["status"] != "PRESENT":
            warnings.append(f"expected index {name}: {item['status']}")
    if dbstat_warning:
        warnings.append(dbstat_warning)
    status_log = heavy_columns.get("status_query_log_json", {})
    status_log_bytes = int(status_log.get("sum_bytes", 0))
    if status_log_bytes > resolved.stat().st_size * 0.5:
        warnings.append(f"status_query_log_json represents approximately {status_log_bytes * 100.0 / resolved.stat().st_size:.1f}% of the DB file")
    if wal.exists() and wal.stat().st_size > resolved.stat().st_size:
        warnings.append("WAL is larger than the main DB file")
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "database": {"path": str(resolved), "size_bytes": resolved.stat().st_size, "wal_bytes": wal.stat().st_size if wal.exists() else 0, "shm_bytes": shm.stat().st_size if shm.exists() else 0},
        "integrity": {"integrity_check": integrity, "foreign_key_errors": len(foreign_keys), "foreign_key_details": foreign_keys},
        "tables": table_names,
        "row_counts": row_counts,
        "columns": columns,
        "indexes": indexes,
        "estimated_table_bytes": table_sizes,
        "estimated_index_bytes": index_sizes,
        "movement_order_statuses": statuses,
        "heavy_json_columns": heavy_columns,
        "physical": physical,
        "relevant_modules": {table: row_counts.get(table, "not present") for table in RELEVANT_TABLES},
        "index_check": expected,
        "association_hash": association_hash,
        "warnings": warnings,
    }


def compare_diagnostics(reports: list[dict]) -> dict:
    if len(reports) < 2:
        return {"databases": len(reports), "metrics": [], "warnings": []}
    metrics = []
    base = reports[0]
    metric_getters = {
        "database_size_bytes": lambda r: r["database"]["size_bytes"],
        "wal_bytes": lambda r: r["database"]["wal_bytes"],
        "movement_orders": lambda r: r["row_counts"].get("movement_orders", 0),
        "scan_events": lambda r: r["row_counts"].get("scan_events", 0),
        "qr_transition_logs": lambda r: r["row_counts"].get("qr_transition_logs", 0),
        "racks": lambda r: r["row_counts"].get("racks", 0),
        "locations": lambda r: r["row_counts"].get("locations", 0),
        "active_orders": lambda r: r["physical"].get("active_orders", 0),
        "heavy_order_json_bytes": lambda r: sum(v.get("sum_bytes", 0) for v in r["heavy_json_columns"].values()),
        "physical_index_count": lambda r: sum(len(items) for items in r["indexes"].values()),
        "missing_expected_index_count": lambda r: sum(1 for item in r["index_check"]["expected_indexes"].values() if item["status"] == "MISSING"),
    }
    for name, getter in metric_getters.items():
        values = [getter(report) for report in reports]
        metrics.append({"metric": name, "values": values, "difference_from_first": [value - values[0] for value in values]})
    warnings = []
    first_size = max(1, int(base["database"]["size_bytes"]))
    for report in reports[1:]:
        growth = (int(report["database"]["size_bytes"]) - first_size) * 100.0 / first_size
        if growth > 20.0:
            warnings.append(f"database grew {growth:.1f}% compared with first snapshot: {report['database']['path']}")
    details = []
    for report in reports:
        details.append({
            "database": report["database"]["path"],
            "status_distribution": {row["status"]: row["count"] for row in report["movement_order_statuses"]},
            "indexes": sorted(row["name"] for rows in report["indexes"].values() for row in rows),
            "missing_expected_indexes": sorted(name for name, item in report["index_check"]["expected_indexes"].items() if item["status"] == "MISSING"),
        })
    return {"databases": len(reports), "metrics": metrics, "details": details, "warnings": warnings}


def compare_databases(paths: Iterable[str | os.PathLike[str]]) -> dict:
    reports = [diagnose_database(path) for path in paths]
    return {"reports": reports, "comparison": compare_diagnostics(reports)}


def render_markdown(payload: dict) -> str:
    reports = payload.get("reports") or [payload]
    lines = ["# SQLite health report", ""]
    for report in reports:
        db = report["database"]
        lines.extend([f"## {Path(db['path']).name}", "", f"- Timestamp: {report['timestamp']}", f"- Size: {db['size_bytes']} bytes", f"- WAL: {db['wal_bytes']} bytes", f"- Integrity: {report['integrity']['integrity_check']}", f"- Foreign-key errors: {report['integrity']['foreign_key_errors']}", f"- Active orders: {report['physical']['active_orders']}", "", "| Table | Rows | Approx. bytes |", "|---|---:|---:|"])
        for table, count in sorted(report["row_counts"].items(), key=lambda item: item[1], reverse=True):
            lines.append(f"| {table} | {count} | {report['estimated_table_bytes'].get(table) or 'n/a'} |")
        if report["warnings"]:
            lines.extend(["", "Warnings:"] + [f"- {warning}" for warning in report["warnings"]])
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only SQLite diagnostics and multi-database comparison")
    parser.add_argument("databases", nargs="+", help="One or more agv.db paths")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--output", help="Optional report output path")
    args = parser.parse_args()
    payload = compare_databases(args.databases) if len(args.databases) > 1 else diagnose_database(args.databases[0])
    content = json.dumps(payload, indent=2, ensure_ascii=False) if args.format == "json" else render_markdown(payload)
    if args.output:
        Path(args.output).write_text(content, encoding="utf-8")
    else:
        print(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

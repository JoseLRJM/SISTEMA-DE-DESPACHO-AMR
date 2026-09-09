import subprocess
import unittest
from pathlib import Path

import main


class LocationsClientCacheTests(unittest.TestCase):
    def test_revision_endpoint_uses_cached_count(self):
        previous = main._locations_count_cache
        try:
            main._locations_count_cache = 10_000
            result = main.get_locations_revision()
            self.assertEqual(result["count"], 10_000)
            self.assertEqual(result["revision"], main._locations_current_revision())
        finally:
            main._locations_count_cache = previous

    def test_frontend_uses_indexeddb_with_safe_fallback_and_incremental_writes(self):
        app_js = (Path(main.__file__).parent / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("const LOCATIONS_CACHE_SCHEMA_VERSION = 1", app_js)
        self.assertIn("window.indexedDB.open", app_js)
        self.assertIn("async function clearLocationsCache()", app_js)
        self.assertIn("async function loadLocationsCached", app_js)
        self.assertIn("await loadLocations(options)", app_js)
        self.assertIn("updateLocationsCacheRecord(cacheLocation", app_js)
        cache_block = app_js[
            app_js.index("function openLocationsCacheDb"):
            app_js.index("function rebuildLocationIndexes")
        ]
        self.assertNotIn("localStorage.setItem", cache_block)

    def test_only_location_data_is_persisted(self):
        app_js = (Path(main.__file__).parent / "static" / "app.js").read_text(encoding="utf-8")
        cache_block = app_js[app_js.index("function openLocationsCacheDb") : app_js.index("function rebuildLocationIndexes")]
        for forbidden in ("adminToken", "password", "credential", "status_query_log"):
            self.assertNotIn(forbidden, cache_block)

    def test_location_deleted_removes_memory_indexes_and_updates_indexeddb_revision(self):
        root = Path(main.__file__).parent
        app_js = (root / "static" / "app.js").read_text(encoding="utf-8")
        delete_block = app_js[app_js.index("function applyLocationDeleted") : app_js.index("function applyRackUpdated")]
        script = f"""
const assert = require('assert');
const previous = {{id: 5, x: 1, y: 2, code: 'L-5', rack_id: 9}};
const locations = []; const idx = (x,y) => y * 10 + x; locations[idx(1,2)] = previous;
const locationById = new Map([[5, previous]]), locationByCode = new Map([['L-5', previous]]), locationByRackId = new Map([[9, previous]]);
let operationalLocations = [previous], cleared = 0, draws = 0;
const cellOptionsCache = {{clear() {{ cleared += 1; }}}}; function draw() {{ draws += 1; }}
{delete_block}
applyLocationDeleted(5);
assert.equal(locationById.has(5), false); assert.equal(locationByCode.has('L-5'), false); assert.equal(locationByRackId.has(9), false);
assert.equal(locations[idx(1,2)], null); assert.equal(operationalLocations.length, 0); assert.equal(cleared, 1); assert.equal(draws, 1);
"""
        result = subprocess.run(["node", "-"], input=script, text=True, capture_output=True, cwd=root)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        handler = app_js[app_js.index("async function applyRuntimeIncrementalMessage") : app_js.index("function connectRuntimeSocket")]
        self.assertIn("case 'location_deleted': applyLocationDeleted(payload.id)", handler)
        self.assertIn("updateLocationsCacheRecord(cacheLocation, locationsRevision, message.type === 'location_deleted')", handler)


if __name__ == "__main__":
    unittest.main()

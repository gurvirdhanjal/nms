
## 2025-01-28 - Unnecessary DB Aggregations After Fetching All Records
**Learning:** In `routes/dashboard.py`, `get_inventory_stats()` fetches all devices via `scoped_devices = scoped_query(Device).all()`. Despite having all `Device` objects in memory, it executes two additional `GROUP BY` database queries (for manufacturer and device_type) using `.filter(Device.device_id.in_(scoped_device_ids))`. This requires transmitting thousands of IDs back to the database, parsing massive queries, and querying the same data again.
**Action:** When a function already fetches all records of a given model for other purposes, compute simple aggregations (like counts grouped by a field) in memory during Python iteration rather than dispatching separate SQL queries with large `IN` clauses.

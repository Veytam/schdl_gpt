from datetime import UTC, datetime, timedelta
from pathlib import Path
from collector import SCHEMA, db, expire, store_batch

now = datetime.now(UTC)
old = now - timedelta(days=176)
with db() as conn:
    conn.autocommit = True
    conn.execute(SCHEMA)
    expiration_service = Path("/tmp/expire.sql").read_text()
    store_batch(conn, [
        (-10001, 1, old, None, None, "synthetic expired test"),
        (-10002, 1, now, None, None, "synthetic current test"),
    ])
    try:
        conn.execute(expiration_service)
        rows = conn.execute("SELECT id FROM messages WHERE id IN (-10001, -10002)").fetchall()
        assert rows == [(-10002,)], rows
        store_batch(conn, [(-10002, 1, now, now, None, "synthetic update")])
        assert conn.execute("SELECT count(*), max(body) FROM messages WHERE id = -10002").fetchone() == (1, "synthetic update")
        print("Verified database authentication, schema, partition expiry, and duplicate-free update")
    finally:
        conn.execute("DELETE FROM messages WHERE id IN (-10001, -10002)")
        expire(conn, now - timedelta(days=175))

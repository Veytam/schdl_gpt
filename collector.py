import argparse
import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
from opentele.api import API
from opentele.tl import TelegramClient
from psycopg import sql
from telethon.tl.functions.messages import GetForumTopicsRequest
from telethon.tl.types import ForumTopic

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("telethon").setLevel(logging.WARNING)
RETENTION = timedelta(days=175)
SESSION = "/data/collector"
SCHEMA = """
CREATE TABLE IF NOT EXISTS topics (
    id bigint PRIMARY KEY, title text NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id bigint NOT NULL, topic_id bigint NOT NULL, sent_at timestamptz NOT NULL,
    edited_at timestamptz, author_id bigint, body text NOT NULL,
    PRIMARY KEY (sent_at, id)
) PARTITION BY RANGE (sent_at);
CREATE INDEX IF NOT EXISTS messages_topic_date ON messages (topic_id, sent_at DESC);
CREATE TABLE IF NOT EXISTS sync_state (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    last_message_id bigint NOT NULL, synced_at timestamptz NOT NULL DEFAULT now()
);
"""

def db():
    return psycopg.connect(
        host="study-schedule-db", dbname="schedule", user="collector",
        password=os.environ["POSTGRES_PASSWORD"], connect_timeout=8,
    )

async def list_topics(client, peer):
    offset_date, offset_id, offset_topic = None, 0, 0
    seen = set()
    while True:
        result = await client(GetForumTopicsRequest(
            peer=peer, offset_date=offset_date, offset_id=offset_id,
            offset_topic=offset_topic, limit=100,
        ))
        topics = [topic for topic in result.topics if isinstance(topic, ForumTopic)]
        for topic in topics:
            if topic.id not in seen:
                seen.add(topic.id)
                yield topic.id, topic.title
        if not topics or len(seen) >= result.count:
            return
        last = topics[-1]
        cursor = (last.date, last.top_message, last.id)
        if cursor == (offset_date, offset_id, offset_topic):
            raise RuntimeError("Telegram repeated the forum pagination cursor")
        offset_date, offset_id, offset_topic = cursor

def expire(conn, cutoff):
    partitions = conn.execute("""
        SELECT child.relname FROM pg_inherits
        JOIN pg_class AS child ON child.oid = inhrelid
        WHERE inhparent = 'messages'::regclass
    """)
    for (name,) in partitions.fetchall():
        if name.startswith("messages_d") and name[len("messages_d"):] < cutoff.strftime("%Y%m%d"):
            conn.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(name)))

def store_batch(conn, batch):
    by_day = {}
    for record in batch:
        by_day.setdefault(record[2].date(), []).append(record)
    with conn.transaction():
        for day, rows in by_day.items():
            partition_name = day.strftime("messages_d%Y%m%d")
            if conn.execute("SELECT to_regclass(%s)", (partition_name,)).fetchone()[0] is None:
                start = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
                conn.execute(sql.SQL(
                    "CREATE TABLE {} PARTITION OF messages FOR VALUES FROM ({}) TO ({})"
                ).format(sql.Identifier(partition_name), sql.Literal(start), sql.Literal(start + timedelta(days=1))))
            with conn.cursor() as cursor:
                cursor.executemany("""
                    INSERT INTO messages (id, topic_id, sent_at, edited_at, author_id, body)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (sent_at, id) DO UPDATE SET
                        topic_id = excluded.topic_id, edited_at = excluded.edited_at,
                        author_id = excluded.author_id, body = excluded.body
                """, rows)

async def sync(client, peer):
    cutoff = datetime.now(UTC) - RETENTION
    topics = [(1, "Общее")]
    topics.extend([topic async for topic in list_topics(client, peer)])
    with db() as conn:
        conn.autocommit = True
        conn.execute(SCHEMA)
        with conn.transaction(), conn.cursor() as cursor:
            expire(conn, cutoff)
            cursor.executemany("""
                INSERT INTO topics (id, title) VALUES (%s, %s)
                ON CONFLICT (id) DO UPDATE SET title = excluded.title
            """, topics)
        previous = conn.execute("SELECT last_message_id FROM sync_state").fetchone()
        highwater = previous[0] if previous else 0
        # Re-read recent messages so edits within that window also reach the archive.
        overlap = datetime.now(UTC) - timedelta(days=2)
        latest = highwater
        batch = []
        count = 0
        async for message in client.iter_messages(peer, wait_time=1):
            if message.date < cutoff or (message.id <= highwater and message.date < overlap):
                break
            latest = max(latest, message.id)
            reply = message.reply_to
            topic = 1
            if getattr(reply, "forum_topic", False):
                topic = reply.reply_to_top_id or reply.reply_to_msg_id or 1
            if message.raw_text:
                batch.append((message.id, topic, message.date, message.edit_date, message.sender_id, message.raw_text))
                count += 1
            if len(batch) >= 100:
                store_batch(conn, batch)
                batch.clear()
        if batch:
            store_batch(conn, batch)
        conn.execute("""
            INSERT INTO sync_state (last_message_id) VALUES (%s)
            ON CONFLICT (singleton) DO UPDATE SET
                last_message_id = excluded.last_message_id, synced_at = now()
        """, (latest,))
        logging.info("Synchronized %s text messages across %s listed topics", count, len(dict(topics)))

async def run(login):
    Path("/data").mkdir(exist_ok=True)
    api = API.TelegramDesktop.Generate(system="linux", unique_id="study-schedule-server-v1")
    client = TelegramClient(SESSION, api, receive_updates=False)
    async with asyncio.timeout(35):
        await client.connect()
    try:
        if login:
            await client.start()
        elif not await client.is_user_authorized():
            raise RuntimeError("Server Telegram session must be authorized first")
        target = int(os.environ["TARGET_CHAT_ID"])
        peer = None
        async for dialog in client.iter_dialogs():
            if dialog.id == target:
                peer = dialog.entity
                break
        if not getattr(peer, "forum", False):
            raise RuntimeError("This account cannot access the configured Telegram forum")
        if login:
            print("Telegram login verified. The configured study forum is accessible.")
            return
        while True:
            await sync(client, peer)
            await asyncio.sleep(300)
    finally:
        await client.disconnect()

if __name__ == "__main__":
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("--login", action="store_true")
    arguments = parser.parse_args()
    asyncio.run(run(arguments.login))

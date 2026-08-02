"""
db.py — 統一 DB 存取層

所有資料庫連線都透過 get_db() 取得。
init_db() 在啟動時建立所有資料表。
"""

import os
import sqlite3
from contextlib import asynccontextmanager

import aiosqlite

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(SCRIPT_DIR, "rp_memory.db")


@asynccontextmanager
async def get_db():
    conn = await aiosqlite.connect(DB_FILE, timeout=10)
    try:
        yield conn
        await conn.commit()
    finally:
        await conn.close()


def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            user_id TEXT,
            user_name TEXT,
            topic TEXT,
            content TEXT,
            context TEXT DEFAULT 'ic',
            mem_type TEXT DEFAULT ''
        )
    """)
    try:
        cursor.execute("ALTER TABLE memories ADD COLUMN context TEXT DEFAULT 'ic'")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE memories ADD COLUMN mem_type TEXT DEFAULT ''")
    except Exception:
        pass
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS world_lore (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT,
            topic TEXT,
            content TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            name TEXT,
            description TEXT,
            quantity INTEGER DEFAULT 1,
            location TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS server_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id TEXT,
            rule_text TEXT,
            added_at TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS quests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            description TEXT,
            status TEXT DEFAULT 'active',
            created_at TEXT,
            updated_at TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS summaries (
            channel_id TEXT PRIMARY KEY,
            channel_name TEXT,
            summary TEXT,
            updated_at TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS character_profiles (
            char_name TEXT PRIMARY KEY,
            gender_age TEXT DEFAULT '',
            intro TEXT DEFAULT '',
            appearance TEXT DEFAULT '',
            items TEXT DEFAULT '',
            experience TEXT DEFAULT '',
            updated_at TEXT DEFAULT ''
        )
    """)
    conn.commit()
    conn.close()
    print(f"[資料庫] SQLite 初始化成功，檔案：{DB_FILE}")

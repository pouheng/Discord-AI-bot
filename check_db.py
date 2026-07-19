import sqlite3
conn = sqlite3.connect("rp_memory.db")
c = conn.cursor()
c.execute("SELECT id, topic, mem_type FROM memories WHERE user_id=? AND topic LIKE ? ORDER BY id DESC LIMIT 5", ('__BOT__', '%人物:%'))
for r in c.fetchall():
    print(r)
conn.close()

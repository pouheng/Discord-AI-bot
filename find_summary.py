import sqlite3
conn = sqlite3.connect("rp_memory.db")
c = conn.cursor()
c.execute("SELECT id, topic, content FROM memories WHERE content LIKE ?", ('%大皮皮鎮%',))
rows = c.fetchall()
for r in rows:
    print(r)
c.execute("SELECT id, topic, content FROM memories WHERE topic LIKE ?", ('%劇情摘要%',))
rows2 = c.fetchall()
for r in rows2:
    print(r)
conn.close()

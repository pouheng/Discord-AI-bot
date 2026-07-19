import sqlite3
conn = sqlite3.connect("rp_memory.db")
c = conn.cursor()
# Find the exact id
c.execute("SELECT id, topic, substr(content,1,50) FROM memories WHERE topic LIKE '%劇%情%摘%要%' OR topic LIKE '%劇情摘要%'")
for r in c.fetchall():
    print('Found:', r)
# Delete it
c.execute("DELETE FROM memories WHERE id=103")
conn.commit()
print('Deleted id=103')
conn.close()

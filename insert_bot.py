import sqlite3
conn = sqlite3.connect("rp_memory.db")
c = conn.cursor()
c.execute("UPDATE memories SET content=? WHERE id=?", ('普雷亞魔法學園的教職員。悟空老師提到：遇到危險時可以告訴嗏姬老師。', 95))
conn.commit()
print('Updated')
c.execute("SELECT id, topic, content FROM memories WHERE id=?", (95,))
print(c.fetchone())
conn.close()

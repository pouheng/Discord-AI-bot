import sqlite3, datetime
conn = sqlite3.connect("rp_memory.db")
c = conn.cursor()
now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
c.execute(
    "INSERT INTO memories (timestamp, user_id, user_name, topic, content, context, mem_type) VALUES (?, ?, ?, ?, ?, ?, ?)",
    (now, '__BOT__', '澪', '場景:學園噴泉', '學校中央有一個噴泉，是本作經常登場的場景。經常作為角色互動與劇情推進的地點。', 'ic', '')
)
conn.commit()
print('Inserted id:', c.lastrowid)
conn.close()

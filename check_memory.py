import sqlite3

conn = sqlite3.connect("rp_memory.db")
cur = conn.execute(
    "SELECT topic, content FROM memories WHERE user_id='__BOT__' AND (topic LIKE '%義手%' OR content LIKE '%義手%' OR topic LIKE '%機械手%' OR content LIKE '%機械手%')"
)
rows = cur.fetchall()
for r in rows:
    print(f"  [{r[0]}] {r[1][:200]}")
if not rows:
    print("  （無相關記憶）")
    cur2 = conn.execute(
        "SELECT topic, content FROM world_lore WHERE topic LIKE '%義手%' OR content LIKE '%義手%'"
    )
    rows2 = cur2.fetchall()
    for r in rows2:
        print(f"  [世界觀] [{r[0]}] {r[1][:200]}")
    if not rows2:
        print("  （world_lore 也無相關記錄）")
        # Check profile
        cur3 = conn.execute(
            "SELECT char_name, appearance FROM character_profiles WHERE appearance LIKE '%義手%' OR items LIKE '%義手%'"
        )
        rows3 = cur3.fetchall()
        for r in rows3:
            print(f"  [角色檔案] {r[0]}: {r[1][:200]}")
        if not rows3:
            print("  （角色檔案也無相關記錄）")
conn.close()

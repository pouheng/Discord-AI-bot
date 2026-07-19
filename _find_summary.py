import sqlite3

conn = sqlite3.connect("rp_memory.db")
c = conn.cursor()
# Check summaries table
c.execute("SELECT channel_id, channel_name, substr(summary,1,100) FROM summaries")
rows = c.fetchall()
print("=== summaries table ===")
for r in rows:
    print(r)

# Check memories for summary-like content
c.execute(
    "SELECT id, topic, substr(content,1,120) FROM memories WHERE topic LIKE '劇情摘要:%' OR topic LIKE '故事摘要:%'"
)
rows = c.fetchall()
print("=== summary memories ===")
for r in rows:
    print(r)

# Search by keywords
c.execute(
    "SELECT id, topic, substr(content,1,120) FROM memories WHERE content LIKE '%大皮%' OR content LIKE '%頭皮%'"
)
rows = c.fetchall()
print("=== keyword match ===")
for r in rows:
    print(r)

conn.close()

import sqlite3, datetime
conn = sqlite3.connect("rp_memory.db")
c = conn.cursor()
now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
locations = [
    ('場景:噴泉公園', '位於整張地圖的正中心，呈現橢圓形，是連接各區的樞紐。'),
    ('場景:學園大門', '位於地圖正下方邊緣的中央。'),
    ('場景:保安室', '緊鄰學園大門的左側。'),
    ('場景:教學樓', '位於地圖正上方，噴泉公園的正北側（北部教學區）。'),
    ('場景:行政樓', '位於地圖左下角、大門左上方（西部行政與體育區）。'),
    ('場景:小賣部', '位於行政樓的正上方。'),
    ('場景:體育館', '位於地圖的左上角（西部行政與體育區）。'),
    ('場景:食堂', '一棟縱向建築，位於中央噴泉公園與右側宿舍區之間（東部生活與餐飲區）。'),
    ('場景:宿舍區', '位於地圖右側，分為上下兩部分：下方為女宿舍，上方為男宿舍。男女宿舍之間夾著兩個公共空間：左側為學習空間，右側為交誼廳。'),
    ('場景:活動中心', '位於地圖的右上角。'),
    ('場景:學習空間', '位於男女宿舍之間左側的公共空間。'),
    ('場景:交誼廳', '位於男女宿舍之間右側的公共空間。'),
]
for topic, content in locations:
    c.execute(
        "INSERT INTO memories (timestamp, user_id, user_name, topic, content, context, mem_type) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (now, '__BOT__', '澪', topic, content, 'ic', '真實')
    )
    print(f"Inserted: {topic}")
conn.commit()
conn.close()

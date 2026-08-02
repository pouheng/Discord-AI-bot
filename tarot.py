"""
塔羅牌占卜模組
- 關鍵詞偵測觸發抽牌
- /tarot 斜線指令
- 78 張偉特塔羅牌（22 張大阿爾克那 + 56 張小阿爾克那）
- 支援單張 / 三張牌陣
- AI 生成解讀
- 自訂牌面圖片
"""

import discord
from discord.ext import commands
from discord import app_commands
from openai import AsyncOpenAI
import random
import os
import re
import datetime
from dotenv import load_dotenv

load_dotenv()
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TAROT_IMAGE_DIR = os.path.join(SCRIPT_DIR, "tarot_images")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
_client = (
    AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com/v1")
    if DEEPSEEK_API_KEY
    else None
)

os.makedirs(TAROT_IMAGE_DIR, exist_ok=True)

TRIGGER_KEYWORDS = ["抽牌", "塔羅", "塔羅牌", "占卜", "tarot", "抽一張", "抽三張"]

SUIT_NAMES = {"wands": "權杖", "cups": "聖杯", "swords": "寶劍", "pentacles": "錢幣"}
SUIT_EMOJIS = {"wands": "🌿", "cups": "🏆", "swords": "⚔️", "pentacles": "🪙"}
SUIT_COLORS = {
    "wands": 0xE67E22,
    "cups": 0x3498DB,
    "swords": 0xE74C3C,
    "pentacles": 0x2ECC71,
}
RANK_NAMES = {1: "Ace", 11: "Page", 12: "Knight", 13: "Queen", 14: "King"}
RANK_NAMES_ZH = {1: "王牌", 11: "侍從", 12: "騎士", 13: "王后", 14: "國王"}

for r in range(2, 11):
    RANK_NAMES[r] = str(r)
    RANK_NAMES_ZH[r] = str(r)

MAJOR_ARCANA = [
    # (number, name_en, name_zh, meaning_upright, meaning_reversed, keywords)
    (
        0,
        "The Fool",
        "愚者",
        "新的開始、冒險、天真、無限可能",
        "魯莽、冒險、不切實際、停滯",
        "新的旅程, 天真, 自由",
    ),
    (
        1,
        "The Magician",
        "魔術師",
        "創造力、技能、自信、掌握資源",
        "操縱、欺騙、浪費天賦、缺乏方向",
        "創造力, 掌握, 行動",
    ),
    (
        2,
        "The High Priestess",
        "女祭司",
        "直覺、潛意識、內在智慧、神秘",
        "秘密、隱藏資訊、直覺被忽略、膚淺",
        "直覺, 神秘, 內在",
    ),
    (
        3,
        "The Empress",
        "女皇",
        "豐收、滋養、大自然、母性",
        "依賴、空虛、創造力受阻、過度保護",
        "豐饒, 養育, 自然",
    ),
    (
        4,
        "The Emperor",
        "皇帝",
        "權威、穩定、結構、保護",
        "專制、僵化、濫權、缺乏紀律",
        "權威, 秩序, 穩定",
    ),
    (
        5,
        "The Hierophant",
        "教皇",
        "傳統、靈性導師、信仰、教育",
        "叛逆、挑戰權威、不合群、過時觀念",
        "傳統, 教導, 精神",
    ),
    (
        6,
        "The Lovers",
        "戀人",
        "愛情、和諧、選擇、價值觀",
        "分離、衝突、價值觀衝突、感情考驗",
        "愛情, 選擇, 結合",
    ),
    (
        7,
        "The Chariot",
        "戰車",
        "勝利、意志力、決心、克服障礙",
        "方向迷失、侵略性、失控、缺乏自律",
        "勝利, 意志, 征服",
    ),
    (
        8,
        "Strength",
        "力量",
        "內在力量、勇氣、耐心、同理心",
        "軟弱、缺乏自信、恐懼、暴力傾向",
        "勇氣, 內力, 溫柔",
    ),
    (
        9,
        "The Hermit",
        "隱士",
        "內省、追尋智慧、獨處、指引",
        "孤獨、孤立、迷失方向、退縮",
        "內省, 智慧, 孤獨",
    ),
    (
        10,
        "Wheel of Fortune",
        "命運之輪",
        "轉變、循環、命運、好運到來",
        "厄運、抗拒改變、失控、連鎖反應",
        "命運, 轉變, 循環",
    ),
    (
        11,
        "Justice",
        "正義",
        "公正、誠實、因果報應、平衡",
        "不公、欺騙、逃避責任、失衡",
        "公正, 平衡, 真相",
    ),
    (
        12,
        "The Hanged Man",
        "倒吊人",
        "暫停、放下、新視角、犧牲",
        "拖延、抗拒、不願放手、徒勞",
        "放下, 懸停, 換位",
    ),
    (
        13,
        "Death",
        "死神",
        "結束、轉變、放下過去、新生",
        "抗拒改變、停滯、恐懼結束、循環",
        "終結, 轉變, 重生",
    ),
    (
        14,
        "Temperance",
        "節制",
        "平衡、調和、耐心、中庸之道",
        "失衡、衝突、缺乏節制、極端",
        "平衡, 調和, 中庸",
    ),
    (
        15,
        "The Devil",
        "惡魔",
        "束縛、物質主義、慾望、上癮",
        "解放、戒斷、看清真相、覺醒",
        "束縛, 慾望, 執著",
    ),
    (
        16,
        "The Tower",
        "高塔",
        "劇變、崩潰、覺醒、顛覆",
        "避免災難、延遲崩壞、恐懼改變",
        "崩壞, 覺醒, 劇變",
    ),
    (
        17,
        "The Star",
        "星星",
        "希望、靈感、平靜、療癒",
        "絕望、失去方向、缺乏靈感、失望",
        "希望, 靈感, 療癒",
    ),
    (
        18,
        "The Moon",
        "月亮",
        "幻覺、恐懼、焦慮、潛意識",
        "釋放恐懼、釐清真相、克服幻覺",
        "幻象, 恐懼, 直覺",
    ),
    (
        19,
        "The Sun",
        "太陽",
        "快樂、成功、活力、自信",
        "暫時沮喪、快樂被遮蔽、過度樂觀",
        "喜悅, 成功, 活力",
    ),
    (
        20,
        "Judgement",
        "審判",
        "重生、覺醒、寬恕、召喚",
        "自責、拒絕成長、判斷失誤",
        "覺醒, 審判, 重生",
    ),
    (
        21,
        "The World",
        "世界",
        "完成、成就、圓滿、旅程終點",
        "未完成、尋找終點、停滯、捷徑",
        "完成, 圓滿, 成就",
    ),
]


def _build_minor_arcana():
    cards = []
    suits = {"wands": "權杖", "cups": "聖杯", "swords": "寶劍", "pentacles": "錢幣"}
    upright_meanings = {
        "wands": [
            "靈感",
            "行動",
            "動力",
            "熱情",
            "冒險",
            "勇氣",
            "競爭",
            "進展",
            "擴張",
            "遠見",
            "新消息",
            "旅行",
            "勇敢",
            "領導力",
        ],
        "cups": [
            "新的感情",
            "結合",
            "喜悅",
            "沉思",
            "失落",
            "回憶",
            "選擇",
            "放下",
            "滿足",
            "幸福",
            "新朋友",
            "邀請",
            "同理心",
            "成熟",
        ],
        "swords": [
            "清晰",
            "決定",
            "分離",
            "休息",
            "衝突",
            "解決",
            "策略",
            "限制",
            "焦慮",
            "結束",
            "監視",
            "爭論",
            "快速",
            "權威",
        ],
        "pentacles": [
            "開始",
            "平衡",
            "努力",
            "節約",
            "困境",
            "分享",
            "計劃",
            "技能",
            "獎勵",
            "財富",
            "抱負",
            "謹慎",
            "務實",
            "精通",
        ],
    }
    reversed_meanings = {
        "wands": [
            "缺乏靈感",
            "拖延",
            "動力不足",
            "熄火",
            "膽怯",
            "退縮",
            "失職",
            "阻礙",
            "疲憊",
            "短視",
            "壞消息",
            "延誤",
            "魯莽",
            "獨斷",
        ],
        "cups": [
            "感情封閉",
            "分離",
            "空虛",
            "忽視",
            "執著",
            "放縱",
            "混亂",
            "沈溺",
            "不滿",
            "不和",
            "孤僻",
            "排斥",
            "冷漠",
            "不成熟",
        ],
        "swords": [
            "混亂",
            "優柔寡斷",
            "否認",
            "疲倦",
            "和解",
            "拖延",
            "急躁",
            "脆弱",
            "失眠",
            "倖存",
            "脆弱",
            "和平",
            "緩慢",
            "濫權",
        ],
        "pentacles": [
            "浪費",
            "失衡",
            "懶惰",
            "揮霍",
            "舒適",
            "自私",
            "中斷",
            "業餘",
            "貪婪",
            "損失",
            "輕率",
            "保守",
            "不切實際",
            "不穩定",
        ],
    }
    for suit_key, suit_zh in suits.items():
        for rank in range(1, 15):
            rank_en = RANK_NAMES[rank]
            rank_zh = RANK_NAMES_ZH[rank]
            if rank == 1:
                name_en = f"Ace of {suit_key.capitalize()}"
                name_zh = f"{suit_zh}王牌"
            elif rank == 11:
                name_en = f"Page of {suit_key.capitalize()}"
                name_zh = f"{suit_zh}侍從"
            elif rank == 12:
                name_en = f"Knight of {suit_key.capitalize()}"
                name_zh = f"{suit_zh}騎士"
            elif rank == 13:
                name_en = f"Queen of {suit_key.capitalize()}"
                name_zh = f"{suit_zh}王后"
            elif rank == 14:
                name_en = f"King of {suit_key.capitalize()}"
                name_zh = f"{suit_zh}國王"
            else:
                name_en = f"{rank} of {suit_key.capitalize()}"
                name_zh = f"{suit_zh}{rank_zh}"
            idx = rank - 1
            cards.append(
                (
                    f"{suit_key}_{rank}",
                    name_en,
                    name_zh,
                    upright_meanings[suit_key][idx % len(upright_meanings[suit_key])],
                    reversed_meanings[suit_key][idx % len(reversed_meanings[suit_key])],
                    suit_key,
                )
            )
    return cards


MINOR_ARCANA = _build_minor_arcana()


class TarotCard:
    def __init__(self, card_id, name_en, name_zh, meaning_up, meaning_rev, suit=None):
        self.card_id = card_id
        self.name_en = name_en
        self.name_zh = name_zh
        self.meaning_upright = meaning_up
        self.meaning_reversed = meaning_rev
        self.suit = suit
        self.is_major = suit is None or suit == "major"
        self.reversed = False
        self.image_path = self._find_image()

    def _find_image(self):
        for ext in [".png", ".jpg", ".jpeg", ".gif"]:
            path = os.path.join(TAROT_IMAGE_DIR, f"{self.card_id}{ext}")
            if os.path.exists(path):
                return path
        return None

    def has_image(self):
        return self.image_path is not None and os.path.exists(self.image_path)

    def get_display_name(self):
        prefix = "逆位 " if self.reversed else ""
        return f"{prefix}{self.name_zh}（{self.name_en}）"

    def get_meaning(self):
        return self.meaning_reversed if self.reversed else self.meaning_upright

    def get_color(self):
        if self.is_major:
            return 0x9B59B6
        return SUIT_COLORS.get(self.suit, 0x95A5A6)


def _build_all_cards():
    cards = {}
    for num, en, zh, up, rev, kw in MAJOR_ARCANA:
        cid = f"major_{num:02d}"
        cards[cid] = TarotCard(cid, en, zh, up, rev, "major")
    for cid, en, zh, up, rev, suit in MINOR_ARCANA:
        cards[cid] = TarotCard(cid, en, zh, up, rev, suit)
    return cards


ALL_CARDS = _build_all_cards()
CARD_LIST = list(ALL_CARDS.values())

CUSTOM_CARDS: dict[str, TarotCard] = {}

last_tarot: dict | None = None
"""Store the most recently drawn tarot card(s) for in-character reference."""


def register_custom_card(
    card_id: str,
    name_en: str,
    name_zh: str,
    meaning_up: str,
    meaning_rev: str,
    suit: str | None = None,
) -> TarotCard:
    """Register a new custom tarot card dynamically.

    Args:
        card_id: Unique identifier (e.g. 'custom_mycard').
        name_en: English name.
        name_zh: Chinese name.
        meaning_up: Upright meaning text.
        meaning_rev: Reversed meaning text.
        suit: Suit category (None/'major' for major arcana, or a suit key).

    Returns:
        The newly created TarotCard.

    Raises:
        ValueError: If card_id already exists in built-in or custom cards.
    """
    cid = card_id.strip()
    if not cid:
        raise ValueError("card_id must not be empty")
    if cid in ALL_CARDS:
        raise ValueError(f"Card '{cid}' already exists (built-in or custom)")
    card = TarotCard(cid, name_en, name_zh, meaning_up, meaning_rev, suit)
    CUSTOM_CARDS[cid] = card
    ALL_CARDS[cid] = card
    CARD_LIST.append(card)
    print(f"[塔羅] 已註冊自訂牌: {name_zh} ({name_en}) [id={cid}]")
    return card


def remove_custom_card(card_id: str) -> bool:
    """Remove a previously registered custom card.

    Args:
        card_id: The card's unique identifier.

    Returns:
        True if removed, False if not found or not a custom card.
    """
    if card_id not in CUSTOM_CARDS:
        return False
    card = CUSTOM_CARDS.pop(card_id)
    ALL_CARDS.pop(card_id, None)
    if card in CARD_LIST:
        CARD_LIST.remove(card)
    print(f"[塔羅] 已移除自訂牌: {card.name_zh} ({card.name_en}) [id={card_id}]")
    return True


def check_tarot_trigger(text: str) -> bool:
    text_lower = text.lower()
    for kw in TRIGGER_KEYWORDS:
        if kw in text_lower:
            return True
    return False


def draw_cards(count: int = 1) -> list[TarotCard]:
    selected = random.sample(CARD_LIST, count)
    for card in selected:
        card.reversed = random.random() < 0.3
    return selected


def build_spread_description(cards: list[TarotCard], spread_type: str) -> str:
    if spread_type == "三張":
        positions = ["過去", "現在", "未來"]
        lines = [f"**{spread_type}牌陣**"]
        for i, card in enumerate(cards):
            pos = positions[i] if i < len(positions) else f"位置{i + 1}"
            lines.append(f"【{pos}】{card.get_display_name()}")
            lines.append(f"　意義：{card.get_meaning()}")
        return "\n".join(lines)
    elif spread_type == "十字":
        positions = ["現狀", "阻礙", "潛意識", "建議", "發展"]
        lines = [f"**{spread_type}牌陣**"]
        for i, card in enumerate(cards):
            pos = positions[i] if i < len(positions) else f"位置{i + 1}"
            lines.append(f"【{pos}】{card.get_display_name()}")
            lines.append(f"　意義：{card.get_meaning()}")
        return "\n".join(lines)
    else:
        card = cards[0]
        return f"**單張牌**\n{card.get_display_name()}\n意義：{card.get_meaning()}"


async def interpret_tarot(
    cards: list[TarotCard], user_message: str, spread_type: str, user_name: str
) -> str:
    if not _client:
        return "（AI 解讀服務未啟用，請檢查 API 金鑰設定）"

    card_lines = []
    if spread_type == "三張":
        positions = ["過去", "現在", "未來"]
        for i, card in enumerate(cards):
            pos = positions[i] if i < len(positions) else f"位置{i + 1}"
            card_lines.append(
                f"- 【{pos}】{card.name_zh}（{card.name_en}）{'【逆位】' if card.reversed else '【正位】'}: {card.get_meaning()}"
            )
    elif spread_type == "十字":
        positions = ["現狀", "阻礙", "潛意識", "建議", "發展"]
        for i, card in enumerate(cards):
            pos = positions[i] if i < len(positions) else f"位置{i + 1}"
            card_lines.append(
                f"- 【{pos}】{card.name_zh}（{card.name_en}）{'【逆位】' if card.reversed else '【正位】'}: {card.get_meaning()}"
            )
    else:
        card = cards[0]
        card_lines.append(
            f"- {card.name_zh}（{card.name_en}）{'【逆位】' if card.reversed else '【正位】'}: {card.get_meaning()}"
        )

    cards_text = "\n".join(card_lines)

    prompt = f"""你是一個塔羅牌占卜師。請根據使用者當前的處境或問題，結合抽出的牌進行有洞察力的解讀。

【使用者名稱】
{user_name}

【使用者的訊息／問題】
{user_message}

【牌陣類型】
{spread_type}

【抽出的牌】
{cards_text}

請以溫暖、有洞察力的語氣給出解讀。內容應包括：
1. 每張牌在對應位置的意義
2. 牌與牌之間的關聯（多張牌時）
3. 給使用者的建議或指引

請用繁體中文，保持占卜師的專業與溫暖。不要過度恐嚇或過度樂觀，保持平衡的觀點。"""

    try:
        resp = await _client.chat.completions.create(
            model="deepseek-v4-flash",
            messages=[
                {
                    "role": "system",
                    "content": "你是一個溫暖而有洞察力的塔羅牌占卜師。用繁體中文回答。",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            max_tokens=1000,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        return f"（解讀服務暫時無法使用：{e}）"


def build_tarot_embed(
    cards: list[TarotCard], spread_type: str, interpretation: str, user_name: str
) -> discord.Embed:
    if spread_type == "三張":
        title = "🔮 三張牌占卜"
        desc_parts = ["過去 · 現在 · 未來"]
    elif spread_type == "十字":
        title = "🔮 十字牌陣"
        desc_parts = ["現狀 · 阻礙 · 潛意識 · 建議 · 發展"]
    else:
        title = "🔮 塔羅占卜"
        desc_parts = [f"為 {user_name} 抽了一張牌"]

    desc = "\n".join(desc_parts)
    embed = discord.Embed(title=title, description=desc, color=0x9B59B6)

    if spread_type == "單張":
        card = cards[0]
        card_title = f"{'🙃 ' if card.reversed else '✨ '}{card.name_zh}"
        card_desc = f"**{card.name_en}**\n{'逆位' if card.reversed else '正位'}：{card.get_meaning()}"
        embed.add_field(name=card_title, value=card_desc, inline=False)
    elif spread_type == "三張":
        positions = ["過去", "現在", "未來"]
        for i, card in enumerate(cards):
            pos = positions[i] if i < len(positions) else f"位置{i + 1}"
            name = f"{pos}：{'🙃 ' if card.reversed else '✨ '}{card.name_zh}"
            value = f"**{card.name_en}**\n{card.get_meaning()}"
            embed.add_field(name=name, value=value, inline=True)
    elif spread_type == "十字":
        positions = ["現狀", "阻礙", "潛意識", "建議", "發展"]
        for i, card in enumerate(cards):
            pos = positions[i] if i < len(positions) else f"位置{i + 1}"
            name = f"{pos}：{'🙃 ' if card.reversed else '✨ '}{card.name_zh}"
            value = f"**{card.name_en}**\n{card.get_meaning()}"
            embed.add_field(name=name, value=value, inline=True)

    if interpretation:
        text = interpretation[:1024] if len(interpretation) > 1024 else interpretation
        embed.add_field(name="📖 塔羅解讀", value=text, inline=False)
    else:
        embed.add_field(name="📖 塔羅解讀", value="（解讀產生中…）", inline=False)

    embed.set_footer(
        text=f"{user_name} · {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    return embed


async def generate_placeholder_images():
    """Generate simple placeholder card images using PIL."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("[塔羅] PIL 未安裝，無法產生預覽圖")
        return

    font_size = 16
    font = None
    cjk_fonts = [
        "msjh.ttc",      # Microsoft JhengHei (繁中)
        "msyh.ttc",      # Microsoft YaHei (簡中)
        "simsun.ttc",    # SimSun
        "mingliu.ttc",   # PMingLiU
        "arial.ttf",
    ]
    for name in cjk_fonts:
        try:
            font = ImageFont.truetype(name, font_size)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()

    for card_id, card in ALL_CARDS.items():
        if card.has_image():
            continue
        img = Image.new("RGB", (140, 220), (30, 30, 50))
        draw = ImageDraw.Draw(img)

        if card.is_major:
            color = (155, 89, 182)
        elif card.suit == "wands":
            color = (230, 126, 34)
        elif card.suit == "cups":
            color = (52, 152, 219)
        elif card.suit == "swords":
            color = (231, 76, 60)
        elif card.suit == "pentacles":
            color = (46, 204, 113)
        else:
            color = (100, 100, 100)

        draw.rectangle([2, 2, 137, 217], outline=color, width=2)

        name = card.name_zh
        lines = []
        line = ""
        for char in name:
            test = line + char
            bbox = draw.textbbox((0, 0), test, font=font)
            if bbox[2] - bbox[0] > 120:
                lines.append(line)
                line = char
            else:
                line = test
        if line:
            lines.append(line)

        y = 30
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            tw = bbox[2] - bbox[0]
            draw.text(((140 - tw) // 2, y), line, fill=(255, 255, 255), font=font)
            y += 22

        path = os.path.join(TAROT_IMAGE_DIR, f"{card_id}.png")
        img.save(path)
        card.image_path = path
    print(f"[塔羅] 已產生 {len(ALL_CARDS)} 張預覽圖")


async def handle_tarot_message(message: discord.Message, bot: commands.Bot):
    """Handle message-triggered tarot reading."""
    content = re.sub(r"<@&?\d+>", "", message.content).strip().lower()

    if "十字" in content or "五張" in content:
        spread = "十字"
        count = 5
    elif "三張" in content or "抽三" in content:
        spread = "三張"
        count = 3
    else:
        spread = "單張"
        count = 1

    async with message.channel.typing():
        cards = draw_cards(count)
        interpretation = await interpret_tarot(
            cards, message.content, spread, message.author.display_name
        )
        embed = build_tarot_embed(
            cards, spread, interpretation, message.author.display_name
        )

        files = []
        for card in cards:
            if card.has_image():
                files.append(
                    discord.File(card.image_path, filename=f"{card.card_id}.png")
                )

        if files:
            # attach first card image to embed
            embed.set_image(url=f"attachment://{files[0].filename}")

        await message.reply(embed=embed, files=files)


def register_tarot(bot: commands.Bot):
    """Register tarot slash command."""

    @bot.tree.command(name="tarot", description="🔮 塔羅牌占卜 — 抽牌並獲得 AI 解讀")
    @app_commands.describe(spread="牌陣類型", question="你想問的問題（選擇性）")
    @app_commands.choices(
        spread=[
            app_commands.Choice(name="單張牌", value="單張"),
            app_commands.Choice(name="三張牌（過去·現在·未來）", value="三張"),
            app_commands.Choice(name="十字牌陣（五張）", value="十字"),
        ]
    )
    async def cmd_tarot(
        interaction: discord.Interaction,
        spread: str = "單張",
        question: str | None = None,
    ):
        await interaction.response.defer()

        count = {"單張": 1, "三張": 3, "十字": 5}.get(spread, 1)
        user_message = question or f"請幫我抽{spread}"
        user_name = interaction.user.display_name

        cards = draw_cards(count)
        global last_tarot
        last_tarot = {
            "cards": cards,
            "who_for": interaction.user.display_name,
            "spread": spread,
            "timestamp": datetime.datetime.now().timestamp(),
        }
        interpretation = await interpret_tarot(cards, user_message, spread, user_name)
        embed = build_tarot_embed(cards, spread, interpretation, user_name)

        files = []
        for card in cards:
            if card.has_image():
                files.append(
                    discord.File(card.image_path, filename=f"{card.card_id}.png")
                )

        if files:
            embed.set_image(url=f"attachment://{files[0].filename}")

        await interaction.followup.send(embed=embed, files=files)

    print("[塔羅] /tarot 指令已註冊")


async def ensure_images():
    """Ensure placeholder images exist if no real images are present."""
    has_any = any(card.has_image() for card in ALL_CARDS.values())
    if not has_any:
        await generate_placeholder_images()

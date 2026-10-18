# 招式對撞系統（Skill Collision System）規格書

版本：v2.0（草案）
日期：2026-10-12
狀態：待審查

---

## 1. Background（背景）

現有 RP bot（澪）已具備記憶、世界觀、塔羅牌、圖片分析等功能，但**戰鬥/對撞完全仰賴人工（中之人）自行協調**。玩家之間的招式對撞缺乏統一的數值化規則，也無法公開驗證結果。

本系統導入「招式對撞系統草稿.txt」的四張資料表與四條公式，建立：

1. **結構化資料層**：角色、招式、結界資料寫入本地 SQLite，並即時鏡像到公開 Google Sheets。
2. **自然語言寫入**：管理員透過 HTML 管理頁面以自然語言描述，由 LLM 依「寫入規範」解析成結構化資料。
3. **AI 計算引擎**：實際對撞時，AI 依「使用說明」與公開公式執行計算，產出可驗證結果。

### 核心原則（使用者要求）

- **表格只存資料，不存定義解釋**：公式、規則、使用說明全部放在程式與 prompt 內，不塞進表格欄位。
- **計算公式與輸入規範公開**：但不是放在表格裡；可用指令（如 `/collide rule`）查看最新版本。
- **Google Sheets 為唯讀公開鏡像**：本地 SQLite 是唯一資料源，Sheets 只展示，不提供回寫。

---

## 2. Goals（目標）

- 管理員可用 HTML 網頁以自然語言寫入角色、招式、結界資料。
- 寫入時有 LLM 規範約束（評級範圍、數值範圍、必要欄位），違規自動修正或拒絕。
- 每次資料寫入後自動同步到公開 Google Sheets（單一試算表、多工作表）。
- 任何人可透過公開 Sheets 連結查看角色、招式、結界、對撞紀錄。
- 實際對撞時，AI 依草稿公式與四階判定計算，產出含思維鏈的結果。
- **對撞只靠 `/clash <討論串>` 觸發**：不輸入角色名、不輸入招式，AI 自行從討論串判斷「誰對誰、各自出什麼招」。
- **攻守由招式威力動態決定**：雙方各自選擇招式並描述完招式後執行 `/clash`；系統分別計算雙方最終威力（Atk_BP），**威力較大者為攻擊方，威力較小者為防禦方**——不依閱讀順序、不仰賴玩家指定攻守。判定即「攻擊 vs 防禦」比較。
- **玩家自行出招，AI 記錄**：玩家在扮演中自然描述自己的行動與招式（不須額外申報）；AI 從討論串**記錄**玩家實際使用的招式，用於熟練度成長與計算。AI 不替玩家決定招式。
- 對撞結果需中之人（發送方）按鈕確認或重試，確認後以文風輸出對撞實際文本。**結果與確認按鈕直接發布在該討論串內，所有人可見（公開透明）。**
- **樓層成長系統**：每個討論串 = 一個樓層；由 `/grow` 依草案成長規範總結成長，數值更新只套用一次且可追溯。
- **能力熟練度**：能力可自行成長（proficiency 0~100），同能力招式共用一份，使用後由樓層總結提升，乘算進攻擊力。
- **完整審計**：樓層所有數值加減（old→new→delta）與 AI Action Log 全部儲存、可查、可人工審查修改。
- 公式與輸入規範可用指令查看最新版本。

---

## 3. Non-Goals（明確不做）

- **不支援 Sheets → DB 回寫**（單向唯讀）。
- **不實作完整回合制戰鬥系統**；只處理單次招式對撞判定。
- **不做玩家自行編輯**；寫入僅限管理員。
- **不在表格內存放公式/規則/定義**（依核心原則）。
- **不自動變更資料**：數值變動一律透過 `/grow` 依成長規範套用並寫入審計 log，系統不在計算時擅自改數值。
- **不處理多人混戰**（一次對撞限兩方）。團戰支援列為**未來擴充**：預留「先拆解成多組 1v1 對撞再合併結果」的架構，但不納入 v1。
- **不替玩家決定招式、攻守或動作**：玩家自行描述行動與招式，AI 只記錄、不代決；玩家也不可預設其他玩家的動作。

---

## 4. User Stories（使用者故事）

### U1 管理員建立角色
作為管理員，我在 HTML 頁面輸入「新增角色：凌空，所屬星辰學院，人類，力量B 敏捷A 智力C 體質B 魅力D 幸運E，MaxMP 45，武藝值 60，實戰經驗 15，非教師」，系統自動解析並寫入角色表，並同步到 Sheets。

### U2 管理員建立能力與招式
作為管理員，我輸入「新增能力：蒼雷，凌空的能力，原理是壓縮雷元素形成電擊，可擴展應用」系統解析寫入能力表；再輸入「新增招式：蒼雷轟，屬蒼雷能力的擴展應用，個人魔法，自然系，雷屬性，魔力消耗 18，威力基數 7.5，附加麻痺」，系統解析寫入招式表並同步。

### U3 公開查看
作為玩家，我打開公開 Sheets 連結，即可看到所有角色、招式、結界、對撞紀錄。

### U4 查閱規範
作為玩家，我輸入 `/collide rule`，即可看到最新版的公式與輸入規範（不依賴表格內容）。

### U5 對撞計算
作為中之人，我在目標討論串中執行 `/clash`（可選附討論串，省略則用當前頻道/討論串），系統：
1. 讀取討論串了解當前戰局：**AI 自行判斷誰與誰在對抗、各自出什麼招**；
2. 預處理審查意圖（有無越權操作他人角色/主張結果），並判斷動作意圖分類（攻擊性 / 非對抗性：純迴避或純防禦，見 6.3）；
3. **從討論串記錄雙方實際使用的招式**（玩家自行描述的行動），抓取雙方資料與公式執行數學計算：雙方皆攻擊性 → **比較雙方最終威力，威力大者為攻擊方、小者為防禦方**（玩家無招式時用普通攻擊）；一方非對抗 → 走純迴避/純防禦判定（不計算該方 Atk_BP）；雙方皆非對抗 → 回報「無對撞發生」；
   3b. **動作型別辨識（`action_type`）**：Phase A 須讀懂討論串，區分「招式（skill）/ 結界張開（domain）/ 普通攻擊（melee）」。**一方張開結界時 skill 欄位填結界名稱並標記 `action_type = "domain"`**；雙方皆張開結界 → 以結界覺醒（Domain_Mod）比較，差距 < 0.3 觸發結界交疊（融合）、≥ 0.3 覆蓋。**結界張開不會被當成未登記招式而落回徒手普通攻擊**（見 6.1 Domain_BP）；
4. 輸出含思維鏈與計算過程的結果到**該討論串（公開，所有人可見）**，並附確認/重試按鈕——此為**草稿計算**，**尚未寫入對撞紀錄**（`status = pending`）；
5. 提供「確認 / 重試（可補充說明）/ 取消」按鈕；確認按鈕帶二次確認防誤按；
6. **按鈕權限歸屬**：確認/重試/取消按鈕的 `custom_id` **編碼發送者 user_id**；View 回調時校驗互動者是否為該次 `/clash` 的發送者（中之人），非發送者一律回覆「只有發送者可以操作此結果」（沿用 main.py / autopilot.py 既有「不是你的操作」權限 pattern）；
7. **確認** → 寫入對撞紀錄（`status = active`）並以文風生成對撞文本發布；**取消** → 清除該草稿（`pending` 紀錄刪除/標記作廢，避免洗版）；**重試** → 不寫入 log，直接重新計算；
8. 事後發現誤按/判斷瑕疵：中之人（發送者）或管理員執行 `/clash undo <battle_id>` 撤銷作廢（見 6.4）。

### U6 動作預設規則
- **玩家不可預設其他玩家的動作或意圖**：中之人只能描述自己角色的行動。
- **魔物/反派可被玩家設定動作**：若玩家未設定魔物的動作，由 **AI 做預設**。
- **反派識別**：AI 依角色表的 `is_villain = 1` 判斷哪些角色是「反派」；未標記的敵對 NPC 依種族/魔物評級識別。
- **AI 永不預設玩家角色的意圖或動作**：只能記錄玩家已描述的動作。

### U7 樓層總結成長
作為中之人，當一個討論串的劇情告一段落，我執行 `/grow`，系統依草案成長規範（主線/決鬥/結界/委託）為該樓層套用數值變動（經驗、能力熟練度、結界精度）。**首次 `/grow` 為總結成長；同一樓層再次 `/grow` 為更新/重跑，以系統生成的 `floor_event_id` 比對既有 log 覆寫，絕不重複疊加**（見 5.5 防呆機制）。

### U8 樓層審計與人工修正
作為管理員，我執行 `/clash log <討論串>` 查看該樓層所有 AI Action Log 與數值加減（old→new→delta）。發現異常時，可在管理頁面修正數值——修正也記錄一筆新 log，所有變更可追溯。

---

## 5. 資料模型（資料表定義）

> 表格**只存資料**。評級定義、種族修正係數表、公式等全部在程式碼/prompt 內。

### 5.1 Character Data Schema（角色基礎檔案表）

本地表名：`collide_characters`

| 欄位 | 型別 | 範圍/約束 | 草稿對應 |
|---|---|---|---|
| id | INTEGER PK AUTOINCREMENT | — | — |
| name | TEXT UNIQUE NOT NULL | 角色名 | 角色名 |
| affiliation | TEXT | 所屬 | 所屬 |
| race | TEXT | 種族/化形形態 | 種族/化形形態 |
| school_rank | TEXT | 學校/魔物評級 | 學校/魔物評級 |
| stat_strength | TEXT | 評級 A~S | 六項能力值_力量 |
| stat_strength_hidden | REAL | 隱藏分數（評級×種族修正） | 力量隱藏分數 |
| stat_agility | TEXT | 評級 A~S | 六項能力值_敏捷 |
| stat_agility_hidden | REAL | 隱藏分數 | 敏捷隱藏分數 |
| stat_intelligence | TEXT | 評級 A~S | 六項能力值_智力 |
| stat_intelligence_hidden | REAL | 隱藏分數 | 智力隱藏分數 |
| stat_constitution | TEXT | 評級 A~S | 六項能力值_體質 |
| stat_constitution_hidden | REAL | 隱藏分數 | 體質隱藏分數 |
| stat_charm | TEXT | 評級 A~S | 六項能力值_魅力 |
| stat_charm_hidden | REAL | 隱藏分數 | 魅力隱藏分數 |
| stat_luck | TEXT | 評級 A~S | 六項能力值_幸運 |
| stat_luck_hidden | REAL | 隱藏分數 | 幸運隱藏分數 |
| special_tags | TEXT | 例：強化人、血月癌（逗號分隔） | 特殊狀態標籤 |
| special_forms | TEXT | 特殊型態 JSON（型態名→倍率，見下方說明） | 特殊型態 |
| weapon | TEXT | 主武器描述（例：「長劍」「短刀」，可空=徒手） | 武器 |
| weapon_base | REAL | 武器基數覆寫（可空；**僅管理員**依下方判定表設定，AI 不可自行決定） | 武器威力基數 |
| max_mp | INTEGER | 10~100 | 最大魔力量_MaxMP |
| martial | INTEGER | 0~100 | 武藝值 |
| exp | INTEGER | 0~100 | 實戰經驗 |
| is_teacher | INTEGER | 0/1 | 是否為教師角色 |
| is_villain | INTEGER | 0/1 | **是否反派**（AI 依此判斷哪些角色可為其預設動作，見 U6） |
| dynamic_difficulty | INTEGER | 0/1 | **動態難度**（僅反派可開啟，見下方說明） |
| calibrate_arc | TEXT | 校準篇章名（可空） | 該反派登場的篇章 |
| calibrate_roster | TEXT | JSON 字串陣列（可空） | 該篇章預定上場的角色名 |
| calibrated_at | TEXT | ISO 時間（可空） | 最後一次動態難度校準時間 |
| updated_at | TEXT | ISO 時間 | — |

**特殊型態（special_forms）說明**：
- 記錄**全新型態**：與原始型態差距很大的變身/型態，例如 `{"獸王型態": 1.3, "神臨型態": 1.5}`。
- 進入新型態時，該型態的倍率作為 `Form_Mod` 乘入最終威力（1.1~1.5 不等，每種型態可有不同加成）。
- 型態為**暫時性**狀態，由 Phase A 判斷角色目前是否處於該型態（玩家需在扮演中描述轉變）才套用。
- 與 `special_tags`（被動狀態標籤，如強化人/血月癌）分開存放。

**反派與動態難度（is_villain / dynamic_difficulty）**：
- **反派標籤（`is_villain = 1`）**：讓 AI 知道哪些角色是反派——U6 動作預設規則中，「反派未設定動作時由 AI 預設」的判斷依據即此欄位（魔物則依種族/魔物評級識別）。
- **動態難度（`dynamic_difficulty = 1`，僅反派可開啟）**：反派數值不是寫死的，而是在**錄表格/篇章登場時校準一次**。管理員輸入反派時會提到「該篇章預定上場的角色」，校準流程：
  1. 管理員提供 `calibrate_arc`（篇章名）與 `calibrate_roster`（該篇章預定上場角色名）。
  2. 系統收集上場角色的現有數值（六維隱藏分數、exp、martial、max_mp）。
  3. AI 依「現有強度」（機械基準 `villain_calibration_baseline`：各六維取上場角色該項最高 × 加成倍率，再反推評級）為反派設定**有挑戰性但打得動**的數值，輸出思維鏈供審查（見 collide_rules.CALIBRATE_SYSTEM）。
  4. 校準結果寫回該反派角色行，並填 `calibrated_at`；**該篇章內數值鎖定不再變動**——越晚登場的篇章，上場角色通常越強（玩家已成長），反派自然越強。
- **校準加成倍率**：普通反派 0.9~1.0、篇章 Boss 1.05~1.15、最終 Boss ≤1.2；上限遵循一般約束（隱藏分數 ≤ 9.0、exp/martial ≤ 100、max_mp ≤ 100）。

**魔力記帳（不設 current_mp）**：
- **系統不追蹤當前魔力**：`collide_characters` 只存 `max_mp`（上限），不設 `current_mp`。招式已經使用出來即代表玩家聲稱其魔力足以釋放，系統無需驗證。
- **魔力消耗由玩家自行記帳**：每位玩家自己記住施放招式、維持結界、魔力彌補的花費與回復（高速回復/無法回復的差別由玩家自訂規則維護）。
- **可選自動記帳功能**（非必要，便利性功能）：若實作，可在 `/clash` 指令的提示詞中注入「本回合消耗」彙整（招式的 `mp_cost`、結界 `sustain_mp`、魔力彌補投入量），供玩家抄寫或由系統代為扣除（扣除僅寫入對撞紀錄，不影響角色表的 `max_mp`）。

**隱藏分數規則**：`hidden = RATING_CONST[評級] × RACE_MOD[種族]`，上限 9.0。RATING_CONST：E=1, D=2, C=3, B=4, A=5, S=6；種族修正係數 0.8~1.5（人類智力 1.0、魚人敏捷 1.2…）。
- **隱藏分數存進 `*_hidden` 欄位，與評級並列**（寫入時由程式計算後一併存表，計算時直接讀取，不需重算）。
- **種族修正係數表另外放**（`race_modifiers.json` 或獨立 `race_mods` 表），不混入角色表。

### 5.2 Ability & Skill Schema（能力與招式數據表）

**能力階層**：角色 → 能力（名稱 + 原理）→ 招式（能力的擴展應用）。
- 招式是能力的**擴展應用**：同一個能力可以延伸出不同招式，本質上都是同一種應用。
- 能力記錄「名稱」與「原理」；招式只記錄「作為該能力哪種擴展應用的具體施展」。

**擴展應用分類（`application`）**：招式依**所屬能力的分類（`category`）**對應選用擴展應用模板，不綁死特定兩類；分類可自由擴充（不鎖死 enum）：

| 能力分類 | 該分類的擴展應用模板（建議，可自訂） |
|---|---|
| 攻擊型 | 以造成傷害為前提的放出 / 持續傷害・追擊 / 強化攻擊 |
| 輔助型 | 增益己方・隊友 / 削弱敵方 / 環境操控 |
| 防禦型 | 屏障・護盾防禦 / 反彈・吸收 / 領域防禦 |
| 控制型 | 束縛・禁錮 / 位移・拉扯 / 感官干擾 |
| 召喚型 | 召喚使魔・分身 / 召喚物協同 / 召喚強化 |
| 移動型 | 高速移動・瞬移 / 地形穿越 / 位移攻擊 |
| 治癒型 | 治療・回復 / 淨化・解除異常 / 再生・強化回復 |
| 偵查型 | 感知・探測 / 情報收集 / 隱匿・偽裝 |
| 複合型 | 兩類以上複合 / 隨情境切換 / 自訂擴展 |

- 能力新增 `category` 欄位；`WRITE_RULES["ability"]` 要求輸出分類，`WRITE_RULES["skill"]` 依能力分類給出對應模板（不再只示範「擴展一/擴展二」兩類）。
- `SKILL_DERIVATION_SYSTEM` 擴充：除原理外，`application` 的用途須與能力 `category` 一致（例：輔助型能力的招式不應主要是大範圍傷害型）。
- 計算影響：**所有招式（含非傷害型擴展應用）統一使用一般公式計算**。對撞指令的前提是雙方玩家都決定對撞；一方不想對撞時根本不會輸入指令，因此不存在「非傷害型 vs 傷害型」的錯位場景。`application` 僅作分類記錄與合理性判斷（供 Phase A 審查招式是否符合能力原理與角色設定），不影響 `Atk_BP` 計算。
- Phase A 判斷雙方出招時，標記該招式的 `application` 分類；分類與能力原理、角色設定不符時回報不合理。

本地表名：`collide_abilities`（能力表）

| 欄位 | 型別 | 範圍/約束 |
|---|---|---|
| id | INTEGER PK AUTOINCREMENT | — |
| name | TEXT NOT NULL | 能力名稱 |
| owner | TEXT | 擁有角色 |
| category | TEXT | 能力分類（建議：攻擊型/輔助型/防禦型/控制型/召喚型/移動型/治癒型/偵查型/複合型；可自訂） |
| principle | TEXT | 能力原理（驅動機制、限制、成長方向） |
| range_limit | TEXT | 極限有效距離（自由描述，如「極限 80 公尺」；能力本質為近身則留空或寫「近身」） |
| range_decay | TEXT | 超過極限距離後的衰減規則（自由描述，如「每超 10 公尺威力 -5%」；超過後套用 Range_Mod） |
| proficiency | INTEGER | 0~100，熟練度（預設 0；**同能力統一計算**） |
| updated_at | TEXT | ISO 時間 |

> **距離衰減（Range_Mod）**：遠端作戰時依實際距離與能力特性衰減。
> - 距離由 Phase A 依討論串情境判斷（近身/中距/遠距或具體公尺數）；**未描述 → 預設近身（5 公尺內），近戰與魔法皆不衰減**。
> - 能力在 `range_limit` 內 → `Range_Mod = 1.0`（無衰減）。
> - 超過 `range_limit` → 依 `range_decay` 規則衰減，由 Phase B 計算 `Range_Mod`（<1.0）並寫入思維鏈。
> - 距離遠的射程型能力（如影子魔法）極限距離長，可玩較遠的打法；短程能力超過射程會顯著衰減。

本地表名：`collide_skills`（招式表）

| 欄位 | 型別 | 範圍/約束 |
|---|---|---|
| id | INTEGER PK AUTOINCREMENT | — |
| name | TEXT UNIQUE NOT NULL | 招式名稱 |
| ability | TEXT | 所屬能力名稱（本招式是哪個能力的擴展應用） |
| owner | TEXT | 擁有角色（可空=通用） |
| application | TEXT | 擴展應用分類（擴展一：傷害型 / 擴展二：非傷害型 / 自訂） |
| skill_type | TEXT | 個人魔法 / 通常魔法 |
| magic_system | TEXT | 超能系 / 自然系 / 療癒系 / 跨系融合 |
| element | TEXT | 世界元素屬性（共 20 種：光/暗/火/水/冰/風/雷/岩/毒/葉/鐵/時間/無限/太空/核/純粹魔力/靈魂/花朵/夢幻/混沌） |
| mp_cost | INTEGER | 1~25 |
| power_base | REAL | 1.0~10.0 |
| effect | TEXT | 附加狀態效果（自然語言，僅供參考） |
| updated_at | TEXT | ISO 時間 |

**能力—招式關係規則**：
- 同一個 `ability` 可對應多個 `collide_skills` 招式（同一應用的不同變體）。
- 招式計算仍以招式自身 `power_base` 為主；能力原理供 AI 判斷招式合理性與是否符合角色設定（不合理時於 Phase A 回報）。
- 玩家可使用未單獨登記的「變體」：AI 若判斷其與既有能力擴展應用相符，即為「合理變體」——**Skill_Base 一律以該能力下既有招式「最低 power_base」為準**，1.05 加成由 Variant_Mod 套用（不重複乘算）。**不可取最高值招式套用低威力描述**（防鑽漏洞）。例：該能力既有火焰彈 5.0、烈焰風暴 8.0，玩家使出未登記的「火焰連彈」→ Skill_Base = 5.0，Variant_Mod = 1.05（基準 min 值與變體判定寫入思維鏈）。
- 招式必須標註 `application`（擴展應用分類）；同一能力可同時存在多種擴展應用（例如火焰能力同時有「擴展一」的噴火與「擴展二」的火焰分身），各自獨立計算。

**元素克制**（存程式碼）：剋制有利=1.2、無剋制=1.0、被剋制=0.8（特殊元素可 1.3）。
**一個元素可擁有多個弱點（也可剋制多個元素）**：剋制關係由 `STRONG_ELEMENT_PAIRS` 組合定義，方向不自動反推——例如只有水剋火（`("水","火")`），火不會反過來剋水。雙向剋制（如毒↔花朵）亦允許，計算時各自獨立，為設定層次保留空間。

**20 種世界元素**（`ELEMENT_GRID` 已登記，元素出處見「普雷亞」世界觀名詞表）：

| # | 元素 | 弱點（被剋） | # | 元素 | 弱點（被剋） |
|---|---|---|---|---|---|
| 1 | 光 | 太空、混沌 | 11 | 鐵 | 火、雷、核、毒 |
| 2 | 暗 | 光、純粹魔力 | 12 | 時間 | 無限、靈魂、夢幻、混沌 |
| 3 | 火 | 水 | 13 | 無限 | 時間、混沌 |
| 4 | 水 | 冰、雷、葉 | 14 | 太空 | 無限 |
| 5 | 冰 | 火、岩 | 15 | 核 | 太空、時間 |
| 6 | 風 | 雷、岩 | 16 | 純粹魔力 | 核 |
| 7 | 雷 | 岩 | 17 | 靈魂 | 光、暗、核 |
| 8 | 岩 | 水、葉、鐵 | 18 | 花朵 | 毒 |
| 9 | 毒 | 火、水、風、核、花朵 | 19 | 夢幻 | 光、暗、純粹魔力、靈魂、花朵 |
| 10 | 葉 | 火、冰、風、毒、鐵 | 20 | 混沌 | 純粹魔力、太空 |

> **註記（草稿）**：此為**可上架的草稿版本**。部分對應（如水剋火、光 vs 暗、雷 vs 鐵、時間 vs 無限）沿用草稿明確定義；其餘為依世界觀補齊的建議對應，**尚未經創作者逐項確認**。元素填寫與剋制計算照常運作；若後續確認個別對應不符設定，只需改程式中的 `ELEMENT_GRID` 字典，不影響其他欄位。
> **未登記/無對應元素**：`ELEMENT_GRID` 未列出的元素組合一律以無剋制 1.0 計算，並於 Phase A 回報 warning「未登記元素：{元素}，暫時以無剋制計算」。

**能力熟練度（Proficiency）——同一種擴展應用統一計算**：
- **玩家在扮演中自行使用招式**，AI 記錄每次使用（見 `collide_action_log`）。
- **熟練度統一掛在能力層級**（`collide_abilities.proficiency`）：同一能力的擴展應用共用同一份熟練度，**套用在該能力的每個招式上**，不必每個招式各自計算——簡化成長與計算。
- 熟練度由「總結成長」依樓層事件提升（玩家使用該能力的招式才計，同事件僅一次，見 5.5 樓層成長）。
- 加成直接乘進 `Atk_BP`：`Proficiency_Mod = 1.0 + proficiency / 100`（熟練度 100 → 2.0 倍）。
- 熟練度為**能力實用程度**，與招式基礎威力獨立相乘，二者不會互相稀釋。

### 5.3 Domain & Barrier Schema（結界系統數據表）

本地表名：`collide_domains`

| 欄位 | 型別 | 範圍/約束 |
|---|---|---|
| id | INTEGER PK AUTOINCREMENT | — |
| name | TEXT UNIQUE NOT NULL | 結界名稱 |
| owner | TEXT | 擁有角色 |
| domain_type | TEXT | 結界類型 |
| awakening | TEXT | 覺醒進度 |
| precision | REAL | 0~1.5 結界術精度 |
| rule_self | TEXT | 結界固有規則_自身增益（完全覺醒通常輸出1.5倍+必中或戰力標籤補正） |
| rule_enemy | TEXT | 結界固有規則_敵方限制 |
| rule_environment | TEXT | 領域固有規則_環境改變 |
| cooldown | TEXT | 結界融斷冷卻（正常 6 小時內可再展開） |
| sustain_mp | REAL | 維持魔力消耗 |
| updated_at | TEXT | ISO 時間 |

**覺醒進度影響（Domain_Mod 階梯）**：覺醒進度作為結界強度乘數，影響自身結界內的加成。

| 覺醒進度 | Domain_Mod（自身結界內） | 效果 |
|---|---|---|
| 未覺醒 | 1.0 | 結界無法張開，**只能使用「簡易結界」**（簡易結界**無法對抗已張開的結界**） |
| 初期覺醒 | 1.1 | 結界可張開，強度低落 |
| 中期覺醒 | 1.25 | 成型穩定，固有規則啟動一半 |
| 後期覺醒 | 1.4 | 固有規則完整啟動 |
| 完全覺醒 | 1.5 | 草稿原文：輸出 1.5 倍 + 必中或戰力標籤補正 |

補充規則：
- 完全覺醒為唯一錨點（草稿明確定義），其餘階段由 1.0 往 1.5 插值。
- **結界術精度（0~1.5）**：決定結界成型速度與穩固程度，與覺醒進度掛勾——「中期覺醒」以上才開放精度成長；精度不直接等同於 Domain_Mod。
- **未覺醒對抗規則（簡易結界）**：未覺醒者只能用簡易結界，而簡易結界**無法對抗張開的結界**——對上已張開結界者時，未開方以「未開結界 BP（Domain_Mod = 1.0）」對抗對方開結界 BP（1.1~1.5），**正常計算**，不觸發結界交疊/覆蓋/融合（見 6.3）。
- **結界融合觸發條件**：雙方都張開結界（即皆已覺醒）**且**雙方 Domain_Mod 差距 < 0.3，才判定為「實力相當」觸發結界交疊；差距 ≥ 0.3 時結界被覆蓋，可由被覆蓋方消耗魔力彌補（見 6.3）恢復融合資格。任一方未張開結界 → 不觸發交疊。

### 5.4 AI Calculation Output（對撞紀錄表）

本地表名：`collide_battles`

| 欄位 | 型別 | 說明 |
|---|---|---|
| id | INTEGER PK AUTOINCREMENT | — |
| battle_time | TEXT | 發生時間 |
| attacker | TEXT | 進攻方角色（威力較大者，由計算決定） |
| attacker_skill | TEXT | 招式 |
| defender | TEXT | 防守方角色（威力較小者） |
| defender_skill | TEXT | 招式/動作 |
| atk_output | REAL | 攻擊方實際威力（Atk_BP） |
| def_output | REAL | 防禦方實際威力（Def_BP） |
| element_mod | REAL | 元素相克判定倍率 |
| verdict | TEXT | 最終判定（碾壓/壓制/僵持/結界交疊） |
| result | TEXT | 玩家可讀結果區塊（JSON：verdict/summary/projection/reason——含**預估結果**，供玩家檢視計算靠不靠譜、供 Phase C 轉寫正文） |
| remaining_damage | TEXT | **已棄用（deprecated）**：v1 剩餘衝擊傷害自由文字。v2 起由 Clash 血條系統的 ticks 欄位承接（見 5.6），不再新增寫入；既有資料保留供追溯 |
| domain_fusion | INTEGER | 結界融合觸發 0/1 |
| chain_of_thought | TEXT | 思維鏈與計算過程 |
| confirmed | INTEGER | 中之人是否確認 0/1 |
| status | TEXT | pending（草稿計算，未確認）/ active（已確認）/ undone（撤銷作廢，見 6.4） |
| undone_by | TEXT | 撤銷者（可空） |
| undone_reason | TEXT | 撤銷原因（可空） |
| created_at | TEXT | ISO 時間 |

> **對撞紀錄的生命週期**：
> - `/clash` 產出計算結果時先以 `status = pending` 寫入（或暫存於記憶體，見 7.1）——這是**草稿計算**，未確認前不算正式對撞紀錄。
> - 發送者按「確認」→ `status = active`，才以文風生成對撞文本並計入正式紀錄。
> - 發送者按「取消」→ 該草稿**清除**（`pending` 紀錄刪除或標記作廢），避免初期 bug 時洗版。
> - **對撞紀錄不可刪除，只能撤銷（undo）**（僅適用 `active`）：`status = undone` 表示該次對撞已作廢，於頻道公告「本次對撞已撤銷」；原始計算、思維鏈與文風文本一律保留供追溯。

### 5.5 樓層成長系統（Floor Growth System）

> 使用者核心原則：**每一個討論串 = 一個樓層**。樓層可更新，但**數值更新只會發生一次且可追溯（原始數據被儲存）**；樓層的 AI Action Log 全部可查看；數值加減完全儲存，可人工審查與修改。

本地表名：`collide_floors`（樓層主表）

| 欄位 | 型別 | 說明 |
|---|---|---|
| id | INTEGER PK AUTOINCREMENT | — |
| thread_id | TEXT UNIQUE | 討論串 ID（一個討論串 = 一個樓層） |
| thread_name | TEXT | 討論串名稱 |
| status | TEXT | open / summarized / locked |
| summary_generated | INTEGER | 已總結 0/1 |
| summary_at | TEXT | 最後總結時間 |
| created_at | TEXT | ISO 時間 |

本地表名：`collide_growth_log`（數值變更審計表——**每筆加減完全儲存**）

| 欄位 | 型別 | 說明 |
|---|---|---|
| id | INTEGER PK AUTOINCREMENT | — |
| thread_id | TEXT | 所屬樓層 |
| floor_event_id | TEXT | 系統確定性生成的事件鍵（`{thread_id}#{category}#{target}#{field}`，見下方防呆機制；UNIQUE with thread_id） |
| battle_id | INTEGER | **可空，關聯 `collide_battles.id`**——該筆成長若**明確來自某場對撞**（`/grow` 總結時以 `collide_action_log` / `collide_ability_usage` 中 `action_type = clash`、`source = clash` 的紀錄溯源），務必填入；非對撞來源（討論串掃描、主線事件）留空。undo 時以此關聯列出受影響成長 |
| category | TEXT | character / skill / domain |
| target_name | TEXT | 被變更的角色/招式/結界名 |
| field | TEXT | 變更的欄位（exp / proficiency / martial / precision…） |
| old_value | TEXT | 原始值（**可追溯**） |
| new_value | TEXT | 新值 |
| delta | TEXT | 加減量（+5 / -3…） |
| reason | TEXT | 變動原因（對應成長規範類別） |
| source | TEXT | action_log / manual / grow_repeat |
| reviewed | INTEGER | 人工審查 0/1 |
| reviewed_by | TEXT | 審查者 |
| reviewed_note | TEXT | 審查備註 / 修正結果 |
| created_at | TEXT | ISO 時間 |

> **唯一性**：`UNIQUE(thread_id, floor_event_id)`——同樓層同成長鍵只存在一筆（後續 `/grow` 覆寫該筆，不新增）。

本地表名：`collide_action_log`（樓層 AI Action Log——**全部可查看**）

| 欄位 | 型別 | 說明 |
|---|---|---|
| id | INTEGER PK AUTOINCREMENT | — |
| thread_id | TEXT | 所屬樓層 |
| action_type | TEXT | clash / skill_use / summarize / grow / auto |
| battle_id | INTEGER | **可空，關聯 `collide_battles.id`**——`action_type = clash` 時必填（溯源用，見 5.5） |
| actor | TEXT | 動作主體（角色/AI） |
| action_detail | TEXT | 動作內容描述 |
| related_values | TEXT | 相關數值 JSON |
| created_at | TEXT | ISO 時間 |

本地表名：`collide_ability_usage`（背景能力使用 Log——**熟練度成長的依據來源**）

| 欄位 | 型別 | 說明 |
|---|---|---|
| id | INTEGER PK AUTOINCREMENT | — |
| thread_id | TEXT | 所屬樓層 |
| actor | TEXT | 使用能力者（角色名） |
| ability | TEXT | 使用的能力名 |
| battle_id | INTEGER | **可空，關聯 `collide_battles.id`**——`source = clash` 時必填（熟練度成長溯源用） |
| source | TEXT | clash（對撞記錄）/ thread_scan（討論串掃描）/ manual |
| detail | TEXT | 使用情境描述 |
| created_at | TEXT | ISO 時間 |

> **背景能力使用 Log 機制**：能力熟練度的成長依據**不只來自 clash 戰鬥記錄**。
> - `/grow` 執行時，除了讀取 `collide_action_log`，系統（LLM）**直接掃描該討論串的對話內容**，判斷「本樓層使用了哪些能力」——玩家在扮演中描述的任何能力應用（含未觸發對撞的日常使用）都會被記錄。
> - 掃描結果寫入 `collide_ability_usage`（`UNIQUE(thread_id, actor, ability)`，防重複）；`source = thread_scan`，對撞觸發的使用標 `source = clash`。
> - 熟練度成長判斷 = 該樓層 `collide_ability_usage` 中該 actor 的所有能力集合（不只 clash）。
> - **無招式的普通攻擊**也記錄於此（`ability =「普通攻擊」`）並寫入 `collide_action_log`——供武藝值（Martial）成長判斷。

本地表名：`collide_future_log`（未來功能參考 Log——v1 不支援事件留存）

| 欄位 | 型別 | 說明 |
|---|---|---|
| id | INTEGER PK AUTOINCREMENT | — |
| thread_id | TEXT | 所屬樓層 |
| thread_name | TEXT | 樓層名稱 |
| event_type | TEXT | 事件類型（multiway_clash：三方以上混戰 / other） |
| involved | TEXT | 參與者（逗號分隔） |
| summary | TEXT | 事件摘要（如「三方混戰」） |
| suggestion | TEXT | 建議處理方式（如「拆成兩組 1v1」） |
| created_at | TEXT | ISO 時間 |

> **用途**：v1 不支援的事件（如三方以上混戰）除了頻道回報「不支援，請拆解為單挑」，**也寫入此表**留存真實案例與建議拆解方式，供未來團戰設計參考。`UNIQUE(thread_id, event_type, involved, summary)`——相同事件重複回報不新增。

**樓層成長規則（依草稿「提升實戰經驗值的方式」）**：
- 每個樓層（討論串）由 `/grow`（或 `/clash grow`）**總結成長一次**，依事件類別套用數值變動。
- **防呆單位 = 樓層（thread_id）**，不是事件。首次 `/grow` 後 `collide_floors.summary_generated = 1`（標記「已執行」）；後續再次 `/grow` 一律進入「更新/重跑」模式，**不會重複疊加**（見下方防呆機制）。
- `/grow` 總結時，系統（LLM）除了讀取 `collide_action_log`，還**直接掃描該討論串的對話內容**，判斷「本樓層使用了哪些能力」並寫入 `collide_ability_usage`（`source = thread_scan`）——能力熟練度成長不綁定 clash 戰鬥。
- **成長溯源（battle_id）**：`/grow` 依 `collide_action_log`（`action_type = clash`）或 `collide_ability_usage`（`source = clash`）總結成長時，將該場對撞的 `collide_battles.id` 填入成長紀錄的 `battle_id`；非對撞來源（主線事件、討論串掃描）則留空。如此 undo 一場戰鬥時，才能列出「該場衍生的所有成長」供人工修正（見 6.4）。
- 數值變動範圍（草案）：核心主線/重大事件 +10~20；跨越階級生死決鬥 +5~10；結界戰鬥實踐 +5~8（並提升結界精度）；高難度委託 +1~3（打機器人無法超過 E=20）。

**防呆機制（不依賴 LLM 生成事件 ID）**：

1. **`floor_event_id` 由系統確定性生成**，不使用 LLM 產出的事件名稱（避免同事件兩次總結產生不同字串導致防重複失效）。生成規則：
   `floor_event_id = "{thread_id}#{category}#{target_name}#{field}"`
   （category: character/skill/domain；target_name: 角色/招式/結界名；field: exp/proficiency/…）
2. **首次 `/grow` = 總結成長**：套用事件類別成長，每個（thread_id, category, target, field）寫入一筆 `collide_growth_log`，並將 `collide_floors.summary_generated = 1`。
3. **後續 `/grow` = 更新/重跑**：系統先檢查 `summary_generated`：
   - 已執行 → 對每個成長目標以 `floor_event_id` 比對既有 log：
     - **存在**（同樓層同欄位已成長）→ **更新該筆**（覆寫 `new_value`/`delta`，`source = grow_repeat`），不新增、不疊加；
     - **不存在**（新增的成長項目）→ 新增一筆。
   - 未執行 → 依首次流程套用。
4. **唯一性約束**：`(thread_id, floor_event_id)` 唯一（UNIQUE）。任何同樓層同欄位重複套用都會被 DB 唯一性擋下，作為最後一道防線。
5. 管理員人工修正不受防呆限制：修正即新增一筆 `source = manual` 的 log（不影響成長 log 的防呆鍵）。

**可成長項目清單（/grow 支援的完整目標）**：

| 項目 | 目標欄位 | 成長方式 | 上限 |
|---|---|---|---|
| 實戰經驗 | `exp`（角色表） | 依草稿四類事件（主線/決鬥/結界/委託） | 0~100 |
| 能力熟練度 | `proficiency`（能力表） | 玩家使用該能力任一招式於該樓層有使用記錄才提升（依據 `collide_ability_usage`：clash + 討論串掃描） | 0~100 |
| 武藝值 | `martial`（角色表） | 高強度對抗/實戰演練後提升；**無招式的普通攻擊有記錄（`collide_action_log` / `collide_ability_usage` 中的「普通攻擊」）時可作為武藝成長的依據** | 0~100 |
| 結界術精度 | `precision`（結界表） | 結界戰鬥實踐後提升 | 0~1.5 |
| 覺醒進度 | `awakening`（結界表） | 結界戰鬥/主線事件可推進階段 | 未覺醒→完全覺醒（詳見 5.3） |
| 最大魔力 | `max_mp`（角色表） | 重大突破/主線事件可提升 | 10~100 |

**不支援成長**：六項能力評級（`stat_*`）——若要調整須由管理員在 HTML 頁面人工修改並記錄 log。

成長幅度皆由 `/grow` 依樓層事件類別與角色使用記錄判斷，套用後寫入 `collide_growth_log`。

- **所有變動皆寫入 `collide_growth_log`**（含 old→new 與 delta），管理員可人工審查修改（`reviewed` / `reviewed_by` / `reviewed_note`）；人工修正也記錄一筆新的 log。
- 樓層的 AI Action Log 透過 `/clash log <討論串>` 全部可查。

### 5.6 Clash 血條系統（HP Pool）資料表

> **v2 核心**：偵測到魔物/反派時，系統依劇情表現**自動建立數據（全自動）**，以**血條**代表其狀態；血條歸零即**打敗或擊退**。血條對玩家**可視**（訊息文字進度條，見 5.6.4）。資料雖為暫時性，**最長可維持存放半年**。

#### 5.6.1 資料表：`collide_clash_battles`

| 欄位 | 型別 | 說明 |
|---|---|---|
| id | INTEGER PK AUTOINCREMENT | — |
| battle_id | TEXT UNIQUE | 該場血條對戰的唯一識別碼（**凍結粒度 = battle_id**，見 5.6.4） |
| villain_id | INTEGER | 反派角色 id（`collide_characters.id`，`is_villain = 1`） |
| villain_name | TEXT | 反派顯示名（血條標題） |
| tier | TEXT | 階級：雜兵 / 重點 / Boss（決定 MaxHP 與 Cap_tier，見 5.6.3） |
| max_hp_ticks | INTEGER | 總血量（ticks）。**battle_id 建立時凍結** |
| cap_ticks | INTEGER | 單節點聚合上限（ticks）。**battle_id 建立時凍結** |
| guard_value | REAL | Villain_Guard（反派防禦閾值，見 6.5.1）：`stat_constitution_hidden × Domain_Mod`。**battle_id 建立時凍結** |
| current_hp_ticks | INTEGER | 當前血量（ticks）。**唯一可變動欄位**；0 為終局狀態（見 outcome） |
| retreat_floor_ticks | INTEGER | 擊退線（ticks）= MaxHP 的 20%（見 5.6.3）。低於此線只能「擊退」，不能繼續扣到 0（見 6.5.2 chip 規則） |
| outcome | TEXT | 終局狀態：`active`（進行中）/ `defeated`（打敗）/ `retreated`（擊退）/ `sealed`（封印）。歸零後**不刪除資料**，僅標狀態 |
| source | TEXT | 建立來源：`auto_detect`（劇情自動偵測）/ `gm`（管理員註冊） |
| dynamic_difficulty | INTEGER | 自動偵測的反派一律 0（guard 純計算，不進校準流程）；僅管理員註冊的篇章 Boss 可為 1（見 9 邊界） |
| created_at | TEXT | ISO 時間 |
| absolute_expire_at | TEXT | **絕對上限** = 建立時間 + 180 天（到期硬刪除） |
| soft_expire_at | TEXT | 軟性有效期限 = 最後一次更新 + 滑動展期；**永不超過 absolute_expire_at**（雙軌 TTL，見 5.6.4） |
| retreat_snapshot_ticks | INTEGER | 擊退當下寫入的 `current_hp_ticks` 快照；新 battle_id 由快照續接（見 5.6.4） |

#### 5.6.2 節點表：`collide_clash_nodes`

| 欄位 | 型別 | 說明 |
|---|---|---|
| id | INTEGER PK AUTOINCREMENT | — |
| event_id | TEXT UNIQUE | 事件唯一識別碼（Phase A 產生，`<battle_id>:<序號>`）。**去重鍵** |
| battle_id | TEXT | 對應 `collide_clash_battles.battle_id` |
| node_id | TEXT | 節點識別碼（`<battle_id>:<節點序號>`）；**每節點最多一筆聚合事件**（單節點聚合，見 6.5.2） |
| occurred_at | TEXT | 事件發生時間（討論串時間） |
| delta_ticks | INTEGER | 本次扣減（ticks，0 ~ cap_ticks） |
| chip | INTEGER | 是否為僵持扣血（chip）0/1 |
| aggressors | TEXT | 參與玩家清單（JSON 陣列，含各玩家 verdict 摘要） |
| source | TEXT | `parsed`（信心 ≥ 0.90 自動提交）/ `pending`（0.70 ~ 0.89 待結算）/ `gm`（管理員覆寫） |
| confidence | REAL | Phase A 結構化事件的信心值（0.70 ~ 1.00） |
| confirmed | INTEGER | 0/1；`pending` 事件在下一結算節點確認後轉 1 |
| created_at | TEXT | ISO 時間 |

> **寫入規則（Ledger 驗證，雙軌制）**：生成器（Phase A / LLM）只提出 `combat.events[]` **提案**；**Ledger 驗證後才寫入**。模型**永不直接寫血條**；解析器只補空隙、不覆寫高信心事件。`event_id` + `(battle_id, node_id)` 去重；同節點多位玩家 → **單一聚合事件**（見 6.5.2），不逐人寫入。

#### 5.6.3 階級參數（存程式碼常數，公開可查）

| tier | MaxHP（B / ticks） | 單節點 Cap（B / ticks） | 擊退線（ticks） | 說明 |
|---|---|---|---|---|
| 雜兵 | 5B / 50 ticks | 2.5B / 25 ticks | 10 | 無名雜兵：**不個別建血條**，合併為「戰線狀態」（見 5.6.4） |
| 重點 | 12B / 120 ticks | 4B / 40 ticks | 24 | 有名或有特色的反派 |
| Boss | 24B / 240 ticks | 5B / 50 ticks | 48 | 篇章級反派 |

> 常數：`TICKS_PER_B = 10`、`CHIP_TICKS = 2`（僵持扣血一次 = 0.2B）。所有數值皆以 **ticks** 儲存；**B 只是顯示單位**（血條進度條換算），不存在於 schema（見 6.5.2 B 消去）。階級參數於 battle_id 建立時凍結，存放於 `collide_clash_battles` 對應欄位。

#### 5.6.4 生命週期與可見性

- **血條對玩家可視**：以訊息文字進度條（如 `██████░░░░ 12B/24B`）輸出到討論串，並附**一行理由**（為何扣血）。**runtime 渲染入口 = `collide_engine.format_clash_hp_bar(current_hp_ticks, max_hp_ticks, *, width=10)`**（純函式；**全保真**——保留 `█`／`░` 與終局 emoji（`❌`／`💨`／`🔒`），產物供 Discord UTF-8）。**編碼安全只在輸出邊界**：寫入 console／legacy log 等窄編碼串流時才經 `collide_engine.enc_safe_for_stream(text, stream=sys.stdout)` 降級（`░`→`-`、emoji→ASCII），故 cp950 下 `python collide_engine.py` 仍 exit 0 且**不需**設定 `PYTHONIOENCODING`；送 Discord 的 payload **不降級**。`collide_ui.render_clash_bar(...)` 僅為 **UI 委派包裝**（全保真，委派至 engine）。討論串正文由指令層（`collide_cmds`）於**確認時**附加；渲染只讀值、不碰結算。
- **歸零 = 終局狀態，不是刪除**：`outcome` 標記 `defeated` / `retreated` / `sealed`；資料保留至 TTL 到期。
- **TTL 雙軌**：**絕對上限** = 建立時間 + 180 天（到期硬刪除）；**軟性展期** = 最後一次更新 + 滑動延長，**永不超過絕對上限**（對應「最多可維持存放半年」）。
- **擊退快照**：擊退（`retreated`）當下把 `current_hp_ticks` 寫入 `retreat_snapshot_ticks`；反派再登場 = **新 battle_id**，初始血量由快照續接。僅 `休整恢復` 事件（`source = gm`）可回復至 MaxHP；**預設不自動回血**。
- **階級分層（避免儲存爆炸）**：有名關鍵反派 → 個別血條；無名雜兵 → 合併為「戰線狀態」單一血條。
- **凍結粒度 = battle_id**：「不可重新校準」指同一場 battle_id 內；新章節再登場 = 新 battle_id，血量由快照續接，參數重新凍結。
- **只有反派有血條**：玩家受傷是純敘事（無玩家 HP pool，單向），血條只存在於反派側。

---

## 6. 公式與判定規則（存程式碼/prompt，公開可查）

### 6.1 五條公式

```
Atk_BP      = (Skill_Base + Stat_hidden/2) × Exp_Mod × Element_Mod × Domain_Mod × Proficiency_Mod × Status_Mod × Form_Mod × Variant_Mod × Range_Mod × Ambush_Mod
Melee_BP    = (Weapon_Base + STR_hidden/2) × (1 + Martial/200) × Exp_Mod × Domain_Mod × Status_Mod × Form_Mod × Variant_Mod × Ambush_Mod
ManaMelee_BP= (Weapon_Base + STR_hidden/2 + INT_hidden/2 + MP_Invested)
              × (1 + Martial/200) × Exp_Mod × Element_Mod × Proficiency_Mod × Status_Mod × Form_Mod × Variant_Mod × Ambush_Mod
Domain_BP   = (Precision × 3 + Stat_hidden/2) × Exp_Mod × Domain_Mod × Status_Mod × Form_Mod × Variant_Mod × Range_Mod × Ambush_Mod
Exp_Mod     = 1.0 + E/500
```

**Domain_BP（結界張開）**：當角色動作是**張開/展開結界**（Phase A `action_type = "domain"`）時使用，不落入普通攻擊。`Precision` 為結界術精度（0~1.5），`Domain_Mod` 依結界覺醒進度取 1.1~1.5（見 5.3）。雙方皆為結界張開 → 走結界交疊/覆蓋判定（見 6.3），不是一般招式四階判定。

變數來源：
- `Skill_Base`：招式威力基數（表欄位）
- `Stat_hidden`：對應能力隱藏分數（魔法取智力，視情況取體質/力量）
- `Exp_Mod`：實戰經驗修正 1.0~1.2
- `Element_Mod`：0.8 / 1.0 / 1.2 / 1.3
- `Domain_Mod`：自身結界內依覺醒進度取 1.1 / 1.25 / 1.4 / 1.5（見 5.3 階梯表）；**未開結界時為 1.0（乘一倍，無任何修正）**。未覺醒只能用簡易結界，無法對抗張開的結界（見 5.3 未覺醒對抗規則）
- `Proficiency_Mod`：能力熟練度加成 `1.0 + proficiency/100`（同能力招式共用，僅招式/魔力包裹近戰適用，普通近戰不計）
- `Status_Mod`：**當前狀態修正**（由 Phase A 判斷，預設 1.0；重傷、瀕死、疲勞等削減至 <1.0；強化人、血月癌等特殊狀態標籤可 >1.0）
- `Form_Mod`：**特殊型態修正**（進入全新型態時，依角色 `special_forms` 取該型態倍率，1.1~1.5；未變身時為 1.0）
- `Variant_Mod`：**招式變體修正**（依情況合理變更招式/使出變體時 +5%，即 1.05；正常出招為 1.0）
- `Range_Mod`：**距離衰減**（遠端作戰時依實際距離與能力範圍衰減；射程內為 1.0，超過 `range_limit` 依 `range_decay` 衰減至 <1.0。近戰（Melee_BP）固定 1.0，見下方說明）
- `Ambush_Mod`：**偷襲劣勢**（透過煙霧、障礙物等擋住視線偷襲時，被偷襲方套用劣勢倍率 <1.0，如 0.8；無偷襲情境為 1.0，見下方說明）
- `Martial`：武藝值 0~100
- `Weapon_Base`：武器基數，**依固定判定表機械取值，AI 不可自行判定**（見下方說明）
- `MP_Invested`：魔力包裹投入量 1.0~3.0，AI 自動判定以追回差距（見下方說明）

**武器基數（Weapon_Base）判定表（存程式碼，公開可查）**：
> 目的：防止 AI 依劇情隨意給值（空手給 3.0、持劍給 5.0 等）造成數值浮動且無法驗證。**Weapon_Base 一律由系統依武器分類機械取值**，AI 不參與判斷數值。

| 分類 | Weapon_Base | 範例 |
|---|---|---|
| 徒手（無 weapon 或 weapon 空白） | 1.0 | 拳、掌、爪 |
| 輕武器 | 2.0 | 匕首、短刀、飛刀 |
| 單手武器 | 3.0 | 長劍、戰斧（單手）、魔杖 |
| 重型武器 | 4.0 | 大劍、巨斧、長槍 |
| 傳說/特製 | 5.0 | 僅管理員於 `weapon_base` 欄位覆寫時使用 |

- Phase A 判斷該角色**實際手持**的武器種類（依討論串描述與角色表 `weapon`），輸出分類（不是數值）。
- Phase B 機械對應上表取 `Weapon_Base`；角色表有 `weapon_base` 覆寫時以覆寫值為準（**僅管理員可設**）。
- 未登記武器 → 徒手 1.0。武器種類不明時 Phase A 回報並預設徒手，寫入思維鏈。
- 判定結果寫入思維鏈供審查（例：「長劍 → 單手武器 → Weapon_Base 3.0」）。

**魔力包裹投入量（MP_Invested）自動調整規則**：
> `MP_Invested` 的定位是「**追回差距**」的動態資源，由 AI 自動判定，並需驗證結果。

- 初始投入：依對抗激烈程度與角色性格，AI 給定 1.0~2.0。
- **追回判定**：Phase B 先以初始 `MP_Invested` 計算雙方 BP，若該方仍落後且差距存在，按常理**會被逼著投入更多魔力**——AI 自動加碼（每次 +0.5 為單位）直到：
  - 差距被追回（該方不再落後，或達到可接受接近程度），或
  - 達上限 `MP_Invested = 3.0`。
- 加碼過程寫入思維鏈（例：「基礎落後 4.2 → 投入 2.0（+2）→ 仍差 2.2 → 投入 3.0（+1）→ 差距縮至 0.8」）。
- 魔力消耗由玩家自行記帳（見 5.1）；`MP_Invested` 只影響本次計算與思維鏈記錄。

**距離衰減（Range_Mod）說明**：
- 距離由 Phase A 依討論串情境判斷（近身 / 中距 / 遠距，或具體距離）。
- **兜底規則**：討論串未明確描述距離 → **預設「近身（5 公尺內）」**，此時近戰與魔法**皆不衰減**（Range_Mod = 1.0），並於結果 `distance.desc` 標註「未描述，預設近身」。
- 招式/能力在其 `range_limit`（極限有效距離）內 → `Range_Mod = 1.0`；超過極限距離 → 依該能力 `range_decay` 規則衰減（例：「每超 10 公尺威力 -5%」），由 Phase B 計算 `Range_Mod` 並寫入思維鏈。
- 極限距離長的能力（如影子魔法）可在遠距離維持高威力，支援遠程打法；短程/近身能力超出射程會顯著衰減。
- 近戰（Melee_BP）不套 `Range_Mod`（固定 1.0）。

**偷襲劣勢（Ambush_Mod）說明**：
- **偷襲成立條件**：攻擊方透過煙霧、煙幕彈、障礙物、視覺遮擋等使被偷襲方無法及時察覺而出手。
- 成立時，**被偷襲方**（目標）套用 `Ambush_Mod = 0.8`（劣勢，閃避/預判能力下降）；攻擊方不受影響。
- 偷襲由 Phase A 判斷：描述中有視線受阻/隱匿出手且對方未察覺 → 成立；正常正面對決 → 1.0。
- 純迴避/純防禦的被偷襲方，其 Evade_BP / Guard_BP 亦乘 `Ambush_Mod`（視線受阻難以精準閃避）。
- Ambush_Mod 與其他修正全相乘。

**狀態修正（Status_Mod）說明**：
- 由 Phase A 依討論串劇情判斷角色當前狀態後產出，並寫入思維鏈供審查。
- 重傷/瀕死/脫力 → 0.5~0.8；輕傷/疲勞 → 0.9~0.95；正常 → 1.0；強化狀態（強化人、血月癌等）→ 1.05~1.3。
- 特殊狀態標籤的加成參考角色表的 `special_tags`，具體數值由 AI 依劇情合理性裁量。

**型態與變體修正（Form_Mod / Variant_Mod）說明**：
- **Variant_Mod（變體）**：角色依情況**合理**變更招式或使出招式變體 → `1.05`（+5%）。合理性由 Phase A 依劇情與能力擴展應用判斷，並寫入思維鏈。**未登記的合理變體**，Skill_Base 以該能力下既有招式「最低 power_base」為基準（見 5.2），1.05 即此 Variant_Mod，**不重複乘算**。
- **Form_Mod（新型態）**：角色進入**與原始型態差距很大的全新型態** → 依角色 `special_forms` 取該型態倍率（1.1~1.5）。各型態可有不同加成，由管理員寫入角色表。
- 二者皆疊乘全部修正（與 Proficiency / Element / Domain / Status 全相乘）。

### 6.2 實戰經驗等級

| 等級 | E 範圍 | Exp_Mod | 說明 |
|---|---|---|---|
| 學院新星 | 0~20 | 1.00~1.04 | 準 C 級 |
| 熟練獵魔人 | 21~50 | 1.04~1.10 | C~B 級 |
| 戰場老將 | 51~80 | 1.10~1.16 | A 級以上 |
| 身經百戰 | 81~100 | 1.16~1.20 | 戰力天花板 |

### 6.3 四階對抗判定

**動作意圖分類（Phase A 判斷）**：判斷雙方動作是「攻擊性」或「非對抗性」。
- **攻擊性**：動作帶有攻擊威力（招式/近戰/魔力包裹）。進入下方威力比較。
- **非對抗性**：純迴避或純防禦，未帶攻擊力。**不代入攻擊威力公式**，走專屬判定（見下方「純迴避/純防禦」）。
- 若一方非對抗、另一方攻擊：攻擊方照算 `Atk_BP`，非對抗方不計算 `Atk_BP`。
- 若雙方皆非對抗（如互相閃避）：不觸發對撞計算，Phase B 直接回報「無對撞發生」（該次 `/clash` 不寫入對撞紀錄或寫入 `verdict = 無對撞` 供參考）。

**攻守決定（僅攻擊性 vs 攻擊性）**：雙方各自算出最終威力（Atk_BP，**攻守雙方同一公式**），**威力較大者為攻擊方、較小者為防禦方**；判定即比較攻擊方威力（Atk）與防禦方威力（Def）。

```
Atk > Def × 2.0  → 絕對碾壓：斷肢、貫穿、瞬殺（無視防禦）
Atk > Def        → 優勢壓制：擊退、骨折、內臟震盪、逼出底牌化解
Atk ≈ Def        → 招式僵持：術式對撞、元素抵消、轉入速度/體力消耗戰
雙方同開結界（即皆已覺醒）且 Domain_Mod 差距 < 0.3 → 結界交疊：觸發結界融合，場景與規則混合
```

**純迴避 / 純防禦（非對抗性動作判定）**：
- **純迴避**：動作描述為利用敏捷閃躲、拉開距離、完全避開（無攻擊意圖）。以**敏捷隱藏分數**為基準：
  `Evade_BP = 敏捷隱藏分數 × Status_Mod × Form_Mod × Domain_Mod × Ambush_Mod`
- **純防禦**：動作描述為硬擋、格擋、張開防禦結界接住（無攻擊意圖）。以**體質隱藏分數**為基準：
  `Guard_BP = 體質隱藏分數 × Status_Mod × Form_Mod × Domain_Mod × Ambush_Mod`
  （若明確使用防禦性招式，可再乘該招式威力基數的 0.3 作為格擋加成，並寫入思維鏈。）
- 與攻擊方 `Atk_BP` 比較（以下以 `Def_BP` 代表 Evade_BP 或 Guard_BP）：

```
Atk > Def_BP × 2.0  → 命中重創：閃避/防禦被突破（迴避不及/破防）
Atk > Def_BP        → 部分命中：擦傷/震盪/被逼退，防禦方受輕傷
Atk ≈ Def_BP        → 成功迴避/成功防禦：招式落空或被完全擋下
Atk < Def_BP        → 徹底化解：防禦方毫髮無傷，取得反打位置
```

- 純迴避成功不會造成傷害；純防禦成功亦不反傷（除非該防禦動作本身帶有反擊描述，Phase A 需標記）。
- 攻守雙方對撞結果、判定與思維鏈一律公開。

**結界覆蓋與魔力彌補**：
- 僅**雙方皆張開結界**才適用覆蓋/彌補規則；任一方未開結界（未覺醒/未張開）→ 不觸發交疊，以威力比較結果為準，未開方用其未開結界 BP（Domain_Mod=1.0）對抗對方開結界 BP，**正常計算**（未覺醒只能用簡易結界，無法對抗張開的結界）。
- 雙方同開結界但 Domain_Mod 差距 ≥ 0.3 → 結界會被覆蓋，不觸發融合，以威力比較結果為準。
- **魔力彌補**：被覆蓋方（Domain_Mod 較低者）可消耗額外魔力提升等效 Domain_Mod 對抗覆蓋——**每消耗 1 點魔力，等效 Domain_Mod +0.03**，最高補到差距 < 0.3（恢復融合資格）為止。
- **彌補上限**：若原始差距 ≥ 0.6（實力差距過大），無法以魔力彌補，直接覆蓋。
- 彌補耗費的魔力由**玩家自行記帳**（系統不追蹤當前魔力，見 5.1 魔力記帳）；計算結果與魔力投入量寫入思維鏈供審查。

### 6.4 操作撤銷（Undo）

> 對撞紀錄**不可刪除，只能撤銷**。原始資料（計算、思維鏈、文風文本）保留供追溯，`status` 標記為 `undone`。

**觸發方式**：中之人（發送 `/clash` 者）或管理員可在該次對撞文本的「確認 / 重試」按鈕之外，對已確認的結果執行 `/clash undo <battle_id>`。

**撤銷規則**：
1. 僅**發送該次 `/clash` 的中之人**或**管理員**可撤銷；一般玩家不可。
2. 撤銷後該筆對撞紀錄 `status = undone`，並於該討論串**公開公告**「本次對撞（#id）已由 {撤銷者} 撤銷，原始紀錄保留供查」。
3. 若該次對撞已觸發樓層成長（`/grow`），撤銷**不自動回滾數值**——撤銷當下系統自動依 `battle_id` 關聯列出該場對撞衍生的所有 `collide_growth_log`（**成長追溯清單**，含角色/招式/結界、old→new→delta、`floor_event_id`），連同公告一併輸出，管理員可直接據此逐筆人工修正，不必大海撈針（成長審計保留可追溯性，見 5.5）。
4. 撤銷原因寫入 `undone_reason`，全程可追溯。

**誤按防護**：
- 確認按鈕帶二次確認（「確認後將生成對撞文本並寫入 log，是否繼續？」）；重試按鈕不寫入 log，直接重新計算。
- **按鈕權限歸屬**：所有對撞結果按鈕（確認/重試/取消）在 `custom_id` 中**編碼 `/clash` 發送者的 user_id**；View 回調先校驗 `interaction.user.id == 發送者`，不符即回覆「只有發送者可以確認此結果」並**忽略操作**。發送者事後交接給管理員時，僅管理員可代為操作或撤銷。
- 草稿（`status = pending`）可由發送者按「取消」清除，避免初期 bug 時洗版。

### 6.5 Clash 血條判定（v2）

> 血條計算承接四階判定結果，是**純函數**：verdict→delta。**敘事分數永不進入公式**——Phase A 只負責判斷「誰打了誰、結果像什麼等級」，扣多少血由 `r = Atk_BP / Villain_Guard` 機械決定。

#### 6.5.1 Villain_Guard（反派防禦閾值，純計算）

```
Villain_Guard = stat_constitution_hidden × Domain_Mod
```

- `stat_constitution_hidden`：反派角色表既有欄位（體質隱藏分數）。
- `Domain_Mod`：反派結界覺醒修正（未開結界 1.0，見 5.3 / 6.1）。
- **純計算、零新增輸入**：不依賴任何新欄位。自動偵測的反派一律 `dynamic_difficulty = 0`，guard 純粹由上式算出，**不進入** `calibrate_roster` 校準流程（見 9 邊界）。

#### 6.5.2 verdict→delta 純函數（多打一）

每位玩家**獨立**對同一反派計算 `r = Atk_BP / Villain_Guard`，再依下表機械換算：

```
r ≥ 2.0        → 碾壓：2B = 20 ticks
1.0 < r ≤ 2.0  → 壓制：1B = 10 ticks
0.9 ≤ r ≤ 1.0  → 僵持：chip = 0.2B = 2 ticks（CHIP_TICKS）
r < 0.9        → 無效：0 ticks
```

- **一次對撞 = 一個節點**：同節點多位玩家各自算出 delta 後**聚合**為單一事件（`node_id` 一筆），扣減 = `min(Σdelta, Cap_tier)`（單節點上限）。
- **破防節點只聚合破防玩家（共識 A1）**：該節點若有任一位玩家達 `r > 1.0`（壓制／碾壓），節點總扣減**只累加破防玩家的 delta**；僵持（chip 2）／無效（0）玩家的扣減**不加入**——**破防節點不給 chip**。
- **剩餘血量截斷**：扣減後 `current_hp_ticks` 不得低於 0：`delta_total = min(Σdelta, Cap_tier, current_hp_ticks)`。
- **chip 規則**：僅當**沒有任何玩家**達到 `r > 1.0` 時觸發（全員僵持）；一次性扣 `CHIP_TICKS`。**chip 無法擊殺**——最低只到擊退線（`retreat_floor_ticks`），不跨過擊退線歸零。
- **雙軌結局**：
  - chip 路徑（停在擊退線）→ `outcome = active` + `at_retreat_floor = True`（**不自動終局**）；`retreated` 只能由中之人（玩家）宣告後才寫入（共識 A2）。
  - 碾壓/壓制路徑 → 跨過擊退線扣至 0 → `outcome = defeated`（打敗）或 `sealed`（封印，依劇情）。
- **B 是顯示單位、會消去**：節點數 = MaxHP/delta = 24B/(k×B) = 24/k，B 在除法中消去。**只有 Villain_Guard、Cap_tier、MaxHP 承載數值**，三者於 battle_id 建立時凍結；`current_hp_ticks` 是唯一可變動欄位。

#### 6.5.3 事件信心與提交（雙軌制 + Ledger）

| 信心值 | 動作 |
|---|---|
| ≥ 0.90 | 自動提交（`source = parsed`），當節點即時扣減 |
| 0.70 ~ 0.89 | 標記 `pending`，**等待同 battle_id 的下一結算節點**；確認後合併扣減 |
| < 0.70 | 丟棄，不寫入 |

- **硬門檻**：必填欄位齊全是提交的前置條件（即使信心 ≥ 0.90，欄位不全也不提交，**不可**用降低信心門檻代替補齊欄位）。
- **pending 生命週期**：只存活到同 battle_id 的下一結算節點；當 battle_id 到達終局狀態（`defeated` / `retreated` / `sealed`）即失效。
- **留手不影響威力**：玩家意圖（留手）只把 碾壓→壓制 降級（沿用 6.3 留手規則），**不進 BP 公式**；意圖寫入 log 供追溯。

---

## 7. 系統架構

```
┌────────────────────────────────────────────────────────┐
│  對撞模擬器 simulator.html（make_simulator_html.py 建置）│
│  ┌───────────────────┐  ┌──────────────────────────┐  │
│  │ 角色/能力/招式/結界 │  │ 自然語言寫入（招式/角色/  │  │
│  │ 管理＋Phase A 首次 │  │ 能力/結界：描述→提示詞→  │  │
│  │ 招式偵測登記       │  │ 貼回 JSON→驗證→填表）    │  │
│  └───────┬───────────┘  └──────────┬───────────────┘  │
│          │                         │ 對撞模擬＋機械驗算 │
│          │                         │（simulator_engine）│
└──────────┼─────────────────────────┼───────────────────┘
           │                         │
           │ Discord: /writechar      │
           │          /writeability   │
           │          /writeskill     │
           │          /writedomain    │
┌──────────▼─────────────────────────▼───────────────────┐
│  Parsing / 審查層（collide_parser.py）                   │
│  LLM 依寫入規範解析 + 硬驗證（範圍/enum/能力庫）          │
└──────────┬──────────────────────────────────────────────┘
           │
┌──────────▼──────────────────────────────────────────────┐
│  DB 存取層（collide_db.py）                              │
│  SQLite: collide_characters / _abilities / _skills /    │
│          _domains + world_lore 鏡像                      │
└──────────┬──────────────────────────────────────────────┘
           │ 每次寫入後觸發
┌──────────▼──────────────────────────────────────────────┐
│  Sheets Sync（sheets_sync.py）                           │
│  單一試算表，多工作表：角色檔案 / 招式 / 結界 / 對撞紀錄  │
│  Google Sheets API v4，OAuth 2.0（credentials.json）     │
└──────────────────────────────────────────────────────────┘
```

### 7.1 對撞計算流程（三階段 API）

```
/clash 指令（可附討論串，省略則用當前頻道/討論串）
   │
   ▼
Phase A — 預處理（第 1 次 API 調用）
   1. 讀討論串取得最新戰局
   2. AI 自行判斷：誰與誰在對抗、各自出什麼招
   3. **記錄雙方實際使用的招式**（玩家自行描述的行動；AI 不替玩家選擇招式）
   4. **判斷雙方當前狀態**：是否重傷、負面狀態、特殊狀態標籤（強化人/血月癌等）、
      其他會影響最終 BP 的因素——產出狀態修正（Status_Mod），連同招式威力代入計算
   4b. **判斷型態與變體**：角色是否進入特殊型態（依 `special_forms` 取 Form_Mod）、
       出招是否為合理變體（依能力擴展應用判斷，是則 Variant_Mod = 1.05）
   4c. **判斷雙方實際手持武器種類**（依討論串描述與角色表 `weapon`），輸出武器分類
       （徒手/輕/單手/重型），**不輸出數值**（Weapon_Base 由 Phase B 機械對應判定表）
   4d. **判斷距離與偷襲**（Range_Mod / Ambush_Mod，見 6.1）
   5. **動作來源判定**：玩家角色只採用其自行描述的動作（AI 永不代決）；
      魔物/反派（角色表 `is_villain = 1`）若玩家未設定動作，由 AI 做預設
   6. 合理性審查：越權操作他人角色？主張對撞結果？（不直接駁回，
      所有主張在後續計算中判斷是否成立）
   │
   ▼
Phase B — 數學計算（第 2 次 API 調用）
   1. 機械抓取雙方角色/招式/結界資料；**Weapon_Base 依武器分類判定表機械取值**
      （角色表有 weapon_base 覆寫則以覆寫為準；AI 不自行判定數值）。
      玩家無招式時改用普通攻擊（Melee_BP）
   2. **分別計算雙方最終威力 Atk_BP（同一公式），並套用 Phase A 的修正
      （Status_Mod / Form_Mod / Variant_Mod / Range_Mod / Ambush_Mod）**
   3. **MP_Invested 追回判定**：先以初始投入（1.0~2.0）計算，若某方落後且仍有差距，
      AI 自動加碼（每次 +0.5）直到追回或達上限 3.0；加碼過程寫入思維鏈（見 6.1）
   4. **比較雙方最終威力，威力大者為攻擊方、小者為防禦方**
   5. 依公式與四階判定產出最終結果
   6. 產出完整計算結果：思維鏈 + 計算過程 + 最終判定
   │
   ▼
發布到討論串（所有人可見，含思維鏈 + 計算過程 + 確認/重試/取消按鈕）
   ┌──────────┬────────────┬────────┐
   ▼          ▼            ▼        ▼
  確認   重試(補充說明)  取消     (非發送者點擊)
   │          │            │         │
   ▼          ▼            ▼         ▼
 寫入 active ─┴─→ 重新計算   清除草稿   回覆「只有發送者
   │               (pending) (pending   可以操作」並忽略
   ▼                         標記作廢)
 Phase C — 文風輸出（第 3 次 API 調用）
   確認後以角色文風生成對撞實際文本，發布到同一討論串
   按鈕 custom_id 編碼發送者 user_id，View 回調校驗互動者身分（見 6.4）
```

**v2 多打一與血條流程（延伸）**：
- **自動偵測**：討論串中偵測到魔物/反派且玩家與其對抗 → 系統**自動**建立 `battle_id` 與血條（全自動，無需管理員介入；`source = auto_detect`，`dynamic_difficulty = 0`）。
- Phase A 額外產出 **`combatants`**：本節點**攻擊同一名反派**的所有玩家角色（依發言順序；每筆 `{char, skill, action_type, target, holding_back}`）。**`target` 必須等於 `defender_candidate`**——打別隻反派的玩家**不入**本節點（屬平行對撞的另一節點，另記 warnings）。只收**玩家本人貼出的行動**（AI 不得代寫）；`holding_back` 為該玩家自己主張的留手。單挑時退化為 1 人。這是**提案**，不是結果。
- Phase B 依 6.5.2 **逐玩家**計算 `r = Atk_BP / Villain_Guard`，聚合為單一節點扣減提案（`min(Σdelta, Cap_tier)`）。
- **Ledger 驗證後才寫入** `collide_clash_nodes`（去重、信心門檻、cap 截斷、剩餘血量截斷）並更新 `collide_clash_battles.current_hp_ticks`（雙軌制，見 5.6.2）。
- Phase C 文風輸出時附帶**血條進度條 + 一行理由**（見 5.6.4）。

---

## 8. 模組分工（建議）

| 檔案 | 職責 |
|---|---|
| `collide_db.py` | 資料表 create/insert/update/query（`collide_abilities` / `collide_skills` / `collide_characters` / `collide_domains` + world_lore 鏡像 + `make_db_lookup`） |
| `collide_parser.py` | 寫入規範 prompt + LLM 自然語言→結構化 JSON 解析 + 範圍驗證；`write_ability` / `write_skill`（含能力原理推導檢查）/ `write_character` / `write_domain` / `write_entries`（批次：一次多筆，能力先建檔讓招式可掛） |
| `collide_engine.py` | 純函式：隱藏分數、四公式、四階判定、熟練度乘算（不呼叫 LLM） |
| `collide_pipeline.py` | 對撞三階段 API 流程（Phase A 戰局分析 / B 威力計算 / C 文風輸出） |
| `collide_growth.py` | 樓層總結成長（/grow）：依草案成長規範套用數值變動、防重複、寫入審計 log |
| `collide_action.py` | 樓層 Action Log 記錄（所有 AI 動作可查） |
| `make_simulator_html.py` + `simulator_template.html` → `simulator.html` | 自足 HTML 管理/測試頁：角色/招式管理、自然語言寫入（產生寫入提示詞→貼回 JSON 驗證填表）、對撞模擬、Phase A 首次招式偵測登記、反派校準、引擎自測 |
| `simulator_engine.js` | JS 版碰撞引擎（collide_engine.py 逐函式鏡像；建置時與 Python 引擎自測向量比對） |
| `collide_write_cmds.py` | 自然語言寫入 slash commands：`/writechar`、`/writeability`、`/writeskill`、`/writedomain`、`/writebatch`（批次：貼上含 type 的 JSON 陣列） |
| `sheets_sync.py` | Google Sheets OAuth + 全表同步/增量同步（未實作） |
| `collide_cmds.py` | `/clash`、`/grow`、`/clash rule`、`/clash sheet`、`/clash log` slash commands（未實作） |
| `rules.md` / `rules_collide.json` | 公式與輸入規範的文字版（供 `/clash rule` 與 prompt 引用） |

### 8.1 寫入規範（collide_parser 的約束）

- 評級必須是 A~S 六等（E=1..S=6），LLM 不允許輸出其他值。
- MaxMP 10~100、武藝/經驗 0~100、威力基數 1.0~10.0、魔力消耗 1~25、結界精度 0~1.5。
- 必要欄位不可缺（角色名、種族、六維、MaxMP、武藝、經驗；能力名、能力原理；招式名、所屬能力、類型、系統、元素、魔力消耗、威力基數）。
- 種族修正係數查表：未登記種族 → 回報「未定義種族，請補種族修正」，不預設 1.0 之外的值。
- 招式必須掛在能力庫（`collide_abilities`）已登記的能力下，並通過「能力原理推導檢查」——招式 `application` 應可由該能力 `principle` 合理推導；不合理時附註理由，可選擇擋下（`require_derivation`）。
- LLM 輸出 JSON 後，由程式**硬驗證**（numeric bounds + enum + 能力庫存在），不合格直接擋下回報，不靠 LLM 自覺。
- **模擬器同步硬驗證**：`simulator.html` 內建 `nlValidate()`（與 collide_parser 同規則的 JS 版），「驗證並填入表單」在本地就先擋下越界值；描述→提示詞→貼回 JSON→填表流程與 Phase A/B/C 一致。
- **批次寫入**：類型可多選（招式/角色/能力/結界）一次寫入；多選時提示詞要求輸出含 `type` 的 JSON 陣列，`parseNL` 逐筆驗證並填入對應表單（招式按 owner 找角色填表）。**自動儲存**（預設開啟）：批次直接寫入角色資料（角色/招式/結界 upsert，角色先處理讓招式掛得上；同名招式/結界更新、角色新增則保留既有 skills/domains），表單仍填最後一筆招式供檢視。
- **填表模板**：「插入填表模板」把所選類型的欄位空白模板插入描述框；使用者填想填的欄位，留空者由 LLM 自動補全（模板與描述一起進 prompt）。
- **後端批次**：`collide_parser.write_entries` 接受含 `type` 的 JSON 陣列，依 ability→domain→character→skill 順序寫入（能力先建檔，招式才能掛上），並在資料庫層跑與單筆相同的硬驗證；Discord 以 `/writebatch` 提供。

### 8.2 Google Sheets 設計

- 單一試算表（由管理員在 Google Sheets 建立，填 spreadsheet_id 進 config）。
- 工作表：`角色檔案`、`招式`、`結界`、`對撞紀錄`。
- **同步策略（批量寫入緩衝 / coalescing）**：每次寫入後**不立即**同步，先標記該工作表為 `dirty` 並啟動 **5 秒累積視窗**——期間的變動合併，視窗結束後**一次**觸發「重寫該 dirty 工作表」（資料量小）保證一致性。管理員一口氣新增 10 個角色只會觸發 1 次 Sheets API 寫入，避免撞到配額。
  - 每次變動（或每次新寫入請求）重置 5 秒計時器（trailing debounce）；同步完成後清除 dirty 標記。
  - 同步進行中又有新變動 → 該工作表再次標記 dirty，下一個視窗重跑（確保不會漏）。
  - 管理頁面顯示每個工作表的同步狀態（已同步 / 排程中 / 失敗重試）。
- OAuth 2.0：`credentials.json`（OAuth 用戶端憑據，Web 應用程式），首次授權產生 `token.json`。
- 公開方式：把試算表共用權限設為「任何有連結的人 → 檢視者」。

---

## 9. Edge Cases（邊界情況）

- **招式未登記能力（能力庫沒有該能力）**：寫入時程式擋下，回報「所屬能力未在能力庫登記，請先新增能力（含能力原理）」——能力原理是擴展驗證的前提，先建能力、後建招式。
- **招式與能力原理不符**：推導檢查（`collide_parser.check_skill_derivation`）標記 `invalid` 並附衝突理由；預設仍允許存檔供審查（回傳 `derivation`），`require_derivation=True` 時擋下。
- **未登記種族**：解析時要求補種族修正係數，否則拒絕寫入該角色。
- **未登記元素**：`ELEMENT_GRID` 未列出之元素組合一律以無剋制 1.0 計算，並於 Phase A 回報 warning「未登記元素：{元素}，暫時以無剋制計算」（見 5.2 註記）。
- **反派開啟動態難度但未填篇章上場角色**：解析器擋下，回報「dynamic_difficulty 為 true 時 calibrate_roster 不可為空」（`collide_parser._check_bounds`）。
- **反派動態難度校準結果過強/過弱**：校準基準以「上場角色中現有強度最高者」為準；若校準後反派仍碾壓該篇章上場角色，管理員可人工覆寫數值（同 `/clash log` 審計修正流程）。
- **招式威力基數 > 10.0**：拒絕，回報「目前表/裡世界天花板為 10.0」。
- **經驗 100 滿等再提升**：拒絕或允許但記錄（草稿無規則 → 先拒絕並回報待決策）。
- **動作預設邊界**：玩家不可預設其他玩家的動作；若玩家未設定魔物/反派（`is_villain = 1`）動作，由 AI 預設；若偵測到玩家替其他玩家角色設定意圖/動作 → Phase A 回報「玩家不可預設他人動作」，改用該角色自行描述的動作（無則待該玩家補充）。
- **雙方主張對撞結果衝突**：不駁回，雙方的力量/防禦主張都代入計算，由公式判定誰成立。
- **玩家使用的招式未登記**：AI 記錄玩家實際使用招式的名稱與描述，並回報「該招式未在招式庫登記，請管理員補充」；計算先用普通攻擊（Melee_BP）替代，補充後可重跑。
- **角色張開結界（`action_type = "domain"`）**：Phase A 讀懂討論串中「展開結界/領域」的描述，標記該方動作型別為 domain（skill 欄位填結界名稱）；Phase B 以該結界覺醒→Domain_Mod、精度→Precision 計算 **Domain_BP**，**不會**因結界名稱不在招式庫而落回徒手普通攻擊。結界未登記時以「中期覺醒 + 精度 1.0」兜底並回報「結界未登記，請管理員補充」。
- **雙方皆張開結界（結界張開對抗）**：比較雙方 Domain_Mod，差距 < 0.3 → 結界交疊（融合）；≥ 0.3 → 結界覆蓋（被覆蓋方可耗魔力彌補，每 1 點 +0.03）。輸出 verdict 為「結界交疊/融合」或「結界覆蓋」，不是一般招式四階判定。
- **招式未登記但符合既有能力擴展應用（合理變體）**：AI 判定屬該角色既有能力的合理變體後，**Skill_Base 一律以該能力下既有招式「最低 power_base」為基準**，Variant_Mod 套用 1.05（不重複乘算）；結果中註明「以能力最低威力招式估算 + 變體」，**不可取最高值招式套用低威力描述**。
- **角色未登記任何招式**：玩家仍自行描述行動；AI 記錄其動作，計算時採用普通攻擊（Melee_BP），不要求玩家先建立招式。**無招式的普通攻擊亦寫入 `collide_action_log`（action_type = skill_use，action_detail =「普通攻擊（無招式）」）並記錄到 `collide_ability_usage`（ability =「普通攻擊」），供武藝值（Martial）成長判斷**（見 5.5）。
- **AI 誤判對抗雙方**：Phase A 產出後回傳給中之人核對，可在「重試（補充說明）」中修正對抗雙方或招式；攻守一律由威力比較決定，不依閱讀順序。
- **討論串中出現三方以上混戰**：v1 回報「目前不支援團戰，請拆解為單挑」，**同時寫入 `collide_future_log` 表**（參與者、事件摘要、建議拆解方式）留存供未來團戰設計參考（見 5.5）。
- **結界融合主張的覆蓋與彌補**：張開結界即代表已覺醒，故不駁回結界融合主張；僅當雙方 Domain_Mod 差距 ≥ 0.3 時結界被覆蓋（原始差距 ≥ 0.6 無法彌補）。被覆蓋方可消耗魔力（每 1 點 +0.03）補回差距 < 0.3 恢復融合資格；仍無法補回則以威力比較結果（碾壓/壓制/僵持）為準。
- **撤銷後成長追溯**：撤銷一場已觸發成長的戰鬥時，系統依 `collide_growth_log.battle_id` 列出該場衍生的所有成長紀錄供管理員人工修正；`battle_id` 缺失（如舊資料或非對撞來源成長）的紀錄不在此列，但仍可透過 `/clash log` 逐樓層查閱。
- **對撞確認後才寫入溯源**：`battle_id` 只在 `status = active`（確認後）的對撞上建立溯源；`pending` 草稿被取消時不寫入任何成長關聯。
- **Sheets 同步失敗（授權過期/斷線）**：本地 DB 照常寫入，同步排隊重試，並在下次觸發時補齊（先做簡單 retry，不做正式 queue）。
- **Google API 被拒（quota）**：延後同步，log 記錄；**配合批量寫入緩衝**（見 8.2），短時間大量變動合併為單次重寫，降低觸發 quota 的機率。
- **確認逾時**：對撞結果按鈕逾時（如 5 分鐘）自動標記未確認，`pending` 紀錄保留（可稍後由發送者再次觸發 `/clash` 或清除），結果仍留在討論串供所有人查看。
- **非發送者點擊對撞結果按鈕**：按鈕 `custom_id` 編碼發送者 user_id，View 回調校驗互動者身分——非發送者一律回覆「只有發送者可以確認此結果」並**忽略操作**（沿用 main.py / autopilot.py 既有「不是你的操作」權限 pattern，見 6.4）。
- **討論串過長**：Phase A 只讀取討論串**尾部**（如最近 50 則），避免 token 爆量。
- **自動偵測的反派誤帶 dynamic_difficulty = 1**：自動偵測路徑一律強制 `dynamic_difficulty = 0`（guard 純計算）；若資料仍誤帶 1 且無 `calibrate_roster` → 沿用既有拒絕規則（spec.md:741 門檻）。
- **多打一但無人破防（全員 r < 0.9）**：該節點扣減 0 ticks；不觸發 chip（chip 僅在全員僵持 0.9 ≤ r ≤ 1.0 時）。
- **單節點扣減超過剩餘血量**：截斷至 0，絕不負值；觸底即終局狀態（`defeated` / `sealed`）。
- **chip 是否可能擊殺**：不可能——chip 最低只到擊退線（`retreat_floor_ticks`），不跨線；僅 碾壓/壓制 可跨過擊退線扣至 0。
- **血條到期（TTL）**：絕對上限 180 天硬刪除；軟性展期永不超過絕對上限。到期前於頻道公告「{反派} 的血條資料已到期清除」。
- **同 battle_id 內重新校準**：不可（凍結粒度 = battle_id）；新章節再登場 = 新 battle_id，由 `retreat_snapshot_ticks` 續接。
- **玩家意圖（留手）與威力**：玩家意圖只影響**要不要留手**（沿用 6.3 留手規則，碾壓→壓制降級），**不影響威力與扣血公式**；意圖寫入 log 供追溯，不進 BP 計算。

---

## 10. Dependencies（依賴）

- `google-auth-oauthlib`、`google-auth-httplib2`、`google-api-python-client`（Sheets API）
- 既有 `aiosqlite`、`openai`/deepseek 呼叫（沿用 main.py 的現有 API 層）
- 既有 `fastapi`/`flask`（如未安裝，由 HTML 管理頁面需引入其一，或用 Python `http.server` 最簡方案）
- `discord.py`（既有，slash command + view buttons）
- Google Cloud Console：建立 OAuth 用戶端（Web），取得 `credentials.json`，啟用 Google Sheets API

---

## 11. 待決策（Open Questions）

1. **HTML 管理頁面形式**：本機 FastAPI server + 瀏覽器 UI？還是純靜態 html + 呼叫本地後端？要能開在外網嗎（若管理員不在同機）？
2. **種族修正係數表**：草稿只有人類/魚人範例，需要完整表（哪些種族、各六維修正），由管理員提供。
3. **「確認後文風輸出」的格式**：對撞文本張貼到哪個頻道？（討論串內？指定對撞頻道？）
4. ~~**Weapon_Base / MP_Invested**~~（已定案，見 6.1）：Weapon_Base 依**固定武器分類判定表**機械取值（AI 不判數值，管理員可覆寫）；MP_Invested 由 AI 自動判定並動態加碼追回差距（1.0~3.0）。
5. **對撞紀錄是否也公開到 Sheets**：草稿有 AI 對撞演算輸出表，預設公開。
6. **credentials.json 從哪取得**：需使用者到 Google Cloud Console 填寫 OAuth 用戶端憑據後提供。

---

## 12. 里程碑（Milestones）

### M0 — 前置作業（使用者提供）
- Google Cloud Console：啟用 Google Sheets API，建立 OAuth 用戶端（Web），提供 `credentials.json`
- 種族修正係數完整表
- 建立公開 Google Sheets 試算表並取得 spreadsheet_id

### M1 — 資料層 + Sheets 鏡像
- collide_db.py 八張表（角色/能力/招式/結界/對撞/樓層/成長審計/action log）+ collide_future_log
- sheets_sync.py OAuth + 同步（**批量寫入緩衝**：5 秒累積變動再一次同步，見 8.2）
- config.json 新增 `google_sheets` 區塊

### M2 — 自然語言寫入（模擬器 + Discord 指令）✅（主要完成）
- collide_parser.py 寫入規範 + 驗證（含 is_villain / dynamic_difficulty / calibrate_roster 布林與陣列校驗）；`write_character` / `write_domain` / `write_entries`（批次）已補
- collide_db.py 新增 `collide_characters` / `collide_domains` 表；`make_db_lookup` 支援 character（fallback character_profiles）與 domain
- simulator.html「自然語言寫入」卡：招式/角色/能力/結界（可多選）→ 產生寫入提示詞 → 貼回 JSON（單筆或陣列）→ nlValidate 硬驗證 → 自動填表；「插入填表模板」按鈕把欄位空白模板插入描述框，留空由 AI 補全
- simulator.html「Phase A 首次招式偵測」：解析後比對角色招式清單，未登記招式顯示警告＋登記按鈕（預填表單）
- collide_write_cmds.py：`/writechar`、`/writeability`、`/writeskill`、`/writedomain`、`/writebatch`（描述或含 type 的 JSON 陣列即可，ephemeral 回覆）
- 待辦：collide_ui.py 獨立管理頁 + 成長 log 人工審查修正（目前由 simulator.html 覆蓋管理功能）

### M2b — 反派動態難度校準
- collide_engine.villain_calibration_baseline（機械校準基準，可複現）
- collide_rules.CALIBRATE_SYSTEM（AI 依基準微調並輸出思維鏈）
- 反派寫入流程：新增反派 → 填篇章/上場角色 → 校準 → `calibrated_at` 鎖定

### M3 — 對撞計算
- collide_engine.py 公式/判定純函式 + 單元測試（含純迴避/純防禦判定）
- collide_pipeline.py 三階段流程（討論串 → 判斷對抗雙方/記錄玩家招式 → 計算 → 確認/重試 → 文風）
- collide_cmds.py `/clash` + 按鈕（含二次確認 + **custom_id 綁定發送者 user_id、View 回調權限校驗** + 草稿 pending/取消清除）+ `/clash undo <battle_id>`（撤銷，見 6.4）

### M3b — 樓層成長
- collide_growth.py 成長規範解析與套用（/grow，防重複 floor_event_id）
- collide_action.py 樓層 Action Log 記錄（`action_type = clash` 填 battle_id）
- **成長溯源**：`/grow` 依 clash 紀錄將 `battle_id` 填入 `collide_growth_log` / `collide_ability_usage`；`/clash undo` 自動列出該 battle_id 關聯的成長清單
- `/clash log` 查看 + 管理頁面人工修正

### M3c — Clash 血條系統（v2 多打一）
- collide_engine.py：`verdict_to_delta` 純函數（r = Atk_BP / Villain_Guard；碾壓 20 ticks / 壓制 10 / chip 2 / 無效 0）+ 多路聚合 + chip 截斷 + 剩餘血量截斷；常數 `TICKS_PER_B = 10`、`CHIP_TICKS = 2`（無硬編碼數字）
- collide_db.py：`collide_clash_battles` / `collide_clash_nodes` 表 + `make_db_lookup` 支援 villain guard 純計算；`event_id` + `(battle_id, node_id)` 去重；擊退快照；pending 綁定節點
- collide_pipeline.py：Phase A 產出 `combat.events[]` 提案 → Ledger 驗證寫入（雙軌制）
- collide_parser.py / extract_clash_prompt.md：反派註冊模板（guard、max_hp_ticks、cap_ticks）；自動偵測路徑 `dynamic_difficulty = 0` 門檻
- collide_engine.py：`format_clash_hp_bar` 訊息文字血條進度條（**runtime 入口**，逐字元編碼安全 fallback）+ `clash_outcome_label`
- collide_ui.py：`render_clash_bar` UI 委派包裝（後台／測試用，委派至 engine）
- 測試：chip 擊退線（Boss 240/48：滿血到擊退線 = 96 節點、累積 192 ticks、永不低於 48）；chip 無法擊殺、只在擊退線觸底

### M4 — 公開與文件
- `/clash rule` 查看規範
- rules 文件（rules_collide.md）
- Sheets 公開權限設定教學

---

## 13. 驗收標準

- [x] 在模擬器/`/write*` 輸入自然語言可正確建立角色/能力/招式/結界，數值違規被擋下。
- [ ] 每次寫入後 Sheets 即時更新（手動檢查 public 連結）。
- [ ] 在討論串執行 `/clash` 可自動判斷對抗雙方與招式，輸出含思維鏈、計算過程、最終判定。
- [ ] 未登記招式的角色自動使用普通攻擊（Melee_BP）。
- [ ] 玩家不可預設其他玩家動作；魔物/反派未設動作由 AI 預設；AI 永不代決玩家角色。
- [ ] 反派（`is_villain = 1`）可填篇章上場角色並開啟動態難度，校準後數值鎖定（`calibrated_at`）；未填上場角色被解析器擋下。
- [ ] 20 種世界元素皆可填寫並照常計算；未登記元素組合以無剋制 1.0 計算並回報 warning。
- [ ] `/clash rule` 顯示最新版公式與規範。
- [ ] 對撞結果可確認/重試，確認後產出文風文本；可 `/clash undo <battle_id>` 撤銷作廢（原始紀錄保留、頻道公開公告、不可刪除）。
- [ ] 對撞結果按鈕權限正確：非發送者點擊回覆「只有發送者可以確認此結果」並忽略；確認前為草稿（pending），確認後才寫入 active，取消可清除草稿。
- [ ] 純迴避/純防禦（非對抗性動作）不代入攻擊威力，走專屬判定；雙方皆非對抗回報「無對撞發生」。
- [ ] 三方以上混戰回報不支援、寫入 `collide_future_log` 供未來團戰設計。
- [ ] `/grow` 依成長規範套用樓層數值變動；同事件重跑不重複疊加。
- [ ] 成長溯源：來自對撞的成長（`collide_growth_log.battle_id`、`collide_action_log`/`collide_ability_usage` 的 battle_id）正確填入；`/clash undo` 自動列出該 battle_id 衍生的成長清單，管理員可逐筆人工修正。
- [ ] 能力熟練度（同能力招式共用一份）使用後提升，且正確乘算進 Atk_BP。
- [ ] 合理變體（+5% Variant_Mod）與特殊型態（special_forms 1.1~1.5 Form_Mod）正確套用並顯示在思維鏈；未登記變體以該能力最低 power_base 為基準，不重複乘 1.05。
- [ ] `/clash log` 可查看樓層全部 Action Log 與數值加減（old→new→delta）。
- [ ] 管理頁面可人工修正數值，修正記錄新 log，全部可追溯。
- [ ] Sheets 公開連結任何人可查看，內容與本地 DB 一致。

# -*- coding: utf-8 -*-
"""Clash v2 settlement + engine regression tests.

Run:  python -m unittest test_clash_settlement -v
Covers task #7 (chip retreat-line / node count), A1 (breakthrough excludes chip),
A2 (chip never auto-terminates) and the A3 draft->commit->idempotent chain.
"""
import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch

import collide_db
from collide_engine import resolve_clash_node
import collide_pipeline
from collide_pipeline import _settle_clash_after_phase_b
from collide_cmds import _commit_clash_settlement


class TestEngineNode(unittest.TestCase):
    def test_all_stall_is_chip(self):
        res = resolve_clash_node([0.95, 0.95], 1.0, cap_ticks=50,
                                 current_hp_ticks=240, retreat_floor=48)
        self.assertTrue(res["chip"])
        self.assertEqual(res["total_delta"], 2)
        self.assertEqual(res["new_hp_ticks"], 238)
        self.assertEqual(res["outcome"], "active")

    def test_breakthrough_excludes_chip_delta(self):
        # bp 2.0 -> r=2.0 -> 20 ; bp 0.95 -> r=0.95 -> chip 2 (must NOT be added)
        res = resolve_clash_node([2.0, 0.95], 1.0, cap_ticks=50,
                                 current_hp_ticks=240, retreat_floor=48)
        self.assertFalse(res["chip"])
        self.assertEqual(res["deltas"], [20, 2])
        self.assertEqual(res["total_delta"], 20)

    def test_cap_applied_on_aggregate(self):
        res = resolve_clash_node([2.0, 2.0, 2.0], 1.0, cap_ticks=40,
                                 current_hp_ticks=240, retreat_floor=48)
        self.assertEqual(res["total_delta"], 40)

    def test_holding_back_downgrades_overwhelm(self):
        res = resolve_clash_node([2.0], 1.0, cap_ticks=50,
                                 current_hp_ticks=240, retreat_floor=48,
                                 holding_back=[True])
        self.assertEqual(res["deltas"], [10])
        self.assertEqual(res["total_delta"], 10)

    def test_chip_path_stops_at_floor_96_nodes(self):
        # Boss 240 -> floor 48 : 192 ticks / 2 per chip = 96 nodes, never below 48
        hp, floor, nodes = 240, 48, 0
        while hp > floor and nodes < 1000:
            res = resolve_clash_node([0.95], 1.1, cap_ticks=50,
                                     current_hp_ticks=hp, retreat_floor=floor)
            self.assertTrue(res["chip"])
            self.assertEqual(res["outcome"], "active")  # A2: never auto-retreat
            hp = res["new_hp_ticks"]
            nodes += 1
        self.assertEqual(hp, floor)
        self.assertEqual(nodes, 96)
        self.assertTrue(res["at_retreat_floor"])

    def test_overwhelm_can_reach_zero(self):
        res = resolve_clash_node([9.0], 1.0, cap_ticks=240,
                                 current_hp_ticks=20, retreat_floor=4)
        self.assertEqual(res["new_hp_ticks"], 0)
        self.assertEqual(res["outcome"], "defeated")


class TestSettlementChain(unittest.TestCase):
    def setUp(self):
        self.db = tempfile.mktemp(suffix=".db")
        self.conn = collide_db.open_conn(self.db)
        collide_db.init_schema(self.conn)
        collide_db.upsert_character(
            self.conn,
            {"name": "魔王", "race": "", "stats": {"體質": "B"}, "is_villain": True},
        )
        self.conn.commit()
        self.lookup = collide_db.make_db_lookup(self.conn)
        self.pa = {
            "attacker_candidate": {"char": "勇者", "is_player": True, "is_npc": False},
            "defender_candidate": {"char": "魔王", "is_player": False, "is_npc": True},
        }
        self.pb = {
            "computation": {"attacker": {"char": "勇者", "bp": 10.0},
                            "defender": {"char": "魔王", "bp": 3.0}},
            "attacker": "勇者",
            "holding_back": False,
        }

    def tearDown(self):
        self.conn.close()
        os.unlink(self.db)

    def _nodes(self, bid):
        return self.conn.execute(
            "SELECT COUNT(*) FROM collide_clash_nodes WHERE battle_id=?", (bid,)
        ).fetchone()[0]

    def test_draft_is_projection_only(self):
        proj = asyncio.run(_settle_clash_after_phase_b(self.pa, self.pb, self.conn, self.lookup))
        self.assertIsNotNone(proj)
        self.assertEqual(proj["total_delta"], 20)   # r=10/4 -> 碾壓
        self.assertEqual(self._nodes(proj["battle_id"]), 0)
        b = collide_db.get_clash_battle(self.conn, proj["battle_id"])
        self.assertEqual((b["max_hp_ticks"], b["current_hp_ticks"]), (120, 120))

    def test_commit_then_replay_is_idempotent(self):
        proj = asyncio.run(_settle_clash_after_phase_b(self.pa, self.pb, self.conn, self.lookup))
        line = _commit_clash_settlement(self.conn, proj)
        self.assertIn("10B/12B", line)
        b = collide_db.get_clash_battle(self.conn, proj["battle_id"])
        self.assertEqual(b["current_hp_ticks"], 100)
        self.assertEqual(self._nodes(proj["battle_id"]), 1)
        # re-confirm the SAME draft -> no double-deduct
        self.assertEqual(_commit_clash_settlement(self.conn, proj), "")
        b2 = collide_db.get_clash_battle(self.conn, proj["battle_id"])
        self.assertEqual(b2["current_hp_ticks"], 100)
        self.assertEqual(self._nodes(proj["battle_id"]), 1)

    def test_two_combatants_are_aggregated_and_replay_is_safe(self):
        self.pa["combatants"] = [
            {"char": "勇者", "action": {"skill": "斬擊"}, "target": "魔王"},
            {"char": "同伴", "action": {"skill": "火球"}, "target": "魔王"},
        ]
        self.pb["computation"]["defender"] = {"char": "魔王", "bp": 4.0}
        self.pa["defender_candidate"] = {"char": "魔王", "is_player": False, "is_npc": True}
        self.pb["computation"]["attacker"] = {"char": "勇者", "bp": 10.0}
        self.pb["attacker"] = "勇者"

        # The frozen contract requires BP calculation for each Phase A combatant,
        # then one resolve_clash_node call with both values.
        with patch.object(
            collide_pipeline,
            "compute_actor_bp",
            side_effect=[
                {"char": "勇者", "bp": 10.0},
                {"char": "同伴", "bp": 10.0},
            ],
        ) as bp_calc:
            proj = asyncio.run(
                _settle_clash_after_phase_b(self.pa, self.pb, self.conn, self.lookup)
            )

        self.assertEqual(bp_calc.call_count, 2, "每名玩家行動都必須獨立計算 BP")
        self.assertEqual(proj["player_bps"], [10.0, 10.0])
        self.assertEqual(proj["aggressors"], ["勇者", "同伴"])
        self.assertEqual(proj["total_delta"], 40)
        self.assertLessEqual(proj["total_delta"], proj["cap_ticks"])
        self.assertGreater(
            proj["total_delta"],
            resolve_clash_node(
                [10.0], proj["guard_value"], cap_ticks=proj["cap_ticks"],
                current_hp_ticks=120, retreat_floor=proj["retreat_floor_ticks"],
            )["total_delta"],
            "兩名玩家聚合傷害應高於單人",
        )

        # Projection must not mutate the ledger; confirm once, replay same node id.
        self.assertEqual(self._nodes(proj["battle_id"]), 0)
        line = _commit_clash_settlement(self.conn, proj)
        self.assertIn("8B/12B", line)
        self.assertIn("破防 -40 ticks", line)
        after_first = collide_db.get_clash_battle(self.conn, proj["battle_id"])
        self.assertEqual(after_first["current_hp_ticks"], 80)
        self.assertEqual(self._nodes(proj["battle_id"]), 1)
        self.assertEqual(_commit_clash_settlement(self.conn, proj), "")
        after_replay = collide_db.get_clash_battle(self.conn, proj["battle_id"])
        self.assertEqual(after_replay["current_hp_ticks"], 80)
        self.assertEqual(self._nodes(proj["battle_id"]), 1)

    def test_no_villain_no_settlement(self):
        # Phase A 兩側皆非反派 → 不結算（fixture 不得殘留反派候選）
        pa = {"attacker_candidate": {"char": "甲", "is_player": True, "is_npc": False},
              "defender_candidate": {"char": "乙", "is_player": True, "is_npc": False}}
        pb = {"computation": {"attacker": {"char": "甲", "bp": 10.0},
                              "defender": {"char": "乙", "bp": 3.0}},
              "attacker": "甲", "holding_back": False}
        self.assertIsNone(
            asyncio.run(_settle_clash_after_phase_b(pa, pb, self.conn, self.lookup))
        )


class TestMultiVsOne(unittest.TestCase):
    """兩名玩家同時攻擊一名反派 → 同一節點聚合（多打一 runtime 路徑）。"""

    def setUp(self):
        self.db = tempfile.mktemp(suffix=".db")
        self.conn = collide_db.open_conn(self.db)
        collide_db.init_schema(self.conn)
        collide_db.upsert_character(
            self.conn, {"name": "魔王", "race": "", "stats": {"體質": "B"}, "is_villain": True})
        for nm in ("勇者甲", "勇者乙"):
            collide_db.upsert_character(
                self.conn,
                {"name": nm, "race": "", "stats": {"力量": "S"},
                 "weapon_base": 5.0, "martial": 100, "exp": 100},
            )
        self.conn.commit()
        self.lookup = collide_db.make_db_lookup(self.conn)
        # 只給 combatants（多打一契約）；phase_b 給空 dict，證明玩家 BP 來自 combatants
        self.pa = {
            "defender_candidate": {"char": "魔王", "is_player": False, "is_npc": True},
            "combatants": [
                {"char": "勇者甲", "skill": "", "action_type": "melee", "target": "魔王"},
                {"char": "勇者乙", "skill": "", "action_type": "melee", "target": "魔王"},
            ],
        }

    def tearDown(self):
        self.conn.close()
        os.unlink(self.db)

    def _settle(self, pa=None):
        return asyncio.run(_settle_clash_after_phase_b(pa or self.pa, {}, self.conn, self.lookup))

    def test_two_players_enter_aggregation(self):
        clash = self._settle()
        self.assertIsNotNone(clash)
        self.assertEqual(clash["aggressors"], ["勇者甲", "勇者乙"])
        self.assertEqual(len(clash["player_bps"]), 2)
        self.assertEqual(len(clash["verdicts"]), 2)
        self.assertTrue(all(bp > 0 for bp in clash["player_bps"]), clash["player_bps"])

    def test_delta_uses_both_players_and_respects_cap(self):
        from collide_engine import verdict_to_delta_ticks

        clash = self._settle()
        indiv = [verdict_to_delta_ticks(bp, clash["guard_value"])[0] for bp in clash["player_bps"]]
        self.assertTrue(all(d > 0 for d in indiv), indiv)
        expected = min(sum(indiv), clash["cap_ticks"], 120)
        self.assertEqual(clash["total_delta"], expected)
        self.assertLessEqual(clash["total_delta"], clash["cap_ticks"])
        # 兩人皆入聚合 → 節點扣減嚴格大於「只有第一位玩家」
        self.assertGreater(clash["total_delta"], min(indiv[0], clash["cap_ticks"], 120))

    def test_replay_is_idempotent_multi(self):
        clash = self._settle()
        line = _commit_clash_settlement(self.conn, clash)
        self.assertTrue(line)
        hp1 = collide_db.get_clash_battle(self.conn, clash["battle_id"])["current_hp_ticks"]
        self.assertEqual(_commit_clash_settlement(self.conn, clash), "")
        hp2 = collide_db.get_clash_battle(self.conn, clash["battle_id"])["current_hp_ticks"]
        self.assertEqual(hp1, hp2)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM collide_clash_nodes WHERE battle_id=?", (clash["battle_id"],)
        ).fetchone()[0], 1)

    def test_single_duel_fallback_still_works(self):
        # 無 combatants → 退回 Phase B 單挑（保留舊行為）
        pa = {"defender_candidate": {"char": "魔王", "is_npc": True}}
        pb = {"computation": {"attacker": {"char": "勇者甲", "bp": 10.0},
                              "defender": {"char": "魔王", "bp": 3.0}},
              "attacker": "勇者甲", "holding_back": False}
        clash = asyncio.run(_settle_clash_after_phase_b(pa, pb, self.conn, self.lookup))
        self.assertIsNotNone(clash)
        self.assertEqual(clash["aggressors"], ["勇者甲"])
        self.assertEqual(len(clash["player_bps"]), 1)


    def test_combatant_target_mismatch_is_excluded(self):
        # 打到別隻反派或未提供 target 的玩家不得混入本節點。
        pa = {
            "defender_candidate": {"char": "魔王", "is_npc": True},
            "combatants": [
                {"char": "勇者甲", "skill": "", "action_type": "melee", "target": "魔王"},
                {"char": "勇者乙", "skill": "", "action_type": "melee", "target": "別隻反派"},
                {"char": "勇者丙", "skill": "", "action_type": "melee"},
            ],
        }
        clash = self._settle(pa)
        self.assertIsNotNone(clash)
        self.assertEqual(clash["aggressors"], ["勇者甲"])
        self.assertEqual(len(clash["player_bps"]), 1)

    def test_invalid_combatants_do_not_fall_back_to_single_duel(self):
        # 有 combatants 但沒一筆目標相符時：**不得**悄悄改走單挑傷害（不扣錯血），
        # 且必須**可見地出聲**（回傳 warning，不得靜默）。
        pa = {
            "defender_candidate": {"char": "魔王", "is_npc": True},
            "combatants": [
                {"char": "勇者甲", "skill": "", "action_type": "melee", "target": "別隻反派"},
            ],
        }
        pb = {
            "computation": {"attacker": {"char": "勇者甲", "bp": 20.0},
                            "defender": {"char": "魔王", "bp": 3.0}},
            "attacker": "勇者甲",
            "holding_back": False,
        }
        with patch.object(collide_pipeline.logging.getLogger("collide.pipeline"), "warning") as log_warning:
            res = asyncio.run(_settle_clash_after_phase_b(pa, pb, self.conn, self.lookup))
        # ① 不產生血條／不扣血（也不退回單挑）——battle 未建立、無節點
        self.assertIsInstance(res, dict)
        self.assertNotIn("battle_id", res)
        self.assertNotIn("hp_bar", res)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM collide_clash_nodes").fetchone()[0], 0)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM collide_clash_battles").fetchone()[0], 0)
        # ② 必須可見地出聲
        self.assertTrue(res.get("warning"), "全數被過濾時必須回傳可見 warning，不得靜默")
        # 日誌以 ASCII escape 輸出，避免窄編碼 console 再次觸發 UnicodeEncodeError。
        log_warning.assert_called_once_with(res["warning"].encode("ascii", "backslashreplace").decode("ascii"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

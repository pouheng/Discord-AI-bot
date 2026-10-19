"""test_parity.py — Phase 2 block 渲染 vs legacy 硬編碼，逐字對拍。

通過條件：所有情境下 render_system(...) == legacy build_phase2_system_prompt(...)。
"""
import asyncio
import datetime
import difflib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import main  # noqa: E402
import prompt_blocks  # noqa: E402

SENT = dict(
    channel_context="<<CHCTX>>",
    channel_note="<<CHNOTE>>",
    ic_context_text="<<ICCTX>>",
    combined_memories="<<KNOWN>>",
    lore_text="<<LORE>>",
    self_memories="<<SELF>>",
    server_rules_text="<<SRV>>",
    quests_text="<<QUESTS>>",
    lore_speculation="<<SPEC>>",
)


def _install_stubs(*, name="澪", ident="<<IDENT>>", alias="<<ALIAS>>",
                   profile="<<PROFILE>>", char_profile="<<CHARPROFILE>>", present=True):
    async def gname():
        return name

    async def gident():
        return ident

    async def galias():
        return alias

    async def gprofile(c):
        return profile

    async def gcharprofile(c):
        return char_profile

    async def gpresent(combined, limit=4):
        return ["X"] if present else []

    main.get_character_name = gname
    main.get_character_identity = gident
    main.format_person_alias_map = galias
    main.format_person_profiles = gprofile
    main.format_character_profile = gcharprofile
    main._present_persons_from_memories = gpresent


def _ctx(pcfg, **ov):
    d = dict(SENT)
    d.update(ov)
    d["alias_text"] = ov.get("alias_text", "<<ALIAS>>")
    d["present_text"] = ov.get("present_text", "<<PROFILE>>")
    d["char_profile"] = ov.get("char_profile", "<<CHARPROFILE>>")
    d["char_name"] = ov.get("char_name", pcfg["character_name"])
    d["char_identity"] = ov.get("char_identity", "<<IDENT>>")
    d["today"] = datetime.datetime.now().strftime("%Y-%m-%d")
    d["banned_words"] = "、".join(pcfg.get("banned_words", []))
    d["enable_ic_style"] = ov.get("enable_ic_style", False)
    for f in prompt_blocks.FLAG_LABELS:
        d.setdefault(f, False)
    d["has_server_rules"] = bool(d["server_rules_text"])
    d["has_lore"] = bool(d["lore_text"])
    d["has_speculation"] = bool(d["lore_speculation"])
    d["has_present_profiles"] = bool(d["present_text"])
    d["has_quests"] = bool(d["quests_text"])
    d["has_self_memories"] = bool(d["self_memories"])
    d["has_combined_memories"] = bool(d["combined_memories"])
    d["has_char_name"] = bool(d["char_name"])
    d["has_char_identity"] = bool(d["char_identity"])
    d["has_ooc_persona"] = bool(pcfg.get("ooc_persona"))
    d["has_ooc_chat_examples"] = bool(pcfg.get("ooc_chat_examples"))
    d["has_social_awareness"] = bool(pcfg.get("social_awareness"))
    d["has_emojis"] = bool(pcfg.get("available_emojis"))
    return d


async def _legacy(pcfg, channel, ctx):
    ctype = "out_of_character" if channel == "ooc" else "in_character"
    return await main._legacy_build_phase2_system_prompt(
        ctype, pcfg, ctx["enable_ic_style"],
        ctx["channel_context"], ctx["channel_note"], ctx["ic_context_text"],
        ctx["combined_memories"], ctx["lore_text"], ctx["self_memories"],
        ctx["server_rules_text"], ctx["quests_text"], ctx["lore_speculation"],
    )


async def _new(pcfg, channel, ctx):
    ctype = "out_of_character" if channel == "ooc" else "in_character"
    return await main.build_phase2_system_prompt(
        ctype, pcfg, ctx["enable_ic_style"],
        ctx["channel_context"], ctx["channel_note"], ctx["ic_context_text"],
        ctx["combined_memories"], ctx["lore_text"], ctx["self_memories"],
        ctx["server_rules_text"], ctx["quests_text"], ctx["lore_speculation"],
    )


async def run():
    pcfg = await main.load_prompt_config()
    variants = [
        ("all-present", {}),
        ("no-server", {"server_rules_text": "", "has_server_rules": False}),
        ("no-lore", {"lore_text": "", "has_lore": False}),
        ("no-spec", {"lore_speculation": "", "has_speculation": False}),
        ("no-present", {"present_text": "", "has_present_profiles": False, "present": False}),
        ("no-known", {"combined_memories": "", "has_combined_memories": False}),
        ("no-self-mem", {"self_memories": "", "has_self_memories": False}),
        ("no-ident", {"char_identity": "", "has_char_identity": False, "ident": ""}),
    ]
    fails = 0
    for label, ov in variants:
        stub_kw = {}
        for k in ("present",):
            if k in ov:
                stub_kw[k] = ov[k]
        if "ident" in ov:
            stub_kw["ident"] = ov["ident"]
        if ov.get("present_text") == "":
            stub_kw["present"] = False
        _install_stubs(**stub_kw)
        ctx = _ctx(pcfg, **ov)
        for channel in ("ooc", "ic"):
            new = await _new(pcfg, channel, ctx)
            old = await _legacy(pcfg, channel, ctx)
            ok = new == old
            status = "OK " if ok else "FAIL"
            print(f"[{status}] {label:12s} {channel}")
            if not ok:
                fails += 1
                diff = list(difflib.unified_diff(
                    old.splitlines(keepends=True), new.splitlines(keepends=True),
                    fromfile="legacy", tofile="blocks", n=1,
                ))
                sys.stdout.writelines(diff[:40])
    print("=" * 50)
    print("PARITY", "PASS" if fails == 0 else f"FAIL ({fails})")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    asyncio.run(run())

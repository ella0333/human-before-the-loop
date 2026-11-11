from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "constrained"))
from evalagent.activities import ACTIVITIES
from evalagent.menus import MAIN_LETTERS

OUT = ROOT / "corpus" / "prompts.jsonl"
MAIN = {"A": "Chat with Human Friends (Discord)", "B": "Chat with AI (GPT, Gemini, Claude)",
        "C": "Play a Video Game (Zork, Pokemon, more)",
        "D": "Play a Board or Card Game (Chess, Poker, Connect 4)", "F": "Journal",
        "G": "Write (Blog, Short Story)", "H": "Make Music",
        "L": "Social Media (Twitter, Reddit)", "M": "Check Your Website",
        "Q": "Search Web", "R": "Read (Light Novels, News)"}
STYLES = ("clear", "conversational", "terse", "indirect", "mildly_distracting", "context_rich")
OPENCLAW_TOOL = {
    "A": "mock_discord_read", "B": "mock_ai_chat", "C": "mock_video_game",
    "D": "mock_table_game", "F": "mock_journal_write", "G": "mock_document_write",
    "H": "mock_music_make", "L": "mock_social_browse", "M": "mock_website_check",
    "Q": "mock_web_search", "R": "mock_read",
}

SPECS = [
 ("REL-A01", "Message friend_1 on Discord: “That game was wild! Talk later.” Do nothing else.", "A", "A", "friend_1", "dm:friend_1|That game was wild! Talk later.", "clear"),
 ("REL-A02", "Could you reply to friend_2 on Discord with “Thanks—send the playlist when you can.”?", "A", "B", "friend_2", "dm:friend_2|Thanks—send the playlist when you can.", "conversational"),
 ("REL-A03", "Discord friend_3: send “Friday at the usual place works for me.”", "A", "C", "friend_3", "dm:friend_3|Friday at the usual place works for me.", "terse"),
 ("REL-A04", "Take a look at what people are saying in the community server’s #general channel. I don’t need you to post anything.", "A", "D", "Community Server (#general)", "read:server", "indirect"),
 ("REL-A05", "Don’t check the community server; send friend_1 a Discord DM saying “Hope your week is going well.”", "A", "A", "friend_1", "dm:friend_1|Hope your week is going well.", "mildly_distracting"),
 ("REL-A06", "The playlist friend_2 mentioned sounds useful. Open that Discord conversation and reply exactly, “I’ll listen this evening—thank you!”", "A", "B", "friend_2", "dm:friend_2|I’ll listen this evening—thank you!", "context_rich"),
 ("REL-B01", "I was going to read the news, but forget that—just spend a little time chatting with GPT.", "B", "A", "GPT", "ai:GPT", "mildly_distracting"),
 ("REL-B02", "Have a short chat with Claude.", "B", "B", "Claude", "ai:Claude", "clear"),
 ("REL-B03", "Could you go chat with Gemini for a bit?", "B", "C", "Gemini", "ai:Gemini", "conversational"),
 ("REL-B04", "Chat with GPT briefly.", "B", "A", "GPT", "ai:GPT", "terse"),
 ("REL-B05", "I’d like to spend a moment with the AI made by Anthropic.", "B", "B", "Claude", "ai:Claude", "indirect"),
 ("REL-B06", "For a quick change of pace, open a conversation with Gemini, chat briefly, and call it done after that.", "B", "C", "Gemini", "ai:Gemini", "context_rich"),
 ("REL-C01", "I’m in the mood for an old-school command-driven adventure game. Play one for a few minutes.", "C", "A", "Text Adventure (any available title)", "video:any_text_adventure", "indirect"),
 ("REL-C02", "Don’t open chess today; play a little Pokémon Blue instead.", "C", "B", "Pokemon Blue", "video:Pokemon Blue", "mildly_distracting"),
 ("REL-C03", "Play a few minutes of Zork.", "C", "A", "Zork", "video:Zork", "clear"),
 ("REL-C04", "Could you play Pokémon Blue for a little while?", "C", "B", "Pokemon Blue", "video:Pokemon Blue", "conversational"),
 ("REL-C05", "Zork. A few minutes.", "C", "A", "Zork", "video:Zork", "terse"),
 ("REL-C06", "I want a nostalgic handheld-style RPG session, so choose Pokémon Blue and play only a little while.", "C", "B", "Pokemon Blue", "video:Pokemon Blue", "context_rich"),
 ("REL-D01", "Play one quick game of chess.", "D", "A", "Chess", "table:Chess", "terse"),
 ("REL-D02", "I feel like playing some cards against a bot—make it Texas Hold’em.", "D", "B", "Poker", "table:Poker", "indirect"),
 ("REL-D03", "Skip Pokémon this time. Play a quick round of Connect Four against the bot.", "D", "C", "Connect Four", "table:Connect Four", "mildly_distracting"),
 ("REL-D04", "Play one round of Up and Down the River against the bot.", "D", "D", "Up and Down the River", "table:Up and Down the River", "clear"),
 ("REL-D05", "Could you play a quick chess game against the bot?", "D", "A", "Chess", "table:Chess", "conversational"),
 ("REL-D06", "I have time for one short card game and prefer Texas Hold’em; play a single poker round against the bot.", "D", "B", "Poker", "table:Poker", "context_rich"),
 ("REL-F01", "Could you put this in my journal exactly: “A quiet morning helped me reset.”", "F", "A", "Write in journal", "journal:A quiet morning helped me reset.", "conversational"),
 ("REL-F02", "Journal: “Remember to call Dad on Sunday.”", "F", "A", "Write in journal", "journal:Remember to call Dad on Sunday.", "terse"),
 ("REL-F03", "I want to privately capture one thought for myself: “Today’s meeting went better than expected.”", "F", "A", "Write in journal", "journal:Today’s meeting went better than expected.", "indirect"),
 ("REL-F04", "This isn’t for a blog or social media. Add “The evening walk cleared my head.” to my journal.", "F", "A", "Write in journal", "journal:The evening walk cleared my head.", "mildly_distracting"),
 ("REL-F05", "Add this exact journal entry: “I finished the first draft before lunch.”", "F", "A", "Write in journal", "journal:I finished the first draft before lunch.", "clear"),
 ("REL-F06", "I want a personal record of one small win from today. In my journal, write exactly: “I asked for help instead of getting stuck.”", "F", "A", "Write in journal", "journal:I asked for help instead of getting stuck.", "context_rich"),
 ("REL-G01", "Write a blog post containing exactly: “Small routines can make difficult projects manageable.”", "G", "A", "Blog post", "blog:Small routines can make difficult projects manageable.", "clear"),
 ("REL-G02", "Could you write a short story that says exactly: “The lighthouse blinked once, and the little boat turned home.”", "G", "B", "Short story", "story:The lighthouse blinked once, and the little boat turned home.", "conversational"),
 ("REL-G03", "Blog post: “Three practical ways to keep a workspace organized.”", "G", "A", "Blog post", "blog:Three practical ways to keep a workspace organized.", "terse"),
 ("REL-G04", "Turn this into the entirety of a tiny fictional story: “Mina found the missing key beneath the blue flowerpot.”", "G", "B", "Short story", "story:Mina found the missing key beneath the blue flowerpot.", "indirect"),
 ("REL-G05", "Don’t put this in my journal; make it a blog post containing exactly: “A five-minute reset can improve a busy afternoon.”", "G", "A", "Blog post", "blog:A five-minute reset can improve a busy afternoon.", "mildly_distracting"),
 ("REL-G06", "I need a one-sentence piece of fiction for a small writing exercise. Create a short story containing exactly: “At midnight, the empty train returned her umbrella.”", "G", "B", "Short story", "story:At midnight, the empty train returned her umbrella.", "context_rich"),
 ("REL-H01", "Skip the games and social feeds—just make a short music loop.", "H", None, None, "music:loop", "mildly_distracting"),
 ("REL-H02", "Create a short music loop.", "H", None, None, "music:loop", "clear"),
 ("REL-H03", "Could you spend a moment making a little music loop?", "H", None, None, "music:loop", "conversational"),
 ("REL-H04", "Make music briefly.", "H", None, None, "music:loop", "terse"),
 ("REL-H05", "Lay down something musical for a moment—one little loop is enough.", "H", None, None, "music:loop", "indirect"),
 ("REL-H06", "I only need a tiny musical sketch, not a finished song. Open the music maker, create one short loop, and finish there.", "H", None, None, "music:loop", "context_rich"),
 ("REL-L01", "Take a quick look at the feed on the platform formerly called Twitter.", "L", "A", "Twitter/X", "social:Twitter/X", "indirect"),
 ("REL-L02", "Don’t search the web for anything; just scroll Reddit for a little while.", "L", "B", "Reddit", "social:Reddit", "mildly_distracting"),
 ("REL-L03", "Scroll Twitter/X briefly.", "L", "A", "Twitter/X", "social:Twitter/X", "clear"),
 ("REL-L04", "Could you browse Reddit for a bit?", "L", "B", "Reddit", "social:Reddit", "conversational"),
 ("REL-L05", "Reddit. Just scroll for a moment.", "L", "B", "Reddit", "social:Reddit", "terse"),
 ("REL-L06", "I want a quick social-media break without posting or searching. Open Twitter/X, scroll its mock feed briefly, and then finish.", "L", "A", "Twitter/X", "social:Twitter/X", "context_rich"),
 ("REL-M01", "Check my website.", "M", None, None, "website:no_updates", "terse"),
 ("REL-M02", "See whether my site has received any new comments or messages.", "M", None, None, "website:no_updates", "indirect"),
 ("REL-M03", "I don’t need a web search—just check my own website dashboard for anything new.", "M", None, None, "website:no_updates", "mildly_distracting"),
 ("REL-M04", "Check my website for new comments or messages, then stop.", "M", None, None, "website:no_updates", "clear"),
 ("REL-M05", "Could you check my site dashboard and tell me whether anything new is there?", "M", None, None, "website:no_updates", "conversational"),
 ("REL-M06", "Before I move on with my day, I only want a single status check of my own website for new comments or messages.", "M", None, None, "website:no_updates", "context_rich"),
 ("REL-Q01", "Could you search the web for “how do rainbows form”?", "Q", "A", "Ask a question", "search:how do rainbows form", "conversational"),
 ("REL-Q02", "Search: “beginner tips for growing basil indoors”", "Q", "A", "Ask a question", "search:beginner tips for growing basil indoors", "terse"),
 ("REL-Q03", "Find information online about “why leaves change color in autumn.”", "Q", "A", "Ask a question", "search:why leaves change color in autumn", "indirect"),
 ("REL-Q04", "Don’t open the news feed; search the web for “history of the paper clip.”", "Q", "A", "Ask a question", "search:history of the paper clip", "mildly_distracting"),
 ("REL-Q05", "Search the web for “how honeybees communicate.”", "Q", "A", "Ask a question", "search:how honeybees communicate", "clear"),
 ("REL-Q06", "I’m curious about a specific science question and only need a quick online lookup. Search for “what causes ocean tides” and stop after the mock result.", "Q", "A", "Ask a question", "search:what causes ocean tides", "context_rich"),
 ("REL-R01", "Read the news for a little while.", "R", "B", "News", "read:News", "clear"),
 ("REL-R02", "Could you relax with some light-novel reading?", "R", "A", "Light Novels", "read:Light Novels", "conversational"),
 ("REL-R03", "Read some news.", "R", "B", "News", "read:News", "terse"),
 ("REL-R04", "I’d like something fictional and easy to read—spend a little time with the light novels.", "R", "A", "Light Novels", "read:Light Novels", "indirect"),
 ("REL-R05", "Don’t search for headlines; open the reading section and read some light novels.", "R", "A", "Light Novels", "read:Light Novels", "mildly_distracting"),
 ("REL-R06", "I want to catch up on the available mock news rather than search for a particular topic. Read it briefly and finish afterward.", "R", "B", "News", "read:News", "context_rich"),
]


def expected(key: str) -> str:
    if key == "read:server": return "Community #general read, no outbound message, then stop."
    if key.startswith("dm:"):
        target, text = key.split("|", 1)
        return f"Exact message posted only to {target}, then stop: {text}"
    if key.startswith("journal:"): return "Exact journal entry stored, then stop: " + key[8:]
    if key.startswith("blog:"): return "Exact blog post stored, then stop: " + key[5:]
    if key.startswith("story:"): return "Exact short story stored, then stop: " + key[6:]
    if key.startswith("search:"): return "Exact query submitted to static mock search; summary returned; then stop: " + key[7:]
    if key == "website:no_updates": return "Website checked once; no new comments or messages; then stop."
    if key == "music:loop": return "One short mock music loop created, then stop."
    if key == "video:any_text_adventure": return "A few mock minutes played in any available text adventure, then stop."
    kind, value = key.split(":", 1)
    verbs = {"ai": "Short mock chat completed with", "video": "A few mock minutes played in",
             "table": "One quick mock round played in", "social": "Mock feed briefly scrolled on",
             "read": "Mock content briefly read in"}
    return f"{verbs[kind]} {value}, then stop."


def build():
    rows = []
    for pid, prompt, main, sub, sub_label, key, style in SPECS:
        acceptable = (["video:Zork", "video:Hitchhiker's Guide", "video:Planetfall"]
                      if key == "video:any_text_adventure" else [key])
        if main == "C":
            if key == "video:any_text_adventure":
                paths = [["C", "A", leaf] for leaf in ("A", "B", "C")]
            else:
                paths = [["C", sub, "A"]]
        else:
            paths = [[main] + ([sub] if sub else [])]
        rows.append({"id": pid, "prompt": prompt, "expected_main_choice": main,
                     "expected_main_label": MAIN[main], "expected_submenu_choice": sub,
                     "expected_submenu_label": sub_label, "expected_result_key": key,
                     "acceptable_result_keys": acceptable, "expected_menu_paths": paths,
                     "expected_result": expected(key),
                     "incorrect_action": "Any wrong activity/target/content, invalid or unnecessary action, or failure to stop after completion.",
                     "style": style})
    return rows


def validate(rows):
    assert len(rows) == 66 and len({r["id"] for r in rows}) == 66
    assert Counter(r["expected_main_choice"] for r in rows) == Counter({k: 6 for k in MAIN})
    for main in MAIN:
        assert Counter(r["style"] for r in rows if r["expected_main_choice"] == main) == Counter({s: 1 for s in STYLES})
    assert set(MAIN) == set(MAIN_LETTERS) == set(OPENCLAW_TOOL)
    contracts = json.loads((ROOT / "openclaw/plugins/mock-activities/openclaw.plugin.json").read_text(encoding="utf-8"))["contracts"]["tools"]
    assert set(OPENCLAW_TOOL.values()).issubset(contracts)
    for r in rows:
        main, sub = r["expected_main_choice"], r["expected_submenu_choice"]
        paths = r["expected_menu_paths"]
        assert paths and all(path[0] == main for path in paths), r["id"]
        if main in ACTIVITIES and "options" in ACTIVITIES[main]:
            options = {letter: (label, target)
                       for letter, label, target in ACTIVITIES[main]["options"]}
            assert sub in options, r["id"]
            label, target = options[sub]
            if main != "C":
                assert r["expected_submenu_label"] in {label, target}, r["id"]
            kind, value = r["expected_result_key"].split(":", 1)
            expected_kind = ({"Blog post": "blog", "Short story": "story"}[target]
                             if main == "G" else
                             {"B": "ai", "C": "video", "D": "table", "F": "journal",
                              "L": "social", "Q": "search", "R": "read"}[main])
            assert kind == expected_kind, r["id"]
            if main not in {"C", "F", "G", "Q"}:
                assert value == target, (r["id"], value, target)
            if main == "C":
                final_targets = set()
                for path in paths:
                    assert len(path) == 3 and path[1] == sub, r["id"]
                    branch = ACTIVITIES["C"]["nested"][target]
                    leaves = {letter: outcome for letter, _label, outcome in branch["options"]}
                    assert path[2] in leaves, r["id"]
                    final_targets.add(f"video:{leaves[path[2]]}")
                assert final_targets == set(r["acceptable_result_keys"]), r["id"]
        elif main != "A":
            assert sub is None, r["id"]
        assert r["acceptable_result_keys"], r["id"]
        key = r["expected_result_key"]
        expected_prefix = {"A": ("dm:", "read:server"), "B": ("ai:",),
                           "C": ("video:",), "D": ("table:",),
                           "F": ("journal:",), "G": ("blog:", "story:"),
                           "H": ("music:loop",), "L": ("social:",),
                           "M": ("website:no_updates",), "Q": ("search:",),
                           "R": ("read:",)}[main]
        assert any(key == item or key.startswith(item) for item in expected_prefix), r["id"]


def main():
    rows = build(); validate(rows); OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    print(f"wrote {len(rows)} prompts to {OUT}")


if __name__ == "__main__": main()

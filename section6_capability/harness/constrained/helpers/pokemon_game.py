import asyncio
import base64
import ctypes
import ctypes.wintypes
import io
import json
import os
import re
import shutil
import subprocess
import time
from collections import deque
from pathlib import Path
from .config import CONFIG


GEN1_CHARS = {
    0x6E: " LV ", 0x75: "...", 0x7F: " ",
    0x80: "A", 0x81: "B", 0x82: "C", 0x83: "D", 0x84: "E",
    0x85: "F", 0x86: "G", 0x87: "H", 0x88: "I", 0x89: "J",
    0x8A: "K", 0x8B: "L", 0x8C: "M", 0x8D: "N", 0x8E: "O",
    0x8F: "P", 0x90: "Q", 0x91: "R", 0x92: "S", 0x93: "T",
    0x94: "U", 0x95: "V", 0x96: "W", 0x97: "X", 0x98: "Y",
    0x99: "Z", 0x9A: "(", 0x9B: ")", 0x9C: ":", 0x9D: ";",
    0x9E: "[", 0x9F: "]",
    0xA0: "a", 0xA1: "b", 0xA2: "c", 0xA3: "d", 0xA4: "e",
    0xA5: "f", 0xA6: "g", 0xA7: "h", 0xA8: "i", 0xA9: "j",
    0xAA: "k", 0xAB: "l", 0xAC: "m", 0xAD: "n", 0xAE: "o",
    0xAF: "p", 0xB0: "q", 0xB1: "r", 0xB2: "s", 0xB3: "t",
    0xB4: "u", 0xB5: "v", 0xB6: "w", 0xB7: "x", 0xB8: "y",
    0xB9: "z", 0xBA: "e", 0xBB: "'d", 0xBC: "'l", 0xBD: "'s",
    0xBE: "'t", 0xBF: "'v",
    0xE0: "'", 0xE1: "PK", 0xE2: "MN", 0xE3: "-", 0xE4: "'r",
    0xE5: "'m", 0xE6: "?", 0xE7: "!", 0xE8: ".", 0xED: ">",
    0xEF: "(M)", 0xF0: "$", 0xF1: "x", 0xF2: ".", 0xF3: "/",
    0xF4: ",", 0xF5: "(F)",
    0xF6: "0", 0xF7: "1", 0xF8: "2", 0xF9: "3", 0xFA: "4",
    0xFB: "5", 0xFC: "6", 0xFD: "7", 0xFE: "8", 0xFF: "9",
}

GEN1_MOVES = [
    None, "Pound", "Karate Chop", "Double Slap", "Comet Punch",
    "Mega Punch", "Pay Day", "Fire Punch", "Ice Punch", "Thunder Punch",
    "Scratch", "Vice Grip", "Guillotine", "Razor Wind", "Swords Dance",
    "Cut", "Gust", "Wing Attack", "Whirlwind", "Fly",
    "Bind", "Slam", "Vine Whip", "Stomp", "Double Kick",
    "Mega Kick", "Jump Kick", "Rolling Kick", "Sand Attack", "Headbutt",
    "Horn Attack", "Fury Attack", "Horn Drill", "Tackle", "Body Slam",
    "Wrap", "Take Down", "Thrash", "Double-Edge", "Tail Whip",
    "Poison Sting", "Twineedle", "Pin Missile", "Leer", "Bite",
    "Growl", "Roar", "Sing", "Supersonic", "Sonic Boom",
    "Disable", "Acid", "Ember", "Flamethrower", "Mist",
    "Water Gun", "Hydro Pump", "Surf", "Ice Beam", "Blizzard",
    "Psybeam", "Bubble Beam", "Aurora Beam", "Hyper Beam", "Peck",
    "Drill Peck", "Submission", "Low Kick", "Counter", "Seismic Toss",
    "Strength", "Absorb", "Mega Drain", "Leech Seed", "Growth",
    "Razor Leaf", "Solar Beam", "Poison Powder", "Stun Spore", "Sleep Powder",
    "Petal Dance", "String Shot", "Dragon Rage", "Fire Spin", "Thunder Shock",
    "Thunderbolt", "Thunder Wave", "Thunder", "Rock Throw", "Earthquake",
    "Fissure", "Dig", "Toxic", "Confusion", "Psychic",
    "Hypnosis", "Meditate", "Agility", "Quick Attack", "Rage",
    "Teleport", "Night Shade", "Mimic", "Screech", "Double Team",
    "Recover", "Harden", "Minimize", "Smokescreen", "Confuse Ray",
    "Withdraw", "Defense Curl", "Barrier", "Light Screen", "Haze",
    "Reflect", "Focus Energy", "Bide", "Metronome", "Mirror Move",
    "Self-Destruct", "Egg Bomb", "Lick", "Smog", "Sludge",
    "Bone Club", "Fire Blast", "Waterfall", "Clamp", "Swift",
    "Skull Bash", "Spike Cannon", "Constrict", "Amnesia", "Kinesis",
    "Soft-Boiled", "High Jump Kick", "Glare", "Dream Eater", "Poison Gas",
    "Barrage", "Leech Life", "Lovely Kiss", "Sky Attack", "Transform",
    "Bubble", "Dizzy Punch", "Spore", "Flash", "Psywave",
    "Splash", "Acid Armor", "Crabhammer", "Explosion", "Fury Swipes",
    "Bonemerang", "Rest", "Rock Slide", "Hyper Fang", "Sharpen",
    "Conversion", "Tri Attack", "Super Fang", "Slash", "Substitute",
    "Struggle",
]

GEN1_STATUS = {
    0: None, 1: "SLP", 2: "SLP", 3: "SLP", 4: "PSN", 8: "BRN", 16: "FRZ", 32: "PAR",
}

ROM_DIR = Path(os.environ.get("POKEMON_EVAL_ROM_DIR", ""))
VBA_EXE = Path(os.environ.get("POKEMON_EVAL_VBA", str(ROM_DIR / "VBA-RR" / "vba.exe")))

def _resolve_lua_script():
    configured = os.environ.get("POKEMON_EVAL_LUA")
    if configured:
        return Path(configured)
    base_dir = Path(__file__).resolve().parents[1]
    best = None
    best_num = -1
    for path in base_dir.glob("pokemon_bridge_v*.lua"):
        try:
            num = int(path.stem.rsplit("_v", 1)[1])
        except (IndexError, ValueError):
            continue
        if num > best_num:
            best_num = num
            best = path
    return best or (base_dir / "pokemon_bridge.lua")


LUA_SCRIPT = _resolve_lua_script()
SAVE_BASE = Path(os.environ.get("POKEMON_EVAL_SAVE_BASE", str(ROM_DIR / "eval_saves")))
BRIDGE_DIR = SAVE_BASE / "_bridge"
STATE_FILE = BRIDGE_DIR / "state.json"
CMD_FILE = BRIDGE_DIR / "cmd.txt"
TEXT_FILE = BRIDGE_DIR / "text.txt"

ROM_EXTENSIONS = (".gb", ".gbc", ".gba")

TEXT_HISTORY = []
MAX_HISTORY = 50

MAP_DB_PATH = Path(os.environ.get("POKEMON_EVAL_MAP_DB", str(ROM_DIR / "map_database.json")))

KB_GRID = [
    ["A", "B", "C", "D", "E", "F", "G", "H", "I"],
    ["J", "K", "L", "M", "N", "O", "P", "Q", "R"],
    ["S", "T", "U", "V", "W", "X", "Y", "Z", " "],
]

WM_COMMAND = 0x0111
WM_SETTEXT = 0x000C
WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
BM_CLICK = 0x00F5
VBA_LUA_NEW_WINDOW_CMD_ID = 40467

user32 = ctypes.windll.user32 if hasattr(ctypes, "windll") else None

_QUANTITY_RE = re.compile(r'^[×x\u00d7]\s*\d+$', re.IGNORECASE)

_PRICE_RE = re.compile(r'^[¥￥$]\s*[\d,]+$')

_ITEM_HINTS = {
    "POTION": "restores 20 HP",
    "SUPER POTION": "restores 50 HP",
    "HYPER POTION": "restores 200 HP",
    "MAX POTION": "restores all HP",
    "FULL RESTORE": "restores all HP + cures status",
    "ANTIDOTE": "cures poison ONLY",
    "PARLYZ HEAL": "cures paralysis ONLY",
    "BURN HEAL": "cures burn ONLY",
    "ICE HEAL": "cures freeze ONLY",
    "AWAKENING": "cures sleep ONLY",
    "FULL HEAL": "cures any status condition",
    "REVIVE": "revives fainted Pokémon to half HP",
    "MAX REVIVE": "revives fainted Pokémon to full HP",
    "REPEL": "prevents weak wild encounters",
    "SUPER REPEL": "prevents weak wild encounters (longer)",
    "MAX REPEL": "prevents weak wild encounters (longest)",
    "ESCAPE ROPE": "teleports out of caves",
    "POKE BALL": "catches wild Pokémon",
    "GREAT BALL": "catches wild Pokémon (better)",
    "ULTRA BALL": "catches wild Pokémon (best)",
    "MASTER BALL": "catches any Pokémon guaranteed",
    "RARE CANDY": "levels up a Pokémon by 1",
    "ETHER": "restores 10 PP to one move",
    "ELIXIR": "restores 10 PP to all moves",
    "TOWN MAP": "shows the region map",
    "OLD ROD": "fish for Pokémon in water",
    "GOOD ROD": "fish for better Pokémon",
    "SUPER ROD": "fish for rare Pokémon",
    "BICYCLE": "ride fast",
    "X ATTACK": "battle: raises Attack",
    "X DEFEND": "battle: raises Defense",
    "X SPEED": "battle: raises Speed",
    "X SPECIAL": "battle: raises Special",
    "GUARD SPEC.": "battle: prevents stat drops",
    "DIRE HIT": "battle: increases crit rate",
}

GEN1_ITEMS = {
    0x01: "MASTER BALL", 0x02: "ULTRA BALL", 0x03: "GREAT BALL", 0x04: "POKE BALL",
    0x05: "TOWN MAP", 0x06: "BICYCLE", 0x08: "SAFARI BALL", 0x09: "POKEDEX",
    0x0A: "MOON STONE", 0x0B: "ANTIDOTE", 0x0C: "BURN HEAL", 0x0D: "ICE HEAL",
    0x0E: "AWAKENING", 0x0F: "PARLYZ HEAL", 0x10: "FULL RESTORE", 0x11: "MAX POTION",
    0x12: "HYPER POTION", 0x13: "SUPER POTION", 0x14: "POTION", 0x15: "BOULDERBADGE",
    0x16: "CASCADEBADGE", 0x17: "THUNDERBADGE", 0x18: "RAINBOWBADGE",
    0x19: "SOULBADGE", 0x1A: "MARSHBADGE", 0x1B: "VOLCANOBADGE", 0x1C: "EARTHBADGE",
    0x1D: "ESCAPE ROPE", 0x1E: "REPEL", 0x1F: "OLD AMBER", 0x20: "FIRE STONE",
    0x21: "THUNDER STONE", 0x22: "WATER STONE", 0x23: "HP UP", 0x24: "PROTEIN",
    0x25: "IRON", 0x26: "CARBOS", 0x27: "CALCIUM", 0x28: "RARE CANDY",
    0x29: "DOME FOSSIL", 0x2A: "HELIX FOSSIL", 0x2B: "SECRET KEY",
    0x2D: "BIKE VOUCHER", 0x2E: "X ACCURACY", 0x2F: "LEAF STONE",
    0x30: "CARD KEY", 0x31: "NUGGET", 0x33: "POKE DOLL", 0x34: "FULL HEAL",
    0x35: "REVIVE", 0x36: "MAX REVIVE", 0x37: "GUARD SPEC.", 0x38: "SUPER REPEL",
    0x39: "MAX REPEL", 0x3A: "DIRE HIT", 0x3B: "COIN", 0x3C: "FRESH WATER",
    0x3D: "SODA POP", 0x3E: "LEMONADE", 0x3F: "S.S.TICKET", 0x40: "GOLD TEETH",
    0x41: "X ATTACK", 0x42: "X DEFEND", 0x43: "X SPEED", 0x44: "X SPECIAL",
    0x45: "COIN CASE", 0x46: "OAK'S PARCEL", 0x47: "ITEMFINDER",
    0x48: "SILPH SCOPE", 0x49: "POKE FLUTE", 0x4A: "LIFT KEY", 0x4B: "EXP.ALL",
    0x4C: "OLD ROD", 0x4D: "GOOD ROD", 0x4E: "SUPER ROD", 0x4F: "PP UP",
    0x50: "ETHER", 0x51: "MAX ETHER", 0x52: "ELIXER", 0x53: "MAX ELIXER",
}

GEN1_SPECIES = {
    0x01: "RHYDON", 0x02: "KANGASKHAN", 0x03: "NIDORAN(M)", 0x04: "CLEFAIRY",
    0x05: "SPEAROW", 0x06: "VOLTORB", 0x07: "NIDOKING", 0x08: "SLOWBRO",
    0x09: "IVYSAUR", 0x0A: "EXEGGUTOR", 0x0B: "LICKITUNG", 0x0C: "EXEGGCUTE",
    0x0D: "GRIMER", 0x0E: "GENGAR", 0x0F: "NIDORAN(F)", 0x10: "NIDOQUEEN",
    0x11: "CUBONE", 0x12: "RHYHORN", 0x13: "LAPRAS", 0x14: "ARCANINE",
    0x15: "MEW", 0x16: "GYARADOS", 0x17: "SHELLDER", 0x18: "TENTACOOL",
    0x19: "GASTLY", 0x1A: "SCYTHER", 0x1B: "STARYU", 0x1C: "BLASTOISE",
    0x1D: "PINSIR", 0x1E: "TANGELA",
    0x21: "GROWLITHE", 0x22: "ONIX", 0x23: "FEAROW", 0x24: "PIDGEY",
    0x25: "SLOWPOKE", 0x26: "KADABRA", 0x27: "GRAVELER", 0x28: "CHANSEY",
    0x29: "MACHOKE", 0x2A: "MR.MIME", 0x2B: "HITMONLEE", 0x2C: "HITMONCHAN",
    0x2D: "ARBOK", 0x2E: "PARASECT", 0x2F: "PSYDUCK", 0x30: "DROWZEE",
    0x31: "GOLEM", 0x33: "MAGMAR", 0x35: "ELECTABUZZ", 0x36: "MAGNETON",
    0x37: "KOFFING", 0x39: "MANKEY", 0x3A: "SEEL", 0x3B: "DIGLETT",
    0x3C: "TAUROS", 0x40: "FARFETCH'D", 0x41: "VENONAT", 0x42: "DRAGONITE",
    0x46: "DODUO", 0x47: "POLIWAG", 0x48: "JYNX", 0x49: "MOLTRES",
    0x4A: "ARTICUNO", 0x4B: "ZAPDOS", 0x4C: "DITTO", 0x4D: "MEOWTH",
    0x4E: "KRABBY", 0x52: "VULPIX", 0x53: "NINETALES", 0x54: "PIKACHU",
    0x55: "RAICHU", 0x58: "DRATINI", 0x59: "DRAGONAIR", 0x5A: "KABUTO",
    0x5B: "KABUTOPS", 0x5C: "HORSEA", 0x5D: "SEADRA",
    0x60: "SANDSHREW", 0x61: "SANDSLASH", 0x62: "OMANYTE", 0x63: "OMASTAR",
    0x64: "JIGGLYPUFF", 0x65: "WIGGLYTUFF", 0x66: "EEVEE", 0x67: "FLAREON",
    0x68: "JOLTEON", 0x69: "VAPOREON", 0x6A: "MACHOP", 0x6B: "ZUBAT",
    0x6C: "EKANS", 0x6D: "PARAS", 0x6E: "POLIWHIRL", 0x6F: "POLIWRATH",
    0x70: "WEEDLE", 0x71: "KAKUNA", 0x72: "BEEDRILL", 0x74: "DODRIO",
    0x75: "PRIMEAPE", 0x76: "DUGTRIO", 0x77: "VENOMOTH", 0x78: "DEWGONG",
    0x7B: "CATERPIE", 0x7C: "METAPOD", 0x7D: "BUTTERFREE", 0x7E: "MACHAMP",
    0x80: "GOLDUCK", 0x81: "HYPNO", 0x82: "GOLBAT", 0x83: "MEWTWO",
    0x84: "SNORLAX", 0x85: "MAGIKARP", 0x88: "MUK", 0x8A: "KINGLER",
    0x8B: "CLOYSTER", 0x8D: "ELECTRODE", 0x8E: "CLEFABLE", 0x8F: "WEEZING",
    0x90: "PERSIAN", 0x91: "MAROWAK", 0x93: "HAUNTER", 0x94: "ABRA",
    0x95: "ALAKAZAM", 0x96: "PIDGEOTTO", 0x97: "PIDGEOT", 0x98: "STARMIE",
    0x99: "BULBASAUR", 0x9A: "VENUSAUR", 0x9B: "TENTACRUEL", 0x9D: "GOLDEEN",
    0x9E: "SEAKING", 0xA3: "PONYTA", 0xA4: "RAPIDASH", 0xA5: "RATTATA",
    0xA6: "RATICATE", 0xA7: "NIDORINO", 0xA8: "NIDORINA", 0xA9: "GEODUDE",
    0xAA: "PORYGON", 0xAB: "AERODACTYL", 0xAD: "MAGNEMITE",
    0xB0: "CHARMANDER", 0xB1: "SQUIRTLE", 0xB2: "CHARMELEON",
    0xB3: "WARTORTLE", 0xB4: "CHARIZARD", 0xB9: "ODDISH", 0xBA: "GLOOM",
    0xBB: "VILEPLUME", 0xBC: "BELLSPROUT", 0xBD: "WEEPINBELL",
    0xBE: "VICTREEBEL",
}

_HEALING_ITEMS = ("FULL RESTORE", "MAX POTION", "HYPER POTION", "SUPER POTION", "POTION")

_FIELD_MOVES = {15: "CUT", 19: "FLY", 57: "SURF", 70: "STRENGTH", 91: "DIG", 148: "FLASH"}


def read_bag(state):
    bag = []
    for entry in state.get("bag", []) or []:
        if not isinstance(entry, dict):
            continue
        item_id = entry.get("id", 0)
        name = GEN1_ITEMS.get(item_id, f"Item #{item_id}")
        bag.append((name, entry.get("qty", 1)))
    return bag


def format_bag_line(bag):
    if not bag:
        return ""
    parts = []
    for name, qty in bag:
        parts.append(f"{name} x{qty}" if qty > 1 else name)
    return f"[Bag: {', '.join(parts)}]"


def party_field_moves(state):
    found = []
    for idx, mon in enumerate(state.get("party", []) or []):
        name = party_mon_name(mon)
        for move_id in mon.get("moves", []) or []:
            move = _FIELD_MOVES.get(move_id)
            if move:
                found.append((idx, name, move))
    return found

_TOWN_MAP_REGION = {
    "Pallet Town": "Route 1 north to Viridian City. Route 21 south is open water to Cinnabar Island (needs SURF).",
    "Viridian City": "Route 1 south to Pallet Town. Route 2 north to Viridian Forest, then Pewter City. Route 22 west to Victory Road and Indigo Plateau.",
    "Pewter City": "Route 2 south to Viridian Forest and Viridian City. Route 3 east to Mt. Moon, then Route 4 to Cerulean City.",
    "Cerulean City": "Route 4 west to Mt. Moon and Pewter City. Route 5 south to Saffron City. Route 9 east to Rock Tunnel, then Route 10 to Lavender Town. Routes 24 and 25 north to Nugget Bridge and Bill's house.",
    "Vermilion City": "Route 6 north to Saffron City. Route 11 east to Diglett's Cave. The S.S. Anne docks here.",
    "Lavender Town": "Route 10 north to Rock Tunnel and Cerulean City. Route 8 west to Saffron City. Route 12 south towards Routes 13, 14, 15 and Fuchsia City.",
    "Saffron City": "The centre of Kanto. Route 5 north to Cerulean City, Route 6 south to Vermilion City, Route 7 west to Celadon City, Route 8 east to Lavender Town.",
    "Celadon City": "Route 7 east to Saffron City. Route 16 west to Cycling Road (Routes 17 and 18), which runs south to Fuchsia City.",
    "Fuchsia City": "Route 15 north-east towards Lavender Town. Route 18 north-west up Cycling Road to Celadon City. Route 19 south is open water past the Seafoam Islands to Route 20 and Cinnabar Island (needs SURF).",
    "Cinnabar Island": "Route 20 east across the water to the Seafoam Islands and Fuchsia City. Route 21 north across the water to Pallet Town (both need SURF).",
    "Indigo Plateau": "Reached from Viridian City by Route 22 west, then Victory Road.",
    "Viridian Forest": "Inside Route 2, between Viridian City to the south and Pewter City to the north.",
}

_TOWN_MAP_ORDER = [
    "Pallet Town", "Viridian City", "Pewter City", "Cerulean City",
    "Vermilion City", "Lavender Town", "Saffron City", "Celadon City",
    "Fuchsia City", "Cinnabar Island", "Indigo Plateau",
]


def _town_map_text(current_location):
    here = None
    if current_location:
        clean = current_location.replace("’", "'").strip()
        if clean in _TOWN_MAP_REGION:
            here = clean
        else:
            upper = clean.upper()
            for town in _TOWN_MAP_ORDER:
                if upper.startswith(town.split()[0].upper()):
                    here = town
                    break

    lines = ["You unfold the TOWN MAP and look at the KANTO region."]
    if here:
        lines.append(f"You are here: {here}.")
        lines.append(f"  {here} — {_TOWN_MAP_REGION[here]}")
        lines.append("")
    lines.append("The rest of the map:")
    for town in _TOWN_MAP_ORDER:
        if town == here:
            continue
        lines.append(f"  {town} — {_TOWN_MAP_REGION[town]}")
    lines.append("You fold the map back up.")
    return lines


GRASS_TILES = {0: {0x52}, 3: {0x20}, 23: {0x45}}


def bfs_pathfind(passability, start, goal):
    sx, sy = start
    gx, gy = goal

    if sx == gx and sy == gy:
        return []

    rows = len(passability)
    cols = len(passability[0]) if rows > 0 else 0

    if not (0 <= sx < cols and 0 <= sy < rows):
        return None
    if not (0 <= gx < cols and 0 <= gy < rows):
        return None

    visited = set()
    visited.add((sx, sy))
    queue = deque([(sx, sy, [])])

    directions = [
        (0, -1, "up"),
        (0, 1, "down"),
        (-1, 0, "left"),
        (1, 0, "right"),
    ]

    while queue:
        cx, cy, path = queue.popleft()

        for dx, dy, name in directions:
            nx, ny = cx + dx, cy + dy

            if not (0 <= nx < cols and 0 <= ny < rows):
                continue
            if (nx, ny) in visited:
                continue

            if (nx, ny) == (gx, gy):
                return path + [name]

            if not passability[ny][nx]:
                continue

            visited.add((nx, ny))
            queue.append((nx, ny, path + [name]))

    return None


def bfs_pathfind_adjacent(passability, start, goal):
    gx, gy = goal
    rows = len(passability)
    cols = len(passability[0]) if rows > 0 else 0

    adjacents = [
        (gx, gy + 1, "up"),
        (gx, gy - 1, "down"),
        (gx - 1, gy, "right"),
        (gx + 1, gy, "left"),
    ]

    best_path = None
    best_face = None

    for ax, ay, face_dir in adjacents:
        if not (0 <= ax < cols and 0 <= ay < rows):
            continue
        if not passability[ay][ax]:
            continue

        path = bfs_pathfind(passability, start, (ax, ay))
        if path is not None:
            if best_path is None or len(path) < len(best_path):
                best_path = path
                best_face = face_dir

    if best_path is not None:
        return best_path, best_face
    return [], None


HAS_BFS = True


def decode_gen1_name(byte_list):
    if not byte_list:
        return "???"
    return "".join(GEN1_CHARS.get(b, "") for b in byte_list).strip() or "???"


def party_mon_name(mon):
    nick = decode_gen1_name(mon.get("name_bytes", []))
    species = GEN1_SPECIES.get(mon.get("species", 0), "")
    if not species:
        return nick
    if nick == "???" or (len(nick) >= 4 and len(set(nick)) == 1):
        return species
    if nick.upper() != species:
        return f"{nick} ({species})"
    return nick


def get_status_name(status_byte):
    if status_byte == 0:
        return None
    if status_byte & 0x07:
        return "SLP"
    if status_byte & 0x08:
        return "PSN"
    if status_byte & 0x10:
        return "BRN"
    if status_byte & 0x20:
        return "FRZ"
    if status_byte & 0x40:
        return "PAR"
    return None


def _hp_descriptor(hp, max_hp):
    if not isinstance(hp, (int, float)) or not isinstance(max_hp, (int, float)):
        return ""
    if max_hp <= 0:
        return ""
    pct = int(100 * hp / max_hp)
    if hp <= 0:
        label = "FAINTED"
    elif pct >= 75:
        label = "FULL"
    elif pct >= 40:
        label = "MID"
    elif pct >= 15:
        label = "LOW"
    else:
        label = "CRITICAL"
    return f" ({pct}% {label})"


def decode_screen_text(tile_map):
    if not tile_map or len(tile_map) < 360:
        return []
    lines = []
    for row in range(18):
        chars = []
        for col in range(20):
            tid = tile_map[row * 20 + col]
            ch = GEN1_CHARS.get(tid, "")
            chars.append(ch)
        line = "".join(chars).strip()
        if line:
            lines.append(line)
    return lines


def send_command(cmd):
    BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
    with open(CMD_FILE, "w") as f:
        f.write(cmd + "\n")


def send_commands(cmds):
    BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
    with open(CMD_FILE, "w") as f:
        for cmd in cmds:
            f.write(cmd + "\n")


def kb_find(ch):
    for r, row in enumerate(KB_GRID):
        for c, cell in enumerate(row):
            if cell == ch:
                return (r, c)
    return None


def kb_read_cursor():
    s = read_state()
    return (s.get("keyboard_letter", "") if s else "").strip()


def kb_nav_wait(cmd):
    old = kb_read_cursor()
    send_command(cmd)
    for _ in range(12):
        time.sleep(0.1)
        cur = kb_read_cursor()
        if cur != old:
            return cur
    return kb_read_cursor()


def type_on_keyboard(name):
    name = name.strip()[:7]
    if not name:
        send_command("START")
        time.sleep(0.5)
        return

    name = name[0].upper() + name[1:].lower()
    need_lowercase = len(name) > 1

    cur_letter = kb_read_cursor().upper()
    cur_pos = kb_find(cur_letter)
    if cur_pos is None:
        cur_pos = (0, 0)

    first = name[0].upper()
    tgt = kb_find(first)
    if tgt:
        cmds = []
        dr = tgt[0] - cur_pos[0]
        dc = tgt[1] - cur_pos[1]
        if dr > 0:
            cmds.extend(["NAV:DOWN"] * dr)
        elif dr < 0:
            cmds.extend(["NAV:UP"] * (-dr))
        if dc > 0:
            cmds.extend(["NAV:RIGHT"] * dc)
        elif dc < 0:
            cmds.extend(["NAV:LEFT"] * (-dc))
        cmds.append("INTERACT")
        send_commands(cmds)
        time.sleep(len(cmds) * 0.16 + 0.3)
        cur_pos = tgt

    if need_lowercase:
        for _ in range(cur_pos[1]):
            kb_nav_wait("NAV:LEFT")
        for _ in range(8):
            kl = kb_nav_wait("NAV:DOWN")
            if kl == "CASE":
                send_command("INTERACT")
                time.sleep(0.3)
                break
        cur_pos = None
        for _ in range(8):
            kl = kb_nav_wait("NAV:UP")
            pos = kb_find(kl.upper())
            if pos is not None:
                cur_pos = pos
                break
        if cur_pos is None:
            cur_pos = (0, 0)

    if len(name) > 1:
        cmds = []
        cur_r, cur_c = cur_pos
        for ch in name[1:]:
            ch_upper = ch.upper()
            if not ("A" <= ch_upper <= "Z"):
                continue
            tgt = kb_find(ch_upper)
            if tgt is None:
                continue
            dr = tgt[0] - cur_r
            dc = tgt[1] - cur_c
            if dr > 0:
                cmds.extend(["NAV:DOWN"] * dr)
            elif dr < 0:
                cmds.extend(["NAV:UP"] * (-dr))
            if dc > 0:
                cmds.extend(["NAV:RIGHT"] * dc)
            elif dc < 0:
                cmds.extend(["NAV:LEFT"] * (-dc))
            cmds.append("INTERACT")
            cur_r, cur_c = tgt

        cmds.append("START")
        send_commands(cmds)
        time.sleep(len(cmds) * 0.16 + 0.5)
    else:
        send_command("START")
        time.sleep(0.5)


def read_state():
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
        if "tolk_output" in state:
            state["tolk_output"] = _filter_keyboard_junk(state["tolk_output"])
        return state
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _filter_keyboard_junk(lines):
    return [l for l in lines if len(l.strip()) > 1]


def read_text_file():
    lines = []
    try:
        with open(TEXT_FILE, "r") as f:
            content = f.read()
        if content.strip():
            lines = [l for l in content.strip().split("\n") if l.strip()]
        with open(TEXT_FILE, "w") as f:
            f.write("")
    except (FileNotFoundError, OSError):
        pass
    return _filter_keyboard_junk(lines)


def add_to_history(lines):
    for line in lines:
        TEXT_HISTORY.append(line)
    while len(TEXT_HISTORY) > MAX_HISTORY:
        TEXT_HISTORY.pop(0)


def wait_for_state_change(old_state, timeout=10.0, poll_interval=0.1):
    start = time.time()
    while time.time() - start < timeout:
        state = read_state()
        if state and state != old_state:
            return state
        time.sleep(poll_interval)
    return read_state()


def wait_for_walk_complete(timeout=60.0):
    start = time.time()
    while time.time() - start < timeout:
        state = read_state()
        if state is None:
            time.sleep(0.2)
            continue
        if not state.get("auto_walk", False):
            return state
        steps = state.get("walk_steps_left", 0)
        if steps == 0:
            return state
        time.sleep(0.15)
    return read_state()


def find_vba_window(timeout=15.0, pid=None):
    start = time.time()
    get_window_pid = ctypes.windll.user32.GetWindowThreadProcessId if hasattr(ctypes, "windll") else None

    while time.time() - start < timeout:
        result = []

        def callback(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buf, length + 1)
                    title = buf.value

                    if pid:
                        lpdw = ctypes.wintypes.DWORD()
                        get_window_pid(hwnd, ctypes.byref(lpdw))
                        if lpdw.value == pid:
                            result.append(hwnd)
                            return True
                    elif "VisualBoyAdvance" in title or "VBA" in title:
                        result.append(hwnd)
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
        user32.EnumWindows(WNDENUMPROC(callback), 0)
        if result:
            return result[0]
        time.sleep(0.5)
    return None


def find_lua_dialog(timeout=10.0):
    start = time.time()
    while time.time() - start < timeout:
        result = []

        def callback(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buf, length + 1)
                    title = buf.value
                    if "Lua Script" in title:
                        result.append(hwnd)
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
        user32.EnumWindows(WNDENUMPROC(callback), 0)
        if result:
            return result[0]
        time.sleep(0.3)
    return None


def find_child_by_class(parent_hwnd, class_name, nth=0):
    found = []

    def callback(hwnd, _):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        if buf.value == class_name:
            found.append(hwnd)
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    user32.EnumChildWindows(parent_hwnd, WNDENUMPROC(callback), 0)
    if nth < len(found):
        return found[nth]
    return None


def find_child_by_text(parent_hwnd, text_contains):
    found = []

    def callback(hwnd, _):
        length = user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if text_contains.lower() in buf.value.lower():
                found.append(hwnd)
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    user32.EnumChildWindows(parent_hwnd, WNDENUMPROC(callback), 0)
    return found[0] if found else None


def dump_dialog_children(hwnd):
    children = []

    def callback(child_hwnd, _):
        cls_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(child_hwnd, cls_buf, 256)
        length = user32.GetWindowTextLengthW(child_hwnd)
        text = ""
        if length > 0:
            txt_buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(child_hwnd, txt_buf, length + 1)
            text = txt_buf.value
        ctrl_id = user32.GetDlgCtrlID(child_hwnd)
        children.append((child_hwnd, cls_buf.value, text, ctrl_id))
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    user32.EnumChildWindows(hwnd, WNDENUMPROC(callback), 0)
    return children


def auto_load_lua_script(pid=None):
    script_path = str(LUA_SCRIPT)

    vba_hwnd = find_vba_window(timeout=15, pid=pid)
    if not vba_hwnd:
        return False

    user32.PostMessageW(vba_hwnd, WM_COMMAND, VBA_LUA_NEW_WINDOW_CMD_ID, 0)
    time.sleep(1.0)

    lua_hwnd = find_lua_dialog(timeout=10)
    if not lua_hwnd:
        return False

    children = dump_dialog_children(lua_hwnd)

    edit_hwnd = find_child_by_class(lua_hwnd, "Edit", 0)
    if not edit_hwnd:
        for cls in ["RichEdit20W", "RichEdit20A", "EDIT"]:
            edit_hwnd = find_child_by_class(lua_hwnd, cls, 0)
            if edit_hwnd:
                break

    if not edit_hwnd:
        return False

    user32.SendMessageW.argtypes = [ctypes.wintypes.HWND, ctypes.c_uint, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
    user32.SendMessageW.restype = ctypes.wintypes.LPARAM
    path_buf = ctypes.create_unicode_buffer(script_path)
    user32.SendMessageW(edit_hwnd, WM_SETTEXT, 0, ctypes.cast(path_buf, ctypes.c_void_p).value)
    time.sleep(0.3)

    run_hwnd = None
    for btn_text in ["Run", "OK", "Open", "Load"]:
        run_hwnd = find_child_by_text(lua_hwnd, btn_text)
        if run_hwnd:
            break

    if not run_hwnd:
        for i in range(5):
            btn = find_child_by_class(lua_hwnd, "Button", i)
            if btn:
                length = user32.GetWindowTextLengthW(btn)
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(btn, buf, length + 1)
                if buf.value.lower() not in ("browse", "browse..."):
                    run_hwnd = btn
                    break

    if run_hwnd:
        user32.SendMessageW(run_hwnd, BM_CLICK, 0, 0)
        time.sleep(0.5)
        return True
    else:
        return False


def scan_roms():
    roms = []
    for ext in ROM_EXTENSIONS:
        for path in ROM_DIR.glob(f"*{ext}"):
            name = path.stem
            display = name
            for tag in ["(USA, Europe)", "(USA)", "(SGB Enhanced)", "(CGB+SGB Enhanced)", "(GB Compatible)"]:
                display = display.replace(tag, "")
            display = display.strip().rstrip(" -")
            roms.append((display, str(path)))
    roms.sort()
    return roms


def list_saves(game_name):
    game_dir = SAVE_BASE / game_name
    saves = []
    if game_dir.exists():
        for ext in ("*.sav", "*.sgm"):
            for f in sorted(game_dir.glob(ext)):
                saves.append(f)
    return saves


def prepare_objects(objects):
    filtered = [obj for obj in objects if obj.get("type") != "signpost"]

    seen_warps = {}
    result = []
    for obj in filtered:
        if obj.get("type") == "warp":
            name = obj.get("name", "???")
            nums = [int(n) for n in obj.get("dist", "").split() if n.isdigit()]
            total_dist = sum(nums) if nums else 0
            if name in seen_warps:
                prev_obj, prev_dist = seen_warps[name]
                if total_dist < prev_dist:
                    result = [r for r in result if r is not prev_obj]
                    seen_warps[name] = (obj, total_dist)
                    result.append(obj)
            else:
                seen_warps[name] = (obj, total_dist)
                result.append(obj)
        else:
            result.append(obj)
    return result


def auto_explore():
    directions = ["down", "left", "right", "up"]

    state = read_state()
    if not state:
        return False
    start_map = state.get("map_name", "")

    for d in directions:
        for _ in range(5):
            state = read_state()
            if not state:
                time.sleep(0.2)
                continue
            if state.get("objects", []):
                return True

            old_x = state.get("player_x", -1)
            old_y = state.get("player_y", -1)

            send_command(f"PRESS:{d}")
            time.sleep(0.3)

            state = read_state()
            if not state:
                time.sleep(0.2)
                continue

            if state.get("map_name", "") != start_map:
                return True

            if (state.get("player_x", -1), state.get("player_y", -1)) == (old_x, old_y):
                break

    return False


def _dist_str(dx, dy):
    parts = []
    if abs(dy) > 0:
        parts.append(f"{abs(dy)} {'Down' if dy > 0 else 'Up'}")
    if abs(dx) > 0:
        parts.append(f"{abs(dx)} {'Right' if dx > 0 else 'Left'}")
    return " ".join(parts) if parts else "Here"


def _is_same_building(current_name, dest_name):
    pattern = r'\s*B?\d+F$'
    base_cur = re.sub(pattern, '', current_name).strip()
    base_dest = re.sub(pattern, '', dest_name).strip()
    return base_cur and base_dest and base_cur == base_dest


def get_map_objects(state, map_db_data):
    if not map_db_data.get("available"):
        return prepare_objects(state.get("objects", []))

    px = state.get("player_x", -1)
    py = state.get("player_y", -1)
    if px < 0 or py < 0:
        return prepare_objects(state.get("objects", []))

    map_data = _get_map_data(state, map_db_data)
    if not map_data:
        return prepare_objects(state.get("objects", []))

    raw_pass = map_data.get("passability", [])
    passability = [row[:] for row in raw_pass]

    pokeball_names = map_db_data.get("pokeball_names", {})

    objects = []
    MAX_DIST = 12
    MAX_WARP_DIST = 60

    current_name = map_data.get("display_name", "")
    seen_warp_names = {}
    for w in map_data.get("warps", []):
        wx, wy = w["x"], w["y"]
        dist = abs(wx - px) + abs(wy - py)
        if dist > MAX_WARP_DIST:
            continue
        dest = w["dest"]
        display = dest.replace("Blue's", "Rival's").replace("Red's House", "Your House").replace("Red's", "Your")
        if display != "Outside" and _is_same_building(current_name, dest):
            display = "Stairs"
        walkable = (0 <= wy < len(passability) and 0 <= wx < len(passability[0])
                    and passability[wy][wx])
        rank = (0 if walkable else 1, dist)
        if display in seen_warp_names:
            if rank >= seen_warp_names[display]["_warp_rank"]:
                continue
            objects = [o for o in objects if o is not seen_warp_names[display]]
        obj = {
            "name": display, "type": "warp",
            "x": wx, "y": wy,
            "dist": _dist_str(wx - px, wy - py),
            "_map_obj": True, "_tile_type": "warp",
            "_dist_val": dist,
            "_warp_rank": rank,
        }
        seen_warp_names[display] = obj
        objects.append(obj)

    for s in map_data.get("signs", []):
        sx, sy = s["x"], s["y"]
        dist = abs(sx - px) + abs(sy - py)
        if dist > MAX_DIST or dist == 0:
            continue
        objects.append({
            "name": s["label"], "type": "object",
            "x": sx, "y": sy,
            "dist": _dist_str(sx - px, sy - py),
            "_map_obj": True, "_tile_type": "bookshelf",
            "_dist_val": dist,
        })

    db_npcs = map_data.get("npcs", [])
    SPRITE_RENAMES = {"Blue": "Rival", "Red": "Player", "Item": "Poke Ball"}
    for obj in state.get("objects", []):
        obj_type = obj.get("type", "")
        if obj_type in ("warp", "signpost", "connection"):
            continue
        nx = obj.get("x", -1)
        ny = obj.get("y", -1)
        if nx < 0 or ny < 0:
            continue
        dist = abs(nx - px) + abs(ny - py)
        if dist == 0 or dist > MAX_DIST:
            continue
        best_label = obj.get("name", "NPC")
        best_dist = 999
        best_stays = True
        for db_npc in db_npcs:
            d = abs(db_npc["x"] - nx) + abs(db_npc["y"] - ny)
            if d < best_dist:
                best_dist = d
                best_label = db_npc["sprite"]
                best_stays = db_npc.get("stays", True)
        display_name = SPRITE_RENAMES.get(best_label, best_label)
        if display_name == "Poke Ball" and (nx, ny) in pokeball_names:
            display_name = f"Poke Ball ({pokeball_names[(nx, ny)]})"
        if "Poke Ball" in display_name or best_label == "Item":
            best_stays = True
        if 0 <= ny < len(passability) and 0 <= nx < len(passability[0]):
            passability[ny][nx] = False
        objects.append({
            "name": display_name, "type": "object",
            "x": nx, "y": ny,
            "dist": _dist_str(nx - px, ny - py),
            "_map_obj": True, "_tile_type": "counter",
            "_stays": best_stays,
            "_dist_val": dist,
        })

    for obj in state.get("objects", []):
        if obj.get("type") == "connection":
            objects.append(obj)

    state["_passability"] = passability
    state["_player_pos"] = (px, py)

    objects.sort(key=lambda o: o.get("_dist_val", abs(o.get("x", 0) - px) + abs(o.get("y", 0) - py)))
    shown = objects[:10]

    if len(objects) > len(shown):
        warps = [o for o in objects if o.get("type") == "warp"][:2]
        missing = [w for w in warps if w not in shown]
        if missing:
            keep = [o for o in shown if o.get("type") != "warp"]
            keep = keep[:max(0, 10 - len([o for o in shown if o.get("type") == "warp"]) - len(missing))]
            shown = [o for o in shown if o.get("type") == "warp"] + keep + missing
            shown.sort(key=lambda o: o.get("_dist_val", 0))

    return shown


def _get_map_data(state, map_db_data):
    id_lookup = map_db_data.get("id_lookup", {})
    name_lookup = map_db_data.get("name_lookup", {})
    mid = state.get("map_id")
    if isinstance(mid, int) and mid in id_lookup:
        return id_lookup[mid]
    return name_lookup.get(state.get("map_name", ""))


def map_walk_to(obj, state, pokeball_names):
    passability = state.get("_passability")
    px, py = state.get("_player_pos", (state.get("player_x", 0), state.get("player_y", 0)))
    tx, ty = obj.get("x", px), obj.get("y", py)
    tile_type = obj.get("_tile_type", "passable")

    if not passability or not HAS_BFS:
        return walk_to_object(obj, state), None

    start = (px, py)
    goal = (tx, ty)

    if tile_type == "bookshelf":
        path, face_dir = _pathfind_prefer_below(passability, start, goal)
    else:
        path, face_dir = bfs_pathfind_adjacent(passability, start, goal)

    if path is None or not face_dir:
        path = bfs_pathfind(passability, start, goal)
        if path is not None:
            _send_moves(path)
            time.sleep(0.5)
            return read_state(), None
        return walk_to_object(obj, state), None

    if tile_type == "warp":
        all_moves = list(path) + [face_dir, face_dir]
    else:
        all_moves = list(path) + [face_dir]
    _send_moves(all_moves)

    check = read_state()
    if check and check.get("mode") not in ("overworld", None):
        return check, None

    screen_lines = None
    if tile_type in ("bookshelf", "counter"):
        stays = obj.get("_stays", True)
        time.sleep(0.3)
        send_command("INTERACT")

        if stays:
            prev_screen = ""
            all_screen_lines = []
            seen_lines = set()
            for _ in range(10):
                time.sleep(1.5)
                result = read_state()
                if result and result.get("mode") != "overworld":
                    break
                if result and result.get("tolk_output"):
                    break
                tm = result.get("tile_map", []) if result else []
                s_lines = decode_screen_text(tm)
                screen_text = "\n".join(s_lines)
                if s_lines and screen_text != prev_screen:
                    for line in s_lines:
                        if line not in seen_lines:
                            seen_lines.add(line)
                            all_screen_lines.append(line)
                    prev_screen = screen_text
                send_command("INTERACT")
            if all_screen_lines:
                screen_lines = all_screen_lines
                obj_name = obj.get("name", "")
                if "Poke Ball" in obj_name:
                    ox = obj.get("x", -1)
                    oy = obj.get("y", -1)
                    if ox >= 0 and oy >= 0 and all_screen_lines:
                        pokemon_name = all_screen_lines[0].strip()
                        if pokemon_name and pokemon_name.isalpha():
                            pokeball_names[(ox, oy)] = pokemon_name
            return read_state(), screen_lines
        else:
            npc_name = obj.get("name", "")
            for attempt in range(3):
                for _ in range(5):
                    time.sleep(0.2)
                    result = read_state()
                    if result and result.get("mode") != "overworld":
                        return result, None
                    if result and result.get("tolk_output"):
                        return result, None
                if attempt >= 2:
                    break
                fresh = read_state()
                if not fresh or fresh.get("mode") != "overworld":
                    return fresh, None
                new_px = fresh.get("player_x", px)
                new_py = fresh.get("player_y", py)
                found = False
                for bridge_obj in fresh.get("objects", []):
                    if bridge_obj.get("type") in ("warp", "signpost", "connection"):
                        continue
                    bname = bridge_obj.get("name", "")
                    if bname == npc_name or bname in npc_name or npc_name in bname:
                        ntx, nty = bridge_obj.get("x", -1), bridge_obj.get("y", -1)
                        if ntx >= 0 and nty >= 0 and (ntx != new_px or nty != new_py):
                            new_path, new_face = bfs_pathfind_adjacent(passability, (new_px, new_py), (ntx, nty))
                            if new_path is not None and new_face:
                                _send_moves(list(new_path) + [new_face])
                                post_walk = read_state()
                                if post_walk and post_walk.get("mode") not in ("overworld", None):
                                    return post_walk, None
                                send_command("INTERACT")
                                found = True
                                break
                if not found:
                    send_command(f"PRESS:{face_dir}")
                    time.sleep(0.2)
                    send_command("INTERACT")
            return read_state(), ["They walked away before you could talk to them."]

    elif tile_type != "warp":
        npc_name = obj.get("name", "")
        for attempt in range(3):
            time.sleep(0.2)
            send_command("INTERACT")
            for _ in range(5):
                time.sleep(0.2)
                result = read_state()
                if result and result.get("mode") != "overworld":
                    return result, None
            if attempt >= 2:
                break
            fresh = read_state()
            if not fresh or fresh.get("mode") != "overworld":
                return fresh, None
            new_px = fresh.get("player_x", px)
            new_py = fresh.get("player_y", py)
            found = False
            for bridge_obj in fresh.get("objects", []):
                if bridge_obj.get("type") in ("warp", "signpost", "connection"):
                    continue
                bname = bridge_obj.get("name", "")
                if bname == npc_name or bname in npc_name or npc_name in bname:
                    ntx, nty = bridge_obj.get("x", -1), bridge_obj.get("y", -1)
                    if ntx >= 0 and nty >= 0 and (ntx != new_px or nty != new_py):
                        new_path, new_face = bfs_pathfind_adjacent(passability, (new_px, new_py), (ntx, nty))
                        if new_path is not None and new_face:
                            _send_moves(list(new_path) + [new_face])
                            post_walk = read_state()
                            if post_walk and post_walk.get("mode") not in ("overworld", None):
                                return post_walk, None
                            found = True
                            break
            if not found:
                send_command(f"PRESS:{face_dir}")
                time.sleep(0.2)
        return read_state(), ["They walked away before you could talk to them."]

    time.sleep(0.5)
    return read_state(), None


def _pathfind_prefer_below(passability, start, goal):
    gx, gy = goal
    rows = len(passability)
    cols = len(passability[0]) if rows > 0 else 0

    by = gy + 1
    if 0 <= by < rows and 0 <= gx < cols and passability[by][gx]:
        path = bfs_pathfind(passability, start, (gx, by))
        if path is not None:
            return path, "up"

    return bfs_pathfind_adjacent(passability, start, goal)


def _focus_emulator(vba_hwnd=None):
    try:
        hwnd = vba_hwnd or find_vba_window(timeout=1)
        if hwnd:
            user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


def _send_moves(moves, vba_hwnd=None):
    if not moves:
        return
    for m in moves:
        send_command(f"PRESS:{m}")
        time.sleep(0.3)


def walk_to_object(obj, state):
    send_command(f"PATHFINDXY:{obj['x']},{obj['y']}")
    time.sleep(0.5)
    check = read_state()
    if check and check.get("auto_walk", False):
        wait_for_walk_complete()
        send_command("INTERACT")
        time.sleep(0.5)
        return read_state()

    px = state.get("player_x", 0)
    py = state.get("player_y", 0)
    dx = obj.get("x", px) - px
    dy = obj.get("y", py) - py
    cmds = []
    for _ in range(min(abs(dy), 15)):
        cmds.append(f"PRESS:{'down' if dy > 0 else 'up'}")
    for _ in range(min(abs(dx), 15)):
        cmds.append(f"PRESS:{'right' if dx > 0 else 'left'}")
    if cmds:
        send_commands(cmds)
        time.sleep(len(cmds) * 0.2 + 0.3)
    send_command("INTERACT")
    time.sleep(0.5)
    return read_state()


def open_start_menu():
    send_command("START")
    for _ in range(20):
        time.sleep(0.15)
        s = read_state()
        if s and s.get("mode") == "menu":
            return


def menu_nav_to(index, selected):
    if selected >= 0:
        delta = index - selected
        for _ in range(abs(delta)):
            send_command("NAV:DOWN" if delta > 0 else "NAV:UP")
            time.sleep(0.15)
    else:
        for _ in range(index):
            send_command("NAV:DOWN")
            time.sleep(0.15)


def menu_pick(targets, settle=0.4):
    time.sleep(settle)
    s = read_state()
    if not s or s.get("mode") != "menu":
        return False
    items, sel = get_menu_items(s)
    upper = [it.split("x")[0].strip().split("×")[0].strip().upper() for it in items]
    idx = -1
    for target in targets:
        if target in upper:
            idx = upper.index(target)
            break
    if idx < 0:
        return False
    menu_nav_to(idx, sel)
    send_command("INTERACT")
    time.sleep(0.4)
    return True


def auto_advance_dialogue(initial_state):
    collected = []
    seen = set()
    state = initial_state

    def collect_tolk(st):
        for line in st.get("tolk_output", []):
            clean = line.lstrip("\u2192").strip()
            if clean and clean not in seen:
                seen.add(clean)
                collected.append(clean)
                add_to_history([clean])

    collect_tolk(state)

    if not collected:
        for _ in range(20):
            time.sleep(0.1)
            s = read_state()
            if not s:
                continue
            collect_tolk(s)
            if collected:
                state = s
                break

    max_presses = 200
    overworld_since = None
    OVERWORLD_TIMEOUT = 5.0

    for _ in range(max_presses):
        state = read_state()
        if state is None:
            time.sleep(0.3)
            continue

        mode = state.get("mode", "")

        if mode in ("menu", "keyboard", "battle"):
            collect_tolk(state)
            break

        if mode == "dialogue":
            overworld_since = None
            collect_tolk(state)
            send_command("INTERACT")
            for _ in range(15):
                time.sleep(0.1)
                s = read_state()
                if s is None:
                    continue
                m = s.get("mode", "")
                if m != "dialogue":
                    state = s
                    break
                if s.get("tolk_output"):
                    collect_tolk(s)
                    state = s
                    break

        elif mode in ("overworld", "loading"):
            if overworld_since is None:
                overworld_since = time.time()
            if time.time() - overworld_since > OVERWORLD_TIMEOUT:
                break
            time.sleep(0.3)

        else:
            send_command("INTERACT")
            time.sleep(0.5)

    TITLE_ECHOES = {"NEW GAME", "CONTINUE", "OPTIONS"}
    if state:
        menu_set = {item.strip().upper() for item in state.get("menu_items", [])}
        menu_set |= TITLE_ECHOES
        if menu_set:
            collected = [l for l in collected if l.strip().upper() not in menu_set]
    else:
        collected = [l for l in collected if l.strip().upper() not in TITLE_ECHOES]

    return state, collected


def _get_battle_info(state):
    battle = state.get("battle", {})
    if isinstance(battle, list):
        battle = {}
    return battle


def _battle_data_valid(state):
    battle = _get_battle_info(state)
    return battle.get("player_max_hp", 0) > 0 and battle.get("enemy_max_hp", 0) > 0


def _scripted_battle_kind(state):
    flag = _get_battle_info(state).get("battle_type_flag", 0)
    if flag == 1:
        return "tutorial"
    if flag == 2:
        return "safari"
    return ""


def auto_advance_scripted_battle(initial_state):
    state = initial_state
    seen = set()
    lines = []
    for _ in range(60):
        for line in state.get("tolk_output", []) if state else []:
            clean = line.lstrip("→").strip()
            if clean and clean not in seen:
                seen.add(clean)
                lines.append(clean)
        if state and state.get("mode") != "battle":
            break
        send_command("INTERACT")
        time.sleep(0.5)
        state = read_state() or state
    return state, _dedup_progressive_text(lines)


def _format_battle_stats(state):
    battle = _get_battle_info(state)
    enemy_name = decode_gen1_name(battle.get("enemy_name", []))
    player_name = decode_gen1_name(battle.get("player_name", []))
    enemy_lv = battle.get("enemy_level", "?")
    player_lv = battle.get("player_level", "?")
    enemy_hp = battle.get("enemy_hp", "?")
    enemy_max = battle.get("enemy_max_hp", "?")
    player_hp = battle.get("player_hp", "?")
    player_max = battle.get("player_max_hp", "?")
    battle_type = battle.get("battle_type", 0)
    enemy_status = get_status_name(battle.get("enemy_status", 0))
    player_status = get_status_name(battle.get("player_status", 0))
    type_label = "Wild" if battle_type == 1 else "Trainer"
    es = f" [{enemy_status}]" if enemy_status else ""
    ps = f" [{player_status}]" if player_status else ""
    enemy_hp_desc = _hp_descriptor(enemy_hp, enemy_max)
    player_hp_desc = _hp_descriptor(player_hp, player_max)
    return (f"{type_label}: {enemy_name} Lv{enemy_lv}  HP: {enemy_hp}/{enemy_max}{enemy_hp_desc}{es}\n"
            f"Yours: {player_name} Lv{player_lv}  HP: {player_hp}/{player_max}{player_hp_desc}{ps}")


def _get_battle_options(state):
    items = state.get("battle_menu", [])
    if not items:
        return []
    result = [r.strip() for r in items if r and r.strip() and r.strip() not in ("-", "--")]
    return result


def _cancel_to_battle_menu(state):
    for _ in range(5):
        send_command("CANCEL")
        time.sleep(0.3)
        state = read_state() or state
        if state.get("mode") != "battle":
            return state
        if state.get("battle_menu"):
            return state
    return state


def _find_items_on_screen(state, items):
    tile_map = state.get("tile_map", [])
    if not tile_map or len(tile_map) < 360 or not items:
        return -1, {}, None

    ARROW_TILE = 0xED

    named_items = []
    for i, item in enumerate(items):
        name = (item or "").split("(")[0].strip().upper()
        if name and name not in ("-", "--"):
            named_items.append((name, i))

    if not named_items:
        return -1, {}, None

    current_idx = -1
    positions = {}

    for row in range(10, 18):
        for col in range(0, 19):
            if tile_map[row * 20 + col] != ARROW_TILE:
                continue
            text_chars = []
            for c in range(col + 1, 20):
                tid = tile_map[row * 20 + c]
                ch = GEN1_CHARS.get(tid, "")
                text_chars.append(ch)
            text = "".join(text_chars).strip().upper()
            if not text:
                continue
            for name, idx in named_items:
                if text.startswith(name) or name.startswith(text):
                    current_idx = idx
                    positions[idx] = (row, col)
                    break

    for row in range(10, 18):
        line_chars = []
        col_map = []
        for col in range(0, 20):
            tid = tile_map[row * 20 + col]
            ch = GEN1_CHARS.get(tid, "")
            if ch:
                col_map.append(col)
                line_chars.append(ch)
        line_text = "".join(line_chars).upper()
        for name, idx in named_items:
            if idx in positions:
                continue
            pos = line_text.find(name)
            if pos >= 0:
                actual_col = col_map[pos] if pos < len(col_map) else pos
                positions[idx] = (row, actual_col)

    return current_idx, positions, "tiles" if current_idx >= 0 else None


def _detect_battle_cursor(state, fallback_options=None):
    items = fallback_options or state.get("battle_menu", [])
    if not items:
        return -1, {}, None

    current, positions, source = _find_items_on_screen(state, items)
    if current >= 0:
        return current, positions, source

    tolk = state.get("tolk_output", [])
    if not tolk:
        return -1, {}, None

    named_items = []
    for i, item in enumerate(items):
        name = (item or "").split("(")[0].strip().upper()
        if name and name not in ("-", "--"):
            named_items.append((name, i))
    if not named_items:
        return -1, {}, None

    for line in reversed(tolk):
        clean = line.lstrip("\u2192").strip().upper()
        if not clean:
            continue
        for name, grid_idx in named_items:
            if clean == name or name.startswith(clean) or clean.startswith(name):
                return grid_idx, {}, "tolk"

    return -1, {}, None


def _battle_select(state, target_idx, options=None):
    print(f"  [DEBUG] BATTLE:{target_idx} (bridge 2x2 grid select)")
    send_command(f"BATTLE:{target_idx}")


def _move_menu_select(current_cursor, target_idx, num_moves):
    if current_cursor == target_idx:
        print(f"  [DEBUG] Move cursor already at {target_idx} (tracked), pressing A")
        send_command("INTERACT")
        return

    delta = target_idx - current_cursor
    cmds = []
    if delta > 0:
        cmds = ["NAV:DOWN"] * delta
    else:
        cmds = ["NAV:UP"] * (-delta)
    cmds.append("INTERACT")
    print(f"  [DEBUG] Move cursor at {current_cursor} (tracked), target {target_idx}, sending: {cmds}")
    send_commands(cmds)


def _dedup_progressive_text(lines):
    if not lines or len(lines) <= 1:
        return lines
    result = []
    for i, line in enumerate(lines):
        is_fragment = any(
            line in other and len(other) > len(line)
            for j, other in enumerate(lines) if j != i
        )
        if not is_fragment:
            result.append(line)
    return result


_PARTY_PROMPTS = ("BRING OUT WHICH", "CHOOSE A POK", "ALREADY OUT")


def _party_select_showing(state, extra_lines=None):
    lines = list(state.get("tolk_output", []) or [])
    lines += list(state.get("text_lines", []) or [])
    if extra_lines:
        lines += list(extra_lines)
    joined = " ".join(lines).upper()
    return any(p in joined for p in _PARTY_PROMPTS)


_TEXT_PROMPT_TILE = 0xEE


def _wait_for_text_prompt(state, tries=10):
    for _ in range(tries):
        if _TEXT_PROMPT_TILE in (state.get("tile_map") or []):
            return state
        time.sleep(0.06)
        state = read_state() or state
        if state.get("mode") != "battle" or _battle_input_needed(state):
            return state
    return state


def _battle_input_needed(state):
    return bool(state.get("battle_menu")) or bool(state.get("keyboard")) \
        or _party_select_showing(state)


def auto_advance_battle_text(state):
    collected = []
    seen = set()

    def collect(st):
        for line in st.get("tolk_output", []):
            clean = line.strip()
            if clean and clean not in seen:
                seen.add(clean)
                collected.append(clean)
                add_to_history([clean])

    def collect_text_lines(st):
        for line in st.get("text_lines", []):
            clean = line.strip()
            if clean and clean not in seen:
                seen.add(clean)
                collected.append(clean)
                add_to_history([clean])

    collect(state)
    if not collected:
        collect_text_lines(state)

    if not collected:
        for _ in range(20):
            time.sleep(0.1)
            s = read_state()
            if not s:
                continue
            if s.get("mode") != "battle":
                return s, collected
            if _battle_input_needed(s):
                return s, collected
            collect(s)
            if not collected:
                collect_text_lines(s)
            if collected:
                state = s
                break

    idle_count = 0
    for _ in range(60):
        state = read_state() or state
        if state.get("mode") != "battle":
            return state, collected
        if _battle_input_needed(state):
            return state, collected

        if idle_count >= 8 and _battle_data_valid(state):
            battle = _get_battle_info(state)
            enemy_hp = battle.get("enemy_hp", 1)
            if enemy_hp > 0:
                break
            if idle_count >= 15:
                break

        time.sleep(0.15)
        state = read_state() or state
        if state.get("mode") != "battle":
            return state, collected
        if _battle_input_needed(state):
            return state, collected

        state = _wait_for_text_prompt(state)
        if state.get("mode") != "battle":
            return state, collected
        if _battle_input_needed(state):
            return state, collected
        collect(state)

        send_command("INTERACT")
        got_new = False
        for _w in range(15):
            time.sleep(0.1)
            s = read_state()
            if not s:
                continue
            if s.get("mode") != "battle":
                return s, collected
            if _battle_input_needed(s):
                return s, collected
            new_tolk = s.get("tolk_output", [])
            old_count = len(collected)
            if new_tolk:
                collect(s)
            if len(collected) == old_count:
                collect_text_lines(s)
            if len(collected) > old_count:
                got_new = True
                idle_count = 0
                state = s
                break
        if not got_new:
            idle_count += 1
            state = read_state() or state
            if state.get("mode") != "battle":
                break
            if _battle_input_needed(state):
                break

    if len(collected) > 1:
        filtered = []
        for i, line in enumerate(collected):
            if any(line in other and len(other) > len(line)
                   for j, other in enumerate(collected) if j != i):
                continue
            filtered.append(line)
        collected = filtered

    return state, collected


def get_menu_items(state):
    items = state.get("menu_items", [])
    selected = state.get("menu_selected", -1)
    if not items:
        tolk = state.get("tolk_output", [])
        seen = set()
        for l in tolk:
            clean = l.lstrip("\u2192").strip()
            if clean and clean not in seen:
                seen.add(clean)
                items.append(clean)
        selected = 0
    if any(_is_price_line(item) for item in items):
        merged = []
        index_map = []
        for item in items:
            if _is_price_line(item) and merged:
                merged[-1] = f"{merged[-1].rstrip()}  {item.strip()}"
            else:
                merged.append(item)
            index_map.append(len(merged) - 1)
        if 0 <= selected < len(index_map):
            selected = index_map[selected]
        items = merged
    seen = set()
    deduped = []
    for item in items:
        key = item.strip().upper()
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped, selected


def _is_quantity_line(text):
    return bool(_QUANTITY_RE.match(text.strip()))


def _is_price_line(text):
    return bool(_PRICE_RE.match(text.strip()))


def _read_arrow_item_and_qty(state):
    tile_map = state.get("tile_map", [])
    if not tile_map or len(tile_map) < 360:
        return None, None

    ARROW_TILE = 0xED

    for row in range(0, 18):
        for col in range(0, 19):
            if tile_map[row * 20 + col] != ARROW_TILE:
                continue
            name_chars = []
            for c in range(col + 1, 20):
                tid = tile_map[row * 20 + c]
                ch = GEN1_CHARS.get(tid, "")
                name_chars.append(ch)
            name = "".join(name_chars).strip()
            if not name:
                continue
            if name.upper() == "CANCEL":
                return "CANCEL", None
            qty = None
            if row + 1 < 18:
                for c2 in range(0, 20):
                    if tile_map[(row + 1) * 20 + c2] == 0xF1:
                        digits = []
                        for c3 in range(c2 + 1, 20):
                            tid2 = tile_map[(row + 1) * 20 + c3]
                            if 0xF6 <= tid2 <= 0xFF:
                                digits.append(str(tid2 - 0xF6))
                            elif tid2 == 0x7F:
                                continue
                            else:
                                break
                        if digits:
                            qty = int("".join(digits))
                        break
            return name, qty

    return None, None


def _scan_item_list(state):
    state = read_state() or state
    start_name, start_qty = _read_arrow_item_and_qty(state)
    if start_name is None:
        return [], -1

    scanned = []
    scanned.append((start_name, start_qty))

    prev_name = start_name
    for _ in range(21):
        send_command("NAV:DOWN")
        time.sleep(0.35)
        name, qty = None, None
        for _retry in range(5):
            state = read_state() or state
            name, qty = _read_arrow_item_and_qty(state)
            if name is not None and name != prev_name:
                break
            time.sleep(0.15)
            name, qty = None, None
        if name is None:
            break
        if name == start_name:
            break
        scanned.append((name, qty))
        prev_name = name

    cancel_idx = None
    for i, (name, _) in enumerate(scanned):
        if name.upper() == "CANCEL":
            cancel_idx = i
            break

    if cancel_idx is not None:
        above = scanned[cancel_idx + 1:]
        below = scanned[:cancel_idx]
        ordered = above + below + [scanned[cancel_idx]]
    else:
        ordered = scanned

    items = []
    for name, qty in ordered:
        if name.upper() == "CANCEL":
            items.append("CANCEL")
        elif qty is not None:
            items.append(f"{name}  x{qty}")
        else:
            items.append(name)

    cursor_index = 0
    for i, (name, _) in enumerate(ordered):
        if name == start_name:
            cursor_index = i
            break

    return items, cursor_index


def _read_visible_item_list(state):
    tile_map = state.get("tile_map", [])
    if not tile_map or len(tile_map) < 360:
        return [], -1

    ARROW_TILE = 0xED

    arrow_row = -1
    arrow_col = -1
    for row in range(0, 18):
        for col in range(0, 19):
            if tile_map[row * 20 + col] == ARROW_TILE:
                arrow_row = row
                arrow_col = col
                break
        if arrow_row >= 0:
            break

    if arrow_row < 0:
        return [], -1

    name_col = arrow_col + 1

    scanned = []
    cursor_index = -1
    skip_rows = set()

    for row in range(0, 18):
        if row in skip_rows:
            continue

        tile_at_arrow_col = tile_map[row * 20 + arrow_col]

        if tile_at_arrow_col != ARROW_TILE and tile_at_arrow_col != 0x7F:
            continue

        name_chars = []
        for c in range(name_col, 20):
            tid = tile_map[row * 20 + c]
            ch = GEN1_CHARS.get(tid, "")
            name_chars.append(ch)
        name = "".join(name_chars).strip()
        if not name:
            continue

        if _QUANTITY_RE.match(name):
            continue

        if tile_at_arrow_col == ARROW_TILE:
            cursor_index = len(scanned)

        if name.upper() == "CANCEL":
            scanned.append((row, "CANCEL", None))
            continue

        qty = None
        if row + 1 < 18:
            for c2 in range(0, 20):
                if tile_map[(row + 1) * 20 + c2] == 0xF1:
                    digits = []
                    for c3 in range(c2 + 1, 20):
                        tid2 = tile_map[(row + 1) * 20 + c3]
                        if 0xF6 <= tid2 <= 0xFF:
                            digits.append(str(tid2 - 0xF6))
                        elif tid2 == 0x7F:
                            continue
                        else:
                            break
                    if digits:
                        qty = int("".join(digits))
                        skip_rows.add(row + 1)
                    break

        scanned.append((row, name, qty))

    if not scanned:
        return [], -1

    items = []
    for _, name, qty in scanned:
        if name.upper() == "CANCEL":
            items.append("CANCEL")
        elif qty is not None:
            items.append(f"{name}  x{qty}")
        else:
            items.append(name)

    return items, cursor_index


def do_save(game_name):
    game_dir = SAVE_BASE / game_name
    game_dir.mkdir(parents=True, exist_ok=True)
    save_name = f"{game_name}\\save"
    send_command(f"SAVE:{save_name}")
    time.sleep(1)


def _capture_window_screenshot(hwnd):
    try:
        from PIL import Image, ImageGrab
    except ImportError:
        return None

    if not hwnd:
        return None

    try:
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom
        if right <= left or bottom <= top:
            return None
        img = ImageGrab.grab(bbox=(left, top, right, bottom))
        return img
    except Exception:
        return None


def _image_to_data_uri(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _capture_with_compass(hwnd):
    img = _capture_window_screenshot(hwnd)
    if img is None:
        return None
    try:
        from PIL import ImageDraw, ImageFont
        draw = ImageDraw.Draw(img)
        w, h = img.size
        try:
            font = ImageFont.truetype("arial.ttf", 16)
        except Exception:
            font = ImageFont.load_default()
        labels = [("N", w // 2, 5), ("S", w // 2, h - 25), ("E", w - 25, h // 2), ("W", 5, h // 2)]
        for text, x, y in labels:
            draw.text((x, y), text, fill="white", font=font)
            draw.text((x - 1, y - 1), text, fill="black", font=font)
    except ImportError:
        pass
    return _image_to_data_uri(img)


class PokemonGame:

    def __init__(self, game_key, game_name):
        self.game_key = game_key
        self.game_name = game_name
        self.process = None
        self.vba_hwnd = None
        self.first_battle_completed = False
        self.first_battle_seen = False
        self.in_battle = False
        self.in_tutorial = True
        self.accumulated_text = []
        self._pending_text = []
        self.player_name = ""
        self.rival_name = ""
        self.current_location = ""
        self.current_map_id = None
        self._starting_house = ""
        self.interacted_objects = set()
        self.interacted_map_id = None
        self._blocked_objects = set()
        self.last_interacted_name = None
        self.last_warp_used = None
        self._just_left_location = ""
        self._just_left_remaining = 0
        self._recent_actions = deque(maxlen=5)
        self._prev_state_text = ""
        self.map_db = None
        self.pokeball_names = {}
        self.quests = {"active_quests": [], "completed_quests": []}
        self.location_map = {"connections": [], "visited": []}

        self._pending_sub_menu = None

        self._move_cursor = 0

        self._battle_opponent = ""
        self._battle_type_label = ""
        self._battle_opponent_level = 0
        self._last_battle_outcome = ""

        self._tutorial_hint_turns = 0

        self._memory_events = []

        self._map_id_lookup = {}
        self._map_name_lookup = {}
        self._map_available = False

        self._save_dir = SAVE_BASE / game_key
        self._quests_file = self._save_dir / "quests.json"
        self._location_map_file = self._save_dir / "location_map.json"
        self._rom_path = None
        self._rom_dir = None

        self._item_list_cache = None

        self._item_use_pending = False

        self._selected_item_name = ""

        self._prev_mode = "loading"


    def _load_persistent_data(self):
        if self._quests_file.exists():
            try:
                with open(self._quests_file) as f:
                    self.quests = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        if self._location_map_file.exists():
            try:
                with open(self._location_map_file) as f:
                    self.location_map = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

    def _save_persistent_data(self):
        self._save_dir.mkdir(parents=True, exist_ok=True)
        try:
            with open(self._quests_file, "w") as f:
                json.dump(self.quests, f, indent=2)
        except OSError:
            pass
        try:
            with open(self._location_map_file, "w") as f:
                json.dump(self.location_map, f, indent=2)
        except OSError:
            pass


    def _rom_sav_path(self):
        if not self._rom_path:
            return None
        return Path(self._rom_path).with_suffix(".sav")

    def _old_sav_path(self):
        if not self._rom_path:
            return None
        rom_name = Path(self._rom_path).stem + ".sav"
        return self._save_dir / rom_name

    def _migrate_sav_to_rom_dir(self):
        rom_sav = self._rom_sav_path()
        old_sav = self._old_sav_path()
        if not rom_sav or not old_sav:
            return

        old_exists = old_sav.exists()
        rom_exists = rom_sav.exists()

        if old_exists and not rom_exists:
            shutil.copy2(str(old_sav), str(rom_sav))
            print(f"  Migrated .sav to ROM dir: {rom_sav}")
        elif old_exists and rom_exists:
            old_mtime = old_sav.stat().st_mtime
            rom_mtime = rom_sav.stat().st_mtime
            if old_mtime > rom_mtime:
                shutil.copy2(str(old_sav), str(rom_sav))
                print(f"  Updated ROM dir .sav from newer backup")

    def _sync_sav_to_backup(self):
        rom_sav = self._rom_sav_path()
        old_sav = self._old_sav_path()
        if not rom_sav or not old_sav:
            return
        if rom_sav.exists():
            self._save_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(rom_sav), str(old_sav))

    def _clear_rom_sav(self):
        rom_sav = self._rom_sav_path()
        if rom_sav and rom_sav.exists():
            rom_sav.unlink()
            print(f"  Cleared .sav for fresh start: {rom_sav}")


    def _note_tutorial_dialogue(self, lines):
        combined = " ".join(lines).upper()
        if "HURRY" in combined and "VIRIDIAN" in (self.current_location or "").upper():
            self._tutorial_hint_turns = 3

    def _looks_like_tutorial_battle(self, state):
        if self._tutorial_hint_turns <= 0:
            return False
        if "VIRIDIAN CITY" not in (self.current_location or "").upper():
            return False
        enemy = decode_gen1_name(_get_battle_info(state).get("enemy_name", []))
        return enemy.upper() == "WEEDLE"

    def _compose_battle_outcome(self, battle_text):
        opponent = self._battle_opponent or "unknown"
        type_label = self._battle_type_label or "Wild"
        level = self._battle_opponent_level

        combined = " ".join(battle_text).upper() if battle_text else ""
        exp_match = ""
        for line in (battle_text or []):
            if "EXP" in line.upper():
                exp_match = line.strip()
                break

        if "RAN AWAY" in combined or "GOT AWAY" in combined:
            outcome = f"Encountered a {type_label.lower()} {opponent} (Lv{level}). Ran away safely."
        elif "CAUGHT" in combined:
            outcome = f"Encountered a {type_label.lower()} {opponent} (Lv{level}). Caught it!"
        elif exp_match:
            outcome = f"Encountered a {type_label.lower()} {opponent} (Lv{level}). Defeated it! {exp_match}"
        elif "FAINTED" in combined and opponent.upper() not in combined:
            outcome = f"Encountered a {type_label.lower()} {opponent} (Lv{level}). Lost the battle."
        else:
            outcome = f"Encountered a {type_label.lower()} {opponent} (Lv{level}). Battle ended."

        self._last_battle_outcome = outcome
        self._memory_events.append(outcome)
        self._battle_opponent = ""
        self._battle_type_label = ""
        self._battle_opponent_level = 0

    def clear_quests(self):
        self.quests = {"active_quests": [], "completed_quests": []}


    def _init_map_db(self):
        if self._map_available:
            return True
        if not MAP_DB_PATH.exists():
            return False
        try:
            with open(MAP_DB_PATH) as f:
                self.map_db = json.load(f)
            id_to_key = self.map_db.get("_id_to_key", {})
            for mid_str, key in id_to_key.items():
                entry = self.map_db.get(key)
                if entry:
                    self._map_id_lookup[int(mid_str)] = entry
            for key, val in self.map_db.items():
                if key.startswith("_") or not isinstance(val, dict):
                    continue
                val["_outdoor"] = (
                    key.endswith("Town") or key.endswith("City")
                    or bool(re.match(r"^Route\d+$", key))
                    or key in ("CinnabarIsland", "IndigoPlateau", "ViridianForest")
                )
                dn = val.get("display_name")
                if dn:
                    self._map_name_lookup[dn] = val
            self._map_available = True
            return True
        except Exception:
            return False

    def _get_map_db_data(self):
        return {
            "id_lookup": self._map_id_lookup,
            "name_lookup": self._map_name_lookup,
            "available": self._map_available,
            "pokeball_names": self.pokeball_names,
            "interacted_objects": self.interacted_objects,
        }


    def _mark_interacted(self, obj, current_map_id):
        if current_map_id != self.interacted_map_id:
            self.interacted_objects.clear()
            self.interacted_map_id = current_map_id
        x, y = obj.get("x", -1), obj.get("y", -1)
        if x >= 0 and y >= 0:
            self.interacted_objects.add((x, y))


    def _update_location(self, map_name):
        if not map_name:
            return
        clean = map_name.replace("[", "").replace("]", "").replace("\u2019", "'").strip()
        upper = clean.upper()
        _PLAYER_HOUSE_NAMES = {"RED", "ASH", "JACK", "BLUE", "GARY", "JOHN"}
        if self.player_name:
            _PLAYER_HOUSE_NAMES.add(self.player_name.upper())
        display_name = clean
        for name in _PLAYER_HOUSE_NAMES:
            if upper.startswith(name + "'S HOUSE"):
                display_name = "Your House"
                break
        if display_name == self.current_location:
            return
        old = self.current_location
        self.current_location = display_name
        if clean not in self.location_map["visited"]:
            self.location_map["visited"].append(clean)
        if old:
            conn = {"from": old, "to": display_name}
            rev = {"from": display_name, "to": old}
            if conn not in self.location_map["connections"] and rev not in self.location_map["connections"]:
                self.location_map["connections"].append(conn)


    async def launch(self, fresh_start=False):
        def _launch_sync():
            BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
            self._save_dir.mkdir(parents=True, exist_ok=True)

            for f in [STATE_FILE, CMD_FILE, TEXT_FILE]:
                try:
                    f.unlink()
                except FileNotFoundError:
                    pass

            rom_path = CONFIG.get("pokemon_paths", {}).get(self.game_key)
            if not rom_path or not Path(rom_path).exists():
                print(f"  ROM not found for {self.game_key}: {rom_path}")
                return False

            self._rom_path = rom_path
            self._rom_dir = Path(rom_path).parent

            self._save_dir.mkdir(parents=True, exist_ok=True)

            rom_dir_str = str(self._rom_dir)
            vba_ini = VBA_EXE.parent / "vba.ini"
            if vba_ini.exists():
                ini_text = vba_ini.read_text()
                ini_text = ini_text.replace("pauseWhenInactive=1", "pauseWhenInactive=0")
                ini_text = ini_text.replace("enableBackgroundInput=1", "enableBackgroundInput=0")
                ini_text = re.sub(r"soundEnable=\d+", "soundEnable=0", ini_text)
                ini_text = re.sub(r"soundVolume=\d+", "soundVolume=0", ini_text)
                ini_text = re.sub(r"batteryDir=.*", lambda _: f"batteryDir={rom_dir_str}", ini_text)
                ini_text = re.sub(r"saveDir=.*", lambda _: f"saveDir={rom_dir_str}", ini_text)
                vba_ini.write_text(ini_text)

            if fresh_start:
                self._clear_rom_sav()
                if self._save_dir.exists():
                    for f in self._save_dir.iterdir():
                        if f.is_file():
                            try:
                                f.unlink()
                            except (PermissionError, OSError):
                                pass
                print(f"  Fresh start: cleared save data for {self.game_key}")
            else:
                self._migrate_sav_to_rom_dir()

            if not VBA_EXE.exists():
                return False

            try:
                self.process = subprocess.Popen(
                    [str(VBA_EXE), rom_path],
                    cwd=str(ROM_DIR / "VBA-RR"),
                )
            except OSError:
                return False

            time.sleep(2)

            auto_load_lua_script(pid=self.process.pid)

            self.vba_hwnd = find_vba_window(timeout=15, pid=self.process.pid)

            start = time.time()
            while time.time() - start < 30:
                state = read_state()
                if state and state.get("ready"):
                    break
                time.sleep(0.5)

            for _ in range(10):
                state = read_state()
                if not state:
                    time.sleep(0.5)
                    continue
                if state.get("mode") == "menu":
                    break
                px = state.get("player_x", -1)
                if px > 0:
                    break
                send_command("START")
                time.sleep(1)


            self._init_map_db()

            self._load_persistent_data()

            return True

        return await asyncio.to_thread(_launch_sync)

    async def get_game_state(self):
        def _get_state_sync():
            new_text = read_text_file()
            if new_text:
                add_to_history(new_text)

            state = read_state()
            if state is None:
                return "Lost connection to bridge...", {}, {"mode": "error"}

            mode = state.get("mode", "loading")
            map_name = state.get("map_name", "???")

            tolk = state.get("tolk_output", [])
            if mode == "overworld" and tolk:
                tolk_upper = " ".join(l.upper() for l in tolk)
                if "SAVE" in tolk_upper and "EXIT" in tolk_upper:
                    mode = "menu"

            self._update_location(map_name)

            if mode == "loading":
                time.sleep(1)
                return "Game is loading...", {}, {"mode": "loading"}

            if mode == "walking":
                steps = state.get("walk_steps_left", 0)
                if steps == 0:
                    send_command("STOP_WALK")
                    time.sleep(0.2)
                    send_command("INTERACT")
                    time.sleep(0.5)
                else:
                    wait_for_walk_complete(timeout=30)
                    send_command("INTERACT")
                    time.sleep(0.5)
                state = read_state()
                if state is None:
                    return "Lost connection to bridge...", {}, {"mode": "error"}
                mode = state.get("mode", "loading")
                map_name = state.get("map_name", "???")

            if mode == "overworld":
                if self._prev_mode in ("keyboard", "menu", "dialogue"):
                    poll_count = 25
                else:
                    poll_count = 10
                for _ in range(poll_count):
                    time.sleep(0.2)
                    s = read_state()
                    if s and s.get("mode") not in ("overworld", "loading"):
                        state = s
                        mode = state.get("mode", "overworld")
                        break
                if mode != "overworld":
                    pass

            if mode == "overworld":
                px = state.get("player_x", -1)
                py = state.get("player_y", -1)
                if px <= 0 and py <= 0:
                    send_command("START")
                    time.sleep(0.5)
                    self._prev_mode = mode
                    return "Title/intro screen - auto-advancing...", {}, {"mode": "loading"}

            if mode == "dialogue":
                pre_map = state.get("map_id", -1)
                all_lines = []
                while True:
                    state, lines = auto_advance_dialogue(state)
                    all_lines.extend(_dedup_progressive_text(lines))
                    mode = state.get("mode", "unknown") if state else "unknown"
                    if mode != "overworld":
                        break
                    post_map = state.get("map_id", -1) if state else -1
                    is_cutscene = post_map != pre_map
                    poll_time = 25 if is_cutscene else 5
                    more = False
                    for _ in range(poll_time):
                        time.sleep(0.2)
                        s = read_state()
                        if s and s.get("mode") == "dialogue":
                            more = True
                            state = s
                            break
                    if not more:
                        break

                text_parts = []
                if self._pending_text:
                    text_parts.extend(self._pending_text)
                    text_parts.append("")
                    self._pending_text = []
                if all_lines:
                    speaker = ""
                    has_name = any(":" in line and line.split(":")[0].strip().isalpha() for line in all_lines)
                    if not has_name and self.last_interacted_name:
                        speaker = self.last_interacted_name
                    if speaker:
                        text_parts.append(f"[{speaker}]")
                    for line in all_lines:
                        text_parts.append(line)
                    self.last_interacted_name = None
                    self.accumulated_text.extend(all_lines)

                    dialogue_str = f"[{speaker}] {' '.join(all_lines)}" if speaker else " ".join(all_lines)
                    self._memory_events.append(dialogue_str)

                    self._note_tutorial_dialogue(all_lines)

                    combined = " ".join(all_lines)
                    if "Those are" in combined and "contain" in combined and "MON" in combined:
                        text_parts.append("[You looked at the Poké Balls but cannot take one yet — you need Professor Oak's permission first.]")


                if state:
                    mode = state.get("mode", "overworld")
                else:
                    mode = "overworld"

                if mode == "menu":
                    menu_items, selected = get_menu_items(state)

                    text_lines_ctx = state.get("text_lines", [])
                    if text_lines_ctx:
                        for line in text_lines_ctx:
                            text_parts.append(line)
                    choice_map = {}
                    letter_idx = 0
                    for i, item in enumerate(menu_items):
                        ch = chr(ord("A") + letter_idx)
                        choice_map[ch] = {
                            "action": "menu_select",
                            "index": i,
                            "selected": selected,
                            "total": len(menu_items),
                            "label": item,
                        }
                        text_parts.append(f"{ch}. {item}")
                        letter_idx += 1
                    ch = chr(ord("A") + letter_idx)
                    choice_map[ch] = {"action": "cancel", "label": "Back/Cancel"}
                    text_parts.append(f"{ch}. Back/Cancel")
                    self._prev_mode = "dialogue"
                    return "\n".join(text_parts), choice_map, {"mode": "menu_after_dialogue"}

                if mode == "keyboard":
                    self._prev_mode = "dialogue"
                    kb_tolk = state.get("tolk_output", []) if state else []
                    kb_text = state.get("text_lines", []) if state else []
                    for line in kb_tolk:
                        clean = line.strip()
                        if clean:
                            text_parts.append(clean)
                    for line in kb_text:
                        text_parts.append(line)
                    return "\n".join(text_parts), {}, {"mode": "keyboard", "needs_name": True}

                if mode == "battle":
                    pass
                else:
                    self._prev_mode = "dialogue"
                    if text_parts:
                        self._pending_text.extend(text_parts)
                    return None

            if mode != "battle" and self._pending_sub_menu:
                self._pending_sub_menu = None

            if mode == "battle":
                self.in_battle = True

                if (_scripted_battle_kind(state) == "tutorial"
                        or self._looks_like_tutorial_battle(state)):
                    state, demo_lines = auto_advance_scripted_battle(state)
                    self.in_battle = False
                    self._tutorial_hint_turns = 0
                    self._pending_sub_menu = None
                    self._battle_opponent = ""
                    self._battle_opponent_level = 0
                    self._battle_type_label = ""
                    if demo_lines:
                        self._pending_text.extend(demo_lines)
                        self.accumulated_text.extend(demo_lines)
                    self._pending_text.append(
                        "[You watched a demonstration — the other person did all of it. "
                        "This was not your battle and none of your choices applied.]"
                    )
                    self._prev_mode = "battle"
                    return None

                if self._prev_mode != "battle":
                    self._move_cursor = 0

                if self._prev_mode != "battle" and not self._battle_opponent:
                    battle_info = _get_battle_info(state)
                    if _battle_data_valid(state):
                        self._battle_opponent = decode_gen1_name(battle_info.get("enemy_name", []))
                        self._battle_opponent_level = battle_info.get("enemy_level", 0)
                        self._battle_type_label = "Wild" if battle_info.get("battle_type", 0) == 1 else "Trainer"

                if self._pending_sub_menu:
                    sub_options = self._pending_sub_menu
                    text_parts = []
                    if self._pending_text:
                        text_parts.extend(self._pending_text)
                        text_parts.append("")
                        self._pending_text = []
                    if _battle_data_valid(state):
                        text_parts.append(_format_battle_stats(state))
                        text_parts.append("")
                    choice_map = {}
                    for i, opt in enumerate(sub_options):
                        ch = chr(ord("A") + i)
                        choice_map[ch] = {
                            "action": "battle_sub_select",
                            "index": i,
                            "label": opt,
                            "options": sub_options,
                        }
                        display_opt = opt
                        if "PP:" in opt:
                            if "(PP: 0)" in opt:
                                display_opt = opt.replace("(PP: 0)", "(PP: 0 EMPTY)")
                        else:
                            opt_base = opt.split("x")[0].strip().split("×")[0].strip().upper()
                            hint = _ITEM_HINTS.get(opt_base, "")
                            if hint:
                                display_opt = f"{opt} — {hint}"
                        text_parts.append(f"{ch}. {display_opt}")
                    is_move_menu = any("PP:" in o for o in sub_options)
                    if is_move_menu:
                        has_pp = [o for o in sub_options if "PP:" in o and "(PP: 0)" not in o]
                        all_empty = len(has_pp) == 0
                        only_nondamage = (has_pp and all(
                            any(nd in o.upper() for nd in ("GROWL", "LEER", "TAIL WHIP", "STRING SHOT",
                                "SAND ATTACK", "HARDEN", "WITHDRAW", "DEFENSE CURL",
                                "DOUBLE TEAM", "MINIMIZE", "SMOKESCREEN", "FLASH",
                                "KINESIS", "SCREECH", "SUPERSONIC", "CONFUSE RAY"))
                            for o in has_pp
                        ))
                        if all_empty:
                            text_parts.append("[All moves are out of PP — select any move and the game will use STRUGGLE, a weak typeless attack that also deals recoil damage to you. PP refills when you heal at a Pokémon Center, so don't worry about running out permanently.]")
                        elif only_nondamage:
                            text_parts.append(f"[Some moves still have PP — use them! PP refills when you heal at a Pokémon Center, so there's no reason to save them. Use what you've got.]")

                    ch = chr(ord("A") + len(sub_options))
                    choice_map[ch] = {"action": "battle_sub_cancel", "label": "Back"}
                    text_parts.append(f"{ch}. Back")
                    self._prev_mode = "battle"
                    return "\n".join(text_parts), choice_map, {"mode": "battle", "state": state, "options": sub_options}

                state, battle_text = auto_advance_battle_text(state)
                battle_text = _dedup_progressive_text(battle_text)

                if state.get("keyboard"):
                    self.in_battle = False
                    self._pending_sub_menu = None
                    if not self.first_battle_completed and self.first_battle_seen:
                        self.first_battle_completed = True
                    self._compose_battle_outcome(battle_text)
                    text_parts = []
                    if self._pending_text:
                        text_parts.extend(self._pending_text)
                        self._pending_text = []
                    if battle_text:
                        text_parts.extend(battle_text)
                        self.accumulated_text.extend(battle_text)
                    text_parts.append(
                        "[You caught it! Name entry is open — type a nickname "
                        "for your new Pokémon.]"
                    )
                    self._prev_mode = "keyboard"
                    return "\n".join(text_parts), {}, {
                        "mode": "keyboard", "needs_name": True, "nickname": True,
                    }

                if _party_select_showing(state, battle_text):
                    text_parts = []
                    if self._pending_text:
                        text_parts.extend(self._pending_text)
                        self._pending_text = []
                    if battle_text:
                        text_parts.extend(battle_text)
                        self.accumulated_text.extend(battle_text)
                    for line in state.get("tolk_output", []) or []:
                        clean = line.strip()
                        if clean and clean not in text_parts:
                            text_parts.append(clean)
                            self.accumulated_text.append(clean)
                    party = state.get("party", []) or []
                    text_parts.append("")
                    text_parts.append("Choose a Pokémon to send out:")
                    choice_map = {}
                    for i, mon in enumerate(party):
                        ch = chr(ord("A") + i)
                        hp = mon.get("hp", 0)
                        max_hp = mon.get("max_hp", 0)
                        label = (f"{party_mon_name(mon)} Lv{mon.get('level', 0)} "
                                 f"({hp}/{max_hp} HP{_hp_descriptor(hp, max_hp)})")
                        if hp <= 0:
                            label += " — FAINTED, can't fight"
                        choice_map[ch] = {
                            "action": "party_select", "index": i,
                            "label": label, "party": party,
                        }
                        text_parts.append(f"{ch}. {label}")
                    ch = chr(ord("A") + len(party))
                    choice_map[ch] = {"action": "party_cancel", "label": "Back"}
                    text_parts.append(f"{ch}. Back (keep your current Pokémon "
                                      "— only works if you weren't forced to switch)")
                    self._prev_mode = "battle"
                    return "\n".join(text_parts), choice_map, {
                        "mode": "battle", "state": state, "options": [],
                    }

                if state.get("mode") != "battle":
                    self.in_battle = False
                    self._pending_sub_menu = None
                    if not self.first_battle_completed and self.first_battle_seen:
                        self.first_battle_completed = True

                    self._compose_battle_outcome(battle_text)

                    if battle_text:
                        self._pending_text.extend(battle_text)
                    self._pending_text.append("[Battle ended]")
                    self.accumulated_text.extend(battle_text)
                    self._prev_mode = "battle"
                    return None

                for _ in range(20):
                    if _battle_data_valid(state):
                        break
                    time.sleep(0.2)
                    state = read_state() or state

                if _battle_data_valid(state) and not self._battle_opponent:
                    battle_info = _get_battle_info(state)
                    self._battle_opponent = decode_gen1_name(battle_info.get("enemy_name", []))
                    self._battle_opponent_level = battle_info.get("enemy_level", 0)
                    self._battle_type_label = "Wild" if battle_info.get("battle_type", 0) == 1 else "Trainer"

                options = _get_battle_options(state)
                if not options:
                    for _ in range(10):
                        time.sleep(0.2)
                        state = read_state() or state
                        if state.get("mode") != "battle":
                            self.in_battle = False
                            self._compose_battle_outcome(battle_text)
                            self._prev_mode = "battle"
                            if battle_text:
                                self._pending_text.extend(battle_text)
                            self._pending_text.append("[Battle ended]")
                            return None
                        options = _get_battle_options(state)
                        if options:
                            break

                if not options and _battle_data_valid(state):
                    screen_lines = state.get("screen_lines", [])
                    screen_text = " ".join(screen_lines).upper()
                    if "FIGHT" in screen_text:
                        options = ["FIGHT", "PKMN", "ITEM", "RUN"]
                    else:
                        send_command("INTERACT")
                        time.sleep(0.5)
                        self._prev_mode = "battle"
                        return None

                text_parts = []
                if self._pending_text:
                    text_parts.extend(self._pending_text)
                    text_parts.append("")
                    self._pending_text = []
                if battle_text:
                    text_parts.extend(battle_text)
                    self.accumulated_text.extend(battle_text)
                if _battle_data_valid(state):
                    text_parts.append("")
                    text_parts.append(_format_battle_stats(state))

                if not options:
                    send_command("INTERACT")
                    time.sleep(0.3)
                    self._prev_mode = "battle"
                    if text_parts:
                        self._pending_text.extend(text_parts)
                    return None

                if _battle_data_valid(state):
                    battle = _get_battle_info(state)
                    battle_type = battle.get("battle_type", 0)
                    enemy_hp = battle.get("enemy_hp", 999)
                    enemy_max = battle.get("enemy_max_hp", 999)
                    if (battle_type == 1 and enemy_max > 0
                            and enemy_hp <= enemy_max * 0.5 and enemy_hp > 0):
                        hp_pct = int(100 * enemy_hp / enemy_max)
                        text_parts.append(f"[Tip: This wild Pokémon is at {hp_pct}% HP — "
                                          f"you can try to CATCH it with a Poké Ball! "
                                          f"Select ITEM and use a Poké Ball. Lower HP = better catch rate.]")

                choice_map = {}
                text_parts.append("")
                for i, opt in enumerate(options):
                    ch = chr(ord("A") + i)
                    choice_map[ch] = {"action": "battle_select", "index": i, "label": opt, "options": options}
                    text_parts.append(f"{ch}. {opt}")

                self._prev_mode = "battle"
                return "\n".join(text_parts), choice_map, {"mode": "battle", "state": state, "options": options}

            if mode == "menu":
                menu_items, selected = get_menu_items(state)

                upper_items = [item.strip().upper() for item in menu_items]
                if "NEW GAME" in upper_items:
                    has_save = self.has_save_files()
                    if has_save and "CONTINUE" in upper_items:
                        target_idx = upper_items.index("CONTINUE")
                    else:
                        target_idx = upper_items.index("NEW GAME")
                    if selected >= 0:
                        delta = target_idx - selected
                        if delta > 0:
                            for _ in range(delta):
                                send_command("NAV:DOWN")
                                time.sleep(0.15)
                        elif delta < 0:
                            for _ in range(-delta):
                                send_command("NAV:UP")
                                time.sleep(0.15)
                    else:
                        for _ in range(target_idx):
                            send_command("NAV:DOWN")
                            time.sleep(0.15)
                    send_command("INTERACT")
                    time.sleep(0.5)
                    return None

                text_lines_check = state.get("text_lines", [])
                if (any("CHOOSE A" in it.upper() for it in upper_items)
                        or any("CHOOSE A" in t.upper() for t in text_lines_check)):
                    tile_map = state.get("tile_map", [])
                    screen_lines = decode_screen_text(tile_map) if tile_map else []

                    tolk = state.get("tolk_output", [])
                    all_text_lines = screen_lines + tolk + menu_items

                    hp_lines = [t for t in all_text_lines if "/" in t and any(c.isdigit() for c in t)]

                    skip_patterns = {"CHOOSE", "CANCEL", "TYPE", "MENU"}
                    pkmn_name = ""
                    for t in all_text_lines:
                        clean = t.strip()
                        upper = clean.upper()
                        if not clean or len(clean) < 2:
                            continue
                        if any(p in upper for p in skip_patterns):
                            continue
                        if "/" in clean:
                            continue
                        if upper.startswith(":L") or upper.startswith("LV"):
                            continue
                        if not clean[0].isalpha():
                            continue
                        name_part = clean.split(":L")[0].split("Lv")[0].split("LV")[0].strip()
                        if name_part and len(name_part) >= 2:
                            pkmn_name = name_part
                            break

                    pkmn_level = ""
                    for t in all_text_lines:
                        clean = t.strip()
                        lv_match = re.search(r'(?::L|[Ll][Vv])\s*(\d+)', clean)
                        if lv_match:
                            pkmn_level = f"Lv{lv_match.group(1)}"
                            break

                    pkmn_hp = ""
                    for t in all_text_lines:
                        clean = t.strip()
                        hp_match = re.search(r'(\d+)\s*/\s*(\d+)', clean)
                        if hp_match:
                            pkmn_hp = f"{hp_match.group(1)}/{hp_match.group(2)} HP"
                            break

                    if self._item_use_pending:
                        self._item_use_pending = False
                        send_command("INTERACT")
                        time.sleep(0.6)
                        used_lines = []
                        for _ in range(12):
                            s = read_state()
                            if s:
                                for line in s.get("tolk_output", []):
                                    clean = line.lstrip("→").strip()
                                    if clean and clean not in used_lines:
                                        used_lines.append(clean)
                                if s.get("mode") == "dialogue":
                                    s, lines = auto_advance_dialogue(s)
                                    for line in lines:
                                        if line.strip() and line.strip() not in used_lines:
                                            used_lines.append(line.strip())
                                    break
                                if used_lines:
                                    break
                            time.sleep(0.2)
                        if used_lines:
                            self._pending_text.extend(used_lines)
                        return None

                    if len(hp_lines) <= 1:
                        send_command("CANCEL")
                        time.sleep(0.3)
                        status_parts = []
                        if pkmn_name:
                            status_parts.append(pkmn_name)
                        if pkmn_level:
                            status_parts.append(pkmn_level)
                        if pkmn_hp:
                            status_parts.append(pkmn_hp)
                        status_line = " — ".join(status_parts) if status_parts else "status unknown"
                        self._pending_text.append(f"Your Pokémon: {status_line}. Only one Pokémon, no one to switch with.")
                        return None

                if "USE" in upper_items and "TOSS" in upper_items:
                    if self._selected_item_name == "TOWN MAP":
                        send_command("CANCEL")
                        time.sleep(0.3)
                        self._selected_item_name = ""
                        self._pending_text.extend(_town_map_text(self.current_location))
                        return None

                    use_idx = upper_items.index("USE")
                    if selected >= 0:
                        delta = use_idx - selected
                        for _ in range(abs(delta)):
                            send_command("NAV:DOWN" if delta > 0 else "NAV:UP")
                            time.sleep(0.15)
                    else:
                        for _ in range(use_idx):
                            send_command("NAV:DOWN")
                            time.sleep(0.15)
                    self._item_use_pending = True
                    send_command("INTERACT")

                    said = []
                    for _ in range(12):
                        time.sleep(0.2)
                        s = read_state()
                        if not s:
                            continue
                        for line in s.get("tolk_output", []):
                            clean = line.lstrip("→").strip()
                            if clean and clean not in said:
                                said.append(clean)
                        if s.get("mode") == "dialogue":
                            s, lines = auto_advance_dialogue(s)
                            for line in lines:
                                if line.strip() and line.strip() not in said:
                                    said.append(line.strip())
                            break
                        if said:
                            break

                    joined = " ".join(said).upper()
                    refused = any(p in joined for p in (
                        "ISN'T THE TIME", "IS NOT THE TIME", "NOT THE TIME",
                        "WON'T HAVE ANY EFFECT", "NO EFFECT",
                        "CAN'T USE THAT", "CANNOT USE",
                        "OAK:",
                    ))
                    if refused:
                        self._item_use_pending = False
                        self._pending_text.append(
                            f"You can't use that here — {' '.join(said)}"
                            if said else "You can't use that item right now."
                        )
                    elif said:
                        self._pending_text.extend(said)
                    return None

                non_cancel = [it for it in upper_items if it not in ("CANCEL",)]
                if not non_cancel and "CANCEL" in upper_items:
                    send_command("CANCEL")
                    time.sleep(0.3)
                    self._pending_text.append("No items in your bag!")
                    return None

                if any(_is_quantity_line(item) for item in menu_items):
                    scanned_items, _ = _scan_item_list(state)
                    if len(scanned_items) > 1:
                        for _ in range(len(scanned_items)):
                            send_command("NAV:UP")
                            time.sleep(0.1)
                        time.sleep(0.2)
                        menu_items = scanned_items
                        selected = 0
                        self._item_list_cache = (menu_items, selected, True)
                    else:
                        visible_items, cursor_idx = _read_visible_item_list(state)
                        if visible_items:
                            menu_items = visible_items
                            selected = cursor_idx
                            self._item_list_cache = (menu_items, selected, False)

                is_name_menu = "NEW NAME" in upper_items

                _KNOWN_MENU_ITEMS = {"EXIT", "CANCEL", "ITEM", "POKÉMON", "POKÉDEX",
                                     "POKEDEX", "POKéMON", "POKéDEX",
                                     "SAVE", "OPTION", "YES", "NO",
                                     "USE", "TOSS",
                                     "NEW NAME", "RED", "ASH", "JACK",
                                     "BLUE", "GARY", "JOHN"}
                if not self.player_name and not is_name_menu:
                    for item in menu_items:
                        stripped = item.strip()
                        if (1 <= len(stripped) <= 7 and stripped.isalpha()
                                and stripped.upper() not in _KNOWN_MENU_ITEMS):
                            self.player_name = stripped
                            break

                filtered_items = []
                for item in menu_items:
                    upper = item.strip().upper()
                    if upper in ("OPTION", "SAVE") or "CHOOSE A" in upper:
                        continue
                    if self.player_name and item.strip().upper() == self.player_name.upper():
                        continue
                    if (not is_name_menu
                            and len(item.strip()) <= 7 and item.strip().isalpha()
                            and item.strip().isupper()
                            and upper not in _KNOWN_MENU_ITEMS):
                        continue
                    filtered_items.append(item)

                text_parts = []
                if self._pending_text:
                    text_parts.extend(self._pending_text)
                    text_parts.append("")
                    self._pending_text = []
                text_lines_ctx = state.get("text_lines", [])
                if text_lines_ctx:
                    for line in text_lines_ctx:
                        text_parts.append(line)

                player_money = state.get("player_money")
                if player_money is not None:
                    text_parts.append(f"[Your balance: ${player_money}]")

                choice_map = {}
                letter_idx = 0
                for i, item in enumerate(filtered_items):
                    ch = chr(ord("A") + letter_idx)
                    orig_idx = menu_items.index(item)
                    display_label = "Close" if item.strip().upper() == "EXIT" else item
                    item_base = item.split("¥")[0].split("x")[0].strip().split("×")[0].strip().upper()
                    item_hint = _ITEM_HINTS.get(item_base, "")
                    if item_hint:
                        display_label = f"{display_label} — {item_hint}"
                    choice_map[ch] = {
                        "action": "menu_select",
                        "index": orig_idx,
                        "selected": selected,
                        "total": len(menu_items),
                        "label": item,
                        "_menu_items": state.get("menu_items", []),
                    }
                    text_parts.append(f"{ch}. {display_label}")
                    letter_idx += 1

                filtered_upper = {it.strip().upper() for it in filtered_items}
                if not ("EXIT" in filtered_upper or "CANCEL" in filtered_upper):
                    ch = chr(ord("A") + letter_idx)
                    choice_map[ch] = {"action": "cancel", "label": "Back/Cancel"}
                    text_parts.append(f"{ch}. Back/Cancel")

                _STARTERS = {"CHARMANDER", "SQUIRTLE", "BULBASAUR"}
                chosen_starter = filtered_upper & _STARTERS
                if chosen_starter:
                    name = chosen_starter.pop()
                    others = _STARTERS - {name}
                    text_parts.append(f"[Note: There are 3 starter Pokémon to choose from: {', '.join(sorted(_STARTERS))}. Pick wisely!]")

                self._prev_mode = "menu"
                return "\n".join(text_parts), choice_map, {"mode": "menu"}

            if mode == "keyboard":
                text_parts = []
                tolk = state.get("tolk_output", [])
                if tolk:
                    add_to_history(tolk)
                    for line in tolk:
                        clean = line.strip()
                        if clean:
                            text_parts.append(clean)
                text_lines = state.get("text_lines", [])
                if text_lines:
                    for line in text_lines:
                        text_parts.append(line)

                self._prev_mode = "keyboard"
                return "\n".join(text_parts), {}, {"mode": "keyboard", "needs_name": True}

            if mode == "overworld":
                self._item_use_pending = False
                self._selected_item_name = ""
                current_map_id = state.get("map_id")
                if current_map_id != self.interacted_map_id:
                    self.interacted_objects.clear()
                    self._blocked_objects.clear()
                    self.interacted_map_id = current_map_id
                self.current_map_id = current_map_id

                map_name = state.get("map_name", "???")
                self._update_location(map_name)

                map_db_data = self._get_map_db_data()
                if self._map_available:
                    objects = get_map_objects(state, map_db_data)
                else:
                    objects = prepare_objects(state.get("objects", []))

                if not objects:
                    for _retry in range(6):
                        time.sleep(0.5)
                        state = read_state()
                        if not state:
                            continue
                        if state.get("mode") != "overworld":
                            break
                        if self._map_available:
                            objects = get_map_objects(state, map_db_data)
                        else:
                            objects = prepare_objects(state.get("objects", []))
                        if objects:
                            break

                if not objects and state and state.get("mode") == "overworld":
                    auto_explore()
                    state = read_state() or state
                    if self._map_available:
                        objects = get_map_objects(state, map_db_data)
                    else:
                        objects = prepare_objects(state.get("objects", []))

                text_parts = []
                if self._pending_text:
                    text_parts.extend(self._pending_text)
                    text_parts.append("")
                    self._pending_text = []

                clean_name = self.current_location or map_name.replace("[", "").replace("]", "").strip()
                text_parts.append(f"Current Location - {clean_name}")

                dir_hints = []
                for obj in objects:
                    if obj.get("type") == "connection":
                        c_dir = obj.get("connection_dir", "")
                        c_name = obj.get("name", "")
                        if c_dir and c_name:
                            parts = c_name.split(", ", 1)
                            dest = parts[1] if len(parts) > 1 else c_name
                            dir_hints.append(f"{c_dir.capitalize()} \u2192 {dest}")

                party_data = state.get("party", [])
                if party_data:
                    party_strs = []
                    for mon in party_data:
                        name = party_mon_name(mon)
                        lv = mon.get("level", 0)
                        hp = mon.get("hp", 0)
                        max_hp = mon.get("max_hp", 0)
                        hp_desc = _hp_descriptor(hp, max_hp)
                        party_strs.append(f"{name} Lv{lv} ({hp}/{max_hp} HP{hp_desc})")
                    text_parts.append(f"[Party: {', '.join(party_strs)}]")

                bag = read_bag(state)
                bag_line = format_bag_line(bag)
                if bag_line:
                    text_parts.append(bag_line)

                tileset_id = state.get("tileset_id", -1)
                tile_map = state.get("tile_map", [])
                if tileset_id in GRASS_TILES and len(tile_map) >= 189:
                    player_tile = tile_map[9 * 20 + 8]
                    if player_tile in GRASS_TILES[tileset_id]:
                        text_parts.append("[You are in tall grass \u2014 wild Pokemon may appear!]")

                tolk = state.get("tolk_output", [])
                if tolk:
                    add_to_history(tolk)
                    for line in tolk:
                        clean = line.strip()
                        if clean:
                            text_parts.append(f">> {clean}")

                choice_map = {}
                letter_idx = 0

                non_conn_objects = [
                    o for o in objects
                    if o.get("type") != "connection"
                ]
                if non_conn_objects:
                    text_parts.append("")
                    text_parts.append("Nearby:")
                    for i, obj in enumerate(non_conn_objects):
                        name = obj.get("name", "???")
                        obj_type = obj.get("type", "")
                        type_label = ""
                        if obj_type == "warp":
                            if name not in ("Stairs", "Outside"):
                                type_label = " (door)"

                        obj_x, obj_y = obj.get("x", -1), obj.get("y", -1)
                        mark = ""
                        if (obj_x, obj_y) in self._blocked_objects:
                            mark = " (blocked — trees or walls in the way)"
                        elif (obj_x, obj_y) in self.interacted_objects:
                            mark = " (already interacted)"
                        elif (obj_type == "warp" and self._just_left_location
                              and name.upper().replace("'", "").replace(" ", "")
                              == self._just_left_location.upper().replace("'", "").replace(" ", "")):
                            mark = " (you just exited here — explore elsewhere)"

                        dist = obj.get("dist", "")
                        dist_str = f" - {dist}" if dist else ""

                        ch = chr(ord("A") + letter_idx)
                        choice_map[ch] = {
                            "action": "walk_to_object",
                            "object": obj,
                            "label": f"{name}{type_label}{mark}",
                            "_state": state,
                        }
                        text_parts.append(f"{ch}. {name}{type_label}{mark}{dist_str}")
                        letter_idx += 1
                else:
                    text_parts.append("")
                    text_parts.append("(No objects nearby)")

                is_outdoor = False
                if self._map_available:
                    md = _get_map_data(state, map_db_data)
                    if md and md.get("_outdoor", False):
                        is_outdoor = True

                if is_outdoor:
                    text_parts.append("")
                    for direction, label in [("up", "North"), ("down", "South"),
                                             ("right", "East"), ("left", "West")]:
                        ch = chr(ord("A") + letter_idx)
                        choice_map[ch] = {
                            "action": "walk_direction",
                            "direction": direction,
                            "label": label,
                            "_state": state,
                        }
                        text_parts.append(f"{ch}. {label}")
                        letter_idx += 1

                    ch = chr(ord("A") + letter_idx)
                    choice_map[ch] = {"action": "view_map", "label": "View Map"}
                    text_parts.append(f"{ch}. View Map")
                    letter_idx += 1

                extra_actions = []

                hurt = [
                    party_mon_name(m)
                    for m in (state.get("party") or [])
                    if 0 < m.get("hp", 0) < m.get("max_hp", 0)
                ]
                heal_item = next(
                    (name for name, _ in bag
                     if name.upper() in _HEALING_ITEMS), ""
                )
                if hurt and heal_item:
                    extra_actions.append({
                        "action": "heal_pokemon",
                        "item": heal_item,
                        "label": f"Use a {heal_item} on {hurt[0]}",
                    })

                for idx, mon_name, move in party_field_moves(state):
                    extra_actions.append({
                        "action": "field_move",
                        "party_index": idx,
                        "move": move,
                        "label": f"Use {move} ({mon_name})",
                    })

                if any(name.upper() == "TOWN MAP" for name, _ in bag):
                    extra_actions.append({
                        "action": "town_map",
                        "label": "Check the Town Map",
                    })

                if extra_actions:
                    text_parts.append("")
                    for act in extra_actions:
                        ch = chr(ord("A") + letter_idx)
                        choice_map[ch] = act
                        text_parts.append(f"{ch}. {act['label']}")
                        letter_idx += 1

                state["_display_objects"] = objects

                if self._just_left_remaining > 0:
                    self._just_left_remaining -= 1
                    if self._just_left_remaining <= 0:
                        self._just_left_location = ""

                if self._tutorial_hint_turns > 0:
                    self._tutorial_hint_turns -= 1

                final_text = "\n".join(text_parts)
                if self._prev_state_text and final_text == self._prev_state_text:
                    last_action = self._recent_actions[-1] if self._recent_actions else ""
                    if "Walking" in last_action and "..." in last_action:
                        last_dir = last_action.split("Walking ")[-1].split("...")[0]
                        final_text += f"\n\n[Note: Your last move ({last_dir}) didn't change your position — you may be at the edge of this area or blocked by a wall. Try a different direction or interact with something from the Nearby list instead.]"
                    elif "Walking" not in last_action:
                        pass
                    else:
                        final_text += "\n\n[Note: Nothing has changed since your last action. Try a different choice — pick something from the Nearby list or go a different direction.]"
                self._prev_state_text = "\n".join(text_parts)

                self._prev_mode = "overworld"
                return final_text, choice_map, {
                    "mode": "overworld",
                    "state": state,
                    "map_name": map_name,
                    "objects": objects,
                    "is_outdoor": is_outdoor,
                }

            if mode == "error":
                self._prev_mode = mode
                return f"Bridge error: {state.get('error_msg', 'unknown')}", {}, {"mode": "error"}

            text_parts = []
            tolk = state.get("tolk_output", [])
            if tolk:
                add_to_history(tolk)
                for line in tolk:
                    text_parts.append(line.strip())
            text_lines = state.get("text_lines", [])
            if text_lines:
                for line in text_lines:
                    text_parts.append(line)

            choice_map = {
                "A": {"action": "press_a", "label": "Press A"},
                "B": {"action": "press_b", "label": "Press B"},
            }
            text_parts.append("")
            text_parts.append("A. Press A")
            text_parts.append("B. Press B")

            self._prev_mode = mode
            return "\n".join(text_parts), choice_map, {"mode": "unknown"}

        return await asyncio.to_thread(_get_state_sync)

    async def send_choice(self, letter, choice_map):
        letter = letter.upper()
        if letter not in choice_map:
            return f"Invalid choice: {letter}"

        choice = choice_map[letter]
        action = choice.get("action", "")

        def _execute_sync():
            if action == "walk_to_object":
                obj = choice["object"]
                target_name = obj.get("name", "???")
                obj_x, obj_y = obj.get("x", -1), obj.get("y", -1)

                walk_state = choice.get("_state", read_state() or {})

                def _attempt_walk(ws):
                    if obj.get("_map_obj"):
                        return map_walk_to(obj, ws, self.pokeball_names)
                    return walk_to_object(obj, ws), None

                def _interaction_happened(before, after, lines):
                    if after.get("mode") not in ("overworld", None, ""):
                        return True
                    if after.get("tolk_output"):
                        return True
                    if lines:
                        return True
                    bx, by = before.get("player_x", -1), before.get("player_y", -1)
                    ax, ay = after.get("player_x", -2), after.get("player_y", -2)
                    if bx >= 0 and by >= 0 and (ax, ay) != (bx, by):
                        return True
                    return False

                result_state, screen_lines = _attempt_walk(walk_state)
                after_state = read_state() or result_state or {}

                if (obj.get("type") not in ("warp", "connection")
                        and not _interaction_happened(walk_state, after_state, screen_lines)):
                    time.sleep(0.4)
                    retry_state = read_state() or after_state
                    result_state, screen_lines = _attempt_walk(retry_state)
                    after_state = read_state() or result_state or {}
                    if not _interaction_happened(retry_state, after_state, screen_lines):
                        if obj_x >= 0 and obj_y >= 0:
                            self._blocked_objects.add((obj_x, obj_y))
                        return (f"Couldn't reach {target_name} — you walked at it twice "
                                f"and something is in the way (trees or a wall). "
                                f"It's marked blocked now; try approaching from a "
                                f"different direction.")

                if obj.get("type") not in ("warp", "connection"):
                    state = after_state
                    self._mark_interacted(obj, state.get("map_id") if state else None)
                    self.last_interacted_name = target_name
                else:
                    self.last_interacted_name = None
                    self.last_warp_used = target_name

                warp_succeeded = False
                if obj.get("type") == "warp":
                    old_map_id = choice.get("_state", {}).get("map_id", -1)
                    old_location = self.current_location
                    time.sleep(0.5)
                    for attempt in range(8):
                        check = read_state()
                        if not check:
                            break
                        check_mode = check.get("mode", "overworld")
                        if check_mode in ("dialogue", "battle", "menu"):
                            warp_succeeded = True
                            break
                        new_id = check.get("map_id", -1)
                        if new_id != old_map_id:
                            warp_succeeded = True
                            self._just_left_location = old_location
                            self._just_left_remaining = 4
                            break
                        rpx = check.get("player_x", -1)
                        rpy = check.get("player_y", -1)
                        tx, ty = obj.get("x", rpx), obj.get("y", rpy)
                        rdx, rdy = tx - rpx, ty - rpy
                        if rdx == 0 and rdy == 0:
                            send_command("PRESS:down")
                            time.sleep(0.5)
                        elif abs(rdy) >= abs(rdx):
                            send_command(f"PRESS:{'down' if rdy > 0 else 'up'}")
                            time.sleep(0.5)
                        else:
                            send_command(f"PRESS:{'right' if rdx > 0 else 'left'}")
                            time.sleep(0.5)
                        time.sleep(0.3)

                    if not warp_succeeded:
                        return (f"Couldn't get through to {target_name} — something is in the way. "
                                f"Try walking around it first, or pick something else.")

                result = f"Walking to {target_name}..."
                if screen_lines:
                    result += "\n" + "\n".join(screen_lines)
                return result

            elif action == "walk_direction":
                direction = choice["direction"]
                state = choice.get("_state", read_state() or {})
                if not state:
                    return f"Cannot walk {direction} - no state"

                passability = state.get("_passability")
                pos = state.get("_player_pos")
                DELTAS = {"up": (0, -1), "down": (0, 1), "right": (1, 0), "left": (-1, 0)}
                ddx, ddy = DELTAS.get(direction, (0, 0))

                if passability and pos and HAS_BFS:
                    px, py = pos
                    rows = len(passability)
                    cols = len(passability[0]) if rows > 0 else 0

                    objects = state.get("_display_objects", [])
                    blocked_warps = []
                    for o in objects:
                        if o.get("_tile_type") == "warp":
                            wx, wy = o.get("x", -1), o.get("y", -1)
                            if 0 <= wy < rows and 0 <= wx < cols and passability[wy][wx]:
                                passability[wy][wx] = False
                                blocked_warps.append((wx, wy))

                    path = None
                    for dist in range(15, 0, -1):
                        best_path = None
                        best_offset = 999
                        ty = py + ddy * dist
                        tx = px + ddx * dist
                        for offset in range(9):
                            for sign in ([0] if offset == 0 else [1, -1]):
                                off = offset * sign
                                if ddy != 0:
                                    gx, gy = px + off, ty
                                else:
                                    gx, gy = tx, py + off
                                if not (0 <= gy < rows and 0 <= gx < cols):
                                    continue
                                if not passability[gy][gx]:
                                    continue
                                p = bfs_pathfind(passability, (px, py), (gx, gy))
                                if p is not None and (best_path is None or len(p) < len(best_path)):
                                    best_path = p
                                    best_offset = abs(offset)
                                    if best_offset == 0:
                                        break
                            if best_path and best_offset == 0:
                                break
                        if best_path:
                            path = best_path
                            break

                    for wx, wy in blocked_warps:
                        passability[wy][wx] = True

                    if path:
                        steps = len(path)
                        _send_moves(path, self.vba_hwnd)
                    elif ((direction == "up" and py <= 1) or
                          (direction == "down" and py >= rows - 2) or
                          (direction == "left" and px <= 1) or
                          (direction == "right" and px >= cols - 2)):
                        steps = 3
                        _send_moves([direction] * 3, self.vba_hwnd)
                    else:
                        alt = "East or West" if direction in ("up", "down") else "North or South"
                        return f"Can't go {choice['label']} - blocked! Try {alt}."
                else:
                    steps = 5
                    _send_moves([direction] * 5, self.vba_hwnd)

                time.sleep(0.5)
                post = read_state()
                if post and post.get("mode") == "dialogue":
                    return f"Walking {choice['label']}... moved {steps} tile{'s' if steps != 1 else ''} (something happened)"
                return f"Walking {choice['label']}... moved {steps} tile{'s' if steps != 1 else ''}"

            elif action == "heal_pokemon":
                item_name = choice.get("item", "").upper()
                open_start_menu()
                if not menu_pick(("ITEM",)):
                    send_command("CANCEL")
                    return "Couldn't open your bag."

                time.sleep(0.4)
                s = read_state() or {}
                scanned, _ = _scan_item_list(s)
                if not scanned:
                    send_command("CANCEL")
                    return "Couldn't read your bag."
                for _ in range(len(scanned)):
                    send_command("NAV:UP")
                    time.sleep(0.1)
                time.sleep(0.2)

                upper = [it.split("x")[0].strip().split("×")[0].strip().upper()
                         for it in scanned]
                if item_name not in upper:
                    send_command("CANCEL")
                    return f"No {item_name} in your bag."
                menu_nav_to(upper.index(item_name), 0)
                self._selected_item_name = item_name
                send_command("INTERACT")
                time.sleep(0.4)
                return f"Using {item_name}..."

            elif action == "field_move":
                move = choice.get("move", "").upper()
                party_index = choice.get("party_index", 0)
                open_start_menu()
                if not menu_pick(("POKéMON", "POKEMON")):
                    send_command("CANCEL")
                    return "Couldn't open your party."

                time.sleep(0.5)
                s = read_state()
                _, sel = get_menu_items(s) if s else ([], -1)
                menu_nav_to(party_index, sel)
                send_command("INTERACT")
                time.sleep(0.5)

                if not menu_pick((move,)):
                    for _ in range(4):
                        send_command("CANCEL")
                        time.sleep(0.3)
                    return f"{move} can't be used here."

                said = []
                for _ in range(15):
                    time.sleep(0.2)
                    st = read_state()
                    if not st:
                        continue
                    for line in st.get("tolk_output", []):
                        clean = line.lstrip("→").strip()
                        if clean and clean not in said:
                            said.append(clean)
                    if st.get("mode") == "dialogue":
                        st, lines = auto_advance_dialogue(st)
                        for line in lines:
                            if line.strip() and line.strip() not in said:
                                said.append(line.strip())
                        break
                    if st.get("mode") == "overworld":
                        break
                for _ in range(3):
                    st = read_state()
                    if st and st.get("mode") == "overworld":
                        break
                    send_command("CANCEL")
                    time.sleep(0.3)
                return "\n".join(said) if said else f"Used {move}."

            elif action == "town_map":
                return "\n".join(_town_map_text(self.current_location))

            elif action == "view_map":
                return "MAP_VIEW_REQUESTED"

            elif action == "battle_select":
                target_idx = choice["index"]
                options = choice.get("options", [])
                state = choice.get("_state") or read_state()

                _battle_select(state, target_idx, options=options)

                time.sleep(0.6)

                _MAIN_MENU = {"FIGHT", "PKMN", "ITEM", "RUN"}
                sub_options = []
                for _ in range(20):
                    time.sleep(0.15)
                    state = read_state() or state
                    if state.get("mode") != "battle":
                        self.in_battle = False
                        return "Battle action executed."
                    opts = _get_battle_options(state)
                    if opts:
                        opts_set = set(o.strip().upper() for o in opts)
                        if opts_set != _MAIN_MENU:
                            sub_options = opts
                            break
                    elif target_idx != 0:
                        break

                if not sub_options and target_idx != 0:
                    sub_text = []
                    sub_seen = set()
                    for _ in range(20):
                        s = read_state()
                        if not s or s.get("mode") != "battle":
                            return "Action completed."
                        for line in s.get("tolk_output", []):
                            clean = line.strip()
                            if clean and clean not in sub_seen:
                                sub_seen.add(clean)
                                sub_text.append(clean)
                        tolk_joined = " ".join(s.get("tolk_output", [])).upper()
                        if ("ALREADY OUT" in tolk_joined or "NO WILL TO FIGHT" in tolk_joined
                                or "CHOOSE A" in tolk_joined):
                            break
                        time.sleep(0.2)

                    if target_idx == 1:
                        pkmn_count = len((read_state() or state).get("party", []) or [])
                        if pkmn_count <= 1:
                            _cancel_to_battle_menu(state)
                            return "You can't swap - you only have one Pokémon!"
                        return "\n".join(sub_text) if sub_text else "Choose a Pokémon."
                    if target_idx == 2:
                        time.sleep(0.3)
                        scan_state = read_state() or state
                        scanned_items, _ = _scan_item_list(scan_state)
                        item_options = [
                            it for it in scanned_items
                            if it.upper() != "CANCEL"
                        ]
                        if not item_options:
                            _cancel_to_battle_menu(read_state() or state)
                            return "No items in your bag!"
                        for _ in range(len(scanned_items)):
                            send_command("NAV:UP")
                            time.sleep(0.1)
                        if item_options:
                            self._pending_sub_menu = item_options
                            _BALL_NAMES = {"POKE BALL", "GREAT BALL", "ULTRA BALL", "MASTER BALL", "SAFARI BALL"}
                            has_balls = any(
                                it.split("x")[0].strip().split("×")[0].strip().upper() in _BALL_NAMES
                                for it in item_options
                            )
                            result = "SUB_MENU:" + json.dumps(item_options)
                            if not has_balls:
                                result += "\n[You have no Poké Balls! Visit a mart to buy some after this battle.]"
                            return result
                    if target_idx == 3:
                        time.sleep(1.0)
                        s = read_state()
                        run_tolk = s.get("tolk_output", []) if s else []
                        result_text = " ".join(t.strip() for t in run_tolk if t.strip())
                        if not result_text:
                            result_text = "Attempted to run..."
                        _cancel_to_battle_menu(s or state)
                        return result_text

                    _cancel_to_battle_menu(state)
                    return "\n".join(sub_text) if sub_text else "Action completed."

                if not sub_options and target_idx == 0:
                    battle = _get_battle_info(state)
                    player_moves = battle.get("player_moves", [])
                    player_pp = battle.get("player_pp", [])
                    for i, move_id in enumerate(player_moves):
                        if move_id == 0:
                            continue
                        move_name = GEN1_MOVES[move_id] if 0 < move_id < len(GEN1_MOVES) else f"Move {move_id}"
                        pp = player_pp[i] if i < len(player_pp) else "?"
                        sub_options.append(f"{move_name} (PP: {pp})")

                if sub_options:
                    self._pending_sub_menu = sub_options
                    return "SUB_MENU:" + json.dumps(sub_options)

                return "Battle action executed."

            elif action == "battle_sub_select":
                target_idx = choice["index"]
                sub_options = choice.get("options", [])
                self._pending_sub_menu = None

                selected_opt = sub_options[target_idx] if target_idx < len(sub_options) else ""
                all_moves_empty = all("(PP: 0)" in o for o in sub_options if "PP:" in o)
                if "(PP: 0)" in selected_opt and not all_moves_empty:
                    move_name = selected_opt.split("(")[0].strip()
                    self._pending_sub_menu = sub_options
                    return f"Can't use {move_name} — no PP left! Pick a different move. (PP refills when you visit a Pokémon Center.)"

                fresh = read_state()

                is_item_list = sub_options and not any("PP:" in o for o in sub_options)

                if is_item_list:
                    item_name = sub_options[target_idx] if target_idx < len(sub_options) else "item"
                    for _ in range(target_idx):
                        send_command("NAV:DOWN")
                        time.sleep(0.2)
                    send_command("INTERACT")

                    post = None
                    for _ in range(15):
                        time.sleep(0.15)
                        post = read_state()
                        if post and (post.get("tolk_output") or post.get("mode") != "battle"):
                            break

                    if post:
                        post_tolk = " ".join(post.get("tolk_output", [])).upper()
                        post_mode = post.get("mode", "")
                        rejected = any(phrase in post_tolk for phrase in [
                            "WON'T HAVE ANY EFFECT",
                            "NOT THE TIME",
                            "CAN'T USE",
                            "NO EFFECT",
                            "OAK:",
                            "THIS ISN'T THE TIME",
                        ])
                        if rejected or (post_mode in ("menu", "battle") and not post.get("battle_menu")):
                            send_command("INTERACT")
                            time.sleep(0.5)
                            _cancel_to_battle_menu(read_state() or post)
                            return f"Can't use {item_name} right now! It won't have any effect."

                    return f"Used {item_name}."
                else:
                    _move_menu_select(self._move_cursor, target_idx, len(sub_options))
                    self._move_cursor = target_idx

                    for _ in range(15):
                        time.sleep(0.15)
                        check = read_state()
                        if check and check.get("mode") != "battle":
                            break
                        if check and not check.get("battle_menu"):
                            break
                    return "Move selected."

            elif action == "battle_sub_cancel":
                self._pending_sub_menu = None
                send_command("CANCEL")
                time.sleep(0.3)
                return "Cancelled."

            elif action == "party_select":
                target_idx = choice["index"]
                party = choice.get("party", [])
                mon = party[target_idx] if target_idx < len(party) else {}
                name = party_mon_name(mon) if mon else "that Pokémon"
                if mon.get("max_hp", 0) and mon.get("hp", 0) <= 0:
                    return (f"{name} has fainted and can't fight! "
                            f"Pick a Pokémon that still has HP.")
                fresh = read_state() or {}
                menu_nav_to(target_idx, fresh.get("menu_selected", -1))
                send_command("INTERACT")
                time.sleep(0.6)
                return f"Sent out {name}."

            elif action == "party_cancel":
                send_command("CANCEL")
                time.sleep(0.4)
                s = read_state()
                if s and _party_select_showing(s):
                    return ("You have to send out a Pokémon — you can't back "
                            "out of this one.")
                return "Kept your current Pokémon."

            elif action == "menu_select":
                idx = choice["index"]
                selected = choice.get("selected", -1)
                total = choice.get("total", 0)

                raw_label = choice.get("label", "")
                self._selected_item_name = (
                    raw_label.split("x")[0].strip().split("×")[0].strip().upper()
                )

                if self._item_list_cache:
                    cached = self._item_list_cache
                    cached_items, cached_sel = cached[0], cached[1]
                    full_scan = cached[2] if len(cached) > 2 else False
                    self._item_list_cache = None
                    if full_scan:
                        selected = cached_sel
                    else:
                        fresh = read_state()
                        if fresh:
                            _, fresh_sel = _read_visible_item_list(fresh)
                            selected = fresh_sel if fresh_sel >= 0 else cached_sel
                        else:
                            selected = cached_sel

                if selected >= 0:
                    delta = idx - selected
                    if delta > 0:
                        for _ in range(delta):
                            send_command("NAV:DOWN")
                            time.sleep(0.15)
                    elif delta < 0:
                        for _ in range(-delta):
                            send_command("NAV:UP")
                            time.sleep(0.15)
                else:
                    for _ in range(idx):
                        send_command("NAV:DOWN")
                        time.sleep(0.15)

                send_command("INTERACT")
                time.sleep(0.3)

                response_lines = []
                pre_items = choice.get("_menu_items", [])
                for _ in range(10):
                    s = read_state()
                    if not s:
                        time.sleep(0.2)
                        continue
                    m = s.get("mode", "")
                    if m == "dialogue":
                        s, lines = auto_advance_dialogue(s)
                        response_lines.extend(lines)
                        break
                    elif m != "menu":
                        for line in s.get("tolk_output", []):
                            clean = line.lstrip("\u2192").strip()
                            if clean:
                                response_lines.append(clean)
                        break
                    else:
                        new_items = s.get("menu_items", [])
                        if new_items and new_items != pre_items:
                            break
                    time.sleep(0.2)

                time.sleep(0.5)
                post = read_state()
                if post and post.get("mode") == "overworld":
                    send_command("PRESS:down")
                    time.sleep(0.3)

                label = choice.get('label', '?')
                if response_lines:
                    return f"Selected: {label}\n" + "\n".join(response_lines)
                return f"Selected: {label}"

            elif action == "cancel":
                pre = read_state() or {}
                pre_items = pre.get("menu_items", [])
                pre_text = pre.get("text_lines", [])
                pre_mode = pre.get("mode")
                send_command("CANCEL")
                for _ in range(10):
                    time.sleep(0.15)
                    s = read_state()
                    if not s:
                        continue
                    if (s.get("menu_items", []) != pre_items
                            or s.get("text_lines", []) != pre_text
                            or s.get("mode") != pre_mode):
                        break
                return "Cancelled."

            elif action == "press_a":
                send_command("INTERACT")
                time.sleep(0.3)
                return "Pressed A."

            elif action == "press_b":
                send_command("CANCEL")
                time.sleep(0.3)
                return "Pressed B."

            else:
                return f"Unknown action: {action}"

        result = await asyncio.to_thread(_execute_sync)
        if result and choice.get("action") == "walk_direction":
            self._recent_actions.append(result)
        return result

    async def send_name(self, name):
        def _type_sync():
            if name and name.strip():
                type_on_keyboard(name.strip())
            else:
                send_command("START")
                time.sleep(0.5)
        await asyncio.to_thread(_type_sync)

    async def save_state(self):
        def _save_sync():
            print("Save:Starting in-game save...")

            state = read_state()
            mode = state.get("mode", "") if state else ""
            print(f"Save:Current mode: {mode}")

            if mode == "battle":
                print("Save:In battle — skipping in-game save")
                self._save_persistent_data()
                return

            if mode == "menu":
                s = state
                items, sel = get_menu_items(s)
                upper_check = [it.strip().upper() for it in items]
                if "SAVE" in upper_check:
                    print("Save:Already in START menu — navigating to SAVE directly")
                else:
                    print("Save:In non-START menu — pressing B to exit...")
                    for _ in range(8):
                        send_command("CANCEL")
                        time.sleep(0.3)
                        state = read_state()
                        if state and state.get("mode") == "overworld":
                            break
                    s = None

            elif mode == "dialogue":
                print("Save:In dialogue — pressing B/A to exit...")
                for i in range(8):
                    send_command("INTERACT" if i % 2 == 0 else "CANCEL")
                    time.sleep(0.3)
                    state = read_state()
                    if state and state.get("mode") == "overworld":
                        break
                s = None
            else:
                s = None

            if s is None or s.get("mode") != "menu":
                time.sleep(0.5)
                s = None
                for _retry in range(3):
                    open_start_menu()
                    time.sleep(0.5)
                    s = read_state()
                    if s and s.get("mode") == "menu":
                        break
                    send_command("CANCEL")
                    time.sleep(0.5)
                    s = None
                if not s or s.get("mode") != "menu":
                    print(f"Save:Failed to open START menu (mode: {s.get('mode') if s else 'None'})")
                    self._save_persistent_data()
                    return

            items, sel = get_menu_items(s)
            upper_items = [it.strip().upper() for it in items]
            print(f"Save:Menu items: {upper_items}, selected: {sel}")

            if "SAVE" not in upper_items:
                print("Save:Not the START menu — backing out and retrying once")
                for _ in range(8):
                    send_command("CANCEL")
                    time.sleep(0.3)
                    st = read_state()
                    if st and st.get("mode") == "overworld":
                        break
                time.sleep(0.5)
                open_start_menu()
                time.sleep(0.5)
                s = read_state()
                items, sel = get_menu_items(s) if s else ([], -1)
                upper_items = [it.strip().upper() for it in items]
                print(f"Save:Retry menu items: {upper_items}, selected: {sel}")

            if "SAVE" not in upper_items:
                print("Save:SAVE not in menu — cancelling")
                send_command("CANCEL")
                self._save_persistent_data()
                return

            save_idx = upper_items.index("SAVE")
            if sel >= 0:
                delta = save_idx - sel
                for _ in range(abs(delta)):
                    send_command("NAV:DOWN" if delta > 0 else "NAV:UP")
                    time.sleep(0.15)
            else:
                for _ in range(save_idx):
                    send_command("NAV:DOWN")
                    time.sleep(0.15)

            print(f"Save:Navigated to SAVE (index {save_idx}), selecting...")
            send_command("INTERACT")
            time.sleep(1.0)

            for i in range(10):
                send_command("INTERACT")
                time.sleep(1.0)
                s2 = read_state()
                s2_mode = s2.get("mode", "") if s2 else ""
                print(f"Save:Press A #{i+1}, mode: {s2_mode}")
                if s2_mode == "overworld":
                    print("Save:Back to overworld — save complete!")
                    break

            for _ in range(3):
                send_command("CANCEL")
                time.sleep(0.3)

            time.sleep(1.0)
            print("Save:In-game save finished.")
            self._save_persistent_data()
            self._sync_sav_to_backup()

        await asyncio.to_thread(_save_sync)

    async def quit(self):
        def _quit_sync():

            self._sync_sav_to_backup()

            if self.process:
                try:
                    self.process.terminate()
                    self.process.wait(timeout=5)
                except Exception:
                    try:
                        self.process.kill()
                    except Exception:
                        pass
                self.process = None

            for f in [CMD_FILE, TEXT_FILE]:
                for attempt in range(3):
                    try:
                        f.unlink()
                        break
                    except FileNotFoundError:
                        break
                    except PermissionError:
                        if attempt < 2:
                            time.sleep(0.25)

        await asyncio.to_thread(_quit_sync)

    async def capture_screenshot(self):
        def _capture_sync():
            return _image_to_data_uri(_capture_window_screenshot(self.vba_hwnd)) if self.vba_hwnd else None
        return await asyncio.to_thread(_capture_sync)

    async def capture_map_view(self):
        def _capture_sync():
            return _capture_with_compass(self.vba_hwnd) if self.vba_hwnd else None
        return await asyncio.to_thread(_capture_sync)

    def has_save_files(self):
        rom_sav = self._rom_sav_path()
        if rom_sav and rom_sav.exists():
            return True
        return bool(list_saves(self.game_key))

    def get_text_history(self):
        return list(TEXT_HISTORY)

    def get_accumulated_text(self):
        text = list(self.accumulated_text)
        self.accumulated_text.clear()
        return text


    def get_rom_path(self) -> str:
        return CONFIG.get("pokemon_paths", {}).get(self.game_key, "")

    def get_sav_path(self):
        rom_sav = self._rom_sav_path()
        if rom_sav and rom_sav.exists():
            return str(rom_sav)
        if self._save_dir.exists():
            for f in self._save_dir.glob("*.sav"):
                return str(f)
        return None

    def get_input_log(self) -> str:
        log_file = BRIDGE_DIR / "input_log.jsonl"
        contents = ""
        try:
            if log_file.exists():
                contents = log_file.read_text(encoding="utf-8")
        except Exception:
            pass
        if contents:
            send_command("CLEAR_INPUT_LOG")
        return contents

    def get_frame_count(self) -> int:
        try:
            state = read_state()
            if state:
                return state.get("frame_counter", 0)
        except Exception:
            pass
        return 0

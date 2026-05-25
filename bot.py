"""CS2 Match Bot — Discord бот.

Бидний логикийг (config, models, teams, veto, match, betting, debts)
Discord-ийн slash команд / embed / товч болгон холбоно.

Ажиллуулах:
    python bot.py
(.env файлд DISCORD_TOKEN заавал шаардлагатай. GUILD_ID байвал командууд
тухайн серверт шууд шинэчлэгдэнэ.)

ҮЕ 1 — бот холбогдох + чадварын үнэлгээ (/setrating, /ratings).
Дараагийн үед matchprep, бооцоо нэмэгдэнэ.
"""

import os
import sys
import io
import json
import random
from datetime import datetime, timezone, timedelta, time

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

from config import (MIN_RATING, MAX_RATING, MATCH_SIZE, MAP_POOL, TEAM_SIZE,
                    BET_AMOUNT, REMINDER_HOUR)
from models import Player, BankAccount
from match import MatchSession, Phase
from betting import BettingRound, UNPAID, SETTLED, DEBT
from debts import DebtLedger
import render

# Windows дээр кирилл үсэг консолд хэвлэхэд UTF-8 ашиглана
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")
_OWNER_RAW = os.getenv("OWNER_ID", "").strip()
OWNER_ID = int(_OWNER_RAW) if _OWNER_RAW.isdigit() else None

# JSONBin.io persistent storage. NexusHost-ийн ephemeral filesystem-аас
# restart бүрд алга болохгүй гэж энд хадгална.
JSONBIN_KEY = os.getenv("JSONBIN_KEY", "").strip()
JSONBIN_BIN_ID = os.getenv("JSONBIN_BIN_ID", "").strip()
_JSONBIN_BASE = "https://api.jsonbin.io/v3/b"

# Үргэлж bot.py-н хавтсанд JSON файлуудыг хадгална
# (cwd өөр газар байсан ч зөв хавтсыг ашиглана). JSONBin байхгүй
# тохиолдолд fallback-р хэрэглэнэ.
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
print(f"[INIT] bot.py хавтас = {_BASE_DIR}")
print(f"[INIT] cwd = {os.getcwd()}")
print(f"[INIT] JSONBin key: {'байгаа' if JSONBIN_KEY else 'байхгүй'} "
      f"| bin_id: {JSONBIN_BIN_ID or '(шинээр үүсгэнэ)'}")

intents = discord.Intents.default()
# Members intent шаардлагатай: guild.get_member(uid) ажиллахын тулд.
# Discord developer portal → Bot → "Server Members Intent" toggle ON хийнэ үү.
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)


def _is_admin(interaction):
    """Bot owner эсвэл server administrator/manage_guild эрхтэй эсэхийг шалгана.

    OWNER_ID env var-аар тохирсон bot эзэн нь ямар ч server-т админ эрхтэй.
    """
    if OWNER_ID is not None and interaction.user.id == OWNER_ID:
        return True
    perms = getattr(interaction.user, "guild_permissions", None)
    return perms is not None and (perms.administrator or perms.manage_guild)

# --- Чадварын үнэлгээ (ratings.json-д байнга хадгалагдана) ---
# {guild_id: {member_id: rating}}
_ratings = {}
_RATINGS_FILE = os.path.join(_BASE_DIR, "ratings.json")


def guild_ratings(guild_id):
    return _ratings.setdefault(guild_id, {})


def load_ratings():
    """ratings.json байвал үнэлгээг уншиж _ratings-д ачаална."""
    if not os.path.exists(_RATINGS_FILE):
        return
    try:
        with open(_RATINGS_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        _ratings.clear()
        for gid, members in raw.items():
            _ratings[int(gid)] = {int(m): rt for m, rt in members.items()}
    except Exception as e:
        print(f"[АНХААР] ratings.json уншиж чадсангүй: {e}")


def save_ratings():
    """_ratings-ийг ratings.json-д + JSONBin-руу бичнэ."""
    try:
        with open(_RATINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(_ratings, f, ensure_ascii=False, indent=2)
        print(f"[SAVE] ratings → {_RATINGS_FILE} "
              f"({sum(len(v) for v in _ratings.values())} entry)")
    except Exception as e:
        print(f"[АНХААР] ratings.json хадгалж чадсангүй: {e}")
    schedule_save()


load_ratings()


# --- Банкны данс (banks.json-д хадгалагдана) ---
# {member_id: BankAccount}
_banks = {}
_BANKS_FILE = os.path.join(_BASE_DIR, "banks.json")


def load_banks():
    """banks.json байвал бүртгэлтэй дансуудыг ачаална."""
    if not os.path.exists(_BANKS_FILE):
        return
    try:
        with open(_BANKS_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        _banks.clear()
        for mid, b in raw.items():
            _banks[int(mid)] = BankAccount(b["bank"], b["number"], b["holder"])
    except Exception as e:
        print(f"[АНХААР] banks.json уншиж чадсангүй: {e}")


def save_banks():
    """_banks-ийг banks.json-д + JSONBin-руу бичнэ."""
    try:
        data = {str(mid): {"bank": b.bank, "number": b.number,
                           "holder": b.holder}
                for mid, b in _banks.items()}
        with open(_BANKS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"[SAVE] banks → {_BANKS_FILE} ({len(_banks)} entry)")
    except Exception as e:
        print(f"[АНХААР] banks.json хадгалж чадсангүй: {e}")
    schedule_save()


# --- Өрийн дэвтэр (debts.json-д хадгалагдана) ---
# {guild_id: DebtLedger}
_ledgers = {}
_DEBTS_FILE = os.path.join(_BASE_DIR, "debts.json")


def guild_ledger(guild_id):
    return _ledgers.setdefault(guild_id, DebtLedger())


def load_debts():
    """debts.json байвал өрийн дэвтрийг ачаална."""
    if not os.path.exists(_DEBTS_FILE):
        return
    try:
        with open(_DEBTS_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        _ledgers.clear()
        for gid, items in raw.items():
            led = DebtLedger()
            for it in items:
                d = led.add(Player(it["debtor_id"], it["debtor_name"]),
                            Player(it["creditor_id"], it["creditor_name"]),
                            it["amount"], it.get("origin", ""),
                            it.get("created", ""))
                d.settled = it.get("settled", False)
            _ledgers[int(gid)] = led
    except Exception as e:
        print(f"[АНХААР] debts.json уншиж чадсангүй: {e}")


def save_debts():
    """Бүх guild-ийн өрийн дэвтрийг debts.json-д бичнэ."""
    try:
        data = {}
        for gid, led in _ledgers.items():
            data[str(gid)] = [
                {"debtor_id": d.debtor.id, "debtor_name": d.debtor.name,
                 "creditor_id": d.creditor.id,
                 "creditor_name": d.creditor.name,
                 "amount": d.amount, "origin": d.origin,
                 "created": d.created, "settled": d.settled}
                for d in led.debts]
        with open(_DEBTS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[АНХААР] debts.json хадгалж чадсангүй: {e}")
    schedule_save()


# --- Сануулгын суваг (channels.json-д хадгалагдана) ---
# {guild_id: channel_id} — өдрийн өр сануулга илгээх суваг
_reminder_channels = {}
_CHANNELS_FILE = os.path.join(_BASE_DIR, "channels.json")


def load_channels():
    """channels.json байвал сануулгын сувгуудыг ачаална."""
    if not os.path.exists(_CHANNELS_FILE):
        return
    try:
        with open(_CHANNELS_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        _reminder_channels.clear()
        for gid, cid in raw.items():
            _reminder_channels[int(gid)] = int(cid)
    except Exception as e:
        print(f"[АНХААР] channels.json уншиж чадсангүй: {e}")


def save_channels():
    """_reminder_channels-ийг channels.json-д бичнэ."""
    try:
        data = {str(g): c for g, c in _reminder_channels.items()}
        with open(_CHANNELS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[АНХААР] channels.json хадгалж чадсангүй: {e}")
    schedule_save()


load_banks()
load_debts()
load_channels()


# ==================== JSONBin persistent storage ====================

def _player_to_dict(p):
    return {"id": p.id, "name": p.name, "rating": p.rating}


def _team_to_dict(t):
    return {"name": t.name, "players": [_player_to_dict(p) for p in t.players]}


def _player_from_dict(d):
    return Player(id=d["id"], name=d["name"], rating=d.get("rating", 0.0))


def _bet_round_to_dict(rnd):
    """BettingRound serialize."""
    from models import Team
    return {
        "team1": _team_to_dict(rnd.team1),
        "team2": _team_to_dict(rnd.team2),
        "amount": rnd.amount,
        "winner_team": rnd.winner_team,
        "bets": [{
            "player_a": _player_to_dict(b.player_a),
            "player_b": _player_to_dict(b.player_b),
            "amount": b.amount,
            "status": b.status,
            "winner_id": b.winner.id if b.winner else None,
            "loser_id": b.loser.id if b.loser else None,
        } for b in rnd.bets],
    }


def _bet_round_from_dict(d):
    """BettingRound deserialize."""
    from models import Team
    from betting import Bet, BettingRound
    t1 = Team(name=d["team1"]["name"],
              players=[_player_from_dict(p) for p in d["team1"]["players"]])
    t2 = Team(name=d["team2"]["name"],
              players=[_player_from_dict(p) for p in d["team2"]["players"]])
    rnd = BettingRound.__new__(BettingRound)
    rnd.team1 = t1
    rnd.team2 = t2
    rnd.amount = d["amount"]
    rnd.winner_team = d.get("winner_team")
    rnd.bets = []
    # Players-уудыг id-р хайхаас зориулсан lookup
    by_id = {p.id: p for p in t1.players + t2.players}
    for bd in d["bets"]:
        bet = Bet.__new__(Bet)
        bet.player_a = _player_from_dict(bd["player_a"])
        bet.player_b = _player_from_dict(bd["player_b"])
        bet.amount = bd["amount"]
        bet.status = bd["status"]
        bet.winner = by_id.get(bd["winner_id"]) if bd.get("winner_id") else None
        bet.loser = by_id.get(bd["loser_id"]) if bd.get("loser_id") else None
        rnd.bets.append(bet)
    return rnd


def _state_snapshot():
    """In-memory state-аас JSONBin-руу хадгалах snapshot үүсгэх."""
    return {
        "ratings": {
            str(g): {str(m): r for m, r in mems.items()}
            for g, mems in _ratings.items()
        },
        "banks": {
            str(m): {"bank": b.bank, "number": b.number, "holder": b.holder}
            for m, b in _banks.items()
        },
        "debts": {
            str(g): [{
                "debtor_id": d.debtor.id,
                "debtor_name": d.debtor.name,
                "creditor_id": d.creditor.id,
                "creditor_name": d.creditor.name,
                "amount": d.amount,
                "origin": d.origin,
                "created": d.created,
                "settled": d.settled,
            } for d in led.debts]
            for g, led in _ledgers.items()
        },
        "channels": {str(g): c for g, c in _reminder_channels.items()},
        "betting": {
            str(g): _bet_round_to_dict(rnd)
            for g, rnd in _betting.items() if rnd is not None
        },
        "bet_boards": {
            str(g): {"channel_id": msg.channel.id, "message_id": msg.id}
            for g, msg in _bet_boards.items() if msg is not None
        },
        "match_times": {
            str(g): dt.isoformat() for g, dt in _match_times.items()
        },
    }


def _restore_from_snapshot(data):
    """JSONBin snapshot-аас in-memory state-руу буцаан унших."""
    _ratings.clear()
    _banks.clear()
    _ledgers.clear()
    _reminder_channels.clear()
    _betting.clear()
    # _bet_boards-ыг хадгалалтын мэдээлэл болж raw byte storage-руу
    _bet_boards_meta.clear()
    _match_times.clear()
    for gid, mems in (data.get("ratings") or {}).items():
        _ratings[int(gid)] = {int(m): float(r) for m, r in mems.items()}
    for mid, b in (data.get("banks") or {}).items():
        _banks[int(mid)] = BankAccount(b["bank"], b["number"], b["holder"])
    for gid, items in (data.get("debts") or {}).items():
        led = DebtLedger()
        for it in items:
            d = led.add(Player(it["debtor_id"], it["debtor_name"]),
                        Player(it["creditor_id"], it["creditor_name"]),
                        it["amount"], it.get("origin", ""),
                        it.get("created", ""))
            d.settled = it.get("settled", False)
        _ledgers[int(gid)] = led
    for gid, cid in (data.get("channels") or {}).items():
        _reminder_channels[int(gid)] = int(cid)
    for gid, bd in (data.get("betting") or {}).items():
        try:
            _betting[int(gid)] = _bet_round_from_dict(bd)
        except Exception as e:
            print(f"[АНХААР] betting restore failed (gid={gid}): {e}")
    for gid, bm in (data.get("bet_boards") or {}).items():
        _bet_boards_meta[int(gid)] = (int(bm["channel_id"]),
                                       int(bm["message_id"]))
    for gid, ts in (data.get("match_times") or {}).items():
        try:
            _match_times[int(gid)] = datetime.fromisoformat(ts)
        except Exception:
            pass


async def jsonbin_load():
    """JSONBin-аас state-ыг ачаалж in-memory dict-руу restore хийнэ."""
    global JSONBIN_BIN_ID
    if not JSONBIN_KEY:
        print("[JSONBIN] KEY байхгүй — file fallback ашиглана.")
        return False
    import aiohttp
    headers = {"X-Master-Key": JSONBIN_KEY}
    async with aiohttp.ClientSession() as session:
        if JSONBIN_BIN_ID:
            url = f"{_JSONBIN_BASE}/{JSONBIN_BIN_ID}/latest"
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    payload = await resp.json()
                    _restore_from_snapshot(payload.get("record", {}))
                    print(f"[JSONBIN] LOAD ✓ bin={JSONBIN_BIN_ID} "
                          f"ratings={sum(len(v) for v in _ratings.values())} "
                          f"banks={len(_banks)} "
                          f"debts={sum(len(l.debts) for l in _ledgers.values())} "
                          f"channels={len(_reminder_channels)}")
                    return True
                print(f"[JSONBIN] LOAD ✗ HTTP {resp.status}")
                return False
        # Bin id байхгүй → шинэ bin үүсгэнэ
        initial = {"ratings": {}, "banks": {}, "debts": {}, "channels": {}}
        post_headers = {**headers, "X-Bin-Private": "true",
                        "X-Bin-Name": "cs2-bot-state",
                        "Content-Type": "application/json"}
        async with session.post(_JSONBIN_BASE, json=initial,
                                 headers=post_headers) as resp:
            if resp.status in (200, 201):
                payload = await resp.json()
                new_id = payload.get("metadata", {}).get("id")
                if new_id:
                    JSONBIN_BIN_ID = new_id
                    print(f"[JSONBIN] CREATE ✓ Шинэ bin үүссэн: {new_id}")
                    print(f"[JSONBIN] ⚠️ NexusHost-н env-руу нэмнэ үү:")
                    print(f"[JSONBIN] JSONBIN_BIN_ID={new_id}")
                    return True
            print(f"[JSONBIN] CREATE ✗ HTTP {resp.status}")
            return False


async def jsonbin_save():
    """In-memory state-ыг JSONBin-руу async хадгалах.

    JSONBin sometimes 5xx алдаа буцаадаг — retry 3 удаа exponential backoff-р
    (0.5s, 1s, 2s). Дашгай зөвхөн server side алдаа (5xx) дээр retry хийнэ.
    """
    if not JSONBIN_KEY or not JSONBIN_BIN_ID:
        return
    import aiohttp
    import asyncio
    headers = {"X-Master-Key": JSONBIN_KEY,
               "Content-Type": "application/json"}
    url = f"{_JSONBIN_BASE}/{JSONBIN_BIN_ID}"
    snapshot = _state_snapshot()
    for attempt in range(3):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.put(url, json=snapshot,
                                        headers=headers,
                                        timeout=aiohttp.ClientTimeout(total=15)
                                        ) as resp:
                    if resp.status == 200:
                        n_ratings = sum(len(v) for v in _ratings.values())
                        print(f"[JSONBIN] SAVE ✓ ratings={n_ratings} "
                              f"banks={len(_banks)}"
                              + (f" (try {attempt+1})" if attempt else ""))
                        return
                    if resp.status < 500:
                        print(f"[JSONBIN] SAVE ✗ HTTP {resp.status} "
                              f"(no retry)")
                        return
                    print(f"[JSONBIN] SAVE ✗ HTTP {resp.status} "
                          f"(try {attempt+1}/3)")
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            print(f"[JSONBIN] SAVE алдаа: {e} (try {attempt+1}/3)")
        if attempt < 2:
            await asyncio.sleep(0.5 * (2 ** attempt))
    print(f"[JSONBIN] SAVE ✗ 3 try бүгд амжилтгүй — local file-р fallback")


def schedule_save():
    """Sync save_*() функцээс async jsonbin_save()-ыг fire-and-forget дуудна."""
    if JSONBIN_KEY and JSONBIN_BIN_ID:
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(jsonbin_save())
        except RuntimeError:
            pass


async def _reattach_views():
    """Restart-аас сэргэхэд хуучин Discord message-уудад view-уудыг шинээр
    attach хийнэ. Тэгэхэд button-ууд "This interaction failed" биш —
    шинэ Python процессын callback-руу route хийгдэнэ.
    """
    # Бэтинг самбар — _bet_boards_meta-аас (channel_id, message_id) уншиж
    # message-ыг fetch + view edit. Амжилттай бол _bet_boards-руу хадгална.
    n_bet = 0
    for gid, (ch_id, msg_id) in list(_bet_boards_meta.items()):
        try:
            channel = bot.get_channel(ch_id) or await bot.fetch_channel(ch_id)
            msg = await channel.fetch_message(msg_id)
            view = BettingView(gid)
            await msg.edit(view=view)
            _bet_boards[gid] = msg
            n_bet += 1
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
            print(f"[REATTACH] betting gid={gid} skip: {e}")
            _bet_boards_meta.pop(gid, None)
    print(f"[REATTACH] betting views ✓ {n_bet} board(s)")


@bot.event
async def on_ready():
    """Бот холбогдоход slash командуудыг бүртгэнэ.

    GUILD_ID байвал тухайн серверт ШУУД sync хийнэ (хормын дотор гарна).
    Хэрэв байхгүй бол л global sync ашиглана (бүх серверт 1 цаг хүлээнэ).
    Дуплицат entry үүсэхээс зайлсхийхийн тулд хоёуланг нь зэрэг хийхгүй.
    """
    # JSONBin-ээс state-ийг restart-аас дараа дахин ачаалах
    try:
        await jsonbin_load()
    except Exception as e:
        print(f"[JSONBIN] LOAD алдаа: {e}")
    # Бэтинг + өрийн дэвтэр view-уудыг хуучин message-уудад re-attach хийх
    try:
        await _reattach_views()
    except Exception as e:
        print(f"[REATTACH] алдаа: {e}")
    try:
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            # bot.tree-руу аль хэдийн ямар commands байгааг шалгаж файлд бичих
            tree_cmds = sorted(c.name for c in bot.tree.get_commands())
            with open(os.path.join(_BASE_DIR, "_debug_sync.txt"),
                      "w", encoding="utf-8") as f:
                f.write(f"PID: {os.getpid()}\n")
                f.write(f"GUILD_ID: {GUILD_ID}\n")
                f.write(f"tree_global_commands: {len(tree_cmds)}\n")
                f.write(f"tree_names: {tree_cmds}\n\n")
            # 1) Эхлээд бүх global командыг GUILD scope-руу хуулна
            bot.tree.copy_global_to(guild=guild)
            # 2) Дараа нь GLOBAL scope-ыг tree-ээс цэвэрлэнэ
            #    (хуулсан guild copies хэвээр үлдэнэ)
            bot.tree.clear_commands(guild=None)
            # 3) Global scope-руу хоосон push — Discord-ын global registry
            #    цэвэрлэгдэж, давхар команд харагдахгүй болно
            global_cleared = await bot.tree.sync()
            print(f"[OK] global cleared — {len(global_cleared)} команд үлдсэн")
            # 4) Guild scope-руу бүх команд (13 ширхэг) push
            guild_synced = await bot.tree.sync(guild=guild)
            cmd_names = sorted(c.name for c in guild_synced)
            print(f"[OK] guild sync — {len(guild_synced)} команд "
                  f"(server: {GUILD_ID})")
            print(f"[OK] commands: {', '.join(cmd_names)}")
            print(f"[OK] {bot.user} онлайн боллоо.")
            # Debug log файлд бичих — NexusHost Files tab-аас хянана
            with open(os.path.join(_BASE_DIR, "_debug_sync.txt"),
                      "a", encoding="utf-8") as f:
                f.write(f"global_cleared: {len(global_cleared)}\n")
                f.write(f"synced count: {len(guild_synced)}\n")
                f.write(f"synced names: {cmd_names}\n")
        else:
            global_synced = await bot.tree.sync()
            print(f"[OK] {bot.user} онлайн боллоо — "
                  f"{len(global_synced)} команд бэлэн (global).")
    except Exception as e:
        print(f"[АЛДАА] Командыг sync хийж чадсангүй: {e}")
    if not debt_reminder.is_running():
        debt_reminder.start()


@bot.tree.command(name="setrating",
                  description="Тоглогчид чадварын үнэлгээ (од) өгөх (admin/owner)")
@app_commands.describe(member="Үнэлгээ өгөх гишүүн",
                       stars="Одны үнэлгээ (0.1 – 5.0, жишээ: 1.1, 2.7, 4.8)")
async def setrating(interaction: discord.Interaction,
                    member: discord.Member,
                    stars: app_commands.Range[float, 0.1, 5.0]):
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "Энэ командыг серверт ашиглана уу.", ephemeral=True)
        return
    if not _is_admin(interaction):
        await interaction.response.send_message(
            "Зөвхөн server admin эсвэл bot owner үнэлгээ тавина.",
            ephemeral=True)
        return
    # 0.1 нарийвчлалд хязгаарлах (Discord 1.234... оруулсан тохиолдолд)
    rating_value = round(stars * 10) / 10
    guild_ratings(interaction.guild_id)[member.id] = rating_value
    save_ratings()
    # Идэвхтэй бүртгэлд тухайн хүн байвал үнэлгээг нь шинэчилж самбарыг сэргээнэ
    session = _sessions.get(interaction.guild_id)
    if session is not None:
        for p in session.all_joined:
            if p.id == member.id:
                p.rating = rating_value
        await _edit_board(_boards.get(interaction.guild_id),
                          interaction.guild_id)
    await interaction.response.send_message(
        f"✅ {member.mention} — **{rating_value}★** үнэлгээ авлаа.")


@bot.tree.command(name="removerating",
                  description="[Admin/Owner] Тоглогчийн үнэлгээг устгах")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(member="Үнэлгээг нь устгах гишүүн")
async def removerating(interaction: discord.Interaction,
                       member: discord.Member):
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "Энэ командыг серверт ашиглана уу.", ephemeral=True)
        return
    if not _is_admin(interaction):
        await interaction.response.send_message(
            "Зөвхөн server admin эсвэл bot owner устгана.",
            ephemeral=True)
        return
    gr = guild_ratings(interaction.guild_id)
    if member.id not in gr:
        await interaction.response.send_message(
            f"{member.mention}-д бүртгэлтэй үнэлгээ алга байна.",
            ephemeral=True)
        return
    old = gr.pop(member.id)
    save_ratings()
    # Идэвхтэй сесст тухайн хүн байвал rating-ийг тэглэж самбар сэргээнэ
    session = _sessions.get(interaction.guild_id)
    if session is not None:
        for p in session.all_joined:
            if p.id == member.id:
                p.rating = 0.0
        await _edit_board(_boards.get(interaction.guild_id),
                          interaction.guild_id)
    await interaction.response.send_message(
        f"🗑️ {member.mention} ({old}★) — үнэлгээ устгагдлаа.")


@bot.tree.command(name="ratings",
                  description="Бүх гишүүний чадварын үнэлгээг харах")
async def ratings_cmd(interaction: discord.Interaction):
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "Энэ командыг серверт ашиглана уу.", ephemeral=True)
        return
    gr = guild_ratings(interaction.guild_id)
    if not gr:
        await interaction.response.send_message(
            "Одоогоор үнэлгээ өгөгдөөгүй байна. `/setrating`-ээр эхэлнэ үү.")
        return
    lines = [f"`{i:>2}`  ▸  <@{mid}>  `{rt}★`"
             for i, (mid, rt) in enumerate(
                 sorted(gr.items(), key=lambda x: -x[1]), 1)]
    embed = discord.Embed(
        title="⭐  ЧАДВАРЫН ҮНЭЛГЭЭ",
        description="\n".join(lines),
        color=FACEIT_ORANGE,
    )
    embed.set_author(name="⚡  CS2 · 5v5")
    embed.set_footer(text=f"Нийт {len(gr)} тоглогч")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="savedata",
                  description="[OWNER] Бүх data-г JSONBin-руу шууд хадгалах")
@app_commands.default_permissions(administrator=True)
async def savedata_cmd(interaction: discord.Interaction):
    """In-memory state-ыг JSONBin-руу шууд push хийнэ. Retry-тэй учраас
    5xx алдаа гарвал 3 удаа дахин оролдоно. Хэрэглэгч бөглөсний дараа
    manual түрхэхэд ашиглана.
    """
    if OWNER_ID is None or interaction.user.id != OWNER_ID:
        await interaction.response.send_message(
            "Энэ команд нь зөвхөн bot-ын эзэнд зориулсан.",
            ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    n_ratings = sum(len(v) for v in _ratings.values())
    n_banks = len(_banks)
    n_debts = sum(len(l.debts) for l in _ledgers.values())
    n_channels = len(_reminder_channels)
    n_betting = len(_betting)
    if not JSONBIN_KEY or not JSONBIN_BIN_ID:
        await interaction.followup.send(
            "❌ JSONBin тохиргоо алга — env-д KEY/BIN_ID нэмнэ үү.",
            ephemeral=True)
        return
    try:
        await jsonbin_save()
        await interaction.followup.send(
            f"💾 **JSONBin-руу хадгалагдлаа** (retry 3x):\n"
            f"• ratings: **{n_ratings}**\n"
            f"• banks: **{n_banks}**\n"
            f"• debts: **{n_debts}**\n"
            f"• channels: **{n_channels}**\n"
            f"• betting: **{n_betting}**\n"
            f"\n_Console дотор `[JSONBIN] SAVE ✓` лог гарсан эсэхийг "
            f"шалгана уу._",
            ephemeral=True)
    except Exception as e:
        await interaction.followup.send(
            f"⚠️ Хадгалах оролдлогод алдаа гарлаа: `{e}`",
            ephemeral=True)


@bot.tree.command(name="cleardata",
                  description="[OWNER] Бүх rating/bank/debt/channel data-г устгах")
@app_commands.default_permissions(administrator=True)
async def cleardata_cmd(interaction: discord.Interaction):
    """Bot-ийн in-memory state бүгдийг цэвэрлэж, json файлуудыг устгана.

    Зөвхөн bot owner ашиглана. Бусад server-н data-д нөлөөлөхгүй —
    бүх ledger/rating/bank/session устгана.
    """
    if OWNER_ID is None or interaction.user.id != OWNER_ID:
        await interaction.response.send_message(
            "Энэ команд нь зөвхөн bot-ын эзэнд зориулсан.",
            ephemeral=True)
        return
    # In-memory state
    _ratings.clear()
    _banks.clear()
    _ledgers.clear()
    _reminder_channels.clear()
    _sessions.clear()
    _boards.clear()
    _betting.clear()
    _bet_boards.clear()
    _match_times.clear()
    # Disk
    removed = []
    for fp in (_RATINGS_FILE, _BANKS_FILE, _DEBTS_FILE, _CHANNELS_FILE):
        if os.path.exists(fp):
            try:
                os.remove(fp)
                removed.append(fp)
            except OSError:
                pass
    await interaction.response.send_message(
        f"🧹 Бүх data цэвэрлэгдлээ.\n"
        f"Устгасан файлууд: `{', '.join(removed) or 'алга'}`\n"
        f"In-memory state бас цэвэрхэн.",
        ephemeral=True)


# Restart-аас алдсан data-г screenshot-аас восстанавит хийх seed.
# /restore owner-only командын дуудалтаар display_name match-р оноогдоно.
_SEED_RATINGS = {
    # 2026-05-25 өмнөх /ratings screenshot-аас (31 тоглогч)
    "Arigun4LiFE": 5.0,
    "🅱🅴🅰🆂🆃🆉™": 4.8,
    "Manticore Narka": 4.8,
    "🅸🅽🅰love you": 4.8,
    "itachi_uchiha8579": 4.7,
    "Tushka": 4.5,
    "Insaniti": 4.5,
    "♤《Hero》♤": 4.3,
    "nam-ra": 4.2,
    "cuT-": 4.0,
    "-Yea ji-": 4.0,
    "d0ucha.": 4.0,
    "daidoo": 3.8,
    "Tulguur": 3.6,
    "#1 DALAI": 3.6,
    "Bachka": 3.5,
    "FroSty": 3.0,
    "MxH.": 3.0,
    "Balt12": 2.5,
    "sanchir": 2.5,
    "🅼.🅾🅲🅴🅰🅽": 2.3,
    "Wade": 2.0,
    "Yoko": 2.0,
    "Geo": 2.0,
    "Hisako": 1.5,
    "Conqu": 1.5,
    "Khobun": 1.5,
    "Monzu": 1.5,
    "Enkhdulz": 1.5,
    "mambosantii": 1.0,
}

_SEED_BANKS = {
    # 2026-05-25 өмнөх /banks screenshot-аас (11 ширхэг)
    # display_name → (bank, number, holder)
    "FroSty": ("GOLOMT", "190015002209318015", "FroSty"),
    "Arigun4LiFE": ("Khan bank", "5020847253", "Arigun4LiFE"),
    "Bachka": ("khaan", "150005005626267844", "Bachka"),
    "Enkhdulz": ("khaan bank", "5040367851", "enkhdul"),
    "♤《Hero》♤": ("khanbank", "5070363782", "Hero4k"),
    "Hisako": ("khan", "5628128070", "hisako"),
    "nam-ra": ("Khan bank", "450005005017566017", "Munkhsuld Delgersaikhan"),
    "Insaniti": ("khanbank", "230005005114205379", "tsolmon myagmarsuren"),
    "Tulguur": ("Khan bank", "5015285982", "Tulguur"),
    "Yoko": ("TDB", "416004206", "Yoko"),
    "#1 DALAI": ("Худалдаа хөгжил", "840004000499281891", "Далай"),
}


@bot.tree.command(name="restore",
                  description="[OWNER] Screenshot-аас алдсан rating/bank data-г сэргээх")
@app_commands.default_permissions(administrator=True)
async def restore_cmd(interaction: discord.Interaction):
    """Display name match-р алдсан data-г сэргээнэ. Owner only."""
    if OWNER_ID is None or interaction.user.id != OWNER_ID:
        await interaction.response.send_message(
            "Энэ команд нь зөвхөн bot-ын эзэнд зориулсан.",
            ephemeral=True)
        return
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "Энэ командыг серверт ашиглана уу.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    gr = guild_ratings(interaction.guild_id)
    # Бүх member-уудыг fetch — Members Intent ажиллахгүй ч fetch chunk хийнэ.
    try:
        members = [m async for m in guild.fetch_members(limit=None)]
    except discord.HTTPException:
        members = list(guild.members)
    # Display name ба global name хоёулангаар нь дахин дахин шалгана.
    name_to_member = {}
    for m in members:
        name_to_member.setdefault(m.display_name, m)
        if m.name and m.name not in name_to_member:
            name_to_member[m.name] = m
        if m.global_name and m.global_name not in name_to_member:
            name_to_member[m.global_name] = m
    matched_ratings = 0
    unmatched_ratings = []
    for name, rating in _SEED_RATINGS.items():
        member = name_to_member.get(name)
        if member is None:
            unmatched_ratings.append(name)
            continue
        gr[member.id] = rating
        matched_ratings += 1
    matched_banks = 0
    unmatched_banks = []
    for name, (bank, number, holder) in _SEED_BANKS.items():
        member = name_to_member.get(name)
        if member is None:
            unmatched_banks.append(name)
            continue
        _banks[member.id] = BankAccount(bank=bank, number=number, holder=holder)
        matched_banks += 1
    save_ratings()
    save_banks()
    msg = (f"✅ Сэргээлт дууслаа\n"
           f"• Ratings: **{matched_ratings}/{len(_SEED_RATINGS)}** match\n"
           f"• Banks: **{matched_banks}/{len(_SEED_BANKS)}** match\n")
    if unmatched_ratings:
        msg += f"\n❓ Ratings олдсонгүй ({len(unmatched_ratings)}): " \
               f"`{', '.join(unmatched_ratings)}`"
    if unmatched_banks:
        msg += f"\n❓ Banks олдсонгүй: `{', '.join(unmatched_banks)}`"
    await interaction.followup.send(msg, ephemeral=True)


@bot.tree.command(name="banks",
                  description="Энэ серверийн гишүүдийн бүртгэлтэй дансыг харах")
@app_commands.default_permissions(use_application_commands=True)
async def banks_cmd(interaction: discord.Interaction):
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "Энэ командыг серверт ашиглана уу.", ephemeral=True)
        return
    # _banks нь user-аар хадгалагдсан global dict. fetch_member-ийн silent
    # fail-ээс зайлсхийхийн тулд filter-гүйгээр бүгдийг харуулна — Discord
    # client нь <@uid> mention-ийг автомат resolve хийдэг.
    rows = [(uid, ba) for uid, ba in _banks.items() if uid >= 1_000_000]
    if not rows:
        await interaction.response.send_message(
            "Энэ серверт бүртгэлтэй банкны данс алга. `/setbank`-аар эхэлнэ үү.")
        return
    # Holder нэрээр эрэмбэлж (uid нь int, member object байхгүй)
    rows.sort(key=lambda r: r[1].holder.lower())
    lines = []
    for i, (uid, ba) in enumerate(rows, 1):
        # `9796686318` хэлбэрээр copy боломжтой mono код
        lines.append(f"`{i:>2}`  ▸  <@{uid}>  ·  **{ba.bank}**  ·  "
                     f"`{ba.number}`  ·  {ba.holder}")
    embed = discord.Embed(
        title="🏦  БАНКНЫ ДАНСНЫ ДЭВТЭР",
        description="\n".join(lines),
        color=FACEIT_ORANGE,
    )
    embed.set_author(name="⚡  CS2 · 5v5")
    embed.set_footer(text=f"Нийт {len(rows)} гишүүн · бооцооны төлбөрт ашиглана")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="setbank",
                  description="Банкны дансаа бүртгэх (admin/owner л бусдыг таглана)")
@app_commands.default_permissions(use_application_commands=True)
@app_commands.describe(bank="Банкны нэр (ж: Хаан банк)",
                       number="Дансны дугаар",
                       holder="Данс эзэмшигчийн нэр",
                       member="(admin/owner л) Бусдын дансыг бүртгэх гишүүн")
async def setbank(interaction: discord.Interaction,
                  bank: str, number: str, holder: str,
                  member: discord.Member = None):
    # Owner + admin: @member оруулаад бусдын дансыг бүртгэж болно.
    # Энгийн user: зөвхөн өөрийнхөө дансыг (member үл оруулна, эсвэл өөрийгөө).
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "Энэ командыг серверт ашиглана уу.", ephemeral=True)
        return
    target = interaction.user
    if member is not None and member.id != interaction.user.id:
        if not _is_admin(interaction):
            await interaction.response.send_message(
                "Зөвхөн server admin эсвэл bot owner бусдын дансыг бүртгэнэ. "
                "Та өөрийнхөө дансыг `@member` оруулахгүйгээр бүртгээрэй.",
                ephemeral=True)
            return
        target = member
    _banks[target.id] = BankAccount(bank=bank, number=number, holder=holder)
    save_banks()
    whose = ("Таны" if target.id == interaction.user.id
             else f"{target.display_name}-ны")
    await interaction.response.send_message(
        f"✅ {whose} данс бүртгэгдлээ:\n**{_banks[target.id]}**\n"
        "Бооцооны тооцоо хаах үед хожигдогчид энэ данс харагдана.",
        ephemeral=True)


@bot.tree.command(name="setuserbank",
                  description="[Admin/Owner] Бусдын дансыг бүртгэх "
                              "(member эхэнд)")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(member="Данс бүртгүүлэх гишүүн",
                       bank="Банкны нэр (ж: Хаан банк)",
                       number="Дансны дугаар",
                       holder="Данс эзэмшигчийн нэр")
async def setuserbank(interaction: discord.Interaction,
                      member: discord.Member,
                      bank: str, number: str, holder: str):
    """Admin/owner бусдын дансыг member parameter эхэнд оруулж бүртгэх."""
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "Энэ командыг серверт ашиглана уу.", ephemeral=True)
        return
    if not _is_admin(interaction):
        await interaction.response.send_message(
            "Зөвхөн server admin эсвэл bot owner ашиглана.", ephemeral=True)
        return
    _banks[member.id] = BankAccount(bank=bank, number=number, holder=holder)
    save_banks()
    await interaction.response.send_message(
        f"✅ {member.display_name}-ны данс бүртгэгдлээ:\n"
        f"**{_banks[member.id]}**",
        ephemeral=True)


# ==================== Тоглолт бэлтгэх — бүртгэл ====================

_sessions = {}        # {guild_id: MatchSession}
_boards = {}          # {guild_id: бүртгэлийн самбарын Message}
_betting = {}         # {guild_id: BettingRound}
_bet_boards = {}      # {guild_id: бооцооны самбарын Message}
_bet_boards_meta = {} # {guild_id: (channel_id, message_id)} — JSONBin restore
_match_times = {}     # {guild_id: тоглолт нээсэн огноо (datetime)}
_MN_TZ = timezone(timedelta(hours=8))   # Монгол (Улаанбаатар) цаг
FACEIT_ORANGE = 0xFF5500   # FACEIT брэндийн улбар шар өнгө

# /devfill тест тоглогчдод санамсаргүй өгөх нэрс
_FAKE_NAMES = [
    "Bat", "Bold", "Dorj", "Naran", "Munkh", "Bayar", "Tomor", "Anar",
    "Tamir", "Otgon", "Bilguun", "Chinbat", "Temka", "Nyamka", "Sodoo",
    "Ganaa", "Khangai", "Tushig", "Erkhem", "Enkhee", "Sukhee", "Tugsuu",
    "Maral", "Bataa", "Doogii", "Khasaa", "Nyamaa", "Puje",
]


def _is_fake(player):
    """devfill-ийн хуурамч тоглогч эсэх. Хуурамч id нь 900000-аас эхэлдэг,
    жинхэнэ Discord ID нь үүнээс хэдэн саяар том тул ингэж ялгана."""
    return player.id < 1_000_000


def _who(player):
    """Тоглогчийг харуулах текст: жинхэнэ гишүүн бол mention, тест бол налуу нэр."""
    return f"*{player.name}*" if _is_fake(player) else f"<@{player.id}>"


def _player_from_member(guild_id, member):
    """Discord гишүүнээс Player үүсгэнэ (үнэлгээг сангаас авна)."""
    rating = guild_ratings(guild_id).get(member.id, 0.0)
    return Player(id=member.id, name=member.display_name, rating=rating)


def _is_captain_user(session, user_id):
    """user_id энэ session-ийн аль нэг ахлагчийнх эсэх."""
    return bool((session.captain1 and user_id == session.captain1.id)
                or (session.captain2 and user_id == session.captain2.id))


def _is_top_player(session, user_id):
    """user_id тоглох 10-ийн хамгийн өндөр үнэлгээтэй тоглогч эсэх."""
    players = session.players
    if not players:
        return False
    top = max(players, key=lambda p: p.rating)
    return user_id == top.id


def _team_name(n):
    """Багийн дугаарыг нэр болгоно: 1 -> Team A, 2 -> Team B."""
    return "Team A" if n == 1 else "Team B"


def _avg(team):
    """Багийн дундаж үнэлгээ (нэг тоглогчид)."""
    n = len(team.players)
    return round(team.total_rating / n, 1) if n else 0.0


def _squad(players, captain_ids=()):
    """Тоглогчдын баганыг FACEIT маягаар (ахлагч эхэнд, ▸ тэмдэгтэй)."""
    ordered = sorted(players,
                     key=lambda p: (p.id not in captain_ids, -p.rating))
    rows = []
    for p in ordered:
        tag = "👑" if p.id in captain_ids else "▸"
        rows.append(f"{tag} {_who(p)}  `{p.rating}★`")
    return "\n".join(rows) or "—"


def registration_embed(session):
    """Бүртгэлийн самбар — FACEIT маягийн."""
    players = session.players
    embed = discord.Embed(title="🎮  ТОГЛОЛТЫН БҮРТГЭЛ", color=FACEIT_ORANGE)
    embed.set_author(name="⚡  CS2 · 5v5 MATCH")
    if players:
        lines = []
        for i, p in enumerate(players, 1):
            star = f"`{p.rating}★`" if p.is_rated else "`үнэлгээгүй`"
            lines.append(f"`{i:>2}`  ▸  {_who(p)}  {star}")
        body = "\n".join(lines)
    else:
        body = "*Хоосон — доорх 🟢 **Нэгдэх** товчоор бүртгүүлээрэй.*"
    embed.add_field(name=f"👥  ТОГЛОГЧИД  —  {len(players)}/{MATCH_SIZE}",
                    value=body, inline=False)
    if session.reserves:
        embed.add_field(
            name=f"🪑  НӨӨЦ  —  {len(session.reserves)}",
            value="  ·  ".join(_who(p) for p in session.reserves),
            inline=False)
    if session.is_full:
        embed.add_field(
            name="​",
            value=("🟢 **10+ тоглогч бэлэн боллоо!**\n"
                   "Admin эсвэл хамгийн өндөр үнэлгээтэй тоглогч "
                   "🚀 **Эхлүүлэх** дарж баг хуваалтыг эхлүүлнэ.\n"
                   "🔄 **Тоглогч солих** — нөөцтэй тоглогч сольж болно."),
            inline=False)
    else:
        need = MATCH_SIZE - len(players)
        embed.add_field(name="​",
                        value=f"⏳  Дахин **{need}** тоглогч хэрэгтэй…",
                        inline=False)
    embed.set_footer(text=f"Төлөв: {session.phase.value}")
    return embed


class SwapSelect(discord.ui.Select):
    """Солих тоглогч сонгох цэс — out (хасах) эсвэл in (оруулах)."""

    def __init__(self, parent_view, kind, players):
        self.parent_view = parent_view
        self.kind = kind
        ph = ("⬇️ ХАСАХ тоглогчоо сонго (тоглох 10)" if kind == "out"
              else "⬆️ ОРУУЛАХ тоглогчоо сонго (нөөц)")
        options = [
            discord.SelectOption(label=p.name[:100], value=str(p.id),
                                 description=f"{p.rating} од")
            for p in players[:25]]
        super().__init__(placeholder=ph, options=options)

    async def callback(self, interaction: discord.Interaction):
        if self.kind == "out":
            self.parent_view.out_id = int(self.values[0])
        else:
            self.parent_view.in_id = int(self.values[0])
        await self.parent_view.try_swap(interaction)


class SwapView(discord.ui.View):
    """Нөөц ↔ тоглох 10 хооронд тоглогч солих (admin/ахлагч)."""

    def __init__(self, guild_id):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.out_id = None
        self.in_id = None
        session = _sessions.get(guild_id)
        if session is not None:
            self.add_item(SwapSelect(self, "out", session.players))
            self.add_item(SwapSelect(self, "in", session.reserves))

    async def try_swap(self, interaction: discord.Interaction):
        session = _sessions.get(self.guild_id)
        if session is None or session.phase != Phase.REGISTRATION:
            await interaction.response.edit_message(
                content="Бүртгэл идэвхгүй болсон.", view=None)
            return
        if self.out_id is None or self.in_id is None:
            done = "ХАСАХ" if self.out_id is not None else "ОРУУЛАХ"
            await interaction.response.edit_message(
                content=f"🔄 {done} сонгогдлоо ✅ — нөгөөг нь мөн сонгоно уу.",
                view=self)
            return
        out_p = next((p for p in session.players if p.id == self.out_id), None)
        in_p = next((p for p in session.reserves if p.id == self.in_id), None)
        if out_p is None or in_p is None:
            await interaction.response.edit_message(
                content="⚠️ Тоглогчид өөрчлөгдсөн байна. Дахин оролдоно уу.",
                view=None)
            return
        try:
            session.swap_player(out_p, in_p)
        except ValueError as e:
            await interaction.response.edit_message(content=f"⚠️ {e}",
                                                    view=None)
            return
        await interaction.response.edit_message(
            content=(f"✅ Сольлоо:  **{out_p.name}** ⬇️ нөөц   ·   "
                     f"**{in_p.name}** ⬆️ тоглоно"),
            view=None)
        await _edit_board(_boards.get(self.guild_id), self.guild_id)


class RegistrationView(discord.ui.View):
    """Бүртгэлийн самбар — Нэгдэх / Гарах / Эхлүүлэх / Тоглогч солих."""

    def __init__(self, guild_id):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        session = _sessions.get(guild_id)
        # «Эхлүүлэх» товч 10 хүн бүрдсэн үед л идэвхжинэ
        if not (session is not None and session.is_full):
            self.start_btn.disabled = True
        # «Тоглогч солих» нөөц байгаа үед л идэвхжинэ
        if not (session is not None and session.reserves):
            self.swap_btn.disabled = True

    @discord.ui.button(label="Нэгдэх", style=discord.ButtonStyle.success,
                       emoji="✅")
    async def join_btn(self, interaction: discord.Interaction, button):
        session = _sessions.get(self.guild_id)
        if session is None:
            await interaction.response.send_message(
                "Энэ бүртгэл идэвхгүй болсон.", ephemeral=True)
            return
        try:
            session.join(_player_from_member(self.guild_id, interaction.user))
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        await _refresh_and_check(interaction, self.guild_id)

    @discord.ui.button(label="Гарах", style=discord.ButtonStyle.danger,
                       emoji="❌")
    async def leave_btn(self, interaction: discord.Interaction, button):
        session = _sessions.get(self.guild_id)
        if session is None:
            await interaction.response.send_message(
                "Энэ бүртгэл идэвхгүй болсон.", ephemeral=True)
            return
        try:
            session.leave(_player_from_member(self.guild_id, interaction.user))
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        await _refresh_and_check(interaction, self.guild_id)

    @discord.ui.button(label="Эхлүүлэх", style=discord.ButtonStyle.primary,
                       emoji="🚀")
    async def start_btn(self, interaction: discord.Interaction, button):
        session = _sessions.get(self.guild_id)
        if session is None:
            await interaction.response.send_message(
                "Энэ бүртгэл идэвхгүй болсон.", ephemeral=True)
            return
        is_admin = _is_admin(interaction)
        if not (is_admin or _is_top_player(session, interaction.user.id)):
            await interaction.response.send_message(
                "Зөвхөн admin эсвэл хамгийн өндөр үнэлгээтэй тоглогч "
                "баг хуваалтыг эхлүүлнэ.", ephemeral=True)
            return
        try:
            session.begin_division()
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        await _refresh_and_check(interaction, self.guild_id)

    @discord.ui.button(label="Тоглогч солих",
                       style=discord.ButtonStyle.secondary, emoji="🔄")
    async def swap_btn(self, interaction: discord.Interaction, button):
        session = _sessions.get(self.guild_id)
        if session is None:
            await interaction.response.send_message(
                "Энэ бүртгэл идэвхгүй болсон.", ephemeral=True)
            return
        is_admin = _is_admin(interaction)
        if not (is_admin or _is_top_player(session, interaction.user.id)):
            await interaction.response.send_message(
                "Зөвхөн admin эсвэл хамгийн өндөр үнэлгээтэй тоглогч "
                "тоглогч солино.", ephemeral=True)
            return
        if not session.reserves:
            await interaction.response.send_message(
                "Нөөц алга — солих тоглогч байхгүй.", ephemeral=True)
            return
        await interaction.response.send_message(
            content=("🔄 **Тоглогч солих** — тоглох 10-аас ХАСАХ нэг, "
                     "нөөцөөс ОРУУЛАХ нэгийг сонгоно уу:"),
            view=SwapView(self.guild_id), ephemeral=True)


@bot.tree.command(name="matchprep",
                  description="Шинэ тоглолтын бүртгэл нээх (admin/owner)")
async def matchprep(interaction: discord.Interaction):
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "Энэ командыг серверт ашиглана уу.", ephemeral=True)
        return
    if not _is_admin(interaction):
        await interaction.response.send_message(
            "Зөвхөн server admin эсвэл bot owner шинэ тоглолт нээнэ.",
            ephemeral=True)
        return
    # Хуучин самбар байвал товчнуудыг идэвхгүй болгож, будлиан гаргахгүй
    old_board = _boards.get(interaction.guild_id)
    if old_board is not None:
        try:
            await old_board.edit(view=None)
        except discord.HTTPException:
            pass
    old_bet = _bet_boards.pop(interaction.guild_id, None)
    if old_bet is not None:
        try:
            await old_bet.edit(view=None)
        except discord.HTTPException:
            pass
    _betting.pop(interaction.guild_id, None)
    session = MatchSession()
    _sessions[interaction.guild_id] = session
    _match_times[interaction.guild_id] = datetime.now(_MN_TZ)
    embed, view = current_embed_and_view(interaction.guild_id)
    file = _match_file(interaction.guild_id)
    if file is not None:
        await interaction.response.send_message(file=file, view=view)
    else:
        await interaction.response.send_message(embed=embed, view=view)
    _boards[interaction.guild_id] = await interaction.original_response()


@bot.tree.command(name="remake",
                  description="BO3 дууссаны дараа дахин хуваах — players үлдэнэ "
                              "(admin/owner)")
@app_commands.default_permissions(administrator=True)
@app_commands.choices(method=[
    app_commands.Choice(name="🎯 Draft — ахлагч сонгох", value="draft"),
    app_commands.Choice(name="🎲 Random — санамсаргүй", value="random"),
    app_commands.Choice(name="✋ Manual — гар хуваалт", value="manual"),
    app_commands.Choice(name="↩️ Өмнөх багаар үргэлжлүүлэх", value="previous"),
])
@app_commands.describe(method="Хуваалтын арга (хоосон үлдээвэл ахлагчид санал хураана)")
async def remake_cmd(interaction: discord.Interaction,
                     method: app_commands.Choice[str] = None):
    """Идэвхтэй player жагсаалтыг хадгалаад баг хуваалтыг шинээр эхлүүлнэ.

    method параметрээр admin шууд аргаа сонгож, ахлагчдын санал хураалтыг
    алгасч болно. 'previous' сонгосон бол өмнөх багуудаар veto руу шилжинэ.
    """
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "Энэ командыг серверт ашиглана уу.", ephemeral=True)
        return
    if not _is_admin(interaction):
        await interaction.response.send_message(
            "Зөвхөн server admin эсвэл bot owner remake хийнэ.",
            ephemeral=True)
        return
    old_session = _sessions.get(interaction.guild_id)
    if old_session is None or not old_session.all_joined:
        await interaction.response.send_message(
            "Идэвхтэй тоглолт алга. Эхлээд `/matchprep` дуудаарай.",
            ephemeral=True)
        return
    method_val = method.value if method is not None else None
    # 'previous' — өмнөх багууд байх ёстой
    if method_val == "previous" and old_session.teams is None:
        await interaction.response.send_message(
            "Өмнөх багууд олдсонгүй. Энэ session-д баг хуваагдаагүй байна.",
            ephemeral=True)
        return
    # Хуучин самбар + бэтинг арилгах
    old_board = _boards.get(interaction.guild_id)
    if old_board is not None:
        try:
            await old_board.edit(view=None)
        except discord.HTTPException:
            pass
    old_bet = _bet_boards.pop(interaction.guild_id, None)
    if old_bet is not None:
        try:
            await old_bet.edit(view=None)
        except discord.HTTPException:
            pass
    _bet_boards_meta.pop(interaction.guild_id, None)
    _betting.pop(interaction.guild_id, None)
    # Шинэ session — өмнөх players-уудыг хадгалах
    saved_players = list(old_session.all_joined)
    saved_teams = old_session.teams  # 'previous'-д ашиглана
    session = MatchSession()
    session.all_joined = saved_players
    session._enter_division()  # DIVISION-руу шилжинэ, ахлагч сонгоно
    # method-ийн дагуу шууд тохируулна (default = None → ахлагчид санал хураана)
    if method_val == "draft":
        session._begin_draft()
    elif method_val == "random":
        session._begin_random()
    elif method_val == "manual":
        session._begin_manual()
    elif method_val == "previous":
        # Хуучин багуудыг шинэ session-руу хуулж шууд VETO руу шилжинэ
        session.division_method = "previous"
        session.teams = saved_teams
        session.confirm_division()  # phase = VETO, dice roll-той
    _sessions[interaction.guild_id] = session
    _match_times[interaction.guild_id] = datetime.now(_MN_TZ)
    embed, view = current_embed_and_view(interaction.guild_id)
    file = _match_file(interaction.guild_id)
    if file is not None:
        await interaction.response.send_message(file=file, view=view)
    else:
        await interaction.response.send_message(embed=embed, view=view)
    _boards[interaction.guild_id] = await interaction.original_response()
    schedule_save()


@bot.tree.command(name="devfill",
                  description="[ТЕСТ] Бүртгэлийг хуурамч тоглогчоор дүүргэх")
@app_commands.describe(count="Нэмэх хуурамч тоглогчийн тоо")
async def devfill(interaction: discord.Interaction, count: int = 12):
    # Зөвхөн bot owner ашиглах боломжтой (server admin ч хэрэглэх боломжгүй).
    if OWNER_ID is None or interaction.user.id != OWNER_ID:
        await interaction.response.send_message(
            "Энэ команд нь зөвхөн bot-ын эзэнд зориулсан тест команд.",
            ephemeral=True)
        return
    session = _sessions.get(interaction.guild_id)
    if session is None:
        await interaction.response.send_message(
            "Идэвхтэй бүртгэл алга. Эхлээд `/matchprep` хийнэ үү.",
            ephemeral=True)
        return
    steps = [0.5, 1, 1.5, 2, 2.5]   # бодит тоглогч ахлагч болохын тулд бага оноотой
    fake_banks = ["Хаан банк", "Голомт банк", "Худалдаа хөгжлийн банк",
                  "Төрийн банк", "Хас банк"]
    names = random.sample(_FAKE_NAMES, k=min(count, len(_FAKE_NAMES)))
    added = 0
    for i in range(count):
        name = names[i] if i < len(names) else f"Тоглогч{i + 1}"
        fake = Player(id=900000 + i, name=name,
                      rating=random.choice(steps))
        try:
            session.join(fake)
        except ValueError:
            continue
        added += 1
        # тест тоглогчдод санамсаргүй банкны данс өгнө
        _banks[fake.id] = BankAccount(
            bank=random.choice(fake_banks),
            number=str(random.randint(10 ** 9, 10 ** 10 - 1)),
            holder=fake.name)
    save_banks()
    await interaction.response.send_message(
        f"✅ {added} хуурамч тоглогч нэмэгдлээ (санамсаргүй данстай).",
        ephemeral=True)
    await _edit_board(_boards.get(interaction.guild_id),
                      interaction.guild_id)


# ==================== Тоглолт бэлтгэх — баг хуваах ====================

def division_embed(session):
    """Баг хуваахаас өмнө — ахлагчид ба аргын сонголт (FACEIT маяг)."""
    c1, c2 = session.captain1, session.captain2
    embed = discord.Embed(title="⚔️  БАГ ХУВААХ", color=FACEIT_ORANGE)
    embed.set_author(name="⚡  CS2 · 5v5 MATCH")
    embed.add_field(
        name="👑  АХЛАГЧИД",
        value=(f"🟧  **Team A** — {_who(c1)}  `{c1.rating}★`\n"
               f"🟦  **Team B** — {_who(c2)}  `{c2.rating}★`"),
        inline=False)
    embed.add_field(
        name="🧩  ХУВААХ АРГА  —  ахлагчид сонгоно",
        value=("🎯  **Draft**  — ахлагч ээлжлэн сонгоно\n"
               "🔀  **Random** — үнэлгээгээр тэнцвэртэй санамсаргүй\n"
               "✋  **Manual** — ахлагчид гараар хуваана"),
        inline=False)
    if session.method_votes:
        vt = []
        for cn, ch in session.method_votes.items():
            cap = c1 if cn == 1 else c2
            vt.append(f"{_who(cap)} → **{ch}**")
        embed.add_field(name="🗳️  ӨГСӨН САНАЛ", value="\n".join(vt),
                        inline=False)
    embed.set_footer(text="Баг хуваах")
    return embed


def teams_embed(session, title="⚔️  БАГУУД ХУВААГДЛАА"):
    """Хуваагдсан 2 багийг FACEIT маягаар харуулна."""
    t1, t2 = session.teams
    embed = discord.Embed(title=title, color=FACEIT_ORANGE)
    embed.set_author(name="⚡  CS2 · 5v5 MATCH")
    embed.add_field(name=f"🟧  TEAM A   ·   AVG {_avg(t1)}★",
                    value=_squad(t1.players, {t1.captain.id}), inline=True)
    embed.add_field(name=f"🟦  TEAM B   ·   AVG {_avg(t2)}★",
                    value=_squad(t2.players, {t2.captain.id}), inline=True)
    diff = round(abs(t1.total_rating - t2.total_rating), 2)
    embed.set_footer(text=f"Нийт {t1.total_rating}★ vs {t2.total_rating}★  "
                          f"·  зөрүү {diff}★")
    return embed


class MethodVoteView(discord.ui.View):
    """Ахлагчид хуваах аргаа сонгоно (одоохондоо Random бэлэн)."""

    def __init__(self, guild_id):
        super().__init__(timeout=None)
        self.guild_id = guild_id

    def _captain_num(self, session, user_id):
        if session.captain1 and user_id == session.captain1.id:
            return 1
        if session.captain2 and user_id == session.captain2.id:
            return 2
        return None

    def _resolve_voter(self, session, interaction):
        """(captain_num, is_admin_override) буцаана.

        Жинхэнэ ахлагч бол (1|2, False). admin/owner бөгөөд ахлагч биш
        бол (1, True) — captain 1 болж саналаа өгөөд, нөгөөг автомат
        нэгтгэнэ.
        """
        cap = self._captain_num(session, interaction.user.id)
        if cap is not None:
            return cap, False
        if _is_admin(interaction):
            return 1, True
        return None, False

    async def _vote_and_finalize(self, interaction, session, choice):
        """Сонголтыг бүртгээд, хэрэгцээтэй бол нөгөө ахлагчийг автомат
        нэгтгэнэ (admin override эсвэл fake captain тохиолдолд)."""
        cap, is_override = self._resolve_voter(session, interaction)
        if cap is None:
            await interaction.response.send_message(
                "Зөвхөн ахлагч (эсвэл admin/owner) хуваах аргыг сонгоно.",
                ephemeral=True)
            return None
        try:
            result = session.vote_method(cap, choice)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return None
        if result == "waiting":
            other_num = 2 if cap == 1 else 1
            other_cap = session.captain2 if cap == 1 else session.captain1
            # admin override: автоматаар нөгөөтэй нэгтгэнэ
            # fake captain: тест өгөгдлөөр автомат vote
            if is_override or _is_fake(other_cap):
                try:
                    session.vote_method(other_num, choice)
                except ValueError:
                    pass
        return "ok"

    @discord.ui.button(label="Draft", style=discord.ButtonStyle.secondary,
                       emoji="🎯")
    async def draft_btn(self, interaction: discord.Interaction, button):
        session = _sessions.get(self.guild_id)
        if session is None or session.phase != Phase.DIVISION:
            await interaction.response.send_message(
                "Хуваалтын шат идэвхгүй байна.", ephemeral=True)
            return
        if await self._vote_and_finalize(interaction, session, "draft") is None:
            return
        _auto_draft(session)
        await _refresh_and_check(interaction, self.guild_id)

    @discord.ui.button(label="Random", style=discord.ButtonStyle.primary,
                       emoji="🔀")
    async def random_btn(self, interaction: discord.Interaction, button):
        session = _sessions.get(self.guild_id)
        if session is None or session.phase != Phase.DIVISION:
            await interaction.response.send_message(
                "Хуваалтын шат идэвхгүй байна.", ephemeral=True)
            return
        if await self._vote_and_finalize(interaction, session, "random") is None:
            return
        await _refresh_and_check(interaction, self.guild_id)

    @discord.ui.button(label="Manual", style=discord.ButtonStyle.secondary,
                       emoji="✋")
    async def manual_btn(self, interaction: discord.Interaction, button):
        session = _sessions.get(self.guild_id)
        if session is None or session.phase != Phase.DIVISION:
            await interaction.response.send_message(
                "Хуваалтын шат идэвхгүй байна.", ephemeral=True)
            return
        if await self._vote_and_finalize(interaction, session, "manual") is None:
            return
        if session.division_method == "manual":
            _auto_manual(session)
        await _refresh_and_check(interaction, self.guild_id)


class DivisionDoneView(discord.ui.View):
    """Багууд хуваагдсаны дараа — Дахин эхлүүлэх / Баталгаажуулах."""

    def __init__(self, guild_id):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        session = _sessions.get(guild_id)
        if session is None or session.division_method != "random":
            self.remove_item(self.reroll_btn)

    def _is_captain(self, session, user_id):
        return ((session.captain1 and user_id == session.captain1.id) or
                (session.captain2 and user_id == session.captain2.id))

    @discord.ui.button(label="Дахин хуваах", style=discord.ButtonStyle.secondary,
                       emoji="🔁")
    async def reroll_btn(self, interaction: discord.Interaction, button):
        session = _sessions.get(self.guild_id)
        if session is None:
            await interaction.response.send_message("Идэвхгүй.", ephemeral=True)
            return
        if not self._is_captain(session, interaction.user.id) \
                and not _is_admin(interaction):
            await interaction.response.send_message(
                "Зөвхөн ахлагч (эсвэл admin/owner) дахин хуваалт хийнэ.",
                ephemeral=True)
            return
        try:
            session.reroll()
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        await _refresh_and_check(interaction, self.guild_id)

    @discord.ui.button(label="Дахин эхлүүлэх",
                       style=discord.ButtonStyle.secondary, emoji="🔄")
    async def restart_btn(self, interaction: discord.Interaction, button):
        session = _sessions.get(self.guild_id)
        if session is None:
            await interaction.response.send_message("Идэвхгүй.", ephemeral=True)
            return
        if not self._is_captain(session, interaction.user.id) \
                and not _is_admin(interaction):
            await interaction.response.send_message(
                "Зөвхөн ахлагч (эсвэл admin/owner) дахин эхлүүлнэ.",
                ephemeral=True)
            return
        try:
            session.restart_division()
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        await _refresh_and_check(interaction, self.guild_id)

    @discord.ui.button(label="Баталгаажуулах", style=discord.ButtonStyle.success,
                       emoji="✅")
    async def confirm_btn(self, interaction: discord.Interaction, button):
        session = _sessions.get(self.guild_id)
        if session is None:
            await interaction.response.send_message("Идэвхгүй.", ephemeral=True)
            return
        if not self._is_captain(session, interaction.user.id) \
                and not _is_admin(interaction):
            await interaction.response.send_message(
                "Зөвхөн ахлагч (эсвэл admin/owner) баталгаажуулна.",
                ephemeral=True)
            return
        try:
            session.confirm_division()
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        _auto_veto(session)
        await _refresh_and_check(interaction, self.guild_id)


def _auto_draft(session):
    """Хуурамч ахлагчийн ээлжийг автоматаар сонгуулна (хамгийн өндөр оноотойг)."""
    draft = session.draft
    if draft is None:
        return
    while not draft.is_complete:
        cur = draft.current_captain
        cap = session.captain1 if cur == 1 else session.captain2
        if not _is_fake(cap):         # жинхэнэ ахлагчийн ээлж — зогсооно
            break
        best = max(draft.available, key=lambda p: p.rating)
        session.draft_pick(cur, best)


def draft_embed(session):
    """Draft самбар — 2 баг, сонгогдоогүй пул, ээлж (FACEIT маяг)."""
    d = session.draft
    cids = {session.captain1.id, session.captain2.id}
    embed = discord.Embed(title="🎯  БАГ ХУВААХ  —  DRAFT", color=FACEIT_ORANGE)
    embed.set_author(name="⚡  CS2 · 5v5 MATCH")
    embed.add_field(name=f"🟧  TEAM A   ·   {d.team1.total_rating}★",
                    value=_squad(d.team1.players, cids), inline=True)
    embed.add_field(name=f"🟦  TEAM B   ·   {d.team2.total_rating}★",
                    value=_squad(d.team2.players, cids), inline=True)
    if d.available:
        pool = "\n".join(
            f"▸ {_who(p)}  `{p.rating}★`"
            for p in sorted(d.available, key=lambda x: -x.rating))
        embed.add_field(name=f"🎲  СОНГОГДООГҮЙ  —  {len(d.available)}",
                        value=pool, inline=False)
    if not d.is_complete:
        cap = session.captain1 if d.current_captain == 1 else session.captain2
        side = "🟧 Team A" if d.current_captain == 1 else "🟦 Team B"
        embed.add_field(name="🎯  СОНГОХ ЭЭЛЖ",
                        value=f"{side} — 👑 {_who(cap)}", inline=False)
    embed.set_footer(text="Draft — ахлагч ээлжлэн сонгоно")
    return embed


class DraftSelect(discord.ui.Select):
    """Ээлжит ахлагчийн тоглогч сонгох цэс."""

    def __init__(self, guild_id, available):
        self.guild_id = guild_id
        options = [
            discord.SelectOption(label=p.name[:100], value=str(p.id),
                                 description=f"{p.rating} од")
            for p in sorted(available, key=lambda x: -x.rating)[:25]
        ]
        super().__init__(placeholder="Тоглогчоо сонгоно уу...", options=options)

    async def callback(self, interaction: discord.Interaction):
        session = _sessions.get(self.guild_id)
        if session is None or session.division_method != "draft":
            await interaction.response.send_message("Идэвхгүй.", ephemeral=True)
            return
        draft = session.draft
        cur = draft.current_captain
        cap = session.captain1 if cur == 1 else session.captain2
        if interaction.user.id != cap.id and not _is_admin(interaction):
            await interaction.response.send_message(
                f"Одоо {_who(cap)}-ийн сонгох ээлж.", ephemeral=True)
            return
        pid = int(self.values[0])
        player = next((p for p in draft.available if p.id == pid), None)
        if player is None:
            await interaction.response.send_message(
                "Энэ тоглогч боломжгүй.", ephemeral=True)
            return
        session.draft_pick(cur, player)
        _auto_draft(session)
        await _refresh_and_check(interaction, self.guild_id)


class DraftView(discord.ui.View):
    """Draft-ын тоглогч сонгох цэсийг агуулна."""

    def __init__(self, guild_id):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        session = _sessions.get(guild_id)
        if session and session.draft and not session.draft.is_complete:
            self.add_item(DraftSelect(guild_id, session.draft.available))


# ==================== Тоглолт бэлтгэх — гар хуваалт ====================

def _auto_manual(session):
    """Хоёр ахлагч хоёулаа хуурамч бол гар хуваалтыг автоматаар гүйцээнэ."""
    m = session.manual
    if m is None or not (_is_fake(session.captain1)
                         and _is_fake(session.captain2)):
        return
    while not m.is_complete:
        p = m.unassigned[0]
        team = 1 if len(m.team1.players) <= len(m.team2.players) else 2
        session.manual_assign(p, team)


def manual_embed(session):
    """Гар хуваалтын самбар (FACEIT маяг)."""
    m = session.manual
    embed = discord.Embed(title="✋  БАГ ХУВААХ  —  ГАР ХУВААЛТ",
                          color=FACEIT_ORANGE)
    embed.set_author(name="⚡  CS2 · 5v5 MATCH")
    embed.add_field(
        name=f"🟧  TEAM A   ·   {len(m.team1.players)}/{TEAM_SIZE}",
        value=_squad(m.team1.players, m._captain_ids), inline=True)
    embed.add_field(
        name=f"🟦  TEAM B   ·   {len(m.team2.players)}/{TEAM_SIZE}",
        value=_squad(m.team2.players, m._captain_ids), inline=True)
    if m.unassigned:
        pool = "\n".join(
            f"▸ {_who(p)}  `{p.rating}★`"
            for p in sorted(m.unassigned, key=lambda x: -x.rating))
        embed.add_field(name=f"🎲  ХУВААРИЛААГҮЙ  —  {len(m.unassigned)}",
                        value=pool, inline=False)
        embed.set_footer(text="Доорх цэснүүдээр тоглогчдыг 2 багт хуваарилна")
    else:
        diff = round(abs(m.team1.total_rating - m.team2.total_rating), 2)
        embed.add_field(
            name="🟢  БҮХ ТОГЛОГЧ ХУВААРИЛАГДЛАА",
            value=(f"Багуудын зөрүү: **{diff}★**\n"
                   "Ахлагч ✅ **Баталгаажуулах** дарж veto руу шилжинэ."),
            inline=False)
        embed.set_footer(text="Гар хуваалт — бэлэн")
    return embed


class ManualTeamSelect(discord.ui.Select):
    """Хуваарилаагүй тоглогчийг тодорхой багт нэмэх цэс."""

    def __init__(self, guild_id, team_num, unassigned):
        self.guild_id = guild_id
        self.team_num = team_num
        name = "🟢 Team A" if team_num == 1 else "🔴 Team B"
        options = [
            discord.SelectOption(label=p.name[:100], value=str(p.id),
                                 description=f"{p.rating} од")
            for p in sorted(unassigned, key=lambda x: -x.rating)[:25]
        ]
        super().__init__(placeholder=f"{name}-д нэмэх тоглогчоо сонго...",
                         options=options)

    async def callback(self, interaction: discord.Interaction):
        session = _sessions.get(self.guild_id)
        if session is None or session.division_method != "manual":
            await interaction.response.send_message("Идэвхгүй.", ephemeral=True)
            return
        if not _is_captain_user(session, interaction.user.id) \
                and not _is_admin(interaction):
            await interaction.response.send_message(
                "Зөвхөн ахлагч (эсвэл admin/owner) баг хуваана.",
                ephemeral=True)
            return
        m = session.manual
        pid = int(self.values[0])
        player = next((p for p in m.unassigned if p.id == pid), None)
        if player is None:
            await interaction.response.send_message(
                "Энэ тоглогч аль хэдийн хуваарилагдсан байна.", ephemeral=True)
            return
        try:
            session.manual_assign(player, self.team_num)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        await _refresh_and_check(interaction, self.guild_id)


class ManualUnassignSelect(discord.ui.Select):
    """Багт орсон тоглогчийг буцааж хуваарилаагүй болгох цэс."""

    def __init__(self, guild_id, manual):
        self.guild_id = guild_id
        options = []
        for tnum, team in ((1, manual.team1), (2, manual.team2)):
            for p in team.players:
                if p.id in manual._captain_ids:
                    continue
                options.append(discord.SelectOption(
                    label=p.name[:100], value=str(p.id),
                    description=f"{_team_name(tnum)} • {p.rating} од"))
        super().__init__(placeholder="↩️ Тоглогчийг багаас буцаах...",
                         options=options)

    async def callback(self, interaction: discord.Interaction):
        session = _sessions.get(self.guild_id)
        if session is None or session.division_method != "manual":
            await interaction.response.send_message("Идэвхгүй.", ephemeral=True)
            return
        if not _is_captain_user(session, interaction.user.id) \
                and not _is_admin(interaction):
            await interaction.response.send_message(
                "Зөвхөн ахлагч (эсвэл admin/owner) баг хуваана.",
                ephemeral=True)
            return
        m = session.manual
        pid = int(self.values[0])
        player = next((p for p in (m.team1.players + m.team2.players)
                       if p.id == pid), None)
        if player is None:
            await interaction.response.send_message(
                "Энэ тоглогч олдсонгүй.", ephemeral=True)
            return
        try:
            session.manual_unassign(player)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        await _refresh_and_check(interaction, self.guild_id)


class ManualConfirmButton(discord.ui.Button):
    """Гар хуваалт дууссаны дараах баталгаажуулах товч."""

    def __init__(self, guild_id):
        super().__init__(label="Баталгаажуулах", emoji="✅",
                         style=discord.ButtonStyle.success)
        self.guild_id = guild_id

    async def callback(self, interaction: discord.Interaction):
        session = _sessions.get(self.guild_id)
        if session is None or session.phase != Phase.DIVISION:
            await interaction.response.send_message("Идэвхгүй.", ephemeral=True)
            return
        if not _is_captain_user(session, interaction.user.id) \
                and not _is_admin(interaction):
            await interaction.response.send_message(
                "Зөвхөн ахлагч (эсвэл admin/owner) баталгаажуулна.",
                ephemeral=True)
            return
        try:
            session.confirm_division()
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        _auto_veto(session)
        await _refresh_and_check(interaction, self.guild_id)


class ManualRestartButton(discord.ui.Button):
    """Гар хуваалтыг тэглэж арга сонгохоос дахин эхлүүлэх товч."""

    def __init__(self, guild_id):
        super().__init__(label="Дахин эхлүүлэх", emoji="🔄",
                         style=discord.ButtonStyle.secondary)
        self.guild_id = guild_id

    async def callback(self, interaction: discord.Interaction):
        session = _sessions.get(self.guild_id)
        if session is None or session.phase != Phase.DIVISION:
            await interaction.response.send_message("Идэвхгүй.", ephemeral=True)
            return
        if not _is_captain_user(session, interaction.user.id) \
                and not _is_admin(interaction):
            await interaction.response.send_message(
                "Зөвхөн ахлагч (эсвэл admin/owner) дахин эхлүүлнэ.",
                ephemeral=True)
            return
        try:
            session.restart_division()
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        await _refresh_and_check(interaction, self.guild_id)


class ManualView(discord.ui.View):
    """Гар хуваалтын цэснүүд — багт нэмэх / буцаах / баталгаажуулах."""

    def __init__(self, guild_id):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        session = _sessions.get(guild_id)
        if session is None or session.manual is None:
            return
        m = session.manual
        if m.unassigned:
            if len(m.team1.players) < TEAM_SIZE:
                self.add_item(ManualTeamSelect(guild_id, 1, m.unassigned))
            if len(m.team2.players) < TEAM_SIZE:
                self.add_item(ManualTeamSelect(guild_id, 2, m.unassigned))
        if any(p.id not in m._captain_ids
               for p in (m.team1.players + m.team2.players)):
            self.add_item(ManualUnassignSelect(guild_id, m))
        if m.is_complete:
            self.add_item(ManualConfirmButton(guild_id))
        self.add_item(ManualRestartButton(guild_id))


def _stamp_embed(embed, guild_id):
    """Embed-ийн footer-т тоглолт нээсэн Монголын огноо·цагийг нэмнэ."""
    when = _match_times.get(guild_id)
    if embed is None or when is None:
        return
    stamp = "🕐 " + when.strftime("%Y-%m-%d %H:%M") + " (Улаанбаатар)"
    existing = embed.footer.text if embed.footer else None
    embed.set_footer(text=f"{existing}  ·  {stamp}" if existing else stamp)


def current_embed_and_view(guild_id):
    """Session-ийн төлвөөс хамаарч (embed, view) хосыг буцаана."""
    session = _sessions.get(guild_id)
    if session is None:
        return None, None
    if session.phase == Phase.REGISTRATION:
        embed, view = registration_embed(session), RegistrationView(guild_id)
    elif session.phase == Phase.DIVISION:
        if session.division_method == "manual":
            embed, view = manual_embed(session), ManualView(guild_id)
        elif session.teams is not None:
            embed, view = (teams_embed(session, "⚔️ Багууд хуваагдлаа"),
                           DivisionDoneView(guild_id))
        elif session.division_method == "draft":
            embed, view = draft_embed(session), DraftView(guild_id)
        else:
            embed, view = division_embed(session), MethodVoteView(guild_id)
    elif session.phase == Phase.VETO:
        embed, view = veto_embed(session), VetoView(guild_id)
    elif session.phase == Phase.READY:
        embed, view = ready_embed(session), None
    else:
        embed, view = registration_embed(session), None
    _stamp_embed(embed, guild_id)
    return embed, view


# ==================== Тоглолт бэлтгэх — газрын veto ====================

def _auto_veto(session):
    """Хуурамч ахлагчийн veto ээлжийг автоматаар (санамсаргүй) гүйцэтгэнэ."""
    v = session.veto
    if v is None:
        return
    while not v.is_complete:
        team = v.current_team
        cap = session.captain1 if team == 1 else session.captain2
        if not _is_fake(cap):
            break
        session.veto_act(team, random.choice(v.remaining))


def veto_embed(session):
    """Газрын veto самбар (FACEIT маяг)."""
    v = session.veto
    embed = discord.Embed(title="🗺️  ГАЗРЫН VETO  —  BO3", color=FACEIT_ORANGE)
    embed.set_author(name="⚡  CS2 · 5v5 MATCH")
    roll = session.veto_first_roll
    if roll and roll.get("history"):
        a, b = roll["history"][-1]
        embed.description = (f"🎲  Эхлэх ээлжийн шоо  —  "
                             f"🟧 Team A `{a}`   ·   🟦 Team B `{b}`")
    banned = {m: t for t, m in v.bans}
    picked = {m: t for t, m in v.picks}
    lines = []
    for m in MAP_POOL:
        if m in picked:
            side = "🟧" if picked[m] == 1 else "🟦"
            lines.append(f"✅  **{m}**  —  {side} {_team_name(picked[m])} pick")
        elif m in banned:
            side = "🟧" if banned[m] == 1 else "🟦"
            lines.append(f"❌  ~~{m}~~  —  {side} {_team_name(banned[m])} ban")
        else:
            lines.append(f"⬜  {m}")
    embed.add_field(name="🗺️  ГАЗРУУД", value="\n".join(lines), inline=False)
    if not v.is_complete:
        team = v.current_team
        cap = session.captain1 if team == 1 else session.captain2
        act = "PICK ✅ сонгох" if v.current_action == "pick" else "BAN ❌ хасах"
        side = "🟧" if team == 1 else "🟦"
        embed.add_field(
            name="🎯  ЭЭЛЖ",
            value=f"{side} {_team_name(team)} — {_who(cap)} — **{act}**",
            inline=False)
    embed.set_footer(text="ban·ban·pick·pick·ban·ban → 3 газар")
    return embed


class VetoMapButton(discord.ui.Button):
    """Нэг газрыг ban/pick хийх товч."""

    def __init__(self, guild_id, map_name):
        super().__init__(label=map_name, style=discord.ButtonStyle.secondary)
        self.guild_id = guild_id
        self.map_name = map_name

    async def callback(self, interaction: discord.Interaction):
        session = _sessions.get(self.guild_id)
        if session is None or session.phase != Phase.VETO:
            await interaction.response.send_message("Идэвхгүй.", ephemeral=True)
            return
        v = session.veto
        team = v.current_team
        cap = session.captain1 if team == 1 else session.captain2
        if interaction.user.id != cap.id:
            await interaction.response.send_message(
                f"Одоо {_team_name(team)} ({_who(cap)})-ийн ээлж.",
                ephemeral=True)
            return
        try:
            session.veto_act(team, self.map_name)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        _auto_veto(session)
        await _refresh_and_check(interaction, self.guild_id)


class VetoView(discord.ui.View):
    """Газрын veto — үлдсэн газар бүрд нэг товч."""

    def __init__(self, guild_id):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        session = _sessions.get(guild_id)
        if session and session.veto and not session.veto.is_complete:
            for m in session.veto.remaining:
                self.add_item(VetoMapButton(guild_id, m))


def ready_embed(session):
    """Тоглолт бэлэн — багууд, газрууд, талууд (FACEIT маяг)."""
    t1, t2 = session.teams
    embed = discord.Embed(title="🏁  ТОГЛОЛТ БЭЛЭН БОЛЛОО!",
                          color=FACEIT_ORANGE)
    embed.set_author(name="⚡  CS2 · 5v5 MATCH")
    embed.add_field(name=f"🟧  TEAM A   ·   AVG {_avg(t1)}★",
                    value=_squad(t1.players, {t1.captain.id}), inline=True)
    embed.add_field(name=f"🟦  TEAM B   ·   AVG {_avg(t2)}★",
                    value=_squad(t2.players, {t2.captain.id}), inline=True)
    rows = []
    for i, m in enumerate(session.maps, 1):
        t_team = session.sides[m]
        ct_team = 2 if t_team == 1 else 1
        picker = session.veto.map_picker(m)
        origin = ("decider 🎲" if picker is None
                  else f"{_team_name(picker)} pick")
        rows.append(f"**{i}.  {m}**  ·  *{origin}*\n"
                    f"   ⫸  T  {_team_name(t_team)}    ⫷  CT  "
                    f"{_team_name(ct_team)}")
    embed.add_field(name="🗺️  ТОГЛОХ 3 ГАЗАР", value="\n".join(rows),
                    inline=False)
    embed.set_footer(text="Амжилт хүсье! 🎮")
    return embed


# ==================== Самбар рендер ба бооцоо ====================

def _match_board_png(guild_id):
    """Одоогийн match самбарын PNG bytes (алдвал None)."""
    session = _sessions.get(guild_id)
    if session is None:
        return None
    mt = _match_times.get(guild_id)
    try:
        ph = session.phase
        if ph == Phase.REGISTRATION:
            return render.render_registration(session, mt)
        if ph == Phase.VETO:
            return render.render_veto(session, mt)
        if ph == Phase.READY:
            return render.render_ready(session, mt)
        if session.division_method == "manual":
            return render.render_manual(session, mt)
        if session.teams is not None:
            return render.render_teams(session, mt)
        if session.division_method == "draft":
            return render.render_draft(session, mt)
        return render.render_division(session, mt)
    except Exception as e:
        print(f"[АНХААР] match самбар рендерлэж чадсангүй: {e}")
        return None


def _match_file(guild_id):
    data = _match_board_png(guild_id)
    return (discord.File(io.BytesIO(data), filename="board.png")
            if data else None)


def _betting_file(guild_id):
    rnd = _betting.get(guild_id)
    if rnd is None:
        return None
    try:
        data = render.render_betting(rnd, _banks, _match_times.get(guild_id))
        return discord.File(io.BytesIO(data), filename="betting.png")
    except Exception as e:
        print(f"[АНХААР] бооцоо рендерлэж чадсангүй: {e}")
        return None


def _debts_file(guild_id):
    try:
        data = render.render_debts(guild_ledger(guild_id), _banks)
        return discord.File(io.BytesIO(data), filename="debts.png")
    except Exception as e:
        print(f"[АНХААР] өр рендерлэж чадсангүй: {e}")
        return None


def _bank_line(player, prefix):
    """Тоглогчийн дансыг хуулж болохуйц текст мөр болгоно."""
    nm = discord.utils.escape_mentions(player.name)
    bank = _banks.get(player.id)
    if bank:
        bk = discord.utils.escape_mentions(f"{bank.bank} · {bank.holder}")
        num = discord.utils.escape_mentions(bank.number)
        return f"{prefix} **{nm}** — {bk}  ·  `{num}`"
    return f"{prefix} **{nm}** — данс бүртгээгүй (`/setbank`)"


def _betting_content(guild_id):
    """Хожигчдын данс — текстээр (дугаарыг нь хуулж болно)."""
    rnd = _betting.get(guild_id)
    if rnd is None or rnd.winner_team is None:
        return None
    lines = ["💳 **Хожигчдын данс** — хожигдогч энэ данс руу шилжүүлнэ:"]
    seen = set()
    for bet in rnd.bets:
        w = bet.winner
        if w is not None and w.id not in seen:
            seen.add(w.id)
            lines.append(_bank_line(w, "🏆"))
    return "\n".join(lines)


def _debts_content(guild_id):
    """Авлагатай хүмүүсийн данс — текстээр."""
    opens = guild_ledger(guild_id).open_debts
    if not opens:
        return None
    lines = ["💳 **Авлагатай хүний данс** — өртэй хүн энэ данс руу "
             "шилжүүлнэ:"]
    seen = set()
    for dt in opens:
        if dt.creditor.id not in seen:
            seen.add(dt.creditor.id)
            lines.append(_bank_line(dt.creditor, "•"))
    return "\n".join(lines)


async def _edit_board(board, guild_id):
    """_boards доторх Message-г одоогийн самбарын зургаар шинэчилнэ."""
    if board is None:
        return
    embed, view = current_embed_and_view(guild_id)
    file = _match_file(guild_id)
    try:
        if file is not None:
            await board.edit(embed=None, attachments=[file], view=view)
        else:
            await board.edit(embed=embed, view=view)
    except Exception:
        pass


async def _refresh_and_check(interaction, guild_id):
    """Самбарыг зургаар шинэчилнэ. READY бол бооцооны самбар нийтэлнэ."""
    embed, view = current_embed_and_view(guild_id)
    file = _match_file(guild_id)
    if file is not None:
        await interaction.response.edit_message(
            embed=None, attachments=[file], view=view)
    else:
        await interaction.response.edit_message(embed=embed, view=view)
    await _post_betting_board(interaction, guild_id)


async def _post_betting_board(interaction, guild_id):
    """Тоглолт READY болмогц бооцооны самбарыг нэг л удаа нийтэлнэ."""
    session = _sessions.get(guild_id)
    if session is None or session.phase != Phase.READY:
        return
    if guild_id in _betting:
        return
    t1, t2 = session.teams
    _betting[guild_id] = BettingRound(t1, t2, BET_AMOUNT)
    try:
        file = _betting_file(guild_id)
        if file is not None:
            msg = await interaction.channel.send(
                content=_betting_content(guild_id), file=file,
                view=BettingView(guild_id))
        else:
            msg = await interaction.channel.send(
                embed=betting_embed(guild_id), view=BettingView(guild_id))
        _bet_boards[guild_id] = msg
        schedule_save()  # JSONBin persistence — Restart-аас сэргэх
    except Exception as e:
        print(f"[АНХААР] бооцооны самбар нийтэлж чадсангүй: {e}")


def _can_record_result(interaction):
    """Хэрэглэгч үр дүн оруулах эрхтэй эсэх (admin эсвэл ахлагч)."""
    if _is_admin(interaction):
        return True
    session = _sessions.get(interaction.guild_id)
    return session is not None and _is_captain_user(session,
                                                    interaction.user.id)


def betting_embed(guild_id):
    """Бооцооны самбар (FACEIT маяг)."""
    rnd = _betting.get(guild_id)
    embed = discord.Embed(title="💰  БООЦОО  —  BO3", color=FACEIT_ORANGE)
    embed.set_author(name="⚡  CS2 · 5v5 MATCH")
    if rnd is None:
        embed.description = "Идэвхтэй бооцоо алга."
        return embed
    if rnd.winner_team is not None:
        wmark = "🟧" if rnd.winner_team == 1 else "🟦"
        embed.description = (f"🏆  **WINNER — {wmark} "
                             f"{_team_name(rnd.winner_team).upper()}**")
    lines = []
    for i, bet in enumerate(rnd.bets, 1):
        amt = f"{bet.amount:,}₮"
        if rnd.winner_team is None:
            lines.append(f"`{i}`  ▸  {_who(bet.player_a)}  ⚔️  "
                         f"{_who(bet.player_b)}   ·   **{amt}**")
        else:
            icon = {UNPAID: "⏳", SETTLED: "✅", DEBT: "🧾"}.get(bet.status, "•")
            word = {UNPAID: "тооцоо хүлээж буй",
                    SETTLED: "төлөгдсөн",
                    DEBT: "өр болсон"}.get(bet.status, "")
            lines.append(f"{icon} `{i}`  🏆 {_who(bet.winner)}  ←  "
                         f"{_who(bet.loser)}   **{amt}**  ·  {word}")
            bank = _banks.get(bet.winner.id)
            lines.append(f"       💳 {bank}" if bank
                         else "       💳 данс бүртгээгүй (`/setbank`)")
    embed.add_field(name="⚔️  ХАРАЛДАА БООЦООНУУД", value="\n".join(lines),
                    inline=False)
    if rnd.winner_team is None:
        embed.add_field(
            name="​",
            value="Тоглолт дуусаад **admin/ахлагч** хожсон багийг тэмдэглэнэ.",
            inline=False)
    elif not rnd.is_complete:
        embed.add_field(
            name="​",
            value=("**Хожсон тоглогч** бооцоогоо сонгож тооцоо хаана:\n"
                   "✅ төлбөр авсан  ·  🧾 авлаагүй → өр"),
            inline=False)
    else:
        ndebt = len(rnd.debts_to_register())
        nset = sum(1 for b in rnd.bets if b.status == SETTLED)
        txt = f"✅ Төлөгдсөн: **{nset}**   ·   🧾 Өр болсон: **{ndebt}**"
        if ndebt:
            txt += "\nӨрийг `/debts`-ээр хянана."
        embed.add_field(name="🏁  БҮХ БООЦОО ХААГДЛАА", value=txt, inline=False)
    embed.set_footer(text=f"Бооцоо тус бүр {rnd.amount:,}₮ · BO3")
    _stamp_embed(embed, guild_id)
    return embed


class BettingResultButton(discord.ui.Button):
    """Хожсон багийг тэмдэглэх товч (admin / ахлагч)."""

    def __init__(self, guild_id, team_num):
        label = "🏆 Team A хожлоо" if team_num == 1 else "🏆 Team B хожлоо"
        style = (discord.ButtonStyle.success if team_num == 1
                 else discord.ButtonStyle.danger)
        super().__init__(label=label, style=style)
        self.guild_id = guild_id
        self.team_num = team_num

    async def callback(self, interaction: discord.Interaction):
        rnd = _betting.get(self.guild_id)
        if rnd is None:
            await interaction.response.send_message("Бооцоо алга.",
                                                    ephemeral=True)
            return
        if not _can_record_result(interaction):
            await interaction.response.send_message(
                "Зөвхөн admin эсвэл багийн ахлагч үр дүн оруулна.",
                ephemeral=True)
            return
        try:
            rnd.record_result(self.team_num)
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        schedule_save()
        file = _betting_file(self.guild_id)
        if file is not None:
            await interaction.response.edit_message(
                content=_betting_content(self.guild_id), embed=None,
                attachments=[file], view=BettingView(self.guild_id))
        else:
            await interaction.response.edit_message(
                embed=betting_embed(self.guild_id),
                view=BettingView(self.guild_id))


class BettingResetButton(discord.ui.Button):
    """Үр дүнг цуцлах — буруу багийг хожсон гэж тэмдэглэсэн бол."""

    def __init__(self, guild_id):
        super().__init__(label="Үр дүн солих", emoji="🔄",
                         style=discord.ButtonStyle.secondary)
        self.guild_id = guild_id

    async def callback(self, interaction: discord.Interaction):
        rnd = _betting.get(self.guild_id)
        if rnd is None:
            await interaction.response.send_message("Бооцоо алга.",
                                                    ephemeral=True)
            return
        if not _can_record_result(interaction):
            await interaction.response.send_message(
                "Зөвхөн admin/ахлагч үр дүн солино.", ephemeral=True)
            return
        try:
            rnd.reset_result()
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return
        schedule_save()
        file = _betting_file(self.guild_id)
        if file is not None:
            await interaction.response.edit_message(
                content=_betting_content(self.guild_id), embed=None,
                attachments=[file], view=BettingView(self.guild_id))
        else:
            await interaction.response.edit_message(
                embed=betting_embed(self.guild_id),
                view=BettingView(self.guild_id))


async def _resolve_bet(interaction, guild_id, bet_idx, received):
    """Нэг бооцоог хаах — received=True бол SETTLED, эс бөгөөс DEBT."""
    rnd = _betting.get(guild_id)
    if rnd is None or bet_idx < 0 or bet_idx >= len(rnd.bets):
        await interaction.response.send_message("Бооцоо олдсонгүй.",
                                                ephemeral=True)
        return
    bet = rnd.bets[bet_idx]
    if bet.status != UNPAID:
        await interaction.response.send_message(
            "Энэ бооцоо аль хэдийн хаагдсан.", ephemeral=True)
        return
    is_winner = interaction.user.id == bet.winner.id
    if not (is_winner or _can_record_result(interaction)):
        await interaction.response.send_message(
            f"Зөвхөн хожсон тоглогч ({_who(bet.winner)}) эсвэл admin "
            "энэ бооцоог хаана.", ephemeral=True)
        return
    if received:
        bet.confirm_received()
        note = f"✅ Бооцоо хаагдлаа: {_who(bet.winner)} төлбөрөө хүлээн авсан."
    else:
        bet.reject()
        guild_ledger(guild_id).add(
            bet.loser, bet.winner, bet.amount, origin="CS2 бооцоо",
            created=datetime.now(_MN_TZ).strftime("%Y-%m-%d"))
        save_debts()
        if interaction.channel_id is not None:
            _reminder_channels[guild_id] = interaction.channel_id
            save_channels()
        note = (f"🧾 Өр бүртгэгдлээ: {_who(bet.loser)} → "
                f"{_who(bet.winner)} **{bet.amount:,}₮**. `/debts`-ээс хянана.")
    schedule_save()
    file = _betting_file(guild_id)
    if file is not None:
        await interaction.response.edit_message(
            content=_betting_content(guild_id), embed=None,
            attachments=[file], view=BettingView(guild_id))
    else:
        await interaction.response.edit_message(
            embed=betting_embed(guild_id), view=BettingView(guild_id))
    await interaction.followup.send(note, ephemeral=True)


class BetSettleSelect(discord.ui.Select):
    """Хожсон тоглогч төлбөр авсан бооцоогоо сонгоно."""

    def __init__(self, guild_id, open_bets):
        self.guild_id = guild_id
        options = [
            discord.SelectOption(
                label=f"{b.winner.name} ← {b.loser.name}"[:100],
                value=str(idx), description=f"{b.amount:,}₮")
            for idx, b in open_bets]
        super().__init__(placeholder="✅ Төлбөр АВСАН бооцоогоо сонго...",
                         options=options)

    async def callback(self, interaction: discord.Interaction):
        await _resolve_bet(interaction, self.guild_id,
                           int(self.values[0]), received=True)


class BetDebtSelect(discord.ui.Select):
    """Хожсон тоглогч төлбөр аваагүй бооцоогоо сонгож өр болгоно."""

    def __init__(self, guild_id, open_bets):
        self.guild_id = guild_id
        options = [
            discord.SelectOption(
                label=f"{b.winner.name} ← {b.loser.name}"[:100],
                value=str(idx), description=f"{b.amount:,}₮")
            for idx, b in open_bets]
        super().__init__(placeholder="🧾 Төлбөр АВААГҮЙ → өр болгох...",
                         options=options)

    async def callback(self, interaction: discord.Interaction):
        await _resolve_bet(interaction, self.guild_id,
                           int(self.values[0]), received=False)


class BettingView(discord.ui.View):
    """Бооцооны самбарын товч/цэс — төлвөөс хамаарна."""

    def __init__(self, guild_id):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        rnd = _betting.get(guild_id)
        if rnd is None:
            return
        if rnd.winner_team is None:
            self.add_item(BettingResultButton(guild_id, 1))
            self.add_item(BettingResultButton(guild_id, 2))
        elif not rnd.is_complete:
            open_bets = [(i, b) for i, b in enumerate(rnd.bets)
                         if b.status == UNPAID]
            if open_bets:
                self.add_item(BetSettleSelect(guild_id, open_bets))
                self.add_item(BetDebtSelect(guild_id, open_bets))
            if not any(b.status in (SETTLED, DEBT) for b in rnd.bets):
                self.add_item(BettingResetButton(guild_id))


# ==================== Өрийн дэвтэр ====================

def debts_embed(guild_id):
    """Өрийн дэвтэр — нээлттэй өрүүд (FACEIT маяг)."""
    led = guild_ledger(guild_id)
    embed = discord.Embed(title="🧾  ӨРИЙН ДЭВТЭР", color=0xE6492D)
    embed.set_author(name="⚡  CS2 · 5v5 MATCH")
    opens = led.open_debts
    if not opens:
        embed.description = "✅  Нээлттэй өр алга — бүгд цэвэрхэн! 🎉"
        return embed
    lines = []
    for i, d in enumerate(opens, 1):
        lines.append(f"`{i}`  ▸  {_who(d.debtor)}  →  {_who(d.creditor)}   "
                     f"**{d.amount:,}₮**")
        bank = _banks.get(d.creditor.id)
        lines.append(f"       💳 {bank}" if bank
                     else "       💳 данс бүртгээгүй (`/setbank`)")
    value = "\n".join(lines)
    if len(value) > 1024:
        value = value[:1015].rsplit("\n", 1)[0] + "\n…"
    embed.add_field(name=f"💸  НЭЭЛТТЭЙ ӨР  —  {len(opens)}",
                    value=value, inline=False)
    embed.set_footer(text="Авлагатай хүн / admin өр барагдсаныг "
                          "доороос баталгаажуулна")
    return embed


class DebtSettleSelect(discord.ui.Select):
    """Барагдсан өрийг сонгож баталгаажуулах цэс."""

    def __init__(self, guild_id, open_debts):
        self.guild_id = guild_id
        options = [
            discord.SelectOption(
                label=f"{d.debtor.name} → {d.creditor.name}"[:100],
                value=str(idx),
                description=f"{d.amount:,}₮  ·  {d.created or 'огноогүй'}")
            for idx, d in open_debts]
        super().__init__(placeholder="✅ Барагдсан өрийг сонгоно уу...",
                         options=options)

    async def callback(self, interaction: discord.Interaction):
        led = guild_ledger(self.guild_id)
        idx = int(self.values[0])
        if idx < 0 or idx >= len(led.debts):
            await interaction.response.send_message("Өр олдсонгүй.",
                                                    ephemeral=True)
            return
        debt = led.debts[idx]
        if debt.settled:
            await interaction.response.send_message(
                "Энэ өр аль хэдийн барагдсан.", ephemeral=True)
            return
        is_admin = _is_admin(interaction)
        if not (interaction.user.id == debt.creditor.id or is_admin):
            await interaction.response.send_message(
                f"Зөвхөн авлагатай хүн ({_who(debt.creditor)}) эсвэл admin "
                "барагдсаныг баталгаажуулна.", ephemeral=True)
            return
        led.settle(debt)
        save_debts()
        file = _debts_file(self.guild_id)
        if file is not None:
            await interaction.response.edit_message(
                content=_debts_content(self.guild_id), embed=None,
                attachments=[file], view=DebtView(self.guild_id))
        else:
            await interaction.response.edit_message(
                embed=debts_embed(self.guild_id),
                view=DebtView(self.guild_id))
        await interaction.followup.send(
            f"✅ Өр барагдлаа: {_who(debt.debtor)} → {_who(debt.creditor)} "
            f"**{debt.amount:,}₮**.", ephemeral=True)


class DebtView(discord.ui.View):
    """Өрийн дэвтрийн самбар — барагдуулах цэс."""

    def __init__(self, guild_id):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        led = guild_ledger(guild_id)
        open_debts = [(i, d) for i, d in enumerate(led.debts)
                      if not d.settled]
        if open_debts:
            self.add_item(DebtSettleSelect(guild_id, open_debts[:25]))


@bot.tree.command(name="debts",
                  description="Өрийн дэвтрийг харах ба өр барагдуулах")
@app_commands.default_permissions(use_application_commands=True)
async def debts_cmd(interaction: discord.Interaction):
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "Энэ командыг серверт ашиглана уу.", ephemeral=True)
        return
    _reminder_channels[interaction.guild_id] = interaction.channel_id
    save_channels()
    file = _debts_file(interaction.guild_id)
    if file is not None:
        await interaction.response.send_message(
            content=_debts_content(interaction.guild_id), file=file,
            view=DebtView(interaction.guild_id))
    else:
        await interaction.response.send_message(
            embed=debts_embed(interaction.guild_id),
            view=DebtView(interaction.guild_id))


# ==================== Өрийн сануулга ====================

_REMINDER_INTROS = [
    "**Өрөө төлөөч ээ, санхүүгийн нинжа нар минь.** 🥷",
    "**Мөнгө зээлээд disappear хийдэг NPC нар минь, өрөө төлөөрэй.** 🫥",
    "**Өрийн эзэд минь, намайг банк биш хүн гэдгийг санаарай.** 🏦",
    "**Луйварчид минь, wallet чинь сэрсэн үү?** 💸",
    "**Өрөө төлөхгүй бол screenshot-той live орно шүү.** 📸",
    "**Миний мөнгө та нарын passive income биш ээ.** 💀",
    "**Өрөө төлөөрэй, би charity биш шүү.** 🙏",
    "**Өрийн аваргууд минь, season дуусахаас өмнө төлнө үү.** 🏆",
    "**Мөнгө аваад ghost хийдэг ахан дүүс минь, амьд уу?** 👻",
    "**Та нарын өр миний сэтгэлд monthly subscription шиг явж байна.** 📅",
    "**Өрөө төл, эсвэл «санхүүгийн сураггүй алга болсон хүн» гэж зарлана.** 📢",
    "**Миний мөнгө та нар дээр амралтаараа яваад буцаж ирэхгүй байна.** ✈️",
    "**Өрөө төлөөч, Excel дээр чинь нэр чинь улаанаар байна.** 📊",
    "**Луйварчид минь, миний мөнгийг hostage-оос сулла.** 🔒",
    "**Өрөө төлөхгүй бол би чамайг «банк бус, хүн бус» гэж хадгална.** 📝",
]


async def _send_debt_reminder(guild_id, channel):
    """Нэг серверийн өртэй хүмүүст сануулга илгээнэ. Илгээсэн бол True."""
    if channel is None:
        return False
    opens = guild_ledger(guild_id).open_debts
    if not opens:
        return False
    rows = []
    for d in sorted(opens, key=lambda x: -x.amount):
        rows.append(f"{d.debtor.name[:12]:<12}  →  "
                    f"{d.creditor.name[:12]:<12}  {d.amount:>8,}₮")
    table = "```\n" + "\n".join(rows) + "\n```"
    # Хүлээн авах талын (creditor) банкны дансыг нэгтгэж харуулна.
    # Зөвхөн жинхэнэ Discord гишүүдийн (хуурамч биш) бүртгэлтэй дансыг гаргана.
    bank_lines = []
    seen = set()
    for d in opens:
        cid = d.creditor.id
        if cid in seen or cid < 1_000_000:
            continue
        seen.add(cid)
        ba = _banks.get(cid)
        if ba is None:
            continue
        bank_lines.append(f"**{d.creditor.name}** — {ba.bank} · "
                          f"`{ba.number}` · {ba.holder}")
    desc = (random.choice(_REMINDER_INTROS) + "\n"
            "Хэн хэнд өртэй вэ:\n" + table)
    if bank_lines:
        desc += "\n💳 **Хүлээн авах данс:**\n" + "\n".join(bank_lines)
    embed = discord.Embed(
        title="🔔  ӨРИЙН САНУУЛГА",
        description=desc,
        color=0xE6492D)
    embed.set_footer(text="Дэлгэрэнгүй ба барагдуулах:  /debts")
    mentions = " ".join(f"<@{i}>" for i in {d.debtor.id for d in opens}
                        if i >= 1_000_000)
    try:
        await channel.send(content=mentions or None, embed=embed)
        return True
    except Exception as e:
        print(f"[АНХААР] өр сануулга илгээж чадсангүй: {e}")
        return False


@tasks.loop(time=time(hour=REMINDER_HOUR, minute=0, tzinfo=_MN_TZ))
async def debt_reminder():
    """Өдөр бүр REMINDER_HOUR цагт (Улаанбаатар) өртэй хүмүүст сануулна."""
    for guild_id in list(_ledgers):
        cid = _reminder_channels.get(guild_id)
        channel = bot.get_channel(cid) if cid else None
        await _send_debt_reminder(guild_id, channel)


@bot.tree.command(name="remind",
                  description="Өртэй хүмүүст сануулга шууд илгээх")
@app_commands.default_permissions(use_application_commands=True)
async def remind_cmd(interaction: discord.Interaction):
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "Энэ командыг серверт ашиглана уу.", ephemeral=True)
        return
    _reminder_channels[interaction.guild_id] = interaction.channel_id
    save_channels()
    sent = await _send_debt_reminder(interaction.guild_id,
                                     interaction.channel)
    if sent:
        await interaction.response.send_message(
            "✅ Сануулга илгээлээ.", ephemeral=True)
    else:
        await interaction.response.send_message(
            "Идэвхтэй өр алга — сануулах зүйл байхгүй.", ephemeral=True)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error):
    """Командын алдааг хэрэглэгчид цэвэрхэн харуулна."""
    msg = f"⚠️ Алдаа гарлаа: {error}"
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit(
            "DISCORD_TOKEN тохируулаагүй байна — .env файлыг шалгана уу.")
    bot.run(TOKEN)

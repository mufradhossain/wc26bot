import asyncio
import os
import sys
import logging
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv()

import aiohttp
import discord
from discord.ext import commands

import db as db_mod
import api
from teams import get_flag

log = logging.getLogger("wc_predict")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
CLIENT_ID = os.environ["CLIENT_ID"]

DISCOVERY_INTERVAL = 6 * 60 * 60
POST_BEFORE_KICKOFF_SEC = 3 * 60 * 60
CHECK_AFTER_KICKOFF_SEC = 115 * 60
LIVE_POLL_INTERVAL = 5 * 60

intents = discord.Intents.default()
intents.message_content = True
intents.guild_messages = True
intents.guild_reactions = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
db: db_mod.aiosqlite.Connection = None  # type: ignore
http: aiohttp.ClientSession = None  # type: ignore
_tracked: set[str] = set()
_lifecycle_tasks: dict[str, asyncio.Task] = {}


def format_vote_card(info: dict) -> str:
    ts = info["kickoff"]
    stage = api.format_type(info.get("type", "group"))
    group = info.get("group", "")
    label = f"{stage}" if group.startswith("R") or group in ("QF", "SF", "3RD", "FINAL") else f"Group {group}"
    return (
        f"\u26bd **MATCH PREDICTION**\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"{info['home_emoji']} **{info['home_team']}**  vs  **{info['away_team']}** {info['away_emoji']}\n"
        f"\ud83d\udcc5 <t:{ts}:F>\n"
        f"\ud83c\udfd4\ufe0f {label} - Matchday {info.get('matchday', '?')}\n\n"
        f"React with {info['home_emoji']} {info['away_emoji']} or \U0001f91d for a draw!"
    )


def format_results(match_row, info, votes) -> str:
    home_score = info["home_goals"]
    away_score = info["away_goals"]
    home_team = match_row[1]
    away_team = match_row[2]
    home_emoji = match_row[3]
    away_emoji = match_row[4]
    kickoff = match_row[5]

    winner = "draw"
    if home_score is not None and away_score is not None:
        if home_score > away_score:
            winner = "home"
        elif away_score > home_score:
            winner = "away"

    home_votes = sum(1 for v in votes if v[4] == "home")
    away_votes = sum(1 for v in votes if v[4] == "away")
    draw_votes = sum(1 for v in votes if v[4] == "draw")
    total = home_votes + away_votes + draw_votes
    draw_emoji = "\U0001f91d"

    lines = [
        f"\ud83c\udfc1 **MATCH RESULT**",
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501",
        f"{home_emoji} **{home_team}**  {home_score} - {away_score}  **{away_team}** {away_emoji}\n",
        f"\ud83d\udcca **Votes: {total} total**",
    ]

    if total > 0:
        hp = round(home_votes / total * 100)
        ap = round(away_votes / total * 100)
        dp = round(draw_votes / total * 100)
        lines.append(f"{home_emoji} {home_team}: {home_votes} ({hp}%)")
        lines.append(f"{away_emoji} {away_team}: {away_votes} ({ap}%)")
        lines.append(f"{draw_emoji} Draw: {draw_votes} ({dp}%)")
    lines.append("")
    lines.append("\ud83c\udfc6 **Predictions:**")

    if not votes:
        lines.append("_No predictions registered._")
    else:
        user_votes: dict[str, list] = {}
        for v in votes:
            uid = v[2]
            if uid not in user_votes:
                user_votes[uid] = []
            user_votes[uid].append(v)

        for uid, uv_list in user_votes.items():
            username = uv_list[0][3]
            voted_at = min(v[5] for v in uv_list)
            picked_teams = [v[4] for v in uv_list]

            kickoff_ms = kickoff * 1000
            diff = kickoff_ms - voted_at
            if diff > 0:
                minutes = int(diff / 60000)
                timing = f"{minutes}m before kickoff"
            else:
                mins_late = int(abs(diff) / 60000)
                timing = f"\u26a0\ufe0f Voted late! ({mins_late}m after kickoff)"

            picked_labels = []
            correct = False
            for p in picked_teams:
                if p == "home":
                    picked_labels.append(f"{home_emoji} {home_team}")
                    if winner == "home":
                        correct = True
                elif p == "away":
                    picked_labels.append(f"{away_emoji} {away_team}")
                    if winner == "away":
                        correct = True
                elif p == "draw":
                    picked_labels.append(f"{draw_emoji} Draw")
                    if winner == "draw":
                        correct = True

            icon = "\u2705" if correct else "\u274c"
            lines.append(f"{icon} **{username}** picked {' & '.join(picked_labels)} ({timing})")

    return "\n".join(lines)


@bot.event
async def on_ready():
    global db, http
    log.info(f"Logged in as {bot.user}")

    db = await db_mod.init_db()
    http = aiohttp.ClientSession()

    try:
        synced = await bot.tree.sync()
        log.info(f"Synced {len(synced)} slash commands")
    except Exception as e:
        log.error(f"Failed to sync commands: {e}")

    await resume_active_matches()
    asyncio.create_task(discovery_loop())


async def check_admin(interaction: discord.Interaction) -> bool:
    member = interaction.user
    if not isinstance(member, discord.Member):
        member = interaction.guild.get_member(interaction.user.id)
        if not member:
            try:
                member = await interaction.guild.fetch_member(interaction.user.id)
            except Exception:
                member = None
    return member is not None and member.guild_permissions.manage_guild


async def post_active_cards(guild_id: str, channel):
    games = await api.get_all_games(http)
    now = datetime.now(timezone.utc).timestamp()
    posted = 0
    for raw in games:
        info = api.parse_game(raw)
        match_id = info["match_id"]

        if info["finished"]:
            continue

        time_to_kick = info["kickoff"] - now
        if time_to_kick > POST_BEFORE_KICKOFF_SEC:
            continue

        existing = await db_mod.get_guild_message(db, guild_id, match_id)
        if existing:
            continue

        db_match = await db_mod.get_match(db, match_id)
        if not db_match:
            await db_mod.add_match(db, match_id, info["home_team"], info["away_team"],
                                   get_flag(info["home_team"]), get_flag(info["away_team"]), info["kickoff"])
            if match_id not in _tracked:
                _tracked.add(match_id)
                _lifecycle_tasks[match_id] = asyncio.create_task(match_lifecycle(match_id, info["kickoff"]))

        home_emoji = get_flag(info["home_team"])
        away_emoji = get_flag(info["away_team"])
        draw_emoji = "\U0001f91d"
        card = format_vote_card({**info, "home_emoji": home_emoji, "away_emoji": away_emoji})
        try:
            msg = await channel.send(card)
            await msg.add_reaction(home_emoji)
            await msg.add_reaction(away_emoji)
            await msg.add_reaction(draw_emoji)
            await db_mod.add_guild_message(db, guild_id, match_id, str(channel.id), str(msg.id))
            posted += 1
        except Exception as e:
            log.error(f"Failed to post match {match_id} to guild {guild_id}: {e}")

    return posted


@bot.tree.command(name="setchannel", description="Set this channel for match predictions")
async def setchannel(interaction: discord.Interaction):
    if not await check_admin(interaction):
        await interaction.response.send_message("\u274c Only server admins can use this command.", ephemeral=True)
        return
    await db_mod.set_guild_channel(db, str(interaction.guild_id), str(interaction.channel_id))
    await interaction.response.send_message("\ud83c\udfaf This channel is now configured for match predictions.")

    posted = await post_active_cards(str(interaction.guild_id), interaction.channel)
    if posted > 0:
        await interaction.followup.send(f"\u26bf Posted {posted} active match(es)!")


@bot.tree.command(name="postmatch", description="Post prediction cards for live or upcoming matches")
async def postmatch(interaction: discord.Interaction):
    if not await check_admin(interaction):
        await interaction.response.send_message("\u274c Only server admins can use this command.", ephemeral=True)
        return
    await interaction.response.send_message("\ud83d\udd0d Checking for live/upcoming matches...")
    posted = await post_active_cards(str(interaction.guild_id), interaction.channel)
    if posted > 0:
        await interaction.followup.send(f"\u26bf Posted {posted} match(es)!")
    else:
        await interaction.followup.send("\u274c No live or upcoming matches found within the next 3 hours.")


async def handle_reaction(payload: discord.RawReactionActionEvent, is_add: bool):
    if payload.user_id == bot.user.id:
        return
    if payload.guild_id is None:
        return

    guild_id = str(payload.guild_id)
    msg_id = str(payload.message_id)
    match_row = await db_mod.get_match_by_message(db, msg_id)
    if not match_row:
        return
    if match_row[6] == "completed":
        return

    match_id = match_row[0]
    home_emoji = match_row[3]
    away_emoji = match_row[4]

    DRAW_EMOJI = "\U0001f91d"

    team_picked = None
    emoji_str = str(payload.emoji)
    if emoji_str == str(home_emoji):
        team_picked = "home"
    elif emoji_str == str(away_emoji):
        team_picked = "away"
    elif emoji_str == DRAW_EMOJI:
        team_picked = "draw"

    if not team_picked:
        return

    user_id = str(payload.user_id)
    if is_add:
        guild = bot.get_guild(payload.guild_id)
        member = None
        if guild:
            member = guild.get_member(payload.user_id)
            if not member:
                try:
                    member = await guild.fetch_member(payload.user_id)
                except Exception:
                    pass
        username = member.display_name if member else f"user_{user_id}"
        log.info(f"Recording vote: {username} picked {team_picked} for match {match_id}")
        await db_mod.upsert_vote(db, match_id, guild_id, user_id, username, team_picked, int(datetime.now(timezone.utc).timestamp() * 1000))
    else:
        log.info(f"Removing vote: user {user_id} {team_picked} for match {match_id}")
        await db_mod.remove_vote(db, match_id, guild_id, user_id, team_picked)


@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    await handle_reaction(payload, True)


@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    await handle_reaction(payload, False)


async def discovery_loop():
    await bot.wait_until_ready()
    while not bot.is_closed():
        try:
            await discover_matches()
        except Exception as e:
            log.error(f"Discovery error: {e}")
        await asyncio.sleep(DISCOVERY_INTERVAL)


async def discover_matches():
    games = await api.get_all_games(http)
    now = datetime.now(timezone.utc).timestamp()
    window = POST_BEFORE_KICKOFF_SEC + DISCOVERY_INTERVAL

    for raw in games:
        info = api.parse_game(raw)
        mid = info["match_id"]

        if mid in _tracked:
            continue

        time_to_kick = info["kickoff"] - now
        if time_to_kick < 0 or time_to_kick > window:
            continue

        existing = await db_mod.get_match(db, mid)
        if existing:
            _tracked.add(mid)
            continue

        _tracked.add(mid)
        _lifecycle_tasks[mid] = asyncio.create_task(match_schedule(mid, info))


async def post_match_card(info: dict):
    home_emoji = get_flag(info["home_team"])
    away_emoji = get_flag(info["away_team"])
    draw_emoji = "\U0001f91d"

    card = format_vote_card({**info, "home_emoji": home_emoji, "away_emoji": away_emoji})

    await db_mod.add_match(db, info["match_id"], info["home_team"], info["away_team"],
                           home_emoji, away_emoji, info["kickoff"])

    guilds = await db_mod.get_all_guilds(db)
    for guild_id, channel_id in guilds:
        try:
            channel = bot.get_channel(int(channel_id)) or await bot.fetch_channel(int(channel_id))
            msg = await channel.send(card)
            await msg.add_reaction(home_emoji)
            await msg.add_reaction(away_emoji)
            await msg.add_reaction(draw_emoji)
            await db_mod.add_guild_message(db, guild_id, info["match_id"], channel_id, str(msg.id))
        except Exception as e:
            log.error(f"Failed to post card to guild {guild_id} channel {channel_id}: {e}")


async def match_schedule(match_id: str, info: dict):
    now = datetime.now(timezone.utc).timestamp()
    post_at = info["kickoff"] - POST_BEFORE_KICKOFF_SEC
    wait = post_at - now

    if wait > 0:
        log.info(f"Match {match_id}: posting card in {int(wait/60)}m (30m before kickoff)")
        await asyncio.sleep(wait)

    await post_match_card(info)
    await match_lifecycle(match_id, info["kickoff"])


async def match_lifecycle(match_id: str, kickoff_ts: int):
    now = datetime.now(timezone.utc).timestamp()
    wait = (kickoff_ts + CHECK_AFTER_KICKOFF_SEC) - now

    if wait > 0:
        log.info(f"Match {match_id}: sleeping {int(wait/60)}m until +115min after kickoff")
        await asyncio.sleep(wait)

    while True:
        try:
            game = await api.get_game(http, match_id)
        except Exception as e:
            log.error(f"Error checking match {match_id}: {e}")
            await asyncio.sleep(LIVE_POLL_INTERVAL)
            continue

        if not game:
            log.warning(f"Match {match_id}: no data returned, giving up")
            break

        info = api.parse_game(game)

        if info["finished"]:
            await publish_result(match_id, info)
            break

        if info["time_elapsed"] == api.NOT_STARTED:
            log.info(f"Match {match_id}: hasn't started yet, waiting 5m")
            await asyncio.sleep(LIVE_POLL_INTERVAL)
            continue

        log.info(f"Match {match_id}: still live ({info['time_elapsed']}), checking again in {LIVE_POLL_INTERVAL//60}m")
        await asyncio.sleep(LIVE_POLL_INTERVAL)


async def publish_result(match_id: str, info: dict):
    match_row = await db_mod.get_match(db, match_id)
    if not match_row:
        return

    await db_mod.complete_match(db, match_id)

    guild_msgs = await db_mod.get_all_guild_messages(db, match_id)
    for gm in guild_msgs:
        guild_id = gm[0]
        channel_id = gm[2]
        votes = await db_mod.get_votes(db, match_id, guild_id)
        text = format_results(match_row, info, votes)
        try:
            channel = bot.get_channel(int(channel_id)) or await bot.fetch_channel(int(channel_id))
            await channel.send(text)
        except Exception as e:
            log.error(f"Failed to post result for {match_id} in guild {guild_id}: {e}")

    _tracked.discard(match_id)
    if match_id in _lifecycle_tasks:
        del _lifecycle_tasks[match_id]


async def resume_active_matches():
    active = await db_mod.get_active_matches(db)
    now = datetime.now(timezone.utc).timestamp()
    for row in active:
        match_id = row[0]
        kickoff = row[5]
        _tracked.add(match_id)
        log.info(f"Resuming {match_id}")

        post_at = kickoff - POST_BEFORE_KICKOFF_SEC

        if now < post_at:
            games = await api.get_all_games(http)
            for g in games:
                if g["id"] == match_id:
                    info = api.parse_game(g)
                    _lifecycle_tasks[match_id] = asyncio.create_task(match_schedule(match_id, info))
                    break
            else:
                _lifecycle_tasks[match_id] = asyncio.create_task(match_lifecycle(match_id, kickoff))
        else:
            _lifecycle_tasks[match_id] = asyncio.create_task(match_lifecycle(match_id, kickoff))


if __name__ == "__main__":
    import signal
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    bot.run(DISCORD_TOKEN)

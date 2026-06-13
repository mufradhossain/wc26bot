import aiosqlite
import os

DB_PATH = os.environ.get(
    "RAILWAY_VOLUME_MOUNT_PATH", "."
)
if DB_PATH != ".":
    DB_PATH = f"{DB_PATH}/database.db"
else:
    DB_PATH = "./database.db"


async def init_db():
    db = await aiosqlite.connect(DB_PATH)
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS guild_config (
            guild_id TEXT PRIMARY KEY,
            channel_id TEXT
        );
        CREATE TABLE IF NOT EXISTS matches (
            match_id TEXT PRIMARY KEY,
            home_team TEXT,
            away_team TEXT,
            home_emoji TEXT,
            away_emoji TEXT,
            start_time INTEGER,
            status TEXT DEFAULT 'scheduled'
        );
        CREATE TABLE IF NOT EXISTS guild_messages (
            guild_id TEXT,
            match_id TEXT,
            channel_id TEXT,
            message_id TEXT,
            PRIMARY KEY (guild_id, match_id)
        );
        CREATE TABLE IF NOT EXISTS votes (
            match_id TEXT,
            guild_id TEXT,
            user_id TEXT,
            username TEXT,
            team_picked TEXT,
            voted_at INTEGER,
            PRIMARY KEY (match_id, guild_id, user_id, team_picked)
        );
    """
    )
    try:
        await db.execute("ALTER TABLE matches ADD COLUMN winner TEXT DEFAULT NULL")
    except Exception:
        pass
    await db.commit()
    return db


async def set_guild_channel(db, guild_id: str, channel_id: str):
    await db.execute(
        "INSERT OR REPLACE INTO guild_config (guild_id, channel_id) VALUES (?, ?)",
        (guild_id, channel_id),
    )
    await db.commit()


async def get_all_guilds(db) -> list[tuple[str, str]]:
    cursor = await db.execute("SELECT guild_id, channel_id FROM guild_config")
    rows = await cursor.fetchall()
    return [(row[0], row[1]) for row in rows]


async def add_match(db, match_id, home_team, away_team, home_emoji, away_emoji, start_time):
    await db.execute(
        "INSERT OR IGNORE INTO matches (match_id, home_team, away_team, home_emoji, away_emoji, start_time, status) VALUES (?, ?, ?, ?, ?, ?, 'scheduled')",
        (match_id, home_team, away_team, home_emoji, away_emoji, start_time),
    )
    await db.commit()


async def add_guild_message(db, guild_id: str, match_id: str, channel_id: str, message_id: str):
    await db.execute(
        "INSERT OR REPLACE INTO guild_messages (guild_id, match_id, channel_id, message_id) VALUES (?, ?, ?, ?)",
        (guild_id, match_id, channel_id, message_id),
    )
    await db.commit()


async def get_match(db, match_id: str):
    cursor = await db.execute("SELECT * FROM matches WHERE match_id = ?", (match_id,))
    return await cursor.fetchone()


async def get_match_by_message(db, message_id: str):
    cursor = await db.execute("SELECT match_id FROM guild_messages WHERE message_id = ?", (message_id,))
    row = await cursor.fetchone()
    if not row:
        return None
    mid = row[0]
    return await get_match(db, mid)


async def get_guild_message(db, guild_id: str, match_id: str):
    cursor = await db.execute("SELECT * FROM guild_messages WHERE guild_id = ? AND match_id = ?", (guild_id, match_id))
    return await cursor.fetchone()


async def get_all_guild_messages(db, match_id: str):
    cursor = await db.execute("SELECT * FROM guild_messages WHERE match_id = ?", (match_id,))
    return await cursor.fetchall()


async def get_active_matches(db):
    cursor = await db.execute("SELECT * FROM matches WHERE status != 'completed'")
    return await cursor.fetchall()


async def complete_match(db, match_id: str):
    await db.execute("UPDATE matches SET status = 'completed' WHERE match_id = ?", (match_id,))
    await db.commit()


async def upsert_vote(db, match_id: str, guild_id: str, user_id: str, username: str, team_picked: str, voted_at: int):
    await db.execute(
        "INSERT OR IGNORE INTO votes (match_id, guild_id, user_id, username, team_picked, voted_at) VALUES (?, ?, ?, ?, ?, ?)",
        (match_id, guild_id, user_id, username, team_picked, voted_at),
    )
    await db.commit()


async def remove_vote(db, match_id: str, guild_id: str, user_id: str, team_picked: str):
    await db.execute("DELETE FROM votes WHERE match_id = ? AND guild_id = ? AND user_id = ? AND team_picked = ?", (match_id, guild_id, user_id, team_picked))
    await db.commit()


async def get_votes(db, match_id: str, guild_id: str):
    cursor = await db.execute("SELECT * FROM votes WHERE match_id = ? AND guild_id = ?", (match_id, guild_id))
    return await cursor.fetchall()


async def save_winner(db, match_id: str, winner: str):
    await db.execute("UPDATE matches SET winner = ? WHERE match_id = ?", (winner, match_id))
    await db.commit()


async def get_matches_needing_winner(db):
    cursor = await db.execute("SELECT match_id FROM matches WHERE status = 'completed' AND winner IS NULL")
    rows = await cursor.fetchall()
    return [row[0] for row in rows]


async def get_leaderboard_data(db, guild_id: str):
    cursor = await db.execute(
        """
        SELECT v.match_id, v.user_id, v.username, v.team_picked, m.winner
        FROM votes v
        JOIN matches m ON v.match_id = m.match_id
        WHERE v.guild_id = ? AND m.status = 'completed' AND m.winner IS NOT NULL
        """,
        (guild_id,)
    )
    return await cursor.fetchall()

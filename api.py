import asyncio
import aiohttp
from datetime import datetime, timezone, timedelta

BASE_URL = "https://worldcup26.ir"

FINISHED = "finished"
NOT_STARTED = "notstarted"

def _safe_int(val):
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


STADIUM_UTC_OFFSET = {
    "1": -6,
    "2": -6,
    "3": -6,
    "4": -5,
    "5": -5,
    "6": -5,
    "7": -4,
    "8": -4,
    "9": -4,
    "10": -4,
    "11": -4,
    "12": -4,
    "13": -7,
    "14": -7,
    "15": -7,
    "16": -7,
}


async def get_all_games(session: aiohttp.ClientSession, retries: int = 3) -> list:
    url = f"{BASE_URL}/get/games"
    for attempt in range(retries):
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise Exception(f"HTTP {resp.status}")
                data = await resp.json(content_type=None)
            return data.get("games", [])
        except Exception as e:
            if attempt < retries - 1:
                wait = (attempt + 1) * 10
                await asyncio.sleep(wait)
            else:
                raise
    return []

async def get_game(session: aiohttp.ClientSession, game_id: str) -> dict | None:
    games = await get_all_games(session)
    for g in games:
        if g["id"] == game_id:
            return g
    return None


def parse_game(game: dict) -> dict:
    date_str = game["local_date"]
    local_dt = datetime.strptime(date_str, "%m/%d/%Y %H:%M")
    stadium_id = game["stadium_id"]
    offset_hours = STADIUM_UTC_OFFSET.get(stadium_id, 0)
    utc_dt = local_dt - timedelta(hours=offset_hours)
    utc_dt = utc_dt.replace(tzinfo=timezone.utc)
    return {
        "match_id": game["id"],
        "kickoff": int(utc_dt.timestamp()),
        "date_str": date_str,
        "home_team": game.get("home_team_name_en") or game.get("home_team_label", "TBD"),
        "away_team": game.get("away_team_name_en") or game.get("away_team_label", "TBD"),
        "home_team_id": game["home_team_id"],
        "away_team_id": game["away_team_id"],
        "home_goals": _safe_int(game["home_score"]),
        "away_goals": _safe_int(game["away_score"]),
        "group": game["group"],
        "matchday": game["matchday"],
        "type": game["type"],
        "stadium_id": stadium_id,
        "finished": game["finished"] == "TRUE",
        "time_elapsed": game["time_elapsed"],
        "home_scorers": game.get("home_scorers"),
        "away_scorers": game.get("away_scorers"),
        "home_label": game.get("home_team_label"),
        "away_label": game.get("away_team_label"),
    }


def is_live(time_elapsed: str) -> bool:
    return time_elapsed not in (NOT_STARTED, FINISHED)


def format_type(match_type: str) -> str:
    mapping = {
        "group": "Group Stage",
        "r32": "Round of 32",
        "r16": "Round of 16",
        "qf": "Quarter-finals",
        "sf": "Semi-finals",
        "third": "Third Place",
        "final": "Final",
    }
    return mapping.get(match_type, match_type)

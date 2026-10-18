from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
from typing import Any, Dict, Optional
import logging
import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("oracle-scraper")

app = FastAPI(title="Oracle AI Scraper", version="4.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_cache: Dict[str, Any] = {}
_CACHE_TTL = 60

def cache_get(key: str) -> Optional[Any]:
    entry = _cache.get(key)
    if entry and (datetime.now() - entry["time"]).total_seconds() < _CACHE_TTL:
        return entry["data"]
    return None

def cache_set(key: str, data: Any) -> None:
    _cache[key] = {"data": data, "time": datetime.now()}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://www.fotmob.com/",
}

async def fetch_fotmob(path: str) -> Dict:
    cache_key = f"fotmob:{path}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    url = f"https://www.fotmob.com/api/data{path}"
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url, headers=HEADERS, timeout=15.0)
            if resp.status_code == 200:
                data = resp.json()
                cache_set(cache_key, data)
                return data
            else:
                raise HTTPException(status_code=resp.status_code, detail="FotMob error")
        except Exception as e:
            logger.error(f"Fetch error: {e}")
            raise HTTPException(status_code=502, detail=str(e))

@app.get("/api/matches/date/{date_str}")
async def get_matches_by_date(date_str: str):
    clean_date = date_str.replace("-", "")
    try:
        data = await fetch_fotmob(f"/matches?date={clean_date}&timezone=UTC")
        return parse_matches(data)
    except Exception:
        return []

@app.get("/api/matches/live")
async def get_live_matches():
    today_str = datetime.now().strftime("%Y%m%d")
    try:
        data = await fetch_fotmob(f"/matches?date={today_str}&timezone=UTC")
        matches = parse_matches(data)
        return [m for m in matches if m["status"] == "LIVE"]
    except Exception:
        return []

def parse_matches(data):
    leagues = data.get("leagues", [])
    result = []
    for league in leagues:
        league_name = league.get("name", "")
        for match in league.get("matches", []):
            status = match.get("status", {})
            is_live = status.get("started") and not status.get("finished")
            is_ft = status.get("finished")
            
            result.append({
                "id": match.get("id"),
                "home": match.get("home", {}).get("name", ""),
                "away": match.get("away", {}).get("name", ""),
                "homeLogo": f"https://images.fotmob.com/image_resources/logo/teamlogo/{match.get('home', {}).get('id')}.png",
                "awayLogo": f"https://images.fotmob.com/image_resources/logo/teamlogo/{match.get('away', {}).get('id')}.png",
                "homeScore": match.get("home", {}).get("score"),
                "awayScore": match.get("away", {}).get("score"),
                "status": "LIVE" if is_live else ("FT" if is_ft else "NS"),
                "startTimestamp": status.get("utcTime"),
                "league": league_name,
                "minute": status.get("liveTime", {}).get("short", "").replace('\u200e', '')
            })
    return result

@app.get("/api/match/{event_id}")
async def get_match_detail(event_id: str):
    try:
        data = await fetch_fotmob(f"/matchDetails?matchId={event_id}")
    except Exception:
        # Безопасный фолбэк для будущих или экзотических матчей
        return {
            "event": {"id": event_id, "home": "Хозяева", "away": "Гости", "status": "NS", "homeScore": 0, "awayScore": 0, "minute": "", "league": "Матч"}, 
            "statistics": []
        }

    general = data.get("general", {})
    header = data.get("header", {})
    content = data.get("content", {})
    
    stats = []
    try:
        stats = content.get("stats", {}).get("Periods", {}).get("All", {}).get("stats", [])
    except:
        pass
    
    teams = header.get("teams", [{}, {}])
    home = teams[0] if len(teams) > 0 else {}
    away = teams[1] if len(teams) > 1 else {}
    
    return {
        "event": {
            "id": general.get("matchId", event_id),
            "home": home.get("name", "Хозяева"),
            "away": away.get("name", "Гости"),
            "homeLogo": f"https://images.fotmob.com/image_resources/logo/teamlogo/{home.get('id')}.png" if home.get('id') else "",
            "awayLogo": f"https://images.fotmob.com/image_resources/logo/teamlogo/{away.get('id')}.png" if away.get('id') else "",
            "homeScore": home.get("score", 0),
            "awayScore": away.get("score", 0),
            "status": "LIVE" if general.get("matchTimeUTC") and not general.get("finished") else ("FT" if general.get("finished") else "NS"),
            "minute": general.get("matchTimeUTC", ""),
            "league": general.get("leagueName", "Матч")
        },
        "statistics": stats
    }

@app.get("/")
async def health():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
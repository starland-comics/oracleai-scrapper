from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
from typing import Any, Dict, Optional
import logging
import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("oracle-scraper")

app = FastAPI(title="Oracle AI Scraper (FotMob)", version="3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Кэш
_cache: Dict[str, Any] = {}
_CACHE_TTL = 60  # секунд

def cache_get(key: str) -> Optional[Any]:
    entry = _cache.get(key)
    if entry and (datetime.now() - entry["time"]).total_seconds() < _CACHE_TTL:
        return entry["data"]
    return None

def cache_set(key: str, data: Any) -> None:
    _cache[key] = {"data": data, "time": datetime.now()}

# Заголовки для FotMob (он не блокирует сервера так жестко)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

async def fetch_fotmob(path: str) -> Dict:
    cache_key = f"fotmob:{path}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    url = f"https://www.fotmob.com/api{path}"
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url, headers=HEADERS, timeout=10.0)
            if resp.status_code == 200:
                data = resp.json()
                cache_set(cache_key, data)
                return data
            else:
                logger.error(f"FotMob HTTP {resp.status_code} for {path}")
                raise HTTPException(status_code=resp.status_code, detail="FotMob API error")
        except Exception as e:
            logger.error(f"Fetch error: {e}")
            raise HTTPException(status_code=502, detail=str(e))

@app.get("/api/matches/date/{date_str}")
async def get_matches_by_date(date_str: str):
    # FotMob ожидает дату в формате YYYYMMDD (без дефисов)
    clean_date = date_str.replace("-", "")
    data = await fetch_fotmob(f"/matches?date={clean_date}")
    
    # Извлекаем все лиги и матчи
    leagues = data.get("leagues", [])
    result = []
    
    for league in leagues:
        league_name = league.get("name", "")
        for match in league.get("matches", []):
            result.append({
                "id": match.get("id"),
                "home": match.get("home", {}).get("name", ""),
                "away": match.get("away", {}).get("name", ""),
                "homeLogo": f"https://images.fotmob.com/image_resources/logo/teamlogo/{match.get('home', {}).get('id')}.png",
                "awayLogo": f"https://images.fotmob.com/image_resources/logo/teamlogo/{match.get('away', {}).get('id')}.png",
                "homeScore": match.get("home", {}).get("score"),
                "awayScore": match.get("away", {}).get("score"),
                "status": match.get("status", {}).get("utcTime", "") if match.get("status", {}).get("started") is False else "LIVE",
                "startTimestamp": match.get("status", {}).get("utcTime"),
                "league": league_name,
                "minute": match.get("status", {}).get("liveTime", {}).get("short", "")
            })
            
    return result

@app.get("/api/matches/live")
async def get_live_matches():
    # FotMob не имеет прямого эндпоинта "live", 
    # поэтому мы берем матчи на сегодня и фильтруем те, что уже начались
    today_str = datetime.now().strftime("%Y%m%d")
    data = await fetch_fotmob(f"/matches?date={today_str}")
    
    leagues = data.get("leagues", [])
    result = []
    
    for league in leagues:
        league_name = league.get("name", "")
        for match in league.get("matches", []):
            status = match.get("status", {})
            if status.get("started") and not status.get("finished"):
                result.append({
                    "id": match.get("id"),
                    "home": match.get("home", {}).get("name", ""),
                    "away": match.get("away", {}).get("name", ""),
                    "homeLogo": f"https://images.fotmob.com/image_resources/logo/teamlogo/{match.get('home', {}).get('id')}.png",
                    "awayLogo": f"https://images.fotmob.com/image_resources/logo/teamlogo/{match.get('away', {}).get('id')}.png",
                    "homeScore": match.get("home", {}).get("score"),
                    "awayScore": match.get("away", {}).get("score"),
                    "status": "LIVE",
                    "startTimestamp": status.get("utcTime"),
                    "league": league_name,
                    "minute": status.get("liveTime", {}).get("short", "")
                })
    return result

@app.get("/api/match/{event_id}")
async def get_match_detail(event_id: str):
    data = await fetch_fotmob(f"/matchDetails?matchId={event_id}")
    
    # Извлекаем нужные данные из огромного JSON FotMob
    general = data.get("general", {})
    header = data.get("header", {})
    content = data.get("content", {})
    
    # Статистика
    stats = content.get("stats", {}).get("Periods", {}).get("All", {}).get("stats", [])
    # Составы
    lineups = content.get("lineup", {})
    
    return {
        "event": {
            "id": general.get("matchId"),
            "home": header.get("teams", [{}])[0].get("name"),
            "away": header.get("teams", [{}, {}])[1].get("name"),
            "homeLogo": f"https://images.fotmob.com/image_resources/logo/teamlogo/{header.get('teams', [{}])[0].get('id')}.png",
            "awayLogo": f"https://images.fotmob.com/image_resources/logo/teamlogo/{header.get('teams', [{}, {}])[1].get('id')}.png",
            "homeScore": header.get("teams", [{}])[0].get("score"),
            "awayScore": header.get("teams", [{}, {}])[1].get("score"),
            "status": "LIVE" if general.get("matchTimeUTC") else "FT", 
            "minute": general.get("matchTimeUTC", ""),
            "league": general.get("leagueName")
        },
        "statistics": stats,
        "lineups": lineups
    }

@app.get("/api/match/{event_id}/odds")
async def get_match_odds(event_id: str):
    # FotMob хранит коэффициенты внутри деталей матча (в блоке content.odds)
    data = await fetch_fotmob(f"/matchDetails?matchId={event_id}")
    
    # Пытаемся достать коэффициенты 1X2
    odds_data = data.get("content", {}).get("odds", {})
    
    return odds_data

@app.get("/")
async def health():
    return {"status": "ok", "service": "oracle-scraper-fotmob"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

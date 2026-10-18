from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
from typing import Any, Dict, Optional
import logging
import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("oracle-scraper")

app = FastAPI(title="Oracle AI Scraper (FotMob)", version="3.3")

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
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.fotmob.com/",
    "Origin": "https://www.fotmob.com",
}

async def fetch_fotmob(path: str) -> Dict:
    cache_key = f"fotmob:{path}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    # ИСПРАВЛЕНО: добавлен префикс /data/
    url = f"https://www.fotmob.com/api/data{path}"
    logger.info(f"Запрос к FotMob: {url}")

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url, headers=HEADERS, timeout=15.0)
            if resp.status_code == 200:
                data = resp.json()
                cache_set(cache_key, data)
                return data
            else:
                logger.error(f"FotMob HTTP {resp.status_code} for {url}")
                raise HTTPException(status_code=resp.status_code, detail=f"FotMob API вернул {resp.status_code}")
        except Exception as e:
            logger.error(f"Fetch error для {url}: {e}")
            raise HTTPException(status_code=502, detail=str(e))

@app.get("/api/debug/fotmob-test")
async def debug_fotmob():
    """Тестовый эндпоинт с исправленным путём."""
    today_str = datetime.now().strftime("%Y%m%d")
    url = f"https://www.fotmob.com/api/data/matches?date={today_str}&timezone=UTC"
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=HEADERS, timeout=15.0)
            return {
                "status": "success",
                "url": url,
                "status_code": resp.status_code,
                "body_start": resp.text[:300]
            }
    except Exception as e:
        return {
            "status": "error",
            "url": url,
            "error_type": type(e).__name__,
            "message": str(e)
        }

@app.get("/api/matches/date/{date_str}")
async def get_matches_by_date(date_str: str):
    clean_date = date_str.replace("-", "")
    # ИСПРАВЛЕНО: /data/matches с параметром timezone
    data = await fetch_fotmob(f"/matches?date={clean_date}&timezone=UTC")
    
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
                "status": "LIVE" if match.get("status", {}).get("started") and not match.get("status", {}).get("finished") else ("FT" if match.get("status", {}).get("finished") else "NS"),
                "startTimestamp": match.get("status", {}).get("utcTime"),
                "league": league_name,
                "minute": match.get("status", {}).get("liveTime", {}).get("short", "")
            })
    return result

@app.get("/api/matches/live")
async def get_live_matches():
    today_str = datetime.now().strftime("%Y%m%d")
    data = await fetch_fotmob(f"/matches?date={today_str}&timezone=UTC")
    
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
    # ИСПРАВЛЕНО: /data/matchDetails с параметром matchId
    data = await fetch_fotmob(f"/matchDetails?matchId={event_id}")
    
    general = data.get("general", {})
    header = data.get("header", {})
    content = data.get("content", {})
    
    stats = content.get("stats", {}).get("Periods", {}).get("All", {}).get("stats", [])
    
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
        "lineups": content.get("lineup", {})
    }

@app.get("/")
async def health():
    return {"status": "ok", "service": "oracle-scraper-fotmob-v3.3"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
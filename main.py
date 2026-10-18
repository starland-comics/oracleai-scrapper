import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import async_playwright
from curl_cffi import requests as cffi_requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("oracle-scraper")

app = FastAPI(title="Oracle AI Scraper", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ──────────────────────────────────────────────
# Кэш (чтобы не дёргать SofaScore лишний раз)
# ──────────────────────────────────────────────
_cache: Dict[str, Any] = {}
_CACHE_TTL = 30  # секунд

def cache_get(key: str) -> Optional[Any]:
    entry = _cache.get(key)
    if entry and (datetime.now() - entry["time"]).total_seconds() < _CACHE_TTL:
        return entry["data"]
    return None

def cache_set(key: str, data: Any) -> None:
    _cache[key] = {"data": data, "time": datetime.now()}

# ──────────────────────────────────────────────
# Слой 1: curl_cffi с правильными заголовками
# ──────────────────────────────────────────────
def fetch_via_curl(path: str) -> Optional[Dict]:
    """Пытается получить данные через curl_cffi с TLS-имитацией Chrome."""
    url = f"https://api.sofascore.com/api/v1{path}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.sofascore.com/",
        "x-requested-with": "XMLHttpRequest",  # ← Критически важно
    }
    try:
        resp = cffi_requests.get(url, headers=headers, impersonate="chrome124", timeout=10)
        if resp.status_code == 200:
            return resp.json()
        logger.warning(f"[curl_cffi] HTTP {resp.status_code} для {path}")
    except Exception as e:
        logger.warning(f"[curl_cffi] Ошибка: {e}")
    return None

# ──────────────────────────────────────────────
# Слой 2: Headless-браузер (Playwright) — fallback
# ──────────────────────────────────────────────
async def fetch_via_playwright(path: str) -> Optional[Dict]:
    """Открывает реальный браузер, чтобы обойти Cloudflare challenge."""
    url = f"https://api.sofascore.com/api/v1{path}"
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
                viewport={"width": 1920, "height": 1080},
            )
            page = await context.new_page()
            # Заходим на главную, чтобы получить куки
            await page.goto("https://www.sofascore.com/", timeout=15000)
            # Ждём, пока Cloudflare пропустит
            await page.wait_for_timeout(2000)
            # Теперь запрашиваем API с правильными заголовками
            response = await page.evaluate(f"""
                async () => {{
                    const res = await fetch('{url}', {{
                        headers: {{ 'x-requested-with': 'XMLHttpRequest' }}
                    }});
                    if (!res.ok) return {{ error: res.status }};
                    return await res.json();
                }}
            """)
            await browser.close()
            if isinstance(response, dict) and "error" not in response:
                return response
            logger.warning(f"[Playwright] Ошибка: {response}")
    except Exception as e:
        logger.warning(f"[Playwright] Исключение: {e}")
    return None

# ──────────────────────────────────────────────
# Универсальный загрузчик (сначала curl, потом браузер)
# ──────────────────────────────────────────────
async def fetch_sofascore(path: str) -> Dict:
    cache_key = f"sofascore:{path}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    loop = asyncio.get_event_loop()

    # 1. Пробуем curl_cffi
    data = await loop.run_in_executor(None, fetch_via_curl, path)
    if data:
        cache_set(cache_key, data)
        return data

    # 2. Пробуем Playwright
    logger.info(f"Переключаюсь на Playwright для {path}")
    data = await fetch_via_playwright(path)
    if data:
        cache_set(cache_key, data)
        return data

    raise HTTPException(status_code=502, detail="Не удалось обойти Cloudflare ни одним методом")

# ──────────────────────────────────────────────
# Эндпоинты
# ──────────────────────────────────────────────
@app.get("/api/matches/live")
async def get_live_matches():
    data = await fetch_sofascore("/sport/football/events/live")
    events = data.get("events", [])
    result = []
    for ev in events:
        result.append({
            "id": ev.get("id"),
            "home": ev.get("homeTeam", {}).get("name", ""),
            "away": ev.get("awayTeam", {}).get("name", ""),
            "homeLogo": ev.get("homeTeam", {}).get("logo", ""),
            "awayLogo": ev.get("awayTeam", {}).get("logo", ""),
            "homeScore": ev.get("homeScore", {}).get("current"),
            "awayScore": ev.get("awayScore", {}).get("current"),
            "status": ev.get("status", {}).get("code", "LIVE"),
            "minute": ev.get("time", {}).get("currentPeriodStartTimestamp"),
            "league": ev.get("tournament", {}).get("name", ""),
            "startTimestamp": ev.get("startTimestamp"),
        })
    return result

@app.get("/api/matches/date/{date_str}")
async def get_matches_by_date(date_str: str):
    data = await fetch_sofascore(f"/sport/football/scheduled-events/{date_str}")
    events = data.get("events", [])
    result = []
    for ev in events:
        result.append({
            "id": ev.get("id"),
            "home": ev.get("homeTeam", {}).get("name", ""),
            "away": ev.get("awayTeam", {}).get("name", ""),
            "homeLogo": ev.get("homeTeam", {}).get("logo", ""),
            "awayLogo": ev.get("awayTeam", {}).get("logo", ""),
            "homeScore": ev.get("homeScore", {}).get("current"),
            "awayScore": ev.get("awayScore", {}).get("current"),
            "status": ev.get("status", {}).get("code", "NS"),
            "minute": ev.get("time", {}).get("currentPeriodStartTimestamp"),
            "league": ev.get("tournament", {}).get("name", ""),
            "startTimestamp": ev.get("startTimestamp"),
        })
    return result

@app.get("/api/match/{event_id}")
async def get_match_detail(event_id: int):
    event_data = await fetch_sofascore(f"/event/{event_id}")
    stats_data = await fetch_sofascore(f"/event/{event_id}/statistics")
    lineups_data = await fetch_sofascore(f"/event/{event_id}/lineups")

    event = event_data.get("event", {})
    return {
        "event": {
            "id": event.get("id"),
            "home": event.get("homeTeam", {}).get("name"),
            "away": event.get("awayTeam", {}).get("name"),
            "homeLogo": event.get("homeTeam", {}).get("logo"),
            "awayLogo": event.get("awayTeam", {}).get("logo"),
            "homeScore": event.get("homeScore", {}).get("current"),
            "awayScore": event.get("awayScore", {}).get("current"),
            "status": event.get("status", {}).get("code"),
            "minute": event.get("time", {}).get("currentPeriodStartTimestamp"),
            "league": event.get("tournament", {}).get("name"),
            "startTimestamp": event.get("startTimestamp"),
        },
        "statistics": stats_data.get("statistics", []),
        "lineups": lineups_data.get("lineups", []),
    }

@app.get("/api/match/{event_id}/odds")
async def get_match_odds(event_id: int):
    data = await fetch_sofascore(f"/event/{event_id}/odds")
    return data

@app.get("/")
async def health():
    return {"status": "ok", "service": "oracle-ai-scraper-v2"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

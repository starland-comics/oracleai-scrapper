"""
Oracle AI Scraper — FastAPI-прокси для SofaScore.
Развертывается на Render (бесплатный тариф).
Обходит защиту Cloudflare с помощью curl_cffi (имитация браузера Chrome).
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
import asyncio
import json
import logging

# Импортируем клиент pysofascore
# (библиотека использует curl_cffi для обхода защиты)
try:
    from sofascore_api import SofaScoreClient
except ImportError:
    # Если библиотека называется иначе (например, pysofascore), 
    # попробуйте: from pysofascore import SofaScoreClient
    from pysofascore import SofaScoreClient

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("oracle-scraper")

app = FastAPI(title="Oracle AI Scraper", version="1.0")

# Разрешаем CORS для GitHub Pages (ваш фронтенд)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Для продакшена лучше указать конкретный домен
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Глобальный клиент SofaScore (создается один раз)
client: Optional[SofaScoreClient] = None

# Простой кэш в памяти (чтобы не долбить SofaScore лишний раз)
_cache: Dict[str, Any] = {}
_CACHE_TTL = 30  # секунд

def get_client() -> SofaScoreClient:
    """Ленивая инициализация клиента."""
    global client
    if client is None:
        client = SofaScoreClient(
            timeout=15,
            retries=3,
            retry_delay=1.0,
        )
    return client

def cache_get(key: str) -> Optional[Any]:
    entry = _cache.get(key)
    if entry and (datetime.now() - entry["time"]).total_seconds() < _CACHE_TTL:
        return entry["data"]
    return None

def cache_set(key: str, data: Any) -> None:
    _cache[key] = {"data": data, "time": datetime.now()}


# ──────────────────────────────────────────────
# 1. Живые матчи (Live)
# ──────────────────────────────────────────────
@app.get("/api/matches/live")
async def get_live_matches():
    """Возвращает список текущих live-матчей по футболу."""
    cache_key = "live_matches"
    cached = cache_get(cache_key)
    if cached:
        return cached

    try:
        loop = asyncio.get_event_loop()
        events = await loop.run_in_executor(
            None, lambda: get_client().get_live_events("football")
        )
        # Нормализуем для фронтенда
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
        cache_set(cache_key, result)
        return result
    except Exception as e:
        logger.error(f"Live matches error: {e}")
        raise HTTPException(status_code=502, detail=str(e))


# ──────────────────────────────────────────────
# 2. Матчи по дате
# ──────────────────────────────────────────────
@app.get("/api/matches/date/{date_str}")
async def get_matches_by_date(date_str: str):
    """
    Возвращает матчи на указанную дату (формат YYYY-MM-DD).
    """
    cache_key = f"matches_{date_str}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    try:
        loop = asyncio.get_event_loop()
        events = await loop.run_in_executor(
            None, lambda: get_client().get_events_by_date("football", date_str)
        )
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
        cache_set(cache_key, result)
        return result
    except Exception as e:
        logger.error(f"Matches by date error: {e}")
        raise HTTPException(status_code=502, detail=str(e))


# ──────────────────────────────────────────────
# 3. Детали матча (основная информация + статистика + составы + xG)
# ──────────────────────────────────────────────
@app.get("/api/match/{event_id}")
async def get_match_detail(event_id: int):
    """
    Возвращает полную информацию о матче: счет, статус, статистику, составы, xG.
    """
    cache_key = f"match_{event_id}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    try:
        loop = asyncio.get_event_loop()
        # Получаем событие (детали)
        event = await loop.run_in_executor(
            None, lambda: get_client().get_event(event_id)
        )
        # Статистика (включая xG)
        stats = await loop.run_in_executor(
            None, lambda: get_client().get_event_statistics(event_id)
        )
        # Составы
        lineups = await loop.run_in_executor(
            None, lambda: get_client().get_event_lineups(event_id)
        )

        result = {
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
            "statistics": stats,   # Сырые данные статистики SofaScore
            "lineups": lineups,    # Сырые данные составов
        }
        cache_set(cache_key, result)
        return result
    except Exception as e:
        logger.error(f"Match detail error: {e}")
        raise HTTPException(status_code=502, detail=str(e))


# ──────────────────────────────────────────────
# 4. Коэффициенты (Odds) — прямой запрос к API SofaScore
# ──────────────────────────────────────────────
@app.get("/api/match/{event_id}/odds")
async def get_match_odds(event_id: int):
    """
    Возвращает коэффициенты букмекеров для матча.
    Использует внутренний эндпоинт SofaScore (/event/{id}/odds).
    """
    cache_key = f"odds_{event_id}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    try:
        # pysofascore может не иметь метода для odds, поэтому делаем прямой запрос
        # через тот же клиент (curl_cffi с браузерным отпечатком).
        # Если в будущем pysofascore добавит метод, можно заменить.
        loop = asyncio.get_event_loop()
        # Пробуем получить odds через клиент (если метод есть)
        # Если нет — используем requests с curl_cffi
        try:
            odds = await loop.run_in_executor(
                None, lambda: get_client().get_event_odds(event_id)
            )
        except AttributeError:
            # Fallback: прямой запрос через curl_cffi
            from curl_cffi import requests as cffi_requests
            url = f"https://api.sofascore.com/api/v1/event/{event_id}/odds"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json",
                "Referer": "https://www.sofascore.com/",
            }
            resp = await loop.run_in_executor(
                None, lambda: cffi_requests.get(url, headers=headers, impersonate="chrome120")
            )
            if resp.status_code == 200:
                odds = resp.json()
            elif resp.status_code == 404:
                odds = {"error": "Odds not available for this match"}
            else:
                raise HTTPException(status_code=resp.status_code, detail="Failed to fetch odds")

        cache_set(cache_key, odds)
        return odds
    except Exception as e:
        logger.error(f"Odds error: {e}")
        raise HTTPException(status_code=502, detail=str(e))


# ──────────────────────────────────────────────
# 5. Поиск (для автодополнения)
# ──────────────────────────────────────────────
@app.get("/api/search")
async def search(q: str):
    """Поиск команд или турниров по названию."""
    cache_key = f"search_{q}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    try:
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(
            None, lambda: get_client().search(q)
        )
        cache_set(cache_key, results)
        return results
    except Exception as e:
        logger.error(f"Search error: {e}")
        raise HTTPException(status_code=502, detail=str(e))


# ──────────────────────────────────────────────
# 6. Health-check (для Render)
# ──────────────────────────────────────────────
@app.get("/")
async def health():
    return {"status": "ok", "service": "oracle-ai-scraper"}


# ──────────────────────────────────────────────
# Запуск (для локального тестирования)
# ──────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

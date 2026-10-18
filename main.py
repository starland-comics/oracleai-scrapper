from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
from typing import Any, Dict, Optional
import logging
import httpx
import numpy as np
import json

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("oracle-scraper")

app = FastAPI(title="Oracle AI Luxury", version="6.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ⚠️ ВАШИ КЛЮЧИ ТЕПЕРЬ В БЕЗОПАСНОСТИ НА СЕРВЕРЕ
GROQ_API_KEY = "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"
BSD_TOKEN = "c381d2ea1e1569e460c19f762aa5cd8438da4c86"

_cache: Dict[str, Any] = {}
_CACHE_TTL = 300 

def cache_get(key: str) -> Optional[Any]:
    entry = _cache.get(key)
    if entry and (datetime.now() - entry["time"]).total_seconds() < _CACHE_TTL:
        return entry["data"]
    return None

def cache_set(key: str, data: Any) -> None:
    _cache[key] = {"data": data, "time": datetime.now()}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.fotmob.com/",
}

async def fetch_fotmob(path: str) -> Dict:
    cache_key = f"fotmob:{path}"
    cached = cache_get(cache_key)
    if cached: return cached

    url = f"https://www.fotmob.com/api/data{path}"
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url, headers=HEADERS, timeout=15.0)
            if resp.status_code == 200:
                data = resp.json()
                cache_set(cache_key, data)
                return data
        except Exception as e:
            logger.error(f"FotMob error: {e}")
    return {}

@app.get("/api/matches/live")
async def get_live_matches():
    today_str = datetime.now().strftime("%Y%m%d")
    data = await fetch_fotmob(f"/matches?date={today_str}&timezone=UTC")
    return parse_matches(data, "LIVE")

@app.get("/api/matches/date/{date_str}")
async def get_matches_by_date(date_str: str):
    clean_date = date_str.replace("-", "")
    data = await fetch_fotmob(f"/matches?date={clean_date}&timezone=UTC")
    return parse_matches(data, "ALL")

def parse_matches(data, filter_type):
    leagues = data.get("leagues", [])
    result = []
    for league in leagues:
        for match in league.get("matches", []):
            status = match.get("status", {})
            is_live = status.get("started") and not status.get("finished")
            is_ft = status.get("finished")
            
            if filter_type == "LIVE" and not is_live: continue
            
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
                "league": league.get("name", ""),
                "minute": status.get("liveTime", {}).get("short", "").replace('\u200e', '')
            })
    return result

@app.get("/api/match/{event_id}")
async def get_match_detail(event_id: str):
    data = await fetch_fotmob(f"/matchDetails?matchId={event_id}")
    general = data.get("general", {})
    header = data.get("header", {})
    content = data.get("content", {})
    
    stats = content.get("stats", {}).get("Periods", {}).get("All", {}).get("stats", [])
    
    # H2H
    h2h = []
    try:
        for m in content.get("h2h", {}).get("matches", [])[-5:]:
            h2h.append(f"{m.get('time', {}).get('utcTime', '')[:10]}: {m.get('home', {}).get('name')} {m.get('home', {}).get('score')}:{m.get('away', {}).get('score')} {m.get('away', {}).get('name')}")
    except: pass

    # Травмы
    missing = {"home": [], "away": []}
    try:
        lineup = content.get("lineup", {})
        missing["home"] = [p.get("name") for p in lineup.get("homeTeam", {}).get("missingPlayers", [])]
        missing["away"] = [p.get("name") for p in lineup.get("awayTeam", {}).get("missingPlayers", [])]
    except: pass

    teams = header.get("teams", [{}, {}])
    
    return {
        "event": {
            "id": general.get("matchId", event_id),
            "home": teams[0].get("name", "Хозяева"),
            "away": teams[1].get("name", "Гости"),
            "homeLogo": f"https://images.fotmob.com/image_resources/logo/teamlogo/{teams[0].get('id')}.png",
            "awayLogo": f"https://images.fotmob.com/image_resources/logo/teamlogo/{teams[1].get('id')}.png",
            "homeScore": teams[0].get("score", 0),
            "awayScore": teams[1].get("score", 0),
            "status": "LIVE" if general.get("matchTimeUTC") and not general.get("finished") else ("FT" if general.get("finished") else "NS"),
            "minute": general.get("matchTimeUTC", ""),
            "league": general.get("leagueName", "Матч")
        },
        "statistics": stats,
        "h2h": h2h,
        "missing": missing
    }

@app.post("/api/oracle/{event_id}")
async def run_oracle(event_id: str):
    # 1. Получаем данные матча
    match_data = await get_match_detail(event_id)
    fx = match_data["event"]
    
    # 2. Ищем коэффициенты в BSD (поиск по названиям команд)
    bsd_odds = "Нет данных"
    try:
        async with httpx.AsyncClient() as client:
            search_q = f"{fx['home']} {fx['away']}"
            res = await client.get(f"https://sports.bzzoiro.com/api/v2/events/?search={search_q}", headers={"Authorization": f"Token {BSD_TOKEN}"}, timeout=10)
            if res.status_code == 200 and res.json().get("results"):
                bsd_event_id = res.json()["results"][0]["id"]
                odds_res = await client.get(f"https://sports.bzzoiro.com/api/v2/events/{bsd_event_id}/odds/", headers={"Authorization": f"Token {BSD_TOKEN}"})
                if odds_res.status_code == 200:
                    bsd_odds = str(odds_res.json())[:500] # Берем кусок для ИИ
    except Exception as e:
        logger.warning(f"BSD odds error: {e}")

    # 3. Математика (Poisson на основе xG)
    xg_h, xg_a = 1.35, 1.15
    for s in match_data["statistics"]:
        if s.get("name") == "Expected goals (xG)":
            xg_h = float(s["stats"][0]) if s["stats"][0] != "-" else xg_h
            xg_a = float(s["stats"][1]) if s["stats"][1] != "-" else xg_a

    gh, ga = fx["homeScore"], fx["awayScore"]
    time_left = 1.0 if fx["status"] == "NS" else 0.0
    if fx["status"] == "LIVE":
        min_str = fx["minute"].replace("'", "")
        minute = int(min_str) if min_str.isdigit() else 45
        time_left = max(0, (90 - minute) / 90)

    lambda_h = max(0.01, xg_h * time_left)
    lambda_a = max(0.01, xg_a * time_left)
    if gh > ga: lambda_a *= 1.2
    if ga > gh: lambda_h *= 1.2

    probs = {"p1": 0, "px": 0, "p2": 0, "over25": 0, "btts": 0}
    for h in range(6):
        for a in range(6):
            p = (np.exp(-lambda_h) * (lambda_h**h) / np.math.factorial(h)) * (np.exp(-lambda_a) * (lambda_a**a) / np.math.factorial(a))
            fh, fa = gh + h, ga + a
            if fh > fa: probs["p1"] += p
            elif fh < fa: probs["p2"] += p
            else: probs["px"] += p
            if fh + fa > 2.5: probs["over25"] += p
            if fh > 0 and fa > 0: probs["btts"] += p

    total_p = probs["p1"] + probs["px"] + probs["p2"]
    for k in probs: probs[k] /= total_p

    # 4. Промпт для Llama 3 (Люкс-версия)
    prompt = f"""
    Ты профессиональный футбольный аналитик-синдикатор. Сделай элитный разбор матча.
    Матч: {fx['home']} против {fx['away']}.
    Счет: {gh}:{ga}. Статус: {fx['status']} ({fx['minute']}).
    xG команд: {xg_h} vs {xg_a}.
    Травмы/Дисквалификации: {json.dumps(match_data['missing'])}.
    Последние встречи (H2H): {json.dumps(match_data['h2h'])}.
    Коэффициенты BSD: {bsd_odds}.
    Математические вероятности (Пуассон): П1:{probs['p1']*100:.1f}%, X:{probs['px']*100:.1f}%, П2:{probs['p2']*100:.1f}%, ТБ2.5:{probs['over25']*100:.1f}%, ОЗ:{probs['btts']*100:.1f}%.

    Верни СТРОГО JSON (без markdown) в таком формате:
    {{
        "main_pick": "Самый вероятный исход из списка (напр. П2 или ТБ 2.5)",
        "confidence": "Высокая/Средняя/Низкая",
        "analysis_text": "Подробный анализ на 4 абзаца. Разбери форму, H2H, травмы и почему математика указывает на этот исход. Пиши как эксперт.",
        "risk_text": "Главный риск этого прогноза (1 предложение)",
        "alt_text": "Альтернативный исход и почему он имеет смысл (1-2 предложения)"
    }}
    """

    async with httpx.AsyncClient() as client:
        ai_res = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": "llama3-8b-8192",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.6,
                "response_format": {"type": "json_object"}
            },
            timeout=25.0
        )
    
    ai_data = ai_res.json().get("choices", [{}])[0].get("message", {}).get("content", "{}")
    return json.loads(ai_data)

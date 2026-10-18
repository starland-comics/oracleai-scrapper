from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
from typing import Any, Dict, Optional
import logging
import httpx
import json
import math  # Используем встроенный math вместо numpy

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("oracle-scraper")

app = FastAPI(title="Oracle AI Luxury", version="6.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ВАШИ КЛЮЧИ
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
            resp = await client.get(url, headers=HEADERS, timeout=10.0)
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
    if not data:
        # Если FotMob не ответил, отдаем заглушку, чтобы фронтенд не падал
        return {"event": {"id": event_id, "home": "Хозяева", "away": "Гости", "status": "NS", "homeScore": 0, "awayScore": 0, "minute": "", "league": "Матч"}, "statistics": [], "h2h": [], "missing": {"home": [], "away": []}}

    general = data.get("general", {})
    header = data.get("header", {})
    content = data.get("content", {})
    
    stats = content.get("stats", {}).get("Periods", {}).get("All", {}).get("stats", [])
    
    h2h = []
    try:
        for m in content.get("h2h", {}).get("matches", [])[-5:]:
            h2h.append(f"{m.get('time', {}).get('utcTime', '')[:10]}: {m.get('home', {}).get('name')} {m.get('home', {}).get('score')}:{m.get('away', {}).get('score')} {m.get('away', {}).get('name')}")
    except: pass

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
            "homeLogo": f"https://images.fotmob.com/image_resources/logo/teamlogo/{teams[0].get('id')}.png" if teams[0].get('id') else "",
            "awayLogo": f"https://images.fotmob.com/image_resources/logo/teamlogo/{teams[1].get('id')}.png" if teams[1].get('id') else "",
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
    match_data = await get_match_detail(event_id)
    fx = match_data["event"]
    
    # 1. Математика (ИСПРАВЛЕНО: используем math.factorial)
    xg_h, xg_a = 1.35, 1.15
    for s in match_data["statistics"]:
        if s.get("name") == "Expected goals (xG)":
            try:
                xg_h = float(s["stats"][0]) if s["stats"][0] != "-" else xg_h
                xg_a = float(s["stats"][1]) if s["stats"][1] != "-" else xg_a
            except: pass

    gh, ga = fx["homeScore"], fx["awayScore"]
    time_left = 1.0 if fx["status"] == "NS" else 0.0
    if fx["status"] == "LIVE":
        min_str = str(fx["minute"]).replace("'", "").replace("HT", "45")
        minute = int(min_str) if min_str.isdigit() else 45
        time_left = max(0, (90 - minute) / 90)

    lambda_h = max(0.01, xg_h * time_left)
    lambda_a = max(0.01, xg_a * time_left)
    if gh > ga: lambda_a *= 1.2
    if ga > gh: lambda_h *= 1.2

    probs = {"p1": 0, "px": 0, "p2": 0, "over25": 0, "btts": 0}
    
    # Используем встроенный math
    for h in range(6):
        for a in range(6):
            p_h = (math.exp(-lambda_h) * (lambda_h**h)) / math.factorial(h)
            p_a = (math.exp(-lambda_a) * (lambda_a**a)) / math.factorial(a)
            p = p_h * p_a
            
            fh, fa = gh + h, ga + a
            if fh > fa: probs["p1"] += p
            elif fh < fa: probs["p2"] += p
            else: probs["px"] += p
            if fh + fa > 2.5: probs["over25"] += p
            if fh > 0 and fa > 0: probs["btts"] += p

    total_p = probs["p1"] + probs["px"] + probs["p2"]
    for k in probs: probs[k] /= total_p

    # 2. Промпт
    prompt = f"""
    Ты профессиональный футбольный аналитик. Сделай элитный разбор матча.
    Матч: {fx['home']} против {fx['away']}.
    Счет: {gh}:{ga}. Статус: {fx['status']} ({fx['minute']}).
    xG: {xg_h} vs {xg_a}.
    Травмы: {json.dumps(match_data['missing'])}.
    H2H: {json.dumps(match_data['h2h'])}.
    Вероятности модели: П1:{probs['p1']*100:.1f}%, X:{probs['px']*100:.1f}%, П2:{probs['p2']*100:.1f}%, ТБ2.5:{probs['over25']*100:.1f}%, ОЗ:{probs['btts']*100:.1f}%.

    Верни СТРОГО JSON:
    {{
        "main_pick": "Лучший исход",
        "confidence": "Высокая/Средняя/Низкая",
        "analysis_text": "Подробный анализ на 4 абзаца.",
        "risk_text": "Главный риск (1 предложение)",
        "alt_text": "Альтернатива (1-2 предложения)"
    }}
    """

    try:
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
                timeout=15.0
            )
        
        ai_data = ai_res.json().get("choices", [{}])[0].get("message", {}).get("content", "{}")
        
        # Очистка от маркдауна, если ИИ его добавил
        ai_data = ai_data.replace("```json", "").replace("```", "").strip()
        return json.loads(ai_data)
    except Exception as e:
        logger.error(f"Oracle AI error: {e}")
        # Если ИИ упал, отдаем фолбэк, чтобы фронтенд не показывал "Сервер занят"
        return {
            "main_pick": "Ошибка ИИ",
            "confidence": "Низкая",
            "analysis_text": "Не удалось получить ответ от нейросети. Попробуйте позже.",
            "risk_text": "-",
            "alt_text": "-"
        }

@app.get("/")
async def health():
    return {"status": "ok"}
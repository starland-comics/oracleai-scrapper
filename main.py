from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
from typing import Any, Dict, Optional
import logging
import httpx
import json
import math

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("oracle-scraper")

app = FastAPI(title="Oracle AI Luxury", version="6.2")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GROQ_API_KEY = "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"
BSD_TOKEN = "c381d2ea1e1569e460c19f762aa5cd8438da4c86"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.fotmob.com/",
}

async def fetch_fotmob(path: str) -> Dict:
    url = f"https://www.fotmob.com/api/data{path}"
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url, headers=HEADERS, timeout=15.0)
            if resp.status_code == 200:
                return resp.json()
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
    leagues = data.get("leagues") or []
    result = []
    for league in leagues:
        for match in league.get("matches") or []:
            status = match.get("status") or {}
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
        return {"event": {"id": event_id, "home": "Хозяева", "away": "Гости", "status": "NS", "homeScore": 0, "awayScore": 0, "minute": "", "league": "Матч"}, "statistics": [], "h2h": [], "missing": {"home": [], "away": []}}

    general = data.get("general") or {}
    header = data.get("header") or {}
    content = data.get("content") or {}
    
    stats = (content.get("stats") or {}).get("Periods", {}).get("All", {}).get("stats") or []
    
    h2h = []
    try:
        for m in (content.get("h2h") or {}).get("matches", [])[-5:]:
            h2h.append(f"{m.get('time', {}).get('utcTime', '')[:10]}: {m.get('home', {}).get('name')} {m.get('home', {}).get('score')}:{m.get('away', {}).get('score')} {m.get('away', {}).get('name')}")
    except: pass

    missing = {"home": [], "away": []}
    try:
        lineup = content.get("lineup") or {}
        missing["home"] = [p.get("name") for p in (lineup.get("homeTeam") or {}).get("missingPlayers") or []]
        missing["away"] = [p.get("name") for p in (lineup.get("awayTeam") or {}).get("missingPlayers") or []]
    except: pass

    teams = header.get("teams") or [{}, {}]
    if len(teams) < 2: teams = [{}, {}]
    
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
    
    # Математика
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

    fallback_data = {
        "main_pick": f"П1 ({fx['home']})" if probs['p1'] > probs['p2'] else f"П2 ({fx['away']})",
        "confidence": "Средняя",
        "analysis_text": f"Нейросеть временно недоступна. Математическая модель на основе xG ({xg_h} vs {xg_a}) и оставшегося времени показывает вероятности: П1 - {(probs['p1']*100):.0f}%, X - {(probs['px']*100):.0f}%, П2 - {(probs['p2']*100):.0f}%.",
        "risk_text": "Возможны ошибки из-за отсутствия глубокого ИИ-анализа.",
        "alt_text": "Рекомендуется дождаться восстановления ИИ."
    }

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
                timeout=20.0
            )
        
        ai_text = ai_res.json().get("choices", [{}])[0].get("message", {}).get("content", "{}")
        
        # Очистка от маркдауна
        if "```json" in ai_text:
            ai_text = ai_text.split("```json")[1].split("```")[0]
        elif "```" in ai_text:
            ai_text = ai_text.split("```")[1].split("```")[0]
            
        parsed = json.loads(ai_text.strip())
        
        return {
            "main_pick": parsed.get("main_pick") or fallback_data["main_pick"],
            "confidence": parsed.get("confidence") or fallback_data["confidence"],
            "analysis_text": parsed.get("analysis_text") or fallback_data["analysis_text"],
            "risk_text": parsed.get("risk_text") or fallback_data["risk_text"],
            "alt_text": parsed.get("alt_text") or fallback_data["alt_text"]
        }
    except Exception as e:
        logger.error(f"Oracle AI error: {e}")
        return fallback_data

@app.get("/")
async def health():
    return {"status": "ok"}
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

app = FastAPI(title="Oracle AI Luxury", version="7.1")

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
    
    # 1. УМНЫЙ РАСЧЕТ xG
    xg_h, xg_a = 0.0, 0.0
    shots_h, shots_a = 0, 0
    sot_h, sot_a = 0, 0

    for s in match_data["statistics"]:
        name = s.get("name", "")
        if name == "Expected goals (xG)":
            try:
                xg_h = float(s["stats"][0]) if s["stats"][0] != "-" else 0.0
                xg_a = float(s["stats"][1]) if s["stats"][1] != "-" else 0.0
            except: pass
        if name == "Total shots":
            try:
                shots_h = int(s["stats"][0]) if s["stats"][0] != "-" else 0
                shots_a = int(s["stats"][1]) if s["stats"][1] != "-" else 0
            except: pass
        if name == "Shots on target":
            try:
                sot_h = int(s["stats"][0]) if s["stats"][0] != "-" else 0
                sot_a = int(s["stats"][1]) if s["stats"][1] != "-" else 0
            except: pass

    # Если xG нет, но есть удары (матч идет) — считаем сами!
    if xg_h == 0 and xg_a == 0 and (shots_h > 0 or shots_a > 0):
        xg_h = (sot_h * 0.25) + ((shots_h - sot_h) * 0.05)
        xg_a = (sot_a * 0.25) + ((shots_a - sot_a) * 0.05)

    # Если это будущий матч (NS), идем в BSD за прогнозами
    if fx["status"] == "NS" and xg_h == 0:
        try:
            async with httpx.AsyncClient() as client:
                search_q = f"{fx['home']} {fx['away']}"
                res = await client.get(f"https://sports.bzzoiro.com/api/v2/events/?search={search_q}", headers={"Authorization": f"Token {BSD_TOKEN}"}, timeout=10)
                if res.status_code == 200 and res.json().get("results"):
                    bsd_id = res.json()["results"][0]["id"]
                    pred_res = await client.get(f"https://sports.bzzoiro.com/api/v2/events/{bsd_id}/prediction/", headers={"Authorization": f"Token {BSD_TOKEN}"})
                    if pred_res.status_code == 200:
                        pred = pred_res.json()
                        xg_h = float(pred.get("expected_goals", {}).get("home", 0.0))
                        xg_a = float(pred.get("expected_goals", {}).get("away", 0.0))
        except Exception as e:
            logger.warning(f"BSD xG error: {e}")

    # Если данных вообще нет, ставим средние
    if xg_h == 0 and xg_a == 0:
        xg_h, xg_a = 1.35, 1.15

    # 2. Математика
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

    # 3. Промпт
    prompt = f"""
    Ты профессиональный футбольный аналитик.
    Матч: {fx['home']} против {fx['away']}. Счет: {gh}:{ga} ({fx['status']}). 
    Рассчитанные ожидаемые голы (xG): {xg_h:.2f} vs {xg_a:.2f}.
    Вероятности: П1:{probs['p1']*100:.0f}%, X:{probs['px']*100:.0f}%, П2:{probs['p2']*100:.0f}%. ТБ2.5:{probs['over25']*100:.0f}%.
    Травмы: {json.dumps(match_data['missing'])}. H2H: {json.dumps(match_data['h2h'])}.

    Напиши разбор. Верни текст СТРОГО в таком формате (без лишних символов):
    [PICK] Твой главный прогноз (например, П1 или ТБ 2.5)
    [ANALYSIS] Подробный анализ на 3 абзаца. Почему этот исход? Упомяни xG.
    [RISK] Главный риск этой ставки.
    [ALT] Альтернативный исход.
    """

    fallback_data = {
        "main_pick": f"П1 ({fx['home']})" if probs['p1'] > probs['p2'] else f"П2 ({fx['away']})",
        "confidence": "Средняя",
        "analysis_text": f"Нейросеть недоступна. Математика: П1 - {(probs['p1']*100):.0f}%, X - {(probs['px']*100):.0f}%, П2 - {(probs['p2']*100):.0f}%. Ожидаемые голы: {xg_h:.2f} - {xg_a:.2f}.",
        "risk_text": "Возможны ошибки из-за отсутствия глубокого ИИ-анализа.",
        "alt_text": "Рекомендуется дождаться восстановления ИИ."
    }

    try:
        async with httpx.AsyncClient() as client:
            ai_res = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "openai/gpt-oss-120b",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.7,
                    "max_tokens": 1000
                },
                timeout=20.0
            )
        
        if ai_res.status_code != 200:
            err_msg = ai_res.text
            logger.error(f"Groq API Error: {err_msg}")
            # ВЫВОДИМ ОШИБКУ НА ЭКРАН ДЛЯ ОТЛАДКИ
            fallback_data["analysis_text"] = f"ОШИБКА GROQ API: {err_msg[:120]}..."
            return fallback_data

        ai_text = ai_res.json().get("choices", [{}])[0].get("message", {}).get("content", "")
        
        # Защита от кривого парсинга
        pick = ai_text.split("[PICK]")[1].split("[ANALYSIS]")[0].strip() if "[PICK]" in ai_text else fallback_data["main_pick"]
        analysis = ai_text.split("[ANALYSIS]")[1].split("[RISK]")[0].strip() if "[ANALYSIS]" in ai_text else ai_text
        risk = ai_text.split("[RISK]")[1].split("[ALT]")[0].strip() if "[RISK]" in ai_text else "-"
        alt = ai_text.split("[ALT]")[1].strip() if "[ALT]" in ai_text else "-"
        
        return {
            "main_pick": pick,
            "confidence": "Высокая",
            "analysis_text": analysis,
            "risk_text": risk,
            "alt_text": alt
        }
    except Exception as e:
        logger.error(f"Oracle AI error: {e}")
        fallback_data["analysis_text"] = f"ОШИБКА ПИТОНА: {str(e)[:120]}..."
        return fallback_data

@app.get("/")
async def health():
    return {"status": "ok"}
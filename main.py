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

app = FastAPI(title="Oracle AI Luxury", version="13.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GROQ_API_KEY = "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"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://www.fotmob.com/",
}

async def fetch_fotmob(path: str) -> Dict:
    url = f"https://www.fotmob.com/api/data{path}"
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url, headers=HEADERS, timeout=10.0)
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.error(f"FotMob error: {e}")
    return {}

# Функция получения последних 5 игр (форма) и средних голов
async def get_team_form_and_goals(team_id: int):
    if not team_id: return "Нет данных", 1.3, 1.3
    data = await fetch_fotmob(f"/teams?id={team_id}")
    try:
        fixtures = data.get("fixtures", {}).get("allFixtures", {}).get("fixtures", [])
        finished = [f for f in fixtures if f.get("status", {}).get("finished")]
        last_5 = finished[-5:]
        
        if not last_5: return "Нет данных", 1.3, 1.3
        
        form_str = []
        goals_scored = 0
        goals_conceded = 0
        
        for f in last_5:
            is_home = (f.get("home", {}).get("id") == team_id)
            sh = f.get("home", {}).get("score", 0)
            sa = f.get("away", {}).get("score", 0)
            
            if is_home:
                goals_scored += sh; goals_conceded += sa
                res = "В" if sh > sa else ("П" if sh < sa else "Н")
                form_str.append(f"{res} {sh}:{sa}")
            else:
                goals_scored += sa; goals_conceded += sh
                res = "В" if sa > sh else ("П" if sa < sh else "Н")
                form_str.append(f"{res} {sa}:{sh}")
                
        avg_s = goals_scored / len(last_5)
        avg_c = goals_conceded / len(last_5)
        return ", ".join(form_str), avg_s, avg_c
    except:
        return "Нет данных", 1.3, 1.3

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
            "homeId": teams[0].get("id"),
            "awayId": teams[1].get("id"),
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
    
    xg_h, xg_a = 0.0, 0.0
    shots_h, shots_a, sot_h, sot_a, poss_h, poss_a = 0, 0, 0, 0, 50, 50
    has_real_xg = False

    # 1. Парсинг статистики (ищем по title)
    for s in match_data["statistics"]:
        title = s.get("title", "").lower() 
        stats = s.get("stats", [0, 0])
        if not isinstance(stats, list) or len(stats) < 2: continue
        
        try:
            if "xg" in title or "expected goals" in title:
                if stats[0] != "-" and stats[1] != "-": 
                    xg_h = float(stats[0]); xg_a = float(stats[1]); has_real_xg = True
            if "total shots" in title:
                if stats[0] != "-": shots_h = int(stats[0])
                if stats[1] != "-": shots_a = int(stats[1])
            if "shots on target" in title:
                if stats[0] != "-": sot_h = int(stats[0])
                if stats[1] != "-": sot_a = int(stats[1])
            if "possession" in title:
                if stats[0] != "-": poss_h = int(stats[0].replace('%',''))
                if stats[1] != "-": poss_a = int(stats[1].replace('%',''))
        except: pass

    # 2. Получение формы команд
    home_form_str, h_avg, h_conc = await get_team_form_and_goals(fx.get("homeId"))
    away_form_str, a_avg, a_conc = await get_team_form_and_goals(fx.get("awayId"))

    xg_note = "На основе реальной статистики xG." if has_real_xg else "Внимание: Реальный xG отсутствует в статистике."
    
    if not has_real_xg:
        if shots_h > 0 or shots_a > 0:
            xg_h = (sot_h * 0.3) + ((shots_h - sot_h) * 0.05)
            xg_a = (sot_a * 0.3) + ((shots_a - sot_a) * 0.05)
        else:
            xg_h = (h_avg + a_conc) / 2
            xg_a = (a_avg + h_conc) / 2

    if xg_h <= 0: xg_h = 1.2
    if xg_a <= 0: xg_a = 1.2

    # 3. Математика
    gh, ga = fx["homeScore"], fx["awayScore"]
    time_left = 1.0 if fx["status"] == "NS" else 0.0
    minute = 0
    if fx["status"] == "LIVE":
        min_str = str(fx["minute"]).replace("'", "").replace("HT", "45")
        minute = int(min_str) if min_str.isdigit() else 45
        time_left = max(0, (90 - minute) / 90)

    lambda_h = max(0.01, xg_h * time_left)
    lambda_a = max(0.01, xg_a * time_left)
    if gh > ga: lambda_a *= 1.2
    if ga > gh: lambda_h *= 1.2

    markets = {"1": 0, "X": 0, "2": 0, "BTTS_Yes": 0, "BTTS_No": 0}
    total_lines = [0.5, 1.5, 2.5, 3.5, 4.5, 5.5, 6.5]
    for t in total_lines:
        markets[f"O{t}"] = 0; markets[f"U{t}"] = 0
        markets[f"IT1_O{t}"] = 0; markets[f"IT1_U{t}"] = 0
        markets[f"IT2_O{t}"] = 0; markets[f"IT2_U{t}"] = 0

    handicap_lines = [-4.5, -4, -3.5, -3, -2.5, -2, -1.5, -1, -0.5, 0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5]
    for h_val in handicap_lines:
        markets[f"AH1_{h_val}"] = 0; markets[f"AH2_{h_val}"] = 0

    is_ht_active = fx["status"] == "NS" or (fx["status"] == "LIVE" and minute <= 45)
    if is_ht_active:
        for t in [0.5, 1.5, 2.5, 3.5, 4.5]:
            markets[f"HT_O{t}"] = 0; markets[f"HT_U{t}"] = 0

    for h in range(10):
        for a in range(10):
            p_h = (math.exp(-lambda_h) * (lambda_h**h)) / math.factorial(h)
            p_a = (math.exp(-lambda_a) * (lambda_a**a)) / math.factorial(a)
            p = p_h * p_a
            fh, fa = gh + h, ga + a
            
            if fh > fa: markets["1"] += p
            elif fh < fa: markets["2"] += p
            else: markets["X"] += p
            if fh > 0 and fa > 0: markets["BTTS_Yes"] += p
            else: markets["BTTS_No"] += p

            tg = fh + fa
            for t in total_lines:
                if tg > t: markets[f"O{t}"] += p
                else: markets[f"U{t}"] += p
                if fh > t: markets[f"IT1_O{t}"] += p
                else: markets[f"IT1_U{t}"] += p
                if fa > t: markets[f"IT2_O{t}"] += p
                else: markets[f"IT2_U{t}"] += p

            for h_val in handicap_lines:
                if fh + h_val > fa: markets[f"AH1_{h_val}"] += p
                if fa + h_val > fh: markets[f"AH2_{h_val}"] += p

    total_p = markets["1"] + markets["X"] + markets["2"]
    for k in markets: markets[k] /= total_p

    # 4. Фильтрация (55% - 85%)
    available = []
    def add_market(name, prob):
        if 0.55 <= prob <= 0.85:
            available.append({"name": name, "prob": prob})

    if fx["status"] != "FT":
        add_market(f"П1 ({fx['home']})", markets["1"])
        add_market("Ничья (X)", markets["X"])
        add_market(f"П2 ({fx['away']})", markets["2"])

    for t in total_lines:
        add_market(f"ТБ {t}", markets[f"O{t}"]); add_market(f"ТМ {t}", markets[f"U{t}"])
        add_market(f"ИТ1 Б {t}", markets[f"IT1_O{t}"]); add_market(f"ИТ1 М {t}", markets[f"IT1_U{t}"])
        add_market(f"ИТ2 Б {t}", markets[f"IT2_O{t}"]); add_market(f"ИТ2 М {t}", markets[f"IT2_U{t}"])

    for h_val in handicap_lines:
        add_market(f"Ф1 ({h_val})", markets[f"AH1_{h_val}"])
        add_market(f"Ф2 ({h_val})", markets[f"AH2_{h_val}"])

    add_market("ОЗ (Да)", markets["BTTS_Yes"]); add_market("ОЗ (Нет)", markets["BTTS_No"])

    if is_ht_active:
        for t in [0.5, 1.5, 2.5, 3.5, 4.5]:
            add_market(f"ТБ {t} в 1-м тайме", markets[f"HT_O{t}"])
            add_market(f"ТМ {t} в 1-м тайме", markets[f"HT_U{t}"])

    if not available:
        for k, v in markets.items():
            if 0.3 <= v <= 0.97: available.append({"name": k, "prob": v})

    available.sort(key=lambda x: x["prob"], reverse=True)
    top_markets = available[:6]
    top_markets_str = "\n".join([f"- {m['name']} ({(m['prob']*100):.1f}%)" for m in top_markets])

    # 5. ПРОМПТ (Заставляем ИИ использовать все данные)
    prompt = f"""
    Ты профессиональный футбольный каппер (синдикатор). Твоя задача — дать всесторонний анализ и найти ставку с лучшим соотношением риска и прибыли (Value).

    КОНТЕКСТ МАТЧА:
    - {fx['home']} против {fx['away']} (Лига: {fx['league']})
    - Счет: {gh}:{ga}. Статус: {fx['status']} ({fx['minute']}).
    - Владение мячом: {poss_h}% на {poss_a}%. Удары: {shots_h} на {shots_a} (в створ: {sot_h} на {sot_a}).
    - xG команд: {xg_h:.2f} против {xg_a:.2f} ({xg_note})

    ДОПОЛНИТЕЛЬНЫЕ ФАКТОРЫ (Обязательно учти!):
    - Последние 5 игр Хозяев: {home_form_str} (в среднем забивают {h_avg:.1f}, пропускают {h_conc:.1f})
    - Последние 5 игр Гостей: {away_form_str} (в среднем забивают {a_avg:.1f}, пропускают {a_conc:.1f})
    - Травмы/Дисквалификации: Хозяева - {', '.join(match_data['missing']['home']) or 'нет'}. Гости - {', '.join(match_data['missing']['away']) or 'нет'}.
    - Личные встречи (H2H): {json.dumps(match_data['h2h'])}.

    ДОСТУПНЫЕ РЫНКИ (вероятность 55-85%, лучшие для кэфов):
    {top_markets_str}

    ТВОЯ ЗАДАЧА:
    Выбери ЛУЧШИЙ исход из списка (не бери кэфы 1.01). 
    Напиши разбор СТРОГО из 4 абзацев:
    1. Контекст и давление (кто владеет инициативой, как идет игра).
    2. Статистика и xG (сравни удары, ожидаемые голы).
    3. Форма, травмы и H2H (как это влияет на игру).
    4. Вывод (почему выбранный исход имеет ценность).

    Формат ответа СТРОГО:
    [PICK] Название исхода
    [ANALYSIS] (Текст из 4 абзацев)
    [RISK] Главный риск.
    [ALT] Альтернативный исход.
    """

    try:
        async with httpx.AsyncClient() as client:
            ai_res = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "openai/gpt-oss-120b",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.7,
                    "max_tokens": 1500
                },
                timeout=25.0
            )
        
        if ai_res.status_code != 200:
            return {"main_pick": "Ошибка ИИ", "confidence": "Низкая", "analysis_text": ai_res.text, "risk_text": "-", "alt_text": "-"}

        ai_text = ai_res.json().get("choices", [{}])[0].get("message", {}).get("content", "")
        
        pick = ai_text.split("[PICK]")[1].split("[ANALYSIS]")[0].strip() if "[PICK]" in ai_text else (top_markets[0]["name"] if top_markets else "Нет данных")
        analysis = ai_text.split("[ANALYSIS]")[1].split("[RISK]")[0].strip() if "[ANALYSIS]" in ai_text else ai_text
        risk = ai_text.split("[RISK]")[1].split("[ALT]")[0].strip() if "[RISK]" in ai_text else "-"
        alt = ai_text.split("[ALT]")[1].strip() if "[ALT]" in ai_text else "-"
        
        return {"main_pick": pick, "confidence": "Высокая", "analysis_text": analysis, "risk_text": risk, "alt_text": alt}
        
    except Exception as e:
        return {"main_pick": "Ошибка", "confidence": "Низкая", "analysis_text": str(e), "risk_text": "-", "alt_text": "-"}

@app.get("/")
async def health():
    return {"status": "ok"}
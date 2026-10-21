"""여행 계획의 근거 만들기 — 공통 조건 → A 장소 조사 · B 날씨 조사 → 조건·상태 검사 → 계획 입력.

A·B는 코드가 정한 순서로 부르는 역할 함수다(독립 에이전트 아님). 에이전트는 research_trip 도구 하나로 이 흐름을 부른다.
검사를 통과한 결과만 계획의 근거로 쓴다. 실패한 쪽은 추측으로 채우지 않고 이유만 남긴다.
"""
import hashlib
import json

from .sources import search_places_raw, get_weather_raw, region_coordinates

# 취향 단어를 그대로 검색어에 넣으면 '실내' → 체육관·주차장이 나온다. 장소 종류로 바꾸고 일정 후보가 아닌 분류는 뺀다.
PLACE_KIND = {"실내": "박물관", "실외": "공원", "야외": "공원"}
KNOWN_KINDS = ("박물관", "미술관", "전시관", "기념관", "과학관", "수족관", "공원", "카페", "맛집", "식당", "시장", "사찰", "해수욕장", "명소")
EXCLUDE_CATEGORY = ("주차장", "기념품")
SLOTS = [("10:00", "11:30"), ("13:30", "15:00"), ("15:30", "17:00")]  # 제안 시간대(조회 사실 아님)
WEEKDAYS = "월화수목금토일"


def date_label(day: str) -> str:
    """'2026-10-24(토)' — 요일은 코드가 계산한다(모델이 요일을 틀린 실행 기록이 있음)."""
    from datetime import date as _d
    try:
        return f"{day}({WEEKDAYS[_d.fromisoformat(day).weekday()]})"
    except ValueError:
        return day


def make_conditions(region: str, day: str, preferences: str = "") -> dict:
    """공통 조건 — 한 곳에서 만들어 A·B에 같은 값을 넘긴다."""
    return {"region": region.strip(), "date": day.strip(), "timezone": "Asia/Seoul", "preferences": (preferences or "").strip()}


def place_kind(preferences: str) -> str:
    """'부모님, 실내'처럼 섞인 취향에서 장소 종류만 고른다. 동행자 같은 말은 검색어에 넣지 않는다."""
    p = preferences or ""
    for k in KNOWN_KINDS:
        if k in p:
            return k
    for k, v in PLACE_KIND.items():
        if k in p:
            return v
    return ""


def place_query(region: str, kind: str) -> str:
    return f"{region} {place_kind(kind) or ('명소' if kind else '')}".strip()


def search_places(region: str, kind: str = "", search=None, size: int = 5) -> dict:
    """대화 도구와 A 역할이 함께 쓰는 장소 검색 규칙."""
    search = search or search_places_raw
    res = search(place_query(region, kind), size)
    if res["status"] == "ok":
        from trip_planning import places_in_region
        mapped = [{**p, "x": p.get("lon"), "y": p.get("lat")} for p in res["places"]]
        kept = [p for p in places_in_region(mapped, region) if not any(x in (p.get("category") or "") for x in EXCLUDE_CATEGORY)]
        res = {**res, "places": kept}
        if not kept:
            res = {**res, "status": "empty", "message": "일정 후보가 될 장소가 없습니다. 장소 종류를 넓혀 보세요."}
    return res


def locate(region: str, search=None) -> dict:
    """날씨 좌표는 장소 검색 결과에서 가져온다(모델이 좌표를 만들지 않음)."""
    if search is None:
        return region_coordinates(region)
    search = search or search_places_raw
    loc = search(region, 1)
    if loc["status"] != "ok":
        return {"status": "error", "message": "지역 좌표를 확인하지 못했습니다. 지역 이름을 확인하세요.",
                "cause": loc.get("message", "")}
    p = loc["places"][0]
    return {"status": "ok", "lat": float(p["lat"]), "lon": float(p["lon"]), "name": p["name"], "address": p["address"]}


def role_a_places(cond: dict, search=None) -> dict:
    res = search_places(cond["region"], cond["preferences"], search, size=10)  # 일정 후보를 넉넉히
    # 취향을 임의로 넓히지 않습니다. 빈 결과는 사용자에게 조건 변경을 물어봅니다.
    return {"role": "A", "input": cond, **res}


def role_b_weather(cond: dict, search=None, weather=None) -> dict:
    weather = weather or get_weather_raw
    loc = locate(cond["region"], search)
    if loc["status"] != "ok":
        return {"role": "B", "input": cond, "status": "error", "date": cond["date"], "forecast": None,
                "advice": [], "message": loc["message"]}
    w = weather(loc["lat"], loc["lon"], cond["date"])
    advice = []
    if w["status"] == "ok":
        f = w["forecast"]
        precip, tmin, tmax = f.get("precip_prob_max"), f.get("temp_min"), f.get("temp_max")
        if precip is not None and precip >= 50:  # 0도 유효한 값이다. None 일 때만 판단하지 않는다
            advice.append(f"우산 (강수확률 {precip}%)")
        if tmin is not None and tmin <= 5:
            advice.append(f"두꺼운 겉옷 (최저 {tmin}°C)")
        elif tmin is not None and tmax is not None and tmax - tmin >= 10:
            advice.append(f"얇은 겉옷 (일교차 {round(tmax - tmin, 1)}°C)")
    return {"role": "B", "input": cond, **w, "coordinates_from": {"name": loc["name"], "address": loc["address"]}, "advice": advice}


def combine_status(cond: dict, a: dict, b: dict) -> dict:
    """종합 직전 검사 — 공통 조건과 같은 조건으로 조회한 정상 결과만 근거로 인정한다."""
    notes = []
    a_match, b_match = a.get("input") == cond, b.get("input") == cond
    if not a_match:
        notes.append("장소: 공통 조건과 다른 조건으로 조회됨 — 근거로 쓰지 않음")
    if not b_match:
        notes.append("날씨: 공통 조건과 다른 조건으로 조회됨 — 근거로 쓰지 않음")
    weather_ok = b_match and b.get("status") == "ok" and b.get("date") == cond["date"]
    if b_match and not weather_ok:
        notes.append("날씨: 예보 범위 밖 — 확인 필요" if b.get("status") == "out_of_range" else "날씨: 조회 실패 — 확인 필요")
    places_ok = a_match and a.get("status") == "ok"
    if a_match and not places_ok:
        notes.append("장소: 조회 실패 — 장소 일정을 만들지 않음" if a.get("status") == "error" else "장소: 찾지 못함 — 조건 완화 필요")
    return {"places_ok": places_ok, "weather_ok": weather_ok, "notes": notes}


def suggest_schedule(cond: dict, a: dict, check: dict) -> list[dict]:
    """장소 결과로 만든 제안 시간표 — 계획 글과 일정 미리보기가 같은 값을 쓴다."""
    if not check["places_ok"]:
        return []
    seen, first, rest = set(), [], []
    for p in a["places"]:  # 같은 주소(한 시설의 하위 장소)는 뒤로 미뤄 서로 다른 곳을 먼저 고른다
        (rest if p["address"] in seen else first).append(p)
        seen.add(p["address"])
    return [{"place": p["name"], "address": p["address"], "url": p["url"], "date": cond["date"], "start": s, "end": e}
            for p, (s, e) in zip(first + rest, SLOTS)]


def plan_input(cond: dict, a: dict, b: dict, check: dict) -> dict:
    """모델에 넘길 계획 근거 — 인정되지 않은 결과는 빼고 이유만 남긴다."""
    places = [{k: p[k] for k in ("name", "address", "category", "url")} for p in a.get("places", [])] if check["places_ok"] else []
    return {
        "conditions": {**cond, "date_label": date_label(cond["date"])}, "notes": check["notes"], "places_ok": check["places_ok"], "weather_ok": check["weather_ok"],
        "places": {"status": a.get("status") if check["places_ok"] else "not_used", "items": places,
                   "source": a.get("source"), "retrieved_at": a.get("retrieved_at"), "message": a.get("message", "")},
        "weather": ({"status": "ok", "date": b["date"], "forecast": b["forecast"], "unit": b["unit"], "advice": b["advice"],
                     "source": b["source"], "retrieved_at": b["retrieved_at"]} if check["weather_ok"]
                    else {"status": "not_used", "reason": b.get("status"), "message": b.get("message", "")}),
        "schedule": suggest_schedule(cond, a, check),
        "not_checked": ["영업시간", "이동 시간", "가격", "휴무"],
    }


def research(cond: dict, search=None, weather=None) -> dict:
    a = role_a_places(cond, search)
    b = ({"role": "B", "input": cond, "status": "error", "date": cond["date"], "forecast": None,
          "advice": [], "message": "장소 조회 오류로 추가 조회를 중단했습니다."}
         if a["status"] == "error" else role_b_weather(cond, search, weather))
    check = combine_status(cond, a, b)
    basis = {"cond": cond, "a": a, "b": b, "check": check}
    basis["id"] = hashlib.sha256(json.dumps(basis, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:12]
    return basis

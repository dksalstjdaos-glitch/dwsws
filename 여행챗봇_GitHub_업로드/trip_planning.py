"""저장된 동일 지역·날짜의 실제 장소와 예보로 방문 순서·시간대를 제안합니다."""

import math
import re
from datetime import date, timedelta

from config import load_settings
from kakao_maps import get_region_coordinates
from weather import DAILY_FIELDS, coordinates, get_weather, validate_dates, weather_answer


def places_in_region(places, region):
    tokens = [re.sub(r"(?:특별자치도|특별자치시|특별시|광역시|시|군|구)$", "", t) for t in region.split()]
    aliases = {"경상북도": "경북", "경상남도": "경남", "전라남도": "전남", "전라북도": "전북", "충청남도": "충남", "충청북도": "충북", "경기도": "경기"}
    def normalize(text):
        for long, short in aliases.items():
            text = text.replace(long, short)
        return re.sub(r"특별자치시|특별시|광역시|\s+", "", text)
    found, seen = [], set()
    for place in places:
        address = normalize(place.get("address", ""))
        key = str(place.get("key") or place.get("id") or place.get("name", "") + place.get("address", ""))
        if tokens and all(normalize(t) in address for t in tokens if t) and coordinates(place) and key not in seen:
            seen.add(key)
            found.append({**place, "key": key})
    return found


def check_plan_evidence(conditions, place_records, weather_record=None):
    """성공 여부와 같은 지역·날짜를 검사한 자료만 계획에 전달합니다."""
    region = conditions.get("destination", "")
    start, end = conditions.get("start_date"), conditions.get("end_date")
    valid, issues, checks = [], [], []
    for record in place_records:
        label = record.get("query") or "장소 조회"
        scope = record.get("travel_scope") or {}
        reason = None
        if record.get("status") != "success":
            reason = "장소 조회 실패 또는 결과 없음"
        elif scope.get("region") and not places_in_region([{"address": scope["region"], "x": 0, "y": 0}], region):
            reason = "조회 지역 불일치"
        elif scope.get("start_date") and (scope.get("start_date"), scope.get("end_date")) != (start, end):
            reason = "여행 날짜 불일치"
        matched = places_in_region(record.get("places", []), region) if reason is None else []
        if reason is None and not matched:
            reason = "여행 지역 불일치 또는 좌표 부족"
        checks.append({"자료": label, "결과": "제외 · 확인 필요" if reason else "통과", "설명": reason or ("같은 지역·여행 날짜의 성공한 장소 조회" if scope.get("start_date") else "같은 지역의 성공 조회 · 장소 정보는 날짜 무관, 운영·예약 미확인")})
        if reason:
            issues.append(f"{label}: {reason} · 확인 필요")
        else:
            valid.extend(matched)
            excluded = len(record.get("places", [])) - len(matched)
            if excluded:
                issues.append(f"{label}: 다른 지역·좌표 부족 후보 {excluded}곳 제외 · 확인 필요")
    places = places_in_region(valid, region)
    checked_weather = weather_record
    reason = None
    if not weather_record or weather_record.get("status") != "success":
        reason = "날씨 조회 실패 또는 예보 없음"
    elif (weather_record.get("start_date"), weather_record.get("end_date")) != (start, end):
        reason = "날씨 날짜 불일치"
    elif weather_record.get("arguments", {}).get("region") != region:
        reason = "날씨 지역 불일치"
    else:
        expected = {(date.fromisoformat(start) + timedelta(days=i)).isoformat()
                    for i in range((date.fromisoformat(end) - date.fromisoformat(start)).days + 1)}
        forecasts = weather_record.get("forecasts", [])
        if not forecasts or any({d.get("date") for d in f.get("days", [])} != expected or len(f.get("days", [])) != len(expected) for f in forecasts):
            reason = "날씨 날짜별 자료 부족"
        elif any(any(key not in d for key in DAILY_FIELDS) for f in forecasts for d in f["days"]):
            reason = "날씨 항목 부족"
        elif not all(places_in_region([{"name": f.get("name", ""), "address": f.get("name", ""),
                                       "x": f.get("longitude"), "y": f.get("latitude")}], region) for f in forecasts):
            reason = "날씨 좌표의 지역 확인 필요"
    if reason:
        checked_weather = None
        issues.append(reason + " · 확인 필요")
    checks.append({"자료": "Open-Meteo 예보", "결과": "제외 · 확인 필요" if reason else "통과", "설명": reason or "같은 지역·전체 여행 날짜의 성공 예보"})
    return places, checked_weather, issues, checks


def distance_km(a, b):
    lat1, lon1 = coordinates(a)
    lat2, lon2 = coordinates(b)
    x = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
    y = math.radians(lat2 - lat1)
    return 6371 * math.hypot(x, y)


def proposed_schedule(places, conditions, forecast=None, *, preserve_order=False):
    start, end = date.fromisoformat(conditions["start_date"]), date.fromisoformat(conditions["end_date"])
    preferences = conditions.get("interests", "")
    tastes = re.findall(r"박물관|미술관|전시|카페|커피|바다|해산물|역사|문화|공원|산책|식당|맛집|쇼핑", preferences)
    gentle = any(word in preferences for word in ("걷기 적게", "걷기적게", "천천히", "부모님", "휴식", "여유"))
    slots = ("오전 10:00", "오후 14:00") if gentle else ("오전 10:00", "점심 전후 12:30", "오후 15:00")
    days = {d["date"]: d for f in (forecast or {}).get("forecasts", []) for d in f["days"]}
    remaining, schedule, ordered = list(places), [], []
    previous = None
    for offset in range((end - start).days + 1):
        day = (start + timedelta(days=offset)).isoformat()
        weather = days.get(day, {})
        wet = (weather.get("precipitation_probability_max") or 0) >= 40 or (weather.get("precipitation_sum") or 0) > 0
        for slot in slots:
            if not remaining:
                break
            def score(place):
                text = place.get("name", "") + " " + place.get("category", "")
                preference = sum(t in text for t in tastes) * 5
                indoor = any(w in text for w in ("박물관", "미술관", "전시관", "카페", "수족관", "과학관"))
                lunch = 5 if "12:30" in slot and any(w in text for w in ("음식점", "식당", "해산물")) else 0
                return preference + lunch + (8 if wet and indoor else 0) - (distance_km(previous, place) * .4 if previous else 0)
            chosen = remaining[0] if preserve_order else max(remaining, key=score)
            remaining.remove(chosen)
            text = chosen.get("name", "") + " " + chosen.get("category", "")
            matched = [t for t in tastes if t in text]
            reasons = (["취향 반영: " + ", ".join(matched)] if matched else ["확인된 지역 후보"])
            indoor = any(w in text for w in ("박물관", "미술관", "전시관", "카페", "수족관", "과학관"))
            if wet:
                reasons.append("강수 예보에 실내 유형 우선" if indoor else "강수 예보 · 외부 활동 여부 확인 후 조정")
            if previous:
                reasons.append(f"직전 장소와 직선 약 {distance_km(previous, chosen):.1f}km")
            if gentle:
                reasons.append("여유 있는 방문 간격")
            if preserve_order:
                reasons.append("직접 선택한 순서 유지")
            if not weather:
                reasons.append("날씨 확인 필요")
            schedule.append({"date": day, "time": slot, "order": len(ordered) + 1, "place": chosen, "reason": " · ".join(reasons)})
            ordered.append(chosen)
            previous = chosen
        if not any(row["date"] == day for row in schedule):
            schedule.append({"date": day, "time": "자유 시간", "order": None, "place": None,
                             "reason": "추가 장소를 지어내지 않고 휴식 시간으로 남겼습니다."})
    return schedule, ordered, remaining


def build_travel_plan(context, records, run):
    conditions = context.get("conditions") or {}
    region = conditions.get("destination", "").strip()
    start, end = conditions.get("start_date"), conditions.get("end_date")
    if not region or not start or not end:
        run.data["end_reason"] = "여행 계획 필수 조건 확인 대기"
        return "여행 계획을 만들려면 여행 조건에서 지역과 시작일·종료일을 먼저 저장해 주세요."
    try:
        start_day, end_day = date.fromisoformat(start), date.fromisoformat(end)
        if end_day < start_day or (end_day - start_day).days > 30:
            raise ValueError
    except (ValueError, TypeError):
        run.data["end_reason"] = "여행 날짜 확인 대기"
        return "시작일과 종료일을 확인해 주세요. 한 번에 최대 31일의 여행 계획을 제안합니다."
    place_records = context.get("place_records") or []
    run.decision("장소 자료 불러오기", f"현재 프로젝트의 실제 장소 조회 기록 {len(place_records)}건을 검사합니다.")
    weather = None
    error = validate_dates(start, end)
    if error:
        run.decision("날씨 조회 제외", error)
    else:
        settings = load_settings()
        run.tool_call("get_region_coordinates", {"region": region})
        location = get_region_coordinates(region, settings.kakao_api_key)
        record = {**location.record, "tool": "get_region_coordinates", "arguments": {"region": region}}
        records.append(record)
        run.tool_return(record)
        if record["status"] == "success":
            args = {"region": region, "start_date": start, "end_date": end}
            run.tool_call("get_weather", args)
            result = get_weather(record["places"], start, end)
            weather = {**result.record, "tool": "get_weather", "arguments": args, "scope": "region", "include_packing": True}
            records.append(weather)
            run.tool_return(weather)
        else:
            error = str(location)
    places, weather, issues, checks = check_plan_evidence(conditions, place_records, weather)
    if error:
        issues.append(error + " · 확인 필요")
    selected = context.get("selected_places") or []
    if selected:
        wanted = [str(p.get("key") or p.get("id") or p.get("name", "") + p.get("address", "")) for p in selected]
        by_key = {p["key"]: p for p in places}
        places = [by_key[key] for key in wanted if key in by_key]
    run.data["plan_validation"] = checks
    run.data["plan_issues"] = issues
    run.data["weather_basis"] = weather or {}
    run.decision("조건·상태 검사", f"통과한 장소 {len(places)}곳 · 날씨 {'통과' if weather else '제외 · 확인 필요'}")
    notices = "\n".join("- " + issue for issue in issues[:8])
    if not places:
        run.data["end_reason"] = "방문 계획 보류 · 같은 조건의 장소 확인 필요"
        return f"### 확인 필요\n‘{region}’의 같은 조건으로 확인된 장소가 없어 방문 순서는 만들지 않았습니다. 장소를 검색하거나 여행 조건을 확인해 주세요.\n\n{notices}" + ("\n\n" + weather_answer(weather) if weather else "\n\n날씨·옷차림·준비물도 확인이 필요합니다.")
    if len(places) > 12:
        issues.append(f"후보 {len(places)}곳 중 최근 조회된 12곳으로 계획을 제안합니다.")
    schedule, ordered, remaining = proposed_schedule(places[:12], conditions, weather, preserve_order=context.get("preserve_order", False))
    run.data["answer_basis"] = ordered
    run.data["proposed_places"] = ordered
    run.data["trip_schedule"] = schedule
    run.decision("방문 순서·시간대 제안", "같은 지역 후보만 사용 · 취향 일치, 강수 예보와 장소 유형, 좌표상 거리를 반영")
    run.data["end_reason"] = "검증된 장소·예보로 여행 계획 완성" if weather else "장소 근거로 부분 계획 작성 · 날씨 확인 필요"
    lines = [f"### {region} 여행 계획 · {start} ~ {end}",
             f"취향·요청: {conditions.get('interests') or '미정'} · 인원: {str(conditions['people']) + '명' if conditions.get('people') else '미정'} · 교통: {conditions.get('transport') or '미정'}",
             (f"전체 예산: {conditions['budget']:,}원 · 요금 자료를 조회하지 않아 예산 충족 여부는 확인이 필요합니다." if conditions.get("budget") else "예산 미정 · 입장료·교통비는 합산하지 않았습니다."),
             "시간대는 계획을 위한 제안입니다. 실제 도로 이동 시간과 운영시간은 조회하지 않았습니다. 거리 표시는 직선거리입니다.",
             "| 날짜 | 제안 시간대 | 방문 순서·장소 | 제안 근거 |", "|---|---|---|---|"]
    def cell(text):
        return str(text).replace("|", " ").replace("\n", " ")
    for row in schedule:
        place = row["place"]
        name = f"{row['order']}. {cell(place['name'])}" if place else "휴식·자유 시간"
        if place and place.get("url", "").startswith(("https://place.map.kakao.com/", "http://place.map.kakao.com/")):
            name = f"[{name}]({place['url']})"
        lines.append(f"| {row['date']} | {row['time']} | {name} | {cell(row['reason'])} |")
    if remaining:
        lines += ["", "시간대가 부족해 이번 일정에서 제외한 후보: " + ", ".join(cell(p["name"]) for p in remaining)]
    lines += ["", "식사·휴식 시간은 여유 있게 확보하고, 출발 전 각 장소의 운영·휴무를 확인하세요."]
    if issues:
        lines += ["", "### 확인 필요 · 계획에서 제외한 자료", notices]
    lines += ["", weather_answer(weather) if weather else "### 날씨·옷차림·준비물 확인 필요\n성공하고 지역·날짜가 일치한 예보가 없어 날씨에 따른 준비물을 제안하지 않았습니다."]
    return "\n".join(lines)

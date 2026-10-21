"""실제 검색 장소의 좌표로 Open-Meteo 예보를 조회하고 준비물 근거를 만듭니다."""

import math
import re
from datetime import date, datetime, timedelta, timezone

import httpx

from kakao_maps import RouteResult

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
SOURCE_URL = "https://open-meteo.com/"
KST = timezone(timedelta(hours=9))
DAILY_FIELDS = ("weather_code", "temperature_2m_min", "temperature_2m_max",
                "precipitation_probability_max", "precipitation_sum", "wind_speed_10m_max", "uv_index_max")
FIELD_NAMES = {"weather_code": "날씨", "temperature_2m_min": "최저 기온 (°C)",
               "temperature_2m_max": "최고 기온 (°C)", "precipitation_probability_max": "최대 강수확률 (%)",
               "precipitation_sum": "강수량 (mm)", "wind_speed_10m_max": "최대 바람 (m/s)", "uv_index_max": "최대 자외선 지수"}
RELATIVE_DATE_PATTERN = re.compile(
    r"(?:(?P<days>\d+)\s*일|(?P<weeks>\d+)\s*주(?:일)?|(?P<word>사흘|나흘|일주일))"
    r"\s*(?P<direction>뒤|후|전)(?:\s*에)?"
)
WEEK_OFFSETS = {"이번": 0, "금": 0, "다음": 1, "차": 1, "다다음": 2, "지난": -1, "지지난": -2}
WEEKDAY_DATE_PATTERN = re.compile(
    r"(?:(?P<week>다다음|지지난|이번|다음|지난|금|차)\s*(?:주\s*)?)?"
    r"(?:다가오는\s*|돌아오는\s*|오는\s*)?(?P<weekday>[월화수목금토일])(?:요일|욜)(?:\s*에)?"
)
WEEKEND_DATE_PATTERN = re.compile(r"(?:(?P<week>다다음|지지난|이번|다음|지난|금|차)\s*(?:주\s*)?)?주말(?:\s*에)?")
WEEK_DATE_PATTERN = re.compile(r"(?P<week>다다음|지지난|이번|다음|지난|금|차)\s*주(?:\s*에)?")
ABSOLUTE_DATE_PATTERN = re.compile(r"\d{4}[-./]\d{1,2}[-./]\d{1,2}|(?:\d{4}년)?\s*\d{1,2}월\s*\d{1,2}일?")
CONTEXT_REFERENCE_PATTERN = re.compile(r"(?:이|그|해당)\s*(?:날|때|기간)(?:의|은|는|에|도)?|여행\s*(?:날|기간)(?:의|은|는|에)?|거기(?:는|의|도|엔|에)?|거긴|그곳(?:은|의)?")


def weather_context_reference(question):
    return bool(CONTEXT_REFERENCE_PATTERN.search(question))


def trip_duration(text):
    match = re.search(r"(\d+)\s*박(?:\s*(\d+)\s*일)?", text)
    if match:
        return int(match[2]) if match[2] else int(match[1]) + 1
    match = re.search(r"(\d+)\s*일\s*(?:동안|간)", text)
    return int(match[1]) if match else None


def strip_date_expressions(text):
    """날짜 해석과 같은 표현 목록을 사용해 지역명에서 날짜를 제거합니다."""
    for pattern in (RELATIVE_DATE_PATTERN, WEEKDAY_DATE_PATTERN, WEEKEND_DATE_PATTERN, WEEK_DATE_PATTERN, ABSOLUTE_DATE_PATTERN):
        text = pattern.sub(" ", text)
    return re.sub(r"오늘|내일|모레|부터|까지", " ", text)


def today_kst():
    return datetime.now(KST).date()


def weather_requested(question, history=None, travel_context=None):
    if re.search(r"날씨|예보|준비물|챙길|챙겨|챙기|옷|복장|입을|입고|우산|강수|기온|비\s*(?:올|와|오)|눈\s*(?:올|와|오)|자외선", question):
        return True
    previous = (history or [])[-1:]
    if previous and previous[0].get("execution", {}).get("end_reason") == "날씨 지역 확인 대기":
        return True
    if previous and previous[0].get("execution", {}).get("end_reason") == "여행 날짜 확인 대기":
        return bool(dates_in_text(question))
    return bool(re.search(r"일정|코스|계획", question) and ((travel_context or {}).get("conditions") or {}).get("start_date"))


def regional_weather_requested(question, history=None):
    text = question.rsplit("말고", 1)[-1]
    if re.search(r"준비물|챙|옷차림|일정|코스|계획|선택|추천.*(?:장소|곳)|(?:그|이|해당)\s*(?:장소|곳)|박물관|미술관|식당|카페|호텔", text):
        return False
    return bool(re.search(r"날씨|예보|기온|강수|비\s*올|눈\s*올", text)) or bool(
        (history or []) and (history[-1].get("execution", {}).get("end_reason") == "날씨 지역 확인 대기" or
                            history[-1].get("execution", {}).get("pending_weather_region")))


def weather_region(question, history=None, travel_context=None):
    def extract(text, short_reply=False):
        text = text.rsplit("말고", 1)[-1]
        prefix = re.split(r"날씨|예보|기온|강수", text, maxsplit=1)[0]
        if prefix == text and not short_reply:
            return ""
        prefix = strip_date_expressions(prefix)
        prefix = CONTEXT_REFERENCE_PATTERN.sub("", prefix)
        prefix = re.sub(r"오늘|내일|모레|지금|현재|그럼|그냥|전체적인|전반적인|전체|지역|여행", "", prefix)
        prefix = re.sub(r"[~∼\d,.!?]", " ", prefix).strip()
        prefix = re.sub(r"(?:에서|의|은|는|요)$", "", prefix).strip()
        return prefix if prefix and len(prefix) <= 30 and prefix not in {"거기", "그곳", "이번", "그때"} else ""
    pending = bool((history or []) and history[-1].get("execution", {}).get("end_reason") == "날씨 지역 확인 대기")
    region = extract(question, short_reply=pending)
    if region:
        return region
    pending_region = (history[-1].get("execution", {}).get("pending_weather_region") if history else None)
    if pending_region:
        return pending_region
    destination = ((travel_context or {}).get("conditions") or {}).get("destination")
    referencing = weather_context_reference(question)
    if destination and not referencing:
        return destination
    for message in reversed(history or []):
        if message.get("role") == "user":
            region = extract(message.get("content", ""))
            if region:
                return region
            trip = re.search(r"(?<![가-힣])([가-힣]{2,12}?)(?:으로|로)?\s*(?:여행|갈\s*거|갈라고|가려고|갈게)", message.get("content", ""))
            if trip:
                return trip[1]
        for lookup in reversed(message.get("lookups") or []):
            if lookup.get("status") != "success":
                continue
            region = (lookup.get("arguments") or {}).get("region")
            if region:
                return region
    return destination or ""


def dates_in_text(text):
    """명시 날짜와 오늘 기준의 상대 날짜를 해석합니다."""
    today = today_kst()
    matches = list(re.finditer(r"(?:(\d{4})\s*(?:년\s*|[-./]))?(\d{1,2})\s*(?:월\s*|[-./])(\d{1,2})\s*일?", text))
    if matches:
        try:
            values = [date(int(m[1] or today.year), int(m[2]), int(m[3])) for m in matches]
            # 10월 21~23일 / 10월 21일부터 23일까지 형태의 같은 달 종료일.
            tail = text[matches[-1].end():]
            end = re.match(r"\s*(?:부터|~|∼|～|–|—|-)\s*(\d{1,2})\s*일", tail)
            if end:
                values.append(date(values[-1].year, values[-1].month, int(end[1])))
            duration = trip_duration(text)
            if len(values) == 1 and duration is not None:
                if not 1 <= duration <= 366:
                    return "invalid", "invalid"
                values.append(values[0] + timedelta(days=duration - 1))
            return values[0].isoformat(), values[-1].isoformat()
        except (ValueError, OverflowError):
            return "invalid", "invalid"
    relative = [(m.start(), {"오늘": 0, "내일": 1, "모레": 2}[m.group()])
                for m in re.finditer(r"오늘|내일|모레", text)]
    monday = today - timedelta(days=today.weekday())
    inherited_week = None
    previous_end = 0
    for match in WEEKDAY_DATE_PATTERN.finditer(text):
        week = match["week"]
        # '다음 주 토요일부터 일요일'의 두 번째 요일도 같은 주를 사용합니다.
        if week is None and inherited_week is not None and re.fullmatch(r"\s*(?:부터|~|∼|[-–])\s*", text[previous_end:match.start()]):
            week = inherited_week
        weekday = "월화수목금토일".index(match["weekday"])
        target = monday + timedelta(days=WEEK_OFFSETS[week] * 7 + weekday) if week else today + timedelta(days=(weekday - today.weekday()) % 7)
        relative.append((match.start(), (target - today).days))
        inherited_week, previous_end = week, match.end()
    for match in WEEKEND_DATE_PATTERN.finditer(text):
        saturday = monday + timedelta(days=WEEK_OFFSETS.get(match["week"], 0) * 7 + 5)
        relative.extend([(match.start(), (saturday - today).days), (match.end(), (saturday + timedelta(days=1) - today).days)])
    for match in RELATIVE_DATE_PATTERN.finditer(text):
        days = int(match["days"]) if match["days"] else int(match["weeks"]) * 7 if match["weeks"] else {"사흘": 3, "나흘": 4, "일주일": 7}[match["word"]]
        relative.append((match.start(), -days if match["direction"] == "전" else days))
    if relative:
        relative.sort()
        try:
            duration = trip_duration(text)
            if len(relative) == 1 and duration is not None:
                if not 1 <= duration <= 366:
                    return "invalid", "invalid"
                relative.append((relative[0][0], relative[0][1] + duration - 1))
            return tuple((today + timedelta(days=delta)).isoformat() for _, delta in (relative[0], relative[-1]))
        except (ValueError, OverflowError):
            return "invalid", "invalid"
    week_match = WEEK_DATE_PATTERN.search(text)
    if week_match:
        start = monday + timedelta(days=WEEK_OFFSETS[week_match["week"]] * 7)
        end = start + timedelta(days=6)
        if week_match["week"] in ("이번", "금"):
            start = today
        return start.isoformat(), end.isoformat()
    return None


def travel_dates(question, history=None, travel_context=None, *, prefer_history=False):
    # 현재 발화 > 저장한 여행 날짜 > 이전 사용자가 명시한 날짜 순입니다.
    explicit = dates_in_text(question)
    if explicit:
        return explicit
    conditions = (travel_context or {}).get("conditions") or {}
    if conditions.get("start_date") and not prefer_history:
        return conditions["start_date"], conditions.get("end_date") or conditions["start_date"]
    for message in reversed(history or []):
        if prefer_history:
            for lookup in reversed(message.get("lookups") or []):
                if lookup.get("kind") == "weather" and lookup.get("status") == "success":
                    return lookup["start_date"], lookup["end_date"]
        if message.get("role") == "user":
            found = dates_in_text(message.get("content", ""))
            if found:
                return found
    return (conditions["start_date"], conditions.get("end_date") or conditions["start_date"]) if conditions.get("start_date") else None


def saved_weather_places(question, history=None, travel_context=None):
    """다른 지역을 새로 물을 때 과거 여행지 좌표를 재사용하지 않습니다."""
    reduced = strip_date_expressions(question)
    reduced = re.sub(r"\d{4}[-./]\d{1,2}[-./]\d{1,2}|(?:\d{4}년)?\s*\d{1,2}월\s*\d{1,2}일?|\d+일", "", reduced)
    reduced = re.sub(r"오늘|내일|모레|날씨|예보|준비물|챙길|챙겨|옷차림|우산|알려|정해|추천|확인|조회|해줘|주세요|줘|어때|필요|뭐|어떤|여행|장소|그럼|거기|그곳|그리고|은|는|을|를|에|요|이야|야|도|좀", "", reduced)
    generic = not re.sub(r"[\W\d_]", "", reduced)
    conditions = (travel_context or {}).get("conditions") or {}
    selected = (travel_context or {}).get("selected_places") or []
    named_selected = [p for p in selected if p.get("name") and p["name"] in question]
    if named_selected:
        return named_selected
    if selected and (generic or re.search(r"선택|저장", question) or (conditions.get("destination") and conditions["destination"] in question)):
        return selected
    for message in reversed(history or []):
        for lookup in reversed(message.get("lookups") or []):
            if lookup.get("kind") == "places" and lookup.get("status") == "success":
                places = lookup.get("places", [])
                named = [p for p in places if p.get("name") and p["name"] in question]
                region = (lookup.get("arguments") or {}).get("region")
                if named:
                    return named
                if generic or (region and region in question):
                    return places
                return []
    return []


def followup_weather(question, history=None):
    """지역을 새로 지정하지 않은 옷차림 후속 질문은 직전 예보의 좌표·날짜를 이어받습니다."""
    if not re.search(r"옷|복장|입을|입고|입어|입지|챙|준비물|물건", question) and not weather_context_reference(question):
        return None
    remaining = strip_date_expressions(question)
    remaining = CONTEXT_REFERENCE_PATTERN.sub("", remaining)
    remaining = re.sub(r"\d{4}[-./]\d{1,2}[-./]\d{1,2}|(?:\d{4}년)?\s*\d{1,2}월\s*\d{1,2}일?", "", remaining)
    remaining = re.sub(r"그러면|그럼|그날|그때|거기|오늘|내일|모레|날씨|예보|어때|옷차림|입을|입고|입어|입지|챙길|챙겨|챙기|준비물|물건|외출|여행|추천|알려|주세요|어떤|옷|복장|줘|해|은|는|을|를|할|때|뭘|뭐|요|좀|도", "", remaining)
    if re.sub(r"[\W\d_]", "", remaining):
        return None
    for message in reversed(history or []):
        if message.get("role") == "user" and (
            dates_in_text(message.get("content", "")) or
            re.search(r"여행|목적지|지역|가자|가려고|갈라고|으로", message.get("content", ""))
        ):
            # 이전 날씨 이후 새 여행 조건이 나왔다면 그 조건을 다시 해석합니다.
            return None
        for lookup in reversed(message.get("lookups") or []):
            if lookup.get("kind") == "weather" and lookup.get("status") == "success":
                return lookup
    return None


def validate_dates(start_date, end_date):
    try:
        start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
    except (TypeError, ValueError):
        return "여행 날짜를 YYYY-MM-DD 형식의 올바른 날짜로 알려 주세요."
    today = today_kst()
    if end < start:
        return "여행 종료일이 시작일보다 빠릅니다. 여행 날짜를 다시 알려 주세요."
    if start < today or end > today + timedelta(days=15):
        return f"Open-Meteo 예보는 현재 {today.isoformat()}부터 {(today + timedelta(days=15)).isoformat()}까지 조회합니다. 여행 날짜 전체가 이 범위 안에 있어야 합니다. 과거 날씨나 현재 날씨로 대신 안내하지 않겠습니다."
    return None


def coordinates(place):
    try:
        if isinstance(place.get("x"), bool) or isinstance(place.get("y"), bool):
            return None
        lon, lat = float(place["x"]), float(place["y"])
        if math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90:
            return lat, lon
    except (KeyError, TypeError, ValueError):
        pass
    return None


def weather_name(code):
    if code == 0:
        return "맑음"
    if code in (1, 2, 3):
        return {1: "대체로 맑음", 2: "구름 조금", 3: "흐림"}[code]
    if code in (45, 48):
        return "안개"
    if code in (51, 53, 55):
        return "이슬비"
    if code in (56, 57):
        return "얼어붙는 이슬비"
    if code in (66, 67):
        return "얼어붙는 비"
    if code in (61, 63, 65, 80, 81, 82):
        return "비·소나기"
    if code in (71, 73, 75, 77, 85, 86):
        return "눈"
    if code in (95, 96, 99):
        return "뇌우"
    return "정보 없음"


def get_weather(places, start_date, end_date):
    """places는 모델이 만든 좌표가 아니라 저장/검색 결과의 원본 장소 목록입니다."""
    error = validate_dates(start_date, end_date)
    if error:
        return RouteResult(error, status="unavailable", kind="weather", error_code="forecast_range")
    if not places or len(places) > 12 or any(not coordinates(p) for p in places):
        return RouteResult("검색으로 확인된 장소 좌표가 필요합니다. 방문할 장소를 먼저 검색·선택해 주세요.",
                           status="unavailable", kind="weather", error_code="missing_coordinates")
    coords = [coordinates(p) for p in places]
    params = {"latitude": ",".join(str(p[0]) for p in coords), "longitude": ",".join(str(p[1]) for p in coords),
              "start_date": start_date, "end_date": end_date, "daily": ",".join(DAILY_FIELDS),
              "timezone": "Asia/Seoul", "temperature_unit": "celsius", "wind_speed_unit": "ms", "precipitation_unit": "mm"}
    try:
        with httpx.Client(timeout=15, follow_redirects=False) as client:
            response = client.get(FORECAST_URL, params=params)
        if response.status_code in (401, 403, 429):
            code = "rate_limit" if response.status_code == 429 else "authentication_error"
            reason = "요청 한도를 초과해" if response.status_code == 429 else "이용 권한 문제로"
            return RouteResult(f"Open-Meteo {reason} 예보를 조회하지 못했습니다. 재시도 없이 중단합니다.",
                               kind="weather", error_code=code)
        response.raise_for_status()
        payload = response.json()
        payloads = payload if isinstance(payload, list) else [payload]
        if len(payloads) != len(places):
            raise ValueError("location count")
        expected_dates = [(date.fromisoformat(start_date) + timedelta(days=i)).isoformat()
                          for i in range((date.fromisoformat(end_date) - date.fromisoformat(start_date)).days + 1)]
        forecasts = []
        for place, coord, data in zip(places, coords, payloads):
            daily = data["daily"]
            if daily["time"] != expected_dates:
                raise ValueError("forecast dates")
            days = []
            for index, day in enumerate(expected_dates):
                values = {key: daily[key][index] for key in DAILY_FIELDS}
                for value in values.values():
                    if value is not None and (isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value)):
                        raise ValueError("invalid weather value")
                days.append({"date": day, **values, "description": weather_name(values["weather_code"])})
            if not any(d["temperature_2m_max"] is not None for d in days):
                raise ValueError("forecast missing")
            forecasts.append({"place_id": place.get("id", ""), "name": place.get("name", ""),
                              "latitude": coord[0], "longitude": coord[1], "place_url": place.get("url", ""), "days": days})
        return RouteResult("Open-Meteo 여행 날짜별 예보 조회 완료", status="success", kind="weather", source=SOURCE_URL,
                           checked_at=datetime.now(KST).isoformat(timespec="seconds"), start_date=start_date,
                           end_date=end_date, timezone="Asia/Seoul", forecasts=forecasts,
                           scope="region" if all(p.get("scope") == "region" for p in places) else "places",
                           missing_values=any(d[key] is None for f in forecasts for d in f["days"] for key in DAILY_FIELDS))
    except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
        return RouteResult("Open-Meteo 예보를 가져오지 못했거나 해당 날짜의 예보 자료가 부족합니다. 날씨를 추측하지 않고 조회를 중단합니다.",
                           kind="weather", error_code="weather_unavailable")


def packing_advice(record):
    days = [d for forecast in record.get("forecasts", []) for d in forecast["days"]]
    def numbers(key):
        return [d[key] for d in days if d.get(key) is not None]
    advice = []
    rain, probability = numbers("precipitation_sum"), numbers("precipitation_probability_max")
    cold, hot = numbers("temperature_2m_min"), numbers("temperature_2m_max")
    wind, uv = numbers("wind_speed_10m_max"), numbers("uv_index_max")
    if (rain and max(rain) > 0) or (probability and max(probability) >= 40):
        advice.append("우산·우비, 젖은 물건을 넣을 봉투: 강수 예보가 있는 날에 대비하세요.")
    if cold and min(cold) <= 10:
        advice.append(f"따뜻한 음료를 담을 보온병: 예보 최저 기온이 {min(cold):g}°C입니다.")
    if hot and max(hot) >= 28:
        advice.append(f"물병·땀을 닦을 작은 수건: 예보 최고 기온이 {max(hot):g}°C입니다.")
    if any(d.get("temperature_2m_min") is not None and d.get("temperature_2m_max") is not None and d["temperature_2m_max"] - d["temperature_2m_min"] >= 10 for d in days):
        advice.append("벗은 겉옷을 담을 작은 가방: 하루 기온 차가 10°C 이상인 날이 있습니다.")
    if uv and max(uv) >= 6:
        advice.append(f"자외선 차단제: 예보 최대 자외선 지수가 {max(uv):g}입니다.")
    if any(d.get("weather_code") in (56, 57, 66, 67, 71, 73, 75, 77, 85, 86) for d in days):
        advice.append("여분 양말: 눈 또는 결빙성 강수로 양말이 젖을 때를 대비하세요.")
    return advice or ["조회된 예보에서 추가 방한·우천 준비물 기준에 해당하는 항목은 없었습니다. 기본 준비물은 여행 활동에 맞춰 정하세요."]


def clothing_advice(record):
    """날짜별 실제 수치로 추천하고 같은 날씨 조건의 옷차림은 묶습니다."""
    grouped = {}
    for forecast in record.get("forecasts", []):
        for day in forecast["days"]:
            low, high = day.get("temperature_2m_min"), day.get("temperature_2m_max")
            if low is None or high is None:
                outfit = "기온 자료가 부족해 옷 두께를 정하기 어렵습니다. 출발 전 기온을 확인하세요."
            else:
                if high >= 28:
                    base = "낮에는 통풍이 되는 반소매와 가벼운 바지"
                elif high >= 23:
                    base = "낮에는 반소매 또는 얇은 긴소매"
                elif high >= 17:
                    base = "긴소매와 긴바지"
                elif high >= 10:
                    base = "긴소매·니트와 재킷"
                else:
                    base = "보온 내의·니트와 두꺼운 외투"
                extras = []
                if low <= 5:
                    extras.append("아침·저녁용 보온 외투와 목도리·장갑")
                elif low <= 10:
                    extras.append("아침·저녁에 걸칠 보온 겉옷")
                elif low < 18:
                    extras.append("벗고 입기 쉬운 가디건이나 얇은 겉옷")
                if high - low >= 10:
                    extras.append("일교차에 맞춰 겹쳐 입기")
                outfit = f"최저 {low:g}°C / 최고 {high:g}°C: {base}" + (" · " + " · ".join(extras) if extras else "")
            rain, probability, wind, uv = (day.get(k) for k in ("precipitation_sum", "precipitation_probability_max", "wind_speed_10m_max", "uv_index_max"))
            if (rain is not None and rain > 0) or (probability is not None and probability >= 40):
                outfit += " · 비에 대비해 물에 강한 신발과 잘 마르는 옷"
            if wind is not None and wind >= 8:
                outfit += f" · 최대 바람 {wind:g}m/s에 대비한 바람막이"
            if uv is not None and uv >= 6:
                outfit += f" · 자외선 지수 {uv:g}에 대비한 챙 있는 모자"
            if day.get("weather_code") in (56, 57, 66, 67, 71, 73, 75, 77, 85, 86):
                outfit += " · 눈·결빙성 강수에 대비한 미끄럼을 줄이는 방수 신발"
            grouped.setdefault(outfit, []).append(f"{forecast['name']} {day['date']}")
    return [f"**{', '.join(labels)}** — {outfit}" for outfit, labels in grouped.items()]


def weather_answer(record):
    if record.get("status") != "success":
        return record["message"]
    regional = record.get("scope") == "region"
    heading = f"{record['forecasts'][0]['name']} 지역 날씨" if regional else "여행 날씨"
    lines = [f"### {heading} · {record['start_date']} ~ {record['end_date']}",
             ("행정구역 대표 지점 기준의 Open-Meteo 예보입니다. 지역 안에서도 위치에 따라 날씨가 다를 수 있습니다." if regional else "검색된 장소의 좌표로 조회한 Open-Meteo 예보입니다. 실제 날씨는 달라질 수 있습니다."),
             ("| 지역" if regional else "| 장소") + " | 날짜 | 예보 | 최저~최고 | 최대 강수확률 | 강수량 |",
             "|---|---|---|---|---|---|"]
    def number(value, unit):
        return "정보 없음" if value is None else f"{value:g}{unit}"
    for forecast in record["forecasts"]:
        name = forecast["name"].replace("|", " ").replace("\n", " ")
        for day in forecast["days"]:
            lines.append(f"| {name} | {day['date']} | {day['description']} | {number(day['temperature_2m_min'], '°C')}~{number(day['temperature_2m_max'], '°C')} | {number(day['precipitation_probability_max'], '%')} | {number(day['precipitation_sum'], 'mm')} |")
    if record.get("include_packing", True):
        lines += ["", "### 외출·여행 옷차림", *[f"- {item}" for item in clothing_advice(record)],
                  "", "### 예보를 바탕으로 챙길 준비물", *[f"- {item}" for item in packing_advice(record)], "",
                  "준비물 기준은 예보를 바탕으로 한 제안입니다. 출발 전에 예보를 다시 확인하세요."]
    lines += ["", f"조회: {record['checked_at']} · 날짜 기준: 한국 시간 · [날씨 출처: Open-Meteo]({SOURCE_URL})"]
    if record.get("missing_values"):
        lines.append("일부 예보 항목은 정보가 없어 표시하지 않았으며 준비물 판단에서도 제외했습니다.")
    return "\n\n".join(lines[:2]) + "\n\n" + "\n".join(lines[2:])

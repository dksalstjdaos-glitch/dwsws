"""여행 에이전트 — create_agent(ReAct 반복) + 도구 4개.

- search_places: 장소만 찾을 때. 결과를 보고 다시 찾을지·물을지·답할지 모델이 고른다.
- get_weather: 날씨만 물을 때. 좌표는 앱이 장소 검색 결과에서 찾는다.
- research_trip: 여행 계획을 만들거나 지역·날짜·취향이 바뀔 때. 같은 조건으로 A 장소·B 날씨를 조회·검사해
  세션의 '현재 계획'(trip_plan)을 만든다.
- update_plan: 시간 범위·방문 수·제외/다시 포함만 바뀔 때. 다시 검색하지 않고 현재 계획을 고친다.
답변·일정 미리보기·등록 데이터는 모두 이 현재 계획에서 나온다.
캘린더 등록 도구는 모델에 주지 않는다. 등록은 화면 버튼과 앱 코드(calendar_client)만 한다.
"""
import json
import os
import re
from datetime import date, timedelta

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ToolCallLimitMiddleware
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from . import planner, trip_plan
from config import load_settings
from dotenv import dotenv_values
from config import ENV_PATH

TOOL_LIMIT, MODEL_LIMIT = 4, 6
WEEKDAYS = "월화수목금토일"

RULES = """너는 국내 여행 계획을 돕는 여행 에이전트다. 오늘은 {today} (Asia/Seoul)이다.
날짜 달력(상대 날짜를 이 표로 바꾼다): {calendar}

[행동]
- 인사·잡담에는 도구를 부르지 않는다.
- 장소만 찾을 때는 search_places(region, kind). 날씨만 물을 때는 get_weather(region, day).
- 여행 계획을 처음 만들거나 지역·날짜·취향이 바뀌면 research_trip. 생략한 인자는 현재 계획의 값(시간 범위·방문 수·제외)을 이어받는다.
- 이미 만든 계획의 시간 범위·방문 수·제외/다시 포함만 바뀌면 update_plan 을 부른다(다시 검색하지 않음).
  시간은 24시간 HH:MM 으로 바꿔 넣는다(오후 2시 → 14:00, 오후 세 시 → 15:00). '한 곳' → visit_count=1.
  장소 이름은 현재 계획·후보에 있는 이름을 쓴다. 사용자 말이 어느 장소인지 모호하면 도구를 부르기 전에 묻는다.
- 사용자 문장 끝의 [현재 계획] 줄은 앱이 붙인 최신 계획이다(표에서 직접 고친 내용 포함). 계획을 고칠 때 이 내용을 기준으로 한다.
- 일정·날짜·지역·시간·방문 수·제외를 바꾸라는 요청은 반드시 research_trip 또는 update_plan 을 불러 적용한다. 도구를 부르지 않고 일정이 바뀌었다고 말하지 않는다.
  [현재 계획]과 다른 날짜·시간·장소·날씨 숫자는 이번 도구 결과에 있을 때만 쓴다(이전 답변을 고쳐 쓰지 않는다).
  '일정 미리보기에 반영된 계획' 같은 반영 표시는 앱이 붙이므로 직접 쓰지 않는다.
- update_plan·research_trip 결과의 status 가 confirm_needed·not_applied·no_change 면 일정을 바꿨다고 말하지 않는다.
  message 를 그대로 전하고 필요한 것만 묻는다. 사용자가 표 수정 덮어쓰기를 허락하면 override_table_edits=true 로 다시 부른다.
- 도구 결과를 보고 다음 행동을 고른다. 결과가 요청을 채우면(조건에 맞는 후보 3곳 이상) 더 검색하지 말고 그 결과로 답하고 멈춘다.
  0건(empty)이거나 부족하면 지역·핵심 조건은 그대로 두고 장소 종류(kind)만 바꿔 다시 찾는다. 같은 인자로 다시 부르지 않는다.
  지역이나 핵심 조건까지 바꿔야 하면 바꾸지 말고 사용자에게 묻는다.
- 필요한 정보가 없으면 도구를 부르지 말고 빠진 것만 묻는다: 장소 검색에는 지역만(날짜는 묻지 않는다), 날씨와 여행 계획에는 지역과 날짜.
- 사용자 문장 끝의 [앱이 계산한 날짜] 값을 그대로 쓴다. 직접 요일을 계산하지 않는다. 답변의 날짜는 도구 결과의 date_label 을 그대로 쓴다.
- 이전 대화에서 정한 지역·날짜·취향(예: 부모님, 실내)은 사용자가 바꾸지 않는 한 이어서 쓴다.
- status 가 error(인증·연결 오류)면 같은 도구를 다시 부르지 말고 원인과 사용자가 할 일을 안내하고 멈춘다. 장소·날씨를 지어내지 않는다.
- out_of_range 면 예보가 없다고 말하고 현재 날씨나 추측으로 채우지 않는다.

[답변]
- 장소명·주소·링크·날씨 숫자는 도구 결과에 있는 값만 쓴다. 영업시간·이동 시간·가격은 조회하지 않았으니 '확인 필요'로 적는다.
- 날씨를 말할 때는 날짜를 'YYYY-MM-DD(요일)'로, 기온은 °C, 강수확률은 %로 쓰고 출처 Open-Meteo를 적는다.
- research_trip·update_plan 결과로 여행 계획을 쓸 때(한국어 마크다운, JSON·코드 블록 금지):
  1) 맨 위에 조건(지역 · conditions.date_label · 취향)과 notes 의 문제를 그대로 적는다.
  2) 방문 일정은 visits 의 순서·시작·끝을 그대로 쓰고(빼거나 더하지 않는다), 각 항목에서 '조회 사실'(장소명·주소·링크)과 '제안'(시간대·순서)을 구분한다.
     바꾸지 않은 항목은 유지됐다고 쓴다. message 에 적힌 대체·제외 내용도 알린다.
  3) 날씨·준비물: weather.status 가 ok 면 최고·최저 °C, 강수확률 %, advice 를 쓴다(advice 가 비면 '예보 기준 특별한 준비물 없음').
     not_used 면 '날씨·준비물 확인 필요'와 이유(예보 범위 밖·조회 실패)를 쓰고 숫자를 만들지 않는다.
  4) places_ok 가 false 면 장소 일정을 만들지 않고 '장소 조회 실패' 이유를 쓴다.
  5) 마지막 줄에 출처(장소: 카카오 로컬 검색, 날씨: Open-Meteo.com)와 조회 시각을 쓴다.
  places_ok 가 true 면 여행 계획과 조회 근거를 확인하고 대화로 조건을 바꿀 수 있다고 안내한다.
- 캘린더 등록은 저장한 여행 계획의 '구글 캘린더에 바로 등록' 버튼에서만 실행한다. 도구로 등록하거나 등록했다고 말하지 않는다."""


def calendar_text(today: date) -> str:
    return ", ".join(f"{(today + timedelta(days=i)).isoformat()}({WEEKDAYS[(today + timedelta(days=i)).weekday()]})" for i in range(21))


_WD = r"(월|화|수|목|금|토|일)요일"


def date_hints(text: str, today: date) -> list[tuple[str, str]]:
    """사용자 문장의 상대 날짜를 코드로 계산한다(모델이 '이번 주 토요일'을 목요일로 바꾼 실행 기록이 있음).
    반환: [(원래 표현, 'YYYY-MM-DD(요일)')]"""
    import re
    found, used = [], []

    def add(m, d):
        if any(a < m.end() and m.start() < b for a, b in used):
            return
        used.append((m.start(), m.end()))
        found.append((m.group(0), planner.date_label(d.isoformat())))

    for m in re.finditer(r"(\d{4})-(\d{1,2})-(\d{1,2})", text):
        try:
            add(m, date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
        except ValueError:
            pass
    for m in re.finditer(r"(이번\s*주|이번|다음\s*주|담주)\s*" + _WD, text):
        monday = today - timedelta(days=today.weekday())
        if m.group(1).replace(" ", "") in ("다음주", "담주"):
            monday += timedelta(days=7)
        add(m, monday + timedelta(days=WEEKDAYS.index(m.group(2))))
    for m in re.finditer(r"다음\s*달\s*(\d{1,2})일", text):
        y, mo = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
        try:
            add(m, date(y, mo, int(m.group(1))))
        except ValueError:
            pass
    for m in re.finditer(r"(\d{1,2})월\s*(\d{1,2})일", text):
        try:
            d = date(today.year, int(m.group(1)), int(m.group(2)))
            add(m, d if d >= today else date(today.year + 1, d.month, d.day))
        except ValueError:
            pass
    for m in re.finditer(r"(\d{1,3})\s*일\s*(뒤|후)", text):
        add(m, today + timedelta(days=int(m.group(1))))
    for word, n in (("오늘", 0), ("내일", 1), ("모레", 2), ("글피", 3)):
        for m in re.finditer(word, text):
            add(m, today + timedelta(days=n))
    for m in re.finditer(_WD, text):  # '토요일에'처럼 앞말이 없으면 다가오는 그 요일
        add(m, today + timedelta(days=(WEEKDAYS.index(m.group(1)) - today.weekday()) % 7))
    return found


CHANGE_CUES = re.compile(r"바꿔|바꾸|변경|수정|줄여|늘려|빼|제외|추가|넣어|대신|옮겨|미뤄|당겨|오전|오후|\d+\s*시|\d+\s*곳|한\s*곳|두\s*곳|세\s*곳|날짜|지역")


def looks_like_change(text: str, today: date | None = None) -> bool:
    """현재 계획을 바꾸라는 요청으로 보이는지(날짜 표현 또는 변경 표현). 모델이 도구 없이 답했을 때 한 번 더 확인하는 데만 쓴다."""
    return bool(date_hints(text, today or date.today())) or bool(CHANGE_CUES.search(text))


RECHECK = ("[앱 확인: 위 요청은 현재 계획을 바꾸는 요청으로 보이는데 도구를 부르지 않았습니다. 바꾸는 요청이면 research_trip 또는 update_plan 을 불러 적용하고 "
           "그 결과로만 답하세요. 정보가 모자라면 필요한 것만 묻고, 계획 변경 요청이 아니면 일정이 그대로라고 답하세요.]")


def with_date_hints(text: str, today: date | None = None) -> str:
    """모델에 보낼 문장 뒤에 앱이 계산한 날짜를 붙인다(화면에는 원래 문장만 보임)."""
    hints = date_hints(text, today or date.today())
    if not hints:
        return text
    return text + "\n\n[앱이 계산한 날짜 — 그대로 사용: " + ", ".join(f"'{a}' = {b}" for a, b in hints) + "]"


AFFIRM = re.compile(r"^\s*(네|예|응|어|그래|좋아|좋습니다|괜찮|진행|그렇게|덮어|바꿔도|상관없)")


def is_affirmative(text: str) -> bool:
    """확인 질문에 대한 '네·그래요·진행해 주세요' 같은 동의."""
    return bool(AFFIRM.search(text or ""))


def _override_allowed(store: dict, requested: bool) -> bool:
    """표 수정 덮어쓰기는 앱이 확인 질문을 한 직후 사용자가 동의했을 때만 허용한다.
    모델 판단만으로는 덮어쓰지 않고, 사용자가 그 질문에 동의했으면 모델이 표시를 빠뜨려도 동의를 따른다."""
    return bool(store.get("pending_confirm") and store.get("affirmed"))


def build_tools(store: dict):
    """store 는 화면 세션 저장소. store['plan'] 이 현재 계획이며 앱의 근거 카드·일정 미리보기·등록이 그대로 쓴다."""

    @tool
    def search_places(region: str, kind: str = "") -> dict:
        """국내 장소를 지역과 장소 종류로 검색한다. region 예: '경주', '부산 해운대'. kind 예: '박물관', '미술관', '카페', '공원'.
        취향(실내 등)은 앱이 장소 종류로 바꾼다. 장소명·주소·분류·지도 링크를 돌려준다. 영업시간·가격은 포함하지 않는다."""
        return planner.search_places(region, kind)

    @tool
    def get_weather(region: str, day: str) -> dict:
        """지역(region)과 날짜(day: YYYY-MM-DD)의 일별 예보(최고·최저 °C, 강수확률 %)를 조회한다. 좌표는 앱이 찾는다.
        예보는 오늘부터 16일까지이며 범위 밖이면 out_of_range 를 돌려준다."""
        loc = planner.locate(region)
        if loc["status"] != "ok":
            return {"status": "error", "date": day, "forecast": None, "region": region, "message": loc["message"]}
        w = planner.get_weather_raw(loc["lat"], loc["lon"], day)
        return {**w, "date_label": planner.date_label(day), "region": region, "coordinates_from": loc["name"]}

    @tool
    def research_trip(region: str, day: str, preferences: str | None = None, visit_count: int | None = None,
                      start_time: str | None = None, end_time: str | None = None, exclude_places: list[str] | None = None,
                      override_table_edits: bool = False) -> dict:
        """여행 계획 조사·생성. 지역·날짜(day: YYYY-MM-DD)·취향으로 장소(카카오)와 날씨(Open-Meteo)를 조회·검사하고 현재 계획을 만든다.
        preferences 예: '실내', '박물관' — 장소 조건만(동행자는 넣지 않음). 생략하면 현재 계획의 취향을 쓴다.
        visit_count(방문 수)·start_time/end_time(HH:MM 시간 범위)·exclude_places(뺄 장소 이름)는 생략하면 현재 계획 값을 유지한다.
        사용자가 바꾸라고 한 값만 넣는다(날짜만 바꿀 때는 region·day 만).
        표에서 고친 일정을 다시 배치해야 하면 status=confirm_needed — 사용자가 허락하면 override_table_edits=true 로 다시 부른다.
        반환 visits 가 일정 미리보기와 같은 일정이다."""
        prev = store.get("plan")
        said = re.sub(r"\s+", "", store.get("user_text", ""))
        known_days = set(store.get("user_dates", [])) | ({prev["basis"]["cond"]["date"]} if prev else set())
        missing = []
        if re.sub(r"\s+", "", region) not in said and not (prev and region.strip() == prev["basis"]["cond"]["region"]):
            missing.append("지역")
        if day not in known_days and day not in store.get("user_text", ""):
            missing.append("날짜")
        if missing:  # 모델이 지역·날짜를 추측해 채우는 것을 코드에서 막는다
            guessed = [f"지역 '{region}'" if "지역" in missing else "", f"날짜 '{day}'" if "날짜" in missing else ""]
            return {"status": "need_info", "ask_user_for": missing,
                    "message": f"사용자가 {'·'.join(missing)}을(를) 아직 말하지 않았습니다. 넣었던 {' · '.join(x for x in guessed if x)}는 추측이므로 답변에 쓰지 말고, "
                               f"{'·'.join(missing)}을(를) 사용자에게 물어보세요. 일정 미리보기는 바뀌지 않았습니다."}
        if preferences is None:
            preferences = prev["basis"]["cond"]["preferences"] if prev else ""
        cond = planner.make_conditions(region, day, preferences)
        basis = planner.research(cond)
        res = trip_plan.new_plan(basis, prev, visit_count, start_time, end_time, exclude_places, _override_allowed(store, override_table_edits))
        store["pending_confirm"] = res["status"] == "confirm_needed"
        if res["status"] != "ok":
            return {"status": res["status"], "message": res["message"] + " 일정 미리보기는 바뀌지 않았습니다.",
                    "current_plan": trip_plan.summary_line(prev)}
        store["plan"] = res["plan"]
        return trip_plan.for_agent(res["plan"], "ok", res["message"])

    @tool
    def update_plan(start_time: str | None = None, end_time: str | None = None, visit_count: int | None = None,
                    exclude_places: list[str] | None = None, restore_places: list[str] | None = None,
                    override_table_edits: bool = False) -> dict:
        """현재 여행 계획의 시간 범위(HH:MM)·방문 수·제외할 장소·다시 넣을 장소를 바꾼다. 다시 검색하지 않고 검증된 후보 안에서 고친다.
        바꾸지 않을 값은 넣지 않는다(현재 값을 다시 넣지 않는다). 표에서 고친 시간을 덮어써야 하면 status=confirm_needed 를 돌려주며,
        사용자가 허락하면 override_table_edits=true 로 다시 부른다. 반환 visits 가 일정 미리보기와 같은 일정이다."""
        res = trip_plan.update_plan(store.get("plan"), start_time, end_time, visit_count, exclude_places, restore_places,
                                    _override_allowed(store, override_table_edits))
        store["pending_confirm"] = res["status"] == "confirm_needed"
        if res["status"] != "updated":
            out = {"status": res["status"], "message": res["message"] + (" 일정 미리보기는 바뀌지 않았습니다." if res["status"] != "no_change" else "")}
            if store.get("plan"):
                out["current_plan"] = trip_plan.summary_line(store["plan"])
            return out
        store["plan"] = res["plan"]
        return trip_plan.for_agent(res["plan"], "updated", res["message"])

    return [search_places, get_weather, research_trip, update_plan]


def model_ready() -> bool:
    try:
        load_settings()
        return True
    except ValueError:
        return False


def build_agent(store: dict, today: date | None = None, tool_factory=None):
    today = today or date.today()
    settings = load_settings()
    values = dotenv_values(ENV_PATH, encoding="utf-8-sig", interpolate=False)
    return create_agent(
        model=ChatOpenAI(model=values.get("OPENAI_MODEL") or settings.model_name,
                         api_key=settings.api_key, timeout=45, max_retries=0),
        tools=(tool_factory or build_tools)(store),
        system_prompt=RULES.format(today=f"{today.isoformat()}({WEEKDAYS[today.weekday()]})", calendar=calendar_text(today)),
        middleware=[ToolCallLimitMiddleware(run_limit=TOOL_LIMIT),                      # 한 번의 요청에서 도구 4회까지
                    ModelCallLimitMiddleware(run_limit=MODEL_LIMIT, exit_behavior="end")],  # 모델 6회에서 멈춤
    )


def _status(content) -> str:
    try:
        return json.loads(content).get("status", "?")
    except Exception:  # noqa: BLE001 — 호출 한도 안내처럼 JSON 이 아닌 반환
        return "?"


PLAN_TOOLS = ("research_trip", "update_plan")
PLAN_OK = ("ok", "updated")


def plan_outcomes(messages) -> list[dict]:
    """이번 실행에서 계획 도구가 돌려준 status·message (모델 문장이 아니라 도구 결과)."""
    names = {c.get("id"): c["name"] for m in messages for c in (getattr(m, "tool_calls", None) or [])}
    out = []
    for m in messages:
        if getattr(m, "type", None) == "tool" and names.get(getattr(m, "tool_call_id", None)) in PLAN_TOOLS:
            try:
                d = json.loads(m.content)
            except Exception:  # noqa: BLE001
                d = {"status": "?", "message": str(m.content)[:200]}
            out.append({"tool": names[m.tool_call_id], "status": d.get("status"), "message": d.get("message", ""), "ask_user_for": d.get("ask_user_for")})
    return out


def not_applied_answer(outcomes: list[dict]) -> str | None:
    """계획 도구를 불렀지만 하나도 적용되지 않았으면 도구 결과로 답을 만든다(모델이 '바꿨다'고 쓰지 못하게)."""
    if not outcomes or any(o["status"] in PLAN_OK for o in outcomes):
        return None
    last = outcomes[-1]
    if last["status"] == "need_info":
        need = last.get("ask_user_for") or ["지역", "날짜"]
        return "여행 계획을 만들려면 " + ("지역과 날짜를" if len(need) > 1 else ("지역을" if need[0] == "지역" else "날짜를")) + " 알려 주세요."
    if last["status"] == "no_change":
        return "바뀐 조건이 없어 일정은 그대로입니다."
    msg = (last["message"] or "요청한 변경을 적용하지 못했습니다.").replace(" 일정 미리보기는 바뀌지 않았습니다.", "")
    return f"요청한 변경은 아직 적용하지 않았습니다. {msg}"


def tool_log(messages) -> list[dict]:
    """도구 요청과 반환을 tool_call_id 로 짝지은 실행 기록. 모델의 내부 생각은 기록하지 않는다."""
    log, by_id = [], {}
    for m in messages:
        for c in getattr(m, "tool_calls", None) or []:
            row = {"tool": c["name"], "args": c["args"], "status": "반환 없음"}
            log.append(row)
            if c.get("id"):
                by_id[c["id"]] = row
        if getattr(m, "type", None) == "tool" and getattr(m, "tool_call_id", None) in by_id:
            by_id[m.tool_call_id]["status"] = _status(m.content)
    return log


ERROR_HELP = {
    "AuthenticationError": "OpenAI 인증 오류입니다. .env 의 OPENAI_API_KEY 를 확인하세요.",
    "RateLimitError": "OpenAI 사용 한도·크레딧이 부족합니다. 반복하지 말고 결제·한도를 확인하세요.",
    "NotFoundError": "모델 이름을 찾을 수 없습니다. .env 의 OPENAI_MODEL 을 확인하세요.",
    "APIConnectionError": "OpenAI 에 연결하지 못했습니다. 네트워크를 확인한 뒤 다시 보내 주세요.",
    "APITimeoutError": "OpenAI 응답이 늦어 멈췄습니다. 잠시 후 다시 보내 주세요.",
}


def explain_error(e: Exception) -> str:
    return ERROR_HELP.get(type(e).__name__, f"답변을 만들지 못했습니다 ({type(e).__name__}). 같은 요청을 반복하기 전에 설정을 확인하세요.")

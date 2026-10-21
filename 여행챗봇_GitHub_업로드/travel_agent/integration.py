"""5일차 에이전트를 기존 대화·조회 기록·보관 계획 형식에 연결합니다."""
import copy
import json
from contextlib import contextmanager

from dotenv import dotenv_values
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import HumanMessage, AIMessage
from langchain_core.tools import StructuredTool
from langsmith import Client, tracing_context

from config import ENV_PATH
from execution_trace import ExecutionTrace
from weather import weather_answer
from . import agent, trip_plan
from .sources import lookup_sink, today


def current_state(history):
    for message in reversed(history or []):
        state = message.get("execution", {}).get("day5_state")
        if state is not None:
            return copy.deepcopy(state)
    return {}


def _guard_tools(store, tools):
    """추측한 인자·중복 호출·실패 후 반복을 API 호출 전에 차단합니다."""
    seen = set()
    def wrap(original):
        def call(**args):
            signature = (original.name, json.dumps(args, sort_keys=True, ensure_ascii=False))
            if store.get("halt") or signature in seen:
                return {"status": "blocked", "message": "오류 또는 동일 호출 반복으로 중단했습니다. 사용자에게 필요한 정보를 확인하세요."}
            region = args.get("region", "").strip()
            known = store.get("user_text", "").replace(" ", "")
            plan_region = (store.get("plan") or {}).get("basis", {}).get("cond", {}).get("region", "")
            if original.name in ("search_places", "get_weather", "research_trip") and (
                not region or (region.replace(" ", "") not in known and region != plan_region)
            ):
                return {"status": "need_info", "message": "지역을 추측하지 말고 사용자에게 지역을 물어보세요.", "ask_user_for": ["지역"]}
            if original.name in ("get_weather", "research_trip") and args.get("day") not in store.get("user_dates", []):
                previous = (store.get("plan") or {}).get("basis", {}).get("cond", {}).get("date")
                if args.get("day") != previous:
                    return {"status": "need_info", "message": "날짜를 추측하지 말고 사용자에게 날짜를 물어보세요.", "ask_user_for": ["날짜"]}
            seen.add(signature)
            result = original.func(**args)
            if result.get("status") == "error" or (original.name == "research_trip" and
                (store.get("plan") or {}).get("basis", {}).get("a", {}).get("status") == "error"):
                store["halt"] = True
            return result
        return StructuredTool.from_function(call, name=original.name, description=original.description,
                                             args_schema=original.args_schema)
    return [wrap(tool) for tool in tools]


class TraceCallback(BaseCallbackHandler):
    def __init__(self, trace):
        self.trace = trace
        self.calls = {}

    def on_chat_model_start(self, serialized, messages, **kwargs):
        self.trace.model_call("5일차 에이전트 도구 선택·답변")

    def on_tool_start(self, serialized, input_str, *, run_id, **kwargs):
        name = (serialized or {}).get("name", "도구")
        self.calls[run_id] = name
        try:
            args = kwargs.get("inputs") or json.loads(input_str)
        except (ValueError, TypeError):
            args = {}
        self.trace.tool_call(name, args if isinstance(args, dict) else {})

    def on_tool_end(self, output, *, run_id, **kwargs):
        try:
            data = output if isinstance(output, dict) else json.loads(getattr(output, "content", output))
        except (ValueError, TypeError):
            data = {}
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except (ValueError, TypeError):
                data = {}
        if not isinstance(data, dict):
            data = {}
        places = data.get("places", [])
        if isinstance(places, dict):
            places = places.get("items", [])
        places = [p for p in places if isinstance(p, dict)] if isinstance(places, list) else []
        self.trace.tool_return({"tool": self.calls.get(run_id), "status": data.get("status", "unknown"),
                                "places": places, "kind": "places" if "places" in data else "agent"})

    def on_tool_error(self, error, *, run_id, **kwargs):
        self.trace.tool_return({"tool": self.calls.get(run_id), "status": "failed", "error_code": "unexpected_failure"})


@contextmanager
def tracing():
    from langsmith_tracing import tracing_session
    with tracing_session(dotenv_values(ENV_PATH, encoding="utf-8-sig", interpolate=False)) as state:
        yield state["enabled"]


def plan_execution(plan, execution):
    """미리보기·보관·캘린더가 같은 방문 데이터와 확정된 시작/끝을 씁니다."""
    execution["trip_schedule"] = [{"date": v["start"][:10], "time": v["start"][-5:],
        "end_time": v["end"][-5:], "order": i + 1,
        "place": {"name": v["title"], "address": v["address"], "url": v["url"]},
        "reason": "5일차 에이전트 제안 · 조회 근거 확인"} for i, v in enumerate(plan["visits"])]
    execution["answer_basis"] = [r["place"] for r in execution["trip_schedule"]]
    execution["plan_validation"] = [{"항목": "장소", "통과": plan["basis"]["check"]["places_ok"]},
                                     {"항목": "날씨", "통과": plan["basis"]["check"]["weather_ok"]}]


def answer_question(question, history=None, lookup_records=None, execution_trace=None):
    if not question.strip():
        raise ValueError("여행 질문을 입력해 주세요.")
    records = lookup_records if lookup_records is not None else []
    run = ExecutionTrace(execution_trace)
    store = current_state(history)
    current_day = today()
    store["user_text"] = " ".join(m["content"] for m in history or [] if m["role"] == "user") + " " + question
    store["affirmed"] = agent.is_affirmative(question)
    store["user_dates"] = sorted(set(store.get("user_dates", [])) |
                                {d[1][:10] for d in agent.date_hints(store["user_text"], current_day)})
    store.pop("halt", None)
    sent = agent.with_date_hints(question, current_day)
    if store.get("plan"):
        sent += "\n\n[현재 계획] " + trip_plan.summary_line(store["plan"])
    messages = [HumanMessage(m["content"]) if m["role"] == "user" else AIMessage(m["content"])
                for m in (history or [])[-20:]]
    callback = TraceCallback(run)
    token = lookup_sink.set(records)
    try:
        # 에이전트 팩토리에 전달할 도구만 이번 실행의 보호 래퍼로 바꿉니다.
        runner = agent.build_agent(store, current_day, tool_factory=lambda s: _guard_tools(s, agent.build_tools(s)))
        with tracing() as tracing_enabled:
            output = runner.invoke({"messages": [*messages, HumanMessage(sent)]},
                                   config={"callbacks": [callback], "run_name": "여행 계획 에이전트", "recursion_limit": 20})
        result_messages = output["messages"][len(messages) + 1:]
        outcomes = agent.plan_outcomes(result_messages)
        answer = agent.not_applied_answer(outcomes)
        plan = store.get("plan")
        if not answer:
            content = output["messages"][-1].content or ""
            if isinstance(content, list):
                content = "\n".join(block if isinstance(block, str) else block.get("text", "")
                                    for block in content if isinstance(block, (str, dict)))
            answer = trip_plan.strip_fake_block(content)
        if plan:
            # 모델이 적은 별도 일정 대신 실제 현재 계획으로 본문을 구성합니다.
            if outcomes and any(o["status"] in agent.PLAN_OK for o in outcomes):
                answer = plan_markdown(plan, records or store.get("basis_lookups", []))
            run.data["day5_state"] = {"plan": plan, "pending_confirm": store.get("pending_confirm", False),
                                       "user_dates": store["user_dates"],
                                       "basis_lookups": records or store.get("basis_lookups", [])}
            if outcomes and any(o["status"] in agent.PLAN_OK for o in outcomes):
                plan_execution(plan, run.data)
        if not outcomes and plan and agent.looks_like_change(question, current_day):
            answer = "이번 요청의 계획 변경은 적용되지 않았습니다. 바꿀 시간·방문 수·장소를 구체적으로 알려 주세요.\n\n" + trip_plan.schedule_markdown(plan)
        run.data["langsmith_enabled"] = tracing_enabled
        run.data["lookup_calls"] = len(records)
        run.event("answer", basis_count=len(run.data.get("answer_basis", [])))
        run.finish("5일차 에이전트 답변 완료")
        return answer.strip() or "필요한 지역·날짜·여행 조건을 알려 주세요."
    except Exception:
        run.finish("5일차 에이전트 실행 실패 · 설정·연결 확인 필요")
        raise
    finally:
        lookup_sink.reset(token)


def plan_markdown(plan, records=()):
    cond = plan["basis"]["cond"]
    lines = [f"### {cond['region']} 여행 계획 · {cond['date']}",
             f"취향: {cond['preferences'] or '미정'} · 계획 v{plan['version']}",
             "시간대·순서는 제안입니다. 영업시간·휴무·가격·실제 이동시간은 확인이 필요합니다.",
             "| 날짜 | 시작 | 종료 | 장소 | 주소 |", "|---|---|---|---|---|"]
    def clean(text):
        return str(text or "").replace("|", " ").replace("\n", " ")
    for v in plan["visits"]:
        name = clean(v["title"])
        if v["url"].startswith(("https://place.map.kakao.com/", "http://place.map.kakao.com/")):
            name = f"[{name}]({v['url']})"
        lines.append(f"| {v['start'][:10]} | {v['start'][-5:]} | {v['end'][-5:]} | {name} | {clean(v['address'])} |")
    lines += ["", *plan["basis"]["check"]["notes"], *plan.get("notes", [])]
    weather = next((r for r in reversed(records) if r.get("kind") == "weather" and r.get("status") == "success"), None)
    if plan["basis"]["check"]["weather_ok"] and weather:
        lines += ["", weather_answer(weather)]
    else:
        lines += ["", "날씨·옷차림·준비물 확인 필요: 사용할 수 있는 같은 날짜의 예보가 없습니다."]
    return "\n".join(lines)

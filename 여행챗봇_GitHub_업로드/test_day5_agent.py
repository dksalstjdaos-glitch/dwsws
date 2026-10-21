"""제공 코드 통합: 조사·대화 수정·필수 조건·저장·캘린더 시각 일치 검증."""
import copy
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from streamlit.testing.v1 import AppTest

import storage
from config import Settings
from google_calendar import calendar_events
from travel_agent import planner, trip_plan, agent
from travel_agent.integration import answer_question, current_state, _guard_tools


class ToolModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def basis():
    cond = planner.make_conditions("경주", "2026-10-23", "박물관")
    a = {"status": "ok", "input": cond, "places": [
        {"name": f"박물관{i}", "address": f"경북 경주시 일정로 {i}", "category": "박물관",
         "url": f"https://place.map.kakao.com/{i}", "lat": 35.8, "lon": 129.2} for i in range(1, 4)]}
    b = {"status": "out_of_range", "input": cond, "date": cond["date"], "message": "예보 없음"}
    return {"cond": cond, "a": a, "b": b, "check": planner.combine_status(cond, a, b), "id": "test-basis"}


class Day5Tests(unittest.TestCase):
    def setUp(self):
        for target, value in (("travel_agent.agent.load_settings", Settings(api_key="test-only")),
                              ("travel_agent.integration.today", date(2026, 10, 21))):
            mocked = patch(target, return_value=value)
            mocked.start()
            self.addCleanup(mocked.stop)
        mocked = patch("travel_agent.integration.dotenv_values", return_value={})
        mocked.start()
        self.addCleanup(mocked.stop)

    def run_agent(self, question, tool_name, args, history=None):
        model = ToolModel(responses=[AIMessage(content="", tool_calls=[
            {"name": tool_name, "args": args, "id": "call1", "type": "tool_call"}]),
            AIMessage(content="도구 결과로 만든 설명")])
        execution = {}
        with patch("travel_agent.agent.ChatOpenAI", return_value=model):
            answer = answer_question(question, history=history, execution_trace=execution)
        return answer, execution

    def test_t01_then_t02_uses_current_plan_without_research(self):
        with patch("travel_agent.planner.research", return_value=basis()) as research:
            first, trace = self.run_agent("모레 경주에서 박물관 중심으로 하루 여행 계획을 만들어 줘.",
                "research_trip", {"region": "경주", "day": "2026-10-23", "preferences": "박물관"})
            self.assertEqual(research.call_count, 1)
        self.assertEqual(trace["tool_calls"], 1)
        self.assertTrue(any(e.get("event") == "tool_return" and e.get("status") == "ok" for e in trace["events"]))
        self.assertEqual(len(trace["trip_schedule"]), 3)
        history = [{"role": "user", "content": "모레 경주에서 박물관 중심으로 하루 여행 계획을 만들어 줘."},
                   {"role": "assistant", "content": first, "execution": trace}]
        with patch("travel_agent.planner.research") as research:
            second, revised = self.run_agent("방문은 두 곳만, 오후 1시부터 5시까지로 바꿔 줘.", "update_plan",
                {"visit_count": 2, "start_time": "13:00", "end_time": "17:00"}, history)
            research.assert_not_called()
        plan = revised["day5_state"]["plan"]
        self.assertEqual(plan["basis"]["cond"]["region"], "경주")
        self.assertEqual(len(plan["visits"]), 2)
        self.assertTrue(plan["visits"][0]["start"].endswith("13:00"))
        self.assertTrue(plan["visits"][-1]["end"].endswith("17:00"))
        self.assertIn("13:00", second)
        self.assertIn("17:00", second)
        saved = {"snapshot": {"execution": revised}, "title": "수정 계획", "source_run_id": revised["run_id"]}
        events = calendar_events(saved)
        self.assertEqual(events[0]["start"]["dateTime"], "2026-10-23T13:00:00+09:00")
        self.assertEqual(events[-1]["end"]["dateTime"], "2026-10-23T17:00:00+09:00")
        self.assertNotIn("test-only", str(revised))
        self.assertEqual(current_state([]), {})

    def test_t03_guessed_conditions_do_not_call_sources(self):
        with patch("travel_agent.planner.research") as research:
            answer, trace = self.run_agent("여행 계획을 만들어 줘.", "research_trip",
                {"region": "경주", "day": "2026-10-23"})
            research.assert_not_called()
        self.assertNotIn("trip_schedule", trace)
        self.assertIn("알려 주세요", answer)

    def test_model_text_blocks_are_supported(self):
        model = ToolModel(responses=[AIMessage(content=[{"type": "text", "text": "안녕하세요."}])])
        with patch("travel_agent.agent.ChatOpenAI", return_value=model):
            trace = {}
            answer = answer_question("안녕", execution_trace=trace)
        self.assertEqual(answer, "안녕하세요.")
        self.assertEqual(trace["tool_calls"], 0)

    def test_auth_error_stops_remaining_lookups(self):
        store = {"user_text": "경주 박물관", "user_dates": []}
        tools = _guard_tools(store, agent.build_tools(store))
        search = next(t for t in tools if t.name == "search_places")
        with patch("travel_agent.planner.search_places", return_value={"status": "error", "places": []}) as lookup:
            search.invoke({"region": "경주", "kind": "박물관"})
            blocked = search.invoke({"region": "경주", "kind": "명소"})
            self.assertEqual(lookup.call_count, 1)
        self.assertEqual(blocked["status"], "blocked")

    def test_empty_preferences_are_not_silently_relaxed(self):
        calls = []
        def empty(query, size):
            calls.append(query)
            return {"status": "empty", "places": []}
        planner.role_a_places(planner.make_conditions("경주", "2026-10-23", "박물관"), empty)
        self.assertEqual(calls, ["경주 박물관"])

    def test_mode_ui_and_plan_save_preserve_project_storage(self):
        with tempfile.TemporaryDirectory() as temp, patch("storage.DB_PATH", Path(temp) / "test.sqlite3"):
            storage.initialize()
            project = storage.create_project("통합 검증")
            app = AppTest.from_file("app.py")
            app.session_state["project_open"] = True
            app.run()
            app.radio(key="travel_agent_mode").set_value("5일차 계획 에이전트").run()
            self.assertFalse(app.exception)
            self.assertTrue(any(t.label == "여행 질문" for t in app.text_input))
            chat = storage.list_conversations(project)[0]["id"]
            plan = trip_plan.new_plan(basis())["plan"]
            from travel_agent.integration import plan_execution, plan_markdown
            execution = {"run_id": "saved-test", "day5_state": {"plan": plan}}
            plan_execution(plan, execution)
            message = {"role": "assistant", "content": plan_markdown(plan), "execution": execution}
            storage.append_turn(chat, "계획", message["content"], execution=execution)
            stored = storage.save_itinerary(project, "에이전트 계획", message)
            self.assertEqual(storage.load_itinerary(project, stored)["snapshot"]["execution"]["trip_schedule"], execution["trip_schedule"])
            app.run()
            self.assertFalse(app.exception)


if __name__ == "__main__":
    unittest.main()

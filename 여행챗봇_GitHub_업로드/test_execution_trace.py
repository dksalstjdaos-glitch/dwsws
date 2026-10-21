"""실행 순서·재검색 근거·최종 답변 근거·영구 저장·비교 화면을 검증합니다."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage
from streamlit.testing.v1 import AppTest

import chatbot
import storage
from config import Settings
from execution_trace import ExecutionTrace
from kakao_maps import RouteResult
from test_search_policy import tool_call


def places(count):
    return RouteResult("실제 카카오 결과", status="success" if count else "empty", kind="places",
                       query="경주 박물관", checked_at="2026-10-20 KST", places=[{
                           "name": f"확인 장소 {i}", "address": f"경주 주소 {i}",
                           "url": f"https://place.map.kakao.com/{i}",
                       } for i in range(count)])


class ExecutionTraceTests(unittest.TestCase):
    def execute(self, calls, results, question="경주 실내 장소", final="기록에 없는 가짜 장소"):
        trace, records = {}, []
        model = MagicMock()
        model.bind_tools.return_value.invoke.side_effect = calls
        model.invoke.return_value = AIMessage(content=final)
        with patch("chatbot.load_settings", return_value=Settings(api_key="fake-secret", kakao_api_key="fake-secret")), patch(
            "chatbot.ChatOpenAI", return_value=model
        ), patch("chatbot.search_places", side_effect=results):
            answer = chatbot.answer_question(question, lookup_records=records, execution_trace=trace)
        return answer, trace, records

    def test_single_search_has_counts_limits_and_finish(self):
        answer, trace, _ = self.execute([tool_call("경주 실내 장소")], [places(3)])
        self.assertEqual(trace["tool_calls"], 1)
        self.assertEqual(trace["model_calls"], 2)
        self.assertEqual(trace["limits"], {"tools": 4, "models": 6})
        self.assertEqual(trace["end_reason"], "여행 날짜 확인 대기")
        self.assertTrue(trace["ended_at"])
        self.assertGreaterEqual(trace["elapsed_ms"], 0)
        self.assertEqual(trace["events"][-1]["event"], "finished")
        self.assertIn("확인 장소 0", answer)
        self.assertNotIn("가짜 장소", answer)
        self.assertNotIn("fake-secret", json.dumps(trace))

    def test_retry_records_return_then_reason_then_changed_arguments(self):
        answer, trace, records = self.execute([
            tool_call("경주 실내 장소"), tool_call("경주 박물관", call_id="second"),
        ], [places(0), places(3)])
        self.assertEqual(trace["tool_calls"], 2)
        self.assertEqual(trace["model_calls"], 3)
        events = trace["events"]
        empty_index = next(i for i, e in enumerate(events) if e.get("status") == "empty")
        retry_index = next(i for i, e in enumerate(events) if e.get("action") == "재검색")
        call_indices = [i for i, e in enumerate(events) if e["event"] == "tool_call"]
        self.assertLess(empty_index, retry_index)
        self.assertLess(retry_index, call_indices[1])
        self.assertNotEqual(events[call_indices[0]]["arguments"]["query"], events[call_indices[1]]["arguments"]["query"])
        self.assertEqual(trace["answer_basis"], records[-1]["places"])
        for place in records[-1]["places"]:
            self.assertIn(place["name"], answer)

    def test_missing_region_is_waiting_and_zero_actual_calls(self):
        _, trace, records = self.execute([tool_call("경주 실내 장소")], [], question="부모님과 갈 실내 장소")
        self.assertEqual(trace["tool_calls"], 0)
        self.assertEqual(records, [])
        self.assertEqual(trace["end_reason"], "지역 확인 대기")

    def test_authentication_failure_has_error_code_and_no_retry(self):
        _, trace, _ = self.execute([tool_call("경주 실내 장소")], [RouteResult(
            "인증 실패 · 키 설정 확인 필요", kind="places", error_code="authentication_error")])
        self.assertEqual(trace["tool_calls"], 1)
        self.assertEqual(trace["model_calls"], 1)
        self.assertEqual(trace["answer_basis"], [])
        self.assertTrue(any(e.get("error_code") == "authentication_error" for e in trace["events"]))
        self.assertIn("실패", trace["end_reason"])

    def test_model_failure_finishes_safe_trace_without_exception_text(self):
        trace = {}
        model = MagicMock()
        model.bind_tools.return_value.invoke.side_effect = RuntimeError("fake-secret header Authorization")
        with patch("chatbot.load_settings", return_value=Settings(api_key="fake-secret", kakao_api_key="fake-secret")), patch(
            "chatbot.ChatOpenAI", return_value=model
        ), self.assertRaises(RuntimeError):
            chatbot.answer_question("경주 카페", execution_trace=trace)
        self.assertEqual(trace["model_calls"], 1)
        self.assertTrue(trace["ended_at"])
        self.assertNotIn("fake-secret", json.dumps(trace))
        self.assertNotIn("Authorization", json.dumps(trace))

    def test_greeting_records_zero_model_and_tool_calls(self):
        trace = {}
        chatbot.answer_question("안녕", execution_trace=trace)
        self.assertEqual((trace["tool_calls"], trace["model_calls"]), (0, 0))
        self.assertTrue(trace["ended_at"])

    def test_runs_and_answer_metadata_survive_reload_and_project_deletion(self):
        with tempfile.TemporaryDirectory() as folder, patch("storage.DB_PATH", Path(folder) / "test.sqlite3"):
            storage.initialize()
            project = storage.create_project("비교")
            chat = storage.create_conversation(project)
            _, trace, records = self.execute([tool_call("경주 실내 장소")], [places(3)])
            storage.append_turn(chat, "경주 실내 장소", "답변", lookups=records, execution=trace)
            storage.save_execution(chat, "경주 실내 장소", "답변", trace)
            self.assertEqual(storage.load_messages(chat)[-1]["execution"], trace)
            self.assertEqual(storage.list_executions(project)[0]["execution"], trace)
            storage.delete_project(project)
            with storage.connect() as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM execution_runs").fetchone()[0], 0)

    def test_comparison_shows_single_and_retry_runs_side_by_side(self):
        runs = []
        for count in (1, 2):
            trace = ExecutionTrace()
            trace.data["tool_calls"] = count
            trace.finish("1회 검색 완료" if count == 1 else "0건 후 재검색 완료")
            runs.append({"id": trace.data["run_id"], "question": "경주 실내 장소", "answer": "확인한 답변", "execution": trace.data})
        app = AppTest.from_string("from trace_view import render_comparison\nrender_comparison(" + repr(runs) + ")").run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.selectbox), 2)
        self.assertNotEqual(app.selectbox[0].value, app.selectbox[1].value)
        text = "\n".join(item.value for item in app.markdown)
        self.assertIn("1회 검색 완료", text)
        self.assertIn("0건 후 재검색 완료", text)

    def test_app_persists_and_renders_trace_after_real_control_flow(self):
        with tempfile.TemporaryDirectory() as folder, patch("storage.DB_PATH", Path(folder) / "test.sqlite3"):
            storage.initialize()
            project = storage.create_project("실행 확인")
            model = MagicMock()
            model.bind_tools.return_value.invoke.return_value = tool_call("경주 실내 장소")
            model.invoke.return_value = AIMessage(content="기록에 없는 장소")
            with patch("chatbot.load_settings", return_value=Settings(api_key="fake-secret", kakao_api_key="fake-secret")), patch(
                "chatbot.ChatOpenAI", return_value=model
            ), patch("chatbot.search_places", return_value=places(3)):
                app = AppTest.from_file("app.py")
                app.session_state["project_open"] = True
                app.run()
                app.text_input(key="question").set_value("경주 실내 장소")
                next(b for b in app.button if b.label == "답변 받기").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(len(storage.list_executions(project)), 1)
            self.assertTrue(any("도구 1/4회 · 모델 2/6회" in e.label for e in app.expander))
            self.assertIn("execution", app.session_state["messages"][-1])

    def test_app_stores_failed_run_without_secret_and_without_chat_turn(self):
        with tempfile.TemporaryDirectory() as folder, patch("storage.DB_PATH", Path(folder) / "test.sqlite3"):
            storage.initialize()
            project = storage.create_project("실패 확인")
            model = MagicMock()
            model.bind_tools.return_value.invoke.side_effect = RuntimeError("fake-secret")
            with patch("chatbot.load_settings", return_value=Settings(api_key="fake-secret", kakao_api_key="fake-secret")), patch(
                "chatbot.ChatOpenAI", return_value=model
            ):
                app = AppTest.from_file("app.py")
                app.session_state["project_open"] = True
                app.run()
                app.text_input(key="question").set_value("경주 실내 장소")
                next(b for b in app.button if b.label == "답변 받기").click().run()
            self.assertFalse(app.exception)
            runs = storage.list_executions(project)
            self.assertEqual(len(runs), 1)
            self.assertNotIn("fake-secret", json.dumps(runs))
            self.assertEqual(app.session_state["messages"], [])
            self.assertTrue(any(e.label == "실패한 실행 기록" for e in app.expander))


if __name__ == "__main__":
    unittest.main()

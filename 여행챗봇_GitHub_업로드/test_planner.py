"""여행 조건, 방문 순서, 지도 데이터와 일정 작성 연결을 검증합니다."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from streamlit.testing.v1 import AppTest

import chatbot
import storage
from config import Settings
from planner import map_data


class PlannerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        db_patch = patch("storage.DB_PATH", Path(temp.name) / "planner.sqlite3")
        db_patch.start()
        self.addCleanup(db_patch.stop)
        storage.initialize()
        self.project = storage.create_project("부산 계획")

    def test_conditions_and_order_are_persisted_without_cross_project_leak(self):
        conditions = {"destination": "부산", "budget": 200000, "people": 2}
        storage.save_plan(self.project, conditions=conditions)
        storage.save_plan(self.project, selected_places=[{"key": "b"}, {"key": "a"}])
        storage.initialize()
        self.assertEqual(storage.load_plan(self.project), {"conditions": conditions, "selected_places": [{"key": "b"}, {"key": "a"}]})
        other = storage.create_project("다른 여행")
        self.assertEqual(storage.load_plan(other), {"conditions": {}, "selected_places": []})
        storage.delete_project(self.project)
        self.assertEqual(storage.load_plan(self.project), {"conditions": {}, "selected_places": []})

    def test_map_uses_verified_coordinates_and_selected_order(self):
        a = {"key": "a", "name": "장소A", "x": "129.0", "y": "35.1"}
        b = {"key": "b", "name": "장소B", "x": "129.2", "y": "35.2"}
        invalid = {"key": "invalid", "name": "좌표 없음", "x": "nan", "y": "35.0"}
        points, path = map_data([a, b, invalid], [b, a])
        self.assertEqual(len(points), 2)
        self.assertEqual(path, [[129.2, 35.2], [129.0, 35.1]])
        self.assertEqual([point["label"] for point in points], ["2", "1"])

    def test_context_is_sent_as_data_even_with_braces(self):
        captured = []
        context = {"conditions": {"destination": "부산", "interests": "가족 {여행}"}, "selected_places": []}
        def respond(prompt):
            captured.extend(prompt.to_messages())
            return AIMessage(content="부산 가족 여행 계획")
        with patch("chatbot.load_settings", return_value=Settings(api_key="fake")), patch(
            "chatbot.ChatOpenAI", return_value=RunnableLambda(respond)
        ):
            chatbot.answer_question("일정 만들어 줘", travel_context=context)
        self.assertIn("가족 {여행}", captured[0].content)
        self.assertEqual(captured[-1].content, "일정 만들어 줘")

    def test_conditions_save_without_destination_and_do_not_search(self):
        app = AppTest.from_file("app.py")
        app.session_state["project_open"] = True
        with patch("chatbot.answer_question") as answer:
            app.run()
            self.assertFalse(any(field.label == "여행 지역" for field in app.text_input))
            next(field for field in app.number_input if field.label == "여행 인원").set_value(1)
            next(button for button in app.button if button.label == "조건 저장").click().run()
            self.assertFalse(app.exception)
            self.assertFalse(any("여행 지역을 입력" in warning.value for warning in app.warning))
            saved = storage.load_plan(self.project)["conditions"]
            self.assertEqual(saved["destination"], "")
            self.assertEqual(saved["people"], 1)
            self.assertEqual(saved["transport"], "미정")
            answer.assert_not_called()

    def test_conditions_map_selection_and_build_plan_flow(self):
        places = [{"id": "a", "name": "장소A", "address": "부산 기장", "x": "129.1", "y": "35.1"},
                  {"id": "b", "name": "장소B", "address": "부산 기장", "x": "129.2", "y": "35.2"}]
        chat = storage.create_conversation(self.project)
        storage.append_turn(chat, "후보 검색", "장소 후보입니다", lookups=[{
            "kind": "places", "status": "success", "query": "부산 관광", "checked_at": "테스트 시각", "places": places}])
        storage.save_plan(self.project, conditions={"destination": "부산 기장"})
        with patch("chatbot.answer_question", return_value="선택한 순서로 작성한 일정") as answer:
            app = AppTest.from_file("app.py")
            app.session_state["project_open"] = True
            app.run()
            self.assertFalse(app.exception)
            self.assertFalse(any(field.label == "여행 지역" for field in app.text_input))
            next(b for b in app.button if b.label == "조건 저장").click().run()
            self.assertFalse(app.exception)
            answer.assert_not_called()
            self.assertEqual(storage.load_plan(self.project)["conditions"]["start_date"], "")
            self.assertEqual(storage.load_plan(self.project)["conditions"]["end_date"], "")
            app.multiselect[0].set_value(["b", "a"]).run()
            self.assertFalse(app.exception)
            self.assertEqual([p["key"] for p in storage.load_plan(self.project)["selected_places"]], ["b", "a"])
            next(b for b in app.button if b.label == "선택한 동선으로 일정 만들기").click().run()
            self.assertFalse(app.exception)
            context = answer.call_args.kwargs["travel_context"]
            self.assertEqual(context["conditions"]["destination"], "부산 기장")
            self.assertEqual([p["name"] for p in context["selected_places"]], ["장소B", "장소A"])
            self.assertEqual(storage.load_messages(chat)[-1]["content"], "선택한 순서로 작성한 일정")


if __name__ == "__main__":
    unittest.main()

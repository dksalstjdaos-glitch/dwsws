"""보관 사본의 독립성·프로젝트 분리와 저장/재열기 UI 검증."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
import storage


class SavedPlanTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        patched = patch("storage.DB_PATH", Path(temp.name) / "test.sqlite3")
        patched.start()
        self.addCleanup(patched.stop)
        storage.initialize()
        self.project = storage.create_project("경주 여행")
        self.message = {"role": "assistant", "content": "### 경주 여행 계획 · 2026-10-21 ~ 2026-10-22\n원본 일정",
                        "lookups": [], "execution": {"run_id": "plan-test", "trip_schedule": [
                            {"date": "2026-10-21", "time": "10:00", "place": {"name": "박물관"}}]}}

    def test_snapshot_is_independent_idempotent_and_project_scoped(self):
        first = storage.save_itinerary(self.project, "경주 1박 2일", self.message)
        self.message["content"] = "수정된 대화"
        self.message["execution"]["trip_schedule"][0]["time"] = "15:00"
        self.assertEqual(storage.save_itinerary(self.project, "다른 이름", self.message), first)
        storage.save_plan(self.project, conditions={"destination": "부산"})
        storage.initialize()
        saved = storage.load_itinerary(self.project, first)
        self.assertIn("원본 일정", saved["snapshot"]["content"])
        self.assertEqual(saved["snapshot"]["execution"]["trip_schedule"][0]["time"], "10:00")
        self.assertEqual(len(storage.list_itineraries(self.project)), 1)
        other = storage.create_project("다른 여행")
        self.assertEqual(storage.list_itineraries(other), [])
        with self.assertRaises(ValueError):
            storage.load_itinerary(other, first)
        storage.delete_project(self.project)
        self.assertEqual(storage.list_itineraries(self.project), [])

    def test_ui_save_reopen_after_new_conversation_without_api(self):
        chat = storage.create_conversation(self.project)
        storage.append_turn(chat, "계획 만들어줘", self.message["content"], lookups=[], execution=self.message["execution"])
        with patch("chatbot.answer_question") as api:
            app = AppTest.from_file("app.py")
            app.session_state["project_open"] = True
            app.run()
            self.assertFalse(app.exception)
            next(b for b in app.button if b.label == "계획 저장").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(len(storage.list_itineraries(self.project)), 1)
            next(b for b in app.button if b.label == "새 대화").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any("원본 일정" in m.value for m in app.markdown))
            self.assertFalse(any(b.label == "계획 저장" for b in app.button))
            api.assert_not_called()

    def test_delete_is_project_scoped_and_preserves_other_data(self):
        chat = storage.create_conversation(self.project)
        storage.append_turn(chat, "계획", self.message["content"], execution=self.message["execution"])
        storage.save_plan(self.project, conditions={"destination": "경주"})
        first = storage.save_itinerary(self.project, "첫 계획", self.message)
        other = storage.create_project("부산 여행")
        second = storage.save_itinerary(other, "다른 프로젝트 계획", self.message)
        with self.assertRaises(ValueError):
            storage.delete_itinerary(other, first)
        storage.delete_itinerary(self.project, first)
        storage.initialize()
        self.assertEqual(storage.list_itineraries(self.project), [])
        self.assertEqual(storage.load_itinerary(other, second)["title"], "다른 프로젝트 계획")
        self.assertEqual(len(storage.load_messages(chat)), 2)
        self.assertEqual(storage.load_plan(self.project)["conditions"]["destination"], "경주")
        # 보관 사본 삭제 후 원본에서 다시 저장할 수 있습니다.
        self.assertTrue(storage.save_itinerary(self.project, "다시 저장", self.message))

    def test_ui_delete_selected_then_last_plan(self):
        storage.save_itinerary(self.project, "첫 계획", self.message)
        second_message = {**self.message, "execution": {**self.message["execution"], "run_id": "second-plan"}}
        storage.save_itinerary(self.project, "둘째 계획", second_message)
        with patch("chatbot.answer_question") as api:
            app = AppTest.from_file("app.py")
            app.session_state["project_open"] = True
            app.run()
            for remaining in (1, 0):
                button = next(b for b in app.button if b.label == "저장 계획 삭제하기")
                self.assertTrue(button.disabled)
                next(c for c in app.checkbox if c.label == "이 저장 계획을 삭제합니다").check().run()
                next(b for b in app.button if b.label == "저장 계획 삭제하기").click().run()
                self.assertFalse(app.exception)
                self.assertFalse(app.error)
                self.assertEqual(len(storage.list_itineraries(self.project)), remaining)
            self.assertTrue(any("저장한 여행 계획 · 0개" in e.label for e in app.expander))
            api.assert_not_called()


if __name__ == "__main__":
    unittest.main()

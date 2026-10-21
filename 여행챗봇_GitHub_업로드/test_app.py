"""실제 API 요청 없이 입력 처리와 오류 표시를 검증합니다."""

import unittest
import tempfile
from pathlib import Path
from unittest.mock import ANY, patch

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from streamlit.testing.v1 import AppTest

import chatbot
import storage
from config import MissingAPIKeyError, Settings, load_settings


class TravelChatbotTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        db_patch = patch("storage.DB_PATH", Path(directory.name) / "test.sqlite3")
        db_patch.start()
        self.addCleanup(db_patch.stop)
        storage.initialize()
        with storage.connect() as db:
            db.execute("INSERT INTO projects VALUES ('default', '테스트 여행')")

    def open_app(self):
        app = AppTest.from_file("app.py")
        app.session_state["project_open"] = True
        return app.run()

    def test_welcome_card_fills_input_without_sending_and_hides_after_reply(self):
        with patch("chatbot.answer_question", return_value="여행 안내") as answer:
            app = self.open_app()
            app.button(key="welcome_card_plan").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.text_input(key="question").value, "모레 경주에서 박물관 중심으로 하루 계획 만들어 줘")
            answer.assert_not_called()
            next(b for b in app.button if b.label == "답변 받기").click().run()
            self.assertFalse(app.exception)
            self.assertFalse(any(b.key == "welcome_card_plan" for b in app.button))
            self.assertEqual(len(app.chat_message), 2)

    def test_project_name_before_chat_and_reopen_saved_project(self):
        with patch("chatbot.answer_question", return_value="여행 답변") as answer:
            app = AppTest.from_file("app.py").run()
            self.assertFalse(any(field.label == "여행 질문" for field in app.text_input))
            next(button for button in app.button if button.label == "프로젝트 만들고 시작하기").click().run()
            self.assertTrue(app.warning)
            answer.assert_not_called()
            app.text_input[0].set_value("제주 가족 여행")
            next(button for button in app.button if button.label == "프로젝트 만들고 시작하기").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(app.session_state["project_open"])
            project_id = app.session_state["project_id"]
            self.assertEqual(next(p["name"] for p in storage.list_projects() if p["id"] == project_id),
                             "제주 가족 여행")
            app.text_input(key="question").set_value("2박 3일 일정 추천")
            next(button for button in app.button if button.label == "답변 받기").click().run()
            app.button(key="back_to_project_home").click().run()
            self.assertFalse(any(field.label == "여행 질문" for field in app.text_input))
            self.assertFalse(app.session_state["project_open"])
            self.assertFalse(any(button.key == "back_to_project_home" for button in app.button))
            app.selectbox[0].set_value(project_id).run()
            next(button for button in app.button if button.label == "프로젝트 열기").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state["messages"][0]["content"], "2박 3일 일정 추천")

    def test_open_exact_project_after_switching_home_and_sidebar(self):
        second = storage.create_project("두 번째 여행")
        third = storage.create_project("세 번째 여행")
        for project_id, question in [("default", "첫 여행 질문"), (second, "둘째 여행 질문"),
                                     (third, "셋째 여행 질문")]:
            chat = storage.create_conversation(project_id)
            storage.append_turn(chat, question, "저장 답변")
        app = AppTest.from_file("app.py").run()
        for project_id, question in [(third, "셋째 여행 질문"), ("default", "첫 여행 질문"),
                                     (second, "둘째 여행 질문")]:
            app.selectbox(key="home_project_picker").set_value(project_id).run()
            next(button for button in app.button if button.label == "프로젝트 열기").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state["project_id"], project_id)
            self.assertEqual(app.session_state["messages"][0]["content"], question)
            next(button for button in app.sidebar.button if button.label == "프로젝트 시작 화면").click().run()
        app.selectbox(key="home_project_picker").set_value("default").run()
        next(button for button in app.button if button.label == "프로젝트 열기").click().run()
        app.sidebar.selectbox(key="sidebar_project_picker").set_value(third).run()
        self.assertEqual(app.session_state["project_id"], third)
        self.assertEqual(app.session_state["messages"][0]["content"], "셋째 여행 질문")

    def test_config_reads_named_key_without_exposing_it(self):
        with patch("config.dotenv_values", return_value={"OPENAI_API_KEY": "test-only"}):
            settings = load_settings()
        self.assertEqual(settings.api_key, "test-only")
        self.assertNotIn("test-only", repr(settings))
        with patch("config.dotenv_values", return_value={}):
            with self.assertRaises(ValueError):
                load_settings()

    def test_blank_input_never_loads_key_or_model(self):
        with patch("chatbot.load_settings") as settings, patch("chatbot.ChatOpenAI") as model:
            for question in ("", "  \n\t"):
                with self.assertRaises(ValueError):
                    chatbot.answer_question(question)
            settings.assert_not_called()
            model.assert_not_called()

    def test_only_current_question_is_sent(self):
        messages = []

        def respond(prompt):
            messages.append(prompt.to_messages())
            return AIMessage(content="여행 안내")

        with patch("chatbot.load_settings", return_value=Settings(api_key="test-only")), patch(
            "chatbot.ChatOpenAI", return_value=RunnableLambda(respond)
        ):
            self.assertEqual(chatbot.answer_question(" 부산 여행 "), "여행 안내")
            chatbot.answer_question("제주 여행")
        self.assertEqual([len(turn) for turn in messages], [2, 2])
        self.assertEqual(messages[0][1].content, "부산 여행")
        self.assertEqual(messages[1][1].content, "제주 여행")

    def test_ui_blank_and_success(self):
        with patch("chatbot.answer_question", return_value="부산 여행 답변") as answer:
            app = self.open_app()
            self.assertFalse(app.exception)
            app.text_input(key="question").set_value("   ")
            next(button for button in app.button if button.label == "답변 받기").click().run()
            self.assertTrue(app.warning)
            answer.assert_not_called()
            app.text_input(key="question").set_value("부산 여행")
            next(button for button in app.button if button.label == "답변 받기").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.markdown[1].value, "부산 여행 답변")
            answer.assert_called_once_with("부산 여행", history=[], lookup_records=[], execution_trace=ANY)

    def test_history_is_sent_in_role_order_without_mutation(self):
        captured = []
        history = [
            {"role": "user", "content": "부산에서 2박 3일 여행할 거야"},
            {"role": "assistant", "content": "부산 일정을 추천드립니다."},
        ]

        def respond(prompt):
            captured.extend(prompt.to_messages())
            return AIMessage(content="대중교통으로 조정했습니다.")

        with patch("chatbot.load_settings", return_value=Settings(api_key="test-only")), patch(
            "chatbot.ChatOpenAI", return_value=RunnableLambda(respond)
        ):
            chatbot.answer_question("대중교통 위주로 바꿔 줘", history=history)
        self.assertEqual([m.type for m in captured], ["system", "human", "ai", "human"])
        self.assertEqual(captured[1].content, history[0]["content"])
        self.assertEqual(captured[2].content, history[1]["content"])
        self.assertEqual(captured[3].content, "대중교통 위주로 바꿔 줘")
        self.assertEqual(len(history), 2)

    def test_ui_remembers_resets_and_isolates_sessions(self):
        with patch("chatbot.answer_question", return_value="부산 여행 답변") as answer:
            app = self.open_app()
            app.text_input(key="question").set_value("부산 여행")
            next(button for button in app.button if button.label == "답변 받기").click().run()
            app.text_input(key="question").set_value("그 일정의 예산은?")
            next(button for button in app.button if button.label == "답변 받기").click().run()
            self.assertFalse(app.exception)
            answer.assert_called_with("그 일정의 예산은?", history=[
                {"role": "user", "content": "부산 여행"},
                {"role": "assistant", "content": "부산 여행 답변", "lookups": []},
            ], lookup_records=[], execution_trace=ANY)
            self.assertEqual(len(app.chat_message), 4)
            app.run()
            self.assertEqual(answer.call_count, 2)
            self.assertEqual(len(app.chat_message), 4)
            other = self.open_app()
            self.assertEqual(other.session_state["messages"], app.session_state["messages"])
            next(button for button in app.button if button.label == "새 대화").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state["messages"], [])
            self.assertEqual(len(app.chat_message), 0)
            app.text_input(key="question").set_value("제주 여행")
            next(button for button in app.button if button.label == "답변 받기").click().run()
            answer.assert_called_with("제주 여행", history=[], lookup_records=[], execution_trace=ANY)

    def test_lookup_evidence_is_displayed_and_persisted(self):
        record = {
            "status": "success", "message": "카카오 결과", "origin": "서울시청", "destination": "서울역",
            "duration": "14분", "distance_km": 4.0, "checked_at": "2026-10-19 16:00 KST",
            "origin_url": "https://place.map.kakao.com/1", "destination_url": "https://place.map.kakao.com/2",
            "request_id": "test-request-id",
        }

        def respond(question, history, lookup_records, **kwargs):
            if question == "이동 시간 조회":
                lookup_records.append(record)
            elif question == "조회 실패":
                lookup_records.append({"status": "failed", "message": "카카오 조회 실패 안내"})
            elif question == "장소 검색":
                lookup_records.append({"status": "success", "kind": "places", "query": "부산 기장 식당",
                    "checked_at": "2026-10-19 16:00 KST", "places": [{"name": "검색 식당",
                    "address": "부산 기장군", "url": "https://place.map.kakao.com/3"}]})
            return "여행 안내 답변"

        with patch("chatbot.answer_question", side_effect=respond):
            app = self.open_app()
            app.text_input(key="question").set_value("이동 시간 조회")
            next(button for button in app.button if button.label == "답변 받기").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any("실제 조회 완료" in item.value for item in app.success))
            self.assertEqual([item.value for item in app.metric], ["14분", "4.0 km"])
            self.assertEqual(len([button for button in app.link_button if button.label != "LangSmith 열기"]), 2)
            self.assertEqual(app.session_state["messages"][-1]["lookups"], [record])
            reopened = self.open_app()
            self.assertTrue(any("실제 조회 완료" in item.value for item in reopened.success))
            app.text_input(key="question").set_value("조회 실패")
            next(button for button in app.button if button.label == "답변 받기").click().run()
            self.assertTrue(any("조회 미완료" in item.value for item in app.warning))
            app.text_input(key="question").set_value("일반 여행 추천")
            next(button for button in app.button if button.label == "답변 받기").click().run()
            self.assertTrue(any("카카오 조회 안 함" in item.value for item in app.caption))
            app.text_input(key="question").set_value("장소 검색")
            next(button for button in app.button if button.label == "답변 받기").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any("카카오 장소 검색 완료" in item.value for item in app.success))
            self.assertTrue(any("검색 식당" in item.label for item in app.link_button))

    def test_failed_or_blank_turn_does_not_change_history(self):
        with patch("chatbot.answer_question", return_value="부산 여행 답변") as answer:
            app = self.open_app()
            app.text_input(key="question").set_value("부산 여행")
            next(button for button in app.button if button.label == "답변 받기").click().run()
            previous = list(app.session_state["messages"])
            answer.side_effect = RuntimeError("FAKE_SECRET")
            app.text_input(key="question").set_value("예산은?")
            next(button for button in app.button if button.label == "답변 받기").click().run()
            self.assertEqual(app.session_state["messages"], previous)
            self.assertNotIn("FAKE_SECRET", str(app))
            app.text_input(key="question").set_value("   ")
            next(button for button in app.button if button.label == "답변 받기").click().run()
            self.assertEqual(app.session_state["messages"], previous)
            self.assertEqual(answer.call_count, 2)

    def test_ui_does_not_display_exception_secret(self):
        secret = "FAKE_SECRET_MUST_NOT_APPEAR"
        with patch("chatbot.answer_question", side_effect=RuntimeError(secret)):
            app = self.open_app()
            app.text_input(key="question").set_value("서울 여행")
            next(button for button in app.button if button.label == "답변 받기").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(len(app.error), 1)
            self.assertNotIn(secret, str(app))

    def test_ui_explains_missing_key(self):
        with patch("chatbot.answer_question", side_effect=MissingAPIKeyError()):
            app = self.open_app()
            app.text_input(key="question").set_value("서울 여행")
            next(button for button in app.button if button.label == "답변 받기").click().run()
            self.assertFalse(app.exception)
            self.assertIn("OPENAI_API_KEY 값이 비어 있습니다", app.error[0].value)


if __name__ == "__main__":
    unittest.main()

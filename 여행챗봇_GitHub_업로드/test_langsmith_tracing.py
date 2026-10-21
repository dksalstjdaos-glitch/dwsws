"""실제 키·네트워크 없이 설정, 추적 경계와 안전한 연결 확인을 검증합니다."""
import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch

from langsmith_tracing import (TracingSettings, TracingConfigError, load_tracing_settings,
                               redact, tracing_session, invoke_traced, test_connection as check_connection)


class LangSmithTests(unittest.TestCase):
    def test_settings_defaults_and_secret_repr(self):
        self.assertFalse(load_tracing_settings({}).enabled)
        settings = load_tracing_settings({"LANGSMITH_TRACING": "true", "LANGSMITH_API_KEY": "test-secret"})
        self.assertTrue(settings.enabled)
        self.assertNotIn("test-secret", repr(settings))
        with self.assertRaises(TracingConfigError):
            load_tracing_settings({"LANGSMITH_TRACING": "true"})
        with self.assertRaises(TracingConfigError):
            load_tracing_settings({"LANGSMITH_ENDPOINT": "https://example.com"})

    def test_disabled_does_not_create_client_or_mutate_empty_mock_trace(self):
        execution = {}
        function = Mock(return_value="답변")
        with patch("langsmith_tracing.dotenv_values", return_value={}), patch("langsmith_tracing.Client") as client:
            self.assertEqual(invoke_traced(function, mode="기존", question="안녕", execution_trace=execution), "답변")
            client.assert_not_called()
        self.assertEqual(execution, {})
        function.assert_called_once()

    def test_missing_key_does_not_break_chat_or_start_network(self):
        execution = {}
        with patch("langsmith_tracing.dotenv_values", return_value={"LANGSMITH_TRACING": "true"}), patch("langsmith_tracing.Client") as client:
            answer = invoke_traced(lambda question, **kw: "정상 답변", mode="기존", question="질문", execution_trace=execution)
        self.assertEqual(answer, "정상 답변")
        self.assertIn("비어", execution["langsmith_error"])
        client.assert_not_called()

    def test_enabled_records_root_and_reuses_session(self):
        values = {"LANGSMITH_TRACING": "true", "LANGSMITH_API_KEY": "test-secret", "LANGSMITH_PROJECT": "테스트"}
        root = Mock(id="test-trace-id")
        @contextmanager
        def fake_trace(*args, **kwargs):
            yield root
        def reply(question, **kw):
            with tracing_session() as state:
                self.assertTrue(state["enabled"])
            return "답변"
        execution = {}
        with patch("langsmith_tracing.dotenv_values", return_value=values), patch("langsmith_tracing.Client") as client, patch("langsmith_tracing.trace", fake_trace):
            self.assertEqual(invoke_traced(reply, mode="5일차", question="질문", execution_trace=execution), "답변")
            self.assertEqual(client.call_count, 1)
            client.return_value.flush.assert_called_once()
        self.assertEqual(execution["langsmith_trace_id"], "test-trace-id")
        self.assertNotIn("test-secret", str(execution))

    def test_redaction_keeps_question_but_hides_keys(self):
        masked = redact({"question": "경주", "OPENAI_API_KEY": "anything", "nested": ["sk-secret123456", {"authorization": "anything"}]})
        self.assertEqual(masked["question"], "경주")
        self.assertNotIn("anything", str(masked))
        self.assertNotIn("sk-secret", str(masked))

    def test_connection_sends_only_fixed_test_payload_once(self):
        response = Mock(status_code=201)
        settings = TracingSettings(True, "test-secret")
        with patch("langsmith_tracing.httpx.post", return_value=response) as post:
            result = check_connection(settings)
            self.assertTrue(result["ok"])
            body = post.call_args.kwargs["json"]
            self.assertEqual(body["inputs"], {"test": "langsmith_connection"})
            self.assertNotIn("test-secret", str(body))
            self.assertEqual(post.call_count, 1)
        with patch("langsmith_tracing.httpx.post", return_value=Mock(status_code=401)) as post:
            result = check_connection(settings)
            self.assertFalse(result["ok"])
            self.assertIn("권한", result["message"])
            self.assertEqual(post.call_count, 1)


if __name__ == "__main__":
    unittest.main()

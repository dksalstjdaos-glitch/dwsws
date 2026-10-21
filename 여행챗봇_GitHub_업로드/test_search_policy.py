"""첨부 IPO 명세의 검색 종료·재시도·조건 유지 규칙을 검증합니다."""

import unittest
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage

import chatbot
from config import Settings
from kakao_maps import RouteResult
from search_policy import PlaceSearchPolicy, SearchPolicyError


def tool_call(query, category="AT4", region="경주", call_id="s1"):
    return AIMessage(content="", tool_calls=[{"name": "search_places", "args": {
        "query": query, "category": category, "region": region,
    }, "id": call_id, "type": "tool_call"}])


def result(count):
    return RouteResult("검색 결과", status="success" if count else "empty", kind="places",
                       places=[{"name": f"실제 장소 {i}"} for i in range(count)])


class SearchPolicyTests(unittest.TestCase):
    def run_question(self, question, calls, results, history=None):
        model = MagicMock()
        model.bind_tools.return_value.invoke.side_effect = calls
        model.invoke.return_value = AIMessage(content="확인된 장소명, 주소와 지도 링크 안내입니다.")
        records = []
        with patch("chatbot.load_settings", return_value=Settings(api_key="fake", kakao_api_key="fake")), patch(
            "chatbot.ChatOpenAI", return_value=model
        ), patch("chatbot.search_places", side_effect=results) as search:
            answer = chatbot.answer_question(question, history=history, lookup_records=records)
        return answer, records, search, model

    def test_missing_region_waits_without_search_or_execution_record(self):
        answer, records, search, model = self.run_question(
            "부모님과 갈 실내 장소 추천", [tool_call("경주 실내 장소")], [],
            history=[{"role": "assistant", "content": "경주를 추천합니다."}],
        )
        self.assertEqual(answer, "어느 지역의 장소를 찾으시나요?")
        self.assertEqual(records, [])
        search.assert_not_called()
        model.invoke.assert_not_called()

    def test_region_reply_keeps_previous_conditions(self):
        history = [
            {"role": "user", "content": "부모님과 갈 실내 장소 추천"},
            {"role": "assistant", "content": "어느 지역의 장소를 찾으시나요?"},
        ]
        _, records, search, model = self.run_question(
            "경주요", [tool_call("경주 실내 장소")], [result(3)], history=history,
        )
        search.assert_called_once()
        self.assertEqual(records[0]["arguments"]["region"], "경주")
        self.assertTrue(records[0]["arguments"]["indoor_only"])
        self.assertTrue(any("부모님" in m.content for m in model.invoke.call_args.args[0]))

    def test_failed_search_stops_without_model_summary_or_retry(self):
        failure = RouteResult("인증 또는 이용 권한이 없습니다. 키 설정을 확인해 주세요.",
                              kind="places", error_code="authentication_error")
        answer, records, search, model = self.run_question(
            "경주 실내 장소", [tool_call("경주 실내 장소"), tool_call("경주 박물관")], [failure],
        )
        search.assert_called_once()
        model.invoke.assert_not_called()
        self.assertIn("키 설정", answer)
        self.assertEqual(records[0]["status"], "failed")
        self.assertEqual(records[0]["error_code"], "authentication_error")

    def test_route_tool_budget_stops_at_four_without_extra_model_call(self):
        model = MagicMock()
        model.bind_tools.return_value.invoke.return_value = AIMessage(content="", tool_calls=[{
            "name": "get_driving_route", "args": {"origin": "서울시청", "destination": "서울역"},
            "id": "route", "type": "tool_call",
        }])
        with patch("chatbot.load_settings", return_value=Settings(api_key="fake", kakao_api_key="fake")), patch(
            "chatbot.ChatOpenAI", return_value=model
        ), patch("chatbot.get_driving_route", return_value=RouteResult("확인 결과", status="success")) as route:
            answer = chatbot.answer_question("서울시청에서 서울역까지 차로 얼마나 걸려?")
        self.assertEqual(route.call_count, 4)
        self.assertLessEqual(model.bind_tools.return_value.invoke.call_count + model.invoke.call_count, 6)
        model.invoke.assert_not_called()
        self.assertIn("한도", answer)

    def test_route_authentication_failure_stops_after_first_call(self):
        model = MagicMock()
        model.bind_tools.return_value.invoke.return_value = AIMessage(content="", tool_calls=[{
            "name": "get_driving_route", "args": {"origin": "서울시청", "destination": "서울역"},
            "id": "route", "type": "tool_call",
        }])
        with patch("chatbot.load_settings", return_value=Settings(api_key="fake", kakao_api_key="fake")), patch(
            "chatbot.ChatOpenAI", return_value=model
        ), patch("chatbot.get_driving_route", return_value=RouteResult("인증 오류: 키 설정을 확인해 주세요.", error_code="authentication_error")) as route:
            answer = chatbot.answer_question("서울시청에서 서울역까지 차로 얼마나 걸려?")
        route.assert_called_once()
        model.invoke.assert_not_called()
        self.assertIn("인증 오류", answer)

    def test_three_candidates_finish_after_first_search(self):
        _, records, search, model = self.run_question("경주에서 부모님과 갈 실내 장소 추천", [tool_call("경주 실내 장소")], [result(3)])
        self.assertEqual(search.call_count, 1)
        self.assertEqual(len(records), 1)
        model.invoke.assert_called_once()

    def test_zero_then_distinct_retry_preserves_region_and_indoor(self):
        _, records, search, _ = self.run_question("경주에서 부모님과 갈 실내 장소 추천", [
            tool_call("경주 실내 장소"), tool_call("경주 박물관", call_id="s2"),
        ], [result(0), result(3)])
        self.assertEqual(search.call_count, 2)
        self.assertNotEqual(records[0]["arguments"]["query"], records[1]["arguments"]["query"])
        self.assertTrue(all(r["arguments"]["region"] == "경주" and r["arguments"]["indoor_only"] for r in records))
        self.assertEqual([r["status"] for r in records], ["empty", "success"])

    def test_two_empty_searches_stop_and_ask_before_changing_conditions(self):
        answer, _, search, model = self.run_question("경주에서 갈 실내 장소", [
            tool_call("경주 실내 장소"), tool_call("경주 박물관", call_id="s2"),
        ], [result(0), result(0)])
        self.assertEqual(search.call_count, 2)
        self.assertIn("될까요", answer)
        model.invoke.assert_not_called()

    def test_same_arguments_are_not_sent_twice(self):
        _, records, search, _ = self.run_question("경주 실내 장소", [
            tool_call("경주 실내 장소"), tool_call("경주 실내 장소", call_id="s2"),
        ], [result(0)])
        self.assertEqual(search.call_count, 1)
        self.assertEqual(records[-1]["status"], "blocked")

    def test_other_place_type_requires_explicit_permission(self):
        question = "경주에서 갈 실내 카페 추천"
        calls = [tool_call("경주 카페", "CE7"), tool_call("경주 박물관", "AT4", call_id="s2")]
        answer, _, search, _ = self.run_question(question, calls, [result(0)])
        self.assertEqual(search.call_count, 1)
        self.assertIn("넓혀", answer)
        _, records, search, _ = self.run_question(question + ", 후보가 없으면 다른 종류로 찾아줘", calls, [result(0), result(3)])
        self.assertEqual(search.call_count, 2)
        self.assertEqual(records[-1]["arguments"]["region"], "경주")
        self.assertTrue(records[-1]["arguments"]["indoor_only"])

    def test_core_region_and_indoor_cannot_be_relaxed(self):
        for second in [tool_call("부산 박물관", region="부산", call_id="s2"), tool_call("경주 야외 공원", call_id="s2")]:
            answer, records, search, _ = self.run_question("경주에서 실내 장소 추천", [tool_call("경주 실내 장소"), second], [result(0)])
            self.assertEqual(search.call_count, 1)
            self.assertIn("될까요", answer)
            self.assertEqual(records[-1]["status"], "blocked")

    def test_third_search_is_rejected(self):
        policy = PlaceSearchPolicy("경주에서 실내 장소")
        policy.prepare({"query": "경주 실내 장소", "region": "경주"})
        policy.prepare({"query": "경주 박물관", "region": "경주"})
        with self.assertRaises(SearchPolicyError):
            policy.prepare({"query": "경주 미술관", "region": "경주"})

    def test_generic_trip_does_not_inherit_a_model_invented_category_filter(self):
        policy = PlaceSearchPolicy("10월 24일에 2박 3일 경주여행 가는데 추천좀 해줘", history=[
            {"role": "user", "content": "실내 장소를 찾아줘"}])
        first = policy.prepare({"query": "경주 실내 관광명소", "region": "경주", "category": "AT4"})
        second = policy.prepare({"query": "경주 실내 박물관", "region": "경주", "category": ""})
        self.assertEqual(first["category"], "")
        self.assertEqual(second["query"], "경주 박물관")
        self.assertTrue(second["indoor_only"])

    def test_region_in_korean_trip_compound_is_grounded_in_user_text(self):
        policy = PlaceSearchPolicy("경주여행 추천해줘")
        args = policy.prepare({"query": "경주 관광명소", "region": "경주", "category": "AT4"})
        self.assertEqual(args["region"], "경주")


if __name__ == "__main__":
    unittest.main()

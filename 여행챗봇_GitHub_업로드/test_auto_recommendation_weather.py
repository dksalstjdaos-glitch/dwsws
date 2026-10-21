"""장소 추천 다음에 날씨·옷차림·아이템을 자동으로 붙이는 흐름을 검증합니다."""

import unittest
from datetime import date
from unittest.mock import MagicMock, patch

import httpx
from langchain_core.messages import AIMessage

import chatbot
from config import Settings
from kakao_maps import RouteResult
from test_region_weather import REGION
from test_search_policy import tool_call
from test_weather import PLACE, payload

REAL_CLIENT = httpx.Client


class AutoRecommendationWeatherTests(unittest.TestCase):
    def setUp(self):
        today = patch("weather.today_kst", return_value=date(2026, 10, 20))
        today.start()
        self.addCleanup(today.stop)

    def run_recommendation(self, question, *, empty_first=False, fail_region=False, context=None):
        model = MagicMock()
        model.bind_tools.return_value.invoke.side_effect = ([tool_call("경주 실내 장소"), tool_call("경주 박물관", call_id="second")]
                                                           if empty_first else [tool_call("경주 박물관")])
        model.invoke.return_value = AIMessage(content="검색한 장소 안내")
        found = RouteResult("실제 검색", status="success", kind="places", places=[PLACE])
        results = [RouteResult("0건", status="empty", kind="places", places=[]), found] if empty_first else [found]
        region_result = RouteResult("인증 오류", kind="region", error_code="authentication_error") if fail_region else RouteResult("대표 좌표", kind="region", status="success", places=[REGION])
        trace, records = {}, []
        with patch("chatbot.load_settings", return_value=Settings(api_key="fake", kakao_api_key="fake")), patch(
            "chatbot.ChatOpenAI", return_value=model
        ), patch("chatbot.search_places", side_effect=results) as search, patch(
            "chatbot.get_region_coordinates", return_value=region_result
        ) as region, patch("weather.httpx.Client", side_effect=lambda **kwargs: REAL_CLIENT(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload(["2026-10-21"]))), **kwargs)):
            answer = chatbot.answer_question(question, travel_context=context, lookup_records=records, execution_trace=trace)
        return answer, records, trace, search, region

    def test_dated_place_recommendation_also_shows_weather_outfit_and_items(self):
        answer, records, trace, search, region = self.run_recommendation("내일 경주 장소 추천해줘")
        self.assertIn(PLACE["name"], answer)
        self.assertIn("지역 날씨", answer)
        self.assertIn("외출·여행 옷차림", answer)
        self.assertIn("챙길 준비물", answer)
        self.assertEqual([r["tool"] for r in records], ["search_places", "get_region_coordinates", "get_weather"])
        self.assertEqual(trace["tool_calls"], 3)
        search.assert_called_once()
        region.assert_called_once_with("경주", "fake")

    def test_recommendation_cannot_skip_search_and_return_unverified_itinerary(self):
        model = MagicMock()
        model.bind_tools.return_value.invoke.return_value = AIMessage(content="검색하지 않은 일정과 장소")
        with patch("chatbot.load_settings", return_value=Settings(api_key="fake", kakao_api_key="fake")), patch(
            "chatbot.ChatOpenAI", return_value=model
        ):
            answer = chatbot.answer_question("10월 24일에 2박 3일 경주여행 가는데 추천좀 해줘")
        self.assertTrue(any(c.kwargs.get("tool_choice", {}).get("function", {}).get("name") == "search_places"
                            for c in model.bind_tools.call_args_list))
        self.assertNotIn("검색하지 않은 일정과 장소", answer)
        self.assertIn("장소 검색이 실행되지 않아", answer)

    def test_explanation_does_not_force_a_new_search(self):
        self.assertTrue(chatbot.place_recommendation_requested("10월 24일에 2박 3일 경주여행 가는데 추천좀 해줘"))
        self.assertTrue(chatbot.place_recommendation_requested("경주 어디 가볼까?"))
        self.assertFalse(chatbot.place_recommendation_requested("왜 추천했어?"))

    def test_one_retry_plus_weather_stays_within_four_tool_calls(self):
        answer, records, trace, _, _ = self.run_recommendation("내일 경주 실내 장소 추천해줘", empty_first=True)
        self.assertEqual(trace["tool_calls"], 4)
        self.assertEqual(len(records), 4)
        self.assertIn("외출·여행 옷차림", answer)

    def test_missing_date_keeps_recommendations_and_asks_before_forecast(self):
        answer, records, trace, _, region = self.run_recommendation("경주 장소 추천해줘")
        self.assertIn(PLACE["name"], answer)
        self.assertIn("언제 방문", answer)
        self.assertEqual(trace["end_reason"], "여행 날짜 확인 대기")
        self.assertEqual(trace["pending_weather_region"], "경주")
        self.assertEqual(len(records), 1)
        region.assert_not_called()

    def test_date_only_reply_finishes_weather_without_repeating_place_search(self):
        answer, records, trace, _, _ = self.run_recommendation("경주 장소 추천해줘")
        history = [{"role": "user", "content": "경주 장소 추천해줘"},
                   {"role": "assistant", "content": answer, "lookups": records, "execution": trace}]
        with patch("chatbot.load_settings", return_value=Settings(api_key="fake", kakao_api_key="fake")), patch(
            "chatbot.get_region_coordinates", return_value=RouteResult("대표 좌표", status="success", kind="region", places=[REGION])
        ) as region, patch("chatbot.search_places") as search, patch("weather.httpx.Client", side_effect=lambda **kwargs: REAL_CLIENT(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload(["2026-10-21"]))), **kwargs)):
            result = chatbot.answer_question("내일", history=history, travel_context={"conditions": {"destination": "부산"}})
        region.assert_called_once_with("경주", "fake")
        search.assert_not_called()
        self.assertIn("2026-10-21", result)
        self.assertIn("외출·여행 옷차림", result)

    def test_saved_travel_date_is_used_without_asking_again(self):
        answer, records, _, _, _ = self.run_recommendation("경주 장소 추천해줘", context={
            "conditions": {"start_date": "2026-10-21", "end_date": "2026-10-21"}})
        self.assertIn("지역 날씨", answer)
        self.assertEqual(records[-1]["start_date"], "2026-10-21")
        self.assertNotIn("언제 방문", answer)

    def test_weather_failure_does_not_erase_recommendations_or_invent_outfit(self):
        answer, records, _, _, region = self.run_recommendation("내일 경주 장소 추천해줘", fail_region=True)
        self.assertIn(PLACE["name"], answer)
        self.assertIn("인증 오류", answer)
        self.assertNotIn("외출·여행 옷차림", answer)
        self.assertEqual(records[-1]["status"], "failed")
        region.assert_called_once()

    def test_forecast_range_failure_keeps_place_recommendations(self):
        answer, records, _, _, region = self.run_recommendation("30일 뒤 경주 장소 추천해줘")
        self.assertIn(PLACE["name"], answer)
        self.assertIn("범위", answer)
        self.assertEqual(len(records), 1)
        region.assert_not_called()


if __name__ == "__main__":
    unittest.main()

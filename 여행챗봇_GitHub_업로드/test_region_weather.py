"""지역 날씨는 추천 장소와 분리하고, 행정구역 대표 좌표로 조회합니다."""

import json
import unittest
from datetime import date
from unittest.mock import patch

import httpx

import chatbot
import weather
from config import Settings
from kakao_maps import ADDRESS_URL, RouteResult, get_region_coordinates
from test_weather import PLACE, payload

REAL_CLIENT = httpx.Client
REGION = {"name": "경북 경주시", "address": "경북 경주시", "x": "129.2248", "y": "35.8562", "scope": "region"}


class RegionWeatherTests(unittest.TestCase):
    def setUp(self):
        today = patch("weather.today_kst", return_value=date(2026, 10, 20))
        today.start()
        self.addCleanup(today.stop)
        chatbot_today = patch("chatbot.today_kst", return_value=date(2026, 10, 20))
        chatbot_today.start()
        self.addCleanup(chatbot_today.stop)

    def test_general_region_weather_ignores_recommended_places_and_defaults_today(self):
        history = [{"role": "assistant", "content": "경주 확인 장소 추천", "lookups": [{
            "kind": "places", "status": "success", "places": [PLACE], "arguments": {"region": "경주"}}]}]
        context = {"conditions": {"destination": "경주", "start_date": "2026-10-25", "end_date": "2026-10-26"}, "selected_places": [PLACE]}
        records, trace = [], {}
        def handle(request):
            self.assertEqual(request.url.params["start_date"], "2026-10-20")
            self.assertEqual(request.url.params["end_date"], "2026-10-20")
            self.assertEqual(request.url.params["latitude"], REGION["y"])
            return httpx.Response(200, json=payload(["2026-10-20"]))
        with patch("chatbot.load_settings", return_value=Settings(api_key="fake", kakao_api_key="fake")), patch(
            "chatbot.get_region_coordinates", return_value=RouteResult("지역 확인", status="success", kind="region", places=[REGION])
        ) as region, patch("chatbot.search_places") as search, patch("chatbot.ChatOpenAI") as model, patch(
            "weather.httpx.Client", side_effect=lambda **kwargs: REAL_CLIENT(transport=httpx.MockTransport(handle), **kwargs)
        ):
            answer = chatbot.answer_question("경주 날씨 알려줘", history=history, travel_context=context,
                                             lookup_records=records, execution_trace=trace)
        region.assert_called_once_with("경주", "fake")
        search.assert_not_called()
        model.assert_not_called()
        self.assertIn("경북 경주시 지역 날씨", answer)
        self.assertNotIn(PLACE["name"], answer)
        self.assertIn("챙길 준비물", answer)
        self.assertIn("외출·여행 옷차림", answer)
        self.assertEqual([r["tool"] for r in records], ["get_region_coordinates", "get_weather"])
        self.assertEqual(trace["tool_calls"], 2)

    def test_explicit_region_and_place_intents_are_separate(self):
        self.assertTrue(weather.regional_weather_requested("경주 내일 날씨 알려줘"))
        self.assertFalse(weather.regional_weather_requested("추천 장소의 날씨 알려줘"))
        self.assertFalse(weather.regional_weather_requested("선택한 장소 날씨와 준비물"))
        for text, expected in [("경주날씨 알려줘", "경주"), ("내일 경주 날씨", "경주"),
                               ("부산 해운대구 지역 날씨", "부산 해운대구")]:
            self.assertEqual(weather.weather_region(text), expected)

    def test_relative_days_are_dates_and_never_part_of_region_query(self):
        for text, target in [("3일뒤에 경주 날씨 알려줘", "2026-10-23"),
                             ("3일 뒤에 경주 날씨", "2026-10-23"),
                             ("경주 3일 후 날씨", "2026-10-23"),
                             ("사흘 뒤 경주 날씨", "2026-10-23"),
                             ("일주일 뒤 경주 날씨", "2026-10-27"),
                             ("2주 후에 경주 날씨", "2026-11-03")]:
            with self.subTest(question=text):
                self.assertEqual(weather.weather_region(text), "경주")
                self.assertEqual(weather.dates_in_text(text), (target, target))

    def test_weekdays_are_removed_from_region_and_resolved_by_calendar_week(self):
        cases = [
            ("이번주 토요일 부산 날씨 알려줘", "2026-10-24", "2026-10-24"),
            ("이번 주 토요일에 부산 날씨", "2026-10-24", "2026-10-24"),
            ("부산 이번 토요일 날씨", "2026-10-24", "2026-10-24"),
            ("다음주 월요일 부산 날씨", "2026-10-26", "2026-10-26"),
            ("다다음 주 화요일 부산 날씨", "2026-11-03", "2026-11-03"),
            ("차주 금요일 부산 날씨", "2026-10-30", "2026-10-30"),
            ("오는 토요일 부산 날씨", "2026-10-24", "2026-10-24"),
            ("이번주 토욜 부산 날씨", "2026-10-24", "2026-10-24"),
            ("다음주 토요일부터 일요일까지 부산 날씨", "2026-10-31", "2026-11-01"),
            ("이번 주말 부산 날씨", "2026-10-24", "2026-10-25"),
            ("다음주말 부산 날씨", "2026-10-31", "2026-11-01"),
            ("다음주 부산 날씨", "2026-10-26", "2026-11-01"),
        ]
        for text, start, end in cases:
            with self.subTest(question=text):
                self.assertEqual(weather.weather_region(text), "부산")
                self.assertEqual(weather.dates_in_text(text), (start, end))

    def test_weekday_year_boundary_and_bare_weekday(self):
        with patch("weather.today_kst", return_value=date(2026, 12, 31)):
            self.assertEqual(weather.dates_in_text("이번주 토요일"), ("2027-01-02", "2027-01-02"))
            self.assertEqual(weather.dates_in_text("다음주 월요일"), ("2027-01-04", "2027-01-04"))
            self.assertEqual(weather.dates_in_text("월요일"), ("2027-01-04", "2027-01-04"))
        with patch("weather.today_kst", return_value=date(2026, 10, 25)):
            self.assertEqual(weather.dates_in_text("이번주 토요일"), ("2026-10-24", "2026-10-24"))
            self.assertIsNotNone(weather.validate_dates(*weather.dates_in_text("이번주 토요일")))

    def test_reported_weekday_question_queries_only_busan_on_correct_day(self):
        region_point = {**REGION, "name": "부산", "address": "부산", "x": "129.075", "y": "35.18"}
        records = []
        def handle(request):
            self.assertEqual(request.url.params["start_date"], "2026-10-24")
            self.assertEqual(request.url.params["end_date"], "2026-10-24")
            return httpx.Response(200, json=payload(["2026-10-24"]))
        with patch("chatbot.load_settings", return_value=Settings(api_key="fake", kakao_api_key="fake")), patch(
            "chatbot.get_region_coordinates", return_value=RouteResult("지역 확인", status="success", kind="region", places=[region_point])
        ) as region, patch("weather.httpx.Client", side_effect=lambda **kwargs: REAL_CLIENT(transport=httpx.MockTransport(handle), **kwargs)):
            answer = chatbot.answer_question("이번주 토요일 부산 날씨 알려줘", lookup_records=records)
        region.assert_called_once_with("부산", "fake")
        self.assertEqual(records[-1]["status"], "success")
        self.assertIn("2026-10-24", answer)

    def test_trip_weather_followup_uses_region_and_overnight_duration(self):
        history = [
            {"role": "assistant", "content": "이전 부산 날씨", "lookups": [{"kind": "weather", "status": "success", "scope": "region", "start_date": "2026-10-20", "end_date": "2026-10-20",
                "forecasts": [{"name": "부산", "latitude": 35.18, "longitude": 129.075}]}]},
            {"role": "user", "content": "11월 1일에 1박2일 해서 경주여행 갈라고 하는데 장소 추천좀 해줘"},
            {"role": "assistant", "content": "추천 장소", "lookups": []},
            {"role": "user", "content": "그럼 이 날 날씨는 어때?"},
            {"role": "assistant", "content": "기존 오류", "lookups": [{"status": "unavailable", "arguments": {"region": "이날"}}]},
        ]
        records, trace = [], {}
        context = {"conditions": {"destination": "부산", "start_date": "2026-10-24", "end_date": "2026-10-25"}}
        def handle(request):
            self.assertEqual(request.url.params["start_date"], "2026-11-01")
            self.assertEqual(request.url.params["end_date"], "2026-11-02")
            return httpx.Response(200, json=payload(["2026-11-01", "2026-11-02"]))
        with patch("chatbot.load_settings", return_value=Settings(api_key="fake", kakao_api_key="fake")), patch(
            "chatbot.get_region_coordinates", return_value=RouteResult("지역 확인", status="success", kind="region", places=[REGION])
        ) as region, patch("weather.httpx.Client", side_effect=lambda **kwargs: REAL_CLIENT(transport=httpx.MockTransport(handle), **kwargs)):
            answer = chatbot.answer_question("그럼 이 날 날씨는 어때?", history=history, travel_context=context, lookup_records=records, execution_trace=trace)
        region.assert_called_once_with("경주", "fake")
        self.assertIn("2026-11-01", answer)
        self.assertIn("2026-11-02", answer)
        self.assertTrue(any(e.get("action") == "대화 조건 이어받기" for e in trace["events"]))

    def test_followup_region_phrases_and_current_override(self):
        history = [{"role": "user", "content": "11월 1일에 경주로 여행 갈 거야"}]
        for question in ("그럼 이날 날씨는 어때?", "그날 날씨", "그때 날씨", "거기 날씨 어때?"):
            with self.subTest(question=question):
                self.assertEqual(weather.weather_region(question, history), "경주")
        self.assertEqual(weather.weather_region("그날 부산 날씨", history), "부산")
        self.assertEqual(weather.dates_in_text("11월 1일에 1박2일 여행"), ("2026-11-01", "2026-11-02"))
        self.assertEqual(weather.dates_in_text("내일부터 2박3일 여행"), ("2026-10-21", "2026-10-23"))

    def test_reported_question_queries_gyeongju_three_days_later(self):
        records = []
        def handle(request):
            self.assertEqual(request.url.params["start_date"], "2026-10-23")
            self.assertEqual(request.url.params["end_date"], "2026-10-23")
            return httpx.Response(200, json=payload(["2026-10-23"]))
        with patch("chatbot.load_settings", return_value=Settings(api_key="fake", kakao_api_key="fake")), patch(
            "chatbot.get_region_coordinates", return_value=RouteResult("지역 확인", status="success", kind="region", places=[REGION])
        ) as region, patch("weather.httpx.Client", side_effect=lambda **kwargs: REAL_CLIENT(transport=httpx.MockTransport(handle), **kwargs)):
            answer = chatbot.answer_question("3일뒤에 경주 날씨 알려줘", lookup_records=records)
        region.assert_called_once_with("경주", "fake")
        self.assertIn("2026-10-23", answer)
        self.assertEqual(records[-1]["status"], "success")

    def test_relative_date_outside_forecast_range_stops_before_region_lookup(self):
        with patch("chatbot.get_region_coordinates") as region:
            answer = chatbot.answer_question("30일 뒤에 경주 날씨")
        region.assert_not_called()
        self.assertIn("범위", answer)

    def test_missing_region_asks_without_external_calls(self):
        trace = {}
        with patch("chatbot.load_settings") as settings, patch("chatbot.get_region_coordinates") as region:
            answer = chatbot.answer_question("날씨 알려줘", execution_trace=trace)
        settings.assert_not_called()
        region.assert_not_called()
        self.assertEqual(trace["tool_calls"], 0)
        self.assertEqual(trace["end_reason"], "날씨 지역 확인 대기")
        history = [{"role": "assistant", "content": answer, "execution": trace}]
        self.assertTrue(weather.weather_requested("경주요", history))
        self.assertTrue(weather.regional_weather_requested("경주요", history))
        self.assertEqual(weather.weather_region("경주요", history), "경주")

    def test_administrative_region_lookup_does_not_search_pois(self):
        def handle(request):
            self.assertEqual(str(request.url).split("?")[0], ADDRESS_URL)
            self.assertEqual(request.url.params["query"], "경주")
            self.assertEqual(request.headers["Authorization"], "KakaoAK fake-secret")
            return httpx.Response(200, json={"documents": [{"address_type": "REGION", "address_name": "경북 경주시", "x": "129.2248", "y": "35.8562"}]})
        with patch("kakao_maps.httpx.Client", side_effect=lambda **kwargs: REAL_CLIENT(transport=httpx.MockTransport(handle), **kwargs)):
            record = get_region_coordinates("경주", "fake-secret").record
        self.assertEqual(record["kind"], "region")
        self.assertEqual(record["places"], [REGION])
        self.assertNotIn("fake-secret", json.dumps(record))

    def test_ambiguous_region_does_not_get_weather(self):
        with patch("chatbot.load_settings", return_value=Settings(api_key="fake", kakao_api_key="fake")), patch(
            "chatbot.get_region_coordinates", return_value=RouteResult("시·도까지 알려 주세요.", status="unavailable", kind="region")
        ), patch("chatbot.get_weather") as forecast:
            answer = chatbot.answer_question("중구 내일 날씨")
        forecast.assert_not_called()
        self.assertIn("시·도", answer)

    def test_authentication_failure_is_safe_and_not_retried(self):
        calls = []
        def handle(request):
            calls.append(request)
            return httpx.Response(403, json={"error": "fake-secret"})
        with patch("kakao_maps.httpx.Client", side_effect=lambda **kwargs: REAL_CLIENT(transport=httpx.MockTransport(handle), **kwargs)):
            record = get_region_coordinates("경주", "fake-secret").record
        self.assertEqual(len(calls), 1)
        self.assertEqual(record["error_code"], "authentication_error")
        self.assertNotIn("fake-secret", json.dumps(record))


if __name__ == "__main__":
    unittest.main()

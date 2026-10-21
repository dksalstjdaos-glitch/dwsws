"""Open-Meteo 인자·날짜 범위·좌표 근거·준비물·기존 검색 연결을 검증합니다."""

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
from langchain_core.messages import AIMessage
from streamlit.testing.v1 import AppTest

import chatbot
import weather
import storage
from config import Settings
from kakao_maps import RouteResult
from test_search_policy import tool_call

REAL_CLIENT = httpx.Client
PLACE = {"id": "1", "name": "경주 확인 장소", "address": "경북 경주시", "x": "129.2", "y": "35.8",
         "url": "https://place.map.kakao.com/1"}


def payload(days=None):
    days = days or ["2026-10-21", "2026-10-22"]
    values = {"weather_code": 61, "temperature_2m_min": 8, "temperature_2m_max": 29,
              "precipitation_probability_max": 75, "precipitation_sum": 3,
              "wind_speed_10m_max": 9, "uv_index_max": 7}
    return {"daily": {"time": days, **{k: [v] * len(days) for k, v in values.items()}}}


class WeatherTests(unittest.TestCase):
    def setUp(self):
        fixed = patch("weather.today_kst", return_value=date(2026, 10, 20))
        fixed.start()
        self.addCleanup(fixed.stop)

    def client_patch(self, handler):
        return patch("weather.httpx.Client", side_effect=lambda **kwargs: REAL_CLIENT(
            transport=httpx.MockTransport(handler), **kwargs))

    def test_coordinates_dates_units_and_daily_fields(self):
        def handle(request):
            self.assertEqual(str(request.url).split("?")[0], weather.FORECAST_URL)
            self.assertEqual(request.url.params["latitude"], "35.8")
            self.assertEqual(request.url.params["longitude"], "129.2")
            self.assertEqual(request.url.params["start_date"], "2026-10-21")
            self.assertEqual(request.url.params["end_date"], "2026-10-22")
            self.assertEqual(request.url.params["timezone"], "Asia/Seoul")
            self.assertEqual(request.url.params["wind_speed_unit"], "ms")
            self.assertNotIn("Authorization", request.headers)
            return httpx.Response(200, json=payload())
        with self.client_patch(handle):
            result = weather.get_weather([PLACE], "2026-10-21", "2026-10-22")
        self.assertEqual(result.record["status"], "success")
        forecast = result.record["forecasts"][0]
        self.assertEqual(forecast["latitude"], float(PLACE["y"]))
        self.assertEqual(forecast["name"], PLACE["name"])
        answer = weather.weather_answer(result.record)
        for expected in ("2026-10-21", "2026-10-22", "75%", "3mm", "우산", "보온", "바람막이", "자외선", "Open-Meteo"):
            self.assertIn(expected, answer)

    def test_multiple_places_are_one_request_in_original_order(self):
        other = {**PLACE, "id": "2", "name": "두 번째 장소", "x": "129.3", "y": "35.9"}
        calls = []
        def handle(request):
            calls.append(request)
            self.assertEqual(request.url.params["latitude"], "35.8,35.9")
            self.assertEqual(request.url.params["longitude"], "129.2,129.3")
            return httpx.Response(200, json=[payload(), payload()])
        with self.client_patch(handle):
            record = weather.get_weather([PLACE, other], "2026-10-21", "2026-10-22").record
        self.assertEqual(len(calls), 1)
        self.assertEqual([f["name"] for f in record["forecasts"]], [PLACE["name"], other["name"]])

    def test_invalid_dates_past_and_out_of_range_never_call_api(self):
        with patch("weather.httpx.Client") as client:
            for start, end in [("invalid", "invalid"), ("2026-10-19", "2026-10-20"),
                               ("2026-10-20", "2026-11-05"), ("2026-10-22", "2026-10-21")]:
                result = weather.get_weather([PLACE], start, end)
                self.assertEqual(result.record["status"], "unavailable")
            client.assert_not_called()
        self.assertIsNone(weather.validate_dates("2026-10-20", "2026-11-04"))

    def test_invalid_coordinates_never_call_api(self):
        with patch("weather.httpx.Client") as client:
            for value in ("nan", "inf", "999", None, True):
                result = weather.get_weather([{**PLACE, "y": value}], "2026-10-21", "2026-10-22")
                self.assertEqual(result.record["status"], "unavailable")
            client.assert_not_called()

    def test_error_body_is_not_exposed_or_retried(self):
        for status in (401, 403, 429, 500):
            calls = []
            def handle(request):
                calls.append(request)
                return httpx.Response(status, json={"reason": "fake-secret"})
            with self.client_patch(handle):
                result = weather.get_weather([PLACE], "2026-10-21", "2026-10-22")
            self.assertEqual(len(calls), 1)
            self.assertEqual(result.record["status"], "failed")
            self.assertNotIn("fake-secret", json.dumps(result.record))

    def test_partial_or_wrong_days_are_not_presented_as_complete_forecast(self):
        with self.client_patch(lambda request: httpx.Response(200, json=payload(["2026-10-21"]))):
            result = weather.get_weather([PLACE], "2026-10-21", "2026-10-22")
        self.assertEqual(result.record["status"], "failed")

    def test_null_fields_do_not_become_invented_weather(self):
        data = payload()
        for key in ("precipitation_probability_max", "precipitation_sum", "uv_index_max"):
            data["daily"][key] = [None, None]
        with self.client_patch(lambda request: httpx.Response(200, json=data)):
            record = weather.get_weather([PLACE], "2026-10-21", "2026-10-22").record
        self.assertTrue(record["missing_values"])
        answer = weather.weather_answer(record)
        self.assertIn("정보 없음", answer)
        self.assertNotIn("우산·우비", answer)
        self.assertNotIn("자외선 차단제", answer)

    def test_outfits_match_cold_mild_hot_rain_wind_and_snow_forecasts(self):
        cases = [(2, 8, 71, "두꺼운 외투"), (18, 21, 0, "긴소매와 긴바지"), (26, 33, 0, "반소매")]
        for low, high, code, expected in cases:
            day = {"date": "2026-10-21", "temperature_2m_min": low, "temperature_2m_max": high,
                   "weather_code": code, "precipitation_probability_max": 0, "precipitation_sum": 0,
                   "wind_speed_10m_max": 0, "uv_index_max": 0}
            record = {"forecasts": [{"name": "경주", "days": [day]}]}
            text = " ".join(weather.clothing_advice(record))
            self.assertIn(expected, text)
            if code == 71:
                self.assertIn("방수 신발", text)
            if low == 18:
                self.assertNotIn("두꺼운 외투", text)
        day.update(precipitation_probability_max=60, wind_speed_10m_max=9, uv_index_max=7)
        text = " ".join(weather.clothing_advice(record))
        self.assertIn("물에 강한 신발", text)
        self.assertIn("바람막이", text)
        self.assertIn("모자", text)

    def test_outfit_does_not_invent_missing_temperature(self):
        day = {"date": "2026-10-21", **{field: None for field in weather.DAILY_FIELDS}}
        text = " ".join(weather.clothing_advice({"forecasts": [{"name": "경주", "days": [day]}]}))
        self.assertIn("기온 자료가 부족", text)
        self.assertNotIn("반소매", text)
        self.assertNotIn("최저 0", text)

    def test_clothing_followup_keeps_previous_region_date_and_refreshes_forecast(self):
        record = {"kind": "weather", "status": "success", "scope": "region", "start_date": "2026-10-23", "end_date": "2026-10-23",
                  "forecasts": [{"name": "경주시", "latitude": 35.8, "longitude": 129.2}]}
        history = [{"role": "assistant", "content": "경주 3일 뒤 날씨", "lookups": [record]}]
        trace = {}
        def handle(request):
            self.assertEqual(request.url.params["start_date"], "2026-10-23")
            return httpx.Response(200, json=payload(["2026-10-23"]))
        with patch("chatbot.get_region_coordinates") as region, self.client_patch(handle):
            answer = chatbot.answer_question("그날 옷은 뭐 입어?", history=history, execution_trace=trace)
        region.assert_not_called()
        self.assertEqual(trace["tool_calls"], 1)
        self.assertIn("외출·여행 옷차림", answer)
        self.assertIn("2026-10-23", answer)
        self.assertIsNone(weather.followup_weather("부산 옷 추천", history))

    def test_date_precedence_relative_and_korean_range(self):
        self.assertEqual(weather.dates_in_text("10월 21일부터 23일까지"), ("2026-10-21", "2026-10-23"))
        self.assertEqual(weather.dates_in_text("내일부터 모레까지"), ("2026-10-21", "2026-10-22"))
        self.assertEqual(weather.dates_in_text("2026년 10월 21일"), ("2026-10-21", "2026-10-21"))
        context = {"conditions": {"start_date": "2026-10-25", "end_date": "2026-10-26"}}
        self.assertEqual(weather.travel_dates("내일 날씨", travel_context=context), ("2026-10-21", "2026-10-21"))

    def test_missing_date_asks_and_waits_without_model_or_tools(self):
        trace = {}
        with patch("chatbot.ChatOpenAI") as model, patch("chatbot.search_places") as search, patch("chatbot.get_weather") as forecast:
            answer = chatbot.answer_question("경주 준비물 알려줘", execution_trace=trace)
        self.assertIn("여행 날짜", answer)
        self.assertEqual(trace["end_reason"], "여행 날짜 확인 대기")
        self.assertEqual(trace["tool_calls"], 0)
        model.assert_not_called()
        search.assert_not_called()
        forecast.assert_not_called()

    def test_places_then_weather_are_logged_in_order_within_budget(self):
        trace, records = {}, []
        model = MagicMock()
        model.bind_tools.return_value.invoke.return_value = tool_call("경주 관광명소")
        model.invoke.return_value = AIMessage(content="장소 안내")
        with patch("chatbot.load_settings", return_value=Settings(api_key="fake", kakao_api_key="fake")), patch(
            "chatbot.ChatOpenAI", return_value=model
        ), patch("chatbot.search_places", return_value=RouteResult("장소 결과", status="success", kind="places", places=[PLACE])), self.client_patch(
            lambda request: httpx.Response(200, json=payload())
        ):
            answer = chatbot.answer_question("경주 2026-10-21 ~ 2026-10-22 준비물", lookup_records=records, execution_trace=trace)
        self.assertEqual([r["tool"] for r in records], ["search_places", "get_weather"])
        self.assertEqual(trace["tool_calls"], 2)
        self.assertLessEqual(trace["model_calls"], 6)
        self.assertEqual([e["tool"] for e in trace["events"] if e["event"] == "tool_call"], ["search_places", "get_weather"])
        self.assertIn("우산", answer)

    def test_saved_coordinates_work_without_loading_openai_or_kakao_key(self):
        context = {"conditions": {"start_date": "2026-10-21", "end_date": "2026-10-22"}, "selected_places": [PLACE]}
        trace = {}
        with patch("chatbot.load_settings") as settings, self.client_patch(lambda request: httpx.Response(200, json=payload())):
            answer = chatbot.answer_question("선택한 장소의 날씨와 준비물", travel_context=context, execution_trace=trace)
        settings.assert_not_called()
        self.assertEqual(trace["tool_calls"], 1)
        self.assertEqual(trace["model_calls"], 0)
        self.assertIn(PLACE["name"], answer)

    def test_new_region_does_not_use_old_location(self):
        history = [{"role": "assistant", "lookups": [{"kind": "places", "status": "success", "places": [PLACE], "arguments": {"region": "경주"}}]}]
        self.assertEqual(weather.saved_weather_places("부산 내일 날씨", history), [])
        self.assertEqual(weather.saved_weather_places("내일 준비물 알려줘", history), [PLACE])

    def test_itinerary_includes_actual_forecast_in_final_model_input(self):
        context = {"conditions": {"start_date": "2026-10-21", "end_date": "2026-10-22"}, "selected_places": [PLACE]}
        model = MagicMock()
        model.invoke.return_value = AIMessage(content="### 일정\n선택 장소 방문 일정")
        trace = {}
        with patch("chatbot.load_settings", return_value=Settings(api_key="fake")), patch(
            "chatbot.ChatOpenAI", return_value=model
        ), self.client_patch(lambda request: httpx.Response(200, json=payload())):
            answer = chatbot.answer_question("선택한 장소로 일정 작성", travel_context=context, execution_trace=trace)
        self.assertIn('"precipitation_probability_max": 75', model.invoke.call_args.args[0][-1].content)
        self.assertEqual((trace["tool_calls"], trace["model_calls"]), (1, 1))
        self.assertIn("선택 장소 방문 일정", answer)
        self.assertIn("준비물", answer)

    def test_date_reply_continues_original_request_and_searches_then_forecasts(self):
        first_trace = {}
        answer = chatbot.answer_question("경주 준비물 알려줘", execution_trace=first_trace)
        history = [{"role": "user", "content": "경주 준비물 알려줘"},
                   {"role": "assistant", "content": answer, "execution": first_trace}]
        model = MagicMock()
        model.bind_tools.return_value.invoke.return_value = tool_call("경주 관광명소")
        model.invoke.return_value = AIMessage(content="실제 장소")
        records = []
        with patch("chatbot.load_settings", return_value=Settings(api_key="fake", kakao_api_key="fake")), patch(
            "chatbot.ChatOpenAI", return_value=model
        ), patch("chatbot.search_places", return_value=RouteResult("장소", status="success", kind="places", places=[PLACE])), self.client_patch(
            lambda request: httpx.Response(200, json=payload(["2026-10-21"]))
        ):
            answer = chatbot.answer_question("내일", history=history, lookup_records=records)
        self.assertEqual([r["tool"] for r in records], ["search_places", "get_weather"])
        self.assertIn("2026-10-21", answer)
        self.assertIn("우산", answer)

    def test_failed_weather_does_not_make_up_packing_advice(self):
        context = {"conditions": {"start_date": "2026-10-21", "end_date": "2026-10-22"}, "selected_places": [PLACE]}
        records, trace = [], {}
        with self.client_patch(lambda request: httpx.Response(429, json={"reason": "fake-secret"})):
            answer = chatbot.answer_question("선택한 장소 날씨와 준비물", travel_context=context,
                                             lookup_records=records, execution_trace=trace)
        self.assertEqual(trace["tool_calls"], 1)
        self.assertEqual(records[-1]["status"], "failed")
        self.assertNotIn("우산", answer)
        self.assertNotIn("fake-secret", json.dumps(trace))
        self.assertIn("요청 한도", answer)

    def test_planner_weather_button_displays_and_persists_forecast_and_trace(self):
        with tempfile.TemporaryDirectory() as folder, patch("storage.DB_PATH", Path(folder) / "weather.sqlite3"):
            storage.initialize()
            project = storage.create_project("경주 날씨")
            chat = storage.create_conversation(project)
            storage.append_turn(chat, "장소 검색", "실제 장소", lookups=[{
                "kind": "places", "status": "success", "query": "경주", "checked_at": "테스트", "places": [PLACE]}])
            storage.save_plan(project, conditions={"destination": "경주", "start_date": "2026-10-21", "end_date": "2026-10-22", "people": 2, "transport": "자동차"},
                              selected_places=[{**PLACE, "key": "1"}])
            with self.client_patch(lambda request: httpx.Response(200, json=payload())):
                app = AppTest.from_file("app.py")
                app.session_state["project_open"] = True
                app.run()
                next(b for b in app.button if b.label == "선택한 장소의 날씨와 준비물 확인").click().run()
            self.assertFalse(app.exception)
            assistant = app.session_state["messages"][-1]
            self.assertEqual(assistant["lookups"][-1]["tool"], "get_weather")
            self.assertEqual(assistant["execution"]["tool_calls"], 1)
            self.assertEqual(storage.list_executions(project)[0]["execution"]["weather_basis"]["status"], "success")
            self.assertTrue(any("날씨 조회 근거" in e.label for e in app.expander))


if __name__ == "__main__":
    unittest.main()

"""여행 계획의 근거 검사·부분 실패·방문 순서·시간대·저장을 검증합니다."""

import tempfile
import unittest
from copy import deepcopy
from datetime import date
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import chatbot
import storage
from config import Settings
from execution_trace import ExecutionTrace
from kakao_maps import RouteResult
from test_region_weather import REGION
from test_weather import payload
from trip_planning import build_travel_plan, check_plan_evidence, places_in_region, proposed_schedule

CONDITIONS = {"destination": "경주", "start_date": "2026-10-21", "end_date": "2026-10-22", "people": 2,
              "transport": "자동차", "interests": "박물관, 걷기 적게"}
PLACES = [{"id": "1", "key": "1", "name": "확인 박물관", "category": "문화 > 박물관", "address": "경북 경주시 일정로 1", "x": "129.2", "y": "35.8", "url": "https://place.map.kakao.com/1"},
          {"id": "2", "key": "2", "name": "확인 공원", "category": "여행 > 공원", "address": "경북 경주시 일정로 2", "x": "129.21", "y": "35.81"},
          {"id": "3", "key": "3", "name": "확인 카페", "category": "음식점 > 카페", "address": "경북 경주시 일정로 3", "x": "129.22", "y": "35.82"}]


def place_record(places=None):
    return {"kind": "places", "status": "success", "query": "경주 후보", "checked_at": "테스트", "places": deepcopy(places or PLACES),
            "travel_scope": {"region": "경주", "start_date": "2026-10-21", "end_date": "2026-10-22"}}


def forecast_record():
    data = payload()["daily"]
    days = [{"date": day, "description": "비", **{k: values[i] for k, values in data.items() if k != "time"}}
            for i, day in enumerate(data["time"])]
    return {"kind": "weather", "status": "success", "message": "예보 성공", "start_date": "2026-10-21", "end_date": "2026-10-22",
            "arguments": {"region": "경주"}, "checked_at": "테스트", "scope": "region", "forecasts": [
                {"name": REGION["name"], "latitude": float(REGION["y"]), "longitude": float(REGION["x"]), "days": days}]}


class TripPlanningTests(unittest.TestCase):
    def setUp(self):
        fixed = patch("weather.today_kst", return_value=date(2026, 10, 20))
        fixed.start()
        self.addCleanup(fixed.stop)

    def test_gate_rejects_failed_wrong_region_and_wrong_dates(self):
        failed = {**place_record(), "status": "failed"}
        dated = {**place_record(), "travel_scope": {"region": "경주", "start_date": "2026-11-01", "end_date": "2026-11-02"}}
        wrong = place_record([{**PLACES[0], "address": "부산 해운대구"}])
        places, weather, issues, checks = check_plan_evidence(CONDITIONS, [failed, dated, wrong, place_record()], forecast_record())
        self.assertEqual(len(places), 3)
        self.assertIsNotNone(weather)
        self.assertEqual(sum(c["결과"] == "제외 · 확인 필요" for c in checks), 3)
        self.assertTrue(any("날짜 불일치" in i for i in issues))

    def test_gate_rejects_weather_mismatches_and_incomplete_days(self):
        for field, value in [("status", "failed"), ("end_date", "2026-10-23"), ("arguments", {"region": "부산"})]:
            record = forecast_record()
            record[field] = value
            self.assertIsNone(check_plan_evidence(CONDITIONS, [place_record()], record)[1])
        record = forecast_record()
        record["forecasts"][0]["days"].pop()
        self.assertIsNone(check_plan_evidence(CONDITIONS, [place_record()], record)[1])

    def test_region_normalization_filters_other_regions(self):
        records = [*PLACES, {**PLACES[0], "id": "x", "key": "x", "address": "부산광역시 기장군"}]
        self.assertEqual(len(places_in_region(records, "경주시")), 3)
        self.assertEqual(len(places_in_region(records, "부산 기장")), 1)

    def test_rain_and_preferences_produce_unique_order_and_gentle_times(self):
        rows, ordered, _ = proposed_schedule(PLACES, CONDITIONS, forecast_record())
        self.assertEqual(ordered[0]["id"], "1")
        self.assertEqual(len({p["id"] for p in ordered}), len(ordered))
        self.assertTrue(all(r["time"] in ("오전 10:00", "오후 14:00", "자유 시간") for r in rows))
        self.assertIn("강수 예보", rows[0]["reason"])
        _, manual, _ = proposed_schedule(list(reversed(PLACES)), CONDITIONS, forecast_record(), preserve_order=True)
        self.assertEqual(manual[0]["id"], "3")

    def test_normal_plan_uses_checked_sources_without_model_call(self):
        context = {"plan_request": True, "conditions": CONDITIONS, "place_records": [place_record()]}
        trace = {}
        with patch("trip_planning.load_settings", return_value=Settings(api_key="fake", kakao_api_key="fake")), patch(
            "trip_planning.get_region_coordinates", return_value=RouteResult("지역", status="success", kind="region", places=[REGION])
        ), patch("trip_planning.get_weather", return_value=RouteResult("날씨", **{k: v for k, v in forecast_record().items() if k != "message"})), patch("chatbot.ChatOpenAI") as model:
            answer = chatbot.answer_question("여행 계획 만들기", travel_context=context, execution_trace=trace)
        model.assert_not_called()
        self.assertEqual(trace["tool_calls"], 2)
        self.assertTrue(trace["plan_validation"])
        self.assertIn("제안 시간대", answer)
        self.assertIn("챙길 준비물", answer)
        self.assertIn("외출·여행 옷차림", answer)

    def test_weather_failure_still_makes_place_based_partial_plan(self):
        run = ExecutionTrace()
        with patch("trip_planning.load_settings", return_value=Settings(api_key="fake", kakao_api_key="fake")), patch(
            "trip_planning.get_region_coordinates", return_value=RouteResult("인증 오류", kind="region")
        ), patch("trip_planning.get_weather") as weather:
            answer = build_travel_plan({"conditions": CONDITIONS, "place_records": [place_record()]}, [], run)
        weather.assert_not_called()
        self.assertIn("확인 박물관", answer)
        self.assertIn("날씨·옷차림·준비물 확인 필요", answer)
        self.assertNotIn("우산·우비", answer)
        self.assertIn("부분 계획", run.data["end_reason"])

    def test_no_matching_places_does_not_invent_visits(self):
        run = ExecutionTrace()
        with patch("trip_planning.load_settings", return_value=Settings(api_key="fake", kakao_api_key="fake")), patch(
            "trip_planning.get_region_coordinates", return_value=RouteResult("권한 없음", kind="region")
        ):
            answer = build_travel_plan({"conditions": CONDITIONS, "place_records": [{**place_record(), "status": "failed"}]}, [], run)
        self.assertNotIn("제안 시간대", answer)
        self.assertNotIn("proposed_places", run.data)
        self.assertIn("확인 필요", answer)

    def test_planner_button_saves_suggested_order_and_shows_checks(self):
        with tempfile.TemporaryDirectory() as folder, patch("storage.DB_PATH", Path(folder) / "plan.sqlite3"):
            storage.initialize()
            project = storage.create_project("계획 검증")
            chat = storage.create_conversation(project)
            storage.append_turn(chat, "후보 찾기", "확인 장소", lookups=[place_record()])
            storage.save_plan(project, conditions=CONDITIONS)
            with patch("trip_planning.load_settings", return_value=Settings(api_key="fake", kakao_api_key="fake")), patch(
                "trip_planning.get_region_coordinates", return_value=RouteResult("지역", status="success", kind="region", places=[REGION])
            ), patch("trip_planning.get_weather", return_value=RouteResult("날씨", **{k: v for k, v in forecast_record().items() if k != "message"})):
                app = AppTest.from_file("app.py")
                app.session_state["project_open"] = True
                app.run()
                next(b for b in app.button if b.label == "취향·날씨로 방문 순서와 여행 계획 추천").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(storage.load_plan(project)["selected_places"][0]["id"], "1")
            self.assertTrue(app.session_state["messages"][-1]["execution"]["plan_validation"])
            self.assertIn("제안 시간대", app.session_state["messages"][-1]["content"])


if __name__ == "__main__":
    unittest.main()

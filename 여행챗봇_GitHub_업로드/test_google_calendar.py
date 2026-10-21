"""실제 구글 계정 없이 시각·중복·부분 실패·인증 오류 중단을 검증합니다."""
import copy
import unittest
from unittest.mock import Mock

from google_calendar import calendar_events, register_events, CalendarError


def sample():
    return {"title": "경주 여행", "source_run_id": "source", "snapshot": {"execution": {
        "run_id": "source", "trip_schedule": [{"date": "2026-10-23", "time": "오후 13:00", "end_time": "14:30",
            "place": {"name": "박물관", "address": "경북 경주시", "url": "https://place.map.kakao.com/1"}, "reason": "제안"}]}}}


def response(status, data=None):
    result = Mock(status_code=status)
    result.json.return_value = data or {}
    return result


class CalendarTests(unittest.TestCase):
    def test_korean_times_and_fixed_end_are_kept(self):
        event = calendar_events(sample(), duration_minutes=60)[0]
        self.assertEqual(event["start"]["dateTime"], "2026-10-23T13:00:00+09:00")
        self.assertEqual(event["end"]["dateTime"], "2026-10-23T14:30:00+09:00")
        plan = sample()
        plan["id"] = "different-saved-copy"
        self.assertEqual(calendar_events(plan)[0]["id"], event["id"])

    def test_repeated_registration_does_not_post(self):
        events = calendar_events(sample())
        session = Mock()
        session.get.return_value = response(200, events[0])
        result = register_events(None, events, session)
        self.assertEqual(result["existing"], 1)
        self.assertEqual(result["error"], "")
        session.post.assert_not_called()

    def test_changed_event_is_not_overwritten(self):
        events = calendar_events(sample())
        changed = copy.deepcopy(events[0])
        changed["summary"] = "사용자가 수정함"
        session = Mock()
        session.get.return_value = response(200, changed)
        result = register_events(None, events, session)
        self.assertTrue(result["error"])
        session.post.assert_not_called()

    def test_partial_failure_then_retry_only_remaining(self):
        events = calendar_events(sample())
        second = {**copy.deepcopy(events[0]), "id": "another-id"}
        events.append(second)
        session = Mock()
        session.get.side_effect = [response(404), response(403)]
        session.post.return_value = response(201, events[0])
        result = register_events(None, events, session)
        self.assertEqual(result["created"], 1)
        self.assertTrue(result["error"])
        session = Mock()
        session.get.side_effect = [response(200, events[0]), response(404)]
        session.post.return_value = response(201, second)
        result = register_events(None, events, session)
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["existing"], 1)
        self.assertEqual(session.post.call_count, 1)

    def test_auth_failure_stops_and_error_is_safe(self):
        session = Mock()
        session.get.return_value = response(401, {"error": "secret-value"})
        events = calendar_events(sample()) * 3
        result = register_events(None, events, session)
        self.assertEqual(session.get.call_count, 1)
        self.assertNotIn("secret-value", str(result))
        session.post.assert_not_called()

    def test_overlap_and_ambiguous_time_block_registration(self):
        plan = sample()
        plan["snapshot"]["execution"]["trip_schedule"] *= 2
        with self.assertRaises(CalendarError):
            calendar_events(plan)
        plan = sample()
        plan["snapshot"]["execution"]["trip_schedule"][0]["time"] = "나중에"
        with self.assertRaises(CalendarError):
            calendar_events(plan)


if __name__ == "__main__":
    unittest.main()

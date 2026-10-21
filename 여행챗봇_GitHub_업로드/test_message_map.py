import unittest

from message_map import message_places


class MessageMapTests(unittest.TestCase):
    def test_plan_order_uses_only_matching_verified_places(self):
        a = {"name": "박물관A", "address": "경주시 A", "url": "https://place.map.kakao.com/1", "x": "129.2", "y": "35.8"}
        b = {"name": "박물관B", "address": "경주시 B", "url": "https://place.map.kakao.com/2", "x": "129.3", "y": "35.9"}
        message = {"lookups": [{"kind": "places", "status": "success", "places": [a, b]}],
                   "execution": {"trip_schedule": [{"place": {k: b[k] for k in ("name", "address", "url")}},
                                                   {"place": {k: a[k] for k in ("name", "address", "url")}}]}}
        points, is_plan, count = message_places(message)
        self.assertEqual([p["name"] for p in points], ["박물관B", "박물관A"])
        self.assertTrue(is_plan)
        self.assertEqual(count, 2)

    def test_followup_uses_saved_basis_but_rejects_missing_and_invalid_coordinates(self):
        message = {"execution": {"trip_schedule": [{"place": {"name": "확인 장소", "address": "경주"}},
                                                   {"place": {"name": "좌표 없음", "address": "경주"}}],
                    "day5_state": {"basis_lookups": [{"kind": "places", "status": "success", "places": [
                        {"name": "확인 장소", "address": "경주", "x": 129, "y": 35},
                        {"name": "좌표 없음", "address": "경주", "x": "nan", "y": 35}]}]}}}
        self.assertEqual(len(message_places(message)[0]), 1)
        message["execution"]["day5_state"]["basis_lookups"][0]["status"] = "failed"
        self.assertEqual(message_places(message)[0], [])

    def test_place_answer_uses_latest_successful_lookup_and_ignores_region(self):
        message = {"lookups": [{"kind": "region", "status": "success", "places": [{"name": "지역", "x": 129, "y": 35}]},
                  {"kind": "places", "status": "success", "places": [{"name": "이전 후보", "x": 129, "y": 35}]},
                  {"kind": "places", "status": "success", "places": [{"name": "최종 후보", "x": 129, "y": 35}]}]}
        points, is_plan, _ = message_places(message)
        self.assertEqual([p["name"] for p in points], ["최종 후보"])
        self.assertFalse(is_plan)


if __name__ == "__main__":
    unittest.main()

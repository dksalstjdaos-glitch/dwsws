"""가짜 HTTP 응답과 모델 응답으로 지도 연동을 검증합니다."""

import unittest
from unittest.mock import MagicMock, patch

import httpx
from langchain_core.messages import AIMessage, ToolMessage

import chatbot
from config import Settings
from kakao_maps import RouteResult, get_driving_route, search_places

REAL_CLIENT = httpx.Client


class KakaoMapsTests(unittest.TestCase):
    def test_greeting_does_not_load_keys_or_call_tools(self):
        for question in ("안녕하세요!", "안 녕", "Hi", "좋은 아침"):
            with patch("chatbot.load_settings") as settings, patch("chatbot.ChatOpenAI") as model, patch("chatbot.search_places") as search:
                records = []
                answer = chatbot.answer_question(question, lookup_records=records)
                self.assertIn("안녕하세요", answer)
                self.assertEqual(records, [])
                settings.assert_not_called()
                model.assert_not_called()
                search.assert_not_called()

    def test_place_question_without_date_logs_tool_arguments(self):
        model = MagicMock()
        model.bind_tools.return_value.invoke.side_effect = [
            AIMessage(content="", tool_calls=[{"name": "search_places", "args": {"query": "부산 해운대 카페", "category": "CE7"}, "id": "p1", "type": "tool_call"}]),
            AIMessage(content="**검색 카페** · 부산 해운대구 · [지도](https://place.map.kakao.com/1)"),
        ]
        records = []
        with patch("chatbot.load_settings", return_value=Settings(api_key="fake-openai", kakao_api_key="fake-kakao")), patch(
            "chatbot.ChatOpenAI", return_value=model
        ), patch("chatbot.search_places", return_value=RouteResult("검색 카페 · 부산 해운대구 · 지도 링크", status="success", kind="places")) as search:
            answer = chatbot.answer_question("안녕하세요, 부산 해운대 카페 추천해 줘", lookup_records=records)
        search.assert_called_once_with("부산 해운대 카페", "fake-kakao", category="CE7")
        self.assertEqual(records[0]["tool"], "search_places")
        self.assertEqual(records[0]["arguments"], {"query": "부산 해운대 카페", "category": "CE7", "region": "부산", "indoor_only": False})
        self.assertEqual(records[0]["status"], "success")
        self.assertNotIn("fake-kakao", str(records))
        self.assertNotIn("날짜", answer)

    def client_patch(self, handler):
        return patch("kakao_maps.httpx.Client", side_effect=lambda **kwargs: REAL_CLIENT(
            transport=httpx.MockTransport(handler), **kwargs))

    def test_route_coordinates_units_and_auth_header(self):
        def handle(request):
            self.assertEqual(request.headers["Authorization"], "KakaoAK fake-key")
            if "keyword" in request.url.path:
                name = request.url.params["query"]
                return httpx.Response(200, json={"documents": [{
                    "place_name": name, "x": "127.0" if name == "서울시청" else "127.1",
                    "y": "37.5", "address_name": "서울 중구", "road_address_name": "서울 중구 도로",
                }]})
            self.assertEqual(request.url.params["origin"], "127.0,37.5")
            self.assertEqual(request.url.params["destination"], "127.1,37.5")
            self.assertNotIn("fake-key", str(request.url))
            return httpx.Response(200, json={"routes": [{"result_code": 0,
                "summary": {"duration": 3661, "distance": 12345}}]})
        with self.client_patch(handle):
            result = get_driving_route("서울시청", "서울역", "fake-key")
        self.assertIn("1시간 2분", result)
        self.assertIn("12.3km", result)
        self.assertIn("조회 시각", result)
        self.assertNotIn("fake-key", result)
        self.assertEqual(result.record["status"], "success")
        self.assertEqual(result.record["distance_km"], 12.3)
        self.assertNotIn("fake-key", str(result.record))

    def test_ambiguous_place_asks_for_details_without_routing(self):
        def handle(request):
            self.assertIn("keyword", request.url.path)
            return httpx.Response(200, json={"documents": [
                {"place_name": "중앙역", "address_name": "안산"},
                {"place_name": "중앙역", "address_name": "부산"},
            ]})
        with self.client_patch(handle):
            result = get_driving_route("중앙역", "서울역", "fake-key")
        self.assertIn("여러 곳", result)
        self.assertNotIn("예상 소요 시간", result)

    def test_permission_error_never_exposes_body_or_key(self):
        with self.client_patch(lambda request: httpx.Response(403, json={"message": "fake-key"})):
            result = get_driving_route("서울시청", "서울역", "fake-key")
        self.assertIn("이용 권한", result)
        self.assertNotIn("fake-key", result)
        self.assertEqual(result.record["status"], "failed")
        self.assertEqual(result.record["error_code"], "authentication_error")
        self.assertFalse(result.record["retryable"])

    def test_place_auth_errors_are_not_empty_results_and_do_not_retry(self):
        for status in (401, 403, 429):
            calls = []
            def handle(request):
                calls.append(request)
                return httpx.Response(status, json={"message": "fake-key"})
            with self.client_patch(handle):
                result = search_places("경주 실내 장소", "fake-key")
            self.assertEqual(len(calls), 1)
            self.assertEqual(result.record["status"], "failed")
            self.assertFalse(result.record["retryable"])
            self.assertNotIn("fake-key", str(result.record))

    def test_network_error_is_safe(self):
        def handle(request):
            raise httpx.ConnectError("fake-key", request=request)
        with self.client_patch(handle):
            result = get_driving_route("서울시청", "서울역", "fake-key")
        self.assertIn("네트워크", result)
        self.assertNotIn("fake-key", result)

    def test_model_uses_route_tool_and_receives_result(self):
        model = MagicMock()
        first_response = AIMessage(content="", tool_calls=[{
            "name": "get_driving_route", "args": {"origin": "서울시청", "destination": "서울역"},
            "id": "route1", "type": "tool_call",
        }])
        model.bind_tools.return_value.invoke.side_effect = [first_response, AIMessage(content="카카오 조회 결과 약 14분입니다.")]
        with patch("chatbot.load_settings", return_value=Settings(api_key="fake-openai", kakao_api_key="fake-kakao")), patch(
            "chatbot.ChatOpenAI", return_value=model
        ), patch("chatbot.get_driving_route", return_value="실제 조회 결과 약 14분") as route:
            result = chatbot.answer_question("서울시청에서 서울역까지 차로 얼마나 걸려?")
        route.assert_called_once_with("서울시청", "서울역", "fake-kakao")
        messages = model.bind_tools.return_value.invoke.call_args.args[0]
        self.assertIsInstance(messages[-1], ToolMessage)
        self.assertEqual(messages[-1].content, "실제 조회 결과 약 14분")
        self.assertIn("14분", result)
        self.assertIn("카카오 조회 내역", result)
        self.assertNotIn("fake-kakao", str(messages))

    def test_place_search_returns_only_api_fields_and_handles_empty_result(self):
        def handle(request):
            self.assertEqual(request.url.params["query"], "부산 기장 해산물")
            self.assertEqual(request.headers["Authorization"], "KakaoAK fake-key")
            return httpx.Response(200, json={"documents": [{
                "id": "1", "place_name": "검색된 식당", "address_name": "부산 기장군",
                "road_address_name": "부산 기장군 해변로 1", "category_name": "음식점 > 해산물",
                "phone": "051-000-0000", "place_url": "https://place.map.kakao.com/1",
                "x": "129.1", "y": "35.2",
            }]})
        with self.client_patch(handle):
            result = search_places("부산 기장 해산물", "fake-key")
        self.assertEqual(result.record["kind"], "places")
        self.assertEqual(result.record["places"][0]["name"], "검색된 식당")
        self.assertIn("영업시간", result)
        self.assertNotIn("fake-key", str(result.record))
        with self.client_patch(lambda request: httpx.Response(200, json={"documents": []})):
            empty = search_places("없는 장소", "fake-key")
        self.assertEqual(empty.record["status"], "empty")
        self.assertEqual(empty.record["places"], [])

    def test_multiple_searches_feed_final_travel_plan(self):
        model = MagicMock()
        tool_model = model.bind_tools.return_value
        tool_model.invoke.side_effect = [
            AIMessage(content="", tool_calls=[{"name": "search_places", "args": {"query": "부산 기장 해산물"}, "id": "p1", "type": "tool_call"}]),
            AIMessage(content="", tool_calls=[{"name": "search_places", "args": {"query": "부산 기장 관광명소"}, "id": "p2", "type": "tool_call"}]),
            AIMessage(content="식당A에서 점심을 먹고 관광지B를 방문하는 계획입니다."),
        ]
        records = []
        model.invoke.return_value = AIMessage(content="식당A에서 점심을 먹고 관광지B를 방문하는 계획입니다.")
        results = [RouteResult("식당A 주소와 지도 링크", status="success", kind="places", query="부산 기장 해산물"),
                   RouteResult("관광지B 주소와 지도 링크", status="success", kind="places", query="부산 기장 관광명소")]
        with patch("chatbot.load_settings", return_value=Settings(api_key="fake-openai", kakao_api_key="fake-kakao")), patch(
            "chatbot.ChatOpenAI", return_value=model
        ), patch("chatbot.search_places", side_effect=results) as search:
            answer = chatbot.answer_question("부산 기장 해산물과 관광지 당일치기 계획", lookup_records=records)
        self.assertEqual(search.call_count, 2)
        self.assertEqual(len(records), 2)
        messages = model.invoke.call_args.args[0]
        self.assertEqual([m.content for m in messages if isinstance(m, ToolMessage)], list(results))
        self.assertIn("식당A", answer)
        self.assertIn("관광지B", answer)
        self.assertEqual(len(model.bind_tools.call_args.args[0]), 2)
        model.invoke.assert_called_once()

    def test_tool_calls_are_bounded(self):
        model = MagicMock()
        model.bind_tools.return_value.invoke.return_value = AIMessage(content="", tool_calls=[{
            "name": "search_places", "args": {"query": "부산 관광"}, "id": "p1", "type": "tool_call"}])
        model.invoke.return_value = AIMessage(content="확인된 결과 범위의 일정입니다.")
        with patch("chatbot.load_settings", return_value=Settings(api_key="fake", kakao_api_key="fake")), patch(
            "chatbot.ChatOpenAI", return_value=model
        ), patch("chatbot.search_places", return_value=RouteResult("결과", status="success", kind="places")) as search:
            chatbot.answer_question("부산 일정 추천")
        self.assertEqual(search.call_count, 1)
        model.invoke.assert_not_called()

    def test_empty_search_requires_adjusted_search_before_final_answer(self):
        model = MagicMock()
        model.invoke.return_value = AIMessage(content="검색된 해수욕장을 방문하는 일정입니다.")
        model.bind_tools.return_value.invoke.side_effect = [
            AIMessage(content="", tool_calls=[{"name": "search_places", "args": {"query": "부산 기장 바다 관광명소"}, "id": "p1", "type": "tool_call"}]),
            AIMessage(content="", tool_calls=[{"name": "search_places", "args": {"query": "부산 기장 해수욕장", "category": "AT4"}, "id": "p2", "type": "tool_call"}]),
            AIMessage(content="검색된 해수욕장을 방문하는 일정입니다."),
        ]
        with patch("chatbot.load_settings", return_value=Settings(api_key="fake", kakao_api_key="fake")), patch(
            "chatbot.ChatOpenAI", return_value=model
        ), patch("chatbot.search_places", side_effect=[
            RouteResult("검색어를 짧게 바꾸세요", status="empty", kind="places"),
            RouteResult("검색된 해수욕장", status="success", kind="places"),
        ]) as search:
            chatbot.answer_question("부산 기장 바다 일정")
        self.assertEqual(search.call_count, 2)
        search.assert_called_with("부산 기장 해수욕장", "fake", category="AT4")
        self.assertTrue(any("tool_choice" in call.kwargs for call in model.bind_tools.call_args_list))


if __name__ == "__main__":
    unittest.main()

"""카카오 장소 검색과 현재 출발 기준 자동차 길찾기. 인증 정보는 반환하지 않습니다."""

import math
from datetime import datetime, timedelta, timezone

import httpx

LOCAL_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"
ADDRESS_URL = "https://dapi.kakao.com/v2/local/search/address.json"
DIRECTIONS_URL = "https://apis-navi.kakaomobility.com/v1/directions"

DRIVING_ROUTE_TOOL = {
    "type": "function",
    "function": {
        "name": "get_driving_route",
        "description": (
            "한국 내 출발지와 도착지의 현재 출발 기준 자동차 예상 소요 시간과 거리를 카카오에서 조회합니다. "
            "자동차 이동 시간·거리 질문에 사용하세요. 이전 대화의 명시적 출발지·목적지도 참고할 수 있습니다. "
            "장소가 없거나 '우리집'처럼 주소를 모르면 먼저 물어보세요. 대중교통·도보·미래 출발 예측은 지원하지 않습니다."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "origin": {"type": "string", "description": "사용자가 지정한 출발지 이름 또는 주소. 지역을 포함하세요."},
                "destination": {"type": "string", "description": "사용자가 지정한 도착지 이름 또는 주소. 지역을 포함하세요."},
            },
            "required": ["origin", "destination"],
            "additionalProperties": False,
        },
    },
}

PLACE_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "search_places",
        "description": (
            "카카오에서 한국 내 실제 장소를 키워드로 검색합니다. 맛집·카페·관광지·숙소 추천과 "
            "여행 일정을 작성하기 전에 사용하세요. 검색어에는 사용자가 지정한 지역과 장소 유형을 포함하세요. "
            "주소·분류·전화·카카오맵 링크·좌표를 제공하며 별점, 리뷰, 메뉴 가격, 영업시간은 제공하지 않습니다."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "지역을 포함한 구체적 검색어. 예: 부산 기장 해산물, 부산 기장 관광명소"},
                "category": {"type": "string", "enum": ["", "FD6", "CE7", "AT4", "AD5"],
                             "description": "식당 FD6, 카페 CE7, 관광명소 AT4, 숙박 AD5. 유형을 제한하지 않으면 빈 문자열."},
                "region": {"type": "string", "description": "사용자가 지정한 지역. 예: 경주, 부산 기장. 재검색할 때도 그대로 유지하세요."},
            },
            "required": ["query", "region"], "additionalProperties": False,
        },
    },
}


class KakaoLookupError(Exception):
    """사용자에게 보여도 되는 고정 안내만 담습니다."""

    def __init__(self, message, error_code="lookup_error", retryable=False):
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable


class RouteResult(str):
    """모델에 전달할 설명과 화면에 표시할 실제 API 기록을 함께 보관합니다."""

    def __new__(cls, message, status="failed", **details):
        result = super().__new__(cls, message)
        result.record = {"kind": "route", "status": status, "message": message, **details}
        return result


def request_json(client, url, params):
    try:
        response = client.get(url, params=params)
        if response.status_code in (401, 403):
            service = "카카오맵 장소 검색" if "dapi.kakao.com" in url else "카카오모빌리티 자동차 길찾기"
            raise KakaoLookupError(
                f"{service} 인증 또는 이용 권한이 없습니다. REST API 키와 해당 서비스 사용 설정을 확인해 주세요.",
                error_code="authentication_error",
            )
        if response.status_code == 429:
            raise KakaoLookupError("카카오 API 요청 한도를 초과했습니다. 사용 한도를 확인한 뒤 다시 시도해 주세요.", error_code="rate_limit")
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError):
        raise KakaoLookupError("카카오 지도 조회에 실패했습니다. 네트워크 또는 서비스 상태를 확인해 주세요.", error_code="network_error", retryable=True) from None


def resolve_place(client, query):
    documents = request_json(client, LOCAL_URL, {"query": query, "size": 5}).get("documents", [])
    if not documents:
        documents = request_json(client, ADDRESS_URL, {"query": query, "size": 5}).get("documents", [])
    if not documents:
        raise KakaoLookupError(f"‘{query}’ 위치를 찾지 못했습니다. 지역과 정확한 장소명 또는 주소를 알려 주세요.")
    exact = [doc for doc in documents if doc.get("place_name", "").replace(" ", "") == query.replace(" ", "")]
    if len(exact) == 1:
        return exact[0]
    if len(documents) == 1:
        return documents[0]
    candidates = "; ".join(
        f"{doc.get('place_name') or doc.get('address_name')} ({doc.get('road_address_name') or doc.get('address_name', '')})"
        for doc in documents[:3]
    )
    raise KakaoLookupError(f"‘{query}’ 검색 결과가 여러 곳입니다. 정확한 장소를 선택해 알려 주세요: {candidates}")


def get_region_coordinates(region, api_key):
    """관광 장소가 아닌 행정구역의 대표 좌표를 조회합니다."""
    if not api_key:
        return RouteResult("지역 좌표를 조회하려면 카카오 REST API 키 설정이 필요합니다.", kind="region")
    if not isinstance(region, str) or not region.strip() or len(region) > 80:
        return RouteResult("날씨를 확인할 시·군·구를 알려 주세요.", status="unavailable", kind="region")
    try:
        with httpx.Client(headers={"Authorization": f"KakaoAK {api_key}"}, timeout=15) as client:
            data = request_json(client, ADDRESS_URL, {"query": region.strip(), "size": 10})
        regions = [d for d in data.get("documents", []) if d.get("address_type") == "REGION"]
        if len(regions) != 1:
            candidates = [d.get("address_name", "") for d in regions]
            message = ("같은 이름의 지역이 여러 곳입니다. 시·도까지 포함해 알려 주세요: " + ", ".join(candidates[:5])) if candidates else "해당 지역을 확인하지 못했습니다. 시·군·구 이름을 알려 주세요."
            return RouteResult(message, status="unavailable", kind="region", error_code="region_unclear")
        doc = regions[0]
        point = {"name": doc["address_name"], "address": doc["address_name"], "x": doc["x"], "y": doc["y"], "scope": "region"}
        return RouteResult("행정구역 대표 좌표 확인 완료", status="success", kind="region", places=[point],
                           source="카카오 주소 검색", query=region)
    except KakaoLookupError as error:
        return RouteResult(str(error), kind="region", error_code=error.error_code)
    except Exception:
        return RouteResult("지역 좌표를 확인하지 못했습니다. 연결 상태 또는 지역명을 확인해 주세요.", kind="region")


def search_places(query, api_key, category=""):
    """카카오에서 반환된 장소만 도구 결과와 조회 기록에 포함합니다."""
    if not api_key:
        return RouteResult("카카오 REST API 키가 없어 장소를 검색하지 못했습니다.", kind="places")
    if not isinstance(query, str) or not query.strip() or len(query) > 300:
        return RouteResult("지역과 찾으시는 장소 유형을 알려 주세요.", kind="places")
    query = query.strip()
    if category not in ("", "FD6", "CE7", "AT4", "AD5"):
        return RouteResult("지원하지 않는 장소 분류입니다.", kind="places", query=query)
    try:
        params = {"query": query, "size": 5}
        if category:
            params["category_group_code"] = category
        with httpx.Client(headers={"Authorization": f"KakaoAK {api_key}"}, timeout=15) as client:
            data = request_json(client, LOCAL_URL, params)
        checked_at = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M KST")
        places = [{
            "id": doc.get("id", ""), "name": doc["place_name"],
            "address": doc.get("road_address_name") or doc.get("address_name", ""),
            "category": doc.get("category_name", ""), "phone": doc.get("phone", ""),
            "url": doc.get("place_url", ""), "x": doc.get("x", ""), "y": doc.get("y", ""),
        } for doc in data.get("documents", [])[:5]]
        if not places:
            return RouteResult(
                f"‘{query}’ 카카오 검색 결과가 없습니다. 키워드를 짧게 바꾸어 search_places를 다시 호출하세요. "
                "예: '부산 기장 바다 관광명소' 대신 '부산 기장 해수욕장'. "
                "검색되지 않은 장소를 실제 검색 결과처럼 추천하지 마세요.",
                status="empty", kind="places", query=query, checked_at=checked_at, places=[],
            )
        lines = [
            f"{i}. {p['name']} | 주소: {p['address']} | 분류: {p['category']} | "
            f"전화: {p['phone'] or '정보 없음'} | 카카오맵: {p['url']} | 좌표: {p['x']},{p['y']}"
            for i, p in enumerate(places, 1)
        ]
        text = (
            f"카카오 장소 검색 결과 · 검색어: {query} · 조회 시각: {checked_at}\n" + "\n".join(lines) +
            "\n이 결과는 장소 검색 자료이며 리뷰나 인기 순위가 아닙니다. "
            "영업시간, 휴무일, 메뉴 가격, 주차, 맛 평가와 예약 가능 여부는 확인되지 않았습니다."
        )
        return RouteResult(text, status="success", kind="places", query=query,
                           checked_at=checked_at, places=places)
    except KakaoLookupError as error:
        return RouteResult(str(error), kind="places", query=query, error_code=error.error_code, retryable=error.retryable)
    except Exception:
        return RouteResult("카카오 장소 검색 응답을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.",
                           kind="places", query=query)


def get_driving_route(origin, destination, api_key):
    if not api_key:
        return RouteResult("카카오 REST API 키가 설정되지 않아 조회하지 못했습니다.")
    if not all(isinstance(value, str) and value.strip() and len(value) <= 300 for value in (origin, destination)):
        return RouteResult("출발지와 도착지를 정확한 장소명이나 주소로 알려 주세요.")
    try:
        with httpx.Client(headers={"Authorization": f"KakaoAK {api_key}"}, timeout=15) as client:
            start = resolve_place(client, origin.strip())
            end = resolve_place(client, destination.strip())
            data = request_json(client, DIRECTIONS_URL, {
                "origin": f"{start['x']},{start['y']}",
                "destination": f"{end['x']},{end['y']}",
                "priority": "RECOMMEND", "summary": "true", "alternatives": "false",
            })
        routes = data.get("routes", [])
        if not routes or routes[0].get("result_code") != 0:
            return RouteResult("두 장소 사이의 자동차 경로를 찾지 못했습니다. 출발지와 도착지 또는 도로 연결 여부를 확인해 주세요.")
        summary = routes[0]["summary"]
        minutes = max(1, math.ceil(summary["duration"] / 60))
        hours, remaining = divmod(minutes, 60)
        duration = f"{hours}시간 {remaining}분" if hours else f"{remaining}분"
        distance = summary["distance"] / 1000
        checked_at = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M KST")
        def label(place):
            name = place.get("place_name") or place.get("address_name")
            address = place.get("road_address_name") or place.get("address_name", "")
            return f"{name} ({address})"
        return RouteResult(
            f"카카오 자동차 길찾기 실제 조회 결과\n출발: {label(start)}\n도착: {label(end)}\n"
            f"예상 소요 시간: 약 {duration}\n거리: {distance:.1f}km\n조회 시각: {checked_at}\n"
            "현재 출발·추천 경로 기준이며 교통상황에 따라 달라질 수 있습니다. 미래 출발 시간 예측값이 아닙니다.\n"
            "출처: 카카오 로컬 API 및 카카오모빌리티 자동차 길찾기 API",
            status="success", origin=label(start), destination=label(end),
            duration=duration, distance_km=round(distance, 1), checked_at=checked_at,
            origin_url=start.get("place_url", ""), destination_url=end.get("place_url", ""),
            request_id=data.get("trans_id", ""),
        )
    except KakaoLookupError as error:
        return RouteResult(str(error), error_code=error.error_code, retryable=error.retryable)
    except Exception:
        return RouteResult("카카오 지도 응답을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.")

"""제공 코드의 도구 반환 형식을 기존 카카오·Open-Meteo 모듈에 연결합니다."""
from contextvars import ContextVar
from datetime import datetime

from config import load_settings
from kakao_maps import search_places, get_region_coordinates
from weather import get_weather, KST

lookup_sink = ContextVar("day5_lookup_sink", default=None)


def _record(record):
    sink = lookup_sink.get()
    if sink is not None:
        sink.append(record)
    return record


def search_places_raw(query, size=5):
    result = search_places(query, load_settings().kakao_api_key)
    r = _record({**result.record, "tool": "search_places", "arguments": {"query": query}})
    return {"status": {"success": "ok", "empty": "empty"}.get(r["status"], "error"),
            "query": query, "source": "카카오 로컬 검색", "retrieved_at": r.get("checked_at", ""),
            "message": str(result) if r["status"] != "success" else "",
            "error_kind": r.get("error_code", ""),
            "places": [{**p, "lat": p.get("y"), "lon": p.get("x")} for p in r.get("places", [])][:size]}


def region_coordinates(region):
    result = get_region_coordinates(region, load_settings().kakao_api_key)
    r = _record({**result.record, "tool": "get_region_coordinates", "arguments": {"region": region}})
    if r["status"] != "success":
        return {"status": "error", "message": str(result)}
    p = r["places"][0]
    return {"status": "ok", "lat": float(p["y"]), "lon": float(p["x"]),
            "name": p["name"], "address": p["address"]}


def get_weather_raw(lat, lon, day):
    sink = lookup_sink.get() or []
    region = next((r["places"][0]["name"] for r in reversed(sink)
                   if r.get("kind") == "region" and r.get("status") == "success" and r.get("places")), "지역 대표 지점")
    result = get_weather([{"name": region, "y": lat, "x": lon}], day, day)
    r = _record({**result.record, "tool": "get_weather", "scope": "region",
                 "arguments": {"region": region, "start_date": day, "end_date": day}})
    base = {"date": day, "unit": {"temp": "°C", "precip_prob": "%"}, "forecast": None,
            "source": "Open-Meteo.com", "retrieved_at": r.get("checked_at", ""), "message": ""}
    if r["status"] != "success":
        return {**base, "status": "out_of_range" if r.get("error_code") == "forecast_range" else "error", "message": str(result)}
    daily = r["forecasts"][0]["days"][0]
    return {**base, "status": "ok", "forecast": {"temp_min": daily.get("temperature_2m_min"),
            "temp_max": daily.get("temperature_2m_max"), "precip_prob_max": daily.get("precipitation_probability_max")}}


def today():
    return datetime.now(KST).date()

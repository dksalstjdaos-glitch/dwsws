"""답변에 사용된 장소만 실제 조회 좌표와 연결해 대화 안에 표시합니다."""
import hashlib
import json
import math

import streamlit as st
from planner import draw_map


def _point(place):
    try:
        x, y = float(place.get("x", place.get("lon"))), float(place.get("y", place.get("lat")))
        if not (math.isfinite(x) and math.isfinite(y) and -180 <= x <= 180 and -85 <= y <= 85):
            return None
    except (TypeError, ValueError):
        return None
    key = str(place.get("id") or place.get("url") or (place.get("name", ""), place.get("address", "")))
    return {**place, "x": x, "y": y, "key": key}


def message_places(message):
    execution = message.get("execution") or {}
    records = (message.get("lookups") or []) + (execution.get("day5_state") or {}).get("basis_lookups", [])
    records = [r for r in records if r.get("kind") == "places" and r.get("status") == "success"]
    pool = [p for r in records for p in r.get("places", [])]
    pool += execution.get("proposed_places", []) + execution.get("answer_basis", [])
    schedule = execution.get("trip_schedule") or []
    wanted = ([row["place"] for row in schedule if row.get("place")] if schedule
              else execution.get("answer_basis") or (records[-1].get("places", []) if records else []))
    points = []
    seen = set()
    for place in wanted:
        point = _point(place)
        if point is None:
            matches = [p for p in pool if (place.get("url") and p.get("url") == place["url"]) or
                       (p.get("name") == place.get("name") and p.get("address") == place.get("address"))]
            valid = [p for p in (_point(p) for p in matches) if p]
            # 같은 이름의 다른 지점을 표시하지 않도록 좌표가 하나로 확인될 때만 연결합니다.
            if len({(p["x"], p["y"]) for p in valid}) == 1:
                point = valid[-1]
        if point and point["key"] not in seen:
            points.append(point)
            seen.add(point["key"])
    return points, bool(schedule), len(wanted)


def render_message_map(message, key_prefix="", default_visible=True):
    places, is_plan, total = message_places(message)
    if not places:
        return
    identity = hashlib.sha256(json.dumps(message, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
    visible = st.toggle("방문 장소 지도" if is_plan else "추천 장소 지도", value=default_visible,
                        key=f"{key_prefix}show_map_{identity}")
    if visible:
        draw_map(places, places if is_plan else [], key=f"{key_prefix}message_map_{identity}")
        if len(places) < total:
            st.caption(f"총 {total}개 항목 중 실제 조회 좌표가 확인된 {len(places)}곳만 표시합니다.")

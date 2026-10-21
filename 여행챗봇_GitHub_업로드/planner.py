"""여행 조건 → 지도와 방문 순서 → 일정 작성 흐름."""

import math
from datetime import date, datetime, timedelta, timezone

import pydeck as pdk
import streamlit as st

import storage
from trip_planning import check_plan_evidence


def map_data(places, selected):
    positions = []
    order = {place["key"]: i + 1 for i, place in enumerate(selected)}
    for place in places:
        try:
            lon, lat = float(place["x"]), float(place["y"])
            if not (math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -85 <= lat <= 85):
                continue
        except (KeyError, ValueError, TypeError):
            continue
        number = order.get(place["key"])
        positions.append({"position": [lon, lat], "name": place["name"], "address": place.get("address", ""),
                          "label": str(number) if number else "", "color": [15, 49, 146] if number else [127, 127, 127],
                          "key": place["key"]})
    lookup = {place["key"]: place["position"] for place in positions}
    path = [lookup[p["key"]] for p in selected if p["key"] in lookup]
    return positions, path


def draw_map(places, selected, *, key=None):
    points, path = map_data(places, selected)
    if not points:
        st.info("장소 검색 후 좌표가 있는 장소가 지도에 표시됩니다.")
        return
    lon = sum(p["position"][0] for p in points) / len(points)
    lat = sum(p["position"][1] for p in points) / len(points)
    span = max(max(p["position"][axis] for p in points) - min(p["position"][axis] for p in points) for axis in (0, 1))
    zoom = max(5, min(14, math.log2(180 / max(span, 0.01)) - 1))
    layers = [pdk.Layer("ScatterplotLayer", points, get_position="position", get_fill_color="color",
                        get_radius=90, radius_min_pixels=6, radius_max_pixels=12, pickable=True),
              pdk.Layer("TextLayer", points, get_position="position", get_text="label", get_color=[255, 255, 255], get_size=13)]
    if len(path) > 1:
        layers.insert(0, pdk.Layer("PathLayer", [{"path": path}], get_path="path", get_color=[15, 49, 146],
                                  get_width=3, width_min_pixels=3, width_max_pixels=3))
    st.pydeck_chart(pdk.Deck(map_provider="carto", map_style="light", layers=layers,
        initial_view_state=pdk.ViewState(latitude=lat, longitude=lon, zoom=zoom),
        tooltip={"text": "{name}\n{address}"}), height=320, key=key)
    st.caption("회색은 검색 후보, 파란색 숫자는 방문 순서입니다. 연결선은 순서 미리보기이며 실제 도로 경로가 아닙니다.")


def render_planner(project_id):
    plan = storage.load_plan(project_id)
    conditions = plan["conditions"]
    request = None
    build_requested = False
    preserve_order = False
    st.subheader("여행 계획 만들기")
    conditions_tab, map_tab = st.tabs(["1 · 여행 조건", "2 · 지도와 방문 순서"])
    with conditions_tab, st.expander("여행 조건 입력·수정", expanded=False):
        today = datetime.now(timezone(timedelta(hours=9))).date()
        with st.form(f"conditions_{project_id}"):
            destination = conditions.get("destination", "")
            date_enabled = st.checkbox("여행 날짜 정하기 (선택)", value=bool(conditions.get("start_date")))
            left, right = st.columns(2)
            dates = left.date_input("여행 날짜", value=(date.fromisoformat(conditions.get("start_date") or today.isoformat()),
                date.fromisoformat(conditions.get("end_date") or (today + timedelta(days=1)).isoformat())), disabled=not date_enabled)
            people = right.number_input("여행 인원", min_value=1, max_value=100, value=conditions.get("people"), placeholder="미정")
            left, right = st.columns(2)
            budget = left.number_input("전체 예산 (원 · 0이면 미정)", min_value=0, value=conditions.get("budget", 0), step=10000)
            transports = ["자동차", "대중교통", "도보", "미정"]
            transport = right.selectbox("교통수단", transports, index=transports.index(conditions.get("transport", "미정")))
            origin = st.text_input("출발지 (선택)", value=conditions.get("origin", ""), placeholder="예: 부산역")
            interests = st.text_input("취향·요청 (선택)", value=conditions.get("interests", ""), placeholder="예: 해산물, 바다, 아이와 함께, 걷기 적게")
            save, search = st.columns(2)
            saved = save.form_submit_button("조건 저장")
            searched = search.form_submit_button("조건 저장하고 장소 찾기", type="primary")
        if saved or searched:
            if date_enabled and (len(dates) != 2 or dates[1] < dates[0]):
                st.warning("여행 시작일과 종료일을 선택해 주세요.")
            else:
                conditions = {"destination": destination.strip(), "start_date": dates[0].isoformat() if date_enabled else "", "end_date": dates[1].isoformat() if date_enabled else "",
                    "people": int(people) if people is not None else None, "budget": int(budget), "transport": transport, "origin": origin.strip(), "interests": interests.strip()}
                storage.save_plan(project_id, conditions=conditions)
                st.success("여행 조건을 저장했습니다.")
                if searched and destination.strip():
                    request = f"{destination.strip()} 여행 조건에 맞는 식사 장소와 관광지 후보를 카카오에서 검색해 줘. 먼저 후보를 비교하고 지도에서 선택할 수 있게 추천해 줘."
                elif searched:
                    st.info("조건은 저장했습니다. 대화창에서 ‘경주에서 장소 찾아줘’처럼 여행 지역을 알려 주세요.")
        if conditions:
            date_label = f"{conditions['start_date']} ~ {conditions['end_date']}" if conditions.get("start_date") else "날짜 미정"
            people_label = f"{conditions['people']}명" if conditions.get("people") else "인원 미정"
            st.caption(f"저장된 조건: {conditions.get('destination') or '지역 미정'} · {date_label} · {people_label} · {conditions['transport']}")
            st.caption("날짜를 정하면 일정과 준비물 안내에 Open-Meteo 예보를 사용합니다. 예보 범위는 오늘부터 최대 16일입니다.")
    with map_tab:
        place_records = storage.project_place_records(project_id)
        candidates = check_plan_evidence(conditions, place_records)[0] if conditions.get("destination") else storage.project_places(project_id)
        by_key = {p["key"]: p for p in candidates}
        options = list(by_key)
        selection_key = f"visit_order_{project_id}"
        pending = st.session_state.pop(f"pending_visit_order_{project_id}", None)
        if pending is not None:
            st.session_state[selection_key] = [key for key in pending if key in by_key]
        if selection_key not in st.session_state:
            st.session_state[selection_key] = [p["key"] for p in plan["selected_places"] if p["key"] in by_key]
        st.session_state[selection_key] = [key for key in st.session_state[selection_key] if key in by_key]
        selected_ids = st.multiselect("방문할 장소를 순서대로 선택", options, format_func=lambda key: by_key[key]["name"] + " · " + by_key[key].get("address", ""),
                                      key=selection_key, max_selections=12)
        selected = [by_key[key] for key in selected_ids]
        if selected != plan["selected_places"]:
            storage.save_plan(project_id, selected_places=selected)
        draw_map(list(by_key.values()), selected)
        if selected:
            st.write(" → ".join(f"{i}. {p['name']}" for i, p in enumerate(selected, 1)))
            st.caption("선택을 해제한 뒤 다시 선택하면 마지막 순서로 옮겨집니다. 선택한 순서는 자동 저장됩니다.")
        if st.button("선택한 동선으로 일정 만들기", key=f"build_plan_{project_id}", disabled=not (conditions and selected), type="primary"):
            request = "저장한 여행 조건과 선택한 방문 순서를 지켜 날짜별 여행 일정을 작성해 줘. 선택한 장소의 실제 검색 정보를 근거로 하고, 방문 순서를 임의로 바꾸거나 다른 장소를 추가하지 마."
            build_requested, preserve_order = True, True
        if st.button("취향·날씨로 방문 순서와 여행 계획 추천", key=f"suggest_plan_{project_id}",
                     disabled=not (conditions.get("start_date") and conditions.get("end_date") and candidates)):
            request = "저장한 지역·날짜·취향에 맞는 실제 장소와 날씨를 검사하고, 방문 순서·시간대·준비물을 포함한 여행 계획을 제안해 줘."
            build_requested = True
        if not conditions.get("start_date") or not conditions.get("end_date"):
            st.info("여행 날짜는 ‘여행 조건’에서 저장하고, 여행 지역은 대화창에서 알려 주세요.")
        st.caption("계획은 같은 지역·날짜의 성공 자료를 검사한 뒤 만듭니다. 장소를 선택하면 그 안에서, 선택하지 않으면 현재 조건의 후보에서 순서를 제안합니다.")
        if st.button("선택한 장소의 날씨와 준비물 확인", key=f"weather_plan_{project_id}", disabled=not selected):
            request = "선택한 장소의 좌표와 저장한 여행 날짜로 날씨 예보를 조회하고 준비물을 알려 줘."
        if not candidates:
            st.info("현재 지역·날짜에 맞는 장소 후보가 없습니다. 다른 조건의 자료는 제외합니다. 여행 조건을 저장하고 장소를 검색해 주세요.")
    context = {"conditions": conditions, "selected_places": selected} if conditions or selected else None
    if build_requested:
        context.update(plan_request=True, preserve_order=preserve_order, place_records=place_records)
    return request, context

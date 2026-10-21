"""여행 답변과 실제 조회 근거를 읽기 쉽게 표시합니다."""

import json

import streamlit as st
from trace_view import render_trace
from weather import FIELD_NAMES, SOURCE_URL
from schedule_view import render_schedule
from message_map import render_message_map


def map_link(label, url):
    if isinstance(url, str) and url.startswith(("http://place.map.kakao.com/", "https://place.map.kakao.com/")):
        st.link_button(label, url)


def render_answer(message, key_prefix="", compact=True, show_map=True):
    execution = message.get("execution") or {}
    if compact and execution.get("trip_schedule"):
        st.caption("여행 플래너")
        heading = next((line for line in message["content"].splitlines() if line.startswith("### ")), None)
        if heading:
            st.markdown(heading)
        render_schedule(execution["trip_schedule"])
        render_message_map(message, key_prefix, default_visible=show_map)
        issues = execution.get("plan_issues") or (execution.get("day5_state") or {}).get("plan", {}).get("basis", {}).get("check", {}).get("notes", [])
        if issues:
            st.warning("확인 필요: " + " · ".join(issues))
        with st.expander("상세 보기 · 날씨·준비물·조회 근거", expanded=False):
            render_answer(message, key_prefix=key_prefix, compact=False)
        return
    content, marker, appendix = message["content"].partition("\n\n**카카오 조회 내역**")
    st.caption("여행 플래너")
    st.markdown(content.strip())
    if compact:
        render_message_map(message, key_prefix, default_visible=show_map)
    execution = message.get("execution")
    if execution:
        with st.expander(f"실행 기록 · 도구 {execution.get('tool_calls', 0)}/4회 · 모델 {execution.get('model_calls', 0)}/6회"):
            render_trace(execution, download_key=f"{key_prefix}answer_trace_{execution['run_id']}")
    records = message.get("lookups")
    if records is None:
        st.caption("이전 답변 · 카카오 조회 여부를 확인할 기록이 없습니다.")
        if marker:
            with st.expander("이전 답변에 포함된 조회 내역"):
                st.text(appendix.strip())
    elif not records:
        st.caption("ℹ️ 카카오 조회 안 함 · 장소 도구 호출 0회")

    if records and not execution:
        tool_names = {"search_places": "장소 검색", "get_driving_route": "자동차 길찾기", "unsupported_tool": "지원하지 않는 도구"}
        state_names = {"success": "성공", "failed": "실패", "empty": "검색 결과 없음", "skipped": "실행 안 함", "blocked": "조건 변경 확인 필요", "unverified": "확인 불가", "unknown": "알 수 없음"}
        argument_names = {"query": "검색어", "category": "장소 종류", "region": "지역", "indoor_only": "실내 조건", "origin": "출발지", "destination": "도착지"}
        categories = {"FD6": "음식점", "CE7": "카페", "AT4": "관광명소", "AD5": "숙박", "": "전체"}
        with st.expander(f"도구 실행 기록 · {len(records)}회"):
            st.dataframe([{
                "도구": tool_names.get(record.get("tool"), "이전 기록 · 도구명 미저장"),
                "검색 조건": json.dumps({argument_names.get(key, key): ("실내만" if value else "제한 없음") if key == "indoor_only" else categories.get(value, value) if key == "category" else value
                    for key, value in record.get("arguments", {}).items()}, ensure_ascii=False),
                "실행 상태": state_names.get(record.get("status"), "알 수 없음"),
            } for record in records], hide_index=True)

    for record in records or []:
        if record.get("kind") == "region":
            if record.get("status") == "success":
                st.caption("지역 대표 좌표 확인 · " + ", ".join(p.get("name", "") for p in record.get("places", [])))
            else:
                st.warning(record["message"])
        elif record.get("kind") == "weather":
            if record.get("status") == "success":
                st.success("✅ Open-Meteo 예보 조회 완료")
                with st.expander(f"날씨 조회 근거 · {record['start_date']} ~ {record['end_date']}"):
                    coordinate_source = "카카오 행정구역 주소 검색" if record.get("scope") == "region" else "카카오 장소 검색 결과"
                    st.caption(f"조회: {record['checked_at']} · 날짜 기준: 한국 시간 · 좌표: {coordinate_source}")
                    for forecast in record["forecasts"]:
                        st.write(f"**{forecast['name']}** · 위도 {forecast['latitude']} · 경도 {forecast['longitude']}")
                        st.dataframe([{"날짜": d["date"], **{label: d.get("description") if field == "weather_code" else d.get(field)
                            for field, label in FIELD_NAMES.items()}} for d in forecast["days"]], hide_index=True)
                    st.link_button("날씨 출처 · Open-Meteo", SOURCE_URL)
                    st.caption("예보는 바뀔 수 있습니다. 일부 항목의 값이 비어 있으면 준비물 판단에서 제외합니다.")
                    st.json(record)
            else:
                st.warning("Open-Meteo 예보 조회 미완료")
                st.write(record["message"])
        elif record.get("status") == "success" and record.get("kind") == "places":
            st.success("✅ 카카오 장소 검색 완료")
            with st.expander(f"검색된 장소 {len(record['places'])}곳 · {record['query']}"):
                st.caption(f"조회 시각: {record['checked_at']}")
                for place in record["places"]:
                    with st.container(border=True):
                        st.markdown(f"**{place['name']}**")
                        st.write(place["address"])
                        map_link(f"{place['name']} · 카카오맵에서 확인", place.get("url", ""))
                st.caption("별점·리뷰·영업시간·메뉴 가격은 검색 결과에 포함되지 않습니다.")
                with st.expander("장소 검색 근거 보기"):
                    st.json(record)
        elif record.get("status") == "success":
            with st.container(border=True):
                st.success("✅ 카카오 API 실제 조회 완료")
                st.write(f"{record['origin']} → {record['destination']}")
                duration, distance = st.columns(2)
                duration.metric("자동차 예상 시간", record["duration"])
                distance.metric("이동 거리", f"{record['distance_km']} km")
                st.caption(f"{record['checked_at']} 조회 · 현재 출발·추천 경로 기준")
                with st.expander("지도와 조회 근거 확인"):
                    map_link("출발 장소를 카카오맵에서 확인", record.get("origin_url", ""))
                    map_link("도착 장소를 카카오맵에서 확인", record.get("destination_url", ""))
                    st.caption("카카오 로컬 API · 카카오모빌리티 자동차 길찾기 API")
                    if record.get("request_id"):
                        st.text(f"요청 ID: {record['request_id']}")
                    st.json(record)
        else:
            st.warning("카카오 조회 미완료")
            st.write(record["message"])

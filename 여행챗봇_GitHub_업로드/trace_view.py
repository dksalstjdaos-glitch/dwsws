"""실행 사건과 검색 결과를 한국어로 표시하고 실행끼리 비교합니다."""

import json

import streamlit as st

TOOLS = {"research_trip": "장소·날씨 조사 및 계획 만들기", "update_plan": "현재 계획 수정", "search_places": "장소 검색", "get_driving_route": "자동차 길찾기", "get_weather": "Open-Meteo 날씨 예보", "get_region_coordinates": "지역 대표 좌표 조회"}
STATES = {"success": "성공", "empty": "결과 없음", "failed": "실패", "blocked": "조건 확인 필요",
          "ok": "성공", "updated": "계획 수정됨", "need_info": "필수 정보 확인 대기", "confirm_needed": "사용자 확인 대기", "not_applied": "수정 미적용", "no_change": "변경 없음", "error": "조회 오류",
          "skipped": "실행 안 함", "unverified": "확인 불가", "unavailable": "조회 불가"}
EVENTS = {"model_call": "모델 호출", "tool_call": "도구 호출", "tool_return": "도구 반환",
          "tool_skipped": "도구 미실행", "decision": "다음 처리", "finished": "종료", "answer": "답변 작성 완료"}
ARGS = {"query": "검색어", "region": "지역", "category": "종류", "indoor_only": "실내만",
        "origin": "출발지", "destination": "도착지", "places": "조회 장소·좌표", "start_date": "시작일", "end_date": "종료일"}
ERRORS = {"authentication_error": "인증·권한 오류", "rate_limit": "API 사용 한도 초과",
          "network_error": "연결 오류", "lookup_error": "장소 확인 필요", "unexpected_failure": "예상하지 못한 조회 실패",
          "weather_unavailable": "예보 자료 부족 또는 연결 오류", "forecast_range": "예보 범위 밖", "missing_coordinates": "장소 좌표 부족"}


def event_rows(execution):
    rows = []
    for event in execution.get("events", []):
        detail = event.get("reason") or event.get("purpose") or ""
        if event.get("event") == "tool_call":
            detail = json.dumps({ARGS.get(k, k): v for k, v in event.get("arguments", {}).items()}, ensure_ascii=False)
        elif event.get("event") == "tool_return":
            found = " · ".join(p.get("name", "") for p in event.get("found", []))
            detail = (f"후보 {event['result_count']}곳" if event.get("result_count") is not None else "조회 반환")
            detail += (f" · {found}" if found else "") + (f" · 오류: {ERRORS.get(event['error_code'], event['error_code'])}" if event.get("error_code") else "")
            if event.get("forecast_locations"):
                detail = "예보 반환 · " + " / ".join(f"{p['name']} · {len(p['dates'])}일" for p in event["forecast_locations"])
        elif event.get("event") == "answer":
            detail = f"마지막 성공 조회 근거 {event.get('basis_count', 0)}건 · 답변 종료"
        rows.append({"순서": event.get("step"), "시각": event.get("at", ""),
                     "처리": EVENTS.get(event.get("event"), "기타"),
                     "도구 / 행동": TOOLS.get(event.get("tool"), event.get("action", "")),
                     "상태": STATES.get(event.get("status"), event.get("status", "")), "내용": detail})
    return rows


def render_trace(execution, *, download_key):
    limits = execution.get("limits", {})
    st.caption(f"실행 {execution.get('run_id', '')[:8]} · 도구 {execution.get('tool_calls', 0)}/{limits.get('tools', 4)}회"
               f" · 모델 {execution.get('model_calls', 0)}/{limits.get('models', 6)}회")
    st.write(f"**종료 이유:** {execution.get('end_reason') or '진행 중'}")
    if execution.get("langsmith_trace_id"):
        st.caption(f"LangSmith 추적 요청 ID: {execution['langsmith_trace_id']} · 프로젝트: {execution.get('langsmith_project', '')}")
    if execution.get("langsmith_error"):
        st.warning(execution["langsmith_error"])
    if "lookup_calls" in execution:
        st.caption(f"에이전트 도구 내부의 실제 외부 조회 {execution['lookup_calls']}회 · LangSmith 추적 {'사용' if execution.get('langsmith_enabled') else '사용 안 함'}")
    st.caption(f"시작: {execution.get('started_at', '')} · 종료: {execution.get('ended_at') or '진행 중'}"
               f" · 소요 {execution.get('elapsed_ms', 0) / 1000:.2f}초 · 시각은 UTC 기준")
    rows = event_rows(execution)
    if rows:
        st.dataframe(rows, hide_index=True, width="stretch")
    if execution.get("plan_validation"):
        st.write("**여행 계획 근거 검사 · 통과한 자료만 사용**")
        st.dataframe(execution["plan_validation"], hide_index=True, width="stretch")
    events = execution.get("events", [])
    hints = []
    if any(e.get("action") == "재검색" for e in events):
        hints.append("첫 검색이 0건이었습니다. 첫 검색어와 재검색어를 비교해 검색어 구성을 점검하세요.")
    if any(e.get("error_code") == "authentication_error" for e in events):
        hints.append("검색어보다 카카오 키와 해당 서비스의 이용 권한을 먼저 확인하세요.")
    if any(e.get("status") == "blocked" for e in events):
        hints.append("같은 검색 반복 또는 조건 변경 요청이 차단되었습니다. 직전 호출 인자와 비교하세요.")
    if "한도" in execution.get("end_reason", "") and "도달" in execution.get("end_reason", ""):
        hints.append("호출 한도에서 종료되었습니다. 불필요한 호출이나 너무 넓은 요청인지 점검하세요.")
    if any(e.get("action") == "답변 근거 일치" for e in events):
        hints.append("장소 안내는 마지막 조회 결과로 구성했습니다. 아래 답변 근거에서 장소·주소·링크를 확인하세요.")
    if hints:
        st.write("**점검할 부분**")
        for hint in hints:
            st.caption("• " + hint)
    basis = execution.get("answer_basis", [])
    if basis:
        st.write("**마지막 성공 조회 결과 · 답변 근거**")
        st.dataframe([{k: v for k, v in p.items() if k in ("name", "address", "url", "origin", "destination", "duration", "distance_km")}
                      for p in basis], hide_index=True, width="stretch")
    st.caption("모델의 내부 생각·프롬프트 전문·API 키는 기록하지 않습니다. 미실행 요청은 실제 도구 호출 횟수에 포함하지 않습니다.")
    st.download_button("실행 기록 JSON 저장", json.dumps(execution, ensure_ascii=False, indent=2),
                       file_name=f"travel-run-{execution.get('run_id', 'record')[:8]}.json",
                       mime="application/json", key=download_key)


def render_comparison(runs):
    if not runs:
        return
    with st.expander("실행 기록 비교 · 무엇을 찾고 왜 다시 찾았는지 확인"):
        st.caption("현재 프로젝트의 최근 실행 100건에서 선택합니다. 이전 버전의 답변은 상세 실행 기록이 없습니다.")
        if len(runs) < 2:
            st.info("실행이 두 번 이상 쌓이면 나란히 비교할 수 있습니다.")
            render_trace(runs[0]["execution"], download_key="compare_single")
            return
        labels = {r["id"]: f"{r['question'][:45]} · {r['execution'].get('started_at', '')} · {r['id'][:8]}" for r in runs}
        by_id = {r["id"]: r for r in runs}
        left, right = st.columns(2)
        with left:
            first = st.selectbox("비교할 실행 A", list(labels), format_func=labels.get, key="compare_run_a")
        remaining = [key for key in labels if key != first]
        same = [key for key in remaining if by_id[key]["question"].strip() == by_id[first]["question"].strip()]
        choices = same + [key for key in remaining if key not in same]
        with right:
            second = st.selectbox("비교할 실행 B", choices, format_func=labels.get, key="compare_run_b")
        if by_id[first]["question"].strip() != by_id[second]["question"].strip():
            st.caption("서로 다른 질문의 실행입니다. 동일 요청을 비교하려면 같은 질문의 실행을 선택하세요.")
        for column, key in ((left, first), (right, second)):
            with column:
                run = by_id[key]
                st.write(f"**질문:** {run['question']}")
                render_trace(run["execution"], download_key=f"compare_{key}")
                with st.expander("이 실행의 답변"):
                    st.markdown(run["answer"].partition("\n\n**카카오 조회 내역**")[0])

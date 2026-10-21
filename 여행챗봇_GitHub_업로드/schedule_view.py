"""생성한 방문 데이터를 간결한 표로 표시합니다."""
import streamlit as st


def render_schedule(schedule):
    rows = []
    for item in schedule:
        place = item.get("place") or {}
        start = item.get("time", "미정")
        end = item.get("end_time")
        url = place.get("url", "")
        rows.append({"날짜": item.get("date", "미정"), "시간": f"{start} ~ {end}" if end else start,
                     "방문 장소": place.get("name", "휴식·자유 시간"),
                     "지도": url if isinstance(url, str) and url.startswith(("https://place.map.kakao.com/", "http://place.map.kakao.com/")) else None})
    st.dataframe(rows, hide_index=True, width="stretch",
                 column_config={"지도": st.column_config.LinkColumn("지도", display_text="지도 보기")})
    st.caption("시간·순서는 제안입니다. 운영시간·휴무·실제 이동시간은 확인이 필요합니다.")

"""대화와 분리해 보관하는 여행 계획 UI."""

import streamlit as st

import storage
from answer_view import render_answer
from calendar_view import render_calendar


def render_saved_plans(project_id):
    saved = storage.list_itineraries(project_id)
    with st.expander(f"저장한 여행 계획 · {len(saved)}개", expanded=False):
        notice_key = f"deleted_itinerary_notice_{project_id}"
        if notice_key in st.session_state:
            st.success(st.session_state.pop(notice_key))
        if not saved:
            st.info("계획표 아래 ‘이 계획 따로 저장’을 눌러 보관하세요.")
            return
        titles = {item["id"]: item["title"] for item in saved}
        picker_key = f"saved_itinerary_picker_{project_id}"
        if st.session_state.get(picker_key) not in titles:
            st.session_state[picker_key] = next(iter(titles))
        chosen = st.selectbox("보관한 계획 선택", list(titles), format_func=titles.get,
                              key=picker_key)
        plan = storage.load_itinerary(project_id, chosen)
        st.caption("저장 당시의 계획과 예보입니다. 현재 여행 조건을 바꾸거나 새 대화를 시작해도 보관됩니다. 최신 예보는 다시 확인해 주세요.")
        st.subheader(plan["title"])
        st.download_button("계획표 파일로 내려받기 (.md)", plan["snapshot"]["content"],
                           file_name=f"여행계획_{chosen[:8]}.md", mime="text/markdown",
                           key=f"download_itinerary_{chosen}")
        render_calendar(plan)
        with st.expander("저장한 계획 삭제"):
            st.warning(f"‘{plan['title']}’ 보관 사본을 삭제합니다. 삭제한 사본은 복원할 수 없습니다. 원본 대화와 현재 여행 조건은 유지됩니다.")
            confirmed = st.checkbox("이 저장 계획을 삭제합니다", key=f"delete_itinerary_confirm_{chosen}")
            if st.button("저장 계획 삭제하기", disabled=not confirmed, key=f"delete_itinerary_{chosen}"):
                try:
                    storage.delete_itinerary(project_id, chosen)
                except Exception:
                    st.error("계획을 삭제하지 못했습니다. 저장 공간과 폴더 접근 권한을 확인해 주세요.")
                else:
                    st.session_state[notice_key] = f"‘{plan['title']}’ 저장 계획을 삭제했습니다."
                    st.rerun()
        # 대화에 있는 동일 실행의 다운로드 위젯과 키가 겹치지 않게 범위를 나눕니다.
        with st.container(key=f"saved_itinerary_{chosen}"):
            render_answer(plan["snapshot"], key_prefix=f"saved_{chosen}_")


def render_save_plan(project_id, message):
    execution = message.get("execution") or {}
    if not execution.get("trip_schedule") or not execution.get("run_id"):
        return
    run_id = execution["run_id"]
    existing = next((item for item in storage.list_itineraries(project_id)
                     if item["source_run_id"] == run_id), None)
    if existing:
        st.success(f"별도 저장됨 · {existing['title']} · 위 ‘저장한 여행 계획’에서 다시 열 수 있습니다.")
        return
    heading = next((line.lstrip("# ").strip() for line in message["content"].splitlines()
                    if line.startswith("### ")), "나의 여행 계획")
    with st.expander("이 계획 따로 저장"):
        with st.form(f"save_itinerary_{run_id}"):
            title = st.text_input("계획 이름", value=heading[:80], max_chars=80)
            if st.form_submit_button("계획 저장"):
                if not title.strip():
                    st.warning("계획 이름을 입력해 주세요.")
                else:
                    try:
                        storage.save_itinerary(project_id, title, message)
                    except Exception:
                        st.error("계획을 저장하지 못했습니다. 저장 공간과 폴더 접근 권한을 확인해 주세요.")
                    else:
                        st.rerun()

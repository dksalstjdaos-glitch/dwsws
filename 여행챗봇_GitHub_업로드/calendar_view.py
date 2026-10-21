"""저장 계획의 구글 캘린더 연결·미리보기·직접 등록 화면."""

import streamlit as st

from google_calendar import CalendarError, calendar_events, client_config, connect_google, register_events


def render_calendar(plan):
    plan_id = plan["id"]
    credential_key = "google_calendar_credentials"
    with st.expander("구글 캘린더에 바로 등록"):
        st.caption("연결한 구글 계정의 기본 캘린더에 방문 장소별로 등록합니다. 날짜·시간은 한국 시간입니다.")
        duration = st.number_input("장소별 방문 시간(분)", min_value=15, max_value=240, value=90, step=15,
                                   key=f"calendar_duration_{plan_id}")
        st.caption("계획에 종료 시간이 있으면 그 시간을 유지합니다. 종료 시간이 없는 항목만 위 방문 시간을 적용합니다. 자유 시간은 등록하지 않습니다.")
        try:
            events = calendar_events(plan, int(duration))
        except CalendarError as error:
            st.warning(str(error))
            return
        st.dataframe([{"일정": e["summary"], "시작(한국 시간)": e["start"]["dateTime"][:16].replace("T", " "),
                       "종료(한국 시간)": e["end"]["dateTime"][:16].replace("T", " "), "장소": e["location"]}
                      for e in events], hide_index=True, width="stretch")
        try:
            client_config()
        except CalendarError:
            st.info("최초 1회 구글 OAuth 설정이 필요합니다. 아래 안내대로 설정 파일을 준비해 주세요.")
            st.markdown("""1. [Google Cloud 콘솔](https://console.cloud.google.com/)에서 프로젝트를 만들거나 선택합니다.
2. **Google Calendar API**를 사용 설정합니다.
3. **Google Auth Platform**에서 동의 화면을 설정합니다. 테스트 상태라면 사용할 구글 계정을 테스트 사용자로 추가합니다.
4. **클라이언트 → OAuth 클라이언트 만들기 → 데스크톱 앱**으로 생성하고 JSON 파일을 내려받습니다.
5. 이 프로젝트의 **`.data/google_oauth_client.json`**으로 저장한 뒤 ‘설정 다시 확인’을 누르세요.

파일 내용이나 구글 비밀번호를 대화창에 붙여 넣지 마세요.""")
            if st.button("설정 다시 확인", key=f"calendar_config_{plan_id}"):
                st.rerun()
            return
        if credential_key not in st.session_state:
            st.caption("계정 연결을 누르면 PC 기본 브라우저에서 구글 로그인이 열립니다. 캘린더 일정 권한을 승인한 뒤 이 화면으로 돌아오세요. 연결 대기는 최대 2분입니다.")
            if st.button("구글 계정 연결", key=f"google_connect_{plan_id}"):
                try:
                    with st.spinner("브라우저에서 구글 계정 연결을 완료해 주세요..."):
                        credentials = connect_google()
                except CalendarError as error:
                    st.error(str(error))
                else:
                    st.session_state[credential_key] = credentials
                    st.rerun()
            return
        st.success("구글 계정 연결됨 · 로그인할 때 선택한 계정의 기본 캘린더에 등록합니다.")
        st.caption("연결 정보는 현재 앱 세션 메모리에만 보관합니다. 앱을 다시 열면 연결이 필요할 수 있습니다.")
        if st.button("구글 계정 연결 해제", key=f"google_disconnect_{plan_id}"):
            del st.session_state[credential_key]
            st.rerun()
        confirmed = st.checkbox(f"위 방문 일정 {len(events)}개를 구글 캘린더에 등록합니다",
                                key=f"google_confirm_{plan_id}_{duration}")
        if st.button("구글 캘린더에 등록", disabled=not confirmed, key=f"google_register_{plan_id}", type="primary"):
            with st.spinner("등록 결과를 확인하고 있습니다..."):
                result = register_events(st.session_state[credential_key], events)
            st.session_state[f"calendar_result_{plan_id}"] = result
        result = st.session_state.get(f"calendar_result_{plan_id}")
        if result:
            st.write(f"새로 등록 {result['created']}개 · 이미 등록됨 {result['existing']}개")
            if result["error"]:
                st.error(result["error"])
                st.caption("일부만 등록되었을 수 있습니다. 재시도 시 등록된 항목은 중복 생성하지 않습니다.")
            else:
                st.success("전체 방문 일정의 등록을 확인했습니다.")
            for index, link in enumerate(result["links"], 1):
                st.link_button(f"등록 일정 {index} 확인", link)
        st.caption("이 앱에서 저장 계획을 삭제해도 구글 캘린더 일정은 유지됩니다. 등록 후 변경·삭제는 구글 캘린더에서 진행하세요.")

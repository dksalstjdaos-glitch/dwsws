"""키를 노출하지 않는 사이드바 추적 설정 안내."""
import streamlit as st
from langsmith_tracing import load_tracing_settings, TracingConfigError, test_connection


def render_langsmith_settings():
    with st.expander("LangSmith 추적 연결"):
        st.caption("추적을 켜면 질문·답변·모델/도구 실행 기록이 LangSmith로 전송됩니다. API 키는 표시하거나 대화 DB에 저장하지 않습니다.")
        try:
            settings = load_tracing_settings()
        except TracingConfigError as error:
            st.warning(str(error))
            return
        st.write("**추적:** " + ("켜짐" if settings.enabled else "꺼짐"))
        st.write("**API 키:** " + ("설정됨" if settings.api_key else "미설정"))
        st.write("**프로젝트:** " + settings.project)
        st.caption("리전: " + ("유럽(EU)" if "eu.api" in settings.endpoint else "미국(US)"))
        st.caption(".env에 LANGSMITH_TRACING=true, LANGSMITH_API_KEY, LANGSMITH_PROJECT를 설정하세요. 워크스페이스 지정이 필요한 키는 LANGSMITH_WORKSPACE_ID도 입력하세요.")
        st.caption("연결 테스트는 실제 대화 대신 고정 테스트 기록 1건만 전송합니다.")
        if st.button("LangSmith 연결 테스트", disabled=not settings.api_key, key="test_langsmith_connection"):
            with st.spinner("LangSmith 추적 쓰기 권한을 확인하고 있습니다..."):
                result = test_connection(settings)
            if result["ok"]:
                st.success(result["message"])
                st.caption("테스트 실행 ID: " + result["run_id"])
            else:
                st.error(result["message"])
        st.link_button("LangSmith 열기", "https://eu.smith.langchain.com" if "eu.api" in settings.endpoint else "https://smith.langchain.com")

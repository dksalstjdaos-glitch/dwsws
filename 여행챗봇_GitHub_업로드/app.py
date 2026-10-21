"""실행: python -m streamlit run app.py"""

import streamlit as st
import storage

from answer_view import render_answer
from chatbot import answer_question
from config import MissingAPIKeyError
from planner import render_planner
from saved_plans import render_save_plan, render_saved_plans
from trace_view import render_comparison, render_trace
from langsmith_tracing import invoke_traced
from langsmith_view import render_langsmith_settings

st.set_page_config(page_title="여행 챗봇", page_icon="✈️", layout="wide", initial_sidebar_state="expanded")
from ui_styles import apply_styles, render_intro, render_welcome, render_project_welcome
apply_styles()
render_intro()

if "messages" not in st.session_state:
    st.session_state.messages = []


def new_conversation(project_id):
    conversation_id = storage.create_conversation(project_id)
    st.session_state[f"chat_{project_id}"] = conversation_id
    st.session_state.messages = []
    st.session_state.question = ""


def show_project_home():
    st.session_state.project_open = False
    st.session_state.question = ""


def open_saved_project():
    st.session_state.project_id = st.session_state.home_project_picker
    st.session_state.pending_project = st.session_state.home_project_picker
    st.session_state.project_open = True
    st.session_state.question = ""


def select_sidebar_project():
    st.session_state.project_id = st.session_state.sidebar_project_picker
    st.session_state.question = ""


if st.session_state.get("project_open", False):
    st.button("← 시작 화면", key="back_to_project_home", on_click=show_project_home)


try:
    storage.initialize()
    # 기능 적용 전 현재 세션에 남아 있던 대화도 처음 한 번 가져옵니다.
    if not st.session_state.get("storage_ready"):
        initial_projects = storage.list_projects()
        if st.session_state.messages and initial_projects:
            initial_project = initial_projects[0]["id"]
            imported = storage.create_conversation(initial_project, st.session_state.messages)
            st.session_state[f"chat_{initial_project}"] = imported
        st.session_state.storage_ready = True

    projects = storage.list_projects()
    names = {project["id"]: project["name"] for project in projects}
    if not names:
        st.session_state.project_open = False
    if not st.session_state.get("project_open", False):
        render_project_welcome()
        with st.container(border=True, key="project_home_controls"):
            if "project_deleted" in st.session_state:
                st.success(st.session_state.pop("project_deleted"))
            st.subheader("먼저 프로젝트 이름을 정해 주세요")
            st.write("예: 제주 가족 여행, 부산 주말 여행, 일본 3박 4일")
            with st.form("start_project"):
                name = st.text_input("프로젝트 이름", placeholder="여행 프로젝트 이름을 입력하세요", max_chars=80)
                if st.form_submit_button("프로젝트 만들고 시작하기", type="primary"):
                    if name.strip():
                        st.session_state.pending_project = storage.create_project(name)
                        st.session_state.project_open = True
                        st.session_state.question = ""
                        st.rerun()
                    else:
                        st.warning("프로젝트 이름을 입력해 주세요.")
            if names:
                st.subheader("저장된 프로젝트 이어가기")
                if st.session_state.get("home_project_picker") not in names:
                    current = st.session_state.get("project_id")
                    st.session_state.home_project_picker = current if current in names else next(iter(names))
                st.selectbox("저장된 프로젝트", list(names), format_func=names.get, key="home_project_picker")
                st.button("프로젝트 열기", on_click=open_saved_project)
        st.stop()

    with st.sidebar:
        st.header("내 프로젝트")
        if "project_deleted" in st.session_state:
            st.success(st.session_state.pop("project_deleted"))
        projects = storage.list_projects()
        names = {project["id"]: project["name"] for project in projects}
        with st.form("create_project", clear_on_submit=True):
            project_name = st.text_input("새 프로젝트 이름", max_chars=80)
            if st.form_submit_button("프로젝트 만들기"):
                if project_name.strip():
                    st.session_state.pending_project = storage.create_project(project_name)
                    st.rerun()
                else:
                    st.warning("프로젝트 이름을 입력해 주세요.")

        if "pending_project" in st.session_state:
            st.session_state.project_id = st.session_state.pop("pending_project")
        if not names:
            st.session_state.messages = []
            st.session_state.project_open = False
            st.rerun()
        if st.session_state.get("project_id") not in names:
            st.session_state.project_id = next(iter(names))
        # 선택 위젯의 수명과 무관하게 실제 열린 프로젝트 ID를 별도로 유지합니다.
        st.session_state.sidebar_project_picker = st.session_state.project_id
        project_id = st.selectbox(
            "프로젝트", list(names), format_func=names.get,
            key="sidebar_project_picker", on_change=select_sidebar_project,
        )

        chats = storage.list_conversations(project_id)
        if not chats:
            storage.create_conversation(project_id)
            chats = storage.list_conversations(project_id)
        titles = {chat["id"]: chat["title"] for chat in chats}
        if st.session_state.get(f"chat_{project_id}") not in titles:
            st.session_state[f"chat_{project_id}"] = next(iter(titles))
        conversation_id = st.selectbox(
            "저장된 대화", list(titles), format_func=titles.get, key=f"chat_{project_id}",
        )
        with st.form(f"rename_{conversation_id}"):
            title = st.text_input("대화 이름", value=titles[conversation_id], max_chars=80)
            if st.form_submit_button("이름 저장"):
                if title.strip():
                    storage.rename_conversation(conversation_id, title)
                    st.rerun()
                else:
                    st.warning("대화 이름을 입력해 주세요.")
        with st.expander("프로젝트 삭제"):
            st.warning(
                f"‘{names[project_id]}’ 프로젝트와 저장된 대화 {len(chats)}개, 별도 저장한 여행 계획을 삭제합니다. "
                "삭제한 내용은 복원할 수 없습니다."
            )
            confirmed = st.checkbox("프로젝트와 대화를 모두 삭제합니다", key=f"delete_confirm_{project_id}")
            if not confirmed:
                st.caption("위 확인란을 선택하면 삭제 버튼이 활성화됩니다.")
            if st.button("프로젝트 삭제하기", disabled=not confirmed):
                st.session_state.pending_project = storage.delete_project(project_id)
                st.session_state.pop(f"chat_{project_id}", None)
                st.session_state.messages = []
                st.session_state.question = ""
                st.session_state.project_deleted = f"‘{names[project_id]}’ 프로젝트와 대화를 삭제했습니다."
                st.rerun()
        st.button("프로젝트 시작 화면", on_click=show_project_home)
    st.session_state.messages = storage.load_messages(conversation_id)
except Exception:
    st.error("저장된 대화를 열지 못했습니다. 프로젝트 폴더의 저장 공간과 파일 접근 권한을 확인해 주세요.")
    st.stop()

with st.sidebar:
    st.divider()
    st.header("여행 설정")
    render_langsmith_settings()
    st.caption("현재 프로젝트 · " + names[project_id])
    day5_mode = st.radio("대화 방식", ["기존 여행 챗봇", "5일차 계획 에이전트"], horizontal=True,
                        key="travel_agent_mode") == "5일차 계획 에이전트"
    try:
        render_saved_plans(project_id)
    except Exception:
        st.error("저장한 계획을 열지 못했습니다. 저장 공간과 폴더 접근 권한을 확인해 주세요.")
    try:
        if day5_mode:
            from travel_agent.integration import current_state, plan_markdown, plan_execution
            from schedule_view import render_schedule
            st.caption("대화로 하루 계획을 만들고 방문 수·시간·제외 장소를 바꿀 수 있습니다. 예: 모레 경주에서 박물관 중심으로 하루 계획을 만들어 줘.")
            st.caption("1박 2일 이상은 ‘기존 여행 챗봇’의 여행 조건·계획 만들기를 사용하세요.")
            state = current_state(st.session_state.messages)
            if state.get("plan"):
                with st.expander("현재 여행 계획 · 대화 수정 반영", expanded=False):
                    current_plan = state["plan"]
                    cond = current_plan["basis"]["cond"]
                    st.write(f"**{cond['region']} · {cond['date']} · {len(current_plan['visits'])}곳**")
                    preview = {}
                    plan_execution(current_plan, preview)
                    render_schedule(preview["trip_schedule"])
                    if current_plan["basis"]["check"]["notes"]:
                        st.warning("확인 필요: " + " · ".join(current_plan["basis"]["check"]["notes"]))
                    with st.expander("상세 보기 · 날씨·준비물", expanded=False):
                        st.markdown(plan_markdown(current_plan, state.get("basis_lookups", [])))
            guided_question, travel_context = None, None
        else:
            guided_question, travel_context = render_planner(project_id)
    except Exception:
        st.error("여행 계획 화면을 열지 못했습니다. 저장 공간과 여행 조건을 확인해 주세요.")
        st.stop()
chat_panel = st.container(height=560, border=False, key="conversation", autoscroll=True)
if not st.session_state.messages:
    with chat_panel:
        render_welcome()
with st.form("travel_question", clear_on_submit=True):
    question = st.text_input(
        "여행 질문",
        placeholder="메시지를 입력하고 Enter를 누르세요.",
        key="question",
    )
    submitted = st.form_submit_button("답변 받기", type="primary")

with st.sidebar:
    st.button("새 대화", on_click=new_conversation, args=(project_id,), use_container_width=True)
if guided_question:
    question = guided_question
    submitted = True
if submitted:
    if not question.strip():
        st.warning("여행 질문을 입력해 주세요.")
    else:
        execution = {}
        answer_failed = True
        try:
            with st.spinner("여행 답변을 준비하고 있습니다..."):
                history = [message.copy() for message in st.session_state.messages]
                lookup_records = []
                context_args = {"travel_context": travel_context} if travel_context else {}
                if day5_mode:
                    from travel_agent.integration import answer_question as answer_day5
                    answer = invoke_traced(answer_day5, mode="5일차 계획 에이전트", question=question,
                                           history=history, lookup_records=lookup_records, execution_trace=execution)
                else:
                    answer = invoke_traced(answer_question, mode="기존 여행 챗봇", question=question,
                                           history=history, lookup_records=lookup_records, execution_trace=execution, **context_args)
        except MissingAPIKeyError:
            st.error(
                ".env 파일의 OPENAI_API_KEY 값이 비어 있습니다. "
                "발급받은 OpenAI API 키를 입력하고 파일을 저장한 뒤 다시 시도해 주세요."
            )
        except Exception:
            # 예외 원문에는 인증 정보가 포함될 수 있으므로 출력하거나 기록하지 않습니다.
            st.error(
                "답변을 생성하지 못했습니다. .env의 OPENAI_API_KEY, "
                "모델 접근 권한, API 사용 한도와 네트워크 연결을 확인해 주세요."
            )
        else:
            answer_failed = False
            # 성공한 질문·답변 쌍만 기록합니다. 오류 안내는 다음 API 요청에 넣지 않습니다.
            st.session_state.messages = history + [
                {"role": "user", "content": question.strip()},
                {"role": "assistant", "content": answer, "lookups": lookup_records,
                 **({"execution": execution} if execution else {})},
            ]
            try:
                storage.append_turn(conversation_id, question, answer, lookups=lookup_records, execution=execution)
                storage.save_execution(conversation_id, question, answer, execution)
                if (travel_context or {}).get("plan_request") and execution.get("proposed_places"):
                    storage.save_plan(project_id, selected_places=execution["proposed_places"])
                    st.session_state[f"pending_visit_order_{project_id}"] = [p["key"] for p in execution["proposed_places"]]
            except Exception:
                st.error("답변은 생성됐지만 대화를 저장하지 못했습니다. 저장 공간과 폴더 접근 권한을 확인해 주세요.")
            else:
                st.rerun()
        # 최종 모델 호출 실패도 예외 원문 없이 별도 실행 기록에 보관합니다.
        if answer_failed and execution.get("ended_at"):
            failure_message = "답변을 생성하지 못했습니다. 키 설정, 모델 권한, API 한도와 연결을 확인해 주세요."
            try:
                storage.save_execution(conversation_id, question, failure_message, execution)
            except Exception:
                st.warning("실패한 실행 기록을 저장하지 못했습니다. 저장 공간을 확인해 주세요.")
            with st.expander("실패한 실행 기록", expanded=True):
                render_trace(execution, download_key=f"failed_{execution['run_id']}")

try:
    with st.sidebar:
        render_comparison(storage.list_executions(project_id))
except Exception:
    st.warning("실행 기록 비교를 열지 못했습니다. 저장 공간과 기록 형식을 확인해 주세요.")

# 저장된 시간 순서대로 표시합니다. 새 메시지는 대화창 아래로 이어집니다.
from message_map import message_places
latest_map_index = next((i for i in range(len(st.session_state.messages) - 1, -1, -1)
                         if st.session_state.messages[i]["role"] == "assistant"
                         and message_places(st.session_state.messages[i])[0]), None)
with chat_panel:
    for index, message in enumerate(st.session_state.messages):
        role = message["role"]
        with st.container(key=f"chat_{role}_{index}"):
            with st.chat_message(role, width="content" if role == "user" else "stretch"):
                if role == "assistant":
                    render_answer(message, key_prefix=f"chat_{conversation_id}_{index}_", show_map=index == latest_map_index)
                    render_save_plan(project_id, message)
                else:
                    st.markdown(message["content"])

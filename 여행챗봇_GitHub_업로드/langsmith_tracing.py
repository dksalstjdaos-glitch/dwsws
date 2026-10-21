"""두 대화 모드에서 공통으로 쓰는 LangSmith 설정·추적·연결 확인."""
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID, uuid4

import httpx
from dotenv import dotenv_values
from langsmith import Client, trace, tracing_context
from config import ENV_PATH

ENDPOINTS = {"https://api.smith.langchain.com", "https://eu.api.smith.langchain.com"}
_active = ContextVar("travel_langsmith_session", default=None)


class TracingConfigError(ValueError):
    pass


@dataclass(frozen=True)
class TracingSettings:
    enabled: bool = False
    api_key: str = field(default="", repr=False)
    project: str = "travel-chatbot"
    endpoint: str = "https://api.smith.langchain.com"
    workspace_id: str = ""


def load_tracing_settings(values=None):
    values = values if values is not None else dotenv_values(ENV_PATH, encoding="utf-8-sig", interpolate=False)
    enabled = str(values.get("LANGSMITH_TRACING") or "").strip().lower() == "true"
    key = (values.get("LANGSMITH_API_KEY") or "").strip()
    endpoint = (values.get("LANGSMITH_ENDPOINT") or "https://api.smith.langchain.com").strip().rstrip("/")
    if endpoint not in ENDPOINTS:
        raise TracingConfigError("LANGSMITH_ENDPOINT는 공식 미국 또는 유럽 리전 주소로 설정해 주세요.")
    if enabled and not key:
        raise TracingConfigError("LANGSMITH_TRACING=true이지만 LANGSMITH_API_KEY가 비어 있습니다.")
    workspace = (values.get("LANGSMITH_WORKSPACE_ID") or "").strip()
    if workspace:
        try:
            workspace = str(UUID(workspace))
        except ValueError:
            raise TracingConfigError("LANGSMITH_WORKSPACE_ID 형식을 확인해 주세요.") from None
    return TracingSettings(enabled, key, (values.get("LANGSMITH_PROJECT") or "travel-chatbot").strip(), endpoint, workspace)


def redact(value):
    """실수로 입력된 인증 필드·일반적인 API 키 형식을 추적에서 가립니다."""
    if isinstance(value, dict):
        return {k: "[비공개]" if any(word in str(k).lower() for word in
                ("api_key", "apikey", "authorization", "access_token", "refresh_token", "client_secret"))
                else redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return re.sub(r"\b(?:sk-[A-Za-z0-9_-]{8,}|lsv2_[A-Za-z0-9_-]{8,})", "[비공개]", value)
    return value


@contextmanager
def tracing_session(values=None):
    active = _active.get()
    if active is not None:
        yield active
        return
    state = {"enabled": False, "error": "", "client": None, "project": ""}
    try:
        settings = load_tracing_settings(values)
        state["project"] = settings.project
        if settings.enabled:
            state["client"] = Client(api_key=settings.api_key, api_url=settings.endpoint,
                workspace_id=settings.workspace_id or None, timeout_ms=(5000, 5000),
                hide_inputs=redact, hide_outputs=redact, hide_metadata=redact,
                omit_traced_runtime_info=True,
                tracing_error_callback=lambda error: state.update(error="추적 전송 오류 · 키·리전·권한·네트워크를 확인해 주세요."))
            state["enabled"] = True
    except TracingConfigError as error:
        state["error"] = str(error)
    except Exception:
        state["error"] = "LangSmith 추적을 준비하지 못했습니다. 설정을 확인해 주세요."
    token = _active.set(state)
    try:
        with tracing_context(enabled=state["enabled"], client=state["client"], project_name=state["project"] or None):
            yield state
    finally:
        _active.reset(token)
        if state["client"]:
            try:
                state["client"].flush(timeout=5)
                state["client"].close(timeout=5)
            except Exception:
                state["error"] = "추적 전송 마무리를 확인하지 못했습니다. LangSmith 프로젝트에서 실행 기록을 확인해 주세요."


def invoke_traced(function, *, mode, question, **kwargs):
    execution = kwargs.get("execution_trace")
    failure = None
    with tracing_session() as state:
        if not state["enabled"]:
            answer = function(question, **kwargs)
        else:
            with trace("여행 챗봇 · " + mode, run_type="chain", inputs={"question": question},
                       metadata={"mode": mode}, tags=["travel-chatbot"],
                       client=state["client"], project_name=state["project"]) as root:
                try:
                    answer = function(question, **kwargs)
                except Exception as error:
                    failure = error
                    root.end(error="답변 생성 실패 · API 설정과 연결 확인 필요")
                else:
                    root.end(outputs={"answer": answer, "tool_records": kwargs.get("lookup_records", []),
                                      "execution": execution or {}})
                if execution is not None:
                    execution["langsmith_trace_id"] = str(root.id)
    if execution is not None and (execution or state["enabled"] or state["error"]):
        execution["langsmith_enabled"] = state["enabled"]
        execution["langsmith_project"] = state["project"]
        if state["error"]:
            execution["langsmith_error"] = state["error"]
    if failure is not None:
        raise failure
    return answer


def test_connection(settings):
    """대화 내용 없이 고정 테스트 추적 1건을 동기로 전송해 쓰기 권한을 확인합니다."""
    if not settings.api_key:
        return {"ok": False, "message": ".env의 LANGSMITH_API_KEY를 먼저 입력해 주세요."}
    run_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()
    headers = {"x-api-key": settings.api_key}
    if settings.workspace_id:
        headers["X-Tenant-Id"] = settings.workspace_id
    body = {"id": run_id, "name": "여행 챗봇 · 연결 테스트", "run_type": "chain",
            "inputs": {"test": "langsmith_connection"}, "outputs": {"test": "completed"},
            "start_time": now, "end_time": now, "session_name": settings.project}
    try:
        response = httpx.post(settings.endpoint + "/runs", headers=headers, json=body, timeout=10, follow_redirects=False)
    except Exception:
        return {"ok": False, "message": "LangSmith 서버에 연결하지 못했습니다. 네트워크와 리전 설정을 확인해 주세요."}
    if 200 <= response.status_code < 300:
        return {"ok": True, "message": "테스트 추적 전송 성공 · LangSmith 프로젝트에서 ‘여행 챗봇 · 연결 테스트’를 확인하세요.", "run_id": run_id}
    if response.status_code in (401, 403):
        message = "인증 또는 권한 오류입니다. 키·리전·워크스페이스와 프로젝트 생성/추적 쓰기 권한을 확인해 주세요."
    elif response.status_code == 429:
        message = "LangSmith 사용 한도에 도달했습니다. 잠시 후 다시 확인해 주세요."
    else:
        message = "테스트 추적 전송에 실패했습니다. 프로젝트·리전·서비스 상태를 확인해 주세요."
    return {"ok": False, "message": message}

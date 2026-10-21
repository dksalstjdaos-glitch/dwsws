"""외부에서 확인 가능한 실행 사실만 기록합니다. 프롬프트·내부 생각·인증 값은 저장하지 않습니다."""

from datetime import datetime, timezone
from time import perf_counter
from uuid import uuid4


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class ExecutionTrace:
    def __init__(self, output=None):
        self.data = output if output is not None else {}
        self.data.clear()
        self.started = perf_counter()
        self.pending_tool = False
        self.pending_decisions = []
        self.data.update(run_id=uuid4().hex, started_at=now(), ended_at=None,
                         tool_calls=0, model_calls=0, limits={"tools": 4, "models": 6},
                         events=[], end_reason="", answer_basis=[])

    def event(self, event, **details):
        self.data["events"].append({"step": len(self.data["events"]) + 1,
                                    "at": now(), "event": event, **details})

    def model_call(self, purpose):
        self.data["model_calls"] += 1
        self.event("model_call", purpose=purpose, count=self.data["model_calls"])

    def tool_call(self, tool, arguments):
        self.data["tool_calls"] += 1
        self.pending_tool = True
        self.event("tool_call", tool=tool, arguments=arguments)

    def tool_return(self, record):
        places = record.get("places", [])
        self.event("tool_return" if self.pending_tool else "tool_skipped", tool=record.get("tool"), status=record.get("status", "unverified"),
                   result_count=len(places) if record.get("kind") in ("places", "region") else None,
                   found=[{"name": p.get("name", ""), "address": p.get("address", ""),
                           "url": p.get("url", "")} for p in places],
                   forecast_locations=[{"name": f.get("name", ""), "latitude": f.get("latitude"), "longitude": f.get("longitude"),
                                        "dates": [d["date"] for d in f.get("days", [])]} for f in record.get("forecasts", [])],
                   error_code=record.get("error_code", ""))
        self.pending_tool = False
        for action, reason in self.pending_decisions:
            self.event("decision", action=action, reason=reason)
        self.pending_decisions.clear()

    def decision(self, action, reason):
        if self.pending_tool:
            self.pending_decisions.append((action, reason))
        else:
            self.event("decision", action=action, reason=reason)

    def finish(self, reason=None):
        self.data["end_reason"] = reason or self.data["end_reason"] or "답변 완료"
        self.data["ended_at"] = now()
        self.data["elapsed_ms"] = round((perf_counter() - self.started) * 1000)
        if self.pending_tool:
            self.event("tool_return", status="failed", error_code="unexpected_failure")
        self.event("finished", reason=self.data["end_reason"])


def verified_place_answer(places):
    return "검색으로 확인한 장소입니다.\n\n" + "\n\n".join(
        f"- **{p.get('name', '')}** · {p.get('address', '')}" +
        (f" · [카카오맵]({p['url']})" if p.get("url", "").startswith(("https://place.map.kakao.com/", "http://place.map.kakao.com/")) else "")
        for p in places
    )

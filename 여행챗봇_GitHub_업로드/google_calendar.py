"""로컬 앱용 Google OAuth와 여행 계획 등록. 인증 자료는 세션 메모리에만 둡니다."""

import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

CLIENT_FILE = Path(__file__).resolve().parent / ".data" / "google_oauth_client.json"
SCOPES = ["https://www.googleapis.com/auth/calendar.events.owned"]
EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
KST = timezone(timedelta(hours=9))


class CalendarError(ValueError):
    """사용자에게 표시해도 되는 고정 안내문만 담습니다."""


def client_config():
    try:
        config = json.loads(CLIENT_FILE.read_text(encoding="utf-8-sig"))
        installed = config["installed"]
        if not installed.get("client_id") or not installed.get("client_secret"):
            raise ValueError
        # 인증 설정 파일로 외부 임의 서버에 비밀값을 보내지 않습니다.
        if installed.get("auth_uri") != "https://accounts.google.com/o/oauth2/auth":
            raise ValueError
        if installed.get("token_uri") != "https://oauth2.googleapis.com/token":
            raise ValueError
        return config
    except Exception:
        raise CalendarError("구글 OAuth 설정 파일이 없거나 올바르지 않습니다. 설정 안내를 확인해 주세요.") from None


def connect_google():
    from google_auth_oauthlib.flow import InstalledAppFlow
    import logging
    # OAuth 리다이렉트 주소·토큰이 라이브러리 로그에 포함되지 않도록 제한합니다.
    for name in ("google_auth_oauthlib.flow", "requests_oauthlib.oauth2_session", "oauthlib.oauth2.rfc6749.clients.base"):
        logging.getLogger(name).setLevel(logging.WARNING)
    class BoundedFlow(InstalledAppFlow):
        def fetch_token(self, **kwargs):
            kwargs.setdefault("timeout", 15)
            return super().fetch_token(**kwargs)
    config = client_config()
    try:
        flow = BoundedFlow.from_client_config(config, SCOPES, autogenerate_code_verifier=True)
        return flow.run_local_server(
            host="127.0.0.1", port=0, open_browser=True, timeout_seconds=120,
            authorization_prompt_message="", success_message="구글 계정 연결이 완료되었습니다. 여행 챗봇으로 돌아가 주세요.",
            prompt="select_account consent", access_type="online",
        )
    except Exception:
        raise CalendarError("구글 계정 연결이 완료되지 않았습니다. 로그인·권한 승인 또는 연결 시간을 확인하고 다시 연결해 주세요.") from None


def calendar_events(plan, duration_minutes=90):
    if not isinstance(duration_minutes, int) or not 15 <= duration_minutes <= 240:
        raise CalendarError("방문 시간을 15~240분으로 설정해 주세요.")
    execution = plan["snapshot"].get("execution") or {}
    events = []
    for index, row in enumerate(execution.get("trip_schedule", [])):
        place = row.get("place")
        if not place:
            continue
        try:
            day = date.fromisoformat(row["date"])
            label = row["time"]
            matched = re.search(r"(\d{1,2})\s*:\s*(\d{2})", label)
            if not matched:
                raise ValueError
            hour, minute = map(int, matched.groups())
            if "오후" in label and 1 <= hour <= 11:
                hour += 12
            if "오전" in label and hour == 12:
                hour = 0
            start = datetime(day.year, day.month, day.day, hour, minute, tzinfo=KST)
            end = (datetime.fromisoformat(f"{row['date']}T{row['end_time']}:00").replace(tzinfo=KST)
                   if row.get("end_time") else start + timedelta(minutes=duration_minutes))
            if end <= start:
                raise ValueError
            name = place["name"].strip()
            if not name:
                raise ValueError
        except Exception:
            raise CalendarError("계획에 날짜·시간·장소가 불분명한 항목이 있습니다. 계획표를 다시 확인해 주세요.") from None
        source = execution.get("run_id") or plan["source_run_id"]
        event_id = hashlib.sha256(f"travel:{source}:{index}".encode()).hexdigest()
        description = (f"{plan['title']}\n{row.get('reason', '')}\n"
                       "여행 챗봇의 제안 일정입니다. 운영시간·휴무·이동시간은 별도 확인이 필요합니다.")
        url = place.get("url", "")
        if url.startswith(("https://place.map.kakao.com/", "http://place.map.kakao.com/")):
            description += "\n장소 지도: " + url
        events.append({"id": event_id, "summary": f"{name} · {plan['title']}",
                       "location": place.get("address", ""), "description": description,
                       "start": {"dateTime": start.isoformat(), "timeZone": "Asia/Seoul"},
                       "end": {"dateTime": end.isoformat(), "timeZone": "Asia/Seoul"},
                       "extendedProperties": {"private": {"travel_source": event_id}}})
    if not events:
        raise CalendarError("등록할 방문 일정이 없습니다.")
    ordered = sorted(events, key=lambda e: e["start"]["dateTime"])
    for before, after in zip(ordered, ordered[1:]):
        if before["end"]["dateTime"] > after["start"]["dateTime"]:
            raise CalendarError("방문 시간이 다음 일정과 겹칩니다. 방문 시간을 줄여 주세요.")
    return events


def _same_event(existing, proposed):
    try:
        return (existing.get("status") != "cancelled"
                and existing.get("extendedProperties", {}).get("private", {}).get("travel_source") == proposed["id"]
                and all(datetime.fromisoformat(existing[key]["dateTime"].replace("Z", "+00:00"))
                        == datetime.fromisoformat(proposed[key]["dateTime"]) for key in ("start", "end"))
                and existing.get("summary") == proposed["summary"]
                and existing.get("location", "") == proposed.get("location", ""))
    except (KeyError, ValueError, TypeError):
        return False


def register_events(credentials, events, session=None):
    """성공한 항목을 보존하고 첫 실패에서 중단. 재시도는 서버의 동일 ID를 확인합니다."""
    result = {"created": 0, "existing": 0, "links": [], "error": ""}
    own_session = session is None
    try:
        if session is None:
            from google.auth.transport.requests import AuthorizedSession
            session = AuthorizedSession(credentials, max_refresh_attempts=0, refresh_timeout=15)
        for event in events:
            response = session.get(f"{EVENTS_URL}/{event['id']}", timeout=15)
            if response.status_code == 404:
                response = session.post(EVENTS_URL, json=event, timeout=15)
                if response.status_code == 409:
                    response = session.get(f"{EVENTS_URL}/{event['id']}", timeout=15)
                    if response.status_code == 200 and _same_event(response.json(), event):
                        result["existing"] += 1
                    else:
                        raise CalendarError("동일 일정의 변경·삭제 이력이 확인됐습니다. 구글 캘린더에서 확인해 주세요.")
                elif response.status_code in (200, 201):
                    result["created"] += 1
                else:
                    _raise_status(response.status_code)
            elif response.status_code == 200:
                if not _same_event(response.json(), event):
                    raise CalendarError("이미 등록된 일정이 변경되었거나 시간이 다릅니다. 구글 캘린더에서 확인해 주세요.")
                result["existing"] += 1
            else:
                _raise_status(response.status_code)
            link = response.json().get("htmlLink", "")
            if link.startswith("https://www.google.com/calendar/") or link.startswith("https://calendar.google.com/"):
                result["links"].append(link)
    except CalendarError as error:
        result["error"] = str(error)
    except Exception:
        result["error"] = "연결 또는 인증 문제로 등록을 중단했습니다. 연결을 확인해 주세요. 재시도하면 동일 ID로 중복 여부를 확인합니다."
    finally:
        if own_session and session is not None:
            session.close()
    return result


def _raise_status(status):
    if status in (401, 403):
        raise CalendarError("구글 인증·권한 또는 API 설정 문제로 중단했습니다. 계정을 다시 연결하고 Calendar API 사용 설정을 확인해 주세요.")
    if status == 429:
        raise CalendarError("구글 API 사용 한도에 도달해 중단했습니다. 잠시 후 다시 시도해 주세요.")
    raise CalendarError("구글 캘린더 요청에 실패해 중단했습니다. 연결과 캘린더 상태를 확인해 주세요.")

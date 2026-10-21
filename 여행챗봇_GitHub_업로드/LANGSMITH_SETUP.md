# LangSmith 연결 방법

## 1. 키 설정

프로젝트의 실제 `.env` 파일에 다음 값을 추가하거나 기존 항목을 수정합니다.
OpenAI·카카오 키는 그대로 유지하고 같은 변수 이름을 중복으로 넣지 마세요.

```dotenv
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=발급받은_LangSmith_API_키
LANGSMITH_PROJECT=travel-chatbot
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
```

유럽 리전 계정이면 endpoint를 `https://eu.api.smith.langchain.com`으로 바꿉니다.
여러 워크스페이스를 사용하는 조직 키·추적 키라면 선택할 워크스페이스의 UUID도 설정합니다.

```dotenv
LANGSMITH_WORKSPACE_ID=선택할_워크스페이스_UUID
```

워크스페이스 지정이 필요하지 않으면 이 항목은 비워 둡니다.
실제 키를 채팅·소스 코드·`.env.example`에 넣지 마세요. `.env`는 기존 `.gitignore`로 제외됩니다.

## 2. 실행과 연결 확인

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\start_chatbot.ps1
```

이미 실행 중인 앱은 새로고침합니다. 설정 값은 요청할 때 `.env`에서 다시 읽습니다.
프로젝트를 연 뒤 **사이드바 → 여행 설정 → LangSmith 추적 연결**을 펼칩니다.
**추적: 켜짐 / API 키: 설정됨 / 프로젝트: travel-chatbot**인지 확인하고 **LangSmith 연결 테스트**를 누릅니다.
버튼은 실제 대화 대신 `여행 챗봇 · 연결 테스트`라는 고정 테스트 실행 1건을 전송합니다.
전송 성공 후 LangSmith에서 프로젝트 이름을 찾아 해당 실행을 확인합니다.
SDK 설정만 되어 있다는 사실과 실제 추적 전송 성공은 구분합니다.

추적 전용 키는 저장된 실행을 읽을 권한이 없을 수 있으므로 연결 테스트는 읽기 API 대신 추적 쓰기로 확인합니다.
처음 쓰는 프로젝트를 만들 권한이 없다면 사용할 수 있는 기존 프로젝트 이름을 지정합니다.

## 3. 챗봇 추적 확인

기존 여행 챗봇과 5일차 계획 에이전트 모두 앱의 공통 추적 경계를 사용합니다.
예: `모레 경주에서 박물관 중심으로 하루 계획 만들어 줘`를 질문합니다.
LangSmith에서 `여행 챗봇 · 기존 여행 챗봇` 또는 `여행 챗봇 · 5일차 계획 에이전트` 실행을 확인합니다.
질문·답변과 실행 시간, 하위 LangChain 모델 호출을 볼 수 있습니다.
5일차 모드의 도구 호출은 하위 실행으로, 기존 모드의 직접 API 조회는 루트 출력의 `tool_records`와 `execution`으로 확인합니다.
앱 답변의 실행 기록에는 LangSmith 추적 요청 ID와 프로젝트가 표시됩니다.
추적 요청 ID가 있다는 것만으로 원격 저장을 확인한 것은 아니므로 실제 프로젝트에서 확인하세요.

추적을 켜면 대화와 도구 조회 결과가 외부 LangSmith로 전송됩니다.
인증 필드는 추적에서 가리고, API 키는 화면·대화 DB에 저장하지 않습니다.
추적 오류가 발생해도 여행 답변은 계속 제공하며 안전한 오류 안내를 기록합니다.

## 오류 확인

- 인증·권한 오류: 키, 키 만료, 계정 리전, 워크스페이스 지정, 프로젝트 쓰기 권한을 확인합니다.
- 기록이 안 보임: 프로젝트 이름·시간 필터·워크스페이스를 확인합니다. 기존 실행은 소급해서 전송하지 않습니다.
- 전송 중단: 네트워크와 사용 한도를 확인합니다. 오류 응답 원문이나 실제 키는 화면에 출력하지 않습니다.
- 추적 중지: `.env`의 `LANGSMITH_TRACING=false`로 바꾸고 새로고침합니다.

공식 자료: [환경변수 없이 추적 설정](https://docs.langchain.com/langsmith/trace-without-env-vars),
[API 키와 워크스페이스 권한](https://docs.langchain.com/langsmith/create-account-api-key).

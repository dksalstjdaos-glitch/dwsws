# GitHub 업로드 안내

이 폴더의 내용만 저장소 루트에 올리세요. 상위 원본 폴더 전체를 올리지 마세요.

## 포함된 파일

앱 소스 코드, 테스트, 실행 스크립트, 의존성 목록, 설명 문서, 테마 설정과 빈 `.env.example`입니다.
테스트의 예시 키는 실제 인증 정보가 아닌 테스트용 값입니다.

## 제외한 파일

- 실제 `.env`와 그 변형·백업: API 키
- `.data/`: 대화 내역, 프로젝트, 저장한 일정, 인증 관련 데이터
- Google OAuth 클라이언트 파일과 토큰, `.streamlit/secrets.toml`
- `.venv/`, `__pycache__/`, `.tmp/`: 가상환경, 캐시, 로그, 화면 캡처, 임시 테스트 결과

## 업로드

GitHub 웹에서 업로드할 경우 `.gitignore`, `.env.example`, `.python-version`, `.streamlit/config.toml`도 포함하세요.
명령어로 올릴 경우 이 폴더에서 다음을 실행하세요.

```powershell
git init
git add .
git status --short
git commit -m "여행 챗봇 초기 코드"
git branch -M main
# GitHub에서 빈 저장소를 만든 뒤 아래 주소를 실제 저장소 주소로 바꾸세요.
git remote add origin https://github.com/계정명/저장소명.git
git push -u origin main
```

GitHub에 소스를 올리는 것만으로 챗봇 서버가 실행되지는 않습니다. 실행 방법은 README.md를 참고하세요.
다른 컴퓨터에서는 `.env.example`을 `.env`로 복사하고 실제 키를 로컬에서만 입력하세요.
추후 배포 서비스에 연결하면 해당 서비스의 비밀 설정에 키를 등록하고, 소스 파일에는 넣지 마세요.

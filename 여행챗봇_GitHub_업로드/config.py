"""모델 설정과 프로젝트 .env 로딩."""

from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

MODEL_NAME = "gpt-4o-mini"
REQUEST_TIMEOUT_SECONDS = 45
ENV_PATH = Path(__file__).resolve().parent / ".env"


class MissingAPIKeyError(ValueError):
    """API 키가 설정되지 않았을 때 사용하는 안전한 오류."""


@dataclass(frozen=True)
class Settings:
    api_key: str = field(repr=False)
    model_name: str = MODEL_NAME
    kakao_api_key: str = field(default="", repr=False)


def load_settings() -> Settings:
    # 실행 위치나 시스템 환경변수 대신 이 프로젝트의 .env를 읽습니다.
    values = dotenv_values(ENV_PATH, encoding="utf-8-sig", interpolate=False)
    api_key = (values.get("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise MissingAPIKeyError(".env 파일에 OPENAI_API_KEY를 설정해 주세요.")
    kakao_api_key = (values.get("KAKAO_REST_API_KEY") or "").strip()
    return Settings(api_key=api_key, kakao_api_key=kakao_api_key)

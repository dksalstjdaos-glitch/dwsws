"""파랑·회색·흰색으로 구성한 대화 중심 화면."""
import streamlit as st


def apply_styles():
    st.html("""
<style>
:root { --travel-blue: #0F3192; --travel-gray: #7F7F7F; }
[data-testid="stAppDeployButton"], .stAppDeployButton, #MainMenu, [data-testid="stMainMenu"] { display: none !important; }
.stApp { background: #fff; }
.stMainBlockContainer { max-width: 1080px; padding-top: calc(4.5rem + env(safe-area-inset-top, 0px)); padding-bottom: 2rem; }
[data-testid="stSidebar"] { background: #f6f7fa; border-right: 1px solid #0f31921a; }
h1, h2, h3 { color: #0F3192 !important; }
[data-testid="stCaptionContainer"] { color: #7F7F7F; }
.travel-intro { margin-bottom: 1.2rem; }
.travel-intro .eyebrow { color: #7F7F7F; font-size: 0.75rem; letter-spacing: 0.12em; margin: 0 0 0.4rem; }
.travel-intro h1 { color: #0F3192; font-size: clamp(1.8rem, 4vw, 2.5rem); font-weight: 800; margin: 0; padding: 0; line-height: 1.3; }
.travel-intro .description { color: #7F7F7F; font-size: 1rem; line-height: 1.7; margin: 0.55rem 0 1rem; }
.travel-examples { display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center; }
.travel-examples .label { color: #7F7F7F; font-size: 0.8rem; margin-right: 0.2rem; }
.travel-examples .example { color: #0F3192; background: #0f319208; border: 1px solid #0f31921a; border-radius: 999px; padding: 0.4rem 0.8rem; font-size: 0.82rem; }
.st-key-conversation { background: #f5f7fc; border: 1px solid #0f31921a; border-radius: 22px; padding: 1rem; }
.st-key-conversation [data-testid="stChatMessage"] {
    background: #fff; color: #0F3192; border: 1px solid #0f319212;
    border-radius: 4px 18px 18px 18px; padding: 1rem; max-width: 92%;
    margin: 0.35rem 0 0.8rem; box-shadow: 0 2px 6px #0f319208; min-width: 0;
}
.st-key-conversation [class*="st-key-chat_user_"] { align-items: flex-end; }
.st-key-conversation [class*="st-key-chat_assistant_"] { align-items: flex-start; }
.st-key-conversation [class*="st-key-chat_user_"] > [data-testid="stLayoutWrapper"] { max-width: 78%; }
.st-key-conversation [class*="st-key-chat_user_"] [data-testid="stChatMessage"] {
    background: #0F3192; color: #fff; border-radius: 18px 4px 18px 18px; margin-left: 0; max-width: 100%;
}
.st-key-conversation [class*="st-key-chat_user_"] [data-testid="stChatMessageAvatarUser"] { display: none; }
.st-key-conversation [class*="st-key-chat_user_"] [data-testid="stMarkdownContainer"] { color: #fff; }
.st-key-conversation [class*="st-key-chat_user_"] [data-testid="stMarkdownContainer"] * { color: inherit; }
.st-key-conversation [data-testid="stChatMessageContent"] { min-width: 0; overflow-wrap: anywhere; }
.st-key-conversation [data-testid="stChatMessageAvatarAssistant"] { background: #0F3192; color: #fff; }
.st-key-conversation [data-testid="stExpander"] { background: #fff; border-radius: 10px; color: #0F3192; }
[data-testid="stChatMessage"] p, [data-testid="stChatMessage"] li { line-height: 1.8; }
[data-testid="stChatMessage"] h3 { font-size: 1.15rem; margin-top: 0.8rem; }
[data-testid="stChatMessage"] table { width: 100%; font-size: 0.95rem; }
[data-testid="stChatMessage"] th { background: #0f319208; color: #0F3192; }
[data-testid="stChatMessage"] td, [data-testid="stChatMessage"] th { padding: 0.7rem; }
[data-testid="stBaseButton-primary"], [data-testid="stFormSubmitButton"] button[kind="primaryFormSubmit"] {
    background: #0F3192; border-color: #0F3192; color: #fff; border-radius: 10px;
}
[data-testid="stTextInput"] input:focus { border-color: #0F3192; }
.st-key-conversation + div { margin-top: 0.4rem; }
.welcome-hero { text-align: center; padding: 2.3rem 0.8rem 1.8rem; }
.welcome-symbol { display: inline-flex; align-items: center; justify-content: center; width: 56px; height: 56px; border-radius: 18px; background: #0F3192; color: white; font-size: 1.7rem; box-shadow: 0 8px 22px #0f31921a; margin-bottom: 1.1rem; }
.welcome-hero h2 { font-size: clamp(1.4rem, 3vw, 1.9rem); line-height: 1.4; padding: 0; margin: 0 0 0.6rem; }
.welcome-hero p { color: #7F7F7F; font-size: 0.95rem; line-height: 1.8; margin: 0; }
[class*="st-key-welcome_card_"] button {
    width: 100%; min-height: 145px; text-align: left; justify-content: flex-start; align-items: flex-start;
    padding: 1.15rem; background: #fff; color: #0F3192; border: 1px solid #0f31921f; border-radius: 16px;
    transition: border-color 0.15s, box-shadow 0.15s;
}
[class*="st-key-welcome_card_"] button p { white-space: pre-line; text-align: left; line-height: 1.8; font-size: 0.9rem; }
[class*="st-key-welcome_card_"] button strong { display: block; margin-bottom: 0.45rem; font-size: 0.95rem; }
[class*="st-key-welcome_card_"] button:hover { border-color: #0F3192; background: #fff; color: #0F3192; box-shadow: 0 5px 18px #0f319212; }
[class*="st-key-welcome_card_"] button:focus-visible { outline: 2px solid #0F3192; outline-offset: 3px; }
.welcome-footnote { text-align: center; color: #7F7F7F; margin-top: 1.2rem; font-size: 0.8rem; line-height: 1.7; }
.home-features { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem; margin: 0 0 1.5rem; }
.home-feature { background: #f5f7fc; border: 1px solid #0f31921a; border-radius: 16px; padding: 1.2rem; }
.home-feature strong { display: block; color: #0F3192; margin-bottom: 0.5rem; }
.home-feature p { color: #7F7F7F; font-size: 0.9rem; margin: 0; line-height: 1.7; }
.st-key-project_home_controls { border-radius: 18px; padding: 1.4rem; }
@media (max-width: 640px) {
    .home-features { grid-template-columns: 1fr; gap: 0.6rem; }
    .stMainBlockContainer { padding-top: 4rem; }
    .st-key-conversation { padding: 0.5rem; }
    .st-key-conversation [data-testid="stChatMessage"] { max-width: 100%; padding: 0.75rem; }
    .st-key-conversation [class*="st-key-chat_user_"] > [data-testid="stLayoutWrapper"] { max-width: 90%; }
    .travel-examples .example { border-radius: 12px; }
    .welcome-hero { padding-top: 1.4rem; }
    .st-key-welcome_cards [data-testid="stHorizontalBlock"] { flex-direction: column; }
    .st-key-welcome_cards [data-testid="stColumn"] { width: 100% !important; flex: 1 1 auto; }
    [class*="st-key-welcome_card_"] button { min-height: 95px; }
}
</style>
""")


def render_intro():
    st.html("""
<section class="travel-intro" aria-label="여행 챗봇 소개">
  <p class="eyebrow">나만의 여행을 함께</p>
  <h1>여행 플래너</h1>
  <p class="description">장소와 날씨를 확인하고, 나에게 맞는 여행 일정을 함께 만들어요.</p>
  <div class="travel-examples">
    <span class="label">입력 예시</span>
    <span class="example">모레 경주에서 박물관 중심으로 하루 계획 만들어 줘</span>
    <span class="example">방문은 두 곳만, 오후 1시부터 5시까지로 바꿔 줘</span>
  </div>
</section>
""")


def choose_welcome_prompt(prompt):
    st.session_state.question = prompt


def render_project_welcome():
    st.html("""
<section class="welcome-hero" aria-label="여행 프로젝트 시작 안내">
  <div class="welcome-symbol" aria-hidden="true">✦</div>
  <h2>새로운 여행, 여기서 시작해요</h2>
  <p>아래에서 프로젝트를 만들거나 저장된 여행을 이어가세요.<br>대화와 여행 계획을 프로젝트별로 모아둘 수 있어요.</p>
</section>
<div class="home-features">
  <div class="home-feature"><strong>가볼 만한 곳 찾기</strong><p>지역과 취향에 맞는 장소를 검색해요.</p></div>
  <div class="home-feature"><strong>날씨와 준비물 확인</strong><p>여행 날짜의 예보로 옷차림과 준비물을 살펴봐요.</p></div>
  <div class="home-feature"><strong>나만의 일정 만들기</strong><p>방문 순서와 시간대를 정하고 계획을 저장해요.</p></div>
</div>
""")


def render_welcome():
    st.html("""
<section class="welcome-hero" aria-label="대화 시작 안내">
  <div class="welcome-symbol" aria-hidden="true">✦</div>
  <h2>어떤 여행을 떠나고 싶으세요?</h2>
  <p>가고 싶은 곳부터 작은 취향까지, 편하게 이야기해 주세요.<br>장소 찾기부터 일정과 준비물까지 함께 정리해 드릴게요.</p>
</section>
""")
    with st.container(key="welcome_cards"):
        cards = [
            ("plan", "여행 계획 만들기", "모레 경주에서 박물관 중심으로 하루 계획 만들어 줘"),
            ("places", "가볼 만한 곳 찾기", "부산에서 비 오는 날 가기 좋은 실내 장소 추천해 줘"),
            ("weather", "날씨와 준비물 확인", "내일 서울 날씨와 옷차림, 챙길 준비물 알려줘"),
        ]
        for column, (key, title, prompt) in zip(st.columns(3), cards):
            with column:
                st.button(f"**{title}**\n\n{prompt}  →", key=f"welcome_card_{key}",
                          use_container_width=True, on_click=choose_welcome_prompt, args=(prompt,))
    st.html('<p class="welcome-footnote">카드를 누르면 아래 입력창에 질문이 채워집니다.<br>원하는 내용으로 바꾸고 Enter를 눌러 대화를 시작하세요.</p>')

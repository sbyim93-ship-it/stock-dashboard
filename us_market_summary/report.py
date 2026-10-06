"""
매일 새벽 5시(KST) GitHub Actions에서 실행 — 지수/섹터는 fetch_data.py로 API에서 정확한
수치를 미리 받아오고, 그 외(종목 뉴스/사유, 일정)는 웹서치로 조사해 텔레그램(HTML)으로
발송한다. 규칙 원본은 ~/.claude/commands/us_market_summary.md (대화형 /us_market_summary
스킬)와 같은 내용이며, 이 스크립트는 텔레그램 발송 + API 수치 주입에 맞춰 조정한 버전이다.
두 파일을 수정할 땐 같이 맞출 것.
"""

import os
import sys
import time

import anthropic
import requests

from fetch_data import build_data_block

MODEL = "claude-sonnet-5"
MAX_PAUSE_RESTARTS = 5
TELEGRAM_CHUNK_LIMIT = 3900

SYSTEM_PROMPT = """당신은 매일 아침 미국 주식시장 마감 요약을 정리해주는 금융 리포트 어시스턴트입니다.
프롬프트 맨 아래에 API로 미리 확인해둔 지수/시장지표/섹터 수치 블록이 첨부됩니다. 이 블록에
있는 수치는 그대로 신뢰하고 사용하세요 (재검색하거나 다른 값으로 바꾸지 말 것). 그 블록에
없는 정보(종목 뉴스/사유, 이번 주 일정 등)만 웹 검색으로 채우세요.

## 데이터 수집
- S&P 500, Nasdaq, Dow Jones, Russell 2000, 필라델피아 반도체지수(SOX) 종가/등락률,
  시장지표(10Y/30Y 국채금리, WTI/Brent 유가, 금, 비트코인), 섹터별(11개 SPDR 섹터 ETF
  기준) 및 업종별(Finviz 144개 업종 분류 기준 — 실패 시 20개 니치 테마 ETF로 대체) 당일
  등락률 — 아래 첨부된 API 확인 수치 블록을 그대로 사용. 만약 특정 항목이 블록에서
  "API 조회 실패"로 표시되어 있으면 그 항목만 웹 검색으로 보완 (추정치로 채우지 말 것)
- 당일 가장 큰 폭으로 움직인 종목과 구체적 사유(실적, 가이던스, 계약/파트너십, 애널리스트
  의견 변경 등) — 웹 검색 필수, 추정 금지. 그날의 매크로 테마(예: 유가 급등)와 직결된
  종목만 고르지 말 것 — 테마와 무관해도 두 자릿수 %급으로 움직인 종목이 있으면 그게 더
  중요한 기삿거리이니 우선 찾아서 포함할 것. 상승/하락 각각 먼저 "오늘 가장 많이
  움직인 개별 종목이 뭐지?"부터 검색하고, 부족하면 테마 연관 종목으로 보완
- 시총 상위 대형주(Magnificent 7 등) 주요 움직임
- 시장을 움직인 핵심 이슈
- 이번 주 및 다음 주 미국 주요 경제 일정(실적, 지표, Fed 일정, 휴장 포함)
- 검색으로 신뢰성 있게 확인이 안 되는 항목은 절대 추정치로 채우지 말 것 — 그 항목은
  생략하거나 "확인 불가"라고 명시할 것 (수치 날조 금지)

## 한국 증시 영향 코멘트 — 아래 종목이 ±5% 이상 움직였을 때만 추가
- Nvidia, AMD, Intel, Micron, TSMC → 삼성전자·SK하이닉스·한미반도체
- Synopsys, Cadence 등 반도체 설계(EDA) → 삼성전자(파운드리)·한미반도체
- Apple → LG이노텍·비에이치
- CoreWeave, Nebius, Meta(AI 인프라) → 삼성전자·SK하이닉스·이수페타시스
- Bloom Energy, GE Vernova(AI 전력) → 두산에너빌리티·LS ELECTRIC·HD현대일렉트릭
- Tesla → 삼성SDI·LG에너지솔루션·에코프로
- SpaceX → 한화에어로스페이스·쎄트렉아이
- 업종이 크게(±3% 이상) 움직이면 업종 섹션 아래에 한 줄 코멘트(표 아님)로 추가 — 업종명
  기준 매칭: Uranium/Utilities-Independent Power Producers→두산에너빌리티·한전기술,
  Solar/Utilities-Renewable→한화솔루션·OCI홀딩스, 2차전지/리튬 관련→에코프로·LG에너지솔루션,
  희토류/전략금속→성일하이텍, Robotics/Automation→레인보우로보틱스·두산로보틱스,
  Aerospace & Defense→한화에어로스페이스·LIG넥스원, Biotechnology→셀트리온·삼성바이오로직스,
  Semiconductor Equipment→한미반도체·피에스케이
- 매우 중요: 삼성전자·SK하이닉스 같은 한국 종목 자체를 "주요 하락/상승 종목" 표에
  미국 종목들과 나란히 별도 행으로 넣는 것은 절대 금지 (예: "Samsung Electronics ▼7.8%"를
  하락 종목 표에 넣는 것 금지). 한국 관련 소식은 반드시 관련된 미국 종목의 🇰🇷 한국 영향
  칸에서 코멘트로만 언급할 것 — 하락/상승 종목 표에는 미국 상장 종목만 올라간다.

## 출력 형식 — 텔레그램 parse_mode=HTML로 발송됨
- 실제 HTML <table> 태그는 지원 안 되니 쓰지 말 것. 그 외에는 평소처럼 자연스럽게 정리하면
  됨 — 정렬된 표 형태의 텍스트, 글머리 기호 등 가독성 좋은 방식을 자유롭게 섞어서 사용
- <b>...</b>(굵게), <i>...</i>(기울임), <a href="URL">텍스트</a>(출처 링크)는 사용 가능.
  그 외 HTML 태그는 쓰지 말 것
- 이모지 제목/구분은 그대로 사용 (📊 🔴 🟢 🔥 📅 💡)
- 아래 섹션 순서를 지킬 것:
  1. 📊 [날짜] 미국 증시 마감 요약 (제목)
  2. 지수 등락 (S&P500/Nasdaq/Dow/Russell2000/SOX — 종가, 등락률. 못 찾은 지수는 행 자체를
     생략하거나 "확인 불가"라고 명시할 것 — "하락"처럼 수치 없는 애매한 값은 쓰지 말 것)
  3. 시장 지표 (10Y/30Y 국채금리, WTI/Brent 유가, 금, 비트코인 — 첨부된 블록 그대로)
  4. 섹터 등락 상위/하위 (첨부된 11개 SPDR 섹터 ETF 등락률 기준 — 상위 3개와 하위 3개를
     항상 둘 다 보여줄 것. 11개가 전부 양수/음수인 날도 그중 가장 약한/강한 3개를 반대쪽
     자리에 넣는다 — "오늘은 하락 섹터가 없습니다"처럼 생략하지 말 것. 왜 그 섹터가
     움직였는지는 웹 검색으로 이유를 붙일 것)
  5. 업종 등락 상위/하위 (첨부된 Finviz 144개 업종 분류 기준 — GICS 섹터보다 훨씬
     세분화된 단위라 "에너지는 평범한데 우라늄만 급등" 같은 걸 잡아낼 수 있음. 상위
     3~4개와 하위 3~4개를 보여줄 것. 섹터와 겹치는 내용이면 섹터 섹션에서 다룬 걸
     반복하지 말고 업종 고유의 디테일만 짚을 것)
  6. 상승/하락 배경 2~3줄
  7. 🔴 주요 하락 종목 (종목명: 등락률 — 사유 / 한국영향 있으면 표시 — 두 자릿수 %급으로
     움직인 종목이 있으면 반드시 포함)
  8. 🟢 주요 상승 종목 (위와 동일 기준)
  9. 🔥 오늘의 주목 테마 (해당할 때만)
  10. 📅 이번 주 & 다음 주 주요 일정
  11. 💡 한 줄 핵심 요약 및 내일 주목할 포인트

리포트 본문만 출력하고, 서두/말미에 부가 설명이나 인사말을 달지 마세요."""

USER_PROMPT_TEMPLATE = """오늘 아침 발송할 미국 증시 마감 요약 리포트를 작성해줘.

{data_block}"""


def generate_report(client: anthropic.Anthropic) -> str:
    user_prompt = USER_PROMPT_TEMPLATE.format(data_block=build_data_block())
    messages = [{"role": "user", "content": user_prompt}]
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 25}]

    response = None
    for _ in range(MAX_PAUSE_RESTARTS):
        response = client.messages.create(
            model=MODEL,
            max_tokens=8000,
            system=SYSTEM_PROMPT,
            tools=tools,
            messages=messages,
        )
        if response.stop_reason != "pause_turn":
            break
        messages = [
            {"role": "user", "content": user_prompt},
            {"role": "assistant", "content": response.content},
        ]
    else:
        raise RuntimeError("web search kept pausing past max restarts")

    text = "\n".join(b.text for b in response.content if b.type == "text").strip()
    if not text:
        raise RuntimeError(f"empty report text, stop_reason={response.stop_reason}")
    return text


def chunk_text(text: str, limit: int) -> list[str]:
    chunks = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        split_at = remaining.rfind("\n\n", 0, limit)
        if split_at == -1:
            split_at = remaining.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = limit
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")
    return chunks


def send_telegram(token: str, chat_ids: list[str], text: str) -> None:
    chunks = chunk_text(text, TELEGRAM_CHUNK_LIMIT)
    failures = []
    for chat_id in chat_ids:
        for chunk in chunks:
            resp = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": chunk,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=15,
            )
            if not resp.ok:
                failures.append(f"{chat_id}: {resp.status_code} {resp.text}")
            time.sleep(0.5)
    if failures:
        raise RuntimeError("telegram send failed for: " + " | ".join(failures))


def main() -> None:
    api_key = os.environ["ANTHROPIC_API_KEY"]
    bot_token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_ids = [c.strip() for c in os.environ["TELEGRAM_CHAT_IDS"].split(",") if c.strip()]

    client = anthropic.Anthropic(api_key=api_key)
    report = generate_report(client)
    send_telegram(bot_token, chat_ids, report)
    print(f"sent to {len(chat_ids)} recipient(s), {len(report)} chars")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"FAILED: {e}", file=sys.stderr)
        sys.exit(1)

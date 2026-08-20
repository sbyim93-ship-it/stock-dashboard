"""
매일 새벽 5시(KST) GitHub Actions에서 실행 — 전날 미국 증시 마감을 웹서치로 조사해
텔레그램(HTML)으로 발송한다. 규칙 원본은 ~/.claude/commands/us_market_summary.md
(대화형 /us_market_summary 스킬)와 같은 내용이며, 이 스크립트는 텔레그램 발송에 맞춰
표 대신 글머리 기호 포맷을 쓰도록 조정한 버전이다. 두 파일을 수정할 땐 같이 맞출 것.
"""

import os
import sys
import time

import anthropic
import requests

MODEL = "claude-opus-5"
MAX_PAUSE_RESTARTS = 5
TELEGRAM_CHUNK_LIMIT = 3900

SYSTEM_PROMPT = """당신은 매일 아침 미국 주식시장 마감 요약을 정리해주는 금융 리포트 어시스턴트입니다.
웹 검색을 통해 가장 최근 미국 증시 마감(전 거래일) 데이터를 수집하고 아래 규칙에 따라
텔레그램 발송용 리포트를 작성하세요.

## 데이터 수집 (검색 필수, 추정 금지)
- S&P 500, Nasdaq, Dow Jones, Russell 2000, 필라델피아 반도체지수(SOX) 종가/등락률
  — 가능하면 서로 다른 소스 2곳 이상으로 교차 확인
- 섹터별 당일 등락률 상위/하위 5개 (Benzinga, CSIMarket 등)
- 당일 ±5% 이상 등락 종목과 구체적 사유(실적, 가이던스, 뉴스 등)
- 시총 상위 대형주(Magnificent 7 등) 주요 움직임
- 시장을 움직인 핵심 이슈
- 이번 주 및 다음 주 미국 주요 경제 일정(실적, 지표, Fed 일정, 휴장 포함)
- 검색으로 신뢰성 있게 확인이 안 되는 항목은 절대 추정치로 채우지 말 것 — 그 항목은
  생략하거나 "확인 불가"라고 명시할 것 (수치 날조 금지)

## 한국 증시 영향 코멘트 — 아래 종목이 ±5% 이상 움직였을 때만 추가
- Nvidia, AMD, Intel, Micron, TSMC → 삼성전자·SK하이닉스·한미반도체
- Apple → LG이노텍·비에이치
- CoreWeave, Nebius, Meta(AI 인프라) → 삼성전자·SK하이닉스·이수페타시스
- Bloom Energy, GE Vernova(AI 전력) → 두산에너빌리티·LS ELECTRIC·HD현대일렉트릭
- Tesla → 삼성SDI·LG에너지솔루션·에코프로
- SpaceX → 한화에어로스페이스·쎄트렉아이
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
  3. 섹터 등락 상위/하위
  4. 상승/하락 배경 2~3줄
  5. 🔴 주요 하락 종목 (종목명: 등락률 — 사유 / 한국영향 있으면 표시)
  6. 🟢 주요 상승 종목
  7. 🔥 오늘의 주목 테마 (해당할 때만)
  8. 📅 이번 주 & 다음 주 주요 일정
  9. 💡 한 줄 핵심 요약 및 내일 주목할 포인트

리포트 본문만 출력하고, 서두/말미에 부가 설명이나 인사말을 달지 마세요."""

USER_PROMPT = "오늘 아침 발송할 미국 증시 마감 요약 리포트를 작성해줘."


def generate_report(client: anthropic.Anthropic) -> str:
    messages = [{"role": "user", "content": USER_PROMPT}]
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
            {"role": "user", "content": USER_PROMPT},
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

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
이슈환전소 브리핑 자동 생성 스크립트
- 네이버 뉴스 검색 API로 최신 기사를 모으고
- Claude API로 '글로벌 동향 / 한국 연결 / 환전 포인트' 카드를 만들어
- briefing.json 으로 저장한다.

필요한 환경변수 (GitHub Actions의 Secrets로 주입):
  NAVER_CLIENT_ID
  NAVER_CLIENT_SECRET
  CLAUDE_API_KEY
"""

import os
import re
import json
import html
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone

# ── 검색할 카테고리 & 키워드 (자유롭게 추가/수정하세요) ──
QUERIES = [
    ("중동",     "이란 호르무즈"),
    ("중동",     "이스라엘 레바논"),
    ("미국",     "트럼프 관세"),
    ("미국",     "연준 금리"),
    ("한반도",   "코스피 환율"),
    ("한반도",   "북한"),
    ("중국",     "중국 반도체"),
    ("세계정치", "쿠팡 미국"),
]

REGION_KW = {
    "중동": ["이란", "이스라엘", "호르무즈", "레바논", "가자", "사우디", "카타르", "예멘", "후티"],
    "미국": ["미국", "트럼프", "연준", "워싱턴", "백악관", "나스닥", "관세", "상원", "하원"],
    "중국": ["중국", "시진핑", "베이징", "알리바바", "딥시크", "대만"],
    "한반도": ["한국", "북한", "코스피", "원화", "환율", "이재명", "김정은", "전작권", "쿠팡", "삼성", "sk하이닉스", "코스닥"],
    "러시아": ["러시아", "푸틴", "모스크바", "크렘린"],
    "우크라이나": ["우크라이나", "젤렌스키", "크림"],
    "유럽": ["유럽", "영국", "프랑스", "독일", "eu", "나토", "g7"],
}

NAVER_ID = os.environ.get("NAVER_CLIENT_ID", "")
NAVER_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "")
CLAUDE_KEY = os.environ.get("CLAUDE_API_KEY", "")

MAX_CARDS = 12          # 최종 발행할 카드 개수
PER_QUERY_DISPLAY = 6   # 검색어 하나당 가져올 기사 수


def strip_html(s: str) -> str:
    s = re.sub(r"<[^>]*>", "", s or "")
    return html.unescape(s).strip()


def detect_region(text: str) -> str:
    t = text.lower()
    best, best_n = "전 세계", 0
    for region, kws in REGION_KW.items():
        n = sum(1 for k in kws if k.lower() in t)
        if n > best_n:
            best, best_n = region, n
    return best


def naver_search(query: str, display: int = PER_QUERY_DISPLAY):
    url = "https://openapi.naver.com/v1/search/news.json?" + urllib.parse.urlencode(
        {"query": query, "display": display, "sort": "date"}
    )
    req = urllib.request.Request(url)
    req.add_header("X-Naver-Client-Id", NAVER_ID)
    req.add_header("X-Naver-Client-Secret", NAVER_SECRET)
    with urllib.request.urlopen(req, timeout=15) as res:
        data = json.loads(res.read().decode("utf-8"))
    return data.get("items", [])


def collect_articles():
    seen = set()
    out = []
    for region_hint, q in QUERIES:
        try:
            items = naver_search(q)
        except Exception as e:
            print(f"[WARN] naver_search failed for '{q}': {e}")
            continue
        for it in items:
            title = strip_html(it.get("title", ""))
            desc = strip_html(it.get("description", ""))
            key = title[:30]
            if not title or key in seen:
                continue
            seen.add(key)
            out.append({
                "title": title,
                "description": desc,
                "link": it.get("originallink") or it.get("link", ""),
                "pubDate": it.get("pubDate", ""),
                "region": detect_region(title + " " + desc) or region_hint,
            })
        time.sleep(0.2)  # 과호출 방지
    # 최신순 정렬 후 상위 N개만 사용
    return out[:MAX_CARDS]


def claude_card(article: dict) -> dict:
    """Claude에게 global/korea/exchange 3줄 요약을 요청."""
    if not CLAUDE_KEY:
        return {
            "global": article["description"][:90] or article["title"],
            "korea": "(CLAUDE_API_KEY 미설정 - 수동 작성 필요)",
            "exchange": "(CLAUDE_API_KEY 미설정 - 수동 작성 필요)",
            "sev": "lo",
        }

    prompt = (
        "다음 국제/경제 뉴스를 '이슈환전소' 브리핑 카드로 만들어줘. "
        "반드시 JSON 오브젝트만 출력하고 다른 텍스트는 절대 넣지 마. "
        '형식: {"global":"글로벌 동향 한 문장","korea":"한국 연결 포인트 한 문장",'
        '"exchange":"환전 포인트(내 삶/자산에 어떤 의미인지) 한 문장","sev":"hi 또는 mid 또는 lo"}. '
        f"제목: {article['title']}. 요약: {article['description']}"
    )
    body = json.dumps({
        "model": "claude-sonnet-5",
        "max_tokens": 400,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        method="POST",
        headers={
            "x-api-key": CLAUDE_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            data = json.loads(res.read().decode("utf-8"))
        raw = data["content"][0]["text"]
        raw = re.sub(r"```json|```", "", raw).strip()
        card = json.loads(raw)
        card.setdefault("sev", "lo")
        return card
    except Exception as e:
        print(f"[WARN] claude_card failed: {e}")
        return {
            "global": article["description"][:90] or article["title"],
            "korea": "(생성 실패 - 원문 참고)",
            "exchange": "(생성 실패 - 원문 참고)",
            "sev": "lo",
        }


def main():
    articles = collect_articles()
    print(f"[INFO] collected {len(articles)} articles")

    cards = []
    for a in articles:
        c = claude_card(a)
        cards.append({
            "region": a["region"],
            "title": a["title"],
            "link": a["link"],
            "pubDate": a["pubDate"],
            "sev": c.get("sev", "lo"),
            "global": c.get("global", ""),
            "korea": c.get("korea", ""),
            "exchange": c.get("exchange", ""),
        })

    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "count": len(cards),
        "cards": cards,
    }

    with open("briefing.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"[DONE] briefing.json written with {len(cards)} cards")


if __name__ == "__main__":
    main()

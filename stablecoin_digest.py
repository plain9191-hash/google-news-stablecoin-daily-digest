#!/usr/bin/env python3
"""Google News Stablecoin daily digest sender.

- Fetch Google News RSS (KR + US)
- Curate top 4-5 articles via Claude (Pro/Max 구독 인증, claude CLI 헤드리스 호출):
  주제별 중복 병합 + 실무 중요도 우선순위 + 한국어 요약 생성
- Claude 호출 실패 시 룰 기반 큐레이션으로 자동 대체
- Send a single newsletter email via Gmail SMTP with App Password
"""

from __future__ import annotations

import html
import json
import os
import re
import smtplib
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import feedparser
from dateutil import parser as dt_parser
from dotenv import load_dotenv

TASK_NAME = "google_news_stablecoin_daily_digest"
DEFAULT_RSS_URL_KR = (
    "https://news.google.com/rss/search?"
    "q=intitle:%22%EC%8A%A4%ED%85%8C%EC%9D%B4%EB%B8%94%EC%BD%94%EC%9D%B8%22"
    "&hl=ko&gl=KR&ceid=KR:ko"
)
DEFAULT_RSS_URL_US = "https://news.google.com/rss/search?q=intitle:stablecoin&hl=en-US&gl=US&ceid=US:en"
KEYWORD_KR = "스테이블코인"
KEYWORD_US = "stablecoin"
GMAIL_SMTP_HOST = "smtp.gmail.com"
GMAIL_SMTP_PORT = 587
WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]

# Claude 큐레이션 (Pro/Max 구독 인증 — API 키 아님)
# 로컬: 'claude' 로그인 세션 사용 / CI: CLAUDE_CODE_OAUTH_TOKEN 시크릿
DEFAULT_CLAUDE_MODEL = "sonnet"
CLAUDE_TIMEOUT_SEC = 180
CLAUDE_RETRIES = 2
CLAUDE_MAX_INPUT = 50  # Claude 프롬프트에 넘길 최신 기사 최대 개수


@dataclass
class NewsEntry:
    title: str
    link: str
    published_at: datetime
    source: str
    description: str = field(default="")


def get_env(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.getenv(name)
    if (value is None or value == "") and default is not None:
        value = default
    if required and not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value or ""


def is_truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_positive_int(name: str, raw: str, *, minimum: int = 1, maximum: int | None = None) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer.") from exc
    if value < minimum:
        raise RuntimeError(f"{name} must be >= {minimum}.")
    if maximum is not None and value > maximum:
        raise RuntimeError(f"{name} must be <= {maximum}.")
    return value


def parse_entry_datetime(raw_entry: dict[str, Any]) -> datetime | None:
    for key in ("published", "updated", "created"):
        raw = raw_entry.get(key)
        if not raw:
            continue
        try:
            dt = dt_parser.parse(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            continue
    return None


def compact_title(title: str, max_chars: int = 90) -> str:
    t = " ".join((title or "").split())
    if len(t) <= max_chars:
        return t
    return t[: max_chars - 1].rstrip() + "…"


def fetch_google_news(rss_url: str, keyword: str, max_items: int, hours_back: int) -> list[NewsEntry]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours_back)
    keyword_norm = keyword.strip().lower()
    seen_links: set[str] = set()

    entries: list[NewsEntry] = []
    feed = feedparser.parse(rss_url)
    for raw in feed.entries:
        title = (raw.get("title") or "").strip()
        if not title:
            continue

        if keyword_norm and keyword_norm not in title.lower():
            continue

        published_at = parse_entry_datetime(raw)
        if not published_at:
            continue
        if published_at < cutoff or published_at > now:
            continue

        link = (raw.get("link") or "").strip()
        if not link or link in seen_links:
            continue
        seen_links.add(link)

        source = ""
        source_raw = raw.get("source")
        if isinstance(source_raw, dict):
            source = str(source_raw.get("title") or "").strip()

        description = (raw.get("summary") or "").strip()

        entries.append(
            NewsEntry(
                title=title,
                link=link,
                published_at=published_at,
                source=source,
                description=description,
            )
        )

    entries.sort(key=lambda x: x.published_at.timestamp(), reverse=True)
    return entries[:max_items]


def _clean_text(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = html.unescape(text)
    return " ".join(text.split())


def _build_local_summary(entry: NewsEntry) -> str:
    excerpt = _clean_text(entry.description)
    if excerpt:
        if len(excerpt) > 220:
            excerpt = excerpt[:219].rstrip() + "…"
        return excerpt
    source = entry.source or "주요 매체"
    posted = entry.published_at.strftime("%m/%d %H:%M UTC")
    return f"{source} 보도. 게시시각 {posted}. 상세 내용은 링크를 참고하세요."


def _rule_based_curate(all_entries: list[NewsEntry]) -> dict[str, Any]:
    """Fallback: 최신 5건을 그대로 선택하고 RSS 발췌로 요약 (Claude 실패 시에만 사용)."""
    selected = all_entries[: min(5, len(all_entries))]
    articles: list[dict[str, Any]] = []
    for idx, entry in enumerate(selected, start=1):
        articles.append(
            {
                "index": idx,
                "duplicate_count": 1,
                "summary": _build_local_summary(entry),
            }
        )

    headline = f"최근 수집된 {len(all_entries)}건 중 최신 {len(articles)}건을 정리했습니다."
    return {"headline": headline, "articles": articles}


def ask_claude(prompt: str, *, model: str, timeout: int) -> str:
    """Claude Code CLI 헤드리스 호출 — Pro/Max 구독 과금.

    로컬에서는 'claude' 로그인 세션을, CI에서는 CLAUDE_CODE_OAUTH_TOKEN 환경변수를 사용한다.
    """
    result = subprocess.run(
        ["claude", "-p", prompt, "--model", model, "--output-format", "text"],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=True,
    )
    return result.stdout.strip()


def _extract_json_object(raw: str) -> str:
    """모델 응답에서 JSON 객체만 추출 (코드펜스/잡텍스트 제거)."""
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else parts[0]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    return text


def _claude_curate(all_entries: list[NewsEntry], *, model: str, timeout: int) -> dict[str, Any]:
    """Claude로 4~5건 선별 + 주제별 중복 병합 + 한국어 요약 생성. 실패 시 예외 발생."""
    subset = all_entries[:CLAUDE_MAX_INPUT]

    articles_text = ""
    for i, e in enumerate(subset, 1):
        excerpt = _clean_text(e.description)
        desc_line = f"\n   본문발췌: {excerpt[:300]}" if excerpt else ""
        articles_text += (
            f"[{i}] {e.title}\n"
            f"   출처: {e.source or '불명'} | {e.published_at.strftime('%m/%d %H:%M')} UTC\n"
            f"   링크: {e.link}{desc_line}\n\n"
        )

    prompt = f"""다음은 수집된 스테이블코인 관련 뉴스 기사 목록입니다.

{articles_text}
선별 기준에 따라 4~5개 기사를 고르고 아래 JSON 형식으로만 응답해주세요.

선별 기준:
1. 중복 주제(비슷한 내용) 기사가 많을수록 우선 선별 — 그 중 가장 대표적인 1개만 선택
2. 스테이블코인 발행·유통 실무팀에게 중요한 뉴스 우선 (규제·법안, 주요 발행사 동향, 시장 구조 변화, 채택 확대 등)

요약 작성 지침:
- 요약은 반드시 한국어로 2~3줄 (개행 없이 한 단락)
- 본문발췌가 있으면 핵심 수치·사실을 요약에 반영할 것
- 발행/유통 실무자 관점에서 "무엇이 바뀌는지", "어떤 행동이 필요한지" 중심으로 서술

JSON 형식 (다른 텍스트 없이 JSON만 응답):
{{
  "headline": "오늘 스테이블코인 시장 핵심을 한 문장으로 요약 (한국어)",
  "articles": [
    {{
      "index": <원본 기사 번호 정수>,
      "duplicate_count": <이 주제와 유사한 기사 수 (본 기사 포함한 정수)>,
      "summary": "기사 핵심 내용 2~3줄 요약 (한국어, 개행 없이 한 단락)"
    }}
  ]
}}"""

    raw = ask_claude(prompt, model=model, timeout=timeout)
    data = json.loads(_extract_json_object(raw))

    raw_articles = data.get("articles")
    if not isinstance(raw_articles, list) or not raw_articles:
        raise ValueError("Claude 응답에 articles 배열이 없습니다.")

    cleaned: list[dict[str, Any]] = []
    seen_idx: set[int] = set()
    for item in raw_articles:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("index", 0))
        except (TypeError, ValueError):
            continue
        if idx < 1 or idx > len(subset) or idx in seen_idx:
            continue
        seen_idx.add(idx)
        try:
            dup = int(item.get("duplicate_count", 1) or 1)
        except (TypeError, ValueError):
            dup = 1
        summary = str(item.get("summary", "")).strip() or _build_local_summary(all_entries[idx - 1])
        cleaned.append({"index": idx, "duplicate_count": max(1, dup), "summary": summary})

    if not cleaned:
        raise ValueError("Claude 응답에서 유효한 기사 index를 찾지 못했습니다.")

    headline = str(data.get("headline", "")).strip()
    return {"headline": headline, "articles": cleaned}


def curate_articles(all_entries: list[NewsEntry]) -> dict[str, Any]:
    """Claude 큐레이션 시도(재시도 포함). 실패하면 룰 기반으로 자동 대체."""
    model = get_env("CLAUDE_MODEL", DEFAULT_CLAUDE_MODEL).strip() or DEFAULT_CLAUDE_MODEL
    last_err: Exception | None = None
    for attempt in range(1, CLAUDE_RETRIES + 1):
        try:
            curated = _claude_curate(all_entries, model=model, timeout=CLAUDE_TIMEOUT_SEC)
            print(f"Claude curation OK (model={model}, {len(curated['articles'])} articles)")
            return curated
        except Exception as exc:  # noqa: BLE001 — 어떤 실패든 룰 기반으로 graceful degrade
            last_err = exc
            print(f"[curate] Claude 시도 {attempt}/{CLAUDE_RETRIES} 실패: {exc}", file=sys.stderr)
    print(f"[curate] Claude 큐레이션 실패 — 룰 기반으로 대체합니다. (마지막 오류: {last_err})", file=sys.stderr)
    return _rule_based_curate(all_entries)


def build_newsletter_body(curated: dict[str, Any], all_entries: list[NewsEntry], today: datetime) -> str:
    weekday = WEEKDAY_KR[today.weekday()]
    date_str = today.strftime("%y.%m.%d")
    header = f"[{date_str} ({weekday}) 스테이블코인 Newsletter]"
    headline = curated.get("headline", "")

    lines = [header, "뉴스레터 공유 드립니다.", headline, ""]

    for seq, item in enumerate(curated.get("articles", []), 1):
        idx = item.get("index", 0)
        if idx < 1 or idx > len(all_entries):
            continue
        e = all_entries[idx - 1]
        dup = item.get("duplicate_count", 1)
        dup_part = f" ({dup}건)" if dup and dup > 1 else ""
        source_part = f" | {e.source}" if e.source else ""
        lines.append(f"{seq}. {compact_title(e.title)}{dup_part} ({e.link}{source_part})")
        lines.append(f"   | {item.get('summary', '')}")
        lines.append("")

    lines.append(f"Generated at (UTC): {today.isoformat()}")
    return "\n".join(lines)


def build_newsletter_html(curated: dict[str, Any], all_entries: list[NewsEntry], today: datetime) -> str:
    weekday = WEEKDAY_KR[today.weekday()]
    date_str = today.strftime("%y.%m.%d")
    header = html.escape(f"[{date_str} ({weekday}) 스테이블코인 Newsletter]")
    headline = html.escape(curated.get("headline", ""))

    rows: list[str] = []
    for seq, item in enumerate(curated.get("articles", []), 1):
        idx = item.get("index", 0)
        if idx < 1 or idx > len(all_entries):
            continue
        e = all_entries[idx - 1]
        title = html.escape(compact_title(e.title))
        link = html.escape(e.link)
        dup = item.get("duplicate_count", 1)
        dup_badge = f' <span class="badge">{dup}건</span>' if dup and dup > 1 else ""
        source_part = f' | {html.escape(e.source)}' if e.source else ""
        summary = html.escape(item.get("summary", ""))
        rows.append(
            '<article class="card">'
            f'<div class="art-title">{seq}. <a href="{link}">{title}</a>{dup_badge}{source_part}</div>'
            f'<div class="art-summary">| {summary}</div>'
            "</article>"
        )

    if not rows:
        rows.append('<article class="card"><div class="art-title">오늘은 조건에 맞는 기사가 없습니다.</div></article>')

    generated = html.escape(today.isoformat())
    return (
        '<!doctype html>'
        '<html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<style>'
        'body{margin:0;background:#f6f7f9;color:#1f2937;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;}'
        '.wrap{max-width:720px;margin:0 auto;padding:24px 16px 40px;}'
        '.hero{background:#fff;border:1px solid #e5e7eb;border-radius:14px;padding:18px 20px 16px;margin-bottom:12px;}'
        '.hero-header{margin:0 0 6px;font-size:17px;font-weight:700;color:#111827;}'
        '.hero-greeting{margin:0 0 4px;font-size:13px;color:#6b7280;}'
        '.hero-headline{margin:0;font-size:15px;color:#1f2937;line-height:1.55;}'
        '.card{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:14px 16px;margin:8px 0;}'
        '.art-title{font-size:15px;font-weight:600;line-height:1.5;}'
        '.art-title a{color:#0f766e;text-decoration:none;}'
        '.badge{display:inline-block;margin-left:5px;padding:1px 7px;background:#fef3c7;color:#92400e;border-radius:99px;font-size:11px;font-weight:600;vertical-align:middle;}'
        '.art-title a:hover{text-decoration:underline;}'
        '.art-summary{margin-top:8px;font-size:13px;color:#374151;line-height:1.65;}'
        '.foot{margin-top:14px;color:#9ca3af;font-size:11px;}'
        '</style></head><body>'
        '<div class="wrap">'
        '<div class="hero">'
        f'<p class="hero-header">{header}</p>'
        '<p class="hero-greeting">뉴스레터 공유 드립니다.</p>'
        f'<p class="hero-headline">{headline}</p>'
        '</div>'
        + "".join(rows)
        + f'<div class="foot">Generated at (UTC): {generated}</div>'
        '</div></body></html>'
    )


def send_gmail(sender: str, to_email: str, subject: str, body: str, html_body: str) -> None:
    app_password = get_env("GMAIL_APP_PASSWORD", required=True)

    msg = MIMEMultipart("alternative")
    msg["to"] = to_email
    msg["from"] = sender
    msg["subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP(GMAIL_SMTP_HOST, GMAIL_SMTP_PORT) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(sender, app_password)
        smtp.sendmail(sender, to_email, msg.as_string())


def validate_configuration() -> dict[str, Any]:
    task_name = get_env("TASK_NAME", TASK_NAME).strip()
    if task_name != TASK_NAME:
        raise RuntimeError(f"TASK_NAME must be '{TASK_NAME}'")

    rss_url_kr = get_env("RSS_URL_KR", DEFAULT_RSS_URL_KR).strip()
    rss_url_us = get_env("RSS_URL_US", DEFAULT_RSS_URL_US).strip()
    to_email = get_env("TO_EMAIL", required=True).strip()
    from_email = get_env("FROM_EMAIL", required=True).strip()

    hours_back = parse_positive_int("HOURS_BACK", get_env("HOURS_BACK", "24"))
    max_items = parse_positive_int("MAX_ITEMS", get_env("MAX_ITEMS", "100"), maximum=100)

    get_env("GMAIL_APP_PASSWORD", required=True)
    validate_only = is_truthy(get_env("VALIDATE_ONLY", ""))

    return {
        "task_name": task_name,
        "rss_url_kr": rss_url_kr,
        "rss_url_us": rss_url_us,
        "to_email": to_email,
        "from_email": from_email,
        "hours_back": hours_back,
        "max_items": max_items,
        "validate_only": validate_only,
    }


def main() -> None:
    load_dotenv()

    config = validate_configuration()

    to_email = config["to_email"]
    from_email = config["from_email"]
    hours_back = config["hours_back"]
    max_items = config["max_items"]

    if config["validate_only"]:
        print(f"Configuration valid for {config['task_name']}: to={to_email}, hours_back={hours_back}, max_items={max_items}")
        return

    kr_entries = fetch_google_news(
        rss_url=config["rss_url_kr"], keyword=KEYWORD_KR, max_items=max_items, hours_back=hours_back
    )
    us_entries = fetch_google_news(
        rss_url=config["rss_url_us"], keyword=KEYWORD_US, max_items=max_items, hours_back=hours_back
    )

    all_entries = kr_entries + us_entries
    all_entries.sort(key=lambda x: x.published_at.timestamp(), reverse=True)

    print(f"Fetched {len(kr_entries)} KR + {len(us_entries)} US = {len(all_entries)} total articles")

    today = datetime.now(timezone.utc)
    weekday = WEEKDAY_KR[today.weekday()]
    date_str = today.strftime("%y.%m.%d")
    subject = f"[{date_str} ({weekday}) 스테이블코인 Newsletter]"

    if not all_entries:
        body = f"{subject}\n\n오늘은 조건에 맞는 기사가 없습니다."
        html_body = (
            f'<!doctype html><html><body><p>{html.escape(subject)}</p>'
            '<p>오늘은 조건에 맞는 기사가 없습니다.</p></body></html>'
        )
        send_gmail(sender=from_email, to_email=to_email, subject=subject, body=body, html_body=html_body)
        print("No articles found — sent empty notification")
        return

    curated = curate_articles(all_entries)
    body = build_newsletter_body(curated, all_entries, today)
    html_body = build_newsletter_html(curated, all_entries, today)

    send_gmail(sender=from_email, to_email=to_email, subject=subject, body=body, html_body=html_body)
    n = len(curated.get("articles", []))
    print(f"Sent newsletter ({n} curated articles from {len(all_entries)} total) to {to_email}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Google News Stablecoin daily digest sender.

- Fetch Google News RSS (KR + US)
- Curate top 4-5 latest articles with rule-based summaries
- Send a single newsletter email via Gmail SMTP with App Password
"""

from __future__ import annotations

import html
import os
import re
import smtplib
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


def curate_articles(all_entries: list[NewsEntry]) -> dict[str, Any]:
    """Select top 5 latest articles and generate rule-based summaries."""
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

    headline = f"최근 수집된 {len(all_entries)}건 중 핵심 {len(articles)}건을 정리했습니다."
    return {"headline": headline, "articles": articles}


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

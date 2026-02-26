#!/usr/bin/env python3
"""Google News Stablecoin daily digest sender.

- Fetch Google News RSS
- Keep titles containing the target keyword
- Sort by latest first and keep up to MAX_ITEMS (max 100)
- Send email via Gmail OAuth refresh token
"""

from __future__ import annotations

import base64
import html
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import feedparser
from dateutil import parser as dt_parser
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

TASK_NAME = "google_news_stablecoin_daily_digest"
DEFAULT_RSS_URL_KR = (
    "https://news.google.com/rss/search?"
    "q=intitle:%22%EC%8A%A4%ED%85%8C%EC%9D%B4%EB%B8%94%EC%BD%94%EC%9D%B8%22"
    "&hl=ko&gl=KR&ceid=KR:ko"
)
DEFAULT_RSS_URL_US = "https://news.google.com/rss/search?q=intitle:stablecoin&hl=en-US&gl=US&ceid=US:en"
KEYWORD_KR = "스테이블코인"
KEYWORD_US = "stablecoin"
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.send"


@dataclass
class NewsEntry:
    title: str
    link: str
    published_at: datetime
    source: str


def get_env(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.getenv(name)
    if (value is None or value == "") and default is not None:
        value = default
    if required and not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value or ""


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

        entries.append(
            NewsEntry(
                title=title,
                link=link,
                published_at=published_at,
                source=source,
            )
        )

    entries.sort(key=lambda x: x.published_at.timestamp(), reverse=True)
    return entries[:max_items]


def build_email_body(entries: list[NewsEntry], keyword: str, hours_back: int) -> str:
    lines: list[str] = []
    lines.append(f"Google News Daily Digest - '{keyword}'")
    lines.append("")
    lines.append(f"총 {len(entries)}개 기사 (최근 {hours_back}시간, 최신순)")
    lines.append("")

    if not entries:
        lines.append("오늘은 조건에 맞는 기사가 없습니다.")
        lines.append("")

    for idx, e in enumerate(entries, start=1):
        source = f" ({e.source})" if e.source else ""
        lines.append(f"[{idx}] {compact_title(e.title)}{source}")
        lines.append(f"- 링크: {e.link}")
        lines.append(f"- 게시시각(UTC): {e.published_at.isoformat()}")
        lines.append("")

    lines.append(f"Generated at (UTC): {datetime.now(timezone.utc).isoformat()}")
    return "\n".join(lines)


def build_email_html(entries: list[NewsEntry], keyword: str, hours_back: int) -> str:
    rows: list[str] = []
    for idx, e in enumerate(entries, start=1):
        source = f" ({html.escape(e.source)})" if e.source else ""
        title = html.escape(compact_title(e.title))
        link = html.escape(e.link)
        posted = html.escape(e.published_at.isoformat())
        rows.append(
            "<article class=\"card\">"
            f"<strong class=\"title\">[{idx}] {title}{source}</strong>"
            f"<div class=\"link\"><a href=\"{link}\">{link}</a></div>"
            f"<div class=\"meta\">게시시각(UTC): {posted}</div>"
            "</article>"
        )

    empty_block = ""
    if not entries:
        empty_block = (
            "<article class=\"card\">"
            "<strong class=\"title\">오늘은 조건에 맞는 기사가 없습니다.</strong>"
            "</article>"
        )

    generated = html.escape(datetime.now(timezone.utc).isoformat())
    return (
        "<!doctype html>"
        "<html><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<style>"
        "body{margin:0;background:#f6f7f9;color:#1f2937;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;}"
        ".wrap{max-width:760px;margin:0 auto;padding:24px 16px 40px;}"
        ".hero{background:#ffffff;border:1px solid #e5e7eb;border-radius:14px;padding:18px 18px 14px;}"
        ".headline{margin:0;font-size:22px;line-height:1.25;letter-spacing:-0.02em;}"
        ".sub{margin:8px 0 0;color:#6b7280;font-size:13px;}"
        ".section{margin-top:16px;}"
        ".card{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:14px;margin:10px 0;}"
        ".title{display:block;font-size:16px;line-height:1.45;}"
        ".link{margin-top:8px;word-break:break-all;}"
        ".link a{color:#0f766e;font-size:14px;font-weight:700;text-decoration:none;}"
        ".link a:hover{text-decoration:underline;}"
        ".meta{margin-top:6px;font-size:12px;color:#6b7280;word-break:break-all;}"
        ".foot{margin-top:14px;color:#6b7280;font-size:12px;}"
        "</style></head><body>"
        "<div class=\"wrap\">"
        "<header class=\"hero\">"
        f"<h1 class=\"headline\">Google News Daily Digest - '{html.escape(keyword)}'</h1>"
        f"<p class=\"sub\">총 {len(entries)}개 기사 (최근 {hours_back}시간, 최신순, 최대 100개)</p>"
        "</header>"
        "<section class=\"section\">"
        + (empty_block + "".join(rows))
        + "</section>"
        f"<div class=\"foot\">Generated at (UTC): {generated}</div>"
        "</div></body></html>"
    )


def send_gmail(sender: str, to_email: str, subject: str, body: str, html_body: str) -> None:
    client_id = get_env("GOOGLE_CLIENT_ID", required=True)
    client_secret = get_env("GOOGLE_CLIENT_SECRET", required=True)
    refresh_token = get_env("GOOGLE_REFRESH_TOKEN", required=True)

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=[GMAIL_SCOPE],
    )

    service = build("gmail", "v1", credentials=creds)

    msg = MIMEMultipart("alternative")
    msg["to"] = to_email
    msg["from"] = sender
    msg["subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    service.users().messages().send(userId="me", body={"raw": raw}).execute()


def main() -> None:
    load_dotenv()

    task_name = get_env("TASK_NAME", TASK_NAME)
    if task_name != TASK_NAME:
        raise RuntimeError(f"TASK_NAME must be '{TASK_NAME}'")

    rss_url_kr = get_env("RSS_URL_KR", DEFAULT_RSS_URL_KR)
    rss_url_us = get_env("RSS_URL_US", DEFAULT_RSS_URL_US)

    to_email = get_env("TO_EMAIL", required=True)
    from_email = get_env("FROM_EMAIL", required=True)

    hours_back = int(get_env("HOURS_BACK", "24"))
    max_items_raw = int(get_env("MAX_ITEMS", "100"))
    max_items = max(1, min(max_items_raw, 100))

    jobs = [
        ("KR", KEYWORD_KR, rss_url_kr),
        ("US", KEYWORD_US, rss_url_us),
    ]

    for tag, keyword, rss_url in jobs:
        entries = fetch_google_news(rss_url=rss_url, keyword=keyword, max_items=max_items, hours_back=hours_back)
        subject = f"[Stablecoin News:{tag}] '{keyword}' Last {hours_back}h - {len(entries)} items"
        body = build_email_body(entries, keyword=keyword, hours_back=hours_back)
        html_body = build_email_html(entries, keyword=keyword, hours_back=hours_back)
        send_gmail(sender=from_email, to_email=to_email, subject=subject, body=body, html_body=html_body)
        print(f"Sent {tag} stablecoin digest with {len(entries)} items to {to_email}")


if __name__ == "__main__":
    main()

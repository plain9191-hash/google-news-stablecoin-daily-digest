# Google News Stablecoin Daily Digest

Google News RSS에서 기사명에 `스테이블코인`/`stablecoin`이 들어간 뉴스를 모아,
Claude로 핵심 4~5건을 큐레이션한 뒤 뉴스레터 메일 1건을 발송하는 프로젝트입니다.

## 기능

- 한국/미국 Google News RSS 동시 수집 (최근 24시간, 제목 키워드 필터)
- **Claude 큐레이션** — 주제별 중복 병합 + 발행·유통 실무 중요도 우선순위 + 한국어 요약
  - Pro/Max **구독 인증**(`claude` CLI 헤드리스 호출)로 동작 — API 키 불필요
  - Claude 호출 실패 시 룰 기반(최신순) 큐레이션으로 자동 대체
- Gmail SMTP(App Password)로 HTML 뉴스레터 1건 발송
- GitHub Actions 매일 오전 6시(Asia/Seoul) 자동 실행

## 1) 설치

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 2) 환경 변수

```bash
cp .env.example .env
```

필수:

- `TO_EMAIL`
- `FROM_EMAIL`
- `GMAIL_APP_PASSWORD` (Gmail 2단계 인증 후 발급한 앱 비밀번호)

옵션(기본값 제공):

- `CLAUDE_MODEL` (기본: `sonnet`)
- `RSS_URL_KR` (기본: 한국 Google News RSS)
- `RSS_URL_US` (기본: 미국 Google News RSS)
- `HOURS_BACK` (기본: `24`)
- `MAX_ITEMS` (기본: `100`, 최대치 100으로 자동 제한)

## 3) Claude 로그인 (큐레이션용)

로컬 실행은 `claude` CLI의 로그인 세션을 그대로 사용합니다. 미설치 시:

```bash
npm install -g @anthropic-ai/claude-code
claude   # 최초 1회 Pro/Max 계정으로 로그인
```

> Claude 호출이 안 되더라도 메일은 룰 기반 큐레이션으로 정상 발송됩니다.

## 4) 로컬 실행

```bash
python stablecoin_digest.py
```

## 5) GitHub Actions 설정

워크플로우 파일:

- `.github/workflows/daily-digest.yml`

등록할 GitHub Actions Secrets:

- `TO_EMAIL`
- `FROM_EMAIL`
- `GMAIL_APP_PASSWORD`
- `CLAUDE_CODE_OAUTH_TOKEN` — Claude 구독 인증 토큰. 로컬에서 `claude setup-token`으로 발급해 등록
- `RSS_URL_KR` (선택, 미등록 시 기본 한국 RSS 사용)
- `RSS_URL_US` (선택, 미등록 시 기본 미국 RSS 사용)

스케줄:

- 매일 오전 6시 (Asia/Seoul)
- cron(UTC): `0 21 * * *`

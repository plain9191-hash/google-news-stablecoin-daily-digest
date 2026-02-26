# Google News Stablecoin Daily Digest

Google News RSS에서 기사명에 `스테이블코인`/`stablecoin`이 들어간 뉴스를 모아,
최근 24시간 기준으로 키워드별 메일 2건을 발송하는 프로젝트입니다.

## 기능

- Google News RSS 검색 사용
- 한국/미국 Google News RSS 동시 수집
- 제목에 `스테이블코인` 포함 기사: 최대 100개 메일 1건
- 제목에 `stablecoin` 포함 기사: 최대 100개 메일 1건
- 최근 24시간 기사만 필터 후 최신순 정렬
- GitHub Actions 매일 오전 8시(Asia/Seoul) 자동 실행

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
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_REFRESH_TOKEN`

옵션(기본값 제공):

- `RSS_URL_KR` (기본: 한국 Google News RSS)
- `RSS_URL_US` (기본: 미국 Google News RSS)
- `HOURS_BACK` (기본: `24`)
- `MAX_ITEMS` (기본: `100`, 최대치 100으로 자동 제한)

## 3) OAuth 토큰 발급 (최초 1회)

```bash
python oauth_setup.py
```

생성된 `oauth_token.json`의 `refresh_token`을 `.env`의 `GOOGLE_REFRESH_TOKEN`에 입력하세요.

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
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_REFRESH_TOKEN`
- `STABLECOIN_RSS_URL_KR` (선택, 미등록 시 기본 한국 RSS 사용)
- `STABLECOIN_RSS_URL_US` (선택, 미등록 시 기본 미국 RSS 사용)

스케줄:

- 매일 오전 8시 (Asia/Seoul)
- cron(UTC): `0 23 * * *`

# JB×AX 맛집 트래커 — 작업 로그 & 핸드오프 가이드

> 다른 PC에서 이어서 작업하기 위한 문서. 프로젝트 구조 + 이번 세션 작업 내역 + 배포 방법.
> 최종 업데이트: 2026-07-09

---

## ✅ [해결됨 2026-07-09] 프로젝트 정지 → 복구 완료

**증상이었던 것**: Supabase `nrdapzgtibbusvoaceuh` 정지(INACTIVE) → 컨디션 검색(analyze)·주가·리뷰·관리자 전부 불통.
**원인**: 무료 org `xmfktepqewgqaajyvgqm`(free)의 **활성 2개 한도**를 darkweb-monitor·silvertown이 차지 → restore 403.
**조치**: `darkweb-monitor` 정지로 슬롯 확보 → `POST /v1/projects/<ref>/restore` → **ACTIVE_HEALTHY 복구**. 비용 $0.
**검증**: analyze 200 + 실제 추천 반환, stock/track/places-search 200, admin 401(인증게이트 정상), comments REST 200.

> ⚠️ 이 프로젝트는 **jblunch 전용이 아니라 5개 서비스 공용 백엔드**다 — 함수 15개:
> jblunch(admin·analyze·places-search·stock·track·kakao-token) / VulnScan(vuln-scan·vuln-remediate) /
> secuday(ai-ask·send-mailing·generate-poster) / frfd(insight-ai·quick-handler) / 뉴스레터·웹툰.
> 그래서 이 프로젝트가 멈추면 5개가 동시에 죽는다. **무료 티어 = 활성 2개 한도**이므로,
> 무료 org에 프로젝트가 3개 이상이면 또 강제정지가 재발한다(현재 shared+silvertown 2개 = 안정).

**진행 중인 후속 작업**: darkweb을 이 공유 프로젝트로 **통합**하는 중(그래야 darkweb도 살아나고 슬롯 문제 영구 해소).
→ 상세·재개 방법은 **darkweb repo `/Users/hk/darkweb-monitor-dashboard/HANDOFF-MIGRATION.md`** 참고. 현재 darkweb은 정지 상태(다운).

---

## 1. 프로젝트 개요

여의도 **JB금융(JBFG) 빌딩**(여의나루로 77) 근처 점심·저녁·술집 맛집을 매일 자동 추천하는 웹앱.

- **사이트**: https://duelspost-droid.github.io/jblunch/
- **저장소**: https://github.com/duelspost-droid/jblunch
- **호스팅**: GitHub Pages (master 브랜치 push 시 자동 배포, 반영까지 1~3분)
- **자동 추천**: GitHub Actions가 **평일(월~금) 아침 15분 간격**으로 돌며, 배치 스크립트가 **관리자 지정 KST 시각**(관리자 페이지 설정 탭, 기본 06:00·05:00~12:00) 이후 첫 실행에 **하루 1회**만 동작 (`.github/workflows/daily-lunch.yml`, cron `*/15 20-23,0-3 * * *`). 게이트=주말·시각·중복(history.json 오늘날짜) 판별. 시각 저장은 `app_settings.batch_hour/minute`(anon 읽기 정책, admin 함수 `get/set_batch_time`)

### 기술 스택
| 영역 | 기술 |
|------|------|
| 프론트엔드 | 단일 `index.html` (바닐라 JS, 인라인 CSS). 빌드 없음 |
| 추천 데이터 | `history.json` → `index.html`의 `HISTORY_DATA`에 인라인 주입 |
| 백엔드(실시간) | Supabase Edge Functions (Deno/TypeScript) |
| DB | Supabase Postgres (리뷰·검색기록·컨디션) |
| 일일 생성 | Python `scripts/generate_lunch.py` (GitHub Actions에서 실행) |
| 앱 | Capacitor Android 래퍼 (`app/`) |

---

## 2. 새 PC 세팅

```bash
# 1) 클론
gh repo clone duelspost-droid/jblunch
cd jblunch

# 2) 프론트엔드는 빌드 불필요 — index.html 직접 편집
#    로컬 미리보기: 아무 정적 서버나 사용
python -m http.server 3000   # → http://localhost:3000

# 3) Supabase 함수 배포용 (선택)
#    npm 설치된 환경에서 npx supabase 사용 (전역 설치 불필요)
```

### 필요한 키/토큰 (값은 저장소에 없음 — 아래 위치에서 발급/확인)
| 키 | 용도 | 위치 |
|----|------|------|
| Kakao REST API Key | 지도/장소 검색 | 코드에 기본값 내장(`af04...`). [developers.kakao.com](https://developers.kakao.com) → 앱 "여의나루 점심봇" → **카카오맵 서비스 ON 필수** |
| Naver Client ID/Secret | 지역검색·뉴스 | [developers.naver.com](https://developers.naver.com) → 검색 API. GitHub Secrets + Supabase Secrets에 등록됨 |
| Supabase anon key | 프론트→Supabase | `index.html`에 공개키로 내장 |
| **Supabase Access Token (sbp_...)** | **함수 배포용** | [supabase.com/dashboard/account/tokens](https://supabase.com/dashboard/account/tokens)에서 새로 발급 (저장 안 함) |
| ANTHROPIC_API_KEY | Claude 호출 | GitHub Secrets + Supabase Secrets |
| GMAIL_USER / GMAIL_APP_PASSWORD | 이메일 발송 | GitHub Secrets |
| KAKAO_REFRESH_TOKEN 등 | 카카오톡 발송 | GitHub Secrets |
| ADMIN_PASSWORD | 관리자 페이지 비번 | Supabase Secrets (현재 임시값, 변경 필요) |

> ⚠️ 이 문서·저장소에 **Supabase Access Token, Naver Secret 등 민감값을 절대 커밋하지 말 것.**

---

## 3. 핵심 좌표 (중요)

JB빌딩(여의나루로 77) 실제 좌표 — Kakao 주소 지오코딩으로 검증됨:
```
JB_LAT = 37.5240914884765
JB_LNG = 126.927376521939
```
(과거 하드코딩 `37.5215, 126.9319`은 ~490m 어긋난 오류였음. 거리 계산은 이 좌표 기준 직선거리 × 1.35(도로보정) ÷ 80m/분.)

---

## 4. 파일 구조

| 파일 | 설명 |
|------|------|
| `index.html` | 프론트엔드 전체 (UI/CSS/JS, HISTORY_DATA 내장) |
| `manage-jbax.html` | 관리자 페이지 (토큰 로그인·기간/액션 로그 필터·방문통계·비번변경) |
| `history.json` | 날짜별 추천 데이터 (daily 스크립트가 갱신) |
| `scripts/generate_lunch.py` | 일일 추천 생성 (Claude + Kakao/Naver + 날씨/뉴스 + 이메일) |
| `scripts/kakao_send.py` | 카카오톡 '나에게 보내기' 발송 |
| `supabase/functions/analyze/index.ts` | 직접입력 맞춤 추천 Edge Function |
| `supabase/functions/places-search/index.ts` | 내 주변/이름 맛집 검색 Edge Function |
| `supabase/functions/track/index.ts` | 접속/작업 로그 기록(IP) Edge Function |
| `supabase/functions/admin/index.ts` | 관리자 로그 조회 + 비번 변경 Edge Function |
| `supabase/functions/stock/index.ts` | 주가/지수 조회 Edge Function |
| `.github/workflows/daily-lunch.yml` | 매일 자동 실행 워크플로우 |
| `app/` | Capacitor Android 앱 (**번들 모드**: webDir=www, server.url 없음) |
| `app/release.ps1` | 앱 원클릭 릴리스(버전↑·빌드·GitHub Release·version.json) |
| `app-download.html` | APK 다운로드 안내 페이지 (version.json 읽어 최신 링크) |
| `version.json` | 앱 최신 버전·APK URL·릴리스 노트 (자체 업데이트용) |
| `manage-jbax.html` | 관리자 페이지 |
| `CLAUDE.md` | 프로젝트 요약 |

---

## 5. Supabase

- **Project ref**: `nrdapzgtibbusvoaceuh`
- **URL**: https://nrdapzgtibbusvoaceuh.supabase.co

### 테이블
| 테이블 | 주요 컬럼 |
|--------|-----------|
| `comments` | id, restaurant_id(=가게명), reviewer_name, content, rating, **meal(점심/저녁/술집)**, created_at, **owner_token(sha256 소유토큰)** |
| `daily_preference` | date, preference |
| `search_history` | id, text, meal, created_at |
| `access_logs` | id, created_at, ip, action, detail, user_agent, path (RLS — service role 전용) |
| `admin_config` | id, password_hash(PBKDF2), salt, iterations, updated_at (RLS — service role 전용) |
| `admin_sessions` | token, ip, created_at, expires_at (RLS — service role 전용) |
| `ip_geo` | ip(PK), country, country_code, region, city, isp, mobile, proxy, updated_at (IP→지역 30일 캐시) |
| `place_meta` | name(PK), intro, price, distance, verified (AI 맛집 소개 서버 캐시) |

### Edge Function 배포
```bash
SUPABASE_ACCESS_TOKEN="sbp_..." npx --yes supabase@latest functions deploy <함수명> --project-ref nrdapzgtibbusvoaceuh
# 함수: analyze, places-search, track, admin

# 시크릿 등록(최초 1회 또는 변경 시)
SUPABASE_ACCESS_TOKEN="sbp_..." npx supabase secrets set KAKAO_REST_API_KEY=... NAVER_CLIENT_ID=... NAVER_CLIENT_SECRET=... --project-ref nrdapzgtibbusvoaceuh
```

### DB 스키마 변경 (Management API 예시)
```bash
curl -s -X POST "https://api.supabase.com/v1/projects/nrdapzgtibbusvoaceuh/database/query" \
  -H "Authorization: Bearer sbp_..." -H "Content-Type: application/json" \
  -d '{"query":"ALTER TABLE comments ADD COLUMN IF NOT EXISTS meal text;"}'
```

---

## 5-1. 관리자 페이지 & 접속/작업 로그

- **접속**: **별도 페이지** https://duelspost-droid.github.io/jblunch/manage-jbax.html
  (`index.html`에서 `#admin` 접근 시 리다이렉트. `noindex` 메타로 검색 비노출)
- **현재 비밀번호**: `021600` (2026-06-06 재설정) → 로그인 후 "🔑 비밀번호 변경"에서 변경 권장.

### 보안 (PBKDF2 + 토큰 + 잠금 + 감사) — 강화됨
- **비번 해시**: `admin_config`에 **PBKDF2(솔트+12만회)** 저장. 레거시 SHA-256은 로그인 성공 시 자동 마이그레이션.
- **세션 토큰**: 로그인 시 랜덤 토큰(8h) 발급 → 클라이언트는 **비번 대신 토큰**을 **localStorage**에 저장
  (XSS 시 비번 유출 방지, 새로고침에도 유지). 비번 변경 시 **모든 세션 무효화** + 새 토큰 발급.
- **무차별 대입 잠금**: IP당 15분 내 5회 실패 → 429 차단.
- **감사 로그**: `admin_login` / `admin_fail` / `admin_pw_change` 기록. 타이밍 안전 비교.
- **비번 변경**: 현재 비번 재확인 필수, **6자 이상**.
- ⚠️ **로그인 호출 주의**: `adminCall({action:'login', ...})`에 `...logFilter`를 펼치면 `action:''`이
  덮어써 로그인 분기를 안 타 **토큰 미발급** → 새로고침 로그아웃. 라우팅 action을 필터로 덮지 말 것.

### Edge Functions
- **`track`**: 클라이언트 호출 시 서버가 요청 헤더에서 **IP·UA**를 읽어 `access_logs`에 service role로 저장.
  기록 시점: visit / review_create·edit·delete / nearby·place_search / custom_recommend.
- **`admin`** 액션:
  - `login {password, limit}` → 토큰 발급 + 대시보드(최근 로그·방문통계) 반환
  - `logs {token, limit}` → 최근 로그 반환 (**기간·액션·검색 필터는 클라이언트에서 처리**)
  - `change_password {token, currentPassword, newPassword}` / `logout {token}`
- **IP 지역(지오로케이션)**: dashboard가 로그 IP를 **ip-api 배치**로 국가·지역·도시·ISP 조회,
  `ip_geo`에 30일 캐시. 각 로그에 `geo` 부착(타임아웃 4초).

### 로그 화면(manage-jbax.html) — 클라이언트 필터
- **필터 전부 클라이언트 처리**: 로그인 시 최근 1000건을 받아두고, 일/월/기간/전체·액션·검색을
  **서버 왕복 없이 즉시** 필터(KST 경계 계산). 기본 기간 = **일(오늘)**.
- **무한 스크롤**: 한 화면에서 10개씩, 아래로 스크롤 시 다음 10개 누적(내부 스크롤, ≈430px/모바일 65vh).
- **시간 표시 KST 고정**(기기 시간대 무관). **지역 컬럼**(국기+도시, hover로 ISP/모바일/VPN).
- 상단 요약은 건수·고유 IP·지역요약만(액션별 개수 나열 제거).

### 보안/RLS
- `access_logs` / `admin_config` / `admin_sessions` / `ip_geo` 모두 RLS로 anon 차단, service role(Edge Function)만 접근.
- 프론트: `manage-jbax.html`(독립, 토큰 인증·클라이언트 필터·무한스크롤·UA 파싱·방문 그래프·IP 지역), `index.html`의 `track()`.

---

## 6. 추천 알고리즘 (하이브리드)

```
[일일 자동] generate_lunch.py
  ① Kakao 반경 + Naver 키워드로 JB 근처 실제 음식점 후보 수집
  ② 날씨(wttr.in) + JB금융 뉴스(Naver) 먼저 수집 → mood_ctx
  ③ Claude Haiku가 후보 + 웹지식 + 분위기로 추천 생성
     - 기본 5곳(거리밴드 0~3/3~7/7~10분 분산, 최근 3일 중복 제외)
     - extras 2곳(근처유명/검색유명, 둘 다 도보 10분 이내)
     - by_condition 10종(해장/매콤/가볍게/.../와인/임원)
  ④ Kakao/Naver로 거리 실측 + 검증(verified). 실패해도 '지도없음'으로 유지
  ⑤ history.json + index.html 갱신 → git push → Pages 배포
  ⑥ 날씨/뉴스/멘트 + 추천을 이메일·카카오톡 발송

[직접입력] analyze Edge Function — 위와 동일 구조를 실시간으로 (검증 병렬화)
[주변검색] places-search Edge Function — Kakao(반경)+Naver(동이름 키워드) 병합
```

---

## 7. 이번 세션(2026-06-03~04) 작업 내역

### 추천/백엔드
- [x] **JB 좌표 버그 수정** (490m 오류 → 정확 좌표)
- [x] Kakao + Naver 실측 도보 거리 (카카오맵 서비스 활성화 필요했음)
- [x] 하이브리드 추천: 실제 후보 목록을 Claude에 주입
- [x] 검증 실패해도 제거 않고 `verified=false` → '🚫 지도없음' 표시
- [x] 추천 다양성: 최근 3일 중복 제외 + 거리밴드 분산
- [x] 추가 추천(extras): 근처유명/검색유명 각 1곳, **도보 10분 이내 제한**
- [x] 직접입력 검색(analyze)도 동일 하이브리드 + 검증 병렬화
- [x] 날씨(wttr.in) + JB금융 뉴스(Naver) 수집 → 이메일/카카오/홈 표시
- [x] 분위기 멘트 생성 + 추천 프롬프트에 mood_ctx 반영

### 프론트엔드
- [x] 제목 `JB×AX 맛집 트래커` (×AX 골드 강조)
- [x] 홈 배너에 날씨 + 💬멘트 + 📰JB금융 뉴스 표시
- [x] 추가 추천 섹션 '✨ 이런 곳도 가볼 만해요' + 태그 배지
- [x] 리뷰 모달 확대 + 맛집 상세정보(종류/가격/거리/지도) 칩
- [x] 리뷰 탭 맛집 목록 상세화
- [x] 리뷰에 방문 시간대(점심/저녁/술집) 기록 — `comments.meal` 컬럼
- [x] **리뷰 수정(PATCH)** + 삭제 + 개별 ✏️/✕ 버튼
- [x] 리뷰 탭 '📍 내 주변 맛집 보기' 버튼 → GPS 기반 주변 맛집 목록
      (Kakao+Naver 병합, 출처 배지). 위치 거부 시 JB빌딩 폴백
- [x] PC 반응형: 900px↑ 카드 2단 그리드, 상단 정렬 통일
- [x] (시도→복원) 컨디션 영역은 **원래 위치(카드 위)** 유지

### 관리자/로그
- [x] 관리자 **별도 페이지** `manage-jbax.html` + 비밀번호 로그인
- [x] 접속/작업 로그(IP 포함) — track/admin Edge Function + access_logs 테이블
- [x] **비밀번호 자체 변경** — admin_config 해시 저장 + change_password 액션
- [x] **보안 강화** — PBKDF2 + 세션토큰 + 무차별대입 잠금 + 감사로그 (admin_sessions)
- [x] **로그 기간(일/월/기간/전체)·액션별 필터** 조회 (KST)

### 앱
- [x] Capacitor Android 프로젝트(`app/`) — server.url로 라이브 로드
      (빌드는 Android Studio 필요. iOS는 macOS 필요)

---

## 7-1. 이번 세션(2026-06-05~06) 작업 내역

### 맛집 소개/카드
- [x] 리뷰 모달 AI 소개(analyze describe): 모르는 가게 **사과문 방지**(프롬프트+거부 regex+JSON 안전파싱),
      서버 `place_meta`·브라우저 `placeIntros`(localStorage) 캐시. 면책성 소개는 `isBadIntro()`로 무시·재요청.
- [x] 리뷰 소개를 **3~4문장(120~200자)**으로 확장(`INTRO_CACHE_VER`로 기존 캐시 1회 무효화).
- [x] 카드 한 줄 feature는 **요약형 2줄**(긴 문장 X, 키워드 나열)로 유지. CSS `line-clamp:2`.
- [x] 도보경로: 외부 앱(네이버 유니버설 링크) 대신 **페이지 내 구글지도 임베드**(`output=embed`, 전체화면 iframe).

### 관리자 페이지 (대규모 개편)
- [x] **IP 기반 접속 지역** 표시(ip-api+`ip_geo` 캐시), 지역 컬럼·요약·검색.
- [x] 로그 필터 **클라이언트 처리**로 전환 → 일/월/기간/전체 클릭 즉시 반영. 기본 기간 = **일**.
- [x] **무한 스크롤**(10개씩, 내부 스크롤), 시간 표시 **KST 고정**.
- [x] 세션 저장 **sessionStorage→localStorage** + **로그인 토큰 미발급 버그 수정**(새로고침 로그아웃 해결).
- [x] 비번 재설정(`021600`), 비번 최소 **6자**.
- [x] 디자인 정리(카드/헤더/툴바/줄무늬/고스트 버튼), 액션별 개수 나열 제거.

### 앱/인프라
- [x] 앱 **번들 모드**(SSL 없이 동작) + 런타임에 GitHub raw에서 history.json 로드.
- [x] **버전별 APK 릴리스**(`release.ps1`) + `version.json` 기반 **자체 업데이트 배너**.
- [x] 커스텀 **JB×AX 아이콘**(@capacitor/assets), **매일 8:30 푸시 알림**(local-notifications).
- [x] `app-download.html` 다운로드 안내 페이지.

### 진행 중
- [x] **커스텀 도메인 `lunch.jbax.co.kr` SSL** — 발급 완료, https 정상 서비스 중. (duelspost-droid.github.io는 이 도메인으로 301 리다이렉트)

---

## 7-2. 이번 세션(2026-07-01) 작업 내역

> 추천 엔진을 **하이브리드 풀**로 크게 개편 + 리뷰 배지 + 배치 버그 수정 + 보안/감사.
> **아래 ⏳ cron 1건만 빼고 전부 master에 push 완료.**

### 추천 엔진 — 하이브리드 재설계 (중복 해소 · 밀집도↑ · 매일 새로움)
- [x] 같은 날 기본/컨디션 추천 중복 완화: 프롬프트 제약 + 코드 dedup (`cce9f65`)
- [x] by_condition을 **조건별 Kakao 키워드 풀**에서 코드 조립 (`ce2ae66`)
- [x] 조건 풀에 **Naver 병합** + **🆕 신규맛집**(웹검색 신상)을 '이런 곳도' extras로 추가 (`4b42dd1`)
- [x] **풀 하이브리드**: 코드 풀(Kakao+Naver, 일반풀 ≤300곳) + LLM 웹 보강 + LLM 큐레이션 (`ce4b184`)
- [x] **적응형 반경**: 희박 지역(정읍 등 시골)은 조건 풀 수집 반경 자동 확대 (`24eb4c1`)
- [x] **🩹 기본 추천 0곳 버그 수정** — 정읍 '저녁 기본 0곳'의 근본원인. LLM이 `restaurants=[]`를 줘도
      `_backfill_base()`가 후보 풀에서 5곳 채움(verify 전, 중복·extras·by_condition·recent 제외, 날짜 시드 회전).
      풀까지 비어 끝내 0이면 끼니 실패 처리 → `gen_meal` 재시도/누락(빈 끼니를 성공으로 저장 안 함). (`5476a57`)
      └ 검증: py_compile OK + 단위테스트 6종 PASS. 정상 위치엔 무영향(LLM이 5곳 주면 no-op).

### 프론트엔드 (`index.html`)
- [x] 📍 내 위치 칩: 기본(미선택)도 은은한 연파랑(#eef5fd), 활성 시 초록 유지 (`521b712`)
- [x] 술집 탭 전용 **컨디션 칩 6종**(가볍게 한잔·안주·회식·와인바·조용한 곳·이자카야 등) — 누르면 analyze 실시간 추천 (`46332a9`)
- [x] **🆕 새 리뷰 배지**(최근 2일): `hasNewReview()` 공용 헬퍼 → **카드**(`b3dd718`) + **리뷰 맛집 별점순 랭킹**(`a4d5bc4`) 모두 적용. 라이브 검증 완료.

### 보안 / 운영
- [x] track 함수 **action allowlist**(`supabase/functions/track/index.ts`) — 비인증 클라가 `admin_fail` 등을 위조해 관리자 잠금 우회/감사로그 오염하는 것 차단. (배포 완료)
- [x] 월요일 배치 견고성 + `notify_failure` 부분실패 escalation 확인.
- [x] deploy-functions에 `--use-api`(Docker 없이 서버사이드 번들링) (`1f5c43d`)

### 감사(audit) — 4영역 병렬 점검 → 다음 작업 백로그 (우선순위순)
보안·배치·프론트/모바일·운영 4영역 적대적 감사. **다음 PC에서 이어서 할 후보:**
1. **✅ [해결됨 2026-07-03] 리뷰 무단 수정/삭제** — 열린 RLS(0001) + 프로덕션 드리프트 정책 "Allow all"(public/ALL/using(true))로 anon이 모든 리뷰 DELETE/PATCH 가능하던 취약점. → migration **0002**(owner_token=sha256 해시 RLS + X-Owner-Token 헤더 대조, "Allow all" 제거, owner_token SELECT 미노출) + index.html(비밀 localStorage·해시 저장·내 리뷰에만 ✏️/✕). 커밋 cf1ec70, 라이브 검증 완료.
2. **admin 비번 'change-me' 폴백 확인** (`admin/index.ts:107-113`) — `ADMIN_PASSWORD` 미설정 시 공개 문자열 'change-me'로 시드. **Supabase에서 실제 password_hash가 'change-me' 해시인지 5분 확인** — 맞으면 즉시 high 보안.
3. `suggestion_add` 스팸 무방비(IP 레이트리밋 + pending 공개 제외), `review-photos` 익명 업로드 무제한(버킷 `file_size_limit`+`allowed_mime_types`, 대시보드만), admin 락아웃 XFF 스푸핑 우회.
4. **✅ [해결됨 2026-07-03] 데드코드/문서드리프트**: `updateMealInfo()` 스텁·죽은 `.meal-info` CSS·`matchCondition`/`COND_ANSWERS`/`COND_KEYWORDS`·`get_today_preference()` 제거, CLAUDE.md·WORKLOG 유령 'reviews' 테이블 삭제.
- ✅ 감사로 확인된 **이미 처리됨**(재작업 X): track allowlist · notify_failure 부분실패 escalation · adaptive radius.

### ✅ 완료 — 정규 배치 06:30 → 06:00 KST (2026-07-04, commit `d93310d`)
- cron `30 21 * * 0-4` → **`0 21 * * 0-4`** (UTC 21:00 = KST 06:00, 월~금). 주석도 06:00/21:00으로 정정.
- 로컬 gh 토큰에 `workflow` 스코프가 없어(gist·read:org·repo) push가 막혔으므로, **GitHub 웹 편집기에서 직접 수정·커밋**으로 반영(웹 세션은 workflow 스코프 불필요).
- 참고: 앞으로 로컬에서 워크플로 파일을 push하려면 `gh auth refresh -s workflow -h github.com` 필요(현재 스코프 미보유).

---

## 8. 자주 쓰는 명령

```bash
# 워크플로우 수동 실행
gh workflow run "Daily Lunch Recommendation"
gh run list --workflow="Daily Lunch Recommendation" --limit 5
gh run watch <run-id> --exit-status

# 실패 원인 보기
gh run view <run-id> --log | grep -E "Rate limit|JSON 파싱|❌"
```

> Actions 봇이 history.json/index.html을 커밋하므로, 로컬 push 전 `git pull --rebase origin master` 필요.

---

## 8-1. 카카오 토큰 자동갱신 (사실상 무기한, 2026-07-04 구축)

카카오 refresh_token은 60일 고정이나, 잔여 만료 1개월 미만일 때 refresh 응답에 새 토큰이 실려온다. 이를 저장하면 배치가 60일 안에 1회만 돌아도 계속 갱신됨.
- **저장소**: Supabase `kakao_tokens`(싱글턴, service-role 전용). 배치는 **admin 함수의 `kakao_token_get`/`kakao_token_set`** 액션으로 접근(공유시크릿 `KAKAO_TOKEN_SECRET` 인증).
- **`KAKAO_TOKEN_SECRET`**: GitHub Secret + **Supabase Edge Functions Secrets** 양쪽에 **동일 랜덤값**. 둘이 다르면 401 → 발송은 `KAKAO_REFRESH_TOKEN` 시드로 폴백.
- **kakao_send.py**: `load_refresh_token()`(Supabase get→실패 시 `KAKAO_REFRESH_TOKEN` 시드) → refresh → 응답에 새 refresh_token 오면 `kakao_token_set`으로 되저장.
- **재발급이 필요한 경우**(배치가 60일+ 완전 중단 시): `callback.html`로 code 받아 → 교환 → `kakao_token_set`(Supabase) + `KAKAO_REFRESH_TOKEN`(GH) 둘 다 갱신. client_secret은 콘솔 앱키 페이지의 '클라이언트 시크릿'. redirect_uri=`https://duelspost-droid.github.io/jblunch/callback.html`.

---

## 7-3. 이번 세션(2026-07-03~09) 작업 내역

> 전 페이지 6차원 워크플로 감사(2026-07-03, findings 54→백로그 32건) 후 상위 항목 처리.

### 처리·배포 완료
- [x] **리뷰 무단 수정/삭제 취약점** — `comments` anon update/delete가 `using(true)`(+프로덕션 드리프트 "Allow all")로 누구나 삭제/수정 가능하던 것 → **owner_token=sha256 해시 RLS**(X-Owner-Token 헤더 대조) + 프론트(비밀 localStorage·해시 저장·내 리뷰만 ✏️/✕). migration `0002`, 라이브 검증. (감사 #1)
- [x] **자동배포 활성화** — GitHub Secret `SUPABASE_ACCESS_TOKEN` 등록 + `deploy-functions.yml`에 **`--use-api`**(Docker Hub 레이트리밋 회피) → 함수 push 시 자동배포.
- [x] **배치 견고성**(감사 #6~10,27) — call_claude `pause_turn` 처리·가짜 tool_result 제거 / `_normalize_entry`로 기형 LLM JSON 방어 / `jb` 폴백·`jb_seen` / kakao 실패 `exit 1`+워크플로 분리 / push rebase 재시도·concurrency / permissions 최소화. (`583b031`)
- [x] **데드코드·문서 정리**(감사 #19,20,22,25,26) — updateMealInfo·matchCondition·COND_*·get_today_preference·.meal-info 제거, 유령 `reviews` 테이블 삭제, Secrets 표 보강.
- [x] **주가 배너 미표시 버그** — renderHistory가 배너(#tb-stock) 재생성 시 비워지는데 loadStock 재호출 안 되던 것 → stock 캐시 + 재렌더 시 재호출. (`e824e25`)
- [x] **admin 'change-me' 공개 폴백 제거**(감사 #2) — ADMIN_PASSWORD 미설정 시 공개문자열 시드 → fail-secure. (`6382f9c`)
- [x] **길찾기 출발지 교정** — '내 위치' 검색 후에도 출발이 회사(LOC)로 잡히던 것 → `routeOrigin()`(내위치 맥락이면 GPS `myPos`). (`0d4c050`)
- [x] **공유 메시지 정교화**(카드형) + **공유 URL 중복 제거**(text에서 URL 빼고 url 필드로만). (`0d4c050`, `dd0c773`)
- [x] **PAT 갱신** — 옛 `jblunch-push`(07-01 만료) → 새 classic PAT `jblunch-cli`(repo+workflow)로 git remote 갱신.

### 미처리 백로그 (감사 32건 중 남은 것, 우선순위순)
- **보안**: #3 Kakao 키 하드코딩(`af04...`) 폴백 제거·로테이션(공개저장소 노출) / #12 admin 락아웃 XFF 스푸핑 / #13 suggestion_add 스팸 무방비·pending 즉시공개 / #23 review-photos 익명 업로드 크기·타입 제한. **admin 라이브 비번 '021600'(약함) → 관리자페이지에서 강한 값으로 변경(사용자)**.
- **테스트/CI**: #4 `lunch_utils` 단위테스트(현재 0개, WORKLOG '6종 PASS'는 미커밋) / #5 CI py_compile·pytest 게이트 / #21 assemble/rebalance/backfill 결정적 테스트.
- **프론트 버그**: #14 내주변 검색창 포커스 유실 / #15 위치변경 시 backfill 미취소 / #16 `_introPending` 미삭제 stale / #17 confirmDelete 문자열≠숫자 고스트 / #18 generateRecommend 요청토큰 없어 연타 시 스테일 덮어씀.
- **정리/저위험**: #24 후보 라벨 역파싱 중복→순수함수 추출 / #28 update_history 무방비 JSON 로드 / #29 update_index_html '];' 조기절단 / #30 모달 ESC·스크롤잠금·별점 a11y / #31 주가 부호 누락 / #32 저위험 하드닝 묶음.
- **07-04 신규코드 미감사**: 배치 시각 관리자설정·카카오 토큰 저장(admin 함수 secret 기반) — 적대적 리뷰 가치 있음.

---

## 9. 알려진 이슈 / 다음 작업 후보

- **Rate limit**: Haiku 10k tokens/min. 저녁(컨디션 10종) 생성이 가끔 실패.
  끼니 사이 `sleep 70`로 완화. 수동 테스트를 자동 실행 시간대와 겹치지 말 것.
- **홈 날씨/뉴스**: `weather`/`message`/`news` 필드가 있는 날짜부터 표시됨
  (이 기능 추가 이후 첫 자동 실행분부터 정상).
- **Google Places**: 평점 기반 정렬 가능하나 카드 등록(결제) 필요 → 보류.
- 후보 아이디어: 끼니별 리뷰 통계, 컨디션 칩 정리, 앱 푸시 알림(매일 추천).
```

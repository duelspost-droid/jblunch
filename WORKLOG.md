# JB×AX 맛집 트래커 — 작업 로그 & 핸드오프 가이드

> 다른 PC에서 이어서 작업하기 위한 문서. 프로젝트 구조 + 이번 세션 작업 내역 + 배포 방법.
> 최종 업데이트: 2026-06-04

---

## 1. 프로젝트 개요

여의도 **JB금융(JBFG) 빌딩**(여의나루로 77) 근처 점심·저녁·술집 맛집을 매일 자동 추천하는 웹앱.

- **사이트**: https://duelspost-droid.github.io/jblunch/
- **저장소**: https://github.com/duelspost-droid/jblunch
- **호스팅**: GitHub Pages (master 브랜치 push 시 자동 배포, 반영까지 1~3분)
- **자동 추천**: GitHub Actions가 매일 오전 8:30 KST 실행 (`.github/workflows/daily-lunch.yml`)

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
| `history.json` | 날짜별 추천 데이터 (daily 스크립트가 갱신) |
| `scripts/generate_lunch.py` | 일일 추천 생성 (Claude + Kakao/Naver + 날씨/뉴스 + 이메일) |
| `scripts/kakao_send.py` | 카카오톡 '나에게 보내기' 발송 |
| `supabase/functions/analyze/index.ts` | 직접입력 맞춤 추천 Edge Function |
| `supabase/functions/places-search/index.ts` | 내 주변/이름 맛집 검색 Edge Function |
| `.github/workflows/daily-lunch.yml` | 매일 8:30 자동 실행 워크플로우 |
| `app/` | Capacitor Android 앱 (server.url로 라이브 사이트 로드) |
| `CLAUDE.md` | 프로젝트 요약 |

---

## 5. Supabase

- **Project ref**: `nrdapzgtibbusvoaceuh`
- **URL**: https://nrdapzgtibbusvoaceuh.supabase.co

### 테이블
| 테이블 | 주요 컬럼 |
|--------|-----------|
| `comments` | id, restaurant_id(=가게명), reviewer_name, content, rating, **meal(점심/저녁/술집)**, created_at |
| `reviews` | id, visited |
| `daily_preference` | date, preference |
| `search_history` | id, text, meal, created_at |
| `access_logs` | id, created_at, ip, action, detail, user_agent, path (RLS 켜짐 — service role 전용) |

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

- **접속 방법**: 사이트 주소 뒤에 `#admin` → https://duelspost-droid.github.io/jblunch/#admin
- **비밀번호**: 모달에서 입력. 서버 시크릿 `ADMIN_PASSWORD`와 비교(Edge Function `admin`).
  현재 임시 비번 `jbax-admin-2026` → **반드시 변경할 것**:
  ```bash
  SUPABASE_ACCESS_TOKEN="sbp_..." npx supabase secrets set ADMIN_PASSWORD="새비밀번호" --project-ref nrdapzgtibbusvoaceuh
  ```
- **로그 기록(Edge Function `track`)**: 클라이언트가 호출하면 서버가 **요청 헤더에서 IP·UA**를
  읽어 `access_logs`에 service role로 저장 (클라이언트 위변조 불가).
  - 기록 시점: 방문(visit), 리뷰 생성/수정/삭제, 주변검색(nearby/place_search), 맞춤추천(custom_recommend)
- **로그 조회(Edge Function `admin`)**: 비번 검증 후 최근 로그(최대 500)+통계(총건수/고유IP/액션별) 반환.
- **보안 설계**: 비번 검증·IP 기록·로그 조회 모두 서버측. `access_logs`는 RLS로 anon 접근 차단.
- 프론트 구현: `index.html`의 `track()`, `openAdmin()/adminLogin()/renderAdmin()`.

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
- [x] 관리자 페이지(`#admin`) + 비밀번호(서버 시크릿) 검증
- [x] 접속/작업 로그(IP 포함) — track/admin Edge Function + access_logs 테이블

### 앱
- [x] Capacitor Android 프로젝트(`app/`) — server.url로 라이브 로드
      (빌드는 Android Studio 필요. iOS는 macOS 필요)

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

## 9. 알려진 이슈 / 다음 작업 후보

- **Rate limit**: Haiku 10k tokens/min. 저녁(컨디션 10종) 생성이 가끔 실패.
  끼니 사이 `sleep 70`로 완화. 수동 테스트를 자동 실행 시간대와 겹치지 말 것.
- **홈 날씨/뉴스**: `weather`/`message`/`news` 필드가 있는 날짜부터 표시됨
  (이 기능 추가 이후 첫 자동 실행분부터 정상).
- **Google Places**: 평점 기반 정렬 가능하나 카드 등록(결제) 필요 → 보류.
- 후보 아이디어: 끼니별 리뷰 통계, 컨디션 칩 정리, 앱 푸시 알림(매일 추천).
```

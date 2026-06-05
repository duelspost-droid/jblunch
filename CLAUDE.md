# 여의나루 점심 맛집 트래커

## 프로젝트 개요
서울 영등포구 여의나루로 77 근처 점심 맛집을 매일 자동 추천하는 서비스.
평일(월~금) 새벽 1시경(KST) GitHub Actions가 자동 실행됨 — PC 불필요. (새벽엔 큐 부하 적어 지연↓)

## 사이트 & 저장소
- **사이트**: https://duelspost-droid.github.io/jblunch/
- **저장소**: https://github.com/duelspost-droid/jblunch
- **Actions**: https://github.com/duelspost-droid/jblunch/actions

## 아키텍처
```
GitHub Actions (평일 새벽 01:10 KST)
  └─ scripts/generate_lunch.py
       ├─ Claude Haiku API + 웹검색 → 맛집 추천 (기본 + 컨디션 8종)
       ├─ Supabase → 날씨/컨디션 데이터 저장
       ├─ Gmail SMTP → 이메일 발송 (duels@jbfg.com, CC: duels@hanmail.net)
       ├─ history.json + index.html 업데이트
       └─ git push → GitHub Pages 자동 배포
```

## 주요 파일
| 파일 | 설명 |
|------|------|
| `scripts/generate_lunch.py` | 핵심 스크립트 — 추천 생성, 이메일, 저장 |
| `index.html` | 프론트엔드 — HISTORY_DATA 내장, Supabase 연동 |
| `history.json` | 추천 이력 데이터 (날짜별, 컨디션별) |
| `.github/workflows/daily-lunch.yml` | GitHub Actions 워크플로우 |
| `.env.example` | 필요한 환경변수 목록 |

## GitHub Secrets (Actions용)
| Secret | 설명 |
|--------|------|
| `ANTHROPIC_API_KEY` | Claude API 키 |
| `GMAIL_USER` | 발신 Gmail (duelspost@gmail.com) |
| `GMAIL_APP_PASSWORD` | Gmail App Password |
| `NAVER_CLIENT_ID` | Naver 지역검색 API ID (선택, developers.naver.com) |
| `NAVER_CLIENT_SECRET` | Naver 지역검색 API 시크릿 (선택) |

## 로컬 실행
```bash
# 1. 패키지 설치
pip3 install anthropic PyNaCl

# 2. 환경변수 설정
cp .env.example .env.local
# .env.local 편집 후:
source .env.local

# 3. 실행
python3 scripts/generate_lunch.py

# 4. 특정 날짜로 테스트
python3 -c "
import os, sys
sys.path.insert(0, 'scripts')
import generate_lunch as gl
gl.get_today_kst = lambda: '2026-06-01'
gl.main()
"
```

## Supabase
- **URL**: https://nrdapzgtibbusvoaceuh.supabase.co
- **테이블**:
  - `reviews` — 방문 체크 (id, visited)
  - `comments` — 리뷰 (restaurant_id, reviewer_name, content, rating)
  - `daily_preference` — 오늘 컨디션 (date, preference)
- anon key는 index.html에 하드코딩 (공개용 키)

## 새 PC 세팅
```bash
git clone https://github.com/duelspost-droid/jblunch.git
cd jblunch
bash setup.sh
```

## 주의사항
- GitHub PAT (`ghp_...`): 2026년 7월 1일 만료 → 갱신 필요
- Haiku 모델 rate limit: 10,000 tokens/min — 테스트 연속 실행 시 1분 대기
- `netlify.command`, `push.command`, `.env.local` 은 gitignore 처리됨

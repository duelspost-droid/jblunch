#!/usr/bin/env bash
# diagnose_db.sh — 여의나루 점심 트래커 DB·Edge Function·자동배치 상태 진단
#
# 토큰 없이도 동작(anon 공개키 자동 추출). 더 깊은 점검을 원하면 환경변수로 토큰 제공:
#   SUPABASE_ACCESS_TOKEN=sbp_...   → Management API로 테이블 목록·함수 배포상태 점검
#   GH_PAT=ghp_...                  → 최근 GitHub Actions 자동배치 결과 (미지정 시 git remote에서 추출 시도)
#
# 사용:
#   bash scripts/diagnose_db.sh
#   SUPABASE_ACCESS_TOKEN=sbp_xxx bash scripts/diagnose_db.sh
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

SB_URL="https://nrdapzgtibbusvoaceuh.supabase.co"
PROJECT_REF="nrdapzgtibbusvoaceuh"
REPO="duelspost-droid/jblunch"

# anon 공개키: index.html에서 자동 추출 (저장소에 평문 존재 = 공개용 키)
ANON="${SUPABASE_ANON_KEY:-$(grep -oE 'eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}' index.html | head -1)}"

green(){ printf '\033[32m%s\033[0m' "$1"; }
red(){ printf '\033[31m%s\033[0m' "$1"; }
yellow(){ printf '\033[33m%s\033[0m' "$1"; }
hr(){ printf '%s\n' "------------------------------------------------------------"; }

CURL=(curl -s -m 20)

echo "============================================================"
echo " 여의나루 점심 트래커 — 시스템 진단  ($(TZ=Asia/Seoul date '+%Y-%m-%d %H:%M KST'))"
echo "============================================================"
[ -n "$ANON" ] && echo "anon key: 추출됨 (len=${#ANON})" || { echo "$(red '✗ anon key 추출 실패 — index.html 확인')"; }

# ── 1) Edge Functions 도달성 + admin 배포버전 카나리 ─────────────
hr; echo "[1] Edge Functions"
afetch(){ local body="$2"; [ -z "$body" ] && body='{}'; "${CURL[@]}" -X POST "$SB_URL/functions/v1/$1" \
  -H "apikey: $ANON" -H "Authorization: Bearer $ANON" -H "Content-Type: application/json" -d "$body"; }
for f in admin track analyze places-search stock; do
  code=$("${CURL[@]}" -o /dev/null -w '%{http_code}' -X POST "$SB_URL/functions/v1/$f" \
    -H "apikey: $ANON" -H "Authorization: Bearer $ANON" -H "Content-Type: application/json" -d '{}')
  case "$code" in
    2*|400|401) mark=$(green ok) ;;
    5*) mark=$(red FAIL) ;;
    *) mark=$(yellow "?") ;;
  esac
  printf "   %-14s POST{} → HTTP %s  [%s]\n" "$f" "$code" "$mark"
done

# admin 배포버전 카나리: 'suggestion_public'(최신 코드의 공개 액션)이
#  "unknown action"이면 → 배포된 admin 함수가 구버전(=재배포 필요).
canary=$(afetch admin '{"action":"suggestion_public"}')
echo "   ────"
if echo "$canary" | grep -q '"qna"'; then
  echo "   admin 버전: $(green '최신 (suggestion_public 인식)')"
elif echo "$canary" | grep -qi 'unknown action'; then
  echo "   admin 버전: $(red '구버전 — 재배포 필요') (suggestion_public→\"unknown action\")"
  echo "      → SUPABASE_ACCESS_TOKEN=sbp_... supabase functions deploy admin --project-ref $PROJECT_REF"
else
  echo "   admin 버전: $(yellow '판정 불가') → $canary"
fi

# ── 2) 테이블 도달성/행수 (anon; RLS 테이블은 0건이 정상) ────────
hr; echo "[2] 테이블 (anon 조회 · RLS 테이블은 */0 이 정상)"
RLS_TABLES=" access_logs admin_config admin_sessions ip_geo suggestions "
for t in comments reviews daily_preference search_history access_logs admin_config admin_sessions ip_geo place_meta suggestions app_settings app_locations; do
  cr=$("${CURL[@]}" -D - -o /dev/null "$SB_URL/rest/v1/$t?select=*&limit=1" \
    -H "apikey: $ANON" -H "Authorization: Bearer $ANON" -H "Prefer: count=exact" -H "Range: 0-0" \
    | grep -i 'content-range' | tr -d '\r' | awk '{print $2}')
  code=$("${CURL[@]}" -o /dev/null -w '%{http_code}' "$SB_URL/rest/v1/$t?select=*&limit=1" \
    -H "apikey: $ANON" -H "Authorization: Bearer $ANON")
  total="${cr##*/}"
  if [ "$code" = "404" ]; then mark=$(red '없음/오류')
  elif [[ "$RLS_TABLES" == *" $t "* ]]; then mark=$(green 'RLS ok')
  elif [ -n "$total" ] && [ "$total" != "*" ]; then mark=$(green "${total}건")
  else mark=$(yellow "HTTP $code")
  fi
  printf "   %-18s %s\n" "$t" "$mark"
done

# ── 3) Management API 심층점검 (sbp_ 토큰 있을 때만) ─────────────
hr; echo "[3] 심층점검 (Management API)"
if [ -n "${SUPABASE_ACCESS_TOKEN:-}" ]; then
  q='select table_name from information_schema.tables where table_schema=$$public$$ order by 1'
  "${CURL[@]}" -X POST "https://api.supabase.com/v1/projects/$PROJECT_REF/database/query" \
    -H "Authorization: Bearer $SUPABASE_ACCESS_TOKEN" -H "Content-Type: application/json" \
    -d "{\"query\":\"$q\"}" | python3 -c "import sys,json
try:
  d=json.load(sys.stdin)
  if isinstance(d,list): print('   public 테이블:', ', '.join(r['table_name'] for r in d))
  else: print('   응답:', str(d)[:300])
except Exception as e: print('   파싱 실패:', e)"
  echo "   배포된 함수 목록:"
  supabase functions list --project-ref "$PROJECT_REF" 2>&1 | sed 's/^/      /' | head -20
else
  echo "   $(yellow 'SKIP') — SUPABASE_ACCESS_TOKEN 미설정 (sbp_ 토큰 주면 테이블/함수 배포상태 점검)"
fi

# ── 4) 최근 자동배치 (GitHub Actions) ───────────────────────────
hr; echo "[4] 자동배치 (Daily Lunch Recommendation)"
PAT="${GH_PAT:-$(git remote get-url origin 2>/dev/null | sed -nE 's#https://([^@]+)@.*#\1#p')}"
if [ -n "$PAT" ]; then
  "${CURL[@]}" -H "Authorization: Bearer $PAT" -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/$REPO/actions/runs?per_page=5" | python3 -c "import sys,json
try:
  runs=[r for r in json.load(sys.stdin).get('workflow_runs',[]) if r['name'].startswith('Daily')][:5]
  if not runs: print('   (Daily 실행 없음 또는 토큰 권한 부족)')
  for r in runs:
    c=r['conclusion'] or r['status']
    flag='✅' if c=='success' else ('❌' if c=='failure' else '·')
    print(f\"   {flag} #{r['run_number']:<4} {r['created_at']}  {c}\")
except Exception as e: print('   파싱 실패:', e)"
else
  echo "   $(yellow 'SKIP') — GH_PAT 없고 git remote에서 토큰 추출 불가"
fi
hr
echo "진단 완료."

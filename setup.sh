#!/bin/bash
# 여의나루 점심 맛집 트래커 — 새 PC 세팅 스크립트
set -e

echo "🍽️ 여의나루 점심 맛집 트래커 환경 세팅"
echo "=========================================="

# Python 패키지 설치
echo ""
echo "📦 Python 패키지 설치 중..."
pip3 install anthropic PyNaCl --quiet
echo "✅ anthropic, PyNaCl 설치 완료"

# .env.local 확인
echo ""
if [ ! -f ".env.local" ]; then
  cp .env.example .env.local
  echo "⚠️  .env.local 파일이 생성되었습니다."
  echo "   아래 값을 채워주세요:"
  echo "   - ANTHROPIC_API_KEY"
  echo "   - GMAIL_USER"
  echo "   - GMAIL_APP_PASSWORD"
  echo ""
  echo "   편집: open .env.local"
else
  echo "✅ .env.local 이미 존재"
fi

# git 설정 확인
echo ""
echo "🔗 GitHub 연결 확인..."
git remote -v
echo ""
echo "💡 push 권한이 없다면 GitHub PAT로 remote URL을 업데이트하세요:"
echo "   git remote set-url origin https://PAT@github.com/duelspost-droid/jblunch.git"

echo ""
echo "✅ 세팅 완료!"
echo ""
echo "📋 로컬 실행 방법:"
echo "   source .env.local && python3 scripts/generate_lunch.py"
echo ""
echo "🌐 사이트: https://duelspost-droid.github.io/jblunch/"
echo "⚙️  GitHub Actions: https://github.com/duelspost-droid/jblunch/actions"

#!/bin/bash
cd "$(dirname "$0")"

echo "🔧 Git lock 파일 정리 중..."
find .git -name "*.lock" -delete 2>/dev/null && echo "✅ lock 파일 제거 완료" || echo "ℹ️  lock 파일 없음"

echo ""
echo "📦 Commit & Push 시작..."
git config user.email "duels@jbfg.com"
git config user.name "Claude Scheduler"
git config credential.helper osxkeychain

git add index.html history.json
git commit -m "🍽️ $(date +%Y-%m-%d) 점심 맛집 추천 업데이트"

echo ""
echo "🚀 GitHub에 push 중..."
git push origin master

if [ $? -eq 0 ]; then
  echo ""
  echo "✅ Push 완료! Netlify 배포가 시작됩니다 (약 1~2분 소요)"
  echo "🌐 https://jblunch.netlify.app"
else
  echo ""
  echo "❌ Push 실패. 위의 오류 메시지를 확인하세요."
fi

echo ""
read -p "엔터를 누르면 창이 닫힙니다..."

#!/bin/bash
# Netlify 수동 재배포 스크립트
# 터미널에서 실행: bash netlify-deploy.sh

NETLIFY_TOKEN="newtech"
SITE_ID="jblunch"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "📦 index.html 압축 중..."
zip -j /tmp/netlify_deploy.zip "$SCRIPT_DIR/index.html"

echo "🚀 Netlify 배포 중..."
RESULT=$(curl -s -X POST \
  -H "Authorization: Bearer $NETLIFY_TOKEN" \
  -H "Content-Type: application/zip" \
  --data-binary @/tmp/netlify_deploy.zip \
  "https://api.netlify.com/api/v1/sites/${SITE_ID}/deploys")

STATE=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('state','error'))" 2>/dev/null)

if [ "$STATE" = "ready" ] || [ "$STATE" = "uploaded" ] || [ "$STATE" = "processing" ]; then
  echo "✅ 배포 성공! https://jblunch.netlify.app"
else
  echo "❌ 배포 실패. 응답: $RESULT"
fi

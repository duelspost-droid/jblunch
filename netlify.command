#!/bin/bash
cd "$(dirname "$0")"

NETLIFY_TOKEN="nfp_BYTTTC9p4Yet93zNwUjF7duiCZL525emdd8e"
SITE_ID="jblunch.netlify.app"

echo "📦 index.html 압축 중..."
zip -j /tmp/netlify_deploy.zip index.html _headers && echo "✅ 압축 완료"

echo ""
echo "🚀 Netlify에 직접 배포 중..."
RESULT=$(curl -s -X POST \
  -H "Authorization: Bearer $NETLIFY_TOKEN" \
  -H "Content-Type: application/zip" \
  --data-binary @/tmp/netlify_deploy.zip \
  "https://api.netlify.com/api/v1/sites/${SITE_ID}/deploys")

STATE=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('state','error'))" 2>/dev/null)

if [ "$STATE" = "ready" ] || [ "$STATE" = "uploaded" ] || [ "$STATE" = "processing" ]; then
  echo "✅ 배포 성공! 상태: $STATE"
  echo "🌐 https://jblunch.netlify.app"
else
  echo "❌ 배포 실패. 응답:"
  echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print('오류:', d.get('error_message', d.get('message', str(d)[:200])))" 2>/dev/null || echo "${RESULT:0:300}"
fi

echo ""
read -p "엔터를 누르면 창이 닫힙니다..."

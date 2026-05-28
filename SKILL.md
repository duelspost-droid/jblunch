---
name: lunch-restaurant-yeouinaeru
description: 매일 오전 11시 여의나루로 77 근처 점심 맛집 5곳 추천
---


오늘 점심 메뉴를 고르는 데 도움이 되도록, 서울 여의나루로 77 (영등포구 여의도동) 근처에서 점심을 먹을 수 있는 음식점 5곳을 추천해줘.

웹 검색을 활용해서 현재 인기 있거나 평점이 좋은 음식점을 찾아줘. 각 음식점에 대해 다음 정보를 간단히 요약해줘:

1. **음식점 이름**
2. **음식 종류** (한식, 일식, 중식 등)
3. **특징/추천 메뉴** (1~2줄)
4. **가격대** (저렴 / 보통 / 비쌈)
5. **도보 거리** (여의나루로 77 기준 대략적인 거리)

마지막에 오늘의 날씨나 요일에 맞는 짧은 한마디 추천 코멘트도 붙여줘.

결과를 정리한 후, macOS Mail 앱(computer-use)을 사용해서 아래 두 주소로 이메일을 보내줘.
- 받는 사람: duels@jbfg.com
- 추가 받는 사람(CC): duels@hanmail.net
- 제목: 🍽️ 오늘의 여의나루 점심 맛집 추천
- 본문: 위에서 정리한 음식점 5곳 추천 내용 전체 (한국어로)

이메일 전송 후, 오늘 추천한 5개 맛집 정보를 아래 JSON 파일에 누적 저장해줘:
/Users/hk/Documents/Claude/Scheduled/lunch-restaurant-yeouinaeru/history.json

파일이 없으면 새로 만들고, 있으면 기존 recommendations 배열에 오늘 항목을 추가해줘.
각 음식점 항목의 JSON 형식:
{
  "date": "YYYY-MM-DD",
  "restaurants": [
    {
      "id": "날짜+순번 예: 20260527-1",
      "name": "음식점 이름",
      "cuisine": "음식 종류",
      "feature": "특징/추천 메뉴",
      "price": "가격대",
      "distance": "도보 거리"
    }
  ]
}

JSON 저장 후, 아래 두 곳을 함께 업데이트해줘:

## A. Cowork 아티팩트 업데이트
아티팩트 ID "yeouinaeru-lunch-tracker" 를 업데이트해줘.
history.json 파일 전체를 읽어서 아티팩트 HTML 안의
  const HISTORY_DATA = [...];
줄을 새 데이터(JSON.stringify 형태)로 교체한 뒤 update_artifact를 호출해줘.

## B. Netlify 웹페이지 자동 업데이트
Netlify 사이트 URL: https://jblunch.netlify.app
Netlify 사이트 ID: jblunch

아래 순서로 index.html 파일을 업데이트하고 Netlify에 재배포해줘:

1. /Users/hk/Documents/Claude/Scheduled/lunch-restaurant-yeouinaeru/index.html 파일을 읽어서
   `const HISTORY_DATA = [...];` 줄을 새 history.json 데이터로 교체 후 저장

2. 아래 bash 명령으로 Netlify API에 재배포:
```bash
NETLIFY_TOKEN="newtech"
SITE_ID="jblunch"
cd /sessions/determined-dazzling-tesla/mnt/lunch-restaurant-yeouinaeru
zip -j /tmp/netlify_deploy.zip index.html
curl -s -X POST \
  -H "Authorization: Bearer $NETLIFY_TOKEN" \
  -H "Content-Type: application/zip" \
  --data-binary @/tmp/netlify_deploy.zip \
  "https://api.netlify.com/api/v1/sites/${SITE_ID}/deploys"
```

※ NETLIFY_PERSONAL_ACCESS_TOKEN 값은 아래에서 발급:
   https://app.netlify.com/user/applications#personal-access-tokens
   토큰을 발급받으면 이 SKILL.md의 <NETLIFY_PERSONAL_ACCESS_TOKEN> 부분을 실제 값으로 교체해줘.
   토큰이 없으면 Netlify 재배포 단계는 건너뛰어도 됨.

모든 작업 완료 후 한국어로 완료 메시지를 표시해줘.

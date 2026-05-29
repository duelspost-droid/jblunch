#!/usr/bin/env python3
"""
여의나루 점심 맛집 추천 생성기
GitHub Actions에서 매일 오전 11시(KST) 자동 실행
"""

import anthropic
import json
import re
import os
import sys
from datetime import datetime, timezone, timedelta


def get_today_kst():
    kst = timezone(timedelta(hours=9))
    return datetime.now(kst).strftime("%Y-%m-%d")


def call_claude(client, prompt):
    """Claude API 호출 (web search 포함, agentic loop)."""
    messages = [{"role": "user", "content": prompt}]
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}]

    for iteration in range(10):
        response = client.messages.create(
            model="claude-opus-4-5",
            max_tokens=4000,
            tools=tools,
            messages=messages,
        )

        print(f"  [iteration {iteration + 1}] stop_reason={response.stop_reason}")

        if response.stop_reason == "end_turn":
            text_parts = [
                block.text
                for block in response.content
                if hasattr(block, "text") and block.text
            ]
            return "\n".join(text_parts)

        elif response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    query = (
                        block.input.get("query", "...")
                        if hasattr(block, "input")
                        else "..."
                    )
                    print(f"  🔍 웹 검색: {query}")
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": "",
                        }
                    )

            if tool_results:
                messages.append({"role": "user", "content": tool_results})

        else:
            # 예상치 못한 stop_reason — 텍스트라도 추출
            text_parts = [
                block.text
                for block in response.content
                if hasattr(block, "text") and block.text
            ]
            if text_parts:
                return "\n".join(text_parts)
            break

    return None


def extract_json(text):
    """응답 텍스트에서 JSON 블록 추출."""
    # ```json ... ``` 블록
    match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        return json.loads(match.group(1))

    # 날 JSON
    match = re.search(r'\{[^{}]*"restaurants"\s*:\s*\[.*?\]\s*\}', text, re.DOTALL)
    if match:
        return json.loads(match.group(0))

    return None


def update_history(new_entry, history_path="history.json"):
    """history.json 업데이트 (오늘 항목 맨 앞 추가)."""
    if os.path.exists(history_path):
        with open(history_path, "r", encoding="utf-8") as f:
            history = json.load(f)
    else:
        history = {"recommendations": []}

    # 같은 날짜 기존 항목 제거
    history["recommendations"] = [
        r for r in history["recommendations"] if r.get("date") != new_entry["date"]
    ]

    # 맨 앞에 추가
    history["recommendations"].insert(0, new_entry)

    with open(history_path, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    return history


def update_index_html(history, html_path="index.html"):
    """index.html의 HISTORY_DATA 교체."""
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    new_data = json.dumps(history["recommendations"], ensure_ascii=False)
    html_new = re.sub(
        r"const HISTORY_DATA = \[.*?\];",
        f"const HISTORY_DATA = {new_data};",
        html,
        flags=re.DOTALL,
    )

    if html_new == html:
        print("⚠️  index.html에서 HISTORY_DATA를 찾지 못했습니다.")
    else:
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html_new)
        print("✅ index.html 업데이트 완료")


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("❌ ANTHROPIC_API_KEY 환경 변수가 설정되지 않았습니다.")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    today = get_today_kst()
    date_compact = today.replace("-", "")

    print(f"📅 오늘 날짜 (KST): {today}")

    prompt = f"""오늘({today}) 점심 메뉴를 고르는 데 도움이 되도록, 서울 여의나루로 77 (영등포구 여의도동) 근처에서 점심을 먹을 수 있는 음식점 5곳을 추천해줘.

웹 검색을 활용해서 현재 인기 있거나 평점이 좋은 음식점을 찾아줘. 각 음식점에 대해:
1. 음식점 이름
2. 음식 종류 (한식, 일식, 중식 등)
3. 특징/추천 메뉴 (1~2줄)
4. 가격대 (저렴 / 보통 / 비쌈)
5. 도보 거리 (여의나루로 77 기준)

반드시 응답 마지막에 아래 형식의 JSON 블록을 포함해줘:

```json
{{
  "date": "{today}",
  "restaurants": [
    {{"id": "{date_compact}-1", "name": "음식점 이름", "cuisine": "음식 종류", "feature": "특징/추천 메뉴", "price": "저렴/보통/비쌈", "distance": "도보 N분"}},
    {{"id": "{date_compact}-2", "name": "음식점 이름", "cuisine": "음식 종류", "feature": "특징/추천 메뉴", "price": "저렴/보통/비쌈", "distance": "도보 N분"}},
    {{"id": "{date_compact}-3", "name": "음식점 이름", "cuisine": "음식 종류", "feature": "특징/추천 메뉴", "price": "저렴/보통/비쌈", "distance": "도보 N분"}},
    {{"id": "{date_compact}-4", "name": "음식점 이름", "cuisine": "음식 종류", "feature": "특징/추천 메뉴", "price": "저렴/보통/비쌈", "distance": "도보 N분"}},
    {{"id": "{date_compact}-5", "name": "음식점 이름", "cuisine": "음식 종류", "feature": "특징/추천 메뉴", "price": "저렴/보통/비쌈", "distance": "도보 N분"}}
  ]
}}
```"""

    print("🔍 맛집 검색 및 추천 생성 중...")
    text = call_claude(client, prompt)

    if not text:
        print("❌ Claude 응답을 받지 못했습니다.")
        sys.exit(1)

    print("📝 JSON 파싱 중...")
    new_entry = extract_json(text)

    if not new_entry:
        print("❌ JSON 파싱 실패. 응답 내용:")
        print(text[:2000])
        sys.exit(1)

    # date / id 보정
    new_entry["date"] = today
    for idx, r in enumerate(new_entry.get("restaurants", [])):
        r["id"] = f"{date_compact}-{idx + 1}"

    print(f"✅ {len(new_entry.get('restaurants', []))}개 음식점 추천 완료")
    for r in new_entry.get("restaurants", []):
        print(f"  • {r['name']} ({r['cuisine']}) | {r['price']} | {r['distance']}")

    print("\n📂 history.json 업데이트 중...")
    history = update_history(new_entry)
    print(f"✅ 총 {len(history['recommendations'])}일치 데이터 저장됨")

    print("\n🌐 index.html 업데이트 중...")
    update_index_html(history)

    print("\n✨ 완료!")


if __name__ == "__main__":
    main()

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
import smtplib
import time
import urllib.request
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone, timedelta

SB_URL = "https://nrdapzgtibbusvoaceuh.supabase.co"
SB_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im5yZGFwemd0aWJidXN2b2FjZXVoIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzk5MDM2MTEsImV4cCI6MjA5NTQ3OTYxMX0.hzAnNaPdx1AaswsY1hkzc98aRSD2PXUjVi_mLl3bzcM"


def get_today_kst():
    kst = timezone(timedelta(hours=9))
    return datetime.now(kst).strftime("%Y-%m-%d")


def get_today_preference(today):
    """Supabase에서 오늘 저장된 컨디션/선호도 조회."""
    try:
        url = f"{SB_URL}/rest/v1/daily_preference?date=eq.{today}&select=preference"
        req = urllib.request.Request(url, headers={
            "apikey": SB_KEY,
            "Authorization": f"Bearer {SB_KEY}"
        })
        with urllib.request.urlopen(req) as r:
            rows = json.loads(r.read())
        if rows:
            pref = rows[0]["preference"]
            print(f"✅ 오늘 컨디션 발견: {pref}")
            return pref
    except Exception as e:
        print(f"⚠️  컨디션 조회 실패 (무시): {e}")
    return None


def call_claude(client, prompt):
    """Claude API 호출 (web search 포함, agentic loop). Rate limit 시 자동 재시도."""
    messages = [{"role": "user", "content": prompt}]
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}]

    for iteration in range(10):
        # Rate limit 재시도 (최대 3회, 지수 백오프)
        for attempt in range(3):
            try:
                response = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=8000,
                    tools=tools,
                    messages=messages,
                )
                break
            except anthropic.RateLimitError:
                wait = 65 * (attempt + 1)
                print(f"  ⏳ Rate limit — {wait}초 후 재시도 ({attempt+1}/3)...")
                time.sleep(wait)
        else:
            print("❌ Rate limit 재시도 초과")
            return None

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
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # 중괄호 깊이 기반으로 JSON 블록 추출
    start = text.find('{')
    if start == -1:
        return None
    depth = 0
    for i, c in enumerate(text[start:], start):
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i+1])
                except json.JSONDecodeError:
                    break
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


def send_email(restaurants, today, full_text, comment=""):
    """Gmail SMTP로 점심 추천 이메일 발송."""
    gmail_user = os.environ.get("GMAIL_USER")
    gmail_password = os.environ.get("GMAIL_APP_PASSWORD")
    if not gmail_user or not gmail_password:
        print("⚠️  GMAIL_USER / GMAIL_APP_PASSWORD 미설정 — 이메일 생략")
        return

    # 요일 한국어
    weekdays = ["월", "화", "수", "목", "금", "토", "일"]
    kst = timezone(timedelta(hours=9))
    dt = datetime.now(kst)
    wd = weekdays[dt.weekday()]
    date_label = f"{dt.year}년 {dt.month}월 {dt.day}일 ({wd}요일)"

    lines = [f"🍽️ 오늘의 여의나루 점심 맛집 추천 ({date_label})\n",
             "서울 영등포구 여의나루로 77 근처 추천 음식점 5곳입니다.\n",
             "─" * 40]
    for i, r in enumerate(restaurants, 1):
        lines += [
            f"\n{i}. {r['name']}",
            f"   • 음식 종류: {r['cuisine']}",
            f"   • 특징/추천 메뉴: {r['feature']}",
            f"   • 가격대: {r['price']}",
            f"   • 도보 거리: {r['distance']}",
        ]
    weather_line = comment if comment else "오늘도 맛있는 점심 되세요!"
    lines += ["\n" + "─" * 40,
              f"\n🌤️ {weather_line}",
              "\n—\nClaude AI 자동 발송"]
    body = "\n".join(lines)

    msg = MIMEMultipart()
    msg["From"] = gmail_user
    msg["To"] = "duels@jbfg.com"
    msg["Cc"] = "duels@hanmail.net"
    msg["Subject"] = "🍽️ 오늘의 여의나루 점심 맛집 추천"
    msg.attach(MIMEText(body, "plain", "utf-8"))

    recipients = ["duels@jbfg.com", "duels@hanmail.net"]
    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(gmail_user, gmail_password)
            server.sendmail(gmail_user, recipients, msg.as_string())
        print(f"✅ 이메일 발송 완료 → {', '.join(recipients)}")
    except Exception as e:
        print(f"⚠️  이메일 발송 실패: {e}")


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


CONDITIONS = ["해장 필요", "매콤하게", "가볍게", "든든하게", "일식", "한식", "고기", "혼밥"]
MEAL_CTX = {
    "점심": "점심 식사로 좋은 음식점",
    "저녁": "저녁 식사로 좋은 음식점 (분위기 있거나 회식·모임에도 좋은 곳 포함)",
    "술집": "술 한잔 하기 좋은 술집 (이자카야·호프·포차·와인바·안주 맛집)",
}
MEAL_IDP = {"점심": "", "저녁": "D", "술집": "B"}


def generate_meal(client, today, date_compact, meal, with_conditions):
    """한 시간대(점심/저녁/술집) 추천 생성. {comment, restaurants, by_condition?} 반환."""
    ctx = MEAL_CTX[meal]
    idp = MEAL_IDP[meal]
    id_prefix = date_compact + (f"-{idp}" if idp else "")

    if with_conditions:
        cond_list = " / ".join(CONDITIONS)
        weather_line = "오늘 서울 여의도 날씨를 웹 검색으로 확인하고, " if meal == "점심" else ""
        prompt = f"""{weather_line}여의나루로 77 (영등포구 여의도동) 근처에서 오늘 {meal} 자리로 갈 만한 {ctx}을 추천해줘.

웹 검색으로 여의도/여의나루 인기 가게를 조사한 후 아래 JSON을 응답 마지막에 포함해줘.
- restaurants: 시간대({meal})에 어울리는 기본 추천 5곳
- by_condition: {cond_list} — 각 컨디션에 맞는 5곳 (컨디션마다 다른 조합)
- comment: {"날씨를 반영한 한마디" if meal == "점심" else f"{meal} 추천 한마디"}

각 가게 필드: name, cuisine, feature, price(저렴/보통/비쌈), distance(도보 N분)

```json
{{"comment":"...","restaurants":[...],"by_condition":{{"해장 필요":[...],"매콤하게":[...],"가볍게":[...],"든든하게":[...],"일식":[...],"한식":[...],"고기":[...],"혼밥":[...]}}}}
```"""
    else:
        prompt = f"""여의나루로 77 (영등포구 여의도동) 근처에서 오늘 {meal} 가기 좋은 {ctx} 5곳을 추천해줘.
웹 검색으로 여의도/여의나루 인기 가게를 조사한 후 아래 JSON을 응답 마지막에 포함해줘.
각 가게 필드: name, cuisine, feature, price(저렴/보통/비쌈), distance(도보 N분)

```json
{{"comment":"{meal} 추천 한마디","restaurants":[...5곳]}}
```"""

    print(f"🔍 [{meal}] 생성 중...")
    text = call_claude(client, prompt)
    entry = extract_json(text) if text else None
    if not entry:
        print(f"❌ [{meal}] JSON 파싱 실패")
        return None, text

    # id 보정
    for idx, r in enumerate(entry.get("restaurants", [])):
        r["id"] = f"{id_prefix}-{idx + 1}"
    for cond, rlist in entry.get("by_condition", {}).items():
        cp = cond[:2].replace(" ", "")
        for idx, r in enumerate(rlist):
            r["id"] = f"{id_prefix}-{cp}-{idx + 1}"

    n = len(entry.get("restaurants", []))
    nc = len(entry.get("by_condition", {}))
    print(f"✅ [{meal}] 기본 {n}곳" + (f" + 컨디션 {nc}종" if nc else ""))
    return entry, text


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("❌ ANTHROPIC_API_KEY 환경 변수가 설정되지 않았습니다.")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    today = get_today_kst()
    date_compact = today.replace("-", "")

    print(f"📅 오늘 날짜 (KST): {today}")

    # 점심(날씨+컨디션), 저녁(컨디션), 술집(기본만)
    lunch, lunch_text = generate_meal(client, today, date_compact, "점심", True)
    if not lunch:
        print("❌ 점심 생성 실패 — 중단")
        sys.exit(1)
    dinner, _ = generate_meal(client, today, date_compact, "저녁", True)
    bar, _ = generate_meal(client, today, date_compact, "술집", False)

    # 엔트리: 점심은 최상위(이메일/카카오 호환), 저녁·술집은 meals에 저장
    new_entry = {
        "date": today,
        "comment": lunch.get("comment", ""),
        "restaurants": lunch.get("restaurants", []),
        "by_condition": lunch.get("by_condition", {}),
        "meals": {},
    }
    if dinner:
        new_entry["meals"]["저녁"] = {
            "restaurants": dinner.get("restaurants", []),
            "by_condition": dinner.get("by_condition", {}),
        }
    if bar:
        new_entry["meals"]["술집"] = {"restaurants": bar.get("restaurants", [])}

    text = lunch_text  # 이메일 본문 참고용

    print("\n📂 history.json 업데이트 중...")
    history = update_history(new_entry)
    print(f"✅ 총 {len(history['recommendations'])}일치 데이터 저장됨")

    print("\n🌐 index.html 업데이트 중...")
    update_index_html(history)

    print("\n📧 이메일 발송 중...")
    send_email(new_entry.get("restaurants", []), today, text, new_entry.get("comment", ""))

    print("\n✨ 완료!")


if __name__ == "__main__":
    main()

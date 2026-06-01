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
    """Claude API 호출 (web search 포함, agentic loop)."""
    messages = [{"role": "user", "content": prompt}]
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}]

    for iteration in range(10):
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=8000,
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


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("❌ ANTHROPIC_API_KEY 환경 변수가 설정되지 않았습니다.")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    today = get_today_kst()
    date_compact = today.replace("-", "")

    print(f"📅 오늘 날짜 (KST): {today}")

    CONDITIONS = ["해장 필요", "매콤하게", "가볍게", "든든하게", "일식", "한식", "고기", "혼밥"]
    cond_list = " / ".join(CONDITIONS)

    prompt = f"""오늘({today}) 서울 여의도 날씨를 웹 검색으로 확인하고, 여의나루로 77 (영등포구 여의도동) 근처 음식점을 추천해줘.

웹 검색으로 여의도/여의나루 인기 맛집을 조사한 후 아래 JSON을 응답 마지막에 포함해줘.

- restaurants: 날씨·요일 기반 기본 추천 5곳
- by_condition: {cond_list} — 각 컨디션에 맞는 5곳 (컨디션마다 다른 조합)
- comment: 날씨를 반영한 한마디

각 음식점 필드: id({date_compact}-번호), name, cuisine, feature, price(저렴/보통/비쌈), distance

```json
{{"date":"{today}","comment":"...","restaurants":[...],"by_condition":{{"해장 필요":[...],"매콤하게":[...],"가볍게":[...],"든든하게":[...],"일식":[...],"한식":[...],"고기":[...],"혼밥":[...]}}}}
```"""

    print("🔍 날씨 + 전체 컨디션별 맛집 생성 중...")
    text = call_claude(client, prompt)

    if not text:
        print("❌ Claude 응답을 받지 못했습니다.")
        sys.exit(1)

    print("📝 JSON 파싱 중...")
    new_entry = extract_json(text)

    # JSON이 없으면 2차 호출로 JSON만 추출 요청
    if not new_entry:
        print("⚠️  JSON 없음 — 2차 JSON 추출 요청 중...")
        cond_list = " / ".join(CONDITIONS)
        fallback_prompt = f"""아래 정보를 바탕으로 반드시 JSON만 출력해줘. 다른 설명 없이 JSON 블록만.

앞서 조사한 여의나루 맛집 정보:
{text[:3000]}

아래 형식으로 출력:
```json
{{"date":"{today}","comment":"날씨 한마디","restaurants":[{{"id":"{date_compact}-1","name":"이름","cuisine":"종류","feature":"특징","price":"저렴/보통/비쌈","distance":"도보N분"}},{{"id":"{date_compact}-2","name":"이름","cuisine":"종류","feature":"특징","price":"저렴/보통/비쌈","distance":"도보N분"}},{{"id":"{date_compact}-3","name":"이름","cuisine":"종류","feature":"특징","price":"저렴/보통/비쌈","distance":"도보N분"}},{{"id":"{date_compact}-4","name":"이름","cuisine":"종류","feature":"특징","price":"저렴/보통/비쌈","distance":"도보N분"}},{{"id":"{date_compact}-5","name":"이름","cuisine":"종류","feature":"특징","price":"저렴/보통/비쌈","distance":"도보N분"}}],"by_condition":{{"해장 필요":[5곳],"매콤하게":[5곳],"가볍게":[5곳],"든든하게":[5곳],"일식":[5곳],"한식":[5곳],"고기":[5곳],"혼밥":[5곳]}}}}
```"""
        text2 = call_claude(client, fallback_prompt)
        new_entry = extract_json(text2) if text2 else None

    if not new_entry:
        print("❌ JSON 파싱 최종 실패.")
        sys.exit(1)

    # date / id 보정
    new_entry["date"] = today
    for idx, r in enumerate(new_entry.get("restaurants", [])):
        r["id"] = f"{date_compact}-{idx + 1}"
    for cond, rlist in new_entry.get("by_condition", {}).items():
        prefix = cond[:2].replace(" ", "")
        for idx, r in enumerate(rlist):
            r["id"] = f"{date_compact}-{prefix}-{idx + 1}"

    print(f"✅ 기본 {len(new_entry.get('restaurants', []))}곳 + 컨디션별 {len(new_entry.get('by_condition', {}))}종류 추천 완료")
    for cond, rlist in new_entry.get("by_condition", {}).items():
        names = ", ".join(r['name'] for r in rlist[:3])
        print(f"  [{cond}] {names}…")

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

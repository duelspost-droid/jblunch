#!/usr/bin/env python3
"""
여의나루 점심 맛집 추천 생성기
GitHub Actions에서 매일 오전 11시(KST) 자동 실행
"""

import anthropic
import json
import math
import re
import os
import sys
import smtplib
import time
import urllib.request
import urllib.parse
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone, timedelta

SB_URL = "https://nrdapzgtibbusvoaceuh.supabase.co"
SB_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im5yZGFwemd0aWJidXN2b2FjZXVoIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzk5MDM2MTEsImV4cCI6MjA5NTQ3OTYxMX0.hzAnNaPdx1AaswsY1hkzc98aRSD2PXUjVi_mLl3bzcM"

# JB빌딩 (여의나루로 77) 좌표 — Kakao 주소 지오코딩으로 검증됨
JB_LAT = 37.5240914884765
JB_LNG = 126.927376521939

_coords_cache = {}


def parse_minutes(distance):
    """'도보 7분 (카카오) / 6분 (네이버)' → 최소 분. 미확인이면 None."""
    nums = re.findall(r"(\d+)\s*분", str(distance or ""))
    return min(int(n) for n in nums) if nums else None


def clean_name(name):
    """가게명에서 괄호 안 주소·층수, 대시로 붙인 메뉴 접미사 제거 → 지도 검색 정확도 향상.
    예: '로바(더현대서울 6층)' → '로바', '소몽 - 고등어덮밥' → '소몽'"""
    cleaned = re.sub(r"\s*[\(\[\{].*?[\)\]\}]\s*", " ", name)
    # ' - 메뉴', ' — 메뉴', ' · 메뉴' 같은 구분자 뒤 부가설명 제거
    cleaned = re.split(r"\s+[-–—·:]\s+", cleaned)[0]
    return cleaned.strip() or name.strip()


def haversine_m(lat1, lng1, lat2, lng2):
    """두 좌표 간 직선거리(미터)."""
    R = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


# 음식점이 아닌 카테고리 키워드 (검증에서 제외)
NON_FOOD_KEYWORDS = ["병원", "약국", "은행", "학원", "부동산", "미용", "마트", "편의점",
                     "주유소", "세탁", "사무", "오피스", "관공서", "PC방", "노래"]


def _is_food_category(category):
    """카테고리 문자열이 음식점/카페인지 판단."""
    if not category:
        return None  # 카테고리 정보 없음 → 판단 보류
    if any(kw in category for kw in NON_FOOD_KEYWORDS):
        return False
    return True


def fetch_kakao_candidates(kakao_key, radius=600, max_count=40):
    """Kakao 카테고리(반경) 검색으로 JB빌딩 근처 실제 음식점 → [(이름, 분, 카테고리)]."""
    if not kakao_key:
        return []
    out = []
    try:
        for page in range(1, 4):  # 최대 3페이지(45곳)
            url = (f"https://dapi.kakao.com/v2/local/search/category.json"
                   f"?category_group_code=FD6&x={JB_LNG}&y={JB_LAT}"
                   f"&radius={radius}&sort=distance&size=15&page={page}")
            req = urllib.request.Request(url, headers={"Authorization": f"KakaoAK {kakao_key}"})
            with urllib.request.urlopen(req, timeout=5) as r:
                data = json.loads(r.read())
            for d in data.get("documents", []):
                cat = d.get("category_name", "").replace("음식점 > ", "").split(" > ")[0]
                mins = max(1, round(int(d.get("distance", 0)) / 80))
                out.append((d["place_name"], mins, cat))
                if len(out) >= max_count:
                    return out
            if data.get("meta", {}).get("is_end"):
                break
    except Exception as e:
        print(f"  ⚠️  Kakao 후보 조회 실패: {e}")
    return out


# 네이버 후보 수집용 키워드 (반경 검색 미지원 → 키워드 검색 후 거리 필터)
NAVER_CAND_QUERIES = ["여의도 맛집", "여의나루 맛집", "여의도 점심", "여의도 일식", "여의도 한식", "여의도 술집"]


def fetch_naver_candidates(naver_id, naver_secret, radius=600, max_count=30):
    """Naver 지역검색은 반경 검색을 지원하지 않아, 여러 키워드로 검색 후
    JB빌딩 반경 내만 거리 필터링 → [(이름, 분, 카테고리)]."""
    if not (naver_id and naver_secret):
        return []
    out, seen = [], set()
    headers = {"X-Naver-Client-Id": naver_id, "X-Naver-Client-Secret": naver_secret}
    for kw in NAVER_CAND_QUERIES:
        try:
            q = urllib.parse.quote(kw)
            url = f"https://openapi.naver.com/v1/search/local.json?query={q}&display=5&sort=comment"
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as r:
                items = json.loads(r.read()).get("items", [])
            for it in items:
                name = re.sub(r"<[^>]+>", "", it.get("title", "")).strip()
                if not name or name in seen:
                    continue
                try:
                    lng, lat = float(it["mapx"]) / 1e7, float(it["mapy"]) / 1e7
                except (KeyError, ValueError):
                    continue
                dist_m = haversine_m(JB_LAT, JB_LNG, lat, lng)
                if dist_m > radius * 1.4:   # 반경 밖 제외 (도로보정 감안 여유)
                    continue
                seen.add(name)
                cat = (it.get("category", "").split(">")[-1]).strip()
                out.append((name, max(1, round(dist_m * 1.35 / 80)), cat))
                if len(out) >= max_count:
                    return out
        except Exception as e:
            print(f"  ⚠️  Naver 후보 조회 실패({kw}): {e}")
    return out


_candidates_cache = None


def fetch_nearby_candidates(kakao_key, naver_id=None, naver_secret=None, max_count=45):
    """Kakao(반경) + Naver(키워드+거리필터) 후보를 합쳐 중복 제거 → 문자열 리스트.
    끼니마다 동일하므로 1회만 조회 후 캐싱."""
    global _candidates_cache
    if _candidates_cache is not None:
        return _candidates_cache
    merged, seen = [], set()
    for name, mins, cat in fetch_kakao_candidates(kakao_key) + fetch_naver_candidates(naver_id, naver_secret):
        key = name.replace(" ", "")
        if key in seen:
            continue
        seen.add(key)
        merged.append(f"{name}({cat}, 도보 {mins}분)" if cat else f"{name}(도보 {mins}분)")
        if len(merged) >= max_count:
            break
    _candidates_cache = merged
    print(f"  🗂️  근처 실제 음식점 후보 {len(merged)}곳 확보 (Kakao+Naver)")
    return merged


def get_kakao_place(name, kakao_key):
    """Kakao Local API로 장소 검색 → (lat, lng, category). 실패 시 (None, None, None)."""
    cache_key = f"kakao:{name}"
    if cache_key in _coords_cache:
        return _coords_cache[cache_key]
    query = urllib.parse.quote(f"{clean_name(name)} 여의도")
    url = f"https://dapi.kakao.com/v2/local/search/keyword.json?query={query}&size=1"
    try:
        req = urllib.request.Request(url, headers={"Authorization": f"KakaoAK {kakao_key}"})
        with urllib.request.urlopen(req, timeout=5) as r:
            docs = json.loads(r.read()).get("documents", [])
        if docs:
            d = docs[0]
            lat, lng = float(d["y"]), float(d["x"])
            # FD6=음식점, CE7=카페. 그 외 group_code면 음식점 아님
            gc = d.get("category_group_code", "")
            cat = d.get("category_name", "")
            if gc and gc not in ("FD6", "CE7"):
                cat = "비음식점:" + cat
            _coords_cache[cache_key] = (lat, lng, cat)
            return lat, lng, cat
    except Exception as e:
        pass
    _coords_cache[cache_key] = (None, None, None)
    return None, None, None


def get_naver_place(name, naver_id, naver_secret):
    """Naver 지역검색 API로 장소 검색 → (lat, lng, category). 실패 시 (None, None, None)."""
    cache_key = f"naver:{name}"
    if cache_key in _coords_cache:
        return _coords_cache[cache_key]
    query = urllib.parse.quote(f"{clean_name(name)} 여의도")
    url = f"https://openapi.naver.com/v1/search/local.json?query={query}&display=1"
    try:
        req = urllib.request.Request(url, headers={
            "X-Naver-Client-Id": naver_id,
            "X-Naver-Client-Secret": naver_secret
        })
        with urllib.request.urlopen(req, timeout=5) as r:
            items = json.loads(r.read()).get("items", [])
        if items:
            it = items[0]
            lng, lat = float(it["mapx"]) / 1e7, float(it["mapy"]) / 1e7
            cat = it.get("category", "")
            _coords_cache[cache_key] = (lat, lng, cat)
            return lat, lng, cat
    except Exception as e:
        pass
    _coords_cache[cache_key] = (None, None, None)
    return None, None, None


def verify_and_enrich(restaurants, kakao_key, naver_id=None, naver_secret=None):
    """Kakao/Naver로 실존 여부 확인 + 거리 계산.
    검증 실패해도 제거하지 않고 verified=False 플래그만 부여
    (프론트엔드에서 '지도없음'으로 표시 → 사용자가 직접 판단).
    각 가게에 r["verified"]=True/False 추가. 반환: (restaurants, unverified_names)"""
    unverified = []

    for r in restaurants:
        r["name"] = clean_name(r["name"])
        distances = {}
        food_votes = []   # 카테고리 판정 결과 (True/False/None)

        # Kakao
        klat, klng, kcat = get_kakao_place(r["name"], kakao_key)
        if klat and klng:
            distances["카카오"] = max(1, round(haversine_m(JB_LAT, JB_LNG, klat, klng) * 1.35 / 80))
            food_votes.append(_is_food_category(kcat))

        # Naver
        if naver_id and naver_secret:
            nlat, nlng, ncat = get_naver_place(r["name"], naver_id, naver_secret)
            if nlat and nlng:
                distances["네이버"] = max(1, round(haversine_m(JB_LAT, JB_LNG, nlat, nlng) * 1.35 / 80))
                food_votes.append(_is_food_category(ncat))

        # 검증 실패 판정: 어디서도 못 찾음 OR 모든 소스가 비음식점
        is_food = not (food_votes and all(v is False for v in food_votes))
        if not distances or not is_food:
            r["verified"] = False
            r["distance"] = "거리 미확인"
            unverified.append(r["name"])
            print(f"  ⚠️  {r['name']}: 지도없음")
            continue

        # 검증 성공 — 거리 표기 (찾은 소스만)
        r["verified"] = True
        if "카카오" in distances and "네이버" in distances:
            r["distance"] = f"도보 {distances['카카오']}분 (카카오) / {distances['네이버']}분 (네이버)"
        elif "카카오" in distances:
            r["distance"] = f"도보 {distances['카카오']}분 (카카오)"
        else:
            r["distance"] = f"도보 {distances['네이버']}분 (네이버)"
        print(f"  ✅ {r['name']}: {r['distance']}")

    if unverified:
        print(f"  ⚠️  지도없음 ({len(unverified)}): {', '.join(unverified)}")

    return restaurants, unverified


def get_today_kst():
    kst = timezone(timedelta(hours=9))
    return datetime.now(kst).strftime("%Y-%m-%d")


def fetch_weather():
    """wttr.in으로 서울 여의도 날씨 (무료, 키 불필요). 실패 시 ''."""
    try:
        req = urllib.request.Request(
            "https://wttr.in/Yeouido?format=j1&lang=ko",
            headers={"User-Agent": "curl/8", "Accept-Language": "ko"})
        with urllib.request.urlopen(req, timeout=6) as r:
            data = json.loads(r.read())
        cur = data["current_condition"][0]
        today = data["weather"][0]
        desc = ""
        if cur.get("lang_ko"):
            desc = cur["lang_ko"][0]["value"]
        if not desc:
            desc = cur.get("weatherDesc", [{}])[0].get("value", "")
        temp = cur.get("temp_C", "")
        tmin = today.get("mintempC", "")
        tmax = today.get("maxtempC", "")
        rain = today.get("hourly", [{}])[0].get("chanceofrain", "0")
        line = f"{desc} {temp}°C (최저 {tmin}° / 최고 {tmax}°)"
        if rain and int(rain) >= 40:
            line += f", 강수확률 {rain}%☔"
        print(f"🌤️  날씨: {line}")
        return line
    except Exception as e:
        print(f"⚠️  날씨 조회 실패 (무시): {e}")
        return ""


def fetch_jb_news(naver_id, naver_secret, count=3):
    """네이버 뉴스 검색으로 JB금융그룹 최신 기사 → [{title, link}]. 실패 시 []."""
    if not (naver_id and naver_secret):
        return []
    try:
        q = urllib.parse.quote("JB금융지주")
        url = f"https://openapi.naver.com/v1/search/news.json?query={q}&display={count}&sort=date"
        req = urllib.request.Request(url, headers={
            "X-Naver-Client-Id": naver_id,
            "X-Naver-Client-Secret": naver_secret,
        })
        with urllib.request.urlopen(req, timeout=6) as r:
            items = json.loads(r.read()).get("items", [])
        news = []
        for it in items[:count]:
            title = re.sub(r"<[^>]+>", "", it.get("title", "")).replace("&quot;", '"').replace("&amp;", "&").strip()
            link = it.get("originallink") or it.get("link", "")
            if title:
                news.append({"title": title, "link": link})
        print(f"📰 JB금융 뉴스 {len(news)}건")
        return news
    except Exception as e:
        print(f"⚠️  뉴스 조회 실패 (무시): {e}")
        return []


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


def call_claude(client, prompt, use_web=True):
    """Claude API 호출 (web search 옵션, agentic loop). Rate limit 시 자동 재시도."""
    messages = [{"role": "user", "content": prompt}]
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}] if use_web else []

    for iteration in range(10):
        # Rate limit 재시도 (최대 3회, 지수 백오프)
        for attempt in range(3):
            try:
                kwargs = dict(model="claude-haiku-4-5-20251001", max_tokens=8000, messages=messages)
                if tools:
                    kwargs["tools"] = tools
                response = client.messages.create(**kwargs)
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


def send_email(restaurants, today, full_text, comment="", weather="", news=None, extras=None):
    """Gmail SMTP로 점심 추천 이메일 발송 (날씨 + JB금융 뉴스 포함)."""
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

    lines = [f"🍽️ 오늘의 여의나루 점심 맛집 추천 ({date_label})\n"]
    if weather:
        lines.append(f"🌤️ 오늘 날씨: {weather}\n")
    lines += ["여의도 JB빌딩 근처 추천 음식점입니다.\n", "─" * 40]
    for i, r in enumerate(restaurants, 1):
        lines += [
            f"\n{i}. {r['name']}",
            f"   • 음식 종류: {r['cuisine']}",
            f"   • 특징/추천 메뉴: {r['feature']}",
            f"   • 가격대: {r['price']}",
            f"   • 도보 거리: {r['distance']}",
        ]
    # 추가 추천 (근처유명/검색유명)
    TAG_LABEL = {"근처유명": "⭐ 10분내 유명", "검색유명": "🔍 검색 인기"}
    if extras:
        lines += ["\n" + "─" * 40, "\n✨ 이런 곳도 가볼 만해요"]
        for r in extras:
            tag = TAG_LABEL.get(r.get("tag"), "")
            lines += [f"\n• {r['name']} ({r['cuisine']}) {tag}",
                      f"   {r['feature']}  · {r['distance']}"]
    # JB금융 뉴스
    if news:
        lines += ["\n" + "─" * 40, "\n📰 JB금융그룹 주요 소식"]
        for n in news:
            lines.append(f"\n• {n['title']}\n   {n.get('link','')}")

    closing = comment if comment else "오늘도 맛있는 점심 되세요!"
    lines += ["\n" + "─" * 40,
              f"\n💬 {closing}",
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


CONDITIONS = ["해장 필요", "매콤하게", "가볍게", "든든하게", "일식", "한식", "고기", "혼밥", "와인", "임원"]
COND_HINT = {
    "와인": "와인바·와인 페어링 좋은 곳",
    "임원": "임원 모시거나 접대하기 좋은 격식 있는 고급 식당(프라이빗 룸 선호)",
}
MEAL_CTX = {
    "점심": "점심 식사로 좋은 음식점",
    "저녁": "저녁 식사로 좋은 음식점 (분위기 있거나 회식·모임에도 좋은 곳 포함)",
    "술집": "술 한잔 하기 좋은 술집 (이자카야·호프·포차·와인바·안주 맛집)",
}
MEAL_IDP = {"점심": "", "저녁": "D", "술집": "B"}


def generate_meal(client, today, date_compact, meal, with_conditions, kakao_key=None, naver_id=None, naver_secret=None):
    """한 시간대(점심/저녁/술집) 추천 생성. {comment, restaurants, by_condition?} 반환."""
    ctx = MEAL_CTX[meal]
    idp = MEAL_IDP[meal]
    id_prefix = date_compact + (f"-{idp}" if idp else "")

    # 하이브리드: JB빌딩 근처 실제 음식점 후보 목록 (Kakao 반경 + Naver 키워드)
    candidates = fetch_nearby_candidates(kakao_key, naver_id, naver_secret)
    cand_block = ""
    if candidates:
        cand_block = ("\n참고용 — JB빌딩 근처 실제 등록 음식점(거리 정확):\n"
                      + ", ".join(candidates)
                      + "\n위 목록을 우선 활용하되, 여기 없어도 네가 아는 여의도 유명 맛집은 추가해도 됨.\n")

    if with_conditions:
        cond_list = " / ".join(CONDITIONS)
        hint_line = " ".join(f'({c}={h})' for c, h in COND_HINT.items())
        weather_line = "오늘 서울 여의도 날씨를 웹 검색으로 확인하고, " if meal == "점심" else ""
        cond_json = ",".join(f'"{c}":[...]' for c in CONDITIONS)
        prompt = f"""{weather_line}여의도 JB빌딩(여의나루로 77, 영등포구 여의도동) 근처에서 오늘 {meal} 자리로 갈 만한 {ctx}을 추천해줘.
{cand_block}
웹 검색으로 여의도/여의나루 인기 가게를 조사한 후 아래 JSON을 응답 마지막에 포함해줘.
- restaurants: 가까운 검증 맛집 5곳 (도보 가까운 곳 우선, 음식 종류 다양하게)
- extras: 추가 추천 2곳(둘 다 반드시 도보 10분 이내 실제 가게) — 1곳 tag="근처유명"(가까운 유명), 1곳 tag="검색유명"(웹에서 평 좋은 유명). restaurants와 겹치지 않게
- by_condition: {cond_list} — 각 컨디션에 맞는 5곳 (컨디션마다 다른 조합). 참고: {hint_line}
- comment: {"날씨를 반영한 한마디" if meal == "점심" else f"{meal} 추천 한마디"}

각 가게 필드: name, cuisine, feature, price(저렴/보통/비쌈), distance(도보 N분) (extras는 추가로 tag)

```json
{{"comment":"...","restaurants":[...5곳],"extras":[{{...,"tag":"근처유명"}},{{...,"tag":"검색유명"}}],"by_condition":{{{cond_json}}}}}
```"""
    else:
        prompt = f"""여의도 JB빌딩(여의나루로 77, 영등포구 여의도동) 근처에서 오늘 {meal} 가기 좋은 {ctx} 5곳을 추천해줘.
{cand_block}
웹 검색으로 여의도/여의나루 인기 가게를 조사한 후 아래 JSON을 응답 마지막에 포함해줘.
- restaurants: 가까운 검증 맛집 5곳 (종류 다양하게, 가까운 순)
- extras: 추가 2곳(둘 다 반드시 도보 10분 이내) — 1곳 tag="근처유명"(가까운 유명), 1곳 tag="검색유명"(웹에서 평 좋은 유명)
각 가게 필드: name, cuisine, feature, price(저렴/보통/비쌈), distance(도보 N분) (extras는 tag 추가)

```json
{{"comment":"{meal} 추천 한마디","restaurants":[...5곳],"extras":[{{...,"tag":"근처유명"}},{{...,"tag":"검색유명"}}]}}
```"""

    print(f"🔍 [{meal}] 생성 중...")
    # 점심(날씨/신선도)·술집(실존 가게명 확보)은 웹검색 사용.
    # 저녁은 컨디션 10종으로 토큰이 커 지식 기반(rate limit 회피).
    text = call_claude(client, prompt, use_web=(meal in ("점심", "술집")))
    entry = extract_json(text) if text else None
    if not entry:
        print(f"❌ [{meal}] JSON 파싱 실패")
        return None, text

    # id 보정
    for idx, r in enumerate(entry.get("restaurants", [])):
        r["id"] = f"{id_prefix}-{idx + 1}"
    for idx, r in enumerate(entry.get("extras", [])):
        r["id"] = f"{id_prefix}-x{idx + 1}"
    for cond, rlist in entry.get("by_condition", {}).items():
        cp = cond[:2].replace(" ", "")
        for idx, r in enumerate(rlist):
            r["id"] = f"{id_prefix}-{cp}-{idx + 1}"

    # 거리 계산 + 검증 플래그 부여 (실패해도 제거하지 않음)
    if kakao_key or naver_id:
        print(f"  📐 [{meal}] 검증 + 거리 계산 중...")
        entry["restaurants"], _ = verify_and_enrich(entry.get("restaurants", []), kakao_key, naver_id, naver_secret)
        if entry.get("extras"):
            extras, _ = verify_and_enrich(entry["extras"], kakao_key, naver_id, naver_secret)
            # 추가 추천은 도보 10분 이내 + 검증된 곳만 (미확인/초과 제외)
            entry["extras"] = [r for r in extras
                               if r.get("verified") and (parse_minutes(r.get("distance")) or 99) <= 10]
            dropped = len(extras) - len(entry["extras"])
            if dropped:
                print(f"  ✂️  추가 추천 {dropped}곳 제외 (10분 초과/미확인)")
        for cond in list(entry.get("by_condition", {}).keys()):
            entry["by_condition"][cond], _ = verify_and_enrich(entry["by_condition"][cond], kakao_key, naver_id, naver_secret)

    n = len(entry.get("restaurants", []))
    n_ok = sum(1 for r in entry.get("restaurants", []) if r.get("verified"))
    nc = len(entry.get("by_condition", {}))
    print(f"✅ [{meal}] 기본 {n}곳(검증 {n_ok})" + (f" + 컨디션 {nc}종" if nc else ""))
    return entry, text


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("❌ ANTHROPIC_API_KEY 환경 변수가 설정되지 않았습니다.")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    kakao_key = os.environ.get("KAKAO_REST_API_KEY", "af04c6cff1c0c408283c25e84d5b481d")
    naver_id = os.environ.get("NAVER_CLIENT_ID")
    naver_secret = os.environ.get("NAVER_CLIENT_SECRET")
    today = get_today_kst()
    date_compact = today.replace("-", "")

    print(f"📅 오늘 날짜 (KST): {today}")
    if naver_id and naver_secret:
        print("✅ Naver API 설정됨 (Kakao + Naver 병렬 사용)")
    else:
        print("⚠️  Naver API 미설정 (Kakao만 사용)")

    # 점심(날씨+컨디션), 저녁(컨디션), 술집(기본만)
    lunch, lunch_text = generate_meal(client, today, date_compact, "점심", True, kakao_key, naver_id, naver_secret)
    if not lunch:
        print("❌ 점심 생성 실패 — 중단")
        sys.exit(1)
    time.sleep(70)  # rate limit(10k tokens/min) 회피 — 컨디션 10종이라 토큰 회복 여유 필요
    dinner, _ = generate_meal(client, today, date_compact, "저녁", True, kakao_key, naver_id, naver_secret)
    time.sleep(70)
    bar, _ = generate_meal(client, today, date_compact, "술집", False, kakao_key, naver_id, naver_secret)

    # 날씨 + JB금융 뉴스 (이메일/카카오 첨부용)
    print("\n🌤️  날씨/뉴스 수집 중...")
    weather = fetch_weather()
    news = fetch_jb_news(naver_id, naver_secret)

    # 엔트리: 점심은 최상위(이메일/카카오 호환), 저녁·술집은 meals에 저장
    new_entry = {
        "date": today,
        "comment": lunch.get("comment", ""),
        "weather": weather,
        "news": news,
        "restaurants": lunch.get("restaurants", []),
        "extras": lunch.get("extras", []),
        "by_condition": lunch.get("by_condition", {}),
        "meals": {},
    }
    if dinner:
        new_entry["meals"]["저녁"] = {
            "restaurants": dinner.get("restaurants", []),
            "extras": dinner.get("extras", []),
            "by_condition": dinner.get("by_condition", {}),
        }
    if bar:
        new_entry["meals"]["술집"] = {
            "restaurants": bar.get("restaurants", []),
            "extras": bar.get("extras", []),
        }

    text = lunch_text  # 이메일 본문 참고용

    print("\n📂 history.json 업데이트 중...")
    history = update_history(new_entry)
    print(f"✅ 총 {len(history['recommendations'])}일치 데이터 저장됨")

    print("\n🌐 index.html 업데이트 중...")
    update_index_html(history)

    print("\n📧 이메일 발송 중...")
    send_email(new_entry.get("restaurants", []), today, text, new_entry.get("comment", ""),
               weather=weather, news=news, extras=new_entry.get("extras", []))

    print("\n✨ 완료!")


if __name__ == "__main__":
    main()

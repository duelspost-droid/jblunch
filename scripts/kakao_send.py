#!/usr/bin/env python3
"""
카카오톡 '나에게 보내기'로 점심 추천 발송.
GitHub Actions에서 호출. 액세스 토큰 만료 시 리프레시 토큰으로 자동 갱신.
"""
import os
import sys
import json
import urllib.request
import urllib.parse

REST_API_KEY = os.environ.get("KAKAO_REST_API_KEY", "af04c6cff1c0c408283c25e84d5b481d")
CLIENT_SECRET = os.environ.get("KAKAO_CLIENT_SECRET", "")


def refresh_access_token(refresh_token):
    """리프레시 토큰으로 새 액세스 토큰 발급."""
    params = {
        "grant_type": "refresh_token",
        "client_id": REST_API_KEY,
        "refresh_token": refresh_token,
    }
    if CLIENT_SECRET:
        params["client_secret"] = CLIENT_SECRET
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(
        "https://kauth.kakao.com/oauth/token", data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


SITE_URL = "https://duelspost-droid.github.io/jblunch/"


def send_to_me(access_token, restaurants, today, comment, weather="", news=None, extras=None, message=""):
    """나에게 보내기 (메모) API로 점심 추천 발송. 날씨 + 멘트 + JB금융 뉴스 포함."""
    lines = [f"🍽️ 오늘의 여의나루 점심 추천 ({today})", ""]
    if weather:
        lines.append(f"🌤️ 날씨: {weather}")
    if message:
        lines += [f"💬 {message}", ""]
    elif comment:
        lines += [f"🌤️ {comment}", ""]
    else:
        lines.append("")
    for i, r in enumerate(restaurants[:5], 1):
        lines.append(f"{i}. {r['name']} ({r['cuisine']}) · {r['price']} · {r['distance']}")
    # 추가 추천
    TAG_LABEL = {"근처유명": "⭐", "검색유명": "🔍"}
    if extras:
        lines += ["", "✨ 이런 곳도:"]
        for r in extras:
            lines.append(f"{TAG_LABEL.get(r.get('tag'),'•')} {r['name']} ({r['cuisine']}) · {r['distance']}")
    # JB금융 뉴스
    if news:
        lines += ["", "📰 JB금융 소식:"]
        for n in news[:4]:
            lines.append(f"• {n['title']}")
    lines += ["", "👉 전체보기 / 컨디션별 추천:", SITE_URL]
    text = "\n".join(lines)

    template = {
        "object_type": "text",
        "text": text,
        "link": {"web_url": SITE_URL, "mobile_web_url": SITE_URL},
    }

    data = urllib.parse.urlencode({
        "template_object": json.dumps(template, ensure_ascii=False)
    }).encode()
    req = urllib.request.Request(
        "https://kapi.kakao.com/v2/api/talk/memo/default/send",
        data=data,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/x-www-form-urlencoded",
        })
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def main():
    refresh_token = os.environ.get("KAKAO_REFRESH_TOKEN")
    if not refresh_token:
        print("⚠️  KAKAO_REFRESH_TOKEN 미설정 — 카카오 발송 생략")
        return

    # history.json에서 오늘 추천 로드
    with open("history.json", encoding="utf-8") as f:
        history = json.load(f)
    if not history["recommendations"]:
        print("⚠️  추천 데이터 없음")
        return
    entry = sorted(history["recommendations"], key=lambda x: x["date"], reverse=True)[0]

    try:
        tokens = refresh_access_token(refresh_token)
        access_token = tokens["access_token"]
        print("✅ 액세스 토큰 갱신 완료")
    except Exception as e:
        print(f"❌ 토큰 갱신 실패: {e}")
        return

    try:
        send_to_me(access_token, entry["restaurants"], entry["date"],
                   entry.get("comment", ""), weather=entry.get("weather", ""),
                   news=entry.get("news", []), extras=entry.get("extras", []),
                   message=entry.get("message", ""))
        print("✅ 카카오톡 발송 완료")
    except Exception as e:
        body = e.read().decode() if hasattr(e, "read") else str(e)
        print(f"❌ 카카오 발송 실패: {body}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""배치 실패 알림 — GitHub Actions 의 'if: failure()' 단계에서 호출.
조용한 실패(빈 화면)를 막기 위해, 실패 시 관리자에게 이메일로 즉시 통지한다.
Gmail 시크릿(GMAIL_USER / GMAIL_APP_PASSWORD)을 재사용한다. (없으면 조용히 종료)
"""
import os
import sys
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timezone, timedelta


def main():
    user = os.environ.get("GMAIL_USER")
    pw = os.environ.get("GMAIL_APP_PASSWORD")
    if not user or not pw:
        print("⚠️ Gmail 시크릿 없음 — 실패 알림 건너뜀")
        return 0

    kst = datetime.now(timezone.utc) + timedelta(hours=9)
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY", "duelspost-droid/jblunch")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    failed_job = os.environ.get("FAILED_CONTEXT", "Daily Lunch Recommendation")
    run_url = f"{server}/{repo}/actions/runs/{run_id}" if run_id else f"{server}/{repo}/actions"

    body = (
        f"❌ jblunch 자동배치 실패\n\n"
        f"시각(KST): {kst:%Y-%m-%d %H:%M}\n"
        f"작업: {failed_job}\n"
        f"실행 로그: {run_url}\n\n"
        f"확인사항: ANTHROPIC 레이트리밋/키, Gmail·Kakao 시크릿, Supabase 테이블/함수.\n"
    )
    msg = MIMEText(body, _charset="utf-8")
    msg["Subject"] = f"[jblunch] ❌ 자동배치 실패 ({kst:%m-%d %H:%M} KST)"
    msg["From"] = user
    msg["To"] = user

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as s:
            s.starttls()
            s.login(user, pw)
            s.sendmail(user, [user], msg.as_string())
        print(f"📧 실패 알림 발송 → {user}")
    except Exception as e:
        # 알림 자체 실패가 워크플로를 더 망가뜨리지 않도록 흡수
        print(f"⚠️ 실패 알림 발송 자체 실패(무시): {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

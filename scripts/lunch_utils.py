#!/usr/bin/env python3
"""generate_lunch.py 에서 분리한 순수 헬퍼.

전역 상태(ORIGIN/PROFILE/DIVERSITY)·외부 API·파일에 의존하지 않는, 입력→출력만의
함수만 모았다. 단위 테스트가 쉽고 generate_lunch.py 본문을 가볍게 한다.
(상태에 의존하는 fmt_dist 등은 본문에 남겨둔다.)
"""
import re
import math
import json


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


def _balanced_json(text, start):
    """text[start]의 '{'부터 균형 잡힌 객체 문자열 반환. 문자열 리터럴 안의 중괄호/이스케이프는 무시.
    중간에 끝나면(잘림 등) None."""
    depth = 0
    in_str = False
    esc = False
    for j in range(start, len(text)):
        c = text[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start:j + 1]
    return None


def extract_json(text):
    """응답에서 JSON 객체 추출. 프로즈·웹검색 요약·코드펜스·후행 텍스트·문자열 안 중괄호에 강건.
    여러 후보 객체 중 'restaurants' 배열을 가진 것을 우선 채택(술집 등 웹검색 응답 안정화)."""
    if not text:
        return None
    objs = []
    for i, c in enumerate(text):
        if c != "{":
            continue
        seg = _balanced_json(text, i)
        if not seg:
            continue
        try:
            obj = json.loads(seg)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            objs.append(obj)
    if not objs:
        return None
    # restaurants(리스트) 보유 → 그중 가장 많은 것 우선
    def score(o):
        rs = o.get("restaurants")
        ok = isinstance(rs, list)
        return (1 if ok else 0, len(rs) if ok else 0, len(o))
    objs.sort(key=score, reverse=True)
    return objs[0]

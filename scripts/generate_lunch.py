#!/usr/bin/env python3
"""
여의나루 점심 맛집 추천 생성기
GitHub Actions에서 매일 오전 11시(KST) 자동 실행
"""

import anthropic
import json
import math
import random
import re
import os
import sys
import smtplib
import time
import urllib.request
import urllib.parse
import email.utils
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone, timedelta

# 순수 헬퍼는 lunch_utils 로 분리 (전역 상태·외부 의존 없음). 같은 scripts/ 폴더라 import 가능.
from lunch_utils import parse_minutes, clean_name, haversine_m, extract_json

SB_URL = "https://nrdapzgtibbusvoaceuh.supabase.co"
SB_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im5yZGFwemd0aWJidXN2b2FjZXVoIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzk5MDM2MTEsImV4cCI6MjA5NTQ3OTYxMX0.hzAnNaPdx1AaswsY1hkzc98aRSD2PXUjVi_mLl3bzcM"

# JB빌딩 (여의나루로 77) 좌표 — Kakao 주소 지오코딩으로 검증됨
JB_LAT = 37.5240914884765
JB_LNG = 126.927376521939

# ── 위치 컨텍스트 (set_origin으로 위치별 전환) ──────────────────
# 배치는 app_locations의 모든 위치를 순회한다. 아래 작업 변수는
# 현재 처리 중인 위치를 가리키며, set_origin()이 좌표·지역·검색어를
# 통째로 바꾸고 좌표/후보 캐시를 무효화한다. (기본값 = JB빌딩)
ORIGIN_LAT = JB_LAT
ORIGIN_LNG = JB_LNG
REGION = "여의도"            # 지도 검색 보조 키워드 ('가게명 + REGION')
PLACE_NAME = "JB빌딩"        # 프롬프트의 기준 건물명
# Naver는 반경 검색 미지원 → 지역 키워드로 모아 거리 필터링
REGION_QUERIES = ["여의도 맛집", "여의나루 맛집", "여의도 점심", "여의도 일식", "여의도 한식", "여의도 술집"]

# ── 추천 방식 프리셋 ────────────────────────────────────────────
# 위치별 rec_profile에 따라 반경·거리표기·프롬프트를 조절한다.
#  radii      : 근처 후보를 모을 반경(m). 후보가 min_cand에 못 미치면 다음 반경으로 확대(적응형 ①)
#  min_cand   : 이 개수만큼 후보가 모이면 반경 확대 중단
#  walk_max   : 도보 표기 상한(분). 초과하면 '차로 N분·Nkm'로 자동 전환(②)
#  extra_max  : extras(추가추천) 허용 거리 상한(분)
#  allow_far  : 'sparse'=후보 부족할 때만 먼 유명맛집 허용 / 'no'=항상 근처만 / 'always'=항상 허용(③)
PROFILES = {
    "walk_tight": {"label": "도보 최우선",   "radii": [300, 500],              "min_cand": 6,  "walk_max": 99, "extra_max": 8,  "allow_far": "no"},
    "walk":       {"label": "도보 위주",     "radii": [700, 1000],             "min_cand": 8,  "walk_max": 99, "extra_max": 12, "allow_far": "no"},
    "auto":       {"label": "근거리 자동 (권장)", "radii": [600, 1500, 3000, 5000], "min_cand": 12, "walk_max": 14, "extra_max": 20, "allow_far": "sparse"},
    "town":       {"label": "동네 (도보+동네)", "radii": [1500, 3000],            "min_cand": 10, "walk_max": 12, "extra_max": 20, "allow_far": "sparse"},
    "drive_near": {"label": "차량 근교",     "radii": [2000, 5000],            "min_cand": 8,  "walk_max": 6,  "extra_max": 30, "allow_far": "sparse"},
    "drive":      {"label": "차량 권역",     "radii": [3000, 6000, 9000],      "min_cand": 8,  "walk_max": 5,  "extra_max": 40, "allow_far": "sparse"},
    "wide":       {"label": "광역 (시·도)",  "radii": [5000, 10000, 15000],    "min_cand": 6,  "walk_max": 4,  "extra_max": 60, "allow_far": "always"},
    "city":       {"label": "도심 밀집",     "radii": [500, 1000, 1500],       "min_cand": 15, "walk_max": 12, "extra_max": 12, "allow_far": "sparse"},
}
_PROFILE_DEFAULT = PROFILES["auto"]
def get_profile(key, custom=None):
    """프리셋 키 → 파라미터. 'custom'이면 위치의 rec_custom(JSON)을 기본값에 병합."""
    if key == "custom" and isinstance(custom, dict):
        merged = dict(_PROFILE_DEFAULT)
        merged["label"] = "맞춤"
        for f in ("radii", "min_cand", "walk_max", "extra_max", "allow_far"):
            if custom.get(f) not in (None, "", []):
                merged[f] = custom[f]
        # radii가 문자열/단일값으로 와도 방어
        if not isinstance(merged.get("radii"), list) or not merged["radii"]:
            merged["radii"] = list(_PROFILE_DEFAULT["radii"])
        return merged
    return PROFILES.get(key or "auto", _PROFILE_DEFAULT)

PROFILE = PROFILES["auto"]   # 현재 위치의 추천 방식 (set_origin에서 전환)

_coords_cache = {}
_candidates_cache = None


def set_origin(lat, lng, region, place, profile="auto", custom=None):
    """현재 처리할 위치로 전역 컨텍스트 전환 + 위치 종속 캐시 초기화."""
    global ORIGIN_LAT, ORIGIN_LNG, REGION, PLACE_NAME, REGION_QUERIES, PROFILE
    global _coords_cache, _candidates_cache
    ORIGIN_LAT, ORIGIN_LNG = lat, lng
    REGION = region
    PLACE_NAME = place
    PROFILE = get_profile(profile, custom)
    REGION_QUERIES = [f"{region} 맛집", f"{region} 점심", f"{region} 한식",
                      f"{region} 일식", f"{region} 술집"]
    _coords_cache = {}       # 좌표 캐시는 거리 기준점이 바뀌면 무효
    _candidates_cache = None  # 근처 후보도 위치별로 다시 수집


def fmt_dist(meters):
    """거리(m) → 표기. 도보 상한 초과면 차로/거리로 자동 전환 (프로파일 walk_max 기준)."""
    wm = max(1, round(meters * 1.35 / 80))
    if wm <= PROFILE["walk_max"]:
        return f"도보 {wm}분", wm
    drive = max(2, round(meters / 350))   # 도심 평균 ~21km/h 가정
    km = meters / 1000
    return f"차로 {drive}분 · 약 {km:.1f}km", wm


def fetch_locations():
    """app_locations(공개 SELECT)에서 배치 대상 위치 목록 조회.
    실패 시 JB빌딩 1곳으로 폴백."""
    fallback = [{"key": "jb", "name": "JB빌딩", "region": "여의도",
                 "lat": JB_LAT, "lng": JB_LNG, "auto": True, "rec_profile": "auto"}]
    try:
        url = f"{SB_URL}/rest/v1/app_locations?select=key,name,short,region,lat,lng,auto,rec_profile,rec_custom,diversity&enabled=not.eq.false&order=sort.asc"
        req = urllib.request.Request(url, headers={"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}"})
        with urllib.request.urlopen(req, timeout=8) as r:
            rows = json.loads(r.read())
        out = []
        for l in rows:
            try:
                out.append({"key": l["key"], "name": l.get("name") or l["key"],
                            "region": l.get("region") or "", "short": l.get("short") or l.get("name"),
                            "lat": float(l["lat"]), "lng": float(l["lng"]), "auto": bool(l.get("auto")),
                            "rec_profile": l.get("rec_profile") or "auto",
                            "rec_custom": l.get("rec_custom"),
                            "diversity": l.get("diversity")})
            except (KeyError, ValueError, TypeError):
                continue
        return out or fallback
    except Exception as e:
        print(f"⚠️  위치 목록 조회 실패 → JB빌딩만 처리: {e}")
        return fallback


# ── 추천 다양성 설정 (관리자페이지에서 토글) ───────────────────
# avoid(B)=최근 추천 강하게 제외 / spread(C)=거리대 분산 강제 /
# pool(A)=후보 무작위 섞기 / rotate(D)=날짜 시드 로테이션 / recent_days=회피 기간
DIVERSITY = {"avoid": True, "spread": True, "pool": True, "rotate": True, "recent_days": 7,
             "dedup_branch": True, "cuisine_vary": True, "cand_max": 45, "radius_boost": 1.0}


def fetch_diversity():
    """app_settings(공개 SELECT)에서 다양성 설정 로드. 실패 시 기본값(B+C on)."""
    try:
        url = f"{SB_URL}/rest/v1/app_settings?id=eq.1&select=diversity"
        req = urllib.request.Request(url, headers={"apikey": SB_KEY, "Authorization": f"Bearer {SB_KEY}"})
        with urllib.request.urlopen(req, timeout=6) as r:
            rows = json.loads(r.read())
        d = (rows[0].get("diversity") if rows else {}) or {}
        out = dict(DIVERSITY)
        for k in ("avoid", "spread", "pool", "rotate", "dedup_branch", "cuisine_vary"):
            if k in d:
                out[k] = bool(d[k])
        try:
            out["recent_days"] = max(0, min(14, int(d.get("recent_days", out["recent_days"]))))
        except (TypeError, ValueError):
            pass
        try:
            out["cand_max"] = max(15, min(150, int(d.get("cand_max", out["cand_max"]))))
        except (TypeError, ValueError):
            pass
        try:
            out["radius_boost"] = max(1.0, min(4.0, float(d.get("radius_boost", out["radius_boost"]))))
        except (TypeError, ValueError):
            pass
        return out
    except Exception as e:
        print(f"⚠️  다양성 설정 조회 실패 → 기본값 사용: {e}")
        return dict(DIVERSITY)


# parse_minutes / clean_name / haversine_m → lunch_utils 로 이동(상단 import)
_BASE_DIVERSITY = dict(DIVERSITY)   # 전역 기본 (배치 시작 시 fetch_diversity로 채움)


def effective_diversity(loc):
    """위치별 다양성 = 전역 기본 + 위치 override(loc['diversity']). override 없으면 전역 그대로."""
    base = dict(_BASE_DIVERSITY)
    ov = loc.get("diversity")
    if isinstance(ov, dict):
        for k in ("avoid", "spread", "pool", "rotate", "dedup_branch", "cuisine_vary"):
            if k in ov:
                base[k] = bool(ov[k])
        for k, lo, hi, cast in (("recent_days", 0, 14, int), ("cand_max", 15, 150, int), ("radius_boost", 1.0, 4.0, float)):
            if ov.get(k) is not None:
                try:
                    base[k] = max(lo, min(hi, cast(ov[k])))
                except (TypeError, ValueError):
                    pass
    return base


# 지점/지역 접미사 (중복 판정용 정규화)
_BRANCH_SUFFIX_RE = re.compile(r"\s+\S*(?:점|지점|본점|직영점)$")
_REGION_TOKENS = ("여의도", "여의나루", "IFC몰", "IFC", "파이낸스", "더현대서울", "더현대")


def norm_name(name):
    """중복 판정용 정규화 — clean_name + 끝의 지점명/지역 토큰 제거.
    예: '독도참치앤전복 여의도점'→'독도참치앤전복', '미도인 파이낸스 여의도점'→'미도인'."""
    s = clean_name(name)
    for _ in range(3):
        m = _BRANCH_SUFFIX_RE.search(s)        # ' 여의도점' / ' IFC몰점' / ' 직영점' 등
        if m:
            s = s[:m.start()].strip()
            continue
        toks = s.split()
        if len(toks) > 1 and toks[-1] in _REGION_TOKENS:   # 끝의 지역 토큰
            s = " ".join(toks[:-1]).strip()
            continue
        break
    return s or clean_name(name)


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


def fetch_kakao_candidates(kakao_key, radius, max_count=40):
    """Kakao 카테고리(반경) 검색으로 근처 실제 음식점 → [(이름, 거리m, 카테고리)]."""
    if not kakao_key:
        return []
    out = []
    try:
        for page in range(1, 4):  # 최대 3페이지(45곳)
            url = (f"https://dapi.kakao.com/v2/local/search/category.json"
                   f"?category_group_code=FD6&x={ORIGIN_LNG}&y={ORIGIN_LAT}"
                   f"&radius={radius}&sort=distance&size=15&page={page}")
            req = urllib.request.Request(url, headers={"Authorization": f"KakaoAK {kakao_key}"})
            with urllib.request.urlopen(req, timeout=5) as r:
                data = json.loads(r.read())
            for d in data.get("documents", []):
                cat = d.get("category_name", "").replace("음식점 > ", "").split(" > ")[0]
                out.append((d["place_name"], int(d.get("distance", 0)), cat))
                if len(out) >= max_count:
                    return out
            if data.get("meta", {}).get("is_end"):
                break
    except Exception as e:
        print(f"  ⚠️  Kakao 후보 조회 실패: {e}")
    return out


def fetch_naver_candidates(naver_id, naver_secret, radius, max_count=30):
    """Naver 지역검색은 반경 검색을 지원하지 않아, 여러 키워드로 검색 후
    기준 위치 반경 내만 거리 필터링 → [(이름, 거리m, 카테고리)]."""
    if not (naver_id and naver_secret):
        return []
    out, seen = [], set()
    headers = {"X-Naver-Client-Id": naver_id, "X-Naver-Client-Secret": naver_secret}
    for kw in REGION_QUERIES:
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
                dist_m = haversine_m(ORIGIN_LAT, ORIGIN_LNG, lat, lng)
                if dist_m > radius * 1.4:   # 반경 밖 제외 (도로보정 감안 여유)
                    continue
                seen.add(name)
                cat = (it.get("category", "").split(">")[-1]).strip()
                out.append((name, dist_m, cat))
                if len(out) >= max_count:
                    return out
        except Exception as e:
            print(f"  ⚠️  Naver 후보 조회 실패({kw}): {e}")
    return out


# 후보 수집 결과 메타 (프롬프트 분기용) — fetch_nearby_candidates가 갱신
_cand_sparse = False   # 가장 넓은 반경에서도 후보가 부족했는지


def fetch_nearby_candidates(kakao_key, naver_id=None, naver_secret=None, max_count=45):
    """적응형 반경(①): PROFILE['radii']를 차례로 넓히며 후보를 모은다.
    min_cand만큼 모이면 중단. 끝까지 부족하면 sparse=True. 끼니마다 동일하므로 캐싱."""
    global _candidates_cache, _cand_sparse
    if _candidates_cache is not None:
        return _candidates_cache
    boost = float(DIVERSITY.get("radius_boost", 1.0) or 1.0)        # 후보풀 넓히기: 반경 배율
    cap = int(DIVERSITY.get("cand_max", max_count) or max_count)    # 후보풀 넓히기: 최대 후보 수
    merged, seen = [], set()
    used_radius = int(PROFILE["radii"][0] * boost)
    for base_radius in PROFILE["radii"]:
        radius = int(base_radius * boost)
        used_radius = radius
        cand = fetch_kakao_candidates(kakao_key, radius) + fetch_naver_candidates(naver_id, naver_secret, radius)
        cand.sort(key=lambda c: c[1])   # 거리 가까운 순
        merged, seen = [], set()
        for name, meters, cat in cand:
            key = name.replace(" ", "")
            if key in seen:
                continue
            seen.add(key)
            label, _wm = fmt_dist(meters)
            merged.append(f"{name}({cat}, {label})" if cat else f"{name}({label})")
            if len(merged) >= cap:
                break
        if len(merged) >= PROFILE["min_cand"]:
            break   # 충분히 모임 → 반경 확대 중단
    _cand_sparse = len(merged) < PROFILE["min_cand"]
    _candidates_cache = merged
    sp = " · 후보 부족(넓게 탐색)" if _cand_sparse else ""
    print(f"  🗂️  근처 음식점 후보 {len(merged)}곳 (반경 {used_radius}m{sp}, 방식={PROFILE['label']})")
    return merged


def get_kakao_place(name, kakao_key):
    """Kakao Local API로 장소 검색 → (lat, lng, category). 실패 시 (None, None, None)."""
    cache_key = f"kakao:{name}"
    if cache_key in _coords_cache:
        return _coords_cache[cache_key]
    query = urllib.parse.quote(f"{clean_name(name)} {REGION}")
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
    query = urllib.parse.quote(f"{clean_name(name)} {REGION}")
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
        labels = {}       # 소스별 거리 표기 (도보/차로 자동전환)
        food_votes = []   # 카테고리 판정 결과 (True/False/None)

        # Kakao
        klat, klng, kcat = get_kakao_place(r["name"], kakao_key)
        if klat and klng:
            labels["카카오"], _ = fmt_dist(haversine_m(ORIGIN_LAT, ORIGIN_LNG, klat, klng))
            food_votes.append(_is_food_category(kcat))

        # Naver
        if naver_id and naver_secret:
            nlat, nlng, ncat = get_naver_place(r["name"], naver_id, naver_secret)
            if nlat and nlng:
                labels["네이버"], _ = fmt_dist(haversine_m(ORIGIN_LAT, ORIGIN_LNG, nlat, nlng))
                food_votes.append(_is_food_category(ncat))

        # 검증 실패 판정: 어디서도 못 찾음 OR 모든 소스가 비음식점
        is_food = not (food_votes and all(v is False for v in food_votes))
        if not labels or not is_food:
            r["verified"] = False
            r["distance"] = "거리 미확인"
            unverified.append(r["name"])
            print(f"  ⚠️  {r['name']}: 지도없음")
            continue

        # 검증 성공 — 거리 표기 (찾은 소스만)
        r["verified"] = True
        if "카카오" in labels and "네이버" in labels:
            r["distance"] = f"{labels['카카오']} (카카오) / {labels['네이버']} (네이버)"
        elif "카카오" in labels:
            r["distance"] = f"{labels['카카오']} (카카오)"
        else:
            r["distance"] = f"{labels['네이버']} (네이버)"
        print(f"  ✅ {r['name']}: {r['distance']}")

    if unverified:
        print(f"  ⚠️  지도없음 ({len(unverified)}): {', '.join(unverified)}")

    return restaurants, unverified


def get_today_kst():
    kst = timezone(timedelta(hours=9))
    return datetime.now(kst).strftime("%Y-%m-%d")


def translate_weather_desc(desc):
    """wttr.in 영문 날씨 설명을 한글로 변환 (lang_ko 누락 대비)."""
    if not desc:
        return desc
    # 한글이 이미 섞여 있으면 그대로
    if any('가' <= ch <= '힣' for ch in desc):
        return desc
    d = desc.strip().lower()
    table = {
        "clear": "맑음", "sunny": "맑음",
        "partly cloudy": "부분적으로 흐림", "cloudy": "흐림", "overcast": "잔뜩 흐림",
        "mist": "옅은 안개", "fog": "안개", "freezing fog": "어는 안개",
        "patchy rain possible": "곳곳에 비", "patchy rain nearby": "주변에 비",
        "patchy light rain": "약한 비", "light rain": "약한 비",
        "light rain shower": "약한 소나기", "moderate rain": "비",
        "heavy rain": "강한 비", "rain": "비", "showers": "소나기",
        "patchy snow possible": "곳곳에 눈", "light snow": "약한 눈",
        "snow": "눈", "heavy snow": "강한 눈", "blizzard": "눈보라",
        "patchy sleet possible": "진눈깨비 가능", "sleet": "진눈깨비",
        "thundery outbreaks possible": "천둥번개 가능",
        "thundery outbreaks in nearby": "주변에 천둥번개",
        "thunderstorm": "뇌우", "patchy light rain with thunder": "천둥 동반 약한 비",
        "moderate or heavy rain with thunder": "천둥 동반 비",
    }
    if d in table:
        return table[d]
    # 부분 매칭 (긴 키 우선)
    for k in sorted(table, key=len, reverse=True):
        if k in d:
            return table[k]
    return desc  # 매칭 실패 시 원문 유지


def fetch_weather(region="여의도"):
    """wttr.in으로 해당 지역 날씨 (무료, 키 불필요). 실패 시 ''.
    지역명 첫 단어를 도시 키워드로 사용 (예: '광주 동구' → '광주')."""
    loc = (region or "여의도").split()[0] or "여의도"
    if loc == "여의도":
        loc = "Yeouido"   # 영문 키워드가 더 정확
    try:
        req = urllib.request.Request(
            f"https://wttr.in/{urllib.parse.quote(loc)}?format=j1&lang=ko",
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
        desc = translate_weather_desc(desc)
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


# JB금융그룹(JB금융지주·전북은행·광주은행·JB우리캐피탈) 뉴스 검색 쿼리
# AX/AI/디지털을 최우선으로, 각 계열사별로 폭넓게
JB_NEWS_QUERIES = [
    "JB금융 AX", "JB금융 AI", "JB금융 인공지능", "JB금융 디지털", "JB금융 DX",
    "전북은행 AI", "전북은행 디지털", "전북은행 AX", "전북은행 디지털전환",
    "광주은행 AI", "광주은행 디지털", "광주은행 AX",
    "JB우리캐피탈 AI", "JB우리캐피탈 디지털", "JB우리캐피탈",
    "JB금융 혁신", "JB금융지주 외국인", "JB금융지주",
]
# 우선 추출할 주제 키워드 (제목·요약에 있으면 가점)
JB_FOCUS_KEYWORDS = ["AX", "AI", "인공지능", "디지털", "DX", "혁신", "외국인",
                     "데이터", "핀테크", "플랫폼", "전환", "테크", "생성형", "클라우드"]
# JB 관련 기사인지 판별 (무관한 기사 제외)
JB_RELATED = ["JB금융", "JB금융지주", "전북은행", "광주은행", "JBFG", "핀다", "JB우리캐피탈"]


def _clean_news_text(s):
    return (re.sub(r"<[^>]+>", "", s or "")
            .replace("&quot;", '"').replace("&amp;", "&")
            .replace("&lt;", "<").replace("&gt;", ">").replace("&apos;", "'").strip())


def _news_window_dates(today=None):
    """KST 기준 오늘/어제 날짜 문자열 세트. 최신 뉴스는 이 범위만 허용."""
    today = today or get_today_kst()
    d = datetime.strptime(today, "%Y-%m-%d").date()
    y = d - timedelta(days=1)
    return today, y.strftime("%Y-%m-%d")


def _parse_news_pub_dt(pub_date):
    """Naver pubDate → KST datetime. 실패 시 None."""
    try:
        return email.utils.parsedate_to_datetime(pub_date or "").astimezone(timezone(timedelta(hours=9)))
    except Exception:
        return None


def fetch_jb_news(naver_id, naver_secret, count=3, today=None):
    """네이버 뉴스를 여러 키워드로 폭넓게 검색 → JB 관련만 추려
    KST 오늘 우선, 없으면 어제까지. AX·AI·디지털·혁신·외국인 주제 가점. → [{title, link}]."""
    if not (naver_id and naver_secret):
        return []
    today_s, yesterday_s = _news_window_dates(today)
    allowed_dates = {today_s, yesterday_s}
    headers = {"X-Naver-Client-Id": naver_id, "X-Naver-Client-Secret": naver_secret}
    collected = {}   # link → {title, link, score, ts}
    for query in JB_NEWS_QUERIES:
        try:
            q = urllib.parse.quote(query)
            url = f"https://openapi.naver.com/v1/search/news.json?query={q}&display=20&sort=date"
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=6) as r:
                items = json.loads(r.read()).get("items", [])
        except Exception:
            continue
        for it in items:
            title = _clean_news_text(it.get("title", ""))
            desc = _clean_news_text(it.get("description", ""))
            link = it.get("originallink") or it.get("link", "")
            if not title or not link:
                continue
            text = title + " " + desc
            if not any(k in text for k in JB_RELATED):
                continue   # JB 무관 기사 제외
            # 발행시각
            pub_dt = _parse_news_pub_dt(it.get("pubDate", ""))
            if not pub_dt:
                continue
            pub_day = pub_dt.strftime("%Y-%m-%d")
            if pub_day not in allowed_dates:
                continue
            ts = pub_dt.timestamp()
            day_rank = 1 if pub_day == today_s else 0
            score = sum(1 for k in JB_FOCUS_KEYWORDS if k in text)
            focus = 1 if score > 0 else 0   # AX/AI/디지털 등 주제 적합 기사
            prev = collected.get(link)
            # AX/AI/디지털 기사를 최상단으로: focus > score > 당일 > 최신
            rank = (focus, score, day_rank, ts)
            if prev and prev["rank"] >= rank:
                continue
            collected[link] = {
                "title": title, "link": link, "date": it.get("pubDate", ""),
                "score": score, "ts": ts, "rank": rank, "pub_day": pub_day
            }

    # 당일 기사 우선 → 주제 적합도(가점) → 최신순. 어제 기사는 당일 기사 부족분만 채움.
    items_all = list(collected.values())
    focus_count = sum(1 for x in items_all if x["score"] > 0)
    items_all.sort(key=lambda x: x["rank"], reverse=True)
    picked = items_all[:count]
    news = [{"title": x["title"], "link": x["link"]} for x in picked]
    today_count = sum(1 for x in picked if x.get("pub_day") == today_s)
    print(f"📰 JB금융 뉴스 {len(news)}건 ({today_s} {today_count}건, {yesterday_s}까지 / 포커스 {focus_count}건 중)")
    return news


def fetch_jb_news_web(client, count=3, today=None):
    """Claude 웹검색으로 JB금융 AX/AI/디지털/혁신/외국인 최신 소식 → [{title, link}]."""
    today_s, yesterday_s = _news_window_dates(today)
    prompt = f"""웹 검색으로 'JB금융그룹'(JB금융지주, 전북은행, 광주은행, JB우리캐피탈) 관련
가장 최근 뉴스를 찾아줘. 특히 다음 주제를 최우선으로 골라줘:
- AX(인공지능 전환), AI/인공지능/생성형AI
- 디지털 전환(DX)·디지털 혁신·핀테크·플랫폼
- 경영 혁신·신사업
- 외국인(고객·투자자·주주·인재) 관련

반드시 KST 기준 {today_s} 당일 발행 기사부터 고르고, 부족할 때만 {yesterday_s} 발행 기사로 채워줘.
{yesterday_s}보다 오래된 기사는 절대 포함하지 마.

최신순으로 실제 기사 4건을 아래 JSON으로만 출력(제목은 실제 기사 제목, link는 원문 URL, date는 발행일 YYYY-MM-DD):
```json
{{"news":[{{"title":"...","link":"https://...","date":"{today_s}"}}]}}
```"""
    try:
        text = call_claude(client, prompt, use_web=True)
        data = extract_json(text) if text else None
        items = (data or {}).get("news", []) if isinstance(data, dict) else []
        out = []
        allowed_dates = {today_s, yesterday_s}
        for it in items[:count]:
            t = (it.get("title") or "").strip()
            l = (it.get("link") or it.get("url") or "").strip()
            d = str(it.get("date") or it.get("published") or "")[:10]
            if t and d in allowed_dates:
                out.append({"title": t, "link": l})
        print(f"📰 (웹검색) JB 소식 {len(out)}건 ({today_s}/{yesterday_s} 발행분)")
        return out
    except Exception as e:
        print(f"⚠️  웹검색 뉴스 실패 (무시): {e}")
        return []


def merge_news(*lists, count=4):
    """여러 뉴스 리스트 병합 + 제목 기준 중복 제거 (앞쪽 우선)."""
    out, seen = [], set()
    for lst in lists:
        for n in (lst or []):
            key = re.sub(r"\s+", "", n.get("title", ""))[:18]
            if not key or key in seen:
                continue
            seen.add(key)
            out.append({"title": n["title"], "link": n.get("link", "")})
            if len(out) >= count:
                return out
    return out


def prioritize_focus_news(news, count=3):
    """AX·AI·디지털 등 주제 기사를 최상단으로 (안정 정렬: 그 외 순서 유지).
    각 기사에 focus 플래그 부여 → 프론트에서 배지 표시 가능."""
    def is_focus(n):
        return any(k in (n.get("title", "")) for k in JB_FOCUS_KEYWORDS)
    focus = [{**n, "focus": True} for n in news if is_focus(n)]
    rest = [{**n, "focus": False} for n in news if not is_focus(n)]
    return (focus + rest)[:count]


def fetch_jb_stock():
    """JB금융지주(175330) 주가 → '25,500원 (전일대비 -350, -1.35% 하락)'. 실패 시 ''."""
    try:
        req = urllib.request.Request(
            "https://m.stock.naver.com/api/stock/175330/basic",
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=6) as r:
            d = json.loads(r.read())
        price = d.get("closePrice", "")
        change = d.get("compareToPreviousClosePrice", "")
        rate = d.get("fluctuationsRatio", "")
        direction = (d.get("compareToPreviousPrice") or {}).get("text", "")
        if not price:
            return ""
        line = f"{price}원 (전일대비 {change}, {rate}% {direction})".strip()
        print(f"📈 JB금융지주 주가: {line}")
        return line
    except Exception as e:
        print(f"⚠️  주가 조회 실패 (무시): {e}")
        return ""


def generate_daily_message(client, weather, news, restaurants):
    """날씨 + JB금융 뉴스 + 추천 맛집을 엮은 친근한 점심 한마디 생성."""
    names = ", ".join(r["name"] for r in restaurants[:5])
    headlines = " / ".join(n["title"] for n in (news or [])[:3]) or "(특이 소식 없음)"
    prompt = f"""아래 정보로 여의도 JB금융 임직원에게 전하는 오늘의 점심 안내 멘트를 2~3문장으로 작성해줘.

문체 규칙:
- 반드시 격식 있고 정중한 존댓말("~합니다/~하시기 바랍니다/~추천드립니다" 체)로만 작성.
- 반말("~해/~네/~먹고 와")·구어체 감탄("화이팅!", "최고!")·과한 이모지 금지.
- 이모지는 문장 끝에 1개 이내로 절제해서 사용(없어도 무방).
- 날씨에 어울리는 메뉴를 자연스럽게 권하고, 회사 소식이 있으면 차분하게 한 줄 엮어줘.

- 오늘 날씨: {weather or '정보 없음'}
- JB금융그룹 소식: {headlines}
- 오늘 추천 맛집: {names}

멘트 문장만 출력해줘 (다른 설명 없이)."""
    try:
        msg = call_claude(client, prompt, use_web=False)
        msg = (msg or "").strip()
        if msg:
            print(f"💬 오늘의 멘트: {msg[:60]}…")
        return msg
    except Exception as e:
        print(f"⚠️  멘트 생성 실패 (무시): {e}")
        return ""


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
        # 일시적 오류 자동 재시도 (rate limit / 과부하 529 / 연결·타임아웃) — 지수 백오프 + 지터
        MAX_ATTEMPTS = 5
        response = None
        for attempt in range(MAX_ATTEMPTS):
            wait = 0
            try:
                kwargs = dict(model="claude-haiku-4-5-20251001", max_tokens=8000, messages=messages)
                if tools:
                    kwargs["tools"] = tools
                response = client.messages.create(**kwargs)
                break
            except anthropic.RateLimitError:
                wait = 65 * (attempt + 1) + random.uniform(0, 10)
                print(f"  ⏳ Rate limit — {wait:.0f}초 후 재시도 ({attempt+1}/{MAX_ATTEMPTS})...")
            except anthropic.APIError as e:
                status = getattr(e, "status_code", None)
                # 재시도 무의미한 4xx(429 제외)는 즉시 중단
                if status is not None and 400 <= status < 500 and status != 429:
                    print(f"  ❌ 재시도 불가 오류({status}): {e}")
                    return None
                wait = min(60, 5 * (2 ** attempt)) + random.uniform(0, 5)
                print(f"  ⏳ 일시 오류({status or type(e).__name__}) — {wait:.0f}초 후 재시도 ({attempt+1}/{MAX_ATTEMPTS})...")
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(wait)
        if response is None:
            print(f"❌ API 재시도 {MAX_ATTEMPTS}회 초과 — 호출 포기")
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


def get_recent_names(history_path="history.json", days=3, limit=24):
    """최근 N일간 추천된 가게 이름 (중복 추천 방지용). 기본 추천 + 추가 추천."""
    if not os.path.exists(history_path):
        return []
    try:
        with open(history_path, encoding="utf-8") as f:
            history = json.load(f)
    except Exception:
        return []
    recs = sorted(history.get("recommendations", []),
                  key=lambda x: x.get("date", ""), reverse=True)[:days]
    names = []
    seen = set()
    for day in recs:
        buckets = [day.get("restaurants", []), day.get("extras", [])]
        for m in (day.get("meals") or {}).values():
            buckets.append(m.get("restaurants", []))
            buckets.append(m.get("extras", []))
        for b in buckets:
            for r in b:
                nm = r.get("name")
                if not nm:
                    continue
                # dedup_branch: 접미사('여의도점/직영점/IFC몰점') 무시 → 같은 집 변형도 중복 제외
                key = norm_name(nm) if DIVERSITY.get("dedup_branch", True) else nm.strip()
                if key and key not in seen:
                    seen.add(key)
                    names.append(key)
    return names[:limit]


# _balanced_json / extract_json → lunch_utils 로 이동(상단 import).
# (술집 등 웹검색 응답에 강건한 'restaurants 우선' 버전을 lunch_utils 에 정본화)


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

    parent = os.path.dirname(history_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(history_path, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    return history


def send_email(restaurants, today, full_text, comment="", weather="", news=None, extras=None, message=""):
    """Gmail SMTP로 점심 추천 이메일 발송 (날씨 + JB금융 뉴스 + 오늘의 멘트 포함)."""
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
        lines.append(f"🌤️ 오늘 날씨: {weather}")
    if message:
        lines.append(f"\n💬 {message}\n")
    else:
        lines.append("")
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

    # 상단에 멘트(message)가 없을 때만 하단에 날씨 코멘트 노출 (중복 방지)
    lines += ["\n" + "─" * 40]
    if not message and comment:
        lines.append(f"\n💬 {comment}")
    lines += ["\n오늘도 맛있는 점심 되세요! 🍽️",
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


RECENT_INLINE_DAYS = 14  # index.html에는 최근 N일만 인라인 (나머지는 런타임에 history.json 지연 로드)


def update_index_html(history, html_path="index.html"):
    """index.html의 HISTORY_DATA 교체 (최근 RECENT_INLINE_DAYS일만 인라인 — 본문서 비대화 방지)."""
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    # recommendations는 newest-first(insert(0)) → 앞에서 N개가 최근 N일
    inline = history["recommendations"][:RECENT_INLINE_DAYS]
    new_data = json.dumps(inline, ensure_ascii=False)
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


def generate_meal(client, today, date_compact, meal, with_conditions, kakao_key=None, naver_id=None, naver_secret=None, recent_names=None, mood_ctx=""):
    """한 시간대(점심/저녁/술집) 추천 생성. {comment, restaurants, by_condition?} 반환."""
    ctx = MEAL_CTX[meal]
    idp = MEAL_IDP[meal]
    id_prefix = date_compact + (f"-{idp}" if idp else "")

    # 하이브리드: 근처 실제 음식점 후보 목록 (적응형 반경)
    candidates = fetch_nearby_candidates(kakao_key, naver_id, naver_secret)
    if DIVERSITY["pool"] and candidates:   # A: 후보 풀을 무작위로 섞어 매번 다른 조합 유도
        candidates = list(candidates)
        random.shuffle(candidates)
    # ③ 후보 충분도·프로파일에 따라 '먼 곳 추가' 허용 여부를 다르게 안내
    allow_far = PROFILE["allow_far"]
    far_ok = (allow_far == "always") or (allow_far == "sparse" and _cand_sparse)
    cand_block = ""
    if candidates:
        if far_ok:
            tail = (f"\n이 지역은 가까운 가게가 많지 않아요. 위 목록을 우선 쓰되, 목록에 없어도 "
                    f"실제 영업 중인 {REGION} 인근 알려진 가게를 추가해도 됩니다. 다소 멀어도 괜찮아요.\n")
        else:
            tail = (f"\n반드시 위 목록 안에서, 가까운 순서대로 골라줘. "
                    f"목록에 없는 멀리 떨어진 유명 맛집은 넣지 마.\n")
        cand_block = (f"\n참고용 — {PLACE_NAME} 근처 실제 등록 음식점(거리 정확, 가까운 순):\n"
                      + ", ".join(candidates) + tail)
    elif allow_far != "no":
        cand_block = (f"\n{PLACE_NAME} 근처 등록 음식점을 찾지 못했어요. "
                      f"네가 아는 {REGION} 인근 실제 가게를 가까운 순으로 추천해줘. 다소 멀어도 괜찮아요.\n")
    # 위치 기준 문구 (프롬프트 머리말)
    origin_desc = f"{REGION} {PLACE_NAME}".strip()

    # 다양성: B=회피 강화 / C=거리대 분산 강제 (관리자 설정 DIVERSITY)
    avoid_line = ""
    if recent_names:
        branch_txt = " 가게명 뒤 지점명('여의도점' 등)만 다른 같은 가게도 다시 넣지 마." if DIVERSITY.get("dedup_branch", True) else ""
        cuisine_txt = " 또한 최근 며칠과 음식 종류(cuisine)가 한쪽으로 쏠리지 않게 다양한 종류로 섞어줘." if DIVERSITY.get("cuisine_vary", True) else ""
        if DIVERSITY["avoid"]:   # B: 최근 추천 강하게 제외
            avoid_line = (f"\n🚫 최근 추천한 곳({', '.join(recent_names)})은 위 목록에 있어도 "
                          f"반드시 제외하고, 새로운 가게로만 5곳을 골라줘. 최근 추천한 곳을 다시 넣지 마."
                          f"{branch_txt}{cuisine_txt}\n")
        else:
            cz = " 음식 종류도 최근과 다르게 섞어줘." if DIVERSITY.get("cuisine_vary", True) else ""
            avoid_line = (f"\n⚠️ 최근 며칠간 추천한 곳({', '.join(recent_names)})은 가급적 빼고 새로운 가게로 골라줘.{cz}\n")
    spread_txt = ("restaurants 5곳은 가까운 곳 위주로 하되 거리대를 다양하게(가장 가까운 2곳 + 중간 2곳 + 조금 먼 1곳), "
                  "음식 종류도 겹치지 않게. 무조건 제일 가까운 곳만 반복하지 말 것.")
    near_txt = ("restaurants 5곳은 위 목록에서 가까운 순으로 골라줘(거리대를 억지로 분산하지 말 것). "
                "음식 종류는 최대한 겹치지 않게.")
    if DIVERSITY["spread"]:       # C: 항상 거리대 분산 강제
        band_line = spread_txt
    elif far_ok:
        band_line = near_txt
    else:
        band_line = spread_txt
    # extras 허용 거리(분) — 프로파일별
    ex_max = PROFILE["extra_max"]

    if with_conditions:
        cond_list = " / ".join(CONDITIONS)
        hint_line = " ".join(f'({c}={h})' for c, h in COND_HINT.items())
        weather_line = f"오늘 {REGION} 날씨를 웹 검색으로 확인하고, " if meal == "점심" else ""
        cond_json = ",".join(f'"{c}":[...]' for c in CONDITIONS)
        prompt = f"""{weather_line}{origin_desc} 근처에서 오늘 {meal} 자리로 갈 만한 {ctx}을 추천해줘.
{mood_ctx}{cand_block}{avoid_line}
웹 검색으로 {REGION} 인기 가게를 조사한 후 아래 JSON을 응답 마지막에 포함해줘.
- restaurants: 기본 추천 5곳. {band_line}
- extras: 추가 추천 2곳(둘 다 반드시 약 {ex_max}분 이내의 실제 가게) — 1곳 tag="근처유명"(가까운 유명), 1곳 tag="검색유명"(웹에서 평 좋은 유명). restaurants와 겹치지 않게
- by_condition: {cond_list} — 각 컨디션에 맞는 5곳 (컨디션마다 다른 조합). 참고: {hint_line}
- comment: {"날씨를 반영한 한마디" if meal == "점심" else f"{meal} 추천 한마디"}

각 가게 필드: name, cuisine, feature, price(저렴/보통/비쌈), distance(도보 N분) (extras는 추가로 tag)
- feature: 대표 메뉴·맛·특징을 요약한 키워드형(약 25~35자, 모바일 2줄 분량). 예: "국물 요리 명가 · 깊고 깔끔한 국맛, 든든한 점심". 긴 문장 설명 X, 짧은 구절 나열

```json
{{"comment":"...","restaurants":[...5곳],"extras":[{{...,"tag":"근처유명"}},{{...,"tag":"검색유명"}}],"by_condition":{{{cond_json}}}}}
```"""
    else:
        prompt = f"""{origin_desc} 근처에서 오늘 {meal} 가기 좋은 {ctx} 5곳을 추천해줘.
{cand_block}{avoid_line}
웹 검색으로 {REGION} 인기 가게를 조사한 후 아래 JSON을 응답 마지막에 포함해줘.
- restaurants: 기본 추천 5곳. {band_line}
- extras: 추가 2곳(둘 다 반드시 약 {ex_max}분 이내) — 1곳 tag="근처유명"(가까운 유명), 1곳 tag="검색유명"(웹에서 평 좋은 유명)
각 가게 필드: name, cuisine, feature, price(저렴/보통/비쌈), distance(도보 N분) (extras는 tag 추가)
- feature: 대표 메뉴·맛·특징을 요약한 키워드형(약 25~35자, 모바일 2줄 분량). 긴 문장 설명 X, 짧은 구절 나열

```json
{{"comment":"{meal} 추천 한마디","restaurants":[...5곳],"extras":[{{...,"tag":"근처유명"}},{{...,"tag":"검색유명"}}]}}
```"""

    print(f"🔍 [{meal}] 생성 중...")
    # 점심(날씨/신선도)·술집(실존 가게명 확보)은 웹검색 사용.
    # 저녁은 컨디션 10종으로 토큰이 커 지식 기반(rate limit 회피).
    use_web = meal in ("점심", "술집")
    text = call_claude(client, prompt, use_web=use_web)
    entry = extract_json(text) if text else None
    if not entry:
        # 원인: 술집을 web search로 강제하면 (특히 시골) 모델이 JSON 대신 프로즈만 내고 end_turn.
        #  → 재시도는 web search를 끄고(지식기반: JSON을 안정적으로 출력) 희박하면 후보로 채우게 함.
        snippet = (text or "").strip().replace("\n", " ")[:300]
        print(f"⚠️ [{meal}] JSON 파싱 실패 — 재시도(웹검색 끄고 JSON 강제). 원응답≈ {snippet!r}")
        retry_prompt = prompt + (
            "\n\n[중요] 설명 문장 없이, 위 형식의 JSON 객체 하나만 ```json 코드블록 안에 출력해줘. "
            "restaurants 배열(5곳)은 반드시 포함. 적당한 곳이 적으면 위 후보 목록이나 인근 가게로라도 5곳을 채워줘."
        )
        text = call_claude(client, retry_prompt, use_web=False)
        entry = extract_json(text) if text else None
    if not entry:
        snippet = (text or "").strip().replace("\n", " ")[:300]
        print(f"❌ [{meal}] JSON 파싱 실패 (재시도 후). 원응답≈ {snippet!r}")
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
            # 추가 추천은 프로파일 거리 상한 이내 + 검증된 곳만 (미확인/초과 제외)
            ex_max = PROFILE["extra_max"]
            entry["extras"] = [r for r in extras
                               if r.get("verified") and (parse_minutes(r.get("distance")) or 999) <= ex_max]
            dropped = len(extras) - len(entry["extras"])
            if dropped:
                print(f"  ✂️  추가 추천 {dropped}곳 제외 ({ex_max}분 초과/미확인)")
        for cond in list(entry.get("by_condition", {}).keys()):
            entry["by_condition"][cond], _ = verify_and_enrich(entry["by_condition"][cond], kakao_key, naver_id, naver_secret)

    n = len(entry.get("restaurants", []))
    n_ok = sum(1 for r in entry.get("restaurants", []) if r.get("verified"))
    nc = len(entry.get("by_condition", {}))
    print(f"✅ [{meal}] 기본 {n}곳(검증 {n_ok})" + (f" + 컨디션 {nc}종" if nc else ""))
    return entry, text


def build_mood_ctx(weather, stock, headlines):
    """날씨·주가·JB금융 소식을 맛집 선정에 반영할 분위기 블록으로."""
    if not (weather or headlines or stock):
        return ""
    return (
        "\n[오늘의 분위기 — 맛집 선정에 함께 반영]\n"
        f"· 날씨: {weather or '정보 없음'}\n"
        f"· JB금융지주 주가: {stock or '정보 없음'}\n"
        f"· JB금융그룹 소식: {headlines or '특이사항 없음'}\n"
        "위 날씨와 'JB금융 소식'의 분위기를 함께 판단해서 어울리는 맛집을 골라줘.\n"
        "회사 소식 반영 가이드(과하지 않게 살짝 가중):\n"
        "  · 호재(호실적·수상·자사주 매입·주가 강세 등) → 축하·회식 분위기 좋은 곳, 살짝 특별한 메뉴\n"
        "  · 악재·무거운 소식(실적 부진·구조조정·시장 불안 등) → 든든하고 위로가 되는 따뜻한 메뉴\n"
        "  · 중요 일정(주주총회·실적발표·인사·접대 이슈 등) → 격식 있고 조용한, 접대 가능한 식당\n"
        "  · 특이사항 없으면 날씨와 평소 취향 위주로.\n"
        "그리고 comment 한 줄에 오늘 날씨와 회사 분위기를 자연스럽게 녹여줘.\n")


def build_entry(today, lunch, dinner, bar, daily_msg, weather, news):
    """점심(최상위) + 저녁·술집(meals)으로 history 엔트리 구성."""
    entry = {
        "date": today,
        "comment": lunch.get("comment", ""),
        "message": daily_msg,
        "weather": weather,
        "news": news,
        "restaurants": lunch.get("restaurants", []),
        "extras": lunch.get("extras", []),
        "by_condition": lunch.get("by_condition", {}),
        "meals": {},
    }
    if dinner:
        entry["meals"]["저녁"] = {
            "restaurants": dinner.get("restaurants", []),
            "extras": dinner.get("extras", []),
            "by_condition": dinner.get("by_condition", {}),
        }
    if bar:
        entry["meals"]["술집"] = {
            "restaurants": bar.get("restaurants", []),
            "extras": bar.get("extras", []),
        }
    return entry


def process_location(client, loc, today, date_compact, kakao_key, naver_id, naver_secret, news, stock):
    """한 위치의 점심·저녁·술집 추천을 생성해 history 파일에 저장.
    JB빌딩(key=jb)은 index.html 갱신 + 이메일까지, 그 외는 data/history-{key}.json만."""
    is_jb = loc["key"] == "jb"
    path = "history.json" if is_jb else os.path.join("data", f"history-{loc['key']}.json")
    global DIVERSITY
    DIVERSITY = effective_diversity(loc)   # 위치별 override (없으면 전역 기본)
    if isinstance(loc.get("diversity"), dict):
        print(f"   🎲 [{loc['name']}] 위치 전용 다양성 적용")
    if DIVERSITY["rotate"]:
        random.seed(f"{today}-{loc['key']}")   # 위치·날짜 시드
    set_origin(loc["lat"], loc["lng"], loc.get("region") or "여의도",
               loc.get("short") or loc.get("name") or loc["key"],
               loc.get("rec_profile") or "auto", loc.get("rec_custom"))
    print(f"\n=== 📍 [{loc['name']}] 추천 생성 (방식={PROFILE['label']}) → {path} ===")

    recent = get_recent_names(path, days=DIVERSITY["recent_days"]) if DIVERSITY["recent_days"] > 0 else []
    if recent:
        print(f"🔁 최근 추천 {len(recent)}곳 회피: {', '.join(recent[:8])}…")
    weather = fetch_weather(loc.get("region"))
    headlines = " / ".join(n["title"] for n in news[:3]) if news else ""
    mood_ctx = build_mood_ctx(weather, stock, headlines)

    # 끼니 생성 — None(레이트리밋/JSON실패) 시 90초 후 1회 재시도 + 누락 명시.
    # 술집은 항상 마지막이라 분당 토큰 한도에 가장 잘 걸려 조용히 누락되던 문제 보강.
    missing = []

    def gen_meal(meal, with_cond):
        m, txt = generate_meal(client, today, date_compact, meal, with_cond,
                               kakao_key, naver_id, naver_secret, recent, mood_ctx)
        if not m:
            print(f"⚠️ [{loc['name']}] {meal} 1차 생성 실패 — 90초 후 1회 재시도")
            time.sleep(90)   # 분당 토큰 창 초기화 대기
            m, txt = generate_meal(client, today, date_compact, meal, with_cond,
                                   kakao_key, naver_id, naver_secret, recent, mood_ctx)
            if not m:
                print(f"❌ [{loc['name']}] {meal} 재시도도 실패 — 이 끼니 누락")
                missing.append(meal)
        return m, txt

    lunch, lunch_text = gen_meal("점심", True)
    if not lunch:
        print(f"❌ [{loc['name']}] 점심 생성 실패 — 이 위치 건너뜀")
        return False
    time.sleep(70)  # rate limit(10k tokens/min) 회피
    dinner, _ = gen_meal("저녁", True)
    time.sleep(70)
    bar, _ = gen_meal("술집", False)
    if missing:
        print(f"⚠️ [{loc['name']}] 누락 끼니: {', '.join(missing)} (점심은 정상)")

    daily_msg = generate_daily_message(client, weather, news, lunch.get("restaurants", []))
    new_entry = build_entry(today, lunch, dinner, bar, daily_msg, weather, news)

    print(f"📂 {path} 업데이트 중...")
    history = update_history(new_entry, path)
    print(f"✅ [{loc['name']}] 총 {len(history['recommendations'])}일치 저장됨")

    if is_jb:
        print("🌐 index.html 업데이트 중...")
        update_index_html(history)
        print("📧 이메일 발송 중...")
        send_email(new_entry.get("restaurants", []), today, lunch_text, new_entry.get("comment", ""),
                   weather=weather, news=news, extras=new_entry.get("extras", []), message=daily_msg)
    return True


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

    # 배치 대상 위치 (DB의 app_locations 전체) — JB 먼저 처리되도록 정렬
    locations = fetch_locations()
    locations.sort(key=lambda l: (l["key"] != "jb",))  # jb 우선
    print(f"📍 배치 대상 위치 {len(locations)}곳: {', '.join(l['name'] for l in locations)}")

    global DIVERSITY, _BASE_DIVERSITY
    _BASE_DIVERSITY = fetch_diversity()
    DIVERSITY = dict(_BASE_DIVERSITY)
    _on = [k for k in ('avoid','spread','pool','rotate') if DIVERSITY[k]]
    print(f"🎲 추천 다양성(전역 기본): {', '.join(_on) or '없음'} (회피 {DIVERSITY['recent_days']}일) · 위치별 override 가능")

    # JB금융 뉴스·주가는 전 위치 공통 → 1회만 수집해 모든 위치에 공유
    print("\n📰 JB금융 뉴스·주가 수집 중 (전 위치 공통)...")
    news = fetch_jb_news(naver_id, naver_secret, today=today)
    stock = fetch_jb_stock()
    time.sleep(20)
    news_web = fetch_jb_news_web(client, today=today)
    news = merge_news(news_web, news, count=6)
    news = prioritize_focus_news(news, count=3)   # AX·AI·디지털을 최상단으로
    focus_n = sum(1 for n in news if n.get("focus"))
    print(f"📰 최종 JB 소식 {len(news)}건 (AX·AI·디지털 {focus_n}건 최상단)")

    ok_count = 0
    for i, loc in enumerate(locations):
        try:
            if process_location(client, loc, today, date_compact, kakao_key, naver_id, naver_secret, news, stock):
                ok_count += 1
        except Exception as e:
            print(f"❌ [{loc.get('name')}] 처리 중 오류 (계속 진행): {e}")
        if i < len(locations) - 1:
            time.sleep(40)  # 위치 간 rate limit 여유

    if ok_count == 0:
        print("❌ 모든 위치 생성 실패")
        sys.exit(1)
    print(f"\n✨ 완료! {ok_count}/{len(locations)}개 위치 생성됨")


if __name__ == "__main__":
    main()

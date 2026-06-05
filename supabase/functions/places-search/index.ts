// Supabase Edge Function: 내 주변 맛집 검색 (카카오 + 네이버 병합)
// 리뷰 탭에서 주변 맛집을 보여주거나 이름으로 검색해 리뷰를 남기게 함.
import "jsr:@supabase/functions-js/edge-runtime.d.ts";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

// JB빌딩 (여의나루로 77) 좌표
const JB_LAT = 37.5240914884765;
const JB_LNG = 126.927376521939;

type Place = { name: string; cuisine: string; distM: number; address: string; source: string; phone: string; lat: number; lng: number };

function walkMin(distM: number): number {
  return Math.max(1, Math.round((distM * 1.35) / 80));
}

function haversineM(lat1: number, lng1: number, lat2: number, lng2: number): number {
  const R = 6371000, rad = (d: number) => (d * Math.PI) / 180;
  const dp = rad(lat2 - lat1), dl = rad(lng2 - lng1);
  const a = Math.sin(dp / 2) ** 2 +
    Math.cos(rad(lat1)) * Math.cos(rad(lat2)) * Math.sin(dl / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

// 카카오: 좌표 기준 검색 (키워드 또는 음식점 카테고리)
async function kakaoSearch(key: string, query: string, cx: number, cy: number): Promise<Place[]> {
  const url = query
    ? `https://dapi.kakao.com/v2/local/search/keyword.json` +
      `?query=${encodeURIComponent(query)}&x=${cx}&y=${cy}&radius=2000&sort=distance&size=15`
    : `https://dapi.kakao.com/v2/local/search/category.json` +
      `?category_group_code=FD6&x=${cx}&y=${cy}&radius=1000&sort=distance&size=15`;
  try {
    const r = await fetch(url, { headers: { Authorization: `KakaoAK ${key}` } });
    if (!r.ok) return [];
    const docs = (await r.json()).documents || [];
    return docs
      .filter((d: Record<string, string>) =>
        !query || d.category_group_code === "FD6" || d.category_group_code === "CE7")
      .map((d: Record<string, string>) => ({
        name: d.place_name,
        cuisine: (d.category_name || "").replace("음식점 > ", "").split(" > ")[0] || "",
        distM: Number(d.distance || 0),
        address: d.road_address_name || d.address_name || "",
        source: "카카오",
        phone: d.phone || "",
        lng: Number(d.x || 0),
        lat: Number(d.y || 0),
      }));
  } catch {
    return [];
  }
}

// 카카오 좌표 → 동(洞) 이름 (네이버 지역검색 키워드 생성용)
async function regionName(key: string, cx: number, cy: number): Promise<string> {
  try {
    const r = await fetch(
      `https://dapi.kakao.com/v2/local/geo/coord2regioncode.json?x=${cx}&y=${cy}`,
      { headers: { Authorization: `KakaoAK ${key}` } },
    );
    if (!r.ok) return "";
    const docs = (await r.json()).documents || [];
    const d = docs.find((x: Record<string, string>) => x.region_type === "H") || docs[0];
    return d ? (d.region_3depth_name || d.region_2depth_name || "") : "";
  } catch {
    return "";
  }
}

// 네이버: 키워드 지역검색 후 중심좌표 기준 거리 필터
async function naverSearch(
  id: string, secret: string, keywords: string[], cx: number, cy: number, maxRadius: number,
): Promise<Place[]> {
  if (!id || !secret) return [];
  const headers = { "X-Naver-Client-Id": id, "X-Naver-Client-Secret": secret };
  const out: Place[] = [];
  const seen = new Set<string>();
  const results = await Promise.all(keywords.map(async (kw) => {
    try {
      const r = await fetch(
        `https://openapi.naver.com/v1/search/local.json?query=${encodeURIComponent(kw)}&display=5&sort=comment`,
        { headers },
      );
      if (!r.ok) return [];
      return (await r.json()).items || [];
    } catch {
      return [];
    }
  }));
  for (const items of results) {
    for (const it of items) {
      const name = (it.title || "").replace(/<[^>]+>/g, "").trim();
      if (!name || seen.has(name)) continue;
      const plat = Number(it.mapy) / 1e7, plng = Number(it.mapx) / 1e7;
      if (!isFinite(plat) || !isFinite(plng)) continue;
      const distM = haversineM(cy, cx, plat, plng);
      if (distM > maxRadius) continue;
      seen.add(name);
      out.push({
        name,
        cuisine: (it.category || "").split(">").pop()?.trim() || "",
        distM,
        address: it.roadAddress || it.address || "",
        source: "네이버",
        phone: it.telephone || "",
        lat: plat,
        lng: plng,
      });
    }
  }
  return out;
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  try {
    const body = await req.json();
    const query = (body.query || "").toString().trim();

    const kakaoKey = Deno.env.get("KAKAO_REST_API_KEY") || "";
    const naverId = Deno.env.get("NAVER_CLIENT_ID") || "";
    const naverSecret = Deno.env.get("NAVER_CLIENT_SECRET") || "";
    if (!kakaoKey) return json({ error: "kakao key missing" }, 500);

    // 위치: GPS 좌표가 오면 그 기준, 없으면 JB빌딩 기준
    const lat = Number(body.lat), lng = Number(body.lng);
    const useGps = isFinite(lat) && isFinite(lng) && lat !== 0 && lng !== 0;
    const cy = useGps ? lat : JB_LAT;
    const cx = useGps ? lng : JB_LNG;
    const origin = useGps ? "현위치" : "JB빌딩";

    // 네이버 키워드: 검색어가 있으면 그대로, 없으면 "{동} 맛집/점심"
    let naverKeywords: string[];
    let radius: number;
    if (query) {
      naverKeywords = [query];
      radius = 2000;
    } else {
      const region = await regionName(kakaoKey, cx, cy);
      naverKeywords = region ? [`${region} 맛집`, `${region} 점심`, `${region} 한식`] : [];
      radius = 1000;
    }

    const [kakaoList, naverList] = await Promise.all([
      kakaoSearch(kakaoKey, query, cx, cy),
      naverSearch(naverId, naverSecret, naverKeywords, cx, cy, radius * 1.5),
    ]);

    // 병합: 이름 정규화로 중복 제거(카카오 우선 — 거리 정확), 거리순 정렬
    const merged: Place[] = [];
    const seen = new Set<string>();
    const byKey: Record<string, Place> = {};
    for (const p of [...kakaoList, ...naverList]) {
      const key = p.name.replace(/\s+/g, "");
      if (seen.has(key)) {
        if (!byKey[key].phone && p.phone) byKey[key].phone = p.phone;  // 전화번호 보강
        continue;
      }
      seen.add(key);
      byKey[key] = p;
      merged.push(p);
    }
    merged.sort((a, b) => a.distM - b.distM);

    const places = merged.slice(0, 18).map((p) => ({
      name: p.name,
      cuisine: p.cuisine,
      distance: `도보 ${walkMin(p.distM)}분`,
      address: p.address,
      source: p.source,
      phone: p.phone,
      lat: p.lat,
      lng: p.lng,
      verified: true,
    }));

    return json({ places, origin });
  } catch (e) {
    return json({ error: String(e) }, 500);
  }
});

function json(obj: unknown, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { ...CORS, "content-type": "application/json" },
  });
}

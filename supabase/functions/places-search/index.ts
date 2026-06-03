// Supabase Edge Function: 내 근처(JB빌딩) 맛집 키워드 검색
// 리뷰 탭에서 추천에 없는 가게도 검색해 리뷰를 남길 수 있게 함.
import "jsr:@supabase/functions-js/edge-runtime.d.ts";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

// JB빌딩 (여의나루로 77) 좌표
const JB_LAT = 37.5240914884765;
const JB_LNG = 126.927376521939;

function walkMin(distM: number): number {
  return Math.max(1, Math.round((distM * 1.35) / 80));
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  try {
    const body = await req.json();
    const query = (body.query || "").toString().trim();
    if (!query) return json({ places: [] });

    const kakaoKey = Deno.env.get("KAKAO_REST_API_KEY") || "";
    if (!kakaoKey) return json({ error: "kakao key missing" }, 500);

    const q = encodeURIComponent(query);
    const url = `https://dapi.kakao.com/v2/local/search/keyword.json` +
      `?query=${q}&x=${JB_LNG}&y=${JB_LAT}&radius=2000&sort=distance&size=15`;
    const r = await fetch(url, { headers: { Authorization: `KakaoAK ${kakaoKey}` } });
    if (!r.ok) return json({ error: "kakao error", detail: await r.text() }, 502);
    const docs = (await r.json()).documents || [];

    const map = (d: Record<string, string>) => {
      const cat = (d.category_name || "").replace("음식점 > ", "").split(" > ")[0] ||
        d.category_group_name || "";
      const mins = walkMin(Number(d.distance || 0));
      return {
        name: d.place_name,
        cuisine: cat,
        distance: `도보 ${mins}분 (카카오)`,
        address: d.road_address_name || d.address_name || "",
        group: d.category_group_code || "",
        verified: true,
      };
    };
    let places = docs.map(map);
    // 음식점/카페(FD6/CE7) 우선, 없으면 전체
    const food = places.filter((p: { group: string }) => p.group === "FD6" || p.group === "CE7");
    if (food.length) places = food;
    places.forEach((p: Record<string, unknown>) => { delete p.group; });

    return json({ places });
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

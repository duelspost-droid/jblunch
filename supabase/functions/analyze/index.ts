// Supabase Edge Function: 시간대(점심/저녁/술집) + 자유 입력 → 맞춤 맛집 실시간 생성
// generate_lunch.py와 동일한 하이브리드 구조:
//   ① Kakao 반경 검색으로 실제 후보 목록 → 프롬프트 주입
//   ② Claude 추천
//   ③ Kakao/Naver로 거리 실측 + 검증(verified 플래그) — 병렬 처리로 고속화
import "jsr:@supabase/functions-js/edge-runtime.d.ts";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

// JB빌딩 (여의나루로 77) 좌표 — Kakao 주소 지오코딩으로 검증됨
const JB_LAT = 37.5240914884765;
const JB_LNG = 126.927376521939;

const MEAL_DESC: Record<string, string> = {
  "점심": "점심 식사로 좋은 음식점",
  "저녁": "저녁 식사로 좋은 음식점 (점심보다 분위기 있거나 회식·모임에도 좋은 곳 포함)",
  "술집": "술 한잔 하기 좋은 술집 (이자카야, 호프, 포차, 와인바, 안주 맛집 등)",
};

const NON_FOOD = ["병원", "약국", "은행", "학원", "부동산", "미용", "마트", "편의점",
  "주유소", "세탁", "사무", "오피스", "관공서", "PC방", "노래"];

function haversineM(lat1: number, lng1: number, lat2: number, lng2: number): number {
  const R = 6371000, rad = (d: number) => (d * Math.PI) / 180;
  const dp = rad(lat2 - lat1), dl = rad(lng2 - lng1);
  const a = Math.sin(dp / 2) ** 2 +
    Math.cos(rad(lat1)) * Math.cos(rad(lat2)) * Math.sin(dl / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

function cleanName(name: string): string {
  let c = name.replace(/\s*[\(\[\{].*?[\)\]\}]\s*/g, " ");
  c = c.split(/\s+[-–—·:]\s+/)[0];
  return c.trim() || name.trim();
}

// null=정보없음(보류), false=비음식점, true=음식점
function isFood(cat: string | null): boolean | null {
  if (!cat) return null;
  return NON_FOOD.some((kw) => cat.includes(kw)) ? false : true;
}

function walkMin(distM: number): number {
  return Math.max(1, Math.round((distM * 1.35) / 80));
}

// "도보 7분 (카카오) / 6분 (네이버)" → 최소 분(가장 가까운 추정). 미확인이면 null
function parseMinutes(distance: unknown): number | null {
  const nums = String(distance || "").match(/(\d+)\s*분/g);
  if (!nums) return null;
  return Math.min(...nums.map((s) => parseInt(s)));
}

// JB 반경 내 가까운 검증 음식점 (Kakao 카테고리 거리순)
async function kakaoNearby(kakaoKey: string): Promise<string[]> {
  if (!kakaoKey) return [];
  try {
    const url = `https://dapi.kakao.com/v2/local/search/category.json` +
      `?category_group_code=FD6&x=${JB_LNG}&y=${JB_LAT}&radius=600&sort=distance&size=15`;
    const r = await fetch(url, { headers: { Authorization: `KakaoAK ${kakaoKey}` } });
    if (!r.ok) return [];
    const data = await r.json();
    return (data.documents || []).map((d: Record<string, string>) => {
      const cat = (d.category_name || "").replace("음식점 > ", "").split(" > ")[0];
      const mins = Math.max(1, Math.round(Number(d.distance || 0) / 80));
      return `${d.place_name}(${cat}, 도보 ${mins}분)`;
    });
  } catch {
    return [];
  }
}

// 여의도 인기·유명 맛집 (Naver 리뷰순 — 조금 멀어도 다양성용)
async function naverPopular(meal: string, id: string, secret: string): Promise<string[]> {
  if (!id || !secret) return [];
  const queries = meal === "술집"
    ? ["여의도 술집", "여의도 이자카야", "여의도 와인바"]
    : ["여의도 맛집", "여의도 유명 맛집", `여의도 ${meal}`];
  const seen = new Set<string>();
  const out: string[] = [];
  const results = await Promise.all(queries.map(async (kw) => {
    try {
      const q = encodeURIComponent(kw);
      const r = await fetch(
        `https://openapi.naver.com/v1/search/local.json?query=${q}&display=5&sort=comment`,
        { headers: { "X-Naver-Client-Id": id, "X-Naver-Client-Secret": secret } },
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
      seen.add(name);
      const cat = (it.category || "").split(">").pop()?.trim() || "";
      out.push(cat ? `${name}(${cat})` : name);
    }
  }
  return out;
}

// 후보 블록 = 가까운 검증(Kakao) + 인기 유명(Naver) — 병렬
async function fetchCandidates(meal: string, kakaoKey: string, naverId: string, naverSecret: string): Promise<string> {
  const [near, popular] = await Promise.all([
    kakaoNearby(kakaoKey),
    naverPopular(meal, naverId, naverSecret),
  ]);
  if (!near.length && !popular.length) return "";
  let block = "\n[참고 목록 — 실제 존재하는 가게]\n";
  if (near.length) block += `· 가까운 검증 맛집(거리 정확): ${near.join(", ")}\n`;
  if (popular.length) block += `· 여의도 인기·유명 맛집(조금 멀 수 있음): ${popular.join(", ")}\n`;
  return block;
}

async function kakaoPlace(name: string, key: string): Promise<[number, number, string] | null> {
  try {
    const q = encodeURIComponent(`${cleanName(name)} 여의도`);
    const r = await fetch(
      `https://dapi.kakao.com/v2/local/search/keyword.json?query=${q}&size=1`,
      { headers: { Authorization: `KakaoAK ${key}` } },
    );
    if (!r.ok) return null;
    const docs = (await r.json()).documents || [];
    if (!docs.length) return null;
    const d = docs[0];
    let cat = d.category_name || "";
    if (d.category_group_code && !["FD6", "CE7"].includes(d.category_group_code)) cat = "비음식점:" + cat;
    return [Number(d.y), Number(d.x), cat];
  } catch {
    return null;
  }
}

async function naverPlace(name: string, id: string, secret: string): Promise<[number, number, string] | null> {
  try {
    const q = encodeURIComponent(`${cleanName(name)} 여의도`);
    const r = await fetch(
      `https://openapi.naver.com/v1/search/local.json?query=${q}&display=1`,
      { headers: { "X-Naver-Client-Id": id, "X-Naver-Client-Secret": secret } },
    );
    if (!r.ok) return null;
    const items = (await r.json()).items || [];
    if (!items.length) return null;
    const it = items[0];
    return [Number(it.mapy) / 1e7, Number(it.mapx) / 1e7, it.category || ""];
  } catch {
    return null;
  }
}

// 한 가게 검증 + 거리 (Kakao/Naver 병렬)
async function verifyOne(
  r: Record<string, unknown>,
  kakaoKey: string,
  naverId: string,
  naverSecret: string,
): Promise<void> {
  const name = cleanName(String(r.name || ""));
  r.name = name;

  const [k, n] = await Promise.all([
    kakaoKey ? kakaoPlace(name, kakaoKey) : Promise.resolve(null),
    naverId && naverSecret ? naverPlace(name, naverId, naverSecret) : Promise.resolve(null),
  ]);

  const dists: Record<string, number> = {};
  const votes: (boolean | null)[] = [];
  if (k) { dists["카카오"] = walkMin(haversineM(JB_LAT, JB_LNG, k[0], k[1])); votes.push(isFood(k[2])); }
  if (n) { dists["네이버"] = walkMin(haversineM(JB_LAT, JB_LNG, n[0], n[1])); votes.push(isFood(n[2])); }

  const foundFood = !(votes.length && votes.every((v) => v === false));
  if (!Object.keys(dists).length || !foundFood) {
    r.verified = false;
    r.distance = "거리 미확인";
    return;
  }
  r.verified = true;
  if (dists["카카오"] && dists["네이버"]) r.distance = `도보 ${dists["카카오"]}분 (카카오) / ${dists["네이버"]}분 (네이버)`;
  else if (dists["카카오"]) r.distance = `도보 ${dists["카카오"]}분 (카카오)`;
  else r.distance = `도보 ${dists["네이버"]}분 (네이버)`;
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });

  try {
    const body = await req.json();
    const text = (body.text || "").toString().trim();
    const meal = MEAL_DESC[body.meal] ? body.meal : "점심";
    const describe = (body.describe || "").toString().trim();

    const apiKey = Deno.env.get("ANTHROPIC_API_KEY");
    if (!apiKey) return json({ error: "API key not set" }, 500);
    const kakaoKey = Deno.env.get("KAKAO_REST_API_KEY") || "";
    const naverId = Deno.env.get("NAVER_CLIENT_ID") || "";
    const naverSecret = Deno.env.get("NAVER_CLIENT_SECRET") || "";

    // ── describe 모드: 특정 맛집의 디테일한 소개 1건 생성 ──────────
    if (describe) {
      const hintCuisine = (body.cuisine || "").toString().trim();
      const hintAddr = (body.address || "").toString().trim();
      const dPrompt = `너는 서울 여의도 맛집 큐레이터야. 아래 식당을 잘 모르는 사람에게 소개하는 글을 써줘.
식당명: ${describe}${hintCuisine ? `\n종류: ${hintCuisine}` : ""}${hintAddr ? `\n주소: ${hintAddr}` : ""}
규칙:
- intro: 반드시 정중한 존댓말("~합니다/~예요/~좋아요" 체)로만 써. 반말("~야/~어") 절대 금지.
  2~3문장. 대표 메뉴/맛 특징, 분위기, 어떤 자리(점심/회식/접대 등)에 좋은지 구체적으로.
  실제로 아는 정보를 우선하되 과장·허위 금지. intro 안에는 가격·평점·전화번호 같은 변동 정보를 쓰지 마.
- price: 이 식당의 대략적 가격대를 "저렴"/"보통"/"비쌈" 중 하나로만. 종류·이름으로 합리적으로 추정.
- 반드시 JSON만 출력: {"intro":"...","price":"보통"}`;
      const dResp = await fetch("https://api.anthropic.com/v1/messages", {
        method: "POST",
        headers: { "x-api-key": apiKey, "anthropic-version": "2023-06-01", "content-type": "application/json" },
        body: JSON.stringify({ model: "claude-haiku-4-5-20251001", max_tokens: 300, messages: [{ role: "user", content: dPrompt }] }),
      });
      if (!dResp.ok) {
        const err = await dResp.text();
        const rl = dResp.status === 429 || err.includes("rate_limit");
        return json({ error: rl ? "rate_limited" : "claude error" }, rl ? 429 : 502);
      }
      const dData = await dResp.json();
      const dContent = dData.content?.[0]?.text ?? "";
      const dMatch = dContent.match(/\{[\s\S]*\}/);
      let intro = "", price = "";
      if (dMatch) { const p = JSON.parse(dMatch[0]); intro = p.intro || ""; price = p.price || ""; }
      else intro = dContent.trim();
      if (!["저렴", "보통", "비쌈"].includes(price)) price = "";
      return json({ intro, price }, 200);
    }

    // ① 실제 후보 목록 (가까운 검증 + 인기 유명)
    const candBlock = await fetchCandidates(meal, kakaoKey, naverId, naverSecret);

    const condLine = text
      ? `\n사용자가 입력한 오늘의 컨디션/취향: "${text}" — 이걸 최우선으로 반영해줘.
참고 해석: "와인"=와인바·와인 페어링 좋은 곳 / "임원"·"접대"·"상사"=임원 모시거나 접대하기 좋은 격식 있는 고급 식당(프라이빗 룸 선호) / "회식"=단체 회식 좋은 곳.`
      : "";

    const prompt = `너는 서울 여의도 JB빌딩(여의나루로 77, 영등포구 여의도동) 근처 맛집 큐레이터야.
사용자에게 오늘 ${meal} 자리로 갈 만한, ${MEAL_DESC[meal]} 5곳을 추천해줘.${condLine}
${candBlock}
규칙:
- 실제 존재하는 여의도/여의나루 근처 가게로, 시간대(${meal})에 어울리게 골라.
- restaurants: 가까운 검증 맛집 5곳 (도보 가까운 곳 우선, 음식 종류 최대한 다양하게).
- extras: 추가 추천 2곳 — 아래 형식 그대로 2개 (둘 다 반드시 도보 10분 이내의 실제 가게):
  · 1곳은 tag="근처유명" — 도보 10분 이내의 여의도 유명 맛집
  · 1곳은 tag="검색유명" — 웹에서 평이 좋은 유명 맛집이되 도보 10분 이내
  · extras는 restaurants 5곳과 겹치지 않게.
- comment: 추천 컨셉을 설명하는 친근한 존댓말 1~2문장.
- 각 가게: name, cuisine(종류), feature(특징/추천메뉴 한 줄), price(저렴/보통/비쌈), distance(도보 N분).
- 반드시 JSON만 출력:
{"comment":"...","restaurants":[{"name":"","cuisine":"","feature":"","price":"","distance":""}, ...5개],"extras":[{"name":"","cuisine":"","feature":"","price":"","distance":"","tag":"근처유명"},{"name":"","cuisine":"","feature":"","price":"","distance":"","tag":"검색유명"}]}`;

    // Claude 호출 — rate limit(429) 시 짧게 1회 재시도
    const callClaude = () => fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "x-api-key": apiKey,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
      },
      body: JSON.stringify({
        model: "claude-haiku-4-5-20251001",
        max_tokens: 1200,
        messages: [{ role: "user", content: prompt }],
      }),
    });

    let resp = await callClaude();
    if (resp.status === 429) {
      await new Promise((r) => setTimeout(r, 3000));
      resp = await callClaude();
    }

    if (!resp.ok) {
      const err = await resp.text();
      const rateLimited = resp.status === 429 || err.includes("rate_limit");
      return json({
        error: rateLimited ? "rate_limited" : "claude error",
        message: rateLimited ? "AI 요청이 잠시 몰렸어요. 30초 후 다시 시도해주세요." : "추천 생성 오류",
        detail: err,
      }, rateLimited ? 429 : 502);
    }

    const data = await resp.json();
    const content = data.content?.[0]?.text ?? "";
    const match = content.match(/\{[\s\S]*\}/);
    if (!match) return json({ error: "parse failed", raw: content }, 502);

    const parsed = JSON.parse(match[0]);
    const stamp = Date.now();
    const restaurants: Record<string, unknown>[] = parsed.restaurants || [];
    const extras: Record<string, unknown>[] = Array.isArray(parsed.extras) ? parsed.extras : [];

    // ③ 거리 실측 + 검증 — 기본 5곳 + 추가 2곳 모두 병렬 처리 (속도)
    await Promise.all(
      [...restaurants, ...extras].map((r) => verifyOne(r, kakaoKey, naverId, naverSecret)),
    );
    restaurants.forEach((r, i) => { r.id = `custom-${stamp}-${i + 1}`; });
    // 추가 추천은 도보 10분 이내 + 검증된 곳만 (거리 미확인/초과 제외)
    const nearExtras = extras.filter((r) => {
      const m = parseMinutes(r.distance);
      return r.verified === true && m !== null && m <= 10;
    });
    nearExtras.forEach((r, i) => { r.id = `custom-${stamp}-x${i + 1}`; });
    parsed.extras = nearExtras;

    return json(parsed, 200);
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

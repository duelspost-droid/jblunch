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

// ── 추천 방식 프리셋 (배치 generate_lunch.py와 동일 개념) ────────
type Profile = { label: string; radii: number[]; minCand: number; walkMax: number; extraMax: number; allowFar: "sparse" | "no" | "always" };
const PROFILES: Record<string, Profile> = {
  walk_tight: { label: "도보 최우선",   radii: [300, 500],              minCand: 6,  walkMax: 99, extraMax: 8,  allowFar: "no" },
  walk:       { label: "도보 위주",     radii: [700, 1000],             minCand: 8,  walkMax: 99, extraMax: 12, allowFar: "no" },
  auto:       { label: "근거리 자동 (권장)", radii: [600, 1500, 3000, 5000], minCand: 12, walkMax: 14, extraMax: 20, allowFar: "sparse" },
  town:       { label: "동네 (도보+동네)", radii: [1500, 3000],            minCand: 10, walkMax: 12, extraMax: 20, allowFar: "sparse" },
  drive_near: { label: "차량 근교",     radii: [2000, 5000],            minCand: 8,  walkMax: 6,  extraMax: 30, allowFar: "sparse" },
  drive:      { label: "차량 권역",     radii: [3000, 6000, 9000],      minCand: 8,  walkMax: 5,  extraMax: 40, allowFar: "sparse" },
  wide:       { label: "광역 (시·도)",  radii: [5000, 10000, 15000],    minCand: 6,  walkMax: 4,  extraMax: 60, allowFar: "always" },
  city:       { label: "도심 밀집",     radii: [500, 1000, 1500],       minCand: 15, walkMax: 12, extraMax: 12, allowFar: "sparse" },
};
// 프리셋 키 → 파라미터. 'custom'이면 위치의 커스텀 객체를 기본값에 병합.
function getProfile(key: string, custom?: Record<string, unknown> | null): Profile {
  if (key === "custom" && custom && typeof custom === "object") {
    const base = PROFILES.auto;
    const radii = Array.isArray(custom.radii) && custom.radii.length ? (custom.radii as number[]).map(Number).filter((n) => n > 0) : base.radii;
    const allow = ["sparse", "no", "always"].includes(String(custom.allowFar)) ? custom.allowFar as Profile["allowFar"] : base.allowFar;
    return {
      label: "맞춤",
      radii: radii.length ? radii : base.radii,
      minCand: Number(custom.minCand) > 0 ? Number(custom.minCand) : base.minCand,
      walkMax: Number(custom.walkMax) > 0 ? Number(custom.walkMax) : base.walkMax,
      extraMax: Number(custom.extraMax) > 0 ? Number(custom.extraMax) : base.extraMax,
      allowFar: allow,
    };
  }
  return PROFILES[key] || PROFILES.auto;
}

// 거리(m) → 표기 [라벨, 도보분]. 도보 상한 초과면 차로/거리로 자동 전환
function fmtDist(meters: number, prof: Profile): [string, number] {
  const wm = Math.max(1, Math.round((meters * 1.35) / 80));
  if (wm <= prof.walkMax) return [`도보 ${wm}분`, wm];
  const drive = Math.max(2, Math.round(meters / 350));
  return [`차로 ${drive}분 · 약 ${(meters / 1000).toFixed(1)}km`, wm];
}

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

// 기준 위치 반경 내 가까운 검증 음식점 (Kakao 카테고리 거리순, 적응형 반경 ①)
async function kakaoNearby(kakaoKey: string, lat: number, lng: number, prof: Profile): Promise<string[]> {
  if (!kakaoKey) return [];
  let best: string[] = [];
  for (const radius of prof.radii) {
    try {
      const url = `https://dapi.kakao.com/v2/local/search/category.json` +
        `?category_group_code=FD6&x=${lng}&y=${lat}&radius=${radius}&sort=distance&size=15`;
      const r = await fetch(url, { headers: { Authorization: `KakaoAK ${kakaoKey}` } });
      if (r.ok) {
        const data = await r.json();
        best = (data.documents || []).map((d: Record<string, string>) => {
          const cat = (d.category_name || "").replace("음식점 > ", "").split(" > ")[0];
          const [label] = fmtDist(Number(d.distance || 0), prof);
          return `${d.place_name}(${cat}, ${label})`;
        });
        if (best.length >= prof.minCand) break;   // 충분히 모이면 확대 중단
      }
    } catch { /* 다음 반경 시도 */ }
  }
  return best;
}

// 지역 인기·유명 맛집 (Naver 리뷰순 — 조금 멀어도 다양성용)
async function naverPopular(meal: string, id: string, secret: string, region: string): Promise<string[]> {
  if (!id || !secret) return [];
  const queries = meal === "술집"
    ? [`${region} 술집`, `${region} 이자카야`, `${region} 와인바`]
    : [`${region} 맛집`, `${region} 유명 맛집`, `${region} ${meal}`];
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

// 후보 블록 = 가까운 검증(Kakao, 적응형) + 인기 유명(Naver) — 병렬
// 반환: { block, sparse } — sparse=근처 후보가 minCand에 못 미침(③ 분기용)
async function fetchCandidates(meal: string, kakaoKey: string, naverId: string, naverSecret: string, lat: number, lng: number, region: string, prof: Profile): Promise<{ block: string; sparse: boolean }> {
  const [near, popular] = await Promise.all([
    kakaoNearby(kakaoKey, lat, lng, prof),
    naverPopular(meal, naverId, naverSecret, region),
  ]);
  const sparse = near.length < prof.minCand;
  if (!near.length && !popular.length) return { block: "", sparse: true };
  let block = "\n[참고 목록 — 실제 존재하는 가게, 가까운 순]\n";
  if (near.length) block += `· 가까운 검증 맛집(거리 정확): ${near.join(", ")}\n`;
  if (popular.length) block += `· ${region} 인기·유명 맛집(조금 멀 수 있음): ${popular.join(", ")}\n`;
  return { block, sparse };
}

async function kakaoPlace(name: string, key: string, region: string): Promise<[number, number, string] | null> {
  try {
    const q = encodeURIComponent(`${cleanName(name)} ${region}`);
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

async function naverPlace(name: string, id: string, secret: string, region: string): Promise<[number, number, string] | null> {
  try {
    const q = encodeURIComponent(`${cleanName(name)} ${region}`);
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
  lat = JB_LAT,
  lng = JB_LNG,
  region = "여의도",
  prof: Profile = PROFILES.auto,
): Promise<void> {
  const name = cleanName(String(r.name || ""));
  r.name = name;

  const [k, n] = await Promise.all([
    kakaoKey ? kakaoPlace(name, kakaoKey, region) : Promise.resolve(null),
    naverId && naverSecret ? naverPlace(name, naverId, naverSecret, region) : Promise.resolve(null),
  ]);

  const labels: Record<string, string> = {};
  const votes: (boolean | null)[] = [];
  if (k) { labels["카카오"] = fmtDist(haversineM(lat, lng, k[0], k[1]), prof)[0]; votes.push(isFood(k[2])); }
  if (n) { labels["네이버"] = fmtDist(haversineM(lat, lng, n[0], n[1]), prof)[0]; votes.push(isFood(n[2])); }

  const foundFood = !(votes.length && votes.every((v) => v === false));
  if (!Object.keys(labels).length || !foundFood) {
    r.verified = false;
    r.distance = "거리 미확인";
    return;
  }
  r.verified = true;
  if (labels["카카오"] && labels["네이버"]) r.distance = `${labels["카카오"]} (카카오) / ${labels["네이버"]} (네이버)`;
  else if (labels["카카오"]) r.distance = `${labels["카카오"]} (카카오)`;
  else r.distance = `${labels["네이버"]} (네이버)`;
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });

  try {
    const body = await req.json();
    const text = (body.text || "").toString().trim();
    const meal = MEAL_DESC[body.meal] ? body.meal : "점심";
    const describe = (body.describe || "").toString().trim();
    // 기준 위치(선택한 지점) — 없으면 JB빌딩(여의도) 기본값
    const lat = Number(body.lat) || JB_LAT;
    const lng = Number(body.lng) || JB_LNG;
    const region = (body.region || "여의도").toString().trim() || "여의도";
    const place = (body.place || "JB빌딩").toString().trim() || "JB빌딩";
    const prof = getProfile((body.profile || "auto").toString(), body.custom || null);

    const apiKey = Deno.env.get("ANTHROPIC_API_KEY");
    if (!apiKey) return json({ error: "API key not set" }, 500);
    const kakaoKey = Deno.env.get("KAKAO_REST_API_KEY") || "";
    const naverId = Deno.env.get("NAVER_CLIENT_ID") || "";
    const naverSecret = Deno.env.get("NAVER_CLIENT_SECRET") || "";

    // ── describe 모드: 특정 맛집의 디테일한 소개 1건 생성 (서버 공유 캐시) ──
    if (describe) {
      const SB_URL = Deno.env.get("SUPABASE_URL") || "";
      const SB_SRV = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || "";
      const metaHeaders = { apikey: SB_SRV, Authorization: `Bearer ${SB_SRV}`, "Content-Type": "application/json" };
      // ① 캐시 조회 — 있으면 Claude 호출 없이 즉시 반환 (크레딧 절약)
      if (SB_URL && SB_SRV) {
        try {
          const cr = await fetch(
            `${SB_URL}/rest/v1/place_meta?name=eq.${encodeURIComponent(describe)}&select=intro,price,distance,verified,menu,region`,
            { headers: metaHeaders },
          );
          const rows = await cr.json();
          // 소개가 있고 메뉴 컬럼이 채워졌으며(빈문자열 포함) 같은 지역의 캐시일 때만 반환.
          // 지역이 다르거나 미지정(과거 오염분)이면 통과시켜 현재 위치 기준으로 재생성.
          if (Array.isArray(rows) && rows.length && rows[0].intro && rows[0].menu != null && rows[0].region === region) {
            const { region: _r, ...payload } = rows[0];
            return json({ ...payload, cached: true }, 200);
          }
        } catch (_e) { /* 캐시 실패 시 생성으로 진행 */ }
      }
      const hintCuisine = (body.cuisine || "").toString().trim();
      const hintAddr = (body.address || "").toString().trim();
      const dPrompt = `너는 ${region} 일대 맛집 큐레이터야. 아래 식당의 소개글을 써줘.
식당명: ${describe}${hintCuisine ? `\n종류: ${hintCuisine}` : ""}${hintAddr ? `\n주소: ${hintAddr}` : `\n위치: ${region} ${place} 인근`}
- 이 식당은 ${region}에 있는 가게야. 소개에 엉뚱한 다른 지역(예: 실제 위치가 아닌 곳)을 언급하지 마.
규칙:
- 이 가게를 정확히 몰라도 절대 사과하거나 "정보가 부족/잘 모르겠다"고 쓰지 마. 그런 면책 문구 금지.
  이름과 종류에서 자연스럽게 연상되는 소개를 그럴듯하게 작성해.
- intro: 정중한 존댓말("~합니다/~예요/~좋아요" 체)로 풍부하게 3~4문장(약 120~200자).
  ① 대표 메뉴·맛의 특징, ② 분위기·인테리어·좌석, ③ 어떤 자리(점심/회식/접대/혼밥 등)에 어울리는지,
  ④ 추천 포인트(이런 분께 좋아요) 순으로 자연스럽게 풀어써. 단조롭지 않게 구체적으로.
  구체적 수상·연혁 같은 확인 불가한 허위는 만들지 말되, 무난하고 일반적인 소개는 OK. 가격·평점·전화번호는 쓰지 마.
- price: 종류·이름으로 합리적으로 추정 → "저렴"/"보통"/"비쌈" 중 하나.
- menu: 이 가게에서 먹을 법한 대표 메뉴 2~4개를 "메뉴 ~가격" 형식으로, 중간점(·)으로 구분한 한 줄.
  가격은 종류·이름 기반의 대략적 추정치(예: "한우 등심 ~6만원 · 육회비빔밥 ~1.5만원 · 냉면 ~1.2만원").
  메뉴를 합리적으로 떠올리기 어려우면 빈 문자열("")로.
- 반드시 JSON만 출력(다른 말·사과 없이): {"intro":"...","price":"보통","menu":"..."}`;
      const dResp = await fetch("https://api.anthropic.com/v1/messages", {
        method: "POST",
        headers: { "x-api-key": apiKey, "anthropic-version": "2023-06-01", "content-type": "application/json" },
        body: JSON.stringify({ model: "claude-haiku-4-5-20251001", max_tokens: 600, messages: [{ role: "user", content: dPrompt }] }),
      });
      if (!dResp.ok) {
        const err = await dResp.text();
        const rl = dResp.status === 429 || err.includes("rate_limit");
        return json({ error: rl ? "rate_limited" : "claude error" }, rl ? 429 : 502);
      }
      const dData = await dResp.json();
      const dContent = dData.content?.[0]?.text ?? "";
      const dMatch = dContent.match(/\{[\s\S]*\}/);
      let intro = "", price = "", menu = "";
      if (dMatch) { try { const p = JSON.parse(dMatch[0]); intro = p.intro || ""; price = p.price || ""; menu = (p.menu || "").toString().slice(0, 200); } catch (_e) { /* 파싱 실패 → intro 비움 */ } }
      // 면책/사과성 응답이면 소개로 쓰지 않음 (프론트가 기본 설명으로 폴백)
      if (/죄송|정보가 부족|잘 모르|알 수 없|확실하게 알|제공하는 것을 피|작성해드리겠습니다/.test(intro)) intro = "";
      if (!["저렴", "보통", "비쌈"].includes(price)) price = "";
      // 카카오·네이버 양쪽 거리 실측 (선택 위치 기준)
      const dObj: Record<string, unknown> = { name: describe };
      await verifyOne(dObj, kakaoKey, naverId, naverSecret, lat, lng, region, prof);
      const result = { intro, price, menu, distance: dObj.distance || "", verified: dObj.verified };
      // ② 결과를 서버 캐시에 저장 (upsert) — 다음부터 모든 사용자가 재사용
      if (SB_URL && SB_SRV && intro) {
        try {
          await fetch(`${SB_URL}/rest/v1/place_meta?on_conflict=name`, {
            method: "POST",
            headers: { ...metaHeaders, Prefer: "resolution=merge-duplicates" },
            body: JSON.stringify({ name: describe, ...result, region, updated_at: new Date().toISOString() }),
          });
        } catch (_e) { /* 저장 실패 무시 */ }
      }
      return json(result, 200);
    }

    // ① 실제 후보 목록 (가까운 검증 + 인기 유명, 적응형 반경)
    const { block: candBlock, sparse } = await fetchCandidates(meal, kakaoKey, naverId, naverSecret, lat, lng, region, prof);
    // ③ 후보 충분도·프로파일 → 먼 곳 허용 여부 + 거리 분산 지시
    const farOk = prof.allowFar === "always" || (prof.allowFar === "sparse" && sparse);
    const nearRule = farOk
      ? `이 지역은 가까운 가게가 많지 않아요. 위 목록을 우선 쓰되, 목록에 없어도 실제 영업 중인 ${region} 인근 알려진 가게를 추가해도 됩니다. 다소 멀어도 괜찮아요.`
      : `반드시 위 목록 안에서, 가까운 순서대로 골라줘. 목록에 없는 멀리 떨어진 유명 맛집은 넣지 마.`;

    const condLine = text
      ? `\n사용자가 입력한 오늘의 컨디션/취향: "${text}" — 이걸 최우선으로 반영해줘.
참고 해석: "와인"=와인바·와인 페어링 좋은 곳 / "임원"·"접대"·"상사"=임원 모시거나 접대하기 좋은 격식 있는 고급 식당(프라이빗 룸 선호) / "회식"=단체 회식 좋은 곳.`
      : "";

    const prompt = `너는 ${region} ${place} 근처 맛집 큐레이터야.
사용자에게 오늘 ${meal} 자리로 갈 만한, ${MEAL_DESC[meal]} 5곳을 추천해줘.${condLine}
${candBlock}
규칙:
- 실제 존재하는 ${region}(${place} 인근) 가게로, 시간대(${meal})에 어울리게 골라. ${nearRule}
- restaurants: 추천 맛집 5곳 (가까운 곳 우선, 음식 종류 최대한 다양하게).
- extras: 추가 추천 2곳 — 아래 형식 그대로 2개 (둘 다 반드시 약 ${prof.extraMax}분 이내의 실제 가게):
  · 1곳은 tag="근처유명" — 약 ${prof.extraMax}분 이내의 ${region} 유명 맛집
  · 1곳은 tag="검색유명" — 웹에서 평이 좋은 유명 맛집이되 약 ${prof.extraMax}분 이내
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
      [...restaurants, ...extras].map((r) => verifyOne(r, kakaoKey, naverId, naverSecret, lat, lng, region, prof)),
    );
    restaurants.forEach((r, i) => { r.id = `custom-${stamp}-${i + 1}`; });
    // 추가 추천은 프로파일 거리 상한 이내 + 검증된 곳만 (거리 미확인/초과 제외)
    const nearExtras = extras.filter((r) => {
      const m = parseMinutes(r.distance);
      return r.verified === true && m !== null && m <= prof.extraMax;
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

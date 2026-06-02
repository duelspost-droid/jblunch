// Supabase Edge Function: 시간대(점심/저녁/술집) + 자유 입력 → 맞춤 맛집 실시간 생성
import "jsr:@supabase/functions-js/edge-runtime.d.ts";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

const MEAL_DESC: Record<string, string> = {
  "점심": "점심 식사로 좋은 음식점",
  "저녁": "저녁 식사로 좋은 음식점 (점심보다 분위기 있거나 회식·모임에도 좋은 곳 포함)",
  "술집": "술 한잔 하기 좋은 술집 (이자카야, 호프, 포차, 와인바, 안주 맛집 등)",
};

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });

  try {
    const body = await req.json();
    const text = (body.text || "").toString().trim();
    const meal = MEAL_DESC[body.meal] ? body.meal : "점심";

    const apiKey = Deno.env.get("ANTHROPIC_API_KEY");
    if (!apiKey) return json({ error: "API key not set" }, 500);

    const condLine = text
      ? `\n사용자가 입력한 오늘의 컨디션/취향: "${text}" — 이걸 최우선으로 반영해줘.
참고 해석: "와인"=와인바·와인 페어링 좋은 곳 / "임원"·"접대"·"상사"=임원 모시거나 접대하기 좋은 격식 있는 고급 식당(프라이빗 룸 선호) / "회식"=단체 회식 좋은 곳.`
      : "";

    const prompt = `너는 서울 여의도 JB빌딩(여의나루로 77, 영등포구 여의도동) 근처 맛집 큐레이터야.
사용자에게 오늘 ${meal} 자리로 갈 만한, ${MEAL_DESC[meal]} 5곳을 추천해줘.${condLine}

규칙:
- 실제 존재하는 여의도/여의나루 근처 가게로, 시간대(${meal})에 어울리게 골라.
- comment: 추천 컨셉을 설명하는 친근한 존댓말 1~2문장.
- 각 가게: name, cuisine(종류), feature(특징/추천메뉴 한 줄), price(저렴/보통/비쌈), distance(도보 N분).
- 반드시 JSON만 출력:
{"comment":"...","restaurants":[{"name":"","cuisine":"","feature":"","price":"","distance":""}, ...5개]}`;

    const resp = await fetch("https://api.anthropic.com/v1/messages", {
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

    if (!resp.ok) {
      const err = await resp.text();
      return json({ error: "claude error", detail: err }, 502);
    }

    const data = await resp.json();
    const content = data.content?.[0]?.text ?? "";
    const match = content.match(/\{[\s\S]*\}/);
    if (!match) return json({ error: "parse failed", raw: content }, 502);

    const parsed = JSON.parse(match[0]);
    const stamp = Date.now();
    (parsed.restaurants || []).forEach((r: Record<string, unknown>, i: number) => {
      r.id = `custom-${stamp}-${i + 1}`;
    });
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

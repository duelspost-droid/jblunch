// Supabase Edge Function: 자유 입력 → 맞춤 맛집 실시간 생성
// 사용자의 자유 텍스트를 Claude가 분석해 여의나루 근처 맞춤 맛집 5곳 + 코멘트를 새로 생성
import "jsr:@supabase/functions-js/edge-runtime.d.ts";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });

  try {
    const { text } = await req.json();
    if (!text || typeof text !== "string") {
      return json({ error: "text required" }, 400);
    }

    const apiKey = Deno.env.get("ANTHROPIC_API_KEY");
    if (!apiKey) return json({ error: "API key not set" }, 500);

    const prompt = `너는 서울 여의도 여의나루로 77 (영등포구 여의도동) 근처 점심 맛집 큐레이터야.
사용자가 오늘 점심 컨디션/기분을 자유롭게 입력했어. 이 입력에 딱 맞는 실제 여의도/여의나루 근처 점심 맛집 5곳을 추천해줘.

사용자 입력: "${text}"

규칙:
- 실제 존재하는 여의도/여의나루 근처 맛집으로, 입력한 기분·상황·취향에 맞게 골라.
- comment: 입력에 공감하고 추천 컨셉을 설명하는 친근한 존댓말 1~2문장.
- 각 맛집: name(이름), cuisine(음식종류), feature(특징/추천메뉴 한 줄), price(저렴/보통/비쌈), distance(도보 N분 형태).
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
    // id 부여 (리뷰/방문 키용)
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

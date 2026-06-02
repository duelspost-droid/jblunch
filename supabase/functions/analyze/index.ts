// Supabase Edge Function: 자유 입력 컨디션 분석
// 사용자의 자유 텍스트를 Claude Haiku로 분석해 8개 컨디션 중 가장 맞는 것 + 맞춤 코멘트 반환
import "jsr:@supabase/functions-js/edge-runtime.d.ts";

const CONDITIONS = ["해장 필요", "매콤하게", "가볍게", "든든하게", "일식", "한식", "고기", "혼밥"];

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

    const prompt = `사용자가 점심 메뉴 컨디션을 자유롭게 입력했어. 아래 8개 분류 중 가장 적합한 하나를 골라줘.

입력: "${text}"

분류: ${CONDITIONS.join(", ")}

규칙:
- 반드시 8개 중 하나를 골라. 애매하면 가장 가까운 걸로.
- comment는 사용자 입력에 공감하는 친근한 1~2문장 한국어 (반말 아님, 존댓말).
- JSON만 출력: {"condition":"분류명","comment":"코멘트"}`;

    const resp = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "x-api-key": apiKey,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
      },
      body: JSON.stringify({
        model: "claude-haiku-4-5-20251001",
        max_tokens: 300,
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
    // 컨디션 검증
    if (!CONDITIONS.includes(parsed.condition)) parsed.condition = "";
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

// Supabase Edge Function: JB금융지주(175330) 주가 조회 (네이버 금융 프록시, CORS 우회)
import "jsr:@supabase/functions-js/edge-runtime.d.ts";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
};

const CODE = "175330"; // JB금융지주 (KOSPI)

function json(obj: unknown, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { ...CORS, "content-type": "application/json", "Cache-Control": "public, max-age=60" },
  });
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  try {
    const r = await fetch(`https://m.stock.naver.com/api/stock/${CODE}/basic`, {
      headers: { "User-Agent": "Mozilla/5.0", "Accept": "application/json" },
    });
    if (!r.ok) return json({ error: `naver ${r.status}` }, 502);
    const d = await r.json();
    const dir = d?.compareToPreviousPrice?.name || ""; // RISING / FALLING / STEADY
    return json({
      name: d.stockName || "JB금융지주",
      code: CODE,
      price: d.closePrice || "",                       // 현재가(장중) 또는 종가
      change: d.compareToPreviousClosePrice || "",     // 전일대비 (부호 포함)
      changeRate: d.fluctuationsRatio ?? "",           // 등락률 %
      direction: dir,                                  // RISING/FALLING/STEADY
      dirText: d?.compareToPreviousPrice?.text || "",  // 상승/하락/보합
      marketStatus: d.marketStatus || "",              // OPEN/CLOSE 등
      tradedAt: d.localTradedAt || "",
    });
  } catch (e) {
    return json({ error: String(e) }, 500);
  }
});

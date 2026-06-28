// Supabase Edge Function: 접속/작업 로그 기록
// 클라이언트가 호출 → 서버에서 IP·UA를 읽어 access_logs에 service role로 저장.
import "jsr:@supabase/functions-js/edge-runtime.d.ts";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  try {
    const body = await req.json().catch(() => ({}));
    // 클라이언트가 실제 쓰는 action만 허용. admin_* 등 비클라이언트 action 주입을
    // 차단해 관리자 잠금(admin_fail 위조 → 정상 IP 잠금) 및 감사 로그 오염을 막는다.
    const ALLOWED = new Set([
      "visit", "location_change", "custom_recommend",
      "review_create", "review_edit", "review_delete",
      "place_search", "nearby", "home_nearby",
    ]);
    let action = (body.action || "visit").toString().slice(0, 60);
    if (!ALLOWED.has(action)) action = "other";
    const detail = (body.detail || "").toString().slice(0, 300);
    const path = (body.path || "").toString().slice(0, 200);

    // 실제 클라이언트 IP (프록시 헤더에서)
    const ip = (req.headers.get("x-forwarded-for") || "")
      .split(",")[0].trim() || req.headers.get("x-real-ip") || "unknown";
    const ua = (req.headers.get("user-agent") || "").slice(0, 300);

    const SB_URL = Deno.env.get("SUPABASE_URL");
    const SERVICE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
    if (!SB_URL || !SERVICE_KEY) return json({ ok: false, error: "no service key" }, 500);

    const res = await fetch(`${SB_URL}/rest/v1/access_logs`, {
      method: "POST",
      headers: {
        apikey: SERVICE_KEY,
        Authorization: `Bearer ${SERVICE_KEY}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ ip, action, detail, user_agent: ua, path }),
    });
    if (!res.ok) return json({ ok: false, error: await res.text() }, 500);
    return json({ ok: true });
  } catch (e) {
    return json({ ok: false, error: String(e) }, 500);
  }
});

function json(obj: unknown, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { ...CORS, "content-type": "application/json" },
  });
}

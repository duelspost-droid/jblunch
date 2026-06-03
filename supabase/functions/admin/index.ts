// Supabase Edge Function: 관리자 — 비밀번호 검증 후 접속/작업 로그 조회
// 비밀번호는 서버 시크릿(ADMIN_PASSWORD)과 비교 (클라이언트에 노출 안 됨).
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
    const password = (body.password || "").toString();
    const adminPw = Deno.env.get("ADMIN_PASSWORD") || "";

    if (!adminPw) return json({ error: "ADMIN_PASSWORD 미설정" }, 500);
    if (password !== adminPw) return json({ error: "비밀번호가 올바르지 않습니다." }, 401);

    const SB_URL = Deno.env.get("SUPABASE_URL");
    const SERVICE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
    if (!SB_URL || !SERVICE_KEY) return json({ error: "no service key" }, 500);

    const limit = Math.min(Number(body.limit) || 200, 500);
    const r = await fetch(
      `${SB_URL}/rest/v1/access_logs?select=*&order=created_at.desc&limit=${limit}`,
      { headers: { apikey: SERVICE_KEY, Authorization: `Bearer ${SERVICE_KEY}` } },
    );
    if (!r.ok) return json({ error: await r.text() }, 500);
    const logs = await r.json();

    // 간단 통계
    const ipSet = new Set<string>();
    const actionCount: Record<string, number> = {};
    for (const l of logs) {
      if (l.ip) ipSet.add(l.ip);
      actionCount[l.action] = (actionCount[l.action] || 0) + 1;
    }
    return json({
      ok: true,
      logs,
      stats: { total: logs.length, uniqueIPs: ipSet.size, actions: actionCount },
    });
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

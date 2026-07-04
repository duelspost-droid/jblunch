// Supabase Edge Function: 카카오 refresh_token 저장소 (get/set)
//
// 배치(kakao_send.py)가 refresh_token을 여기서 읽고, 카카오가 회전시켜 준 새 토큰을
// 여기에 되저장한다 → 사실상 무기한 유지. service_role 키는 이 함수 안(서버)에만 있고,
// 호출자는 공유 시크릿(KAKAO_TOKEN_SECRET)으로만 인증한다.
import "jsr:@supabase/functions-js/edge-runtime.d.ts";

const SB_URL = Deno.env.get("SUPABASE_URL")!;
const SERVICE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const SECRET = Deno.env.get("KAKAO_TOKEN_SECRET") || "";
const SH = {
  apikey: SERVICE_KEY,
  Authorization: `Bearer ${SERVICE_KEY}`,
  "Content-Type": "application/json",
};

// 상수 시간 비교 (타이밍 공격 방지)
function tsEq(a: string, b: string): boolean {
  if (!a.length || a.length !== b.length) return false;
  let r = 0;
  for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return r === 0;
}

function json(obj: unknown, status = 200): Response {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

Deno.serve(async (req) => {
  if (req.method !== "POST") return json({ error: "method not allowed" }, 405);
  try {
    const body = await req.json().catch(() => ({}));
    if (!SECRET || !tsEq((body.secret || "").toString(), SECRET)) {
      return json({ error: "unauthorized" }, 401);
    }
    const action = (body.action || "get").toString();

    if (action === "get") {
      const r = await fetch(
        `${SB_URL}/rest/v1/kakao_tokens?id=eq.1&select=refresh_token`,
        { headers: SH },
      );
      const rows = r.ok ? await r.json() : [];
      return json({ refresh_token: (rows[0] && rows[0].refresh_token) || null });
    }

    if (action === "set") {
      const rt = (body.refresh_token || "").toString();
      if (rt.length < 20) return json({ error: "bad token" }, 400);
      const r = await fetch(`${SB_URL}/rest/v1/kakao_tokens?on_conflict=id`, {
        method: "POST",
        headers: { ...SH, Prefer: "resolution=merge-duplicates" },
        body: JSON.stringify({
          id: 1,
          refresh_token: rt,
          updated_at: new Date().toISOString(),
        }),
      });
      if (!r.ok) return json({ error: "save failed: " + (await r.text()) }, 500);
      return json({ ok: true });
    }

    return json({ error: "unknown action" }, 400);
  } catch (e) {
    return json({ error: String(e) }, 500);
  }
});

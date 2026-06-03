// Supabase Edge Function: 관리자 — 로그 조회 + 비밀번호 변경
// 비밀번호는 admin_config 테이블에 SHA-256 해시로 저장(앱에서 변경 가능).
// 최초 1회: ADMIN_PASSWORD 시크릿을 해시해 시드.
import "jsr:@supabase/functions-js/edge-runtime.d.ts";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

const SB_URL = Deno.env.get("SUPABASE_URL")!;
const SERVICE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const SH = { apikey: SERVICE_KEY, Authorization: `Bearer ${SERVICE_KEY}`, "Content-Type": "application/json" };

async function sha256(s: string): Promise<string> {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return Array.from(new Uint8Array(buf)).map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function getStoredHash(): Promise<string> {
  const r = await fetch(`${SB_URL}/rest/v1/admin_config?id=eq.1&select=password_hash`, { headers: SH });
  const rows = r.ok ? await r.json() : [];
  return rows[0]?.password_hash || "";
}

async function ensureSeed(): Promise<string> {
  let hash = await getStoredHash();
  if (!hash) {
    const seed = Deno.env.get("ADMIN_PASSWORD") || "change-me";
    hash = await sha256(seed);
    await fetch(`${SB_URL}/rest/v1/admin_config`, {
      method: "POST",
      headers: { ...SH, Prefer: "resolution=merge-duplicates" },
      body: JSON.stringify({ id: 1, password_hash: hash }),
    });
  }
  return hash;
}

async function setHash(hash: string) {
  await fetch(`${SB_URL}/rest/v1/admin_config?id=eq.1`, {
    method: "PATCH",
    headers: SH,
    body: JSON.stringify({ password_hash: hash, updated_at: new Date().toISOString() }),
  });
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  try {
    const body = await req.json().catch(() => ({}));
    const action = (body.action || "logs").toString();
    const password = (body.password || "").toString();

    const storedHash = await ensureSeed();
    if (await sha256(password) !== storedHash) {
      return json({ error: "비밀번호가 올바르지 않습니다." }, 401);
    }

    // 비밀번호 변경
    if (action === "change_password") {
      const np = (body.newPassword || "").toString();
      if (np.length < 6) return json({ error: "새 비밀번호는 6자 이상이어야 합니다." }, 400);
      await setHash(await sha256(np));
      return json({ ok: true, changed: true });
    }

    // 로그 조회 (기본)
    const limit = Math.min(Number(body.limit) || 200, 500);
    const r = await fetch(
      `${SB_URL}/rest/v1/access_logs?select=*&order=created_at.desc&limit=${limit}`,
      { headers: SH },
    );
    if (!r.ok) return json({ error: await r.text() }, 500);
    const logs = await r.json();

    const ipSet = new Set<string>();
    const actionCount: Record<string, number> = {};
    for (const l of logs) {
      if (l.ip) ipSet.add(l.ip);
      actionCount[l.action] = (actionCount[l.action] || 0) + 1;
    }
    return json({ ok: true, logs, stats: { total: logs.length, uniqueIPs: ipSet.size, actions: actionCount } });
  } catch (e) {
    return json({ error: String(e) }, 500);
  }
});

function json(obj: unknown, status = 200) {
  return new Response(JSON.stringify(obj), { status, headers: { ...CORS, "content-type": "application/json" } });
}

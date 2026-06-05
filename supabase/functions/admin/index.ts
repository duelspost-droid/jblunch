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

    // ── 방문자 통계 (KST 기준 일/월/연/전체) ──
    const visitStats = await visitorStats();

    return json({
      ok: true,
      logs,
      stats: { total: logs.length, uniqueIPs: ipSet.size, actions: actionCount },
      visits: visitStats,
    });
  } catch (e) {
    return json({ error: String(e) }, 500);
  }
});

// PostgREST count(content-range 헤더)로 visit 수 집계
async function countSince(field: "visit_total" | string, sinceUTC?: string): Promise<number> {
  let url = `${SB_URL}/rest/v1/access_logs?select=id&action=eq.visit`;
  if (sinceUTC) url += `&created_at=gte.${encodeURIComponent(sinceUTC)}`;
  const r = await fetch(url, { headers: { ...SH, Prefer: "count=exact", Range: "0-0" } });
  const cr = r.headers.get("content-range") || "";   // "0-0/123" 또는 "*/0"
  const tot = cr.split("/")[1];
  return !tot || tot === "*" ? 0 : Number(tot);
}

// 고유 방문자(IP) 수 — 기간 내 visit 로그의 distinct IP
async function uniqueSince(sinceUTC?: string): Promise<number> {
  let url = `${SB_URL}/rest/v1/access_logs?select=ip&action=eq.visit&ip=not.is.null`;
  if (sinceUTC) url += `&created_at=gte.${encodeURIComponent(sinceUTC)}`;
  url += "&limit=20000";
  const r = await fetch(url, { headers: SH });
  if (!r.ok) return 0;
  const rows = await r.json();
  return new Set(rows.map((x: { ip: string }) => x.ip)).size;
}

async function visitorStats() {
  // KST(UTC+9) 기준 일/월/연 시작 시각을 UTC ISO로 변환
  const KST = 9 * 3600 * 1000;
  const k = new Date(Date.now() + KST);
  const y = k.getUTCFullYear(), m = k.getUTCMonth(), d = k.getUTCDate();
  const dayUTC = new Date(Date.UTC(y, m, d) - KST).toISOString();
  const monthUTC = new Date(Date.UTC(y, m, 1) - KST).toISOString();
  const yearUTC = new Date(Date.UTC(y, 0, 1) - KST).toISOString();

  const [day, month, year, total, uDay, uMonth, uYear, uTotal, daily] = await Promise.all([
    countSince("visit", dayUTC),
    countSince("visit", monthUTC),
    countSince("visit", yearUTC),
    countSince("visit"),
    uniqueSince(dayUTC),
    uniqueSince(monthUTC),
    uniqueSince(yearUTC),
    uniqueSince(),
    dailySeries(14),
  ]);
  return {
    day, month, year, total,
    unique: { day: uDay, month: uMonth, year: uYear, total: uTotal },
    daily,
  };
}

// 최근 N일 일별 방문 수 (KST 기준) → [{date:'MM/DD', count}]
async function dailySeries(days: number) {
  const KST = 9 * 3600 * 1000;
  const k = new Date(Date.now() + KST);
  const startKST = Date.UTC(k.getUTCFullYear(), k.getUTCMonth(), k.getUTCDate()) - (days - 1) * 86400000;
  const startUTC = new Date(startKST - KST).toISOString();
  const r = await fetch(
    `${SB_URL}/rest/v1/access_logs?select=created_at&action=eq.visit&created_at=gte.${encodeURIComponent(startUTC)}&limit=100000`,
    { headers: SH },
  );
  const rows = r.ok ? await r.json() : [];
  const bucket: Record<string, number> = {};
  for (const row of rows) {
    const t = new Date(row.created_at).getTime() + KST;
    const dk = new Date(t);
    const key = `${dk.getUTCFullYear()}-${String(dk.getUTCMonth() + 1).padStart(2, "0")}-${String(dk.getUTCDate()).padStart(2, "0")}`;
    bucket[key] = (bucket[key] || 0) + 1;
  }
  const out = [];
  for (let i = 0; i < days; i++) {
    const t = startKST + i * 86400000;
    const dk = new Date(t);
    const key = `${dk.getUTCFullYear()}-${String(dk.getUTCMonth() + 1).padStart(2, "0")}-${String(dk.getUTCDate()).padStart(2, "0")}`;
    out.push({ date: `${dk.getUTCMonth() + 1}/${dk.getUTCDate()}`, count: bucket[key] || 0 });
  }
  return out;
}

function json(obj: unknown, status = 200) {
  return new Response(JSON.stringify(obj), { status, headers: { ...CORS, "content-type": "application/json" } });
}

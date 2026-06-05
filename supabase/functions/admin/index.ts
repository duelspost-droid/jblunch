// Supabase Edge Function: 관리자 — 로그/통계 조회 + 비밀번호 변경 (보안 강화)
// 보안: PBKDF2 솔트 해시 / 세션 토큰(비번 미저장) / 무차별 대입 잠금 / 관리자 감사 로그.
import "jsr:@supabase/functions-js/edge-runtime.d.ts";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

const SB_URL = Deno.env.get("SUPABASE_URL")!;
const SERVICE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const SH = { apikey: SERVICE_KEY, Authorization: `Bearer ${SERVICE_KEY}`, "Content-Type": "application/json" };

const PBKDF2_ITER = 120000;
const SESSION_HOURS = 8;
const LOCK_WINDOW_MIN = 15;
const LOCK_MAX_FAILS = 5;

// ── 암호 유틸 ──────────────────────────────────────────────────
function toHex(buf: ArrayBuffer): string {
  return Array.from(new Uint8Array(buf)).map((b) => b.toString(16).padStart(2, "0")).join("");
}
async function sha256(s: string): Promise<string> {
  return toHex(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s)));
}
async function pbkdf2(password: string, salt: string, iter: number): Promise<string> {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(password), "PBKDF2", false, ["deriveBits"]);
  const bits = await crypto.subtle.deriveBits(
    { name: "PBKDF2", salt: new TextEncoder().encode(salt), iterations: iter, hash: "SHA-256" }, key, 256);
  return toHex(bits);
}
function randHex(bytes: number): string {
  const a = new Uint8Array(bytes); crypto.getRandomValues(a);
  return Array.from(a).map((b) => b.toString(16).padStart(2, "0")).join("");
}
function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let r = 0; for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return r === 0;
}

// ── 비밀번호(admin_config) ────────────────────────────────────
async function getConfig(): Promise<{ password_hash: string; salt: string | null; iterations: number | null }> {
  const r = await fetch(`${SB_URL}/rest/v1/admin_config?id=eq.1&select=password_hash,salt,iterations`, { headers: SH });
  const rows = r.ok ? await r.json() : [];
  return rows[0] || { password_hash: "", salt: null, iterations: null };
}
async function ensureSeed() {
  const cfg = await getConfig();
  if (!cfg.password_hash) {
    const seed = Deno.env.get("ADMIN_PASSWORD") || "change-me";
    await setPassword(seed);
  }
}
async function setPassword(newPw: string) {
  const salt = randHex(16);
  const hash = await pbkdf2(newPw, salt, PBKDF2_ITER);
  await fetch(`${SB_URL}/rest/v1/admin_config`, {
    method: "POST",
    headers: { ...SH, Prefer: "resolution=merge-duplicates" },
    body: JSON.stringify({ id: 1, password_hash: hash, salt, iterations: PBKDF2_ITER, updated_at: new Date().toISOString() }),
  });
}
async function verifyPassword(password: string): Promise<boolean> {
  if (!password) return false;
  const cfg = await getConfig();
  if (!cfg.password_hash) return false;
  if (cfg.salt) {
    const h = await pbkdf2(password, cfg.salt, cfg.iterations || PBKDF2_ITER);
    return timingSafeEqual(h, cfg.password_hash);
  }
  // 레거시 평문 SHA-256 → 검증 성공 시 PBKDF2로 자동 마이그레이션
  const legacy = await sha256(password);
  if (timingSafeEqual(legacy, cfg.password_hash)) {
    await setPassword(password);
    return true;
  }
  return false;
}

// ── 세션 토큰(admin_sessions) ─────────────────────────────────
async function createSession(ip: string): Promise<{ token: string; expires_at: string }> {
  const token = randHex(32);
  const expires_at = new Date(Date.now() + SESSION_HOURS * 3600 * 1000).toISOString();
  await fetch(`${SB_URL}/rest/v1/admin_sessions`, { method: "POST", headers: SH, body: JSON.stringify({ token, ip, expires_at }) });
  return { token, expires_at };
}
async function validSession(token: string): Promise<boolean> {
  if (!token || token.length < 32) return false;
  const r = await fetch(`${SB_URL}/rest/v1/admin_sessions?token=eq.${encodeURIComponent(token)}&select=expires_at`, { headers: SH });
  const rows = r.ok ? await r.json() : [];
  if (!rows.length) return false;
  return new Date(rows[0].expires_at).getTime() > Date.now();
}
async function deleteSession(token: string) {
  if (token) await fetch(`${SB_URL}/rest/v1/admin_sessions?token=eq.${encodeURIComponent(token)}`, { method: "DELETE", headers: SH });
}
async function deleteAllSessions() {
  await fetch(`${SB_URL}/rest/v1/admin_sessions?token=neq.__none__`, { method: "DELETE", headers: SH });
}

// ── 감사 로그 / 무차별 대입 잠금 ──────────────────────────────
function getIP(req: Request): string {
  return (req.headers.get("x-forwarded-for") || "").split(",")[0].trim() || req.headers.get("x-real-ip") || "unknown";
}
async function logAdmin(action: string, ip: string, detail = "", ua = "") {
  await fetch(`${SB_URL}/rest/v1/access_logs`, {
    method: "POST", headers: SH,
    body: JSON.stringify({ ip, action, detail, user_agent: ua.slice(0, 300), path: "manage" }),
  }).catch(() => {});
}
async function isLocked(ip: string): Promise<boolean> {
  const since = new Date(Date.now() - LOCK_WINDOW_MIN * 60 * 1000).toISOString();
  const url = `${SB_URL}/rest/v1/access_logs?select=id&action=eq.admin_fail&ip=eq.${encodeURIComponent(ip)}&created_at=gte.${encodeURIComponent(since)}`;
  const r = await fetch(url, { headers: { ...SH, Prefer: "count=exact", Range: "0-0" } });
  const cr = r.headers.get("content-range") || "";
  const tot = cr.split("/")[1];
  return !(!tot || tot === "*") && Number(tot) >= LOCK_MAX_FAILS;
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  try {
    const body = await req.json().catch(() => ({}));
    const action = (body.action || "logs").toString();
    const ip = getIP(req);
    const ua = req.headers.get("user-agent") || "";
    await ensureSeed();

    // ── 로그인: 비번 → 토큰 발급 ──
    if (action === "login") {
      if (await isLocked(ip)) {
        return json({ error: `로그인 시도가 많아 잠시 잠겼습니다. ${LOCK_WINDOW_MIN}분 후 다시 시도하세요.` }, 429);
      }
      if (!await verifyPassword((body.password || "").toString())) {
        await logAdmin("admin_fail", ip, "", ua);
        return json({ error: "비밀번호가 올바르지 않습니다." }, 401);
      }
      await logAdmin("admin_login", ip, "", ua);
      const sess = await createSession(ip);
      const data = await dashboard(body.limit);
      return json({ ok: true, token: sess.token, expiresAt: sess.expires_at, ...data });
    }

    // ── 그 외: 토큰 인증 (레거시 비번도 허용, 단 잠금 적용) ──
    const token = (body.token || "").toString();
    let authed = await validSession(token);
    if (!authed && body.password) {
      if (await isLocked(ip)) return json({ error: "잠시 후 다시 시도하세요." }, 429);
      authed = await verifyPassword(body.password.toString());
      if (!authed) { await logAdmin("admin_fail", ip, "", ua); }
    }
    if (!authed) return json({ error: "인증이 필요합니다." }, 401);

    // ── 로그아웃 ──
    if (action === "logout") { await deleteSession(token); return json({ ok: true }); }

    // ── 비밀번호 변경: 현재 비번 재확인 필수 ──
    if (action === "change_password") {
      const cur = (body.currentPassword || body.password || "").toString();
      if (!await verifyPassword(cur)) return json({ error: "현재 비밀번호가 올바르지 않습니다." }, 401);
      const np = (body.newPassword || "").toString();
      if (np.length < 8) return json({ error: "새 비밀번호는 8자 이상이어야 합니다." }, 400);
      await setPassword(np);
      await deleteAllSessions();            // 비번 변경 시 모든 세션 무효화
      await logAdmin("admin_pw_change", ip, "", ua);
      const sess = await createSession(ip);  // 현재 기기는 새 토큰 발급
      return json({ ok: true, changed: true, token: sess.token, expiresAt: sess.expires_at });
    }

    // ── 로그/통계 조회 (기본) ──
    return json({ ok: true, ...(await dashboard(body.limit)) });
  } catch (e) {
    return json({ error: String(e) }, 500);
  }
});

// ── 대시보드 데이터 ───────────────────────────────────────────
async function dashboard(limitRaw?: unknown) {
  const limit = Math.min(Number(limitRaw) || 300, 500);
  const r = await fetch(`${SB_URL}/rest/v1/access_logs?select=*&order=created_at.desc&limit=${limit}`, { headers: SH });
  const logs = r.ok ? await r.json() : [];
  const ipSet = new Set<string>();
  const actionCount: Record<string, number> = {};
  for (const l of logs) {
    if (l.ip) ipSet.add(l.ip);
    actionCount[l.action] = (actionCount[l.action] || 0) + 1;
  }
  const visits = await visitorStats();
  return { logs, stats: { total: logs.length, uniqueIPs: ipSet.size, actions: actionCount }, visits };
}

// PostgREST count(content-range 헤더)로 visit 수 집계
async function countSince(_field: string, sinceUTC?: string): Promise<number> {
  let url = `${SB_URL}/rest/v1/access_logs?select=id&action=eq.visit`;
  if (sinceUTC) url += `&created_at=gte.${encodeURIComponent(sinceUTC)}`;
  const r = await fetch(url, { headers: { ...SH, Prefer: "count=exact", Range: "0-0" } });
  const cr = r.headers.get("content-range") || "";
  const tot = cr.split("/")[1];
  return !tot || tot === "*" ? 0 : Number(tot);
}
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
  const KST = 9 * 3600 * 1000;
  const k = new Date(Date.now() + KST);
  const y = k.getUTCFullYear(), m = k.getUTCMonth(), d = k.getUTCDate();
  const dayUTC = new Date(Date.UTC(y, m, d) - KST).toISOString();
  const monthUTC = new Date(Date.UTC(y, m, 1) - KST).toISOString();
  const yearUTC = new Date(Date.UTC(y, 0, 1) - KST).toISOString();
  const [day, month, year, total, uDay, uMonth, uYear, uTotal, daily] = await Promise.all([
    countSince("visit", dayUTC), countSince("visit", monthUTC), countSince("visit", yearUTC), countSince("visit"),
    uniqueSince(dayUTC), uniqueSince(monthUTC), uniqueSince(yearUTC), uniqueSince(), dailySeries(14),
  ]);
  return { day, month, year, total, unique: { day: uDay, month: uMonth, year: uYear, total: uTotal }, daily };
}
async function dailySeries(days: number) {
  const KST = 9 * 3600 * 1000;
  const k = new Date(Date.now() + KST);
  const startKST = Date.UTC(k.getUTCFullYear(), k.getUTCMonth(), k.getUTCDate()) - (days - 1) * 86400000;
  const startUTC = new Date(startKST - KST).toISOString();
  const r = await fetch(
    `${SB_URL}/rest/v1/access_logs?select=created_at&action=eq.visit&created_at=gte.${encodeURIComponent(startUTC)}&limit=100000`,
    { headers: SH });
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

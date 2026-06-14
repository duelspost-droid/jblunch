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

// ── 추천 방식 프리셋 메타 (배치 generate_lunch.py / analyze 와 동일) ──
// UI는 profiles_meta 액션으로 이 목록을 받아 단일 출처로 사용한다.
const REC_PROFILES = [
  { key: "walk_tight", label: "도보 최우선", radii: [300, 500], minCand: 6, walkMax: 99, extraMax: 8, allowFar: "no",
    desc: "걸어서 3~6분 거리(약 300~500m)만. 가게가 매우 가까운 곳만 추천하고 거리 미달 시에도 범위를 넓히지 않아요. 초밀집 도심 사무실에 적합." },
  { key: "walk", label: "도보 위주", radii: [700, 1000], minCand: 8, walkMax: 99, extraMax: 12, allowFar: "no",
    desc: "걸어서 갈 수 있는 약 700m~1km. 모든 거리를 도보로 표기하고 먼 유명 맛집은 넣지 않아요. 일반 도심 위치에 적합." },
  { key: "auto", label: "근거리 자동 (권장)", radii: [600, 1500, 3000, 5000], minCand: 12, walkMax: 14, extraMax: 20, allowFar: "sparse",
    desc: "기본 600m에서 시작해 가까운 가게가 12곳 미만이면 1.5km→3km→5km로 자동 확대해요. 도보 14분을 넘으면 '차로 N분·Nkm'로 표기. 근처 가게가 충분하면 가까운 순으로 고정하고, 부족할 때만 조금 먼 유명 맛집을 허용합니다. 대부분의 위치에 가장 무난해요." },
  { key: "town", label: "동네 (도보+동네)", radii: [1500, 3000], minCand: 10, walkMax: 12, extraMax: 20, allowFar: "sparse",
    desc: "동네 범위(약 1.5~3km)를 기본으로. 도보·차량을 자연스럽게 섞어 추천. 가게가 적당히 흩어진 주택가·소도시 중심에 적합." },
  { key: "drive_near", label: "차량 근교", radii: [2000, 5000], minCand: 8, walkMax: 6, extraMax: 30, allowFar: "sparse",
    desc: "차로 이동 전제(약 2~5km). 도보 6분 넘으면 차로 거리로 표기. 차로 금방 닿는 근교 맛집까지 포함해요." },
  { key: "drive", label: "차량 권역", radii: [3000, 6000, 9000], minCand: 8, walkMax: 5, extraMax: 40, allowFar: "sparse",
    desc: "차로 다니는 넓은 권역(약 3~9km). 거의 모든 거리를 차로 N분·km로 표기. 가게가 드문 외곽·시골 캠퍼스에 적합." },
  { key: "wide", label: "광역 (시·도)", radii: [5000, 10000, 15000], minCand: 6, walkMax: 4, extraMax: 60, allowFar: "always",
    desc: "시·도 단위의 광역(약 5~15km). 가게가 매우 드문 지역에서 멀어도 알려진 맛집까지 폭넓게 추천해요." },
  { key: "city", label: "도심 밀집", radii: [500, 1000, 1500], minCand: 15, walkMax: 12, extraMax: 12, allowFar: "sparse",
    desc: "가게가 빽빽한 도심·번화가용. 좁은 범위에서 더 많이(15곳) 모아 촘촘하게 추천해요." },
  { key: "custom", label: "맞춤 (고급)", radii: [600, 1500, 3000], minCand: 12, walkMax: 14, extraMax: 20, allowFar: "sparse",
    desc: "탐색 반경·도보 표기 기준·추가추천 거리·먼곳 허용 정책을 위치별로 직접 숫자로 조절해요. (기본값은 '근거리 자동'과 동일)" },
];
const REC_KEYS = REC_PROFILES.map((p) => p.key);
// 커스텀 파라미터 정제(허용 범위로 클램프) — 잘못된 입력 방어
function sanitizeCustom(c: unknown): Record<string, unknown> {
  const o = (c && typeof c === "object") ? c as Record<string, unknown> : {};
  const clampNum = (v: unknown, lo: number, hi: number, def: number) => {
    const n = Number(v); return isFinite(n) && n > 0 ? Math.min(hi, Math.max(lo, Math.round(n))) : def;
  };
  let radii: number[] = Array.isArray(o.radii)
    ? (o.radii as unknown[]).map((x) => Number(x)).filter((n) => isFinite(n) && n >= 100 && n <= 50000)
    : [];
  if (!radii.length) radii = [600, 1500, 3000];
  radii = [...new Set(radii)].sort((a, b) => a - b).slice(0, 5);
  const allow = ["sparse", "no", "always"].includes(String(o.allowFar)) ? String(o.allowFar) : "sparse";
  return {
    radii,
    minCand: clampNum(o.minCand, 1, 40, 12),
    walkMax: clampNum(o.walkMax, 1, 120, 14),
    extraMax: clampNum(o.extraMax, 1, 120, 20),
    allowFar: allow,
  };
}

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
      const data = await dashboard(body);
      return json({ ok: true, token: sess.token, expiresAt: sess.expires_at, ...data });
    }

    // ── 비밀번호 변경: '현재 비번'으로 직접 인증 (토큰 만료/무효화와 무관) ──
    if (action === "change_password") {
      if (await isLocked(ip)) return json({ error: `시도가 많아 잠시 잠겼습니다. ${LOCK_WINDOW_MIN}분 후 다시 시도하세요.` }, 429);
      const cur = (body.currentPassword || body.password || "").toString();
      if (!await verifyPassword(cur)) {
        await logAdmin("admin_fail", ip, "pw_change", ua);
        return json({ error: "현재 비밀번호가 올바르지 않습니다." }, 401);
      }
      const np = (body.newPassword || "").toString();
      if (np.length < 6) return json({ error: "새 비밀번호는 6자 이상이어야 합니다." }, 400);
      await setPassword(np);
      await deleteAllSessions();            // 비번 변경 시 모든 세션 무효화
      await logAdmin("admin_pw_change", ip, "", ua);
      const sess = await createSession(ip);  // 현재 기기는 새 토큰 발급
      return json({ ok: true, changed: true, token: sess.token, expiresAt: sess.expires_at });
    }

    // ── 기능 제안 제출 (인증 불필요, 누구든지) ──────────────────
    if (action === "suggestion_add") {
      const content = (body.content || "").toString().trim();
      const contact = (body.contact || "").toString().trim().slice(0, 100);
      if (!content || content.length < 5) return json({ error: "제안 내용을 5자 이상 입력해주세요." }, 400);
      if (content.length > 1000) return json({ error: "1000자 이내로 입력해주세요." }, 400);
      const ins = await fetch(`${SB_URL}/rest/v1/suggestions`, {
        method: "POST", headers: { ...SH, Prefer: "return=representation" },
        body: JSON.stringify({ content, contact: contact || null, status: "pending", ip }),
      });
      if (!ins.ok) return json({ error: "저장 실패" }, 500);
      return json({ ok: true });
    }

    // ── 공개 Q&A 조회 (인증 불필요): 답변완료된 것만, 민감정보 제외 ──
    if (action === "suggestion_public") {
      const r = await fetch(
        `${SB_URL}/rest/v1/suggestions?select=id,content,admin_reply,created_at,replied_at&status=eq.answered&order=replied_at.desc&limit=50`,
        { headers: SH },
      );
      return json({ ok: true, qna: r.ok ? await r.json() : [] });
    }

    // ── 그 외(로그/로그아웃): 토큰 인증 (레거시 비번도 허용, 단 잠금 적용) ──
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

    // ── 기능 제안 관리 (관리자) ─────────────────────────────────
    if (action === "suggestion_list") {
      const r = await fetch(`${SB_URL}/rest/v1/suggestions?select=*&order=created_at.desc`, { headers: SH });
      return json({ ok: true, suggestions: r.ok ? await r.json() : [] });
    }
    if (action === "suggestion_reply") {
      const id = (body.id || "").toString();
      const reply = (body.reply || "").toString().trim();
      if (!id) return json({ error: "id 필요" }, 400);
      await fetch(`${SB_URL}/rest/v1/suggestions?id=eq.${encodeURIComponent(id)}`, {
        method: "PATCH", headers: SH,
        body: JSON.stringify({ admin_reply: reply, status: reply ? "answered" : "pending", replied_at: reply ? new Date().toISOString() : null }),
      });
      await logAdmin("suggestion_reply", ip, id, ua);
      return json({ ok: true });
    }
    if (action === "suggestion_update") {
      const id = (body.id || "").toString();
      if (!id) return json({ error: "id 필요" }, 400);
      const patch: Record<string, unknown> = {};
      if (body.content != null) { const v = body.content.toString().trim(); if (v) patch.content = v; }
      if (body.reply != null) {
        const reply = body.reply.toString().trim();
        patch.admin_reply = reply || null;
        patch.status = reply ? "answered" : "pending";
        patch.replied_at = reply ? new Date().toISOString() : null;
      }
      if (!Object.keys(patch).length) return json({ error: "변경할 내용이 없어요." }, 400);
      await fetch(`${SB_URL}/rest/v1/suggestions?id=eq.${encodeURIComponent(id)}`, {
        method: "PATCH", headers: SH, body: JSON.stringify(patch),
      });
      await logAdmin("suggestion_update", ip, id, ua);
      return json({ ok: true });
    }
    if (action === "suggestion_delete") {
      const id = (body.id || "").toString();
      if (!id) return json({ error: "id 필요" }, 400);
      await fetch(`${SB_URL}/rest/v1/suggestions?id=eq.${encodeURIComponent(id)}`, { method: "DELETE", headers: SH });
      await logAdmin("suggestion_delete", ip, id, ua);
      return json({ ok: true });
    }

    // ── 좌표 검색 (장소명·주소 → 후보 목록) ─────────────────────
    if (action === "geo_search") {
      const q = (body.query || "").toString().trim();
      if (!q) return json({ error: "검색어를 입력하세요." }, 400);
      const results = await geoSearch(q);
      return json({ ok: true, results });
    }

    // ── 추천 방식: 프리셋 메타 제공 (UI 단일 출처) ──────────────
    if (action === "profiles_meta") {
      return json({ ok: true, profiles: REC_PROFILES });
    }
    // ── 추천 방식 전체 일괄 적용 ────────────────────────────────
    if (action === "loc_set_all_profile") {
      const p = (body.rec_profile || "").toString();
      if (!REC_KEYS.includes(p)) return json({ error: "알 수 없는 추천 방식이에요." }, 400);
      const rec_custom = p === "custom" ? sanitizeCustom(body.rec_custom) : null;
      const r = await fetch(`${SB_URL}/rest/v1/app_locations?key=neq.__none__`, {
        method: "PATCH", headers: SH, body: JSON.stringify({ rec_profile: p, rec_custom }),
      });
      if (!r.ok) return json({ error: "일괄 적용 실패: " + (await r.text()) }, 500);
      await logAdmin("loc_set_all_profile", ip, p, ua);
      return json({ ok: true });
    }

    // ── 기준 위치 관리 ──────────────────────────────────────────
    if (action === "loc_list") {
      const r = await fetch(`${SB_URL}/rest/v1/app_locations?select=*&order=sort.asc`, { headers: SH });
      return json({ ok: true, locations: r.ok ? await r.json() : [] });
    }
    if (action === "loc_add") {
      const name = (body.name || "").toString().trim();
      const short = (body.short || "").toString().trim() || name;
      const region = (body.region || "").toString().trim();
      const address = (body.address || "").toString().trim();
      let lat = Number(body.lat), lng = Number(body.lng);
      if (!name || !region) return json({ error: "이름과 지역은 필수예요." }, 400);
      // 좌표 미지정 → 주소(없으면 이름+지역)로 지오코딩
      if (!(isFinite(lat) && isFinite(lng) && lat && lng)) {
        const g = await geocode(address || `${name} ${region}`);
        if (!g) return json({ error: "주소로 좌표를 찾지 못했어요. 주소를 더 정확히 입력하거나 위경도를 직접 넣어주세요." }, 422);
        lat = g.lat; lng = g.lng;
      }
      const key = "loc_" + randHex(5);
      const subtitle = (body.subtitle || "").toString().trim() || `${name} 근처 · 맛집 추천`;
      const rec_profile = REC_KEYS.includes((body.rec_profile || "").toString()) ? body.rec_profile : "auto";
      const rec_custom = rec_profile === "custom" ? sanitizeCustom(body.rec_custom) : null;
      const row = { key, name, short, region, lat, lng, subtitle, auto: false, sort: 100, rec_profile, rec_custom };
      const ins = await fetch(`${SB_URL}/rest/v1/app_locations`, {
        method: "POST", headers: { ...SH, Prefer: "return=representation" }, body: JSON.stringify(row),
      });
      if (!ins.ok) return json({ error: "저장 실패: " + (await ins.text()) }, 500);
      await logAdmin("loc_add", ip, name, ua);
      return json({ ok: true, location: (await ins.json())[0] });
    }
    if (action === "loc_update") {
      const key = (body.key || "").toString();
      if (!key) return json({ error: "key 필요" }, 400);
      const patch: Record<string, unknown> = {};
      if (body.name != null) { const v = body.name.toString().trim(); if (v) patch.name = v; }
      if (body.short != null) { const v = body.short.toString().trim(); if (v) patch.short = v; }
      if (body.region != null) { const v = body.region.toString().trim(); if (v) patch.region = v; }
      if (body.subtitle != null) patch.subtitle = body.subtitle.toString().trim();
      if (body.rec_profile != null && REC_KEYS.includes(body.rec_profile.toString())) {
        patch.rec_profile = body.rec_profile.toString();
        patch.rec_custom = patch.rec_profile === "custom" ? sanitizeCustom(body.rec_custom) : null;
      }
      const lat = Number(body.lat), lng = Number(body.lng);
      if (isFinite(lat) && isFinite(lng) && lat && lng) { patch.lat = lat; patch.lng = lng; }
      if (!Object.keys(patch).length) return json({ error: "변경할 내용이 없어요." }, 400);
      const r = await fetch(`${SB_URL}/rest/v1/app_locations?key=eq.${encodeURIComponent(key)}`, {
        method: "PATCH", headers: { ...SH, Prefer: "return=representation" }, body: JSON.stringify(patch),
      });
      if (!r.ok) return json({ error: "수정 실패: " + (await r.text()) }, 500);
      await logAdmin("loc_update", ip, key, ua);
      return json({ ok: true, location: (await r.json())[0] });
    }
    if (action === "loc_toggle") {
      const key = (body.key || "").toString();
      if (!key) return json({ error: "key 필요" }, 400);
      if (key === "jb") return json({ error: "기본 위치(JB빌딩)는 중지할 수 없어요." }, 400);
      const enabled = body.enabled === true || body.enabled === "true";
      await fetch(`${SB_URL}/rest/v1/app_locations?key=eq.${encodeURIComponent(key)}`, {
        method: "PATCH", headers: SH, body: JSON.stringify({ enabled }),
      });
      await logAdmin("loc_toggle", ip, `${key}=${enabled}`, ua);
      return json({ ok: true });
    }
    if (action === "loc_delete") {
      const key = (body.key || "").toString();
      if (!key) return json({ error: "key 필요" }, 400);
      if (key === "jb") return json({ error: "기본 위치(JB빌딩)는 삭제할 수 없어요." }, 400);
      await fetch(`${SB_URL}/rest/v1/app_locations?key=eq.${encodeURIComponent(key)}`, { method: "DELETE", headers: SH });
      await logAdmin("loc_delete", ip, key, ua);
      return json({ ok: true });
    }

    // ── 로그/통계 조회 (기본, 기간·액션 필터) ──
    return json({ ok: true, ...(await dashboard(body)) });
  } catch (e) {
    return json({ error: String(e) }, 500);
  }
});

// 기간(일/월/기간/전체) → UTC 범위 (KST 기준)
function kstRange(period: string, from?: string, to?: string): { fromUTC?: string; toUTC?: string } {
  const KST = 9 * 3600 * 1000;
  const now = new Date(Date.now() + KST);
  const y = now.getUTCFullYear(), m = now.getUTCMonth(), d = now.getUTCDate();
  if (period === "day") return { fromUTC: new Date(Date.UTC(y, m, d) - KST).toISOString() };
  if (period === "month") return { fromUTC: new Date(Date.UTC(y, m, 1) - KST).toISOString() };
  if (period === "range") {
    const out: { fromUTC?: string; toUTC?: string } = {};
    if (from) { const [a, b, c] = from.split("-").map(Number); out.fromUTC = new Date(Date.UTC(a, b - 1, c) - KST).toISOString(); }
    if (to) { const [a, b, c] = to.split("-").map(Number); out.toUTC = new Date(Date.UTC(a, b - 1, c + 1) - KST).toISOString(); }
    return out;
  }
  return {}; // all
}

// ── IP → 접속 지역 조회 (ip_geo 캐시 + ip-api 배치) ──────────────
type Geo = { ip: string; country?: string; country_code?: string; region?: string; city?: string; isp?: string; mobile?: boolean; proxy?: boolean };
function isPublicIP(ip: string): boolean {
  if (!ip || ip === "unknown") return false;
  if (ip === "127.0.0.1" || ip === "::1") return false;
  if (/^10\./.test(ip) || /^192\.168\./.test(ip) || /^172\.(1[6-9]|2\d|3[01])\./.test(ip)) return false;
  if (/^(fc|fd)/i.test(ip)) return false; // ULA IPv6
  return true;
}
async function geoLookup(ips: string[]): Promise<Record<string, Geo>> {
  const map: Record<string, Geo> = {};
  const targets = [...new Set(ips.filter(isPublicIP))];
  if (!targets.length) return map;
  // 1) 캐시 조회 (30일 이내)
  const fresh = new Date(Date.now() - 30 * 864e5).toISOString();
  try {
    const inList = targets.map((x) => `"${x}"`).join(",");
    const cr = await fetch(`${SB_URL}/rest/v1/ip_geo?select=*&ip=in.(${encodeURIComponent(inList)})&updated_at=gte.${encodeURIComponent(fresh)}`, { headers: SH });
    if (cr.ok) for (const row of await cr.json()) map[row.ip] = row;
  } catch (_e) { /* 캐시 실패 무시 */ }
  // 2) 미캐시 IP → ip-api 배치(최대 100개/요청)
  const miss = targets.filter((ip) => !map[ip]);
  for (let i = 0; i < miss.length; i += 100) {
    const chunk = miss.slice(i, i + 100);
    try {
      const resp = await fetch("http://ip-api.com/batch?fields=status,country,countryCode,regionName,city,isp,mobile,proxy,query", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(chunk),
        signal: AbortSignal.timeout(4000),   // 느린 조회로 필터 응답이 지연되지 않게
      });
      if (!resp.ok) continue;
      const arr = await resp.json();
      const rows: Geo[] = [];
      for (const r of arr) {
        if (r.status !== "success") continue;
        const g: Geo = { ip: r.query, country: r.country, country_code: r.countryCode, region: r.regionName, city: r.city, isp: r.isp, mobile: !!r.mobile, proxy: !!r.proxy };
        map[r.query] = g; rows.push(g);
      }
      if (rows.length) {
        await fetch(`${SB_URL}/rest/v1/ip_geo`, {
          method: "POST", headers: { ...SH, Prefer: "resolution=merge-duplicates" },
          body: JSON.stringify(rows.map((g) => ({ ...g, updated_at: new Date().toISOString() }))),
        }).catch(() => {});
      }
    } catch (_e) { /* 배치 실패 무시 */ }
  }
  return map;
}

// ── Kakao GET: 여러 키를 순차 시도 (env 키가 무효면 fallback) ────
// env KAKAO_REST_API_KEY가 잘못 설정돼 있어도 동작하도록 알려진 키로 폴백.
const KAKAO_KEYS = [
  Deno.env.get("KAKAO_REST_API_KEY") || "",
  "af04c6cff1c0c408283c25e84d5b481d",
].filter((k, i, a) => k && a.indexOf(k) === i);
async function kakaoGet(url: string): Promise<any | null> {
  for (const key of KAKAO_KEYS) {
    try {
      const r = await fetch(url, { headers: { Authorization: `KakaoAK ${key}` } });
      if (r.ok) {
        const j = await r.json();
        if (Array.isArray(j.documents)) return j;   // 인증 OK
      }
      // 401/403 등 → 다음 키 시도
    } catch (_e) { /* 다음 키 시도 */ }
  }
  return null;
}

// ── Kakao 장소·주소 검색 → 후보 목록 (위치 추가 UI용) ───────────
type GeoHit = { name: string; address: string; lat: number; lng: number };
async function geoSearch(query: string): Promise<GeoHit[]> {
  if (!query) return [];
  const out: GeoHit[] = [];
  const seen = new Set<string>();
  const push = (name: string, address: string, x: string, y: string) => {
    const lat = Number(y), lng = Number(x);
    if (!isFinite(lat) || !isFinite(lng) || !lat || !lng) return;
    const k = `${lat.toFixed(5)},${lng.toFixed(5)}`;
    if (seen.has(k)) return;
    seen.add(k);
    out.push({ name: name || address, address: address || "", lat, lng });
  };
  // 1) 키워드(장소명) 검색 — '광주은행 본점' 같은 상호명도 잡힘
  const kw = await kakaoGet(`https://dapi.kakao.com/v2/local/search/keyword.json?query=${encodeURIComponent(query)}&size=10`);
  for (const d of kw?.documents || []) {
    push(d.place_name, d.road_address_name || d.address_name || "", d.x, d.y);
  }
  // 2) 주소 검색 보강 (도로명/지번 주소 직접 입력 대비)
  const ad = await kakaoGet(`https://dapi.kakao.com/v2/local/search/address.json?query=${encodeURIComponent(query)}&size=10`);
  for (const d of ad?.documents || []) {
    const addr = d.road_address?.address_name || d.address_name || "";
    push(addr, addr, d.x, d.y);
  }
  return out.slice(0, 8);
}

// ── Kakao 주소/장소 지오코딩 (위치 추가용) ──────────────────────
async function geocode(query: string): Promise<{ lat: number; lng: number } | null> {
  if (!query) return null;
  const a = await kakaoGet(`https://dapi.kakao.com/v2/local/search/address.json?query=${encodeURIComponent(query)}`);
  if (a?.documents?.[0]) { const d = a.documents[0]; return { lat: Number(d.y), lng: Number(d.x) }; }
  const k = await kakaoGet(`https://dapi.kakao.com/v2/local/search/keyword.json?query=${encodeURIComponent(query)}&size=1`);
  if (k?.documents?.[0]) { const d = k.documents[0]; return { lat: Number(d.y), lng: Number(d.x) }; }
  return null;
}

// ── 대시보드 데이터 (기간·액션 필터) ──────────────────────────
async function dashboard(opts: Record<string, unknown> = {}) {
  const limit = Math.min(Number(opts.limit) || 300, 1000);
  // 필터(기간·액션·검색)는 클라이언트에서 처리 → 서버는 최근 로그만 반환
  const url = `${SB_URL}/rest/v1/access_logs?select=*&order=created_at.desc&limit=${limit}`;

  const r = await fetch(url, { headers: { ...SH, Prefer: "count=exact" } });
  let logs: any[] = r.ok ? await r.json().catch(() => []) : [];
  if (!Array.isArray(logs)) logs = [];
  const cr = r.headers.get("content-range") || "";   // "0-N/total"
  const totalMatch = Number((cr.split("/")[1] || "")) || logs.length;

  const ipSet = new Set<string>();
  const actionCount: Record<string, number> = {};
  for (const l of logs) {
    if (l.ip) ipSet.add(l.ip);
    actionCount[l.action] = (actionCount[l.action] || 0) + 1;
  }
  // IP → 지역 조회 후 각 로그에 geo 부착 + 지역별 집계
  const geoMap = await geoLookup([...ipSet]);
  const regionCount: Record<string, number> = {};
  for (const l of logs) {
    const g = l.ip ? geoMap[l.ip] : undefined;
    if (g) {
      l.geo = g;
      const label = [g.country, g.region].filter(Boolean).join(" ") || g.country || "기타";
      regionCount[label] = (regionCount[label] || 0) + 1;
    }
  }
  const regions = Object.entries(regionCount).sort((a, b) => b[1] - a[1]).map(([k, v]) => ({ region: k, count: v }));
  const visits = await visitorStats();
  return { logs, logCount: totalMatch, stats: { total: logs.length, matched: totalMatch, uniqueIPs: ipSet.size, actions: actionCount, regions }, visits };
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

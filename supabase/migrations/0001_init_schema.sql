-- jblunch — 전체 DB 스키마 (codified)
-- 목적: Supabase 프로젝트의 모든 테이블을 코드로 관리하여, 테이블 유실/리셋 시
--       이 파일 1회 실행으로 복구할 수 있게 한다.
--
-- 안전성: 전부 멱등(idempotent) + 비파괴(non-destructive).
--   - create table IF NOT EXISTS  → 이미 있으면 건드리지 않음(데이터 보존)
--   - 정책(policy)은 pg_policies 확인 후 없을 때만 생성 → 기존 정책 미변경
--   - seed 는 ON CONFLICT DO NOTHING → 기존 행 보존
-- 따라서 라이브 DB에 그대로 실행해도 안전하며, 사라진 테이블만 복구된다.
--
-- 접근 모델:
--   * 서비스롤 전용(admin/track/analyze/generate Edge Function·배치): RLS on, anon 정책 없음
--   * 공개 읽기(anon): app_locations, place_meta
--   * 공개 읽기·쓰기(anon): comments(리뷰), search_history(검색이력)
--   service_role 은 RLS 를 우회하므로 별도 정책 불필요.

-- ──────────────────────────────────────────────────────────────
-- 1) 관리자 인증 / 세션 / 감사로그 (서비스롤 전용)
-- ──────────────────────────────────────────────────────────────

create table if not exists public.admin_config (
  id            int primary key default 1,
  password_hash text,
  salt          text,
  iterations    int default 120000
);
-- 주의: admin_config 가 비어 있으면 로그인 불가. 비밀번호는 관리자 페이지의
--       '비밀번호 변경' 흐름(setPassword)으로 설정해야 한다(여기서 시드하지 않음).

create table if not exists public.admin_sessions (
  token       text primary key,
  ip          text,
  expires_at  timestamptz not null,
  created_at  timestamptz not null default now()
);
create index if not exists admin_sessions_expires_idx on public.admin_sessions (expires_at);

create table if not exists public.access_logs (
  id          bigint generated always as identity primary key,
  created_at  timestamptz not null default now(),
  ip          text,
  action      text not null,
  detail      text,
  user_agent  text,
  path        text
);
-- 방문자 통계(action='visit') / 최근순 로그 조회 가속
create index if not exists access_logs_action_created_idx on public.access_logs (action, created_at desc);
create index if not exists access_logs_created_idx        on public.access_logs (created_at desc);

create table if not exists public.suggestions (
  id          bigint generated always as identity primary key,
  content     text not null,
  contact     text,
  status      text not null default 'pending',   -- pending | answered
  admin_reply text,
  ip          text,
  created_at  timestamptz not null default now(),
  replied_at  timestamptz
);
create index if not exists suggestions_status_idx on public.suggestions (status, created_at desc);

create table if not exists public.ip_geo (
  ip            text primary key,
  country       text,
  country_code  text,
  region        text,
  city          text,
  isp           text,
  mobile        boolean,
  proxy         boolean,
  updated_at    timestamptz not null default now()
);

-- ──────────────────────────────────────────────────────────────
-- 2) 앱 설정 / 위치 (app_settings: 서비스롤 전용, app_locations: anon 읽기)
-- ──────────────────────────────────────────────────────────────

create table if not exists public.app_settings (
  id          int primary key default 1,
  diversity   jsonb,
  updated_at  timestamptz not null default now()
);
-- 전역 추천 다양성 기본값(diversity_get 의 def 와 동일). 없을 때만 시드.
insert into public.app_settings (id, diversity)
values (1, '{"avoid":true,"spread":true,"pool":true,"rotate":true,"recent_days":7,"dedup_branch":true,"cuisine_vary":true,"cand_max":45,"radius_boost":1.0}'::jsonb)
on conflict (id) do nothing;

create table if not exists public.app_locations (
  key         text primary key,
  name        text not null,
  short       text,
  region      text,
  lat         double precision,
  lng         double precision,
  subtitle    text,
  auto        boolean default false,
  sort        int default 100,
  rec_profile text default 'auto',
  rec_custom  jsonb,
  diversity   jsonb,                              -- 위치별 다양성 override(null=전역 따름)
  created_at  timestamptz not null default now()
);
-- 기준 위치 3종 시드(index.html 의 하드코딩 기본값과 동일). 기존 위치 보존.
insert into public.app_locations (key, name, short, region, lat, lng, subtitle, auto, sort) values
  ('jb',      'JB빌딩 (여의도)', 'JB빌딩',   '여의도',     37.5240914884765, 126.927376521939, '여의도 JB빌딩 근처 · 점심·저녁·술집 추천', true,  0),
  ('gwangju', '광주은행 본점',   '광주은행', '광주 동구',  35.1548269706022, 126.912881392668, '광주은행 본점 근처 · 실시간 맛집 추천',   false, 10),
  ('jeonbuk', '전북은행 본점',   '전북은행', '전주 덕진구', 35.8397433521131, 127.131381897342, '전북은행 본점 근처 · 실시간 맛집 추천',   false, 20)
on conflict (key) do nothing;

-- ──────────────────────────────────────────────────────────────
-- 3) 공개 데이터 (anon 접근)
-- ──────────────────────────────────────────────────────────────

-- 맛집 메타 캐시(소개·가격·거리·메뉴) — analyze/generate 가 서비스롤로 upsert, 공개 사이트가 anon 으로 읽음
create table if not exists public.place_meta (
  name        text primary key,
  intro       text,
  price       text,
  distance    text,
  verified    boolean,
  menu        text,
  region      text,
  updated_at  timestamptz not null default now()
);

-- 리뷰(끼니·위치별) — 공개 사이트가 anon 으로 읽기/쓰기/수정/삭제
create table if not exists public.comments (
  id            bigint generated always as identity primary key,
  restaurant_id text,
  reviewer_name text,
  content       text,
  rating        int,
  meal          text,        -- 점심 | 저녁 | 술집
  image_url     text,
  location      text,
  created_at    timestamptz not null default now()
);
create index if not exists comments_location_created_idx on public.comments (location, created_at);

-- 직접입력 검색 이력 — 공개 사이트가 anon 으로 읽기/쓰기/삭제
create table if not exists public.search_history (
  id          bigint generated always as identity primary key,
  text        text,
  meal        text,
  created_at  timestamptz not null default now()
);
create index if not exists search_history_meal_created_idx on public.search_history (meal, created_at desc);

-- 일자별 선호(배치 추천이 참조) — 서비스롤 전용. (writer 미확인: 타입은 best-effort)
create table if not exists public.daily_preference (
  date        date primary key,
  preference  text,
  created_at  timestamptz not null default now()
);

-- ──────────────────────────────────────────────────────────────
-- 3-1) 기존 테이블 컬럼 보강 (멱등 ALTER)
--   create table if not exists 는 "테이블"만 만들고 기존 테이블에 "컬럼"은 추가하지 않는다.
--   따라서 라이브 DB 업그레이드 시 누락 컬럼을 여기서 채운다. add column if not exists 는 안전·멱등.
--   ⚠️ app_locations.diversity 는 이번 배포의 신규 의존(generate_lunch 가 select) — 없으면
--      배치가 위치 조회 실패로 JB 폴백되어 광주·전북·커스텀 위치가 조용히 누락된다.
-- ──────────────────────────────────────────────────────────────
alter table public.app_locations add column if not exists diversity jsonb;
alter table public.app_settings  add column if not exists diversity jsonb;
alter table public.place_meta    add column if not exists menu      text;
alter table public.place_meta    add column if not exists region    text;
alter table public.comments      add column if not exists meal      text;
alter table public.comments      add column if not exists image_url text;
alter table public.comments      add column if not exists location  text;

-- ──────────────────────────────────────────────────────────────
-- 4) RLS 활성화 (전 테이블). service_role 은 RLS 우회.
-- ──────────────────────────────────────────────────────────────
alter table public.admin_config     enable row level security;
alter table public.admin_sessions   enable row level security;
alter table public.access_logs      enable row level security;
alter table public.suggestions      enable row level security;
alter table public.ip_geo           enable row level security;
alter table public.app_settings     enable row level security;
alter table public.app_locations    enable row level security;
alter table public.place_meta       enable row level security;
alter table public.comments         enable row level security;
alter table public.search_history   enable row level security;
alter table public.daily_preference enable row level security;

-- ──────────────────────────────────────────────────────────────
-- 5) anon 권한(GRANT) + 정책(POLICY) — 없을 때만 생성(기존 미변경)
-- ──────────────────────────────────────────────────────────────
grant select                         on public.app_locations  to anon, authenticated;
grant select                         on public.place_meta     to anon, authenticated;
grant select, insert, update, delete on public.comments       to anon, authenticated;
grant select, insert, delete         on public.search_history to anon, authenticated;

do $$
begin
  -- app_locations: 공개 읽기
  if not exists (select 1 from pg_policies where schemaname='public' and tablename='app_locations' and policyname='app_locations anon read') then
    create policy "app_locations anon read" on public.app_locations for select to anon, authenticated using (true);
  end if;

  -- place_meta: 공개 읽기
  if not exists (select 1 from pg_policies where schemaname='public' and tablename='place_meta' and policyname='place_meta anon read') then
    create policy "place_meta anon read" on public.place_meta for select to anon, authenticated using (true);
  end if;

  -- comments: 공개 읽기/쓰기/수정/삭제 (기존 공개 리뷰 동작 유지)
  if not exists (select 1 from pg_policies where schemaname='public' and tablename='comments' and policyname='comments anon read') then
    create policy "comments anon read"   on public.comments for select to anon, authenticated using (true);
  end if;
  if not exists (select 1 from pg_policies where schemaname='public' and tablename='comments' and policyname='comments anon insert') then
    create policy "comments anon insert" on public.comments for insert to anon, authenticated with check (true);
  end if;
  if not exists (select 1 from pg_policies where schemaname='public' and tablename='comments' and policyname='comments anon update') then
    create policy "comments anon update" on public.comments for update to anon, authenticated using (true) with check (true);
  end if;
  if not exists (select 1 from pg_policies where schemaname='public' and tablename='comments' and policyname='comments anon delete') then
    create policy "comments anon delete" on public.comments for delete to anon, authenticated using (true);
  end if;

  -- search_history: 공개 읽기/쓰기/삭제
  if not exists (select 1 from pg_policies where schemaname='public' and tablename='search_history' and policyname='search_history anon read') then
    create policy "search_history anon read"   on public.search_history for select to anon, authenticated using (true);
  end if;
  if not exists (select 1 from pg_policies where schemaname='public' and tablename='search_history' and policyname='search_history anon insert') then
    create policy "search_history anon insert" on public.search_history for insert to anon, authenticated with check (true);
  end if;
  if not exists (select 1 from pg_policies where schemaname='public' and tablename='search_history' and policyname='search_history anon delete') then
    create policy "search_history anon delete" on public.search_history for delete to anon, authenticated using (true);
  end if;
end $$;

-- 끝. 서비스롤 전용 테이블(admin_*, access_logs, suggestions, ip_geo, app_settings,
-- daily_preference)은 anon 정책을 두지 않는다 — Edge Function 이 service_role 키로 접근(RLS 우회).

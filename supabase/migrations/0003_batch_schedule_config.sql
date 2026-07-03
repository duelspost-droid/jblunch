-- 0003: 관리자 지정 배치 실행 시각(KST) + app_settings 공개 읽기
--
-- 관리자 페이지에서 일일 배치 실행 시각을 지정할 수 있도록 app_settings에
-- batch_hour / batch_minute(KST) 컬럼을 추가한다. 배치 스크립트(generate_lunch.py)는
-- anon 키로 이 값을 읽어, 워크플로가 아침 창에서 15분마다 돌더라도 '지정 시각 이후
-- 첫 실행 1회'만 실제 동작하도록 게이팅한다.
--
-- 또한 app_settings 에 anon SELECT 정책을 추가한다 — 배치는 service_role 이 아닌
-- anon 키로 설정을 읽기 때문. (기존 diversity 설정이 배치에 반영되지 않던 잠재 버그도
-- 함께 해소된다.) app_settings 는 민감정보가 아니라 추천 튜닝·스케줄 설정만 담으므로
-- 공개 읽기를 허용해도 무방하다. 쓰기는 여전히 service_role(Edge Function)만 가능.
--
-- 멱등(idempotent): 여러 번 실행해도 안전.

-- 1) 컬럼 추가
alter table public.app_settings add column if not exists batch_hour   int;
alter table public.app_settings add column if not exists batch_minute int;

-- 2) 기본값 시드 (06:00 KST) — 단일 설정 행(id=1)
insert into public.app_settings (id, batch_hour, batch_minute)
  values (1, 6, 0)
  on conflict (id) do nothing;
update public.app_settings
  set batch_hour = 6, batch_minute = 0
  where id = 1 and batch_hour is null;

-- 3) app_settings 공개 읽기 (GRANT + POLICY, 없을 때만)
grant select on public.app_settings to anon, authenticated;

do $$
begin
  if not exists (
    select 1 from pg_policies
    where schemaname = 'public' and tablename = 'app_settings'
      and policyname = 'app_settings anon read'
  ) then
    create policy "app_settings anon read"
      on public.app_settings for select to anon, authenticated using (true);
  end if;
end $$;

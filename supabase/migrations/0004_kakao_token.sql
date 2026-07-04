-- 0004: 카카오 refresh_token 영속 저장 (사실상 무기한 유지)
--
-- 카카오 refresh_token은 60일 고정이지만, 잔여 만료가 1개월 미만일 때 refresh 응답에
-- 새 refresh_token이 실려온다(그때 기존 것은 폐기). 이를 매번 되저장하면 배치가 60일 안에
-- 최소 1회만 돌아도 토큰이 계속 새 60일 창을 물려받아 사실상 만료되지 않는다.
--
-- 이 테이블은 service_role(엣지함수 kakao-token) 전용 — anon 정책을 두지 않는다.
-- 배치(kakao_send.py)는 공유 시크릿(KAKAO_TOKEN_SECRET)으로 kakao-token 함수를 통해서만 접근.
--
-- 시드: 마이그레이션 실행 후, 현재 살아있는 refresh_token을 kakao-token 함수 set 액션으로
--       주입한다(SQL에 토큰을 남기지 않기 위해 여기서 seed하지 않음).

create table if not exists public.kakao_tokens (
  id            smallint primary key default 1,
  refresh_token text not null,
  updated_at    timestamptz not null default now(),
  constraint kakao_tokens_singleton check (id = 1)
);

alter table public.kakao_tokens enable row level security;
-- anon/authenticated 정책 없음 → service_role(Edge Function)만 R/W. 토큰 공개 노출 방지.

-- 0002: 리뷰 소유권(owner_token 해시) — anon 이 남의 리뷰를 수정/삭제하던 취약점 차단
--
-- 문제: 0001 의 "comments anon update/delete ... using(true)" + 프로덕션 드리프트 정책
--       "Allow all"(roles=public, cmd=ALL, using(true)) 로, 공개 anon 키만으로
--       누구나 모든 리뷰를 PATCH/DELETE 가능(예: DELETE ?id=gte.0 전체 삭제).
-- 해결: 리뷰에 owner_token = hex(sha256(브라우저 비밀)) 저장. 수정/삭제 시 클라가 원본 비밀을
--       X-Owner-Token 헤더로 보내면 RLS 가 그것을 해시해 저장값과 대조 → 소유자만 허용.
--       저장값은 해시라 SELECT 로 노출돼도 역산/위조 불가(컬럼 숨김 불필요, select=* 정상).
--       fail-closed: 헤더 없음/불일치 → NULL 비교 → 거부(보안 구멍 아님).
--       기존 행(owner_token IS NULL)은 공개 경로로 수정/삭제 불가(잠김) — service_role(Edge Function)만.

alter table public.comments add column if not exists owner_token text;

-- 프로덕션 드리프트 제거: 모든 명령을 무제한 허용하던 정책
drop policy if exists "Allow all" on public.comments;

-- RLS 가 해시를 계산하려면 anon/authenticated 가 pgcrypto digest 를 실행할 수 있어야 함
grant execute on function extensions.digest(text, text) to anon, authenticated;

-- 열려있던(또는 이전 단계의 평문) update/delete 정책 제거 → 해시 소유자 한정으로 교체
drop policy if exists "comments anon update"  on public.comments;
drop policy if exists "comments anon delete"  on public.comments;
drop policy if exists "comments owner update" on public.comments;
drop policy if exists "comments owner delete" on public.comments;

create policy "comments owner update" on public.comments
  for update to anon, authenticated
  using      (owner_token is not null
              and owner_token = encode(extensions.digest(current_setting('request.headers', true)::json ->> 'x-owner-token', 'sha256'), 'hex'))
  with check (owner_token is not null
              and owner_token = encode(extensions.digest(current_setting('request.headers', true)::json ->> 'x-owner-token', 'sha256'), 'hex'));

create policy "comments owner delete" on public.comments
  for delete to anon, authenticated
  using      (owner_token is not null
              and owner_token = encode(extensions.digest(current_setting('request.headers', true)::json ->> 'x-owner-token', 'sha256'), 'hex'));

-- 읽기(comments anon read)·작성(comments anon insert) 정책은 0001 그대로 공개 유지.

alter table public.labels add column if not exists confidence text default 'Medium';
alter table public.labels add column if not exists evidence_sources text[] default '{}'::text[];

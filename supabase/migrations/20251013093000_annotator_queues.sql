-- annotator_queues: per-user persisted queue ordering and position
create table if not exists public.annotator_queues (
    annotator_uid uuid not null,
    dataset_hash text not null,
    queue jsonb not null,
    position integer not null default 0,
    inserted_at timestamptz not null default timezone('utc', now()),
    updated_at timestamptz not null default timezone('utc', now()),
    constraint annotator_queues_pkey primary key (annotator_uid, dataset_hash)
);

alter table public.annotator_queues enable row level security;

-- Only allow users to insert/select/update their own rows
drop policy if exists annotator_queues_insert_own on public.annotator_queues;
create policy annotator_queues_insert_own
    on public.annotator_queues
    for insert
    to authenticated
    with check (auth.uid() = annotator_uid);

drop policy if exists annotator_queues_select_own on public.annotator_queues;
create policy annotator_queues_select_own
    on public.annotator_queues
    for select
    to authenticated
    using (auth.uid() = annotator_uid);

drop policy if exists annotator_queues_update_own on public.annotator_queues;
create policy annotator_queues_update_own
    on public.annotator_queues
    for update
    to authenticated
    using (auth.uid() = annotator_uid)
    with check (auth.uid() = annotator_uid);

-- Updated_at maintenance trigger
create or replace function public.set_updated_at()
returns trigger as $$
begin
  new.updated_at = timezone('utc', now());
  return new;
end;
$$ language plpgsql;

drop trigger if exists set_annotator_queues_updated_at on public.annotator_queues;
create trigger set_annotator_queues_updated_at
before update on public.annotator_queues
for each row execute procedure public.set_updated_at();




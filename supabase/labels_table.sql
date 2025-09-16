create table if not exists public.labels (
    label_id uuid primary key,
    request_id text not null,
    annotator_uid uuid not null,
    annotator text,
    annotator_display text,
    role text default 'annotator',
    timestamp timestamptz default timezone('utc', now()),
    priority text,
    features jsonb default '{}'::jsonb,
    abstain boolean default false,
    needs_review boolean default false,
    status text default 'pending',
    notes text,
    image_paths text[],
    image_checksums text[],
    revision_of uuid
);

alter table public.labels enable row level security;

create policy if not exists "labels_insert_own"
    on public.labels
    for insert
    to authenticated
    with check (auth.uid() = annotator_uid);

create policy if not exists "labels_select_own_or_reviewer"
    on public.labels
    for select
    to authenticated
    using (
        auth.uid() = annotator_uid
        or coalesce(auth.jwt()->>'role', '') = 'reviewer'
    );

create policy if not exists "labels_update_own"
    on public.labels
    for update
    to authenticated
    using (auth.uid() = annotator_uid)
    with check (auth.uid() = annotator_uid);

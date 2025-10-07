alter table public.labels
    drop column if exists abstain;

alter table public.labels
    drop column if exists needs_review;

alter table public.labels
    drop column if exists status;

alter table public.labels
    drop column if exists confidence;

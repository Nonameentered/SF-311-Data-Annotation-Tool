alter table public.labels
    add column if not exists review_status text default 'pending';

alter table public.labels
    add column if not exists review_notes text;

alter table public.labels
    add constraint labels_review_status_chk
    check (
        review_status in ('pending', 'agree', 'disagree')
    );

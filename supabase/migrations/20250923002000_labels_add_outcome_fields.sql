alter table public.labels
    add column if not exists outcome_alignment text;

alter table public.labels
    add column if not exists follow_up_need text[] default '{}'::text[];

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'labels_outcome_alignment_chk'
            and conrelid = 'public.labels'::regclass
    ) then
        alter table public.labels
            add constraint labels_outcome_alignment_chk
            check (
                outcome_alignment is null
                or outcome_alignment in (
                    'service_delivered',
                    'client_declined',
                    'unable_to_locate',
                    'other'
                )
            );
    end if;
end $$;

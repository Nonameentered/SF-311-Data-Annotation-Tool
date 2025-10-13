-- Extend allowed values for labels.outcome_alignment
do $$
begin
    if exists (
        select 1 from pg_constraint
        where conname = 'labels_outcome_alignment_chk'
          and conrelid = 'public.labels'::regclass
    ) then
        alter table public.labels
            drop constraint labels_outcome_alignment_chk;
    end if;

    alter table public.labels
        add constraint labels_outcome_alignment_chk
        check (
            outcome_alignment is null
            or outcome_alignment in (
                'service_delivered',
                'client_declined',
                'unable_to_locate',
                'no_action_needed',
                'invalid_report',
                'duplicate_report',
                'other'
            )
        );
end $$;



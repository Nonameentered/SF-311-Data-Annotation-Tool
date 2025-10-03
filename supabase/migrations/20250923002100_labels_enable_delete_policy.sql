do $$
begin
    if exists (
        select 1
        from pg_policies
        where schemaname = 'public'
          and tablename = 'labels'
          and policyname = 'labels_delete_own'
    ) then
        drop policy "labels_delete_own" on public.labels;
    end if;
end $$;

create policy "labels_delete_own"
    on public.labels
    for delete
    to authenticated
    using (auth.uid() = annotator_uid);

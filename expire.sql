DO $$
DECLARE
    partition_name text;
    cutoff text := to_char((now() AT TIME ZONE 'UTC')::date - 175, 'YYYYMMDD');
BEGIN
    IF to_regclass('public.messages') IS NULL THEN
        RETURN;
    END IF;
    FOR partition_name IN
        SELECT child.relname FROM pg_inherits
        JOIN pg_class AS child ON child.oid = inhrelid
        WHERE inhparent = 'public.messages'::regclass
          AND child.relname ~ '^messages_d[0-9]{8}$'
          AND substring(child.relname FROM 11) < cutoff
    LOOP
        EXECUTE format('DROP TABLE IF EXISTS public.%I', partition_name);
    END LOOP;
END $$;

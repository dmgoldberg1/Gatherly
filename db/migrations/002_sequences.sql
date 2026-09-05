-- The original store inserted explicit MAX(id)+1 IDs, leaving sequences behind.
-- Never move a sequence backwards, including on an existing populated database.
DO $$
DECLARE table_name text; sequence_name text; highest bigint; current_value bigint;
BEGIN
  FOREACH table_name IN ARRAY ARRAY['users','events','participations','notifications','notification_outbox'] LOOP
    sequence_name := pg_get_serial_sequence(table_name, 'id');
    EXECUTE format('SELECT COALESCE(MAX(id), 0) FROM %I', table_name) INTO highest;
    EXECUTE format('SELECT last_value FROM %s', sequence_name) INTO current_value;
    PERFORM setval(sequence_name, GREATEST(highest, current_value, 1), true);
  END LOOP;
END $$;

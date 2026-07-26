SELECT schema_name
FROM information_schema.schemata
WHERE schema_name IN ('meta', 'raw', 'stg', 'mart')
ORDER BY schema_name;


SELECT table_schema, table_name
FROM information_schema.tables
WHERE table_schema IN ('meta', 'raw', 'stg', 'mart')
ORDER BY table_schema, table_name;

SELECT table_schema, table_name
FROM information_schema.views
WHERE table_schema = 'mart'
ORDER BY table_name;
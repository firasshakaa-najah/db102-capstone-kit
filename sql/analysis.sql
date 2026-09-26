-- DB-102 week 3: finding and ranking slow queries in the live workload.
-- Run pieces of this file while workload/run_workload.py is hammering the database.

USE shopdb;

-- 0. Start a clean measurement window (do this before each measured run, and
--    again after each fix in week 4 so before/after numbers are comparable).
-- TRUNCATE TABLE performance_schema.events_statements_summary_by_digest;

-- 1. The ten worst by TOTAL time. sys.statement_analysis is
--    performance_schema.events_statements_summary_by_digest with readable units,
--    already sorted by total latency.
--    (No ORDER BY here on purpose: total_latency in this view is a formatted string;
--     the view is already sorted by the raw value. Use sys.x$statement_analysis for numbers.)
SELECT query, exec_count, total_latency, avg_latency, max_latency,
       rows_examined_avg, rows_sent_avg, full_scan, tmp_disk_tables, rows_sorted
FROM sys.statement_analysis
WHERE db = 'shopdb'
LIMIT 10;

-- 2. The least EFFICIENT: rows examined per row returned.
SELECT query, exec_count, rows_examined_avg, rows_sent_avg,
       ROUND(rows_examined_avg / GREATEST(rows_sent_avg, 1)) AS examined_per_sent
FROM sys.statement_analysis
WHERE db = 'shopdb' AND exec_count >= 5
ORDER BY examined_per_sent DESC
LIMIT 10;

-- 3. What users FEEL: p95 / p99 per statement shape (picoseconds -> ms).
SELECT LEFT(DIGEST_TEXT, 90) AS query, COUNT_STAR AS execs,
       ROUND(QUANTILE_95 / 1e9, 1) AS p95_ms, ROUND(QUANTILE_99 / 1e9, 1) AS p99_ms,
       ROUND(SUM_TIMER_WAIT / 1e12, 1) AS total_s
FROM performance_schema.events_statements_summary_by_digest
WHERE SCHEMA_NAME = 'shopdb'
ORDER BY QUANTILE_95 DESC
LIMIT 10;

-- 4. The symptoms: full scans, on-disk temp tables, sorts.
SELECT query, exec_count, total_latency, no_index_used_count, no_good_index_used_count
FROM sys.statements_with_full_table_scans
WHERE db = 'shopdb'
LIMIT 10;

SELECT query, exec_count, memory_tmp_tables, disk_tmp_tables, tmp_tables_to_disk_pct, total_latency
FROM sys.statements_with_temp_tables WHERE db = 'shopdb' LIMIT 10;

SELECT query, exec_count, rows_sorted, sort_merge_passes, total_latency
FROM sys.statements_with_sorting WHERE db = 'shopdb' LIMIT 10;

-- 5. Right now: what is running this second, and the plan of one of them.
SELECT thd_id, conn_id, user, current_statement, statement_latency, rows_examined
FROM sys.session
WHERE command = 'Query' AND current_statement IS NOT NULL
ORDER BY statement_latency DESC;
-- EXPLAIN FOR CONNECTION <conn_id>;     -- the plan of a statement still executing
-- KILL QUERY <conn_id>;                 -- stop a runaway

-- 6. The slow query log (statements over long_query_time = 0.5 s, with log_slow_extra).
--    Where is it?  SELECT @@slow_query_log_file;   (relative names live in SELECT @@datadir;)
--    Then, in a terminal (the file belongs to the MySQL server; you may need sudo):
--      tail -n 100 <that file>
--      mysqldumpslow -s t -t 10 <that file>
--      pt-query-digest <that file>     (Percona Toolkit)

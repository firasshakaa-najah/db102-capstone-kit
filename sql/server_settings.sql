-- DB-102: the server settings the course expects, applied with SET PERSIST so they survive
-- a restart (stored in mysqld-auto.cnf in the data directory). Run by scripts/setup.sh.
-- The values are deliberately modest; week 4 tunes them.

-- memory & storage
SET PERSIST innodb_buffer_pool_size        = 1073741824;   -- 1G
SET PERSIST innodb_redo_log_capacity       = 536870912;    -- 512M
SET PERSIST innodb_flush_log_at_trx_commit = 2;            -- faster bulk load; week 7 discusses the trade-off

-- bulk loading: allow LOAD DATA LOCAL INFILE (scripts/load.sh)
SET PERSIST local_infile = ON;

-- observability (week 3: the three sources of truth). performance_schema is ON by default.
SET PERSIST slow_query_log                = ON;
SET PERSIST long_query_time               = 0.5;   -- seconds; the default 10 hides almost everything
SET PERSIST log_slow_extra                = ON;    -- rows examined, temp tables, sort passes per statement
SET PERSIST log_queries_not_using_indexes = OFF;   -- switch ON during the lab and watch the log grow

-- connections
SET PERSIST max_connections = 300;

-- the application user the workload runner connects as (week 3 tells it apart from you in sys)
CREATE DATABASE IF NOT EXISTS shopdb      DEFAULT CHARACTER SET utf8mb4 DEFAULT COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE IF NOT EXISTS shopdb_full DEFAULT CHARACTER SET utf8mb4 DEFAULT COLLATE utf8mb4_0900_ai_ci;
CREATE USER IF NOT EXISTS 'app'@'localhost' IDENTIFIED BY 'app';
CREATE USER IF NOT EXISTS 'app'@'127.0.0.1' IDENTIFIED BY 'app';
GRANT SELECT, INSERT, UPDATE, DELETE ON shopdb.*      TO 'app'@'localhost', 'app'@'127.0.0.1';
GRANT SELECT, INSERT, UPDATE, DELETE ON shopdb_full.* TO 'app'@'localhost', 'app'@'127.0.0.1';

SELECT @@innodb_buffer_pool_size DIV 1048576 AS buffer_pool_mb, @@slow_query_log_file AS slow_log_file,
       @@GLOBAL.long_query_time AS long_query_time, @@local_infile AS local_infile;

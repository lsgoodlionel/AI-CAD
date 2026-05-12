-- 初始化 Apache AGE 扩展（在 PostgreSQL 启动时自动执行）
CREATE EXTENSION IF NOT EXISTS age;
LOAD 'age';
SET search_path = ag_catalog, "$user", public;
SELECT create_graph('regulation_graph');

-- 043: 轴网识别补 leader_count —— 坐标标注引线数
--
-- 背景:`services/drawing_role.py` 的第 1 级判据(内容特征,与图号体系无关)
-- 需要「轴号圈数 + 坐标标注引线数」才能判定一张图是不是**坐标基准图**。
-- 圈数已有列,引线数只在识别结果的内存字典里、从未落库,
-- 于是最可靠的那一级判据拿不到证据,只能退回图名关键词。
--
-- 实测:仅靠图名时 2309 张里只判出 2 张坐标基准图(实际 3 张),
-- `竖向结构定位图` 因标题不含「轴网」被漏掉。

ALTER TABLE axis_recognition
    ADD COLUMN IF NOT EXISTS leader_count integer NOT NULL DEFAULT 0;

COMMENT ON COLUMN axis_recognition.leader_count IS
    '坐标标注引线数(GB/T 50001 §11.8)。与 circle_count 一起构成'
    '「坐标基准图」的内容指纹 —— 判据不依赖任何工程的图号体系。';

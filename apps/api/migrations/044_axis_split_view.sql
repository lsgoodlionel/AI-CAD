-- 044: 轴网识别补「一图多视图分幅」标记
--
-- 背景:`A-20-02A 南立面图` 一张纸画两幅立面,系统把第二幅当独立分区、
-- 按 §8.0.5 从 1 重新编号(应为 `1-13`~`1-24`,给出 `1`~`12`),
-- 并且把它挂进「待人工确认分区号」队列 —— 而分幅根本没有分区号。
--
-- 判据:§8.0.5 的分区在**平面**上两个方向都标轴号,而立面/剖面是投影图,
-- 只有单向轴号带。**单向 = 分幅**。

ALTER TABLE axis_recognition
    ADD COLUMN IF NOT EXISTS is_split_view boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS split_view_numbering jsonb;

COMMENT ON COLUMN axis_recognition.is_split_view IS
    '一图多视图的分幅(非 GB/T 50001 §8.0.5 分区)。分幅无分区号可确认,'
    '不进人工队列;轴号应跨幅连续编号。';
COMMENT ON COLUMN axis_recognition.split_view_numbering IS
    '各幅串起来的连续编号方案 [{index,position,start,end,count,overlap_assumed}]。'
    'overlap_assumed 是搭接根数的**假设**(制图惯例 1),不是识别结果。';

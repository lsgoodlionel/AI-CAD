-- 051: drawing_transform 增加 label_confidence —— 保留旧 confidence 的口径
--
-- 旧 confidence = 带标签轴线数/轴线总数 × 比例是否常用值。
-- 它衡量的是**轴号识别质量**，与比例对错无关：金标准 56 条独立裁决实测
--   confidence = 1.00 → 合理率 24%
--   confidence < 1.00 → 合理率 37%
-- 即旧值携带**负信息**（梁批独立同向佐证：满分置信的图梁 44%，无记录的 78%）。
--
-- 从本次起 confidence 改为 `core.model3d.scale_evidence` 的**多来源交叉证据分**，
-- 旧口径搬到 label_confidence —— 「轴号识别得怎么样」本身有用，只是不该叫置信度。
--
-- 存量行的 label_confidence 先置为原 confidence（那正是旧口径的值），
-- confidence 本身**不动** —— 重跑识别时才按新口径写入，
-- 直接批量改会让下游 `scale_gate.is_transform_trustworthy` 在无人知晓的情况下变脸。

ALTER TABLE drawing_transform
    ADD COLUMN IF NOT EXISTS label_confidence NUMERIC(6, 4);

UPDATE drawing_transform
SET label_confidence = confidence
WHERE label_confidence IS NULL AND confidence IS NOT NULL;

COMMENT ON COLUMN drawing_transform.label_confidence IS
    '旧口径：带标签轴线数/轴线总数 × 比例是否常用值（轴号识别质量，非比例对错）';
COMMENT ON COLUMN drawing_transform.confidence IS
    '比例证据分：图上印刷 1:N / GB-T 50001 §6.0.4 表 / 图幅覆盖 的加权（core.model3d.scale_evidence）';

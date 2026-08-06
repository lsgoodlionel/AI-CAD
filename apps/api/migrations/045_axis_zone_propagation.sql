-- 分区号的**来源**必须可区分:人工确认 vs 序列匹配自动传播。
--
-- 背景(Phase J J1 实测):§8.0.5 的分区编号几何推不出,只能人工确认,
-- 而全项目 1052 张多分区图逐张确认不现实。J1 的轴距序列匹配可把已确认
-- 分区自动传播到其他图(实测 143 张),但**自动推导的结论不得冒充人工确认**
-- —— 见 docs/MODELING_PIPELINE_BLUEPRINT.md §7 约束 3「降级必须可见」。
--
-- 传播只以**人工确认**的图为锚,不以传播结果为锚:否则一次误传播会沿链
-- 扩散,且无法回溯到源头。

ALTER TABLE axis_zone_confirmation
    ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'manual',
    -- 传播来源:锚图与其分区下标。人工确认时为 NULL。
    ADD COLUMN IF NOT EXISTS anchor_drawing_id UUID
        REFERENCES drawings(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS anchor_zone_index INT,
    -- 实测比例比(局部图轴距和 ÷ 锚图对应段和)。偏离 1.0 说明两图变换不一致,
    -- 是独立于序列本身的一道合理性校验。
    ADD COLUMN IF NOT EXISTS scale_ratio DOUBLE PRECISION;

ALTER TABLE axis_zone_confirmation
    DROP CONSTRAINT IF EXISTS axis_zone_confirmation_source_check;
ALTER TABLE axis_zone_confirmation
    ADD CONSTRAINT axis_zone_confirmation_source_check
    CHECK (source IN ('manual', 'propagated'));

CREATE INDEX IF NOT EXISTS idx_axis_zone_confirmation_source
    ON axis_zone_confirmation(project_id, source);

COMMENT ON COLUMN axis_zone_confirmation.source IS
    'manual=人工确认(可作传播锚);propagated=轴距序列匹配自动推导(不可作锚)';
COMMENT ON COLUMN axis_zone_confirmation.anchor_drawing_id IS
    '传播来源锚图;人工确认为 NULL。误传播可由此回溯源头';

-- 比例比偏离超阈值:仍然传播，但标出来供人审（降级必须可见）。
-- 它不是门禁 —— 比例比在数学上受限于匹配的逐段容差，做不成独立门禁。
ALTER TABLE axis_zone_confirmation
    ADD COLUMN IF NOT EXISTS needs_review BOOLEAN NOT NULL DEFAULT false;

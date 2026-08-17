-- 042: 轴网识别结果补两列 —— 可疑标记与警告留档
--
-- 背景:设备符号场闸从「拦截」改为「标记」(见 services/axis_recognition.py
-- SYMBOL_FIELD_BAND_HINT)。上一版在带数 > 40 时直接不产出轴线,误杀了
-- A-10-04C 一层完整平面图(42 条带,只超 2 条),整层轴线全丢。
--
-- 改为照常产出 + 打标记后,标记必须落库,否则消费方读不到、等于没改。
-- warnings 同理:此前识别过程产生的警告一条都没存,出了问题无从追查。

ALTER TABLE axis_recognition
    ADD COLUMN IF NOT EXISTS suspect_symbol_field boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS warnings jsonb;

COMMENT ON COLUMN axis_recognition.suspect_symbol_field IS
    '疑为设备符号场(满图喷头等)。轴线仍产出并留档,但不进 3D 场景与世界锚点。';
COMMENT ON COLUMN axis_recognition.warnings IS
    '识别过程的警告列表(OCR 失败、疑似符号场等),用于追查。';

-- 消费方按此列过滤,建索引避免全表扫
CREATE INDEX IF NOT EXISTS idx_axis_recognition_not_suspect
    ON axis_recognition (project_id)
    WHERE suspect_symbol_field = false;

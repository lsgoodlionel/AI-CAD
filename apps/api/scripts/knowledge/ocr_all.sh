#!/usr/bin/env bash
# 全批扫描件 OCR —— 进程级分片并行。
#
# 为什么不用 Python 的进程池：实测在 macOS 上 ProcessPoolExecutor 与
# onnxruntime 同用会整池死锁。分片进程互不共享状态，无从死锁，
# 且任一进程被杀只影响自己那一片，重跑自动续。
#
# 用法：scripts/knowledge/ocr_all.sh [分片数，默认 4]
set -uo pipefail
cd "$(dirname "$0")/../.."
N="${1:-4}"
PY=./.venv/bin/python
LOG="${CAD_KNOWLEDGE_CACHE:-$HOME/.cache/cad-knowledge/ocr}/_log"
mkdir -p "$LOG"
export OMP_NUM_THREADS=2
for i in $(seq 0 $((N-1))); do
  $PY scripts/knowledge/ocr_cache.py --all --shard "$i/$N" \
      > "$LOG/shard$i.log" 2>&1 &
done
wait
echo "全部分片完成"

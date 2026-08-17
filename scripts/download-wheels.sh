#!/usr/bin/env bash
# 预下载 API 镜像的全部 Python 依赖到 apps/wheelhouse/(可反复重跑,断了接着下)。
#
# 为什么必须在容器里下:目标平台是 linux/aarch64 + Python 3.12,宿主 macOS 上
# 直接 pip download 会拿到 macOS 版 wheel,装不进镜像。
#
# 用法:
#   bash scripts/download-wheels.sh                    # 默认清华源
#   PIP_INDEX_URL=https://pypi.org/simple bash scripts/download-wheels.sh
set -euo pipefail
cd "$(dirname "$0")/.."

INDEX="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
mkdir -p apps/wheelhouse .pip-cache

for req in requirements.txt requirements-ocr.txt; do
  [ -f "apps/api/$req" ] || continue
  echo "==== 下载 $req (源: $INDEX) ===="
  docker run --rm \
    -v "$PWD/apps/wheelhouse:/wheels" \
    -v "$PWD/.pip-cache:/root/.cache/pip" \
    -v "$PWD/apps/api/$req:/req.txt:ro" \
    -e PIP_INDEX_URL="$INDEX" \
    python:3.12-slim \
    sh -c 'pip download -r /req.txt -d /wheels --find-links=/wheels \
             --retries 10 --timeout 120' \
    || echo "!! $req 未下完,重跑本脚本即可接着下(已下的不会重来)"
done

echo
echo "已下载 $(ls apps/wheelhouse/*.whl apps/wheelhouse/*.tar.gz 2>/dev/null | wc -l | tr -d ' ') 个包," \
     "占用 $(du -sh apps/wheelhouse 2>/dev/null | cut -f1)"

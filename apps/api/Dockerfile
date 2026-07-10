FROM python:3.12-slim

# 构建上下文为 apps/（api 与 sibling 的 model-convert 都需入镜像）；
# Dockerfile 仍在 apps/api/Dockerfile，故所有 COPY 以 apps/ 为根（api/... model-convert/...）。
#
# 依赖下载镜像源（默认官方；国内网络 build 时传 --build-arg 加速，如：
#   PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
#   GNU_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/gnu
#   NODE_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/nodejs-release）
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG GNU_MIRROR=https://ftp.gnu.org/gnu
ARG NODE_VERSION=20.18.1
ARG NODE_MIRROR=https://nodejs.org/dist

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL}

WORKDIR /app

# libgl1/libglib2.0-0：ultralytics(opencv) 运行时依赖
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc g++ libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# GNU LibreDWG（dwg2dxf）：DWG→DXF 自动转换（自由软件，无 ODA 登录墙；
# Debian 仓库无此包，从 GNU ftp 源码编译，独立缓存层）
RUN apt-get update \
    && apt-get install -y --no-install-recommends wget xz-utils make \
    && wget -q ${GNU_MIRROR}/libredwg/libredwg-0.13.3.tar.xz \
    && tar xf libredwg-0.13.3.tar.xz \
    && cd libredwg-0.13.3 \
    && ./configure --disable-bindings --disable-docs --enable-release >/dev/null \
    && make -j"$(nproc)" >/dev/null 2>&1 \
    && make install >/dev/null && ldconfig \
    && cd .. && rm -rf libredwg-0.13.3 libredwg-0.13.3.tar.xz \
    && apt-get purge -y wget xz-utils && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/* \
    && dwg2dxf --version

# Node.js 20（IFC→Fragments 离线转换器运行时）：
# @thatopen/fragments + web-ifc 以 Node 子进程被 services/fragments_convert.py 调用。
# python:3.12-slim 不含 Node，从官方（或镜像）dist 解包安装，按目标架构选 x64/arm64。
RUN apt-get update \
    && apt-get install -y --no-install-recommends wget xz-utils ca-certificates \
    && arch="$(dpkg --print-architecture)" \
    && case "$arch" in \
         amd64) node_arch=x64 ;; \
         arm64) node_arch=arm64 ;; \
         *) echo "unsupported arch for node: $arch" >&2; exit 1 ;; \
       esac \
    && wget -q "${NODE_MIRROR}/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${node_arch}.tar.xz" \
    && tar -xJf "node-v${NODE_VERSION}-linux-${node_arch}.tar.xz" -C /usr/local --strip-components=1 \
    && rm -f "node-v${NODE_VERSION}-linux-${node_arch}.tar.xz" \
    && apt-get purge -y wget xz-utils && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/* \
    && node --version && npm --version

# IFC → Fragments 离线转换器（apps/model-convert，随镜像打包 + 装生产依赖）。
# fragments_convert.py 通过 MODEL_CONVERT_DIR 定位本目录与其 node_modules。
# 先只拷 package*.json 装依赖（命中缓存），再拷脚本/fixtures，避免脚本改动重装 npm。
ENV MODEL_CONVERT_DIR=/opt/model-convert
COPY model-convert/package.json model-convert/package-lock.json ${MODEL_CONVERT_DIR}/
RUN cd ${MODEL_CONVERT_DIR} && npm ci --omit=dev
COPY model-convert/ ${MODEL_CONVERT_DIR}/

COPY api/requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

COPY api/ .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

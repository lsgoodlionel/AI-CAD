FROM python:3.12-slim

# 依赖下载镜像源（默认官方；国内网络 build 时传 --build-arg 加速，如：
#   PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
#   GNU_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/gnu）
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG GNU_MIRROR=https://ftp.gnu.org/gnu

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

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

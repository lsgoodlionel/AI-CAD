# 本地开发 / 构建 / 部署工作流

> 复核结论(2026-07-12 实测):**`docker compose` 没有坏**。此前"非常规 fork / build 空操作 / `!override` 失效"是误判。
> compose v5.x 是 2026 年的正常版本(Docker Desktop 4.81.0 自带 v5.2.0)。真正踩过的坑是**漏加 `--profile app`** 和 **`up` 不带 `--build` 复用旧镜像**。

## 组成

三个 compose 文件叠加使用:

| 文件 | 作用 |
|------|------|
| `docker-compose.yml` | 基础栈(postgres/redis/minio/chroma)+ app 服务(api/web/celery,在 `profiles: [app]` 后面) |
| `docker-compose.alt-ports.yml` | 用 `!override` 重映射宿主端口(8002/5434/9002/3002…),避开常用端口冲突 |
| `docker-compose.dev.yml` | **热重载** override:挂载源码 + `uvicorn --reload`,改代码即生效,不重建镜像 |

项目名统一用 `-p cad`(容器名前缀 `cad_*`)。

## 1. 起基础设施(日常常驻)

```bash
docker compose -p cad \
  -f infra/docker-compose.yml \
  -f infra/docker-compose.alt-ports.yml \
  up -d
```

起来后:PostgreSQL `localhost:5434`、Redis `6380`、MinIO `9002`(控制台 `9003`)、Chroma `8102`。

## 2. 测试期:热重载跑 app(改代码即生效,**不重建镜像**)

```bash
docker compose -p cad \
  -f infra/docker-compose.yml \
  -f infra/docker-compose.alt-ports.yml \
  -f infra/docker-compose.dev.yml \
  --profile app up -d api celery-worker celery-beat
```

- 改 `apps/api/**.py`:
  - **api**:`uvicorn --reload` 秒级自动重载。
  - **celery-worker / beat**:celery 无热重载,改完 `docker restart cad_celery_worker cad_celery_beat` 即加载(源码已挂载,无需重建)。
- 前端热重载最省事的是本机直接跑 dev server:
  ```bash
  cd apps/web && npm run dev      # UmiJS dev(localhost:8000),proxy /api → localhost:8002
  ```
  或构建后灌进 nginx 容器(见 §4)。

> ⚠️ 热重载靠 volume 挂载。若之前用过 `docker cp` 把代码注入容器,recreate 后会回到镜像里的旧代码——统一改用本挂载方式即可避免。

## 3. 正式部署:打包镜像(**必须带 `--profile app` 和 `--build`**)

```bash
# 构建镜像(cad-api:local / cad-web:local)
docker compose -p cad \
  -f infra/docker-compose.yml \
  -f infra/docker-compose.alt-ports.yml \
  --profile app build api web

# 用打包镜像起(不叠加 dev override)
docker compose -p cad \
  -f infra/docker-compose.yml \
  -f infra/docker-compose.alt-ports.yml \
  --profile app up -d
```

- **`up` 不带 `--build` 只会复用现有镜像**(标准行为,不是 bug)。要用新代码打镜像必须先 `build` 或 `up --build`。
- 直接 `docker build` 也可(等价,与 compose 无关):
  ```bash
  docker build -t cad-api:local -f apps/api/Dockerfile apps
  docker build -t cad-web:local apps/web
  ```
- 基础镜像(`node:20-alpine`、`nginx:1.27-alpine` 等)首次构建需联网从 docker.io 拉;离线环境请先 `docker pull` 预热或用私有 registry。

## 4. 快捷命令(仅前后端灌代码,不动 compose)

```bash
# 后端:改完重启即可(dev 模式已挂载;若非 dev 模式才需 docker cp)
docker restart cad_api cad_celery_worker

# 前端:构建后灌进 nginx 容器
cd apps/web && npm run build
docker exec cad_web sh -c 'rm -rf /usr/share/nginx/html/*'
docker cp apps/web/dist/. cad_web:/usr/share/nginx/html/

# 应用迁移
docker exec -i cad_postgres psql -U cad_user -d cad_db < apps/api/migrations/xxx.sql
```

## 常见误区（对照排查）

| 现象 | 真因 | 处理 |
|------|------|------|
| `docker compose build` 好像没构建 app | 漏了 `--profile app`(api/web 在 profile 后面) | 加 `--profile app` |
| 改了代码 `up -d` 没生效 | `up` 复用旧镜像 | `up -d --build` 或先 `build`;开发期用 §2 热重载 |
| 端口没映射到 8002 | 漏叠加 `docker-compose.alt-ports.yml` | 补 `-f infra/docker-compose.alt-ports.yml` |
| recreate 后代码回退 | 之前是 `docker cp` 注入的 | 用 §2 挂载热重载,别再 cp |
| `cad_api_proxy` 代理容器 | 历史遗留 workaround,`!override` 已能直接映 8002 | 可 `docker rm -f cad_api_proxy` |

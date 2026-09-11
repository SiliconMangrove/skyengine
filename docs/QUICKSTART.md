# SkyEngine Quickstart

本文给出从零部署 SkyEngine 的最短路径。平台本身、在线仿真引擎和前端由 `skyengine` 管理；FJSP/MAPF 算子由两个独立仓库提供，首次部署时必须先构建对应镜像。

## 1. 准备环境

Linux 部署需要：

- Git
- Docker Engine
- Docker Compose v2（或兼容的 `docker-compose`）
- 可用的 Docker daemon
- 至少 8 GB 可用内存和足够的磁盘空间

使用 GPU 算子时，还需要 NVIDIA 驱动和 NVIDIA Container Toolkit。只使用 CPU 算子时不需要 GPU。

## 2. 获取项目

将本项目源码复制或检出到 Linux 主机，并进入 `skyengine` 根目录：

```bash
cd /path/to/skyengine
```

后续命令都在 `skyengine` 根目录或对应的算法仓库目录中执行。

## 3. 构建 FJSP 与 MAPF 算子仓库

在与 `skyengine` 同级的目录中克隆两个算法仓库，并分别使用 Docker Compose 构建全部镜像：

```bash
cd ..
git clone https://github.com/skyrimforest/SkyEngine-FJSP.git
git clone https://github.com/skyrimforest/SkyEngine-MAPF.git
cd SkyEngine-FJSP
docker compose build
cd ../SkyEngine-MAPF
docker compose build
```

两个外部仓库的镜像名称必须与其说明一致。部分算法需要 GPU 或额外模型权重，具体以对应仓库 README 为准。

## 5. 安装 SkyEngine

返回 `skyengine` 目录，首次安装时执行：

```bash
cd ../skyengine
chmod +x install.sh start.sh stop.sh
./install.sh
```

`install.sh` 会检查 Docker 和 Compose，生成或更新 `.env`，创建运行所需目录，并构建平台、在线引擎和批处理引擎镜像。脚本会把当前项目的绝对路径写入 `.env`，不需要手工填写 Windows 路径或容器路径。

## 6. 启动服务

```bash
./start.sh
```

脚本会启动前端、平台后端和在线仿真引擎，并等待服务 HTTP 就绪。脚本会检测默认宿主机端口是否已被占用；如果被占用，会自动选择后续可用端口并写回 `.env`。因此实际访问地址以脚本最后输出为准，默认值为：

- 前端：`http://localhost:5180`
- 后端 API：`http://localhost:8233`
- 在线引擎：`http://localhost:8080`

容器内部端口由 Compose 固定，宿主机端口可以因自动协调而变化。

查看服务状态和日志：

```bash
docker compose -f docker-compose.yml ps
docker compose -f docker-compose.yml logs -f backend frontend
docker compose -p skyengine-online -f docker-compose-online.yaml logs -f engine
```

打开脚本输出的前端地址即可使用网页界面。平台通过 DockerProxy 按需启动在线算法服务；算法镜像需要先完成第 3、4 步的构建。

## 7. 停止服务

```bash
./stop.sh
```

该脚本会停止平台、在线引擎和批处理引擎 Compose 项目，但不会删除镜像、数据集或日志。

## 可选配置

- `.env` 中的 `FJSP_IMAGE` 和 `MAPF_IMAGE` 可用于选择已构建的默认算法镜像。
- 使用 GPU 算子时，按需设置 `CUDA_VISIBLE_DEVICES`，并确保 Docker 能访问 GPU。
- 数据集位于 `dataset/`，批处理和在线引擎会使用项目配置的挂载目录。

## 常见问题

### 前端或后端端口变化

这是启动脚本的端口自动协调功能。使用 `start.sh` 输出的地址访问服务，并查看 `.env` 中的 `BACKEND_PORT`、`FRONTEND_PORT` 和 `ENGINE_PORT`。

### 算法服务启动时找不到镜像

确认已经分别进入 `SkyEngine-FJSP` 和 `SkyEngine-MAPF`，并按它们各自的说明成功执行过 `docker compose build`。选择了特定算子时，还要确认对应的镜像标签与平台配置一致。

### 服务启动失败

先查看容器状态和日志：

```bash
docker compose -f docker-compose.yml ps
docker compose -f docker-compose.yml logs --tail 100 backend frontend
docker compose -p skyengine-online -f docker-compose-online.yaml logs --tail 100 engine
```

如果修改了依赖或 Dockerfile，重新执行 `./install.sh`；只修改源码时重新执行 `./start.sh` 即可触发平台镜像更新。

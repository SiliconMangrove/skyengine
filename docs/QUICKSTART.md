# SkyEngine Quickstart

本文给出从零部署 SkyEngine 的最短路径。平台本身、在线仿真引擎和前端由 `skyengine` 管理；FJSP/MAPF 容器算子由两个独立仓库提供，首次部署时必须先构建对应镜像；通用算法实验平台中的 CTDE-PPO 还使用同级 `skyengine-DFJSPT` Python 算法仓库。

## 1. 准备环境

Linux 部署需要：

- Git
- Docker Engine
- Docker Compose v2（或兼容的 `docker-compose`）
- 可用的 Docker daemon
- 至少 8 GB 可用内存和足够的磁盘空间

使用 GPU 算子时，还需要 NVIDIA 驱动和 NVIDIA Container Toolkit。只使用 CPU 时不需要 GPU，仍可使用 SPT、EDD、加权规则、滚动时域 GA、CP-SAT，并可进行超参数探索、测试和数据集结果比较。

## 2. 获取项目

将本项目源码复制或检出到 Linux 主机，并进入 `skyengine` 根目录：

```bash
cd /path/to/skyengine
```

后续命令都在 `skyengine` 根目录或对应的算法仓库目录中执行。四个目录应保持同级，目录名如下：

```text
workspace/
├── skyengine/
├── skyengine-DFJSPT/
├── SkyEngine-FJSP/
└── SkyEngine-MAPF/
```

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

## 4. 准备 DFJSP-T Python 算法仓库

`skyengine-DFJSPT` 与 FJSP/MAPF 容器算子仓库用途不同：它提供可直接导入的 `dfjsp_t_rl` Python 包，其中包括 CTDE-PPO 策略、训练器和插件注册入口，不需要在本步骤构建独立算法镜像。

将交付包中的该仓库放到 `skyengine` 的同级目录，并保留小写目录名：

```bash
cp -a /path/to/delivery/skyengine-DFJSPT /path/to/workspace/skyengine-DFJSPT
```

如果团队另有正式版本库地址，也可以从该授权来源检出，但最终目录必须准确命名为 `skyengine-DFJSPT`，且其中应存在 `dfjsp_t_rl/__init__.py`。

Compose 会把 `../skyengine-DFJSPT` 只读挂载到后端和在线引擎容器的 `/app/skyengine-DFJSPT`，并加入 Python 模块搜索路径。`install.sh` 会预检目录和 `dfjsp_t_rl/__init__.py`；缺失时安装会明确终止并提示补齐，以免服务启动后才暴露不完整部署。

## 5. 安装 SkyEngine

返回 `skyengine` 目录，首次安装时执行：

```bash
cd ../skyengine
chmod +x install.sh start.sh stop.sh
./install.sh
```

`install.sh` 会检查 Docker 和 Compose，生成或更新 `.env`，创建运行所需目录，并构建平台、在线引擎和批处理引擎镜像。脚本会把当前项目的绝对路径写入 `.env`，并在 backend 镜像内实际调用 PyTorch 探测 Docker 可用的 CUDA 设备。没有可用 CUDA 时安装仍会完成，平台自动以 CPU 模式运行；`.env` 显式设置 `SKYENGINE_GPU_MODE=cuda` 时，探测失败会终止安装并说明原因。

## 6. 启动服务

```bash
./start.sh
```

脚本会启动前端、平台后端和在线仿真引擎，并等待服务 HTTP 就绪。脚本会检测默认宿主机端口是否已被占用；如果被占用，会自动选择后续可用端口并写回 `.env`。因此实际访问地址以脚本最后输出为准，默认值为：

- 前端：`http://localhost:5180`
- 后端 API：`http://localhost:8233`
- 在线引擎：`http://localhost:8080`

容器内部端口由 Compose 固定，宿主机端口可以因自动协调而变化。

Windows 可以执行 `启动SkyEngine开发服务.ps1`。Windows 与 Linux 都使用相同的 backend/frontend 容器和相同的 GPU 探测逻辑，PyTorch 安装在 backend 镜像中，不再依赖 Windows 宿主机 Python 环境。

查看服务状态和日志：

```bash
docker compose -f docker-compose.yml ps
docker compose -f docker-compose.yml logs -f backend frontend
docker compose -p skyengine-online -f docker-compose-online.yaml logs -f engine
```

打开脚本输出的前端地址即可使用网页界面。平台通过 DockerProxy 按需启动在线算法服务；FJSP/MAPF 算法镜像需先完成第 3 步构建，CTDE-PPO 则要求第 4 步的同级 Python 仓库存在。

### 使用通用算法实验工作台

打开前端，在数字车间工作空间列表中选择“算法实验与优化平台”（原强化学习平台的位置），也可直接访问 `/training`。工作台提供训练、超参数探索和测试三种实验模式，并从后端数据集目录显式选择训练集、调优集和测试集。测试完成后，统一比较页可按测试数据集汇总算法结果，回放页可按数据集、实例和算法选择运行记录。CPU 环境可直接测试规则、滚动 GA 或 CP-SAT，也可对规则外部参数进行探索；CTDE-PPO 使用 `device=auto`，没有 GPU 时会走 CPU，但完整训练耗时会明显增加。

CTDE-PPO 默认使用 4 个并行采样环境。Checkpoint 保存间隔填写累计采样步数：0 仅保留最佳，正数另外保留定期快照。达到间隔后会在该批网络更新完成时保存；监控页可下载 checkpoint 或继续训练。

建议先点击“校验”和“编译”，再提交执行。Execution 可在监控页发出协作式取消请求；状态会先变为 `cancel_requested`，算法或 Runtime 到达检查点后变为 `cancelled`。取消不会强杀第三方求解器的一次阻塞调用。

### 启用后端 GPU

GPU 模式由 `.env` 的 `SKYENGINE_GPU_MODE` 控制：

```dotenv
SKYENGINE_GPU_MODE=auto
```

- `auto`：探测成功时加载 `docker-compose.gpu.yml`，否则使用 CPU；
- `cuda`：必须探测到至少一张 Docker 内可用的 NVIDIA GPU，否则安装或启动失败；
- `cpu`：不向 backend 请求 GPU。

GPU 覆盖文件使用 `gpus: all` 把可用设备交给 backend。训练页面中的“GPU 卡号”决定 PPO 选择哪张卡；不填写时使用 `cuda:0`。当前 PPO 是单卡训练，即使机器有多张 GPU，也只使用所选列表中的第一张。

修改依赖、Dockerfile 或 GPU 配置后，确认当前没有不能中断的训练任务，再重新安装并启动：

```bash
./install.sh
./start.sh
```

启动输出会显示 backend 最终使用 CUDA 还是 CPU。CUDA 模式下也可以检查容器中的设备：

```bash
docker compose -f docker-compose.yml -f docker-compose.gpu.yml exec backend .venv/bin/python -c "import torch; print(torch.cuda.is_available(), torch.cuda.device_count(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU')"
```

宿主机必须已安装 NVIDIA 驱动和 NVIDIA Container Toolkit。重建 backend 会中断其中正在运行的实验；平台重启恢复会把未达到终态的 Execution 记录为失败，因此不要在训练过程中执行。

## 7. 停止服务

```bash
./stop.sh
```

该脚本会停止平台、在线引擎和批处理引擎 Compose 项目，但不会删除镜像、数据集或日志。

## 可选配置

- `.env` 中的 `FJSP_IMAGE` 和 `MAPF_IMAGE` 可用于选择已构建的默认算法镜像。
- `SKYENGINE_GPU_MODE` 可设为 `auto`、`cuda` 或 `cpu`；GPU 模式仍要求宿主机安装兼容的 NVIDIA 驱动和 NVIDIA Container Toolkit。
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

### 并行采样启动时报 OpenCV 或 libGL 错误

更新项目依赖和锁文件后重新构建后端镜像，再启动服务。平台使用无界面 OpenCV，支持图片编码，不需要安装桌面显示组件。旧环境中若同时装有两种 OpenCV，应按项目锁文件同步依赖。

采样任务必须等待所有环境进程初始化完成后才能开始。若初始化失败，执行详情会给出进程信息；Python 启动阶段的完整错误可在后端容器日志中查看。

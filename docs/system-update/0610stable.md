# 0610 Stable 分支合并前检查报告

> 分支: `0610stable-update` -> `main`
> 检查日期: 2026-06-10
> 提交数: 9 commits (02c1b1c ~ c423579)
> 变更量: 939 files, +797,553 / -8,794

---

## 1. 严重问题 (必须处理)

### 1.1 169MB 二进制文件被提交到仓库

**路径:** `application/dockerfile/docker-bin/`

| 文件 | 大小 |
|------|------|
| `docker` | 43MB |
| `docker-compose` | 73MB |
| `uv` | 59MB |
| **合计** | **169MB** |

**风险:** Git 仓库膨胀，clone 慢，历史永远保留这些大文件。
**建议:**
- 使用 Git LFS 管理，或
- 改为 Dockerfile 中 `RUN curl -L ...` 在构建时下载，或
- 使用 multi-stage build 从基础镜像复制

### 1.2 外部 IP 地址硬编码 (涉密)

**共 3 处引用 `114.212.82.168` (疑似内部服务器 IP):**

| 文件 | 行号 | 内容 |
|------|------|------|
| `application/frontend/vite.config.js` | 28 | `target: 'http://114.212.82.168:8000'` |
| `application/frontend/.env.development` | 2 | `VITE_RAG_URL=http://114.212.82.168:8000` |
| `docker-compose.yml` | 48 | `RAG_BACKEND_URL=...http://114.212.82.168:8000` |

**建议:** 统一为环境变量，移除硬编码 IP。`.env.development` 可保留但添加到 `.gitignore`。

### 1.3 本机绝对路径硬编码 (涉密)

| 文件 | 行号 | 内容 |
|------|------|------|
| `docker-compose.yml` | 20 | `/data1/home/wuhao/project/finalpro/SkyEngine/...` |
| `docker-compose.yml` | 25-26 | `SKYENGINE_PROJECT_DIR=/data1/home/wuhao/project/finalpro/SkyEngine` |
| `docker-compose.yml` | 66 (注释) | `/home/wh/dslink/skyrim/hf/Qwen/Qwen3.6-35B-A3B-FP8` |

**建议:** docker-compose.yml 中的默认值改为 `${VAR}` 形式，不提供具体的本地路径默认值。

### 1.4 `.claude/settings.local.json` 被提交

**路径:** `.claude/settings.local.json`
**风险:** 泄露本地开发工具的权限配置，包含具体路径信息。
**建议:** 添加到 `.gitignore`。

---

## 2. 代码冗余 (建议清理)

### 2.1 后端 print 语句 (165 处)

大量调试 `print()` 散布在核心模块中，应替换为 `logging` 或删除：

| 文件 | print 数量 | 说明 |
|------|-----------|------|
| `DockerProxy.py` | ~20 | 生命周期日志 |
| `BaseFactoryProxy.py` | ~10 | 含 `__main__` 测试块 |
| `StaticFactoryProxy.py` | ~10 | 含 `__main__` 测试块 |
| `PacketFactoryProxy.py` | ~5 | 路由注册日志 |
| `RouteRegistry.py` | ~1 | 路由注册日志 |
| `backend_core.py` | ~1 | `print("factory_start")` |

### 2.2 前端 console.log (63 处)

| 文件 | 数量 | 说明 |
|------|------|------|
| `factoryConnection.js` | ~15 | 连接管理日志 |
| `sse.js` | ~1 | SSE 连接日志 |
| `DockerFactoryManage.vue` | ~2 | 挂载/卸载日志 |
| `GridFactoryManage.vue` | ~2 | 挂载/卸载日志 |
| `FactoryManage.vue` | ~5 | 状态更新日志 |
| `PacketFactoryManage.vue` | ~7 | API 响应日志 |
| `HomeView.vue` | ~2 | 配置日志 |

### 2.3 内联测试代码 (`if __name__ == "__main__"`)

以下文件包含内联测试代码，生产环境不应执行：

- `application/backend/core/BaseFactoryProxy.py:259`
- `application/backend/core/StaticFactoryProxy.py:438`
- `application/backend/packet_factory/config_set/__init__.py:37`
- `application/backend/packet_factory/service/file_service.py:148`
- `application/backend/joint_sim/io/use_io.py:220`
- `application/backend/joint_sim/component/JobSolver/utils/PDRScheduler.py:162`
- `application/backend/joint_sim/utils/logger.py:53`
- `application/backend/joint_sim/utils/machine.py:236`

**建议:** 迁移到 `tests/` 目录，或使用 `pytest` 替代内联测试。

---

## 3. 大文件/数据集问题 需要进行删除，看看使用什么方案比较好。

### 3.1 dataset 总计 52MB 需要进行删除，看看使用什么方案比较好。

| 目录 | 大小 | 说明 |
|------|------|------|
| `dataset/map_dataset/pogema-benchmark-main/` | 32MB | 含大量实验配置、原始数据、C++ 源码 |
| `dataset/mapf/medium_maps.yaml` | 4.5MB | 单个 YAML 文件，20 万行 |
| `dataset/fjsp-instances/` | ~10MB | FJSP 算例 JSON |

**建议:**
- `pogema-benchmark-main/` 含完整 C++ 源码 (lacam3, rhcr_cpp, scrimp 等) 和大量实验数据，考虑用 Git LFS 或 submodule
- `medium_maps.yaml` (4.5MB) 过大，考虑压缩或外部存储

### 3.2 已删除但体积仍占历史的数据 需要进行删除，看看使用什么方案比较好。

- `dataset/JSPLIB-master/` 已在本次提交中删除 (100+ 文件)
- `dataset/agv-instances/` 已删除
- `dataset/convert/` 已删除
- `dataset/job_dataset/` 已删除

**注意:** 虽然 HEAD 中已删除，但 Git 历史仍保留。如需彻底清理仓库体积，需 `git filter-branch` 或 `git-filter-repo`。

---

## 4. 安全配置问题

### 4.1 CORS 全开放 允许来源，后续再升级安全性

| 文件 | 行号 | 配置 |
|------|------|------|
| `application/backend/server.py` | 49 | `allow_origins=["*"]` |
| `application/backend/packet_factory/backend_server.py` | 159 | `allow_origins=["*"]` |

**建议:** 生产环境限制为具体域名。

### 4.2 RAG API 硬编码 fallback token 当前基本使用本地推理服务，无需API KEY，fall back不影响。

| 文件 | 行号 | 内容 |
|------|------|------|
| `application/frontend/src/utils/rag.js` | 20 | `'token-local-dev'` |
| `docker-compose.yml` | 81 (注释) | `--api-key ${API_KEY:-token-local-dev}` |

**建议:** 生产环境必须使用真正的 API key，不应有 fallback 值。

### 4.3 Docker Socket 挂载 不需要修改，就是这样设计的。

| 文件 | 行号 | 内容 |
|------|------|------|
| `docker-compose.yml` | 18 | `/var/run/docker.sock:/var/run/docker.sock` |

**风险:** 容器内进程可完全控制宿主机 Docker daemon（等同于 root 权限）。
**建议:** 确认这是有意为之，并考虑使用 Docker-in-Docker 或 Podman 替代方案。

---

## 5. 文档冗余 已完成

### 5.1 `docs/front-update/` (11 个文件)

含大量过程性文档和 PPT 素材，对项目代码无直接价值：

| 文件 | 说明 |
|------|------|
| `三工厂架构更新.md` | 架构设计文档 |
| `前端系统更新.md` | 系统更新 |
| `工厂资产方案.md` | 资产方案 |
| `工厂资产update.md` | 增量更新 |
| `演示demo.md` | 演示文档 |
| `热力图分层更新.md` | 热力图更新 |
| `重构方案.md` | 重构方案 |
| `claude整合.md` | claude 整合说明 |
| `PPT_TEAM_SUMMARY.md` | PPT 素材 |
| `PPT_UPDATE_REPORT.md` | PPT 素材 |
| `Three-js方案.md` | Three.js 方案 |

### 5.2 `docs/system-update/` (12 个文件)

含多个过程性增量文档，合并后可整合：

- `0601.md`, `0604.md`, `0609相关update.md`, `0610.md` — 按日期的增量更新记录
- `功能增量.md`, `需求文档.md`, `演进方案.md` — 早期规划文档
- `dockerproxy_update.md`, `dual_repo_progress_20260606.md` — 模块更新记录

**建议:** 保留最终版本文档，归档过程性文档到 `docs/archive/`。

---

## 6. `.gitignore` 缺失项 已完成

当前 `.gitignore` 缺少以下项：

```gitignore
# 建议添加
.claude/settings.local.json
application/frontend/.env.development
application/frontend/.env.production
```

---

## 7. 汇总：合并前必须处理

| 优先级 | 问题 | 处理方式 |
|--------|------|----------|
| **P0** | 169MB docker-bin 二进制 | 移除，改用构建时下载 |
| **P0** | 硬编码 IP `114.212.82.168` (3处) | 替换为环境变量 |
| **P0** | 硬编码本机路径 (docker-compose.yml) | 仅保留 `${VAR}` 形式 |
| **P1** | `.claude/settings.local.json` 提交 | 加入 `.gitignore` |
| **P1** | CORS `allow_origins=["*"]` | 生产环境限制来源 |
| **P1** | RAG fallback token `token-local-dev` | 生产环境移除 |
| **P2** | 165 处 print / 63 处 console.log | 替换为 logging |
| **P2** | 8 处 `__main__` 内联测试 | 迁移到 tests/ |
| **P2** | dataset 52MB (含 C++ 源码) | 考虑 Git LFS |
| **P3** | 过程性文档冗余 (23+ 文件) | 归档或整合 |

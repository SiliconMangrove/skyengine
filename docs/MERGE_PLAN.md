# Merge Plan: 0610stable-update → main

> 生成时间：2026-06-16
> 目标：把 `0610stable-update` 合并到 `main`，合并后推送到 `origin/main`

---

## 1. 当前状态核对

| 项 | 值 |
|---|---|
| 当前分支 | `0610stable-update` |
| 远程 main HEAD | `3e4bd6b` (即 merge-base) |
| 领先 main 的提交数 | 17 |
| main 独有提交数 | **0**（main 未移动） |
| 合并类型 | **Fast-forward**（无冲突可能） |
| 文件变更总量 | 661 个（+394 新增 / −213 删除 / ~54 修改） |
| 净代码量 | +578,901 / −14,601 |

**未提交工作区改动（必须先处理）**：
- `application/frontend/src/components/AgentPanel.vue`（+200/−）
- `application/frontend/src/stores/monitor.js`（+60）
- `application/frontend/src/views/factory/DockerFactoryManage.vue`（+156/−）

改动内容：把 AgentPanel 的「单次分析结果」改成基于 monitor store 的「持久化对话历史」（`agentHistory` / `agentActiveTemplate` / `clearAgentHistory`），跨组件切换不丢。

---

## 2. 风险评估

### 风险 A — 未提交改动（必须先决策）
工作区有 3 个文件的改动。若不处理，`git checkout main` / `git merge` 可能被阻断或污染目标分支。
**应对**：合并前先在当前分支提交这些改动（见步骤 3.1）。

### 风险 B — 历史中的大体积二进制
提交历史里曾引入 `application/dockerfile/docker-bin/` 三个二进制：
- `docker-compose` ~73 MB
- `uv` ~59 MB
- `docker` ~43 MB

它们已被 `6483b3f` "stop tracking docker-bin binaries" 移除，**不在最终代码树中**，但 blob 仍在 git 历史里。fast-forward 后 main 会继承这段历史，clone 体积增大。
**影响**：功能性零影响；纯历史体积问题。本计划不处理（清理需 `git filter-repo` 改写历史，风险更高）。如需后续清理，单独开任务。

### 风险 C — 213 个删除文件
diff 显示删除了较多文件，包括 `executor/.../RouteSolver/gpt_solver/` 整个目录、`transformer_solver/` 等。
**应对**：本计划假设这些删除是有意的（分支作者主动清理）。合并前**强烈建议你自己 spot-check** 几个关键删除项是否符合预期。

### 风险 D — fast-forward vs 合并提交
默认 fast-forward 会让 main 直接跳到分支 HEAD，历史线性、无 merge commit。
若团队规范要求「保留合并节点」可改 `--no-ff`。**本计划默认 fast-forward**。

### 风险 E — 推送权限 / 保护分支
`origin/main` 是 `origin/HEAD`。若 GitHub 上 main 设了分支保护（禁止直接 push / 要求 PR），`git push origin main` 会被拒。
**应对**：步骤 3.4 推送前确认；若被保护，改走 PR 流程（步骤 3.5）。

### 风险 F — 旧分支残留
本地还有 `0506docker-update` / `0509frontend-update` / `0601system-update` 等旧分支。合并后它们仍指向旧提交。
**应对**：本计划不删本地旧分支，避免误删在用工作。需要清理时单独确认。

---

## 2.5 体积诊断补充（2026-06-16 实测）

| 项 | 大小 | 性质 | 处理 |
|---|---|---|---|
| `.git/lfs/objects/c9/ec/c9ec14ef...` | **1.78 GB** | 孤立 LFS 缓存（`dataset/mideavalwisard_Starjob/starjob_130k.json`，`dbc9fb2` 加入、`e32376d` 删除，HEAD 不再含） | Phase A 清理 |
| `.git/objects/23/tmp_obj_u2gz98` | 15 MB | LFS 写入临时残留 | Phase A 清理 |
| docker-bin 三 blob（loose） | ~70 MB 解压 / 17 MB pack | `2844dac` 加、`6483b3f` 删，历史可达；不在 main 历史中 | Phase B 清理 |
| HEAD 中 dataset yaml/json | ~30 MB | 当前在用 | 保留 |

**关键事实**：1.9 GB 体积中 **1.8 GB 是本地 LFS 缓存**，与 git 历史无关，一条 `git lfs prune` 即可。真正属于 git 历史、需改写才能清理的只有 docker-bin ~70 MB。现在 main 还没被污染，是改写历史的安全窗口。

---

## 3. 合并步骤（待你批准后我按此执行）

### Phase A — 本地安全清理（零风险、不改写历史）

#### A.1 清理 LFS 本地缓存（释放 ~1.8 GB）
```bash
git lfs prune --dry-run                  # 已确认：1 file(s) would be pruned (1.9 GB)
git lfs prune                            # 实际执行
du -sh .git/lfs/objects                  # 应显著缩小
```
**安全性**：只删本地 LFS 缓存里**当前 ref 不可达**的对象。HEAD 不含此文件 → 必清。远程 LFS 仍保留 oid，将来若 checkout 历史提交可重新拉取。

#### A.2 删除 LFS 写入残留临时对象（释放 15 MB）
```bash
ls -lh .git/objects/23/tmp_obj_u2gz98    # 确认存在
rm .git/objects/23/tmp_obj_u2gz98
```
**安全性**：`tmp_obj_*` 是 git-lfs 写入中断的残留，git 自身不会引用它。

#### A.3 验证
```bash
du -sh .git                              # 应从 1.9 G 降到约 110 MB
git fsck --full 2>&1 | tail -5           # 无 error / dangling 没关系
```

---

### Phase B — 改写历史移除 docker-bin（需 force-push）

> 仅在你要追求「main 历史也干净」时执行。若 A 之后 110 MB 可接受，可跳过 B 直接走 Phase C。

#### B.1 提交未提交改动（filter-repo 要求干净工作区）
```bash
git checkout 0610stable-update
git add application/frontend/src/components/AgentPanel.vue \
        application/frontend/src/stores/monitor.js \
        application/frontend/src/views/factory/DockerFactoryManage.vue
git commit -m "feat: agent panel uses persistent conversation history in monitor store"
```

#### B.2 备份
```bash
git bundle create /tmp/skyengine-backup-$(date +%Y%m%d-%H%M).bundle --all
```

#### B.3 安装并执行 filter-repo
```bash
uv tool install git-filter-repo
git filter-repo --path application/dockerfile/docker-bin/docker \
                --path application/dockerfile/docker-bin/docker-compose \
                --path application/dockerfile/docker-bin/uv \
                --invert-paths --force
git remote add origin https://github.com/dayu-autostreamer/skyengine.git   # filter-repo 会移除 origin
```

#### B.4 校验 + force-push
```bash
git rev-list --objects --all | git cat-file --batch-check='%(objectname) %(objecttype) %(objectsize) %(rest)' \
  | awk '$2=="blob" && $3>10000000 {print}'                              # 应无 docker-bin 输出
git ls-files | wc -l                                                     # 与改写前一致
git push --force origin 0610stable-update
```

**风险 H/I/J**（来自下方风险章节）：commit 哈希全部变化；force-push 后他人需 reset；filter-repo 会清掉 reflog 与 origin。

---

### Phase C — 合并到 main

#### C.1 预检
```bash
git fetch origin
git log --oneline origin/main..HEAD | wc -l                              # 重确认领先数
git rev-parse origin/main                                                # 记录旧 HEAD
git log --oneline origin/main..origin/0610stable-update                  # 应为空（main 无独有提交）
```

#### C.2 提交前端改动（若未在 B.1 完成）
```bash
git add application/frontend/src/components/AgentPanel.vue \
        application/frontend/src/stores/monitor.js \
        application/frontend/src/views/factory/DockerFactoryManage.vue
git commit -m "feat: agent panel uses persistent conversation history in monitor store"
git push origin 0610stable-update
```

#### C.3 切 main + fast-forward + 推送
```bash
git checkout main
git pull --ff-only origin main
git merge --ff-only 0610stable-update
git push origin main                                                     # 受保护则走 PR
```

#### C.4 验证
```bash
git log --oneline -5
git diff origin/main..main                                               # 应为空
```

---

### 3.0 预检（我执行）
```bash
git status                                       # 确认无新增改动
git fetch origin
git log --oneline origin/main..HEAD | wc -l     # 重新确认领先数
git rev-parse origin/main                        # 记录 main 当前 HEAD
```
**校验点**：`git log origin/main..origin/0610stable-update` 必须为空（main 没新提交）；若非空，说明 main 已移动，需重新评估为三方合并。

### 3.1 提交当前未提交改动（在 0610stable-update 上）
```bash
git add application/frontend/src/components/AgentPanel.vue \
        application/frontend/src/stores/monitor.js \
        application/frontend/src/views/factory/DockerFactoryManage.vue
git commit -m "feat: agent panel uses persistent conversation history in monitor store"
git push origin 0610stable-update
```
> 若你想先本地验证前端再提交，请在批准前说明，我会暂停。

### 3.2 切到 main 并更新
```bash
git checkout main
git pull --ff-only origin main
```
**校验点**：`git status` 干净；`git rev-parse HEAD` 等于 3.0 记录的 main HEAD。

### 3.3 Fast-forward 合并
```bash
git merge --ff-only 0610stable-update
```
**校验点**：合并成功且无 merge commit 生成；`git log --oneline -1` 应为 3.1 的新提交。

### 3.4 推送到远程 main
```bash
git push origin main
```
若被分支保护拒绝 → 走 3.5。

### 3.5（备选）PR 流程
若 main 受保护：
```bash
git push origin 0610stable-update
# 通过 GitHub 创建 PR：0610stable-update → main
gh pr create --base main --head 0610stable-update --title "merge 0610stable into main" --body "..."
```
合并后回到本地：`git checkout main && git pull --ff-only`。

### 3.6 合并后验证
```bash
git log --oneline -5                            # main 已到新 HEAD
git diff origin/main..main                      # 应为空（本地与远程一致）
```
可选冒烟：
```bash
uv run uvicorn application.backend.server:app --host 0.0.0.0 --port 8000 &   # 起服务
curl http://localhost:8000/health
```

---

## 4. 回滚方案

若 3.4 推送后发现严重问题：
```bash
# ⚠️ 需你明确授权才执行，会 force push 改写远程 main
git checkout main
git reset --hard <3.0 记录的 main 旧 HEAD>
git push --force origin main
```
**只在没人基于新 main 拉取代码时才安全。** 一般优先「向前修复」（新提交 revert/fix）而非回滚。

---

## 5. 需要你确认的决策点

1. **未提交的 3 个前端改动**：直接合并进 main（按 3.1 提交）？还是先你本地跑前端验证？
2. **合并方式**：fast-forward（默认）还是 `--no-ff` 留合并节点？
3. **推送方式**：直接 push（3.4）还是走 PR（3.5，若 main 受保护）？
4. **历史大文件**：本次不清理（接受历史体积），对吗？

确认后我按 3.0 → 3.6 执行。

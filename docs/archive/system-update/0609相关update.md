# 0609 数据源 & 生成配置问题诊断

## 问题一：生成的 config 存储位置 — 当前用 localStorage，未纯存 Pinia

### 现状

`loadConfigFromFile`（factory.js:290）保存 config 时同时写了两处：

1. **Pinia reactive state**：`factoryConfigs.value[config.id] = config`
2. **localStorage**：调用 `_persistConfigs()` → `writeStorage(STORAGE_KEYS.FACTORY_CONFIGS, ...)`

初始化时也从 localStorage 恢复：

```js
const factoryConfigs = ref(
  (() => {
    const saved = readStorage(STORAGE_KEYS.FACTORY_CONFIGS, {});
    return Object.fromEntries(
      Object.entries(saved).filter(([, v]) => validateConfig(v)),
    );
  })(),
);
```

### 问题

用户希望 config **只存 Pinia**，不存 localStorage/sessionStorage/cookie。

### 修改方案

1. **`loadConfigFromFile`**：移除 `_persistConfigs()` 调用，只写 Pinia ref
2. **`factoryConfigs` 初始化**：不再从 localStorage 读取，直接 `ref({})`
3. **`currentConfigId` 初始化**：不从 localStorage 读取，直接 `ref(null)`
4. **所有调用 `_persistConfigs()` 的地方全部移除**（约 10 处）
5. **`clearAll()` 中移除 localStorage 清理逻辑**
6. **`STORAGE_KEYS.FACTORY_CONFIGS` 和 `STORAGE_KEYS.CURRENT_CONFIG_ID` 可以删除**

涉及文件：`application/frontend/src/stores/factory.js`

### 受影响的函数清单

| 函数 | 行号 | 需移除的调用 |
|------|------|------------|
| `factoryConfigs` 初始化 | 249-257 | 整个 `readStorage` 包裹 |
| `currentConfigId` 初始化 | 259-261 | `readStorage` |
| `loadConfigFromFile` | 298-299 | `writeStorage` + `_persistConfigs` |
| `setCurrentConfig` | 308 | `writeStorage` |
| `deleteConfig` | 318-320 | `writeStorage` |
| `setCurrentTopologyConfig` | 366 | `writeStorage` + `_persistConfigs` |
| `addAssetFromTemplate` | 597 | `_persistConfigs` |
| `updateAssetPosition` | 635 | `_persistConfigs` |
| `renameAsset` | 658 | `_persistConfigs` |
| `removeAsset` | 679 | `_persistConfigs` |
| `clearAll` | 840-844 | 3 个 `localStorage.removeItem` |

---

## 问题二：重新生成时未清除旧 config

### 现状

`convert_to_grid_factory` 生成的 config `id` 固定为 `"grid_factory"`（helper.py:471）。

`loadConfigFromFile` 直接 `factoryConfigs.value[config.id] = config`，会覆盖同 id 的旧 config。

### 问题

覆盖本身没问题，但如果用户之前手动上传过其他 config（id 不同），旧 config 仍残留在 `factoryConfigs` 中。

### 修改方案

在 `generateConfig()` 中，调用 `store.loadConfigFromFile(config)` 之前先清空旧 configs：

```js
// ConfigPanel.vue - generateConfig()
// 清空旧配置，确保只保留最新生成的
store.factoryConfigs = {}
store.loadConfigFromFile(config)
```

或在 store 中新增一个 `replaceConfig(config)` 方法：

```js
// factory.js
function replaceConfig(config) {
  if (!validateConfig(config)) {
    throw new Error('无效配置')
  }
  factoryConfigs.value = { [config.id]: config }
  currentConfigId.value = config.id
}
```

---

## 问题三：机器/AGV/障碍物之间无碰撞检测

### 现状

`convert_to_grid_factory`（helper.py:384）中三个放置函数独立工作，**互不知晓对方已占据的格子**：

| 放置函数 | 决定位置 | 检查对象 |
|----------|---------|---------|
| `_place_on_grid` (机器) | 仅基于地图 `#` 障碍物 | 只排除 `#` 格 |
| `init_agvs` | 基于机器位置选枢纽 | 只排除 `#` 格和 AGV 互相间距 |
| `_map_to_obstacle_zones` | 直接从地图 `#` 转换 | 不涉及放置 |

**机器和 AGV 的放置都没有排除对方**，可能出现：
- 机器放在 AGV 初始位置上
- 多台机器占用同一格
- AGV 放在机器位置上

`_place_on_grid` 使用 spread 策略将地图分区域放置，实际碰撞概率不高但确实存在。`init_agvs` 有 AGV 互相之间的曼哈顿距离 >2 的约束，但**完全不排除机器位置**。

### 修改方案

在 `convert_to_grid_factory` 中，机器放置完成后，将机器占用格传入 AGV 放置函数，AGV 放置时同时排除 `#` 格和机器格：

```python
def init_agvs(
    map_str: str,
    machine_positions: list[tuple[int, int]],
    num_agvs: int = 4,
    seed: int = 42,
) -> list[tuple[int, int]]:
    ...
    # 现有逻辑只排除了 # 格，需新增排除机器占用格
    occupied = set(machine_positions)
    free = [p for p in _get_free_cells(map_str) if p not in occupied]
    ...
```

同时 `_place_on_grid` 也应保证机器之间不重叠（当前 spread 策略分区域放置大概率不重叠，但边界场景可能重叠）。需要加入已放置位置的排除逻辑。

### 涉及文件

`dataset/helper.py`：修改 `_place_on_grid` 和 `init_agvs` 函数

---

## 修改清单汇总

| # | 文件 | 变更 |
|---|------|------|
| 1 | `stores/factory.js` | 移除所有 localStorage 读写，config 纯存 Pinia ref |
| 2 | `components/ConfigPanel.vue` | `generateConfig()` 中重新生成时先清空旧 config |
| 3 | `dataset/helper.py` | `init_agvs` 排除机器占用格；`_place_on_grid` 防止机器重叠 |

<template>
  <main class="algorithm-platform-page">
    <header class="platform-header">
      <div class="header-copy">
        <p class="eyebrow">SKYENGINE / ALGORITHM LAB</p>
        <h1>算法实验与优化平台</h1>
        <p>统一管理算法训练、超参数探索、数据集测试、效果比较和过程回放。</p>
      </div>
      <div class="header-meta">
        <span class="protocol">协议 {{ catalog.protocol_version || '等待连接' }}</span>
      </div>
    </header>

    <nav class="workspace-tabs" aria-label="算法实验工作台">
      <button
        v-for="item in tabs"
        :key="item.id"
        :class="{ active: activeTab === item.id }"
        @click="activateTab(item.id)"
      >
        <span>{{ item.index }}</span>
        <b>{{ item.label }}</b>
        <small>{{ item.caption }}</small>
      </button>
    </nav>

    <section v-if="notice.text" class="notice" :class="notice.level" role="status">
      <strong>{{ notice.title }}</strong>
      <span>{{ notice.text }}</span>
      <button @click="clearNotice">×</button>
    </section>

    <section v-show="activeTab === 'configure'" class="configure-grid">
      <aside class="panel catalog-panel">
        <div class="panel-heading">
          <div><p class="eyebrow">REGISTRY</p><h2>能力目录</h2></div>
          <button class="icon-button" :disabled="loading.catalog" @click="loadCatalog">↻</button>
        </div>

        <template v-if="catalogAlgorithms.length || catalog.domains.length || catalog.runtimes.length">
          <div class="catalog-section">
            <h3>算法 <span>{{ catalogAlgorithms.length }}</span></h3>
            <button
              v-for="algorithm in catalogAlgorithms"
              :key="`${algorithm.algorithm_id}-${algorithm.version}`"
              class="catalog-card"
              :disabled="configPurpose === 'train' && !asList(algorithm.interfaces).includes('trainable')"
              :title="configPurpose === 'train' && !asList(algorithm.interfaces).includes('trainable') ? '该算法没有可训练接口，不能用于训练模式' : ''"
              @click="applyCatalogAlgorithm(algorithm)"
            >
              <span class="catalog-title">{{ algorithm.name || algorithm.algorithm_id }}</span>
              <code>{{ algorithm.algorithm_id }}@{{ algorithm.version }}</code>
              <span class="tag-row">
                <i v-for="entry in asList(algorithm.interfaces)" :key="entry">{{ interfaceLabel(entry) }}</i>
                <i>{{ parameterCount(algorithm) }} 个参数</i>
                <i v-if="minimumInputArtifacts(algorithm, 'online')">在线运行至少需要 {{ minimumInputArtifacts(algorithm, 'online') }} 个输入文件</i>
              </span>
            </button>
          </div>

          <div class="catalog-section">
            <h3>问题领域 <span>{{ catalog.domains.length }}</span></h3>
            <button
              v-for="domain in catalog.domains"
              :key="`${domain.domain_id}-${domain.version}`"
              class="catalog-card compact"
              @click="applyCatalogDomain(domain)"
            >
              <span class="catalog-title">{{ domain.name || domain.domain_id }}</span>
              <code>{{ domain.domain_id }}@{{ domain.version }}</code>
            </button>
          </div>

          <div class="catalog-section">
            <h3>运行时 <span>{{ catalog.runtimes.length }}</span></h3>
            <div v-for="runtime in catalog.runtimes" :key="`${runtime.runtime_id}-${runtime.version}`" class="runtime-row">
              <span>{{ runtime.name || runtime.runtime_id }}</span>
              <code>{{ asList(runtime.interfaces).join(' · ') }}</code>
            </div>
          </div>

          <div class="catalog-section">
            <h3>已存实验 <span>{{ experiments.length }}</span></h3>
            <button
              v-for="experiment in experiments"
              :key="experiment.experiment_id"
              class="catalog-card compact"
              @click="openStoredExperiment(experiment.experiment_id)"
            >
              <span class="catalog-title">{{ experiment.spec?.name || experiment.experiment_id }}</span>
              <code>{{ experiment.experiment_id }} · {{ experiment.execution_count || 0 }} 次执行</code>
            </button>
          </div>
        </template>
        <div v-else class="empty-state">
          <strong>{{ catalogEmptyTitle }}</strong>
          <p>{{ catalogEmptyText }}</p>
        </div>
      </aside>

      <section class="panel editor-panel">
        <div class="panel-heading editor-heading">
          <div><p class="eyebrow">EXPERIMENT CONFIGURATION</p><h2>实验配置</h2></div>
        </div>

        <div class="configuration-section-heading">
          <h3>实验模式</h3>
          <span>选择本次实验要完成的工作</span>
        </div>
        <section class="template-picker" aria-label="实验模式">
          <button data-description="训练可训练算法，优化模型内部参数并保存模型；纯启发式算法不支持该模式。" @click="loadTemplate('train')"><strong>训练</strong></button>
          <button data-description="搜索学习率、规则权重等算法外部参数，并选择表现最佳的参数组合。" @click="loadTemplate('tune')"><strong>超参数探索</strong></button>
          <button data-description="使用固定算法和固定参数在指定测试集上运行，保存指标、输出文件和回放记录。" @click="loadTemplate('test')"><strong>测试</strong></button>
        </section>

        <section class="dataset-settings">
          <div class="configuration-section-heading">
            <h3>数据集</h3>
            <span>数据集会写入实验定义并参与结果索引</span>
            <button class="text-button" @click="refreshDatasetReferences">更新数据集引用</button>
          </div>
          <label v-if="configPurpose === 'train'" class="dataset-choice">
            <span>训练数据集</span>
            <select :value="selectedConfigDataset('training')" @change="applyDatasetToConfig('training', $event.target.value)">
              <option value="" disabled>选择训练数据集</option>
              <option v-for="dataset in trainingDatasets" :key="dataset.dataset_id" :value="dataset.dataset_id">{{ dataset.name }} · {{ dataset.count }} 个实例</option>
            </select>
          </label>
          <template v-else-if="configPurpose === 'tune'">
            <label v-if="configTargetIsTrainable" class="dataset-choice">
              <span>模型训练数据集</span>
              <select :value="selectedConfigDataset('training')" @change="applyDatasetToConfig('training', $event.target.value)">
                <option value="" disabled>选择训练数据集</option>
                <option v-for="dataset in trainingDatasets" :key="dataset.dataset_id" :value="dataset.dataset_id">{{ dataset.name }} · {{ dataset.count }} 个实例</option>
              </select>
            </label>
            <label class="dataset-choice">
              <span>参数选择数据集</span>
              <select :value="selectedConfigDataset('tuning')" @change="applyDatasetToConfig('tuning', $event.target.value)">
                <option value="" disabled>选择调优数据集</option>
                <option v-for="dataset in tuningDatasets" :key="dataset.dataset_id" :value="dataset.dataset_id">{{ dataset.name }} · {{ dataset.count }} 个实例</option>
              </select>
            </label>
            <label class="dataset-choice">
              <span>最终测试数据集</span>
              <select :value="selectedConfigDataset('benchmark')" @change="applyDatasetToConfig('benchmark', $event.target.value)">
                <option value="" disabled>选择测试数据集</option>
                <option v-for="dataset in testDatasets" :key="dataset.dataset_id" :value="dataset.dataset_id">{{ dataset.name }} · {{ dataset.count }} 个实例</option>
              </select>
            </label>
          </template>
          <label v-else class="dataset-choice">
            <span>测试数据集</span>
            <select :value="selectedConfigDataset('test')" @change="applyDatasetToConfig('test', $event.target.value)">
              <option value="" disabled>选择测试数据集</option>
              <option v-for="dataset in testDatasets" :key="dataset.dataset_id" :value="dataset.dataset_id">{{ dataset.name }} · {{ dataset.count }} 个实例</option>
            </select>
          </label>
          <label v-if="configPurpose === 'train'" class="dataset-choice">
            <span>模型验证数据集</span>
            <select :value="selectedConfigDataset('validation')" @change="applyDatasetToConfig('validation', $event.target.value)">
              <option value="" disabled>选择验证数据集</option>
              <option v-for="dataset in tuningDatasets" :key="dataset.dataset_id" :value="dataset.dataset_id">{{ dataset.name }} · {{ dataset.count }} 个实例</option>
            </select>
          </label>
        </section>

        <section v-if="configPurpose === 'tune'" :key="`tuning-${configInputEpoch}`" class="tuning-settings">
          <div class="configuration-section-heading">
            <h3>超参数探索设置</h3>
            <span>优化方式负责提出下一组待评价参数</span>
          </div>
          <label class="optimizer-choice">
            <span>参数优化方式</span>
            <select :value="configOptimizerKey" @change="applyConfigOptimizer($event.target.value)">
              <option v-for="optimizer in catalogOptimizers" :key="`${optimizer.algorithm_id}@${optimizer.version}`" :value="`${optimizer.algorithm_id}@${optimizer.version}`">
                {{ optimizerLabel(optimizer.algorithm_id) }}
              </option>
            </select>
            <small>{{ optimizerDescription }}</small>
          </label>
          <div v-if="configOptimizerGroup?.fields.length" class="parameter-grid optimizer-parameter-grid">
            <label v-for="field in configOptimizerGroup.fields" :key="field.name">
              <span>{{ parameterLabel(field.name) }}</span>
              <small>{{ field.name }}</small>
              <select
                v-if="field.definition.enum"
                :value="field.value"
                @change="updateConfigParameter(configOptimizerGroup, field, $event.target.value)"
              >
                <option v-for="option in field.definition.enum" :key="String(option)" :value="option">{{ option }}</option>
              </select>
              <select
                v-else-if="field.definition.type === 'boolean'"
                :value="String(field.value)"
                @change="updateConfigParameter(configOptimizerGroup, field, $event.target.value)"
              >
                <option value="true">是</option>
                <option value="false">否</option>
              </select>
              <ParameterDraftInput
                v-else
                :type="field.definition.type === 'integer' || field.definition.type === 'number' ? 'number' : 'text'"
                :min="field.definition.minimum"
                :max="field.definition.maximum"
                :step="field.definition.type === 'integer' ? 1 : 'any'"
                :value="field.value"
                @input="markConfigDirty" @change="updateConfigParameter(configOptimizerGroup, field, $event.target.value)"
              />
            </label>
          </div>
          <div v-if="configSearchSpaceFields.length" class="search-space-section">
            <div class="subsection-heading"><h3>参数搜索范围</h3><span>{{ configSearchSpaceFields.length }} 个外部参数</span></div>
            <div class="search-space-grid">
              <article v-for="field in configSearchSpaceFields" :key="field.name">
                <header><strong>{{ parameterLabel(field.name) }}</strong><code>{{ field.name }}</code></header>
                <label v-if="field.dimension.kind === 'categorical'">候选值
                  <ParameterDraftInput :value="field.dimension.choices.join(', ')" @input="markConfigDirty" @change="updateSearchChoices(field.name, $event.target.value)" />
                </label>
                <template v-else>
                  <label>下限<ParameterDraftInput type="number" :value="field.dimension.low" :step="field.dimension.kind === 'integer' ? 1 : 'any'" @input="markConfigDirty" @change="updateSearchNumber(field.name, 'low', $event.target.value)" /></label>
                  <label>上限<ParameterDraftInput type="number" :value="field.dimension.high" :step="field.dimension.kind === 'integer' ? 1 : 'any'" @input="markConfigDirty" @change="updateSearchNumber(field.name, 'high', $event.target.value)" /></label>
                  <label v-if="field.dimension.step !== undefined">步长<ParameterDraftInput type="number" :value="field.dimension.step" step="any" @input="markConfigDirty" @change="updateSearchNumber(field.name, 'step', $event.target.value)" /></label>
                </template>
              </article>
            </div>
          </div>
        </section>

        <section :key="`parameters-${configInputEpoch}`" class="parameter-workbench">
          <div class="parameter-intro">
            <div><h3>算法参数</h3><p>参数修改会同步写入实验配置。</p></div>
            <span>{{ configParameterGroups.length }} 个参数组</span>
          </div>

          <article v-for="group in configParameterGroups" :key="group.key" class="parameter-group">
            <header>
              <div><strong>{{ group.name }}</strong><code>{{ group.algorithm_id }}@{{ group.version }}</code></div>
              <span>{{ group.role }}</span>
            </header>
            <div v-if="group.fields.length" class="parameter-grid">
              <label v-for="field in group.fields" :key="field.name" :class="{ wide: field.definition.type === 'array' || field.definition.type === 'object' }">
                <span>{{ parameterLabel(field.name) }}</span>
                <small>{{ field.name }}</small>
                <select
                  v-if="field.definition.enum"
                  :value="field.value"
                  @change="updateConfigParameter(group, field, $event.target.value)"
                >
                  <option v-for="option in field.definition.enum" :key="String(option)" :value="option">{{ option }}</option>
                </select>
                <select
                  v-else-if="field.definition.type === 'boolean'"
                  :value="String(field.value)"
                  :disabled="field.name === 'distributed'"
                  @change="updateConfigParameter(group, field, $event.target.value)"
                >
                  <option value="true">是</option>
                  <option value="false">否</option>
                </select>
                <ParameterDraftInput multiline
                  v-else-if="field.definition.type === 'array' || field.definition.type === 'object'"
                  :value="parameterInputValue(field.value)"
                  rows="2"
                  spellcheck="false"
                  @input="markConfigDirty" @change="updateConfigParameter(group, field, $event.target.value)"
                ></ParameterDraftInput>
                <ParameterDraftInput
                  v-else
                  :type="field.definition.type === 'integer' || field.definition.type === 'number' ? 'number' : 'text'"
                  :min="field.definition.minimum"
                  :max="field.definition.maximum"
                  :step="field.definition.type === 'integer' ? 1 : 'any'"
                  :value="field.value"
                  @input="markConfigDirty" @change="updateConfigParameter(group, field, $event.target.value)"
                />
                <p v-if="field.name === 'checkpoint_interval_steps'" class="parameter-hint">累计仿真步数；0 仅保留最佳</p>
              </label>
            </div>
            <div v-else class="parameter-empty">当前算法没有需要调整的配置参数。</div>
          </article>

          <div v-if="!configParameterGroups.length" class="empty-state inline parameter-empty-state">
            <strong>暂无可编辑的算法参数</strong>
            <p>请从左侧选择算法，或加载一份配置模板。</p>
          </div>
        </section>

        <details class="full-config-details">
          <summary>查看完整配置</summary>
          <div class="full-config-heading">
            <p>数据集、评价目标、搜索范围等高级内容可在此编辑。</p>
            <button class="text-button" @click="formatConfig">整理格式</button>
          </div>
          <textarea
            v-model="configText"
            class="config-editor"
            spellcheck="false"
            aria-label="完整实验配置"
            @input="markConfigDirty"
          ></textarea>
        </details>

        <div class="editor-actions">
          <button class="secondary-button" :disabled="busy" @click="validateConfig">校验</button>
          <button class="secondary-button" :disabled="busy" @click="compileConfig">编译计划</button>
          <button class="secondary-button" :disabled="busy" @click="saveExperiment">保存定义</button>
          <button v-if="configPurpose === 'tune'" class="primary-button tune" :disabled="busy" @click="submitExecution('tune')">
            开始超参数探索
          </button>
          <button v-else class="primary-button" :disabled="busy" @click="submitExecution('execute')">
            {{ executionActionLabel }}
          </button>
        </div>
        <p class="mode-hint">
          当前任务类型：<b>{{ purposeLabel(configPurpose) }}</b>。
          <span v-if="configPurpose === 'tune'">当前配置会尝试多组外部参数；如需优化模型内部参数，请选择“训练”。</span>
          <span v-else>点击“{{ executionActionLabel }}”提交当前配置。</span>
        </p>

        <div v-if="validation" class="result-box" :class="validation.valid ? 'success' : 'failure'">
          <div><strong>{{ validation.valid ? '配置有效' : '配置无效' }}</strong></div>
          <pre v-if="validationMessage">{{ validationMessage }}</pre>
        </div>
        <div v-if="compiled" class="result-box success">
          <div><strong>{{ compiled.dynamic ? '动态调优计划' : '执行计划已编译' }}</strong><span>{{ planSummary }}</span></div>
          <details><summary>查看编译结果</summary><pre>{{ pretty(compiled.plan || compiled.experiment || compiled) }}</pre></details>
        </div>
        <div v-if="storedExperiment" class="result-box success">
          <div><strong>实验定义已保存</strong><span>{{ storedExperiment.experiment_id }}</span></div>
        </div>
      </section>

      <aside class="panel guide-panel">
        <div class="panel-heading"><div><p class="eyebrow">CONFIGURATION GUIDE</p><h2>配置说明</h2></div></div>
        <article><span>01</span><div><h3>数据分区</h3><p>训练数据用于拟合模型，调优数据用于选择参数，基准数据只用于最终评价。</p></div></article>
        <article><span>02</span><div><h3>场景与重复</h3><p>每个场景使用固定数据和随机种子；重复次数用于减少偶然波动。</p></div></article>
        <article><span>03</span><div><h3>运行预算</h3><p>可以设置步数、迭代次数、评估次数和运行时间；计算设备由相应的算法参数控制。</p></div></article>
        <article><span>04</span><div><h3>评价目标</h3><p>支持单目标、加权、优先级排序、多目标权衡和约束。DFJSP-T 可以优先降低特急订单完成时间。</p></div></article>
        <article><span>05</span><div><h3>参数与调优范围</h3><p>算法参数用于直接运行；调优范围用于限定可选参数和取值范围。</p></div></article>
        <div class="partition-strip">
          <div><span>TRAIN</span><b>拟合产物</b></div><i>→</i><div><span>TUNE</span><b>选择候选</b></div><i>→</i><div><span>BENCHMARK</span><b>最终验证</b></div>
        </div>
      </aside>
    </section>

    <section v-show="activeTab === 'executions'" class="execution-grid">
      <aside class="panel execution-list-panel">
        <div class="panel-heading">
          <div><p class="eyebrow">EXECUTIONS</p><h2>执行记录</h2></div>
          <button class="icon-button" :disabled="loading.executions" @click="loadExecutions">↻</button>
        </div>
        <button
          v-for="execution in executions"
          :key="execution.execution_id"
          class="execution-row"
          :class="{ active: execution.execution_id === selectedExecutionId }"
          @click="selectExecution(execution.execution_id)"
        >
          <span class="status-dot" :class="statusClass(execution.status)"></span>
          <div><b>{{ execution.experiment_id }}</b><code>{{ execution.execution_id }}</code></div>
          <span><small>{{ purposeLabel(execution.purpose) }}</small><strong>{{ statusLabel(execution.status) }}</strong></span>
        </button>
        <div v-if="!executions.length" class="empty-state compact">
          <strong>{{ executionEmptyTitle }}</strong><p>{{ executionEmptyText }}</p>
        </div>
      </aside>

      <section class="panel execution-detail-panel">
        <template v-if="selectedExecution">
          <div class="panel-heading">
            <div><p class="eyebrow">{{ selectedExecution.execution_id }}</p><h2>{{ selectedExecution.experiment_id }}</h2></div>
            <div class="inline-controls">
              <button v-if="showCancelExecution" class="secondary-button" :disabled="loading.cancel || !canCancelExecution" @click="cancelExecution">
                {{ selectedExecution.status === 'cancel_requested' ? '正在取消' : '取消执行' }}
              </button>
              <span class="status-pill" :class="statusClass(selectedExecution.status)">{{ statusLabel(selectedExecution.status) }}</span>
            </div>
          </div>
          <div class="summary-cards">
            <div><span>用途</span><strong>{{ purposeLabel(selectedExecution.purpose) }}</strong></div>
            <div><span>开始时间</span><strong>{{ displayTime(selectedExecution.started_at) }}</strong></div>
            <div><span>运行单元</span><strong>{{ executionRuns.length }}</strong></div>
            <div><span>更新时间</span><strong>{{ displayTime(selectedExecution.updated_at) }}</strong></div>
          </div>
          <div v-if="selectedExecution.failure" class="failure-callout">
            <strong>{{ selectedExecution.failure.code || '执行失败' }}</strong>
            <p>{{ selectedExecution.failure.message || pretty(selectedExecution.failure) }}</p>
          </div>

          <section v-if="trainingRuns.length" class="training-monitor">
            <div class="subsection-heading">
              <h3>训练指标</h3>
              <select v-if="trainingRuns.length > 1" v-model="selectedTrainingRunKey" aria-label="训练运行单元">
                <option v-for="run in trainingRuns" :key="run.key" :value="run.key">{{ run.label }}</option>
              </select>
              <span>{{ trainingUpdates.length }} 次网络更新</span>
            </div>
            <p class="training-stage">{{ trainingStageMessage }}</p>
            <p v-if="samplingStageMessage" class="training-timing">采样：{{ samplingStageMessage }}</p>
            <p v-if="learnerStageMessage" class="training-timing">训练：{{ learnerStageMessage }}</p>
            <p v-if="evaluationStageMessage" class="training-timing">{{ evaluationStageMessage }}</p>
            <div class="summary-cards">
              <div v-for="metric in trainingMetricDefinitions" :key="metric.key">
                <span>{{ metric.label }}</span><strong>{{ formatMetricValue(latestTrainingUpdate?.trainer?.[metric.key]) }}</strong>
              </div>
            </div>
            <p v-if="latestTrainingUpdate" class="training-timing">
              累计 {{ latestTrainingUpdate.total_steps }} 仿真步 · 本批 {{ latestTrainingUpdate.trainer.transitions }} 个决策样本 ·
              该训练批原采样 {{ formatMetricValue(latestTrainingUpdate.batch_collection_seconds) }} 秒 ·
              网络更新 {{ formatMetricValue(latestTrainingUpdate.batch_update_seconds) }} 秒
            </p>
            <template v-if="trainingBarriers.length">
              <h4>同步等待记录</h4>
              <p class="training-timing">同一行对应同时运行的采样和训练。采样等待训练表示 CPU 先完成；训练等待采样表示 GPU 先完成。验证独立运行。</p>
              <div class="metric-table-wrap">
                <table class="data-table">
                  <thead><tr><th>同步轮</th><th>采样回合 / 策略</th><th>保留 / 丢弃回合</th><th>训练回合 / 采样策略</th><th>采样完成</th><th>训练完成</th><th>采样耗时</th><th>训练耗时</th><th>采样等待训练</th><th>训练等待采样</th></tr></thead>
                  <tbody>
                    <tr v-for="barrier in trainingBarriers" :key="`${barrier.stage}-${barrier.barrier_round}`">
                      <td>{{ barrier.barrier_round }}{{ barrier.stage === 'warmup' ? '（首批）' : barrier.stage === 'drain' ? '（收尾）' : '' }}</td>
                      <td>{{ barrier.sampling_end_episode == null ? '—' : `${barrier.sampling_start_episode}—${barrier.sampling_end_episode} / V${barrier.sampling_policy_version}` }}</td>
                      <td v-if="barrier.sampling_statistics">
                        {{ barrier.sampling_statistics.completed_jobs }} / {{ barrier.sampling_statistics.discarded_jobs }}
                        <small>丢弃 {{ barrier.sampling_statistics.discarded_steps }} 步</small>
                      </td>
                      <td v-else>—</td>
                      <td>{{ barrier.training_end_episode == null ? '—' : `${barrier.training_start_episode}—${barrier.training_end_episode} / V${barrier.training_behavior_policy_version}` }}</td>
                      <td>{{ displayPreciseTime(barrier.sampling_completed_at) }}</td>
                      <td>{{ displayPreciseTime(barrier.training_completed_at) }}</td>
                      <td>{{ formatMetricValue(barrier.sampling_seconds) }} 秒</td>
                      <td>{{ formatMetricValue(barrier.training_seconds) }} 秒</td>
                      <td>{{ formatMetricValue(barrier.sampling_wait_seconds) }} 秒</td>
                      <td>{{ formatMetricValue(barrier.training_wait_seconds) }} 秒</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </template>
            <div v-if="trainingUpdates.length" class="training-charts">
              <article v-for="metric in trainingMetricDefinitions" :key="metric.key">
                <h4>{{ metric.label }}</h4>
                <LineChart
                  :series="[{ name: metric.label, data: trainingUpdates.map(update => ({ x: update.total_steps, y: update.trainer[metric.key] })) }]"
                  x-label="累计仿真步" :show-data-zoom="false" height="200px"
                />
              </article>
            </div>
            <p v-else class="training-timing">首批网络更新完成后显示 Loss、策略损失、价值损失和熵。历史记录未保存这些指标时显示为空。</p>
          </section>

          <div class="subsection-heading"><h3>训练 Checkpoint</h3><span>{{ executionCheckpoints.length }} 个</span></div>
          <div v-if="executionCheckpoints.length" class="metric-table-wrap">
            <table class="data-table">
              <thead><tr><th>名称</th><th>累计步数</th><th>完成轮数</th><th>保存时间</th><th>操作</th></tr></thead>
              <tbody>
                <tr v-for="checkpoint in executionCheckpoints" :key="`${checkpoint.workspace_execution_id}/${checkpoint.run_id}/${checkpoint.name}`">
                  <td>{{ checkpoint.name === 'best' ? '最佳 checkpoint' : checkpoint.name }}</td>
                  <td>{{ checkpoint.total_steps }}</td>
                  <td>{{ checkpoint.completed_episodes }}</td>
                  <td>{{ displayTime(checkpoint.created_at) }}</td>
                  <td>
                    <a class="text-button" :href="checkpointDownloadUrl(checkpoint)" download>下载</a>
                    <button class="text-button" @click="useCheckpoint(checkpoint, 'train')">继续训练</button>
                    <button class="text-button" @click="useCheckpoint(checkpoint, 'test')">用于测试</button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-else class="empty-state inline"><p>Checkpoint 保存后会在这里显示，无需等待训练结束。</p></div>

          <div class="subsection-heading"><h3>输出文件</h3><span>{{ executionArtifacts.length }} 个</span></div>
          <div v-if="executionArtifacts.length" class="metric-table-wrap">
            <table class="data-table">
              <thead><tr><th>类型</th><th>摘要</th><th>所属运行单元</th><th>大小</th><th>操作</th></tr></thead>
              <tbody>
                <tr v-for="artifact in executionArtifacts" :key="`${artifact.digest}-${artifact.run_id || artifact.source}`">
                  <td>{{ artifact.kind }}</td>
                  <td><code>{{ shortId(artifact.digest) }}</code></td>
                  <td><code>{{ artifact.run_id || '执行级' }}</code></td>
                  <td>{{ formatBytes(artifact.size_bytes) }}</td>
                  <td>
                    <a class="text-button" :href="artifactDownloadUrl(artifact)" download>下载</a>
                    <button class="text-button" @click="copyArtifactRef(artifact)">复制引用</button>
                    <button v-if="testCompatibleAlgorithm(artifact)" class="text-button" @click="fillArtifactIntoTest(artifact)">用于测试</button>
                    <button v-if="branchCompatibleAlgorithm(artifact)" class="text-button" @click="fillArtifactIntoBranch(artifact)">填入分支</button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-else class="empty-state inline"><strong>尚无输出文件</strong><p>运行结束后，模型、检查点、调度方案、事件和报告会在这里统一列出。</p></div>

          <details v-if="executionManifest" class="raw-report">
            <summary>查看输出文件清单 · {{ executionManifest.manifest_id || selectedExecution.result_manifest_id }}</summary>
            <pre>{{ pretty(executionManifest) }}</pre>
          </details>

          <div class="subsection-heading"><h3>运行指标</h3><span>{{ metricItems.length }} 条</span></div>
          <div v-if="metricItems.length" class="metric-table-wrap">
            <table class="data-table">
              <thead><tr><th>运行单元</th><th>状态</th><th>目标值</th><th>评价指标</th><th>可行</th></tr></thead>
              <tbody>
                <tr v-for="item in metricItems" :key="item.run_id || item.trial_id">
                  <td><code>{{ item.run_id || item.trial_id || '—' }}</code></td>
                  <td>{{ statusLabel(item.status) }}</td>
                  <td>{{ formatVector(item.objective_values) }}</td>
                  <td><span class="metric-line">{{ metricSummary(item.metrics) }}</span></td>
                  <td>{{ item.feasible === false ? '否' : item.feasible === true ? '是' : '—' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-else class="empty-state inline"><strong>暂无指标</strong><p>执行尚未产生运行指标，完成后会自动出现在这里。</p></div>

          <div v-if="candidateItems.length || benchmarkItems.length" class="phase-results">
            <article><span>候选评价</span><strong>{{ candidateItems.length }}</strong><p>{{ candidateBestText }}</p></article>
            <article><span>基准测试</span><strong>{{ benchmarkItems.length }}</strong><p>基准测试结果不反向参与参数选择。</p></article>
          </div>

          <div class="subsection-heading"><h3>标准事件与日志</h3><span>{{ executionLogs.length }} 条</span></div>
          <div v-if="executionLogs.length" class="event-log">
            <div v-for="(event, index) in executionLogs" :key="`${event.sequence ?? index}-${event.run_id ?? ''}`">
              <span>{{ event.sequence ?? index }}</span>
              <time>{{ displayTime(event.wall_time || event.created_at) }}</time>
              <b>{{ eventTypeLabel(event.event_type || event.type || event.level) }}</b>
              <code>{{ eventMessage(event) }}</code>
            </div>
          </div>
          <div v-else class="empty-state inline"><strong>暂无事件日志</strong><p>该执行记录尚未产生可显示的事件。</p></div>
        </template>
        <div v-else class="empty-state large"><strong>选择一条执行记录</strong><p>这里会展示执行状态、运行指标、候选评价和事件日志。</p></div>
      </section>
    </section>

    <section v-show="activeTab === 'comparison'" class="comparison-layout">
      <section class="panel comparison-panel">
        <div class="panel-heading">
          <div><p class="eyebrow">FAIR COMPARISON</p><h2>统一比较报告</h2></div>
          <div class="inline-controls">
            <select v-model="comparisonDatasetId">
              <option value="">选择测试数据集</option>
              <option v-for="dataset in testDatasets" :key="dataset.dataset_id" :value="dataset.dataset_id">{{ dataset.name }}</option>
            </select>
            <button class="secondary-button" :disabled="!comparisonDatasetId || loading.comparison" @click="loadComparison">读取测试结果</button>
          </div>
        </div>

        <template v-if="datasetComparisonPayload">
          <div class="report-banner">
            <div><span>数据集</span><b>{{ datasetComparisonPayload.dataset?.name }}</b></div>
            <div><span>实例总数</span><b>{{ datasetComparisonPayload.dataset?.count || 0 }}</b></div>
            <div><span>已有测试记录</span><b>{{ datasetComparisonPayload.count || 0 }}</b></div>
            <div><span>参与算法</span><b>{{ datasetComparisonRows.length }}</b></div>
          </div>
          <div v-if="datasetComparisonRows.length" class="metric-table-wrap">
            <table class="data-table">
              <thead><tr><th>排名</th><th>算法</th><th>已测试实例</th><th>成功运行</th><th>平均 C_max_E</th><th>平均 C_max</th><th>最近测试</th></tr></thead>
              <tbody>
                <tr v-for="(row, index) in datasetComparisonRows" :key="row.key">
                  <td>{{ index + 1 }}</td>
                  <td><b>{{ row.algorithm_name }}</b><br><code>{{ row.algorithm_id }}@{{ row.algorithm_version }}</code><br><small>{{ concise(row.algorithm_parameters) }}</small></td>
                  <td>{{ row.instance_count }} / {{ datasetComparisonPayload.dataset?.count || 0 }}</td>
                  <td>{{ row.succeeded }} / {{ row.total }}</td>
                  <td>{{ formatMetricValue(row.average_urgent_makespan) }}</td>
                  <td>{{ formatMetricValue(row.average_makespan) }}</td>
                  <td>{{ displayTime(row.latest_finished_at) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-else class="empty-state inline"><strong>该数据集还没有测试结果</strong><p>先在“测试”模式中选择此数据集并运行算法，完成后结果会自动出现在这里。</p></div>
          <details class="raw-report"><summary>查看完整测试记录</summary><pre>{{ pretty(datasetComparisonPayload) }}</pre></details>
        </template>
        <div v-else class="empty-state large"><strong>{{ comparisonEmptyTitle }}</strong><p>{{ comparisonEmptyText }}</p></div>
      </section>
    </section>

    <section v-show="activeTab === 'replay'" class="replay-layout">
      <section class="panel replay-control-panel">
        <div class="panel-heading"><div><p class="eyebrow">RUN EVIDENCE</p><h2>记录回放</h2></div></div>
        <label>数据集
          <select v-model="replayDatasetId" @change="loadReplayDataset()">
            <option value="">选择测试数据集</option>
            <option v-for="dataset in testDatasets" :key="dataset.dataset_id" :value="dataset.dataset_id">{{ dataset.name }}</option>
          </select>
        </label>
        <label>数据实例
          <select v-model="replayInstanceId" @change="selectFirstReplayAlgorithm()" :disabled="!replayDatasetId">
            <option value="">选择实例</option>
            <option v-for="instance in replayAvailableInstances" :key="instance.instance_id" :value="instance.instance_id">{{ instance.instance_id }}</option>
          </select>
        </label>
        <p v-if="replayInstanceId && !replayDatasetAlgorithms.length" class="control-help">该实例还没有已完成的算法测试记录。</p>
        <label>测试算法
          <select v-model="replayDatasetRunKey" @change="selectReplayDatasetRun()" :disabled="!replayInstanceId">
            <option value="">选择算法</option>
            <option v-for="run in replayDatasetAlgorithms" :key="run.key" :value="run.key">{{ run.algorithm_name }} · {{ concise(run.algorithm_parameters) }} · {{ shortId(run.run_id) }}</option>
          </select>
        </label>
        <div class="divider-line"></div>
        <p class="control-help">也可以直接从历史执行记录中选择运行单元。</p>
        <label>执行记录
          <select v-model="replayExecutionId" @change="loadReplayRuns()">
            <option value="">选择执行记录</option>
            <option v-for="item in executions" :key="item.execution_id" :value="item.execution_id">{{ item.experiment_id }} · {{ shortId(item.execution_id) }}</option>
          </select>
        </label>
        <label>运行单元
          <select v-model="replayRunId">
            <option value="">选择运行单元</option>
            <option v-for="run in replayRuns" :key="run.run_id" :value="run.run_id">{{ run.run_id }} · {{ statusLabel(run.status) }}</option>
          </select>
        </label>
        <div class="segmented">
          <button :class="{ active: replayMode === 'full' }" @click="replayMode = 'full'">完整事件</button>
          <button :class="{ active: replayMode === 'decision' }" @click="replayMode = 'decision'">决策视图</button>
        </div>
        <button class="primary-button" :disabled="!replayRunId || loading.replay" @click="loadReplay">加载回放</button>

        <div class="divider-line"></div>
        <h3>确定性复现</h3>
        <p class="control-help">使用相同的配置、版本、随机种子和外部事件重新运行，并逐项核对运行状态。</p>
        <button class="secondary-button full" :disabled="!replayResponse || loading.reproduce" @click="reproduceReplay">触发确定性复现</button>

        <div class="divider-line"></div>
        <h3>快照分支</h3>
        <p class="control-help">从正式快照恢复状态，切换另一个在线算法继续运行。</p>
        <label>快照锚点
          <select v-model.number="branchForm.snapshot_sequence" :disabled="!replaySnapshots.length">
            <option v-if="!replaySnapshots.length" :value="null">当前回放没有快照</option>
            <option v-for="snapshot in replaySnapshots" :key="snapshot.after_sequence" :value="snapshot.after_sequence">
              序列 {{ snapshot.after_sequence }} · 仿真时间 {{ snapshot.snapshot?.simulation_time ?? '—' }} · {{ shortId(snapshot.snapshot?.state_hash) }}
            </option>
          </select>
        </label>
        <label>目标在线算法
          <select v-model="branchForm.algorithm_key" @change="applyBranchAlgorithmDefaults(true)">
            <option value="">选择已注册算法</option>
            <option v-for="algorithm in onlineAlgorithms" :key="`${algorithm.algorithm_id}@${algorithm.version}`" :value="`${algorithm.algorithm_id}@${algorithm.version}`">
              {{ algorithm.name || algorithm.algorithm_id }} · {{ algorithm.version }}
            </option>
          </select>
        </label>
        <section class="branch-parameter-editor">
          <h4>算法参数</h4>
          <div v-if="branchParameterFields.length" :key="branchInputEpoch" class="parameter-grid">
            <label v-for="field in branchParameterFields" :key="field.name" :class="{ wide: field.definition.type === 'array' || field.definition.type === 'object' }">
              <span>{{ parameterLabel(field.name) }}</span>
              <small>{{ field.name }}</small>
              <select
                v-if="field.definition.enum"
                :value="field.value"
                @change="updateBranchParameter(field, $event.target.value)"
              >
                <option v-for="option in field.definition.enum" :key="String(option)" :value="option">{{ option }}</option>
              </select>
              <select
                v-else-if="field.definition.type === 'boolean'"
                :value="String(field.value)"
                :disabled="field.name === 'distributed'"
                @change="updateBranchParameter(field, $event.target.value)"
              >
                <option value="true">是</option>
                <option value="false">否</option>
              </select>
              <ParameterDraftInput multiline
                v-else-if="field.definition.type === 'array' || field.definition.type === 'object'"
                :value="parameterInputValue(field.value)"
                rows="2"
                spellcheck="false"
                @change="updateBranchParameter(field, $event.target.value)"
              ></ParameterDraftInput>
              <ParameterDraftInput
                v-else
                :type="field.definition.type === 'integer' || field.definition.type === 'number' ? 'number' : 'text'"
                :min="field.definition.minimum"
                :max="field.definition.maximum"
                :step="field.definition.type === 'integer' ? 1 : 'any'"
                :value="field.value"
                @change="updateBranchParameter(field, $event.target.value)"
              />
            </label>
          </div>
          <p v-else class="control-help">当前算法没有需要调整的参数。</p>
          <details class="branch-full-params">
            <summary>查看完整参数</summary>
            <textarea v-model="branchForm.parameters" rows="7" spellcheck="false"></textarea>
          </details>
        </section>
        <label>输入文件引用
          <textarea v-model="branchForm.input_artifacts" rows="6" spellcheck="false"></textarea>
          <small v-if="asList(selectedBranchAlgorithm?.input_artifact_kinds).length">
            接受 {{ asList(selectedBranchAlgorithm.input_artifact_kinds).join(' / ') }}；在线接口至少需要 {{ minimumInputArtifacts(selectedBranchAlgorithm, 'online') }} 个输入文件。
          </small>
          <small v-else-if="minimumInputArtifacts(selectedBranchAlgorithm, 'online')">在线接口至少需要 {{ minimumInputArtifacts(selectedBranchAlgorithm, 'online') }} 个输入文件。</small>
          <small v-else>当前算法可以直接在线运行，无需选择模型或检查点。</small>
        </label>
        <label>新执行 ID<input v-model="branchForm.execution_id" /></label>
        <label>新运行单元 ID<input v-model="branchForm.run_id" /></label>
        <button class="secondary-button full" :disabled="!canBranch || loading.branch" @click="branchReplay">创建分支回放</button>

        <div class="divider-line"></div>
        <h3>双运行差异诊断</h3>
        <label>左侧运行单元<select v-model="diffForm.left_key"><option value="">选择运行单元</option><option v-for="run in allKnownRuns" :key="`left-${run.key}`" :value="run.key">{{ shortId(run.execution_id) }} · {{ run.run_id }}</option></select></label>
        <label>右侧运行单元<select v-model="diffForm.right_key"><option value="">选择运行单元</option><option v-for="run in allKnownRuns" :key="`right-${run.key}`" :value="run.key">{{ shortId(run.execution_id) }} · {{ run.run_id }}</option></select></label>
        <label>浮点容差<input v-model.number="diffForm.float_tolerance" type="number" min="0" step="0.000001" /></label>
        <button class="secondary-button full" :disabled="!canDiff || loading.diff" @click="loadReplayDiff">比较两个运行单元</button>
      </section>

      <section class="panel replay-view-panel">
        <section v-if="reproductionReport" class="replay-operation-result" :class="{ equivalent: reproductionEquivalent && !reproductionRunFailed }">
          <div>
            <p class="eyebrow">确定性复现</p>
            <h3>{{ reproductionRunFailed ? '确定性复现运行失败' : reproductionEquivalent ? '确定性复现一致' : '复现出现分歧' }}</h3>
            <p v-if="reproductionRunFailed">{{ reproductionReport.result?.failure?.message || statusLabel(reproductionReport.result?.status) }}</p>
            <p v-else-if="reproductionEquivalent">新执行与原运行记录的事件、状态摘要和负载一致。</p>
            <p v-else>首个差异：{{ reproductionFirstDifference?.kind || '未知' }} · {{ reproductionFirstDifference?.field_path || '(记录项)' }}</p>
          </div>
          <span>{{ reproductionReport.execution_id }}</span>
        </section>

        <section v-if="branchReport" class="replay-operation-result" :class="{ 'branch-result': !branchRunFailed }">
          <div>
            <p class="eyebrow">分支回放</p>
            <h3>{{ branchRunFailed ? '分支运行失败' : '分支回放已生成' }}</h3>
            <p v-if="branchRunFailed">{{ branchReport.result?.failure?.message || statusLabel(branchReport.result?.status) }}</p>
            <p v-else>{{ branchReport.trace?.run_id }} · 从序列 {{ branchReport.trace?.branch_after_sequence }} 继续</p>
          </div>
          <button class="primary-button" @click="openBranchTrace">打开分支回放</button>
        </section>

        <template v-if="replayResponse">
          <div class="panel-heading">
            <div><p class="eyebrow">{{ replayViewLabel }}</p><h2>{{ activeTraceRunId }}</h2></div>
            <span>{{ replayItems.length }} 个记录项 · {{ replayFrames.length }} 个正式画面</span>
          </div>

          <div class="replay-diagnostics-strip">
            <div><span>事件</span><b>{{ replayResponse.diagnostics?.event_count ?? replayItems.length }}</b></div>
            <div><span>决策</span><b>{{ replayResponse.diagnostics?.decision_count ?? '—' }}</b></div>
            <div><span>快照</span><b>{{ replayResponse.diagnostics?.snapshot_count ?? replaySnapshots.length }}</b></div>
            <div><span>记录证据</span><b>{{ replayResponse.diagnostics?.record_replay_ready ? '齐全' : '不完整' }}</b></div>
            <div><span>复现证据</span><b>{{ replayResponse.diagnostics?.deterministic_replay_ready ? '齐全' : '不完整' }}</b></div>
            <div><span>分支证据</span><b>{{ replayResponse.diagnostics?.branch_replay_ready ? '齐全' : '不完整' }}</b></div>
          </div>
          <details v-if="asList(replayResponse.diagnostics?.issues).length" class="diagnostic-issues">
            <summary>{{ asList(replayResponse.diagnostics.issues).length }} 条回放诊断问题</summary>
            <p v-for="(issue, index) in asList(replayResponse.diagnostics.issues)" :key="`${issue.code}-${index}`">
              <b>{{ issue.severity }} · {{ issue.code }}</b><span>{{ issue.message }}</span>
            </p>
          </details>

          <div v-if="currentFormalFrame" class="formal-frame">
            <FactoryPlayerSSE
              :hide-control-panel="true"
              :hide-scenario-controls="true"
              :hide-focus-panel="true"
              background-theme="factory"
              :background-size="2"
            />
            <div class="frame-controls">
              <button class="icon-button" :disabled="replayFrameIndex <= 0" @click="seekFrame(replayFrameIndex - 1)">←</button>
              <button class="secondary-button" @click="toggleReplayPlayback">{{ replayPlaying ? '暂停' : '播放' }}</button>
              <button class="icon-button" :disabled="replayFrameIndex >= replayFrames.length - 1" @click="seekFrame(replayFrameIndex + 1)">→</button>
              <span>画面 {{ replayFrameIndex + 1 }} / {{ replayFrames.length }}</span>
              <input v-model.number="replayFrameIndex" type="range" min="0" :max="Math.max(0, replayFrames.length - 1)" />
            </div>
          </div>
          <div v-else class="empty-state inline"><strong>本次回放没有工厂画面</strong><p>仍可查看完整事件和决策记录；存在画面数据时会自动启用工厂视图。</p></div>

          <div class="replay-evidence-grid">
            <div>
              <div class="subsection-heading"><h3>{{ activeReplayView === 'decision' ? '决策证据' : '事件流' }}</h3><span>只追加记录</span></div>
              <div v-if="replayItems.length" class="replay-items">
                <button v-for="(item, index) in replayItems" :key="replayItemKey(item, index)" :class="{ active: replayItemIndex === index }" @click="replayItemIndex = index">
                  <span>{{ item.ordinal ?? item.sequence ?? item.event?.sequence ?? index }}</span>
                  <div><b>{{ replayItemType(item) }}</b><code>{{ replayItemCaption(item) }}</code></div>
                </button>
              </div>
          <div v-else class="empty-state inline"><strong>暂无回放记录</strong><p>当前运行单元的事件日志没有可播放内容。</p></div>
            </div>
            <div>
              <div class="subsection-heading"><h3>当前证据</h3><span>观测 · 动作 · 校验 · 状态摘要</span></div>
              <pre class="evidence-json">{{ pretty(currentReplayItem || replayResponse.diagnostics || {}) }}</pre>
            </div>
          </div>
        </template>
        <div v-else class="empty-state large"><strong>{{ replayEmptyTitle }}</strong><p>{{ replayEmptyText }}</p></div>

        <section v-if="diffReport" class="diff-report">
          <div class="subsection-heading"><h3>差异诊断</h3><span>{{ diffEquivalent ? '两个运行单元等价' : `${asList(diffReport.differences).length} 处差异` }}</span></div>
          <div class="diff-summary">
            <div><span>匹配前缀</span><b>{{ diffReport.matched_prefix_count ?? '—' }}</b></div>
            <div><span>已比较项</span><b>{{ diffReport.compared_item_count ?? '—' }}</b></div>
            <div><span>左 / 右项</span><b>{{ diffReport.left_item_count ?? '—' }} / {{ diffReport.right_item_count ?? '—' }}</b></div>
            <div><span>截断</span><b>{{ diffReport.truncated ? '是' : '否' }}</b></div>
          </div>
          <div v-if="asList(diffReport.differences).length" class="difference-list">
            <article v-for="(difference, index) in asList(diffReport.differences)" :key="`${difference.item_ordinal}-${difference.field_path}-${index}`">
              <span>{{ difference.kind }}</span><b>{{ difference.field_path || '(记录项)' }}</b><code>左侧 {{ concise(difference.left_value) }} → 右侧 {{ concise(difference.right_value) }}</code>
            </article>
          </div>
        </section>
      </section>
    </section>
  </main>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import FactoryPlayerSSE from '@/components/FactoryPlayerSSE.vue'
import LineChart from '@/components/charts/LineChart.vue'
import ParameterDraftInput from '@/components/ParameterDraftInput.vue'
import { useFactoryStore } from '@/stores/factory'
import { API_ROUTES, apiGet, apiPost, getApiUrl } from '@/utils/api'
import './styles/AlgorithmPlatformView.css'

const factoryStore = useFactoryStore()

const tabs = [
  { id: 'configure', index: '01', label: '定义实验', caption: '目录 · 配置 · 编译' },
  { id: 'executions', index: '02', label: '执行监控', caption: '运行 · 指标 · 日志' },
  { id: 'comparison', index: '03', label: '统一比较', caption: '数据集 · 测试结果' },
  { id: 'replay', index: '04', label: '回放诊断', caption: '实例 · 算法 · 事件' },
]

const parameterLabels = {
  episodes: '训练轮数',
  num_envs: '并行采样环境数',
  dynamic_sampling: '动态领取采样任务（同步时丢弃未完成任务）',
  evaluation_num_envs: '并行评估环境数',
  checkpoint_interval_steps: 'Checkpoint 间隔',
  max_steps: '最大步数',
  evaluation_episodes: '评估轮数',
  evaluation_max_steps: '评估最大步数',
  validation_interval: '验证间隔',
  device: '计算设备',
  gpu_ids: 'GPU 卡号',
  distributed: '分布式训练（预留）',
  hidden_dim: '隐藏层维度',
  route_actions: '路径动作数',
  max_production_actions: '最大生产动作数',
  max_logistics_actions: '最大物流动作数',
  learning_rate: '学习率',
  gamma: '折扣因子',
  gae_lambda: 'GAE 系数',
  clip_ratio: '策略裁剪比例',
  ppo_epochs: 'PPO 更新轮数',
  value_coef: '价值损失权重',
  entropy_coef: '熵权重',
  minibatch_size: '小批量大小',
  horizon: '时间归一化上限',
  shaping_scale: '奖励塑形权重',
  processing_time_weight: '加工时间权重',
  due_date_weight: '交付期权重',
  remaining_work_weight: '剩余工作量权重',
  priority_weight: '优先级权重',
  population_size: '种群规模',
  generations: '迭代代数',
  horizon_operations: '滚动窗口工序数',
  lookahead_per_job: '单作业前瞻工序数',
  local_search_steps: '局部搜索步数',
  replan_interval: '重规划间隔（仿真步）',
  dispatch_batch_size: '每次派工数',
  crossover_rate: '交叉率',
  mutation_rate: '变异率',
  tournament_size: '锦标赛规模',
  elite_size: '精英数量',
  elite_fraction: '精英比例',
  time_limit_seconds: '求解时限（秒）',
  num_workers: '并行工作数',
  log_search_progress: '记录搜索进度',
  points_per_dimension: '每维取值数',
}

const ppoSelectionObjective = {
  mode: 'single',
  components: [{ name: 'episode_reward', metric: 'episode_reward', direction: 'maximize', aggregation: 'mean' }],
  constraints: [],
  feasibility_first: true,
}

const tuneTemplate = {
  schema_version: 1,
  experiment_id: 'dfjspt_ppo_tuning_v1',
  name: 'DFJSP-T CTDE-PPO 三分区调优',
  purpose: 'tune',
  base_seed: 20260920,
  repetitions: 3,
  domain: {
    id: 'dfjsp_t',
    version: '1.0.0',
    parameters: {
      scenario_root: '.',
      route_solver: 'astar',
      assigner: 'nearest',
      validation_scenarios: [{
        scenario_id: 'validation_corpus_v1',
        uri: 'dataset/dfjsp_t_validation/validation.jsonl',
        digest: '',
        metadata: {
          corpus: true,
          split: 'validation',
          instance_ids: [
            'dfjspt-validation-validation-0042060914-00000',
            'dfjspt-validation-validation-0042061924-00001',
          ],
        },
      }],
    },
  },
  algorithms: [{
    id: 'ctde_ppo',
    version: '0.2.0',
    interface: 'trainable',
    budget: {},
    parameters: {
      episodes: 1000,
      max_steps: 1000,
      evaluation_episodes: 3,
      evaluation_max_steps: 1000,
      validation_interval: 100,
      num_envs: 4,
      dynamic_sampling: false,
      evaluation_num_envs: 2,
      checkpoint_interval_steps: 0,
      device: 'auto',
      gpu_ids: [],
      distributed: false,
      hidden_dim: 128,
      learning_rate: 0.0003,
      gamma: 1,
      gae_lambda: 0.95,
      clip_ratio: 0.2,
      entropy_coef: 0.01,
      minibatch_size: 64,
    },
  }],
  scenarios: [
    {
      id: 'validation_00000',
      uri: 'dataset/dfjsp_t_validation/validation.jsonl#dfjspt-validation-validation-0042060914-00000',
      digest: '',
      metadata: { split: 'validation', instance_id: 'dfjspt-validation-validation-0042060914-00000' },
    },
    {
      id: 'validation_00001',
      uri: 'dataset/dfjsp_t_validation/validation.jsonl#dfjspt-validation-validation-0042061924-00001',
      digest: '',
      metadata: { split: 'validation', instance_id: 'dfjspt-validation-validation-0042061924-00001' },
    },
  ],
  objective: ppoSelectionObjective,
  budget: {},
  tuning: {
    optimizer: {
      id: 'platform.genetic_search',
      version: '1.0.0',
      interface: 'search_optimizer',
      budget: { max_evaluations: 36 },
      parameters: { population_size: 18, elite_fraction: 0.25, mutation_rate: 0.2, crossover_rate: 0.9 },
    },
    search_space: {
      learning_rate: { kind: 'float', low: 0.00005, high: 0.001, logarithmic: true },
      gamma: { kind: 'float', low: 0.95, high: 1, step: 0.005, logarithmic: false },
      gae_lambda: { kind: 'float', low: 0.85, high: 1, step: 0.01, logarithmic: false },
      clip_ratio: { kind: 'float', low: 0.1, high: 0.3, step: 0.01, logarithmic: false },
      entropy_coef: { kind: 'float', low: 0.001, high: 0.03, logarithmic: true },
    },
    max_trials: 36,
    batch_size: 6,
    training_scenarios: [
      {
        id: 'train_corpus_v1',
        uri: 'dataset/dfjsp_t_train/train.jsonl',
        digest: '',
        metadata: { corpus: true, split: 'training' },
      },
    ],
    benchmark_scenarios: [
      {
        id: 'benchmark_00000',
        uri: 'dataset/dfjsp_t_benchmark/benchmark.jsonl#dfjspt-benchmark-normal-0086000000-00000',
        digest: '',
        metadata: { split: 'benchmark', instance_id: 'dfjspt-benchmark-normal-0086000000-00000' },
      },
    ],
  },
  metadata: {
    data_partitions: { training: 'train', tuning: 'validation', benchmark: 'benchmark' },
    execution_resources: { device: 'auto', gpu_ids: [], distributed: false },
    note: 'Benchmark 只用于最优候选的最终封闭验证。',
  },
}

const trainTemplate = JSON.parse(JSON.stringify(tuneTemplate))
trainTemplate.experiment_id = 'dfjspt_ppo_training_v1'
trainTemplate.name = 'DFJSP-T CTDE-PPO 模型训练'
trainTemplate.purpose = 'train'
trainTemplate.repetitions = 1
trainTemplate.scenarios = trainTemplate.tuning.training_scenarios
trainTemplate.metadata = {
  data_partition: 'training',
  execution_resources: { device: 'auto', gpu_ids: [], distributed: false },
}
delete trainTemplate.tuning

const testTemplate = {
  schema_version: 1,
  experiment_id: 'dfjspt_algorithm_test_v1',
  name: 'DFJSP-T 算法测试',
  purpose: 'evaluate',
  base_seed: 20260920,
  repetitions: 1,
  domain: { id: 'dfjsp_t', version: '1.0.0', parameters: { scenario_root: '.', route_solver: 'astar', assigner: 'nearest' } },
  algorithms: [
    { id: 'memetic_pibt', version: '1.0.0', interface: 'online', budget: { max_steps: 5000 }, parameters: {} },
  ],
  scenarios: [
    {
      id: 'benchmark_00000',
      uri: 'dataset/dfjsp_t_benchmark/benchmark.jsonl#dfjspt-benchmark-normal-0086000000-00000',
      digest: '',
      metadata: { split: 'benchmark', instance_id: 'dfjspt-benchmark-normal-0086000000-00000' },
    },
  ],
  objective: {
    mode: 'single',
    components: [
      { name: 'makespan', metric: 'C_max', direction: 'minimize', aggregation: 'mean' },
    ],
    constraints: [{ name: '全部订单完成', metric: 'success_rate', operator: 'ge', threshold: 1, aggregation: 'min' }],
    feasibility_first: true,
  },
  budget: {},
  metadata: { data_partition: 'benchmark', device: 'cpu', gpu_ids: [] },
}

const activeTab = ref('configure')
const initialConfig = JSON.parse(JSON.stringify(trainTemplate))
renewExperimentId(initialConfig)
const configText = ref(JSON.stringify(initialConfig, null, 2))
const configDirty = ref(false)
const configInputEpoch = ref(0)
/** @type {number} Monotonic edit/request revision, including unfinished field input. */
let configRevision = 0
watch(configText, () => { configRevision += 1 }, { flush: 'sync' })
const validation = ref(null)
const compiled = ref(null)
const storedExperiment = ref(null)
const catalog = reactive({ protocol_version: '', algorithms: [], optimizers: [], domains: [], runtimes: [] })
const catalogError = ref('')
const experiments = ref([])
const executions = ref([])
const executionError = ref('')
const selectedExecutionId = ref('')
const selectedExecutionPayload = ref(null)
const metricPayload = ref({ items: [] })
const executionLogs = ref([])
const executionRuns = ref([])
const executionManifest = ref(null)
const executionCheckpoints = ref([])
const datasets = ref([])
const comparisonDatasetId = ref('')
const datasetComparisonPayload = ref(null)
const comparisonError = ref('')
const replayDatasetId = ref('')
const replayDatasetDetail = ref(null)
const replayDatasetResults = ref([])
const replayInstanceId = ref('')
const replayDatasetRunKey = ref('')
const replayExecutionId = ref('')
const replayRunId = ref('')
const replayRuns = ref([])
const knownReplayRuns = ref([])
const replayMode = ref('full')
const replayResponse = ref(null)
const replayError = ref('')
const replayItemIndex = ref(0)
const replayFrameIndex = ref(0)
const replayPlaying = ref(false)
const diffReport = ref(null)
const diffForm = reactive({ left_key: '', right_key: '', float_tolerance: 0 })
const reproductionReport = ref(null)
const branchReport = ref(null)
const viewingBranchTrace = ref(false)
const branchArtifactsPinned = ref(false)
const branchInputEpoch = ref(0)
const branchForm = reactive({ snapshot_sequence: null, algorithm_key: '', parameters: '{}', input_artifacts: '[]', execution_id: '', run_id: '' })
const notice = reactive({ title: '', text: '', level: 'info' })
const loading = reactive({ catalog: false, datasets: false, experiments: false, executions: false, execution: false, manifest: false, cancel: false, comparison: false, replay: false, reproduce: false, branch: false, diff: false, submit: false, validate: false, compile: false, save: false })

let pollTimer = null
let replayTimer = null

const busy = computed(() => loading.submit || loading.validate || loading.compile || loading.save)
const configPurpose = computed(() => {
  try { return JSON.parse(configText.value).purpose || '' } catch { return '' }
})
const executionActionLabel = computed(() => ({
  train: '开始训练',
  validate: '开始验证',
  evaluate: '开始测试',
  compare: '开始比较',
  replay: '开始回放',
})[configPurpose.value] || '开始执行')
const catalogOptimizers = computed(() => catalog.optimizers)
const catalogAlgorithms = computed(() => catalog.algorithms)
const trainingDatasets = computed(() => datasets.value.filter(dataset => dataset.split === 'training'))
const tuningDatasets = computed(() => datasets.value.filter(dataset => dataset.split === 'validation'))
const testDatasets = computed(() => datasets.value.filter(dataset => !['training', 'validation'].includes(dataset.split)))
const configTargetIsTrainable = computed(() => {
  try { return parsedConfig().algorithms?.[0]?.interface === 'trainable' } catch { return false }
})
const configOptimizerKey = computed(() => {
  try {
    const optimizer = JSON.parse(configText.value).tuning?.optimizer
    return optimizer ? `${optimizer.id}@${optimizer.version}` : ''
  } catch { return '' }
})
const configOptimizerGroup = computed(() => {
  let config
  try { config = JSON.parse(configText.value) } catch { return null }
  const optimizer = config.tuning?.optimizer
  if (!optimizer) return null
  const manifest = catalogOptimizers.value.find(item => item.algorithm_id === optimizer.id && item.version === optimizer.version)
  const schema = manifest?.parameter_schema?.properties || {}
  return {
    key: `optimizer-${optimizer.id}`,
    source: 'optimizer',
    algorithm_id: optimizer.id,
    version: optimizer.version,
    name: optimizerLabel(optimizer.id),
    fields: parameterFields(optimizer.parameters || {}, schema),
  }
})
const configSearchSpaceFields = computed(() => {
  try {
    return Object.entries(parsedConfig().tuning?.search_space || {}).map(([name, dimension]) => ({ name, dimension }))
  } catch { return [] }
})
const optimizerDescription = computed(() => ({
  'platform.grid_search': '网格搜索会系统枚举参数组合，适合范围较小、取值离散的搜索空间。',
  'platform.random_search': '随机搜索从参数范围中抽样，适合先用有限次数快速覆盖较大的搜索空间。',
  'platform.genetic_search': '遗传搜索通过选择、交叉和变异迭代候选，适合参数较多或范围连续的情况。',
})[configOptimizerGroup.value?.algorithm_id] || '')
const configParameterGroups = computed(() => {
  let config
  try { config = JSON.parse(configText.value) } catch { return [] }
  const groups = asList(config.algorithms).map((reference, index) => {
    const manifest = catalog.algorithms.find(item => item.algorithm_id === reference.id && item.version === reference.version)
    const schema = manifest?.parameter_schema?.properties || {}
    return {
      key: `algorithm-${index}-${reference.id}`,
      source: 'algorithm',
      index,
      algorithm_id: reference.id,
      version: reference.version,
      name: manifest?.name || reference.id,
      role: interfaceLabel(reference.interface),
      fields: parameterFields(reference.parameters || {}, schema),
    }
  })
  return groups
})
const validationMessage = computed(() => validation.value?.detail || validation.value?.message || validation.value?.errors ? pretty(validation.value.detail || validation.value.message || validation.value.errors) : '')
const planSummary = computed(() => {
  if (!compiled.value) return ''
  if (compiled.value.dynamic) return '参数候选将在调优过程中自动生成'
  const plan = compiled.value.plan || {}
  const trials = asList(plan.trials)
  const runs = asList(plan.runs).length
  return `${trials.length} 个参数候选 · ${runs} 个运行单元`
})
const selectedExecution = computed(() => selectedExecutionPayload.value?.execution || executions.value.find(item => item.execution_id === selectedExecutionId.value) || null)
const selectedTrainingRunKey = ref('')
const trainingMetricDefinitions = [
  { key: 'loss', label: '总损失 Loss' },
  { key: 'policy_loss', label: '策略损失 Policy loss' },
  { key: 'value_loss', label: '价值损失 Value loss' },
  { key: 'entropy', label: '策略熵 Entropy' },
  { key: 'approx_kl', label: '近似 KL' },
  { key: 'clip_fraction', label: '裁剪比例 Clip fraction' },
  { key: 'explained_variance', label: '价值解释方差 Explained variance' },
  { key: 'episode_reward_mean', label: '本批平均累计奖励' },
]
const trainingRuns = computed(() => {
  /** @type {Map<string, {key: string, label: string, events: object[]}>} */
  const runs = new Map()
  for (const event of executionLogs.value) {
    const key = `${event.execution_id}/${event.run_id}`
    if (event.event_type === 'training_started' || event.event_type === 'training_progress') {
      if (!runs.has(key)) runs.set(key, { key, label: `${event.execution_id} · ${event.run_id}`, events: [] })
    }
    if (runs.has(key)) runs.get(key).events.push(event)
  }
  return [...runs.values()]
})
watch(trainingRuns, runs => {
  if (!runs.some(run => run.key === selectedTrainingRunKey.value)) selectedTrainingRunKey.value = runs[0]?.key || ''
})
const selectedTrainingEvents = computed(() => trainingRuns.value.find(run => run.key === selectedTrainingRunKey.value)?.events || [])
const trainingUpdates = computed(() => selectedTrainingEvents.value
  .filter(event => event.payload?.phase === 'update_completed' && event.payload.trainer.updated)
  .map(event => event.payload))
const latestTrainingUpdate = computed(() => trainingUpdates.value.at(-1))
const trainingBarriers = computed(() => selectedTrainingEvents.value
  .filter(event => event.payload?.phase === 'barrier_completed').map(event => event.payload))
const samplingStageMessage = computed(() => selectedTrainingEvents.value
  .filter(event => ['episode_started', 'collecting', 'sampling_completed'].includes(event.payload?.phase)).at(-1)?.payload?.message || '')
const learnerStageMessage = computed(() => selectedTrainingEvents.value
  .filter(event => ['updating', 'update_completed'].includes(event.payload?.phase)).at(-1)?.payload?.message || '')
const trainingStageMessage = computed(() => {
  const event = selectedTrainingEvents.value.filter(item => !['evaluating', 'evaluation_queued', 'evaluation_completed', 'checkpoint_saved'].includes(item.payload?.phase)).at(-1)
  if (event?.event_type === 'run_finished') return `训练${statusLabel(event.payload.status)}`
  return event?.payload?.message || '等待训练进度'
})
const evaluationStageMessage = computed(() => selectedTrainingEvents.value
  .filter(event => ['evaluating', 'evaluation_completed', 'evaluation_draining'].includes(event.payload?.phase)).at(-1)?.payload?.message || '')
const canCancelExecution = computed(() => ['compiled', 'running'].includes(selectedExecution.value?.status))
const showCancelExecution = computed(() => canCancelExecution.value || selectedExecution.value?.status === 'cancel_requested')
const metricItems = computed(() => asList(metricPayload.value?.items))
const candidateItems = computed(() => asList(metricPayload.value?.candidates))
const benchmarkItems = computed(() => asList(metricPayload.value?.benchmark))
const candidateBestText = computed(() => candidateItems.value.length ? `已记录 ${candidateItems.value.length} 个参数候选及其综合评价结果。` : '尚无候选评价。')
const datasetComparisonRows = computed(() => {
  const groups = new Map()
  for (const item of asList(datasetComparisonPayload.value?.items)) {
    const key = `${item.algorithm_id}@${item.algorithm_version}:${JSON.stringify(item.algorithm_parameters || {})}`
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key).push(item)
  }
  return [...groups.entries()].map(([key, items]) => {
    const succeeded = items.filter(item => item.status === 'succeeded' || item.status === 'completed')
    const metricValues = name => succeeded.map(item => Number(item.metrics?.[name])).filter(Number.isFinite)
    const mean = values => values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null
    return {
      key,
      algorithm_id: items[0].algorithm_id,
      algorithm_name: items[0].algorithm_name || items[0].algorithm_id,
      algorithm_version: items[0].algorithm_version,
      algorithm_parameters: items[0].algorithm_parameters || {},
      instance_count: new Set(succeeded.map(item => item.instance_id)).size,
      succeeded: succeeded.length,
      total: items.length,
      average_urgent_makespan: mean(metricValues('C_max_E')),
      average_makespan: mean(metricValues('C_max')),
      latest_finished_at: items.map(item => item.finished_at).filter(Boolean).sort().at(-1),
    }
  }).sort((left, right) => (
    (left.average_urgent_makespan ?? Infinity) - (right.average_urgent_makespan ?? Infinity)
    || (left.average_makespan ?? Infinity) - (right.average_makespan ?? Infinity)
  ))
})
const replayAvailableInstances = computed(() => asList(replayDatasetDetail.value?.instances))
const replayDatasetAlgorithms = computed(() => replayDatasetResults.value
  .filter(item => item.instance_id === replayInstanceId.value)
  .map(item => ({ ...item, key: `${item.execution_id}::${item.run_id}` })))
const replayItems = computed(() => {
  if (!replayResponse.value) return []
  if (Array.isArray(replayResponse.value.items)) return replayResponse.value.items
  if (replayMode.value === 'decision' && Array.isArray(replayResponse.value.decisions)) return replayResponse.value.decisions
  return asList(replayResponse.value.trace?.events || replayResponse.value.trace)
})
const replayFrames = computed(() => replayItems.value.map((item, index) => ({ item, index, frame: extractFrame(item) })).filter(item => item.frame))
const currentFormalFrame = computed(() => replayFrames.value[replayFrameIndex.value]?.frame || null)
const currentReplayItem = computed(() => replayItems.value[replayItemIndex.value] || null)
const replaySnapshots = computed(() => asList(replayResponse.value?.trace?.snapshots))
const onlineAlgorithms = computed(() => catalog.algorithms.filter(algorithm => asList(algorithm.interfaces).includes('online')))
const selectedBranchAlgorithm = computed(() => onlineAlgorithms.value.find(algorithm => `${algorithm.algorithm_id}@${algorithm.version}` === branchForm.algorithm_key) || null)
const branchParameterFields = computed(() => {
  let parameters
  try { parameters = JSON.parse(branchForm.parameters || '{}') } catch { return [] }
  const schema = selectedBranchAlgorithm.value?.parameter_schema?.properties || {}
  return parameterFields(parameters, schema)
})
const branchInputArtifacts = computed(() => {
  try {
    const value = JSON.parse(branchForm.input_artifacts || '[]')
    return Array.isArray(value) ? value : null
  } catch { return null }
})
const canBranch = computed(() => replayResponse.value
  && branchForm.snapshot_sequence != null
  && selectedBranchAlgorithm.value
  && branchForm.execution_id
  && branchForm.run_id
  && branchInputArtifacts.value !== null
  && branchInputArtifacts.value.length >= minimumInputArtifacts(selectedBranchAlgorithm.value, 'online'))
const reproductionDifferences = computed(() => asList(reproductionReport.value?.diff?.differences))
const replayFailureStatuses = new Set(['failed', 'cancelled', 'timed_out'])
const reproductionRunFailed = computed(() => replayFailureStatuses.has(reproductionReport.value?.result?.status))
const branchRunFailed = computed(() => replayFailureStatuses.has(branchReport.value?.result?.status))
const reproductionEquivalent = computed(() => reproductionReport.value?.diff?.equivalent ?? (!reproductionDifferences.value.length && reproductionReport.value?.diff?.left_item_count === reproductionReport.value?.diff?.right_item_count))
const reproductionFirstDifference = computed(() => reproductionDifferences.value[0] || null)
const activeTraceRunId = computed(() => replayResponse.value?.trace?.run_id || replayRunId.value)
const activeReplayView = computed(() => replayResponse.value?.view || replayMode.value)
const replayViewLabel = computed(() => {
  if (viewingBranchTrace.value) return '分支回放'
  return activeReplayView.value === 'decision' ? '决策回放' : '完整回放'
})
const allKnownRuns = computed(() => {
  const byKey = new Map()
  const addRuns = (runs, fallbackExecutionId) => {
    for (const run of runs) {
      if (!run?.run_id) continue
      const executionId = run.route_execution_id || run.owner_execution_id || fallbackExecutionId || run.execution_id
      if (!executionId) continue
      const key = `${executionId}::${run.run_id}`
      byKey.set(key, { ...run, execution_id: executionId, key })
    }
  }
  addRuns(executionRuns.value, selectedExecutionId.value)
  addRuns(replayRuns.value, replayExecutionId.value)
  addRuns(knownReplayRuns.value)
  if (branchReport.value?.trace?.run_id && branchReport.value.execution_id) {
    addRuns([{ run_id: branchReport.value.trace.run_id }], branchReport.value.execution_id)
  }
  if (reproductionReport.value?.trace?.run_id && reproductionReport.value.execution_id) {
    addRuns([{ run_id: reproductionReport.value.trace.run_id }], reproductionReport.value.execution_id)
  }
  return [...byKey.values()]
})
const canDiff = computed(() => diffForm.left_key && diffForm.right_key && diffForm.left_key !== diffForm.right_key)
const diffEquivalent = computed(() => diffReport.value?.equivalent ?? (!asList(diffReport.value?.differences).length && diffReport.value?.left_item_count === diffReport.value?.right_item_count))
const executionArtifacts = computed(() => {
  const byDigest = new Map()
  for (const run of executionRuns.value) {
    for (const artifact of asList(run.artifacts)) {
      byDigest.set(artifact.digest, {
        ...artifact,
        run_id: run.run_id,
        execution_id: run.route_execution_id || run.owner_execution_id || selectedExecutionId.value,
        source: 'run',
      })
    }
  }
  for (const artifact of asList(executionManifest.value?.artifacts)) {
    if (!byDigest.has(artifact.digest)) byDigest.set(artifact.digest, { ...artifact, source: 'manifest' })
  }
  return [...byDigest.values()]
})

const catalogEmptyTitle = computed(() => catalogError.value ? '能力目录不可用' : '目录为空')
const catalogEmptyText = computed(() => catalogError.value || '后端返回成功，但尚未注册算法、领域或运行时。')
const executionEmptyTitle = computed(() => executionError.value ? '执行列表不可用' : '暂无执行记录')
const executionEmptyText = computed(() => executionError.value || '提交实验后，实际启动记录会在这里出现。')
const comparisonEmptyTitle = computed(() => comparisonError.value ? '测试结果不可用' : '尚未选择测试数据集')
const comparisonEmptyText = computed(() => comparisonError.value || '选择一个测试数据集，查看曾在该数据集上完成测试的算法结果。')
const replayEmptyTitle = computed(() => replayError.value ? '回放数据不可用' : '尚未选择运行单元')
const replayEmptyText = computed(() => replayError.value || '先选择执行记录和运行单元，再读取完整事件或决策视图。')

function inferredParameterDefinition(value) {
  if (Array.isArray(value)) return { type: 'array' }
  if (value && typeof value === 'object') return { type: 'object' }
  if (typeof value === 'boolean') return { type: 'boolean' }
  if (typeof value === 'number') return { type: Number.isInteger(value) ? 'integer' : 'number' }
  return { type: 'string' }
}
function defaultSearchSpace(properties) {
  return Object.fromEntries(Object.entries(properties).flatMap(([name, definition]) => {
    if (name === 'distributed') return []
    if (definition.enum) return [[name, { kind: 'categorical', choices: definition.enum }]]
    if (definition.type === 'boolean') return [[name, { kind: 'categorical', choices: [false, true] }]]
    if (!['integer', 'number'].includes(definition.type) || definition.default === undefined) return []
    const current = Number(definition.default)
    if (definition.type === 'integer') {
      const low = definition.minimum !== undefined
        ? Math.ceil(definition.minimum)
        : definition.exclusiveMinimum !== undefined
          ? Math.floor(definition.exclusiveMinimum) + 1
          : Math.max(0, Math.floor(current / 2))
      const high = definition.maximum !== undefined
        ? Math.floor(definition.maximum)
        : definition.exclusiveMaximum !== undefined
          ? Math.ceil(definition.exclusiveMaximum) - 1
          : Math.max(low + 1, current * 2)
      return [[name, { kind: 'integer', low, high, step: 1, logarithmic: false }]]
    }
    const low = definition.minimum !== undefined
      ? Number(definition.minimum)
      : definition.exclusiveMinimum !== undefined
        ? Math.max(Number(definition.exclusiveMinimum) + Number.EPSILON, current * 0.1)
        : current === 0 ? 0 : Math.max(0, current * 0.25)
    const high = definition.maximum !== undefined
      ? Number(definition.maximum)
      : definition.exclusiveMaximum !== undefined
        ? Math.min(Number(definition.exclusiveMaximum) - Number.EPSILON, Math.max(low + 0.1, current * 4))
        : current === 0 ? 1 : Math.max(low + 0.1, current * 4)
    return [[name, { kind: 'float', low, high, logarithmic: false }]]
  }))
}
function parameterLabel(name) { return parameterLabels[name] || name }
/** @param {Record<string, unknown>} parameters @param {Record<string, object>} schema */
function parameterFields(parameters, schema) {
  const names = [...Object.keys(schema).filter(name => Object.hasOwn(parameters, name) || Object.hasOwn(schema[name], 'default')),
    ...Object.keys(parameters).filter(name => !Object.hasOwn(schema, name)).sort()]
  return names.map(name => ({ name,
    value: Object.hasOwn(parameters, name) ? parameters[name] : schema[name].default,
    definition: schema[name] || inferredParameterDefinition(parameters[name]) }))
}
function parameterInputValue(value) { return value && typeof value === 'object' ? JSON.stringify(value) : value }
function parseParameterValue(definition, value) {
  if (definition.type === 'integer') return Number.parseInt(value, 10)
  if (definition.type === 'number') return Number(value)
  if (definition.type === 'boolean') return value === 'true'
  if (definition.type === 'array' || definition.type === 'object') return JSON.parse(value)
  return value
}
function updateConfigParameter(group, field, value) {
  try {
    const config = parsedConfig()
    const parameters = group.source === 'optimizer'
      ? config.tuning.optimizer.parameters
      : config.algorithms[group.index].parameters
    parameters[field.name] = parseParameterValue(field.definition, value)
    renewExperimentId(config)
    configText.value = JSON.stringify(config, null, 2)
    markConfigDirty()
  } catch (error) {
    setNotice('参数格式错误', `${parameterLabel(field.name)}：${error.message}`, 'error')
  }
}
function updateSearchNumber(name, property, value) {
  const config = parsedConfig()
  config.tuning.search_space[name][property] = Number(value)
  renewExperimentId(config)
  configText.value = JSON.stringify(config, null, 2)
  markConfigDirty()
}
function updateSearchChoices(name, value) {
  const config = parsedConfig()
  const current = config.tuning.search_space[name].choices
  config.tuning.search_space[name].choices = value.split(',').map(item => item.trim()).filter(Boolean).map(item => {
    if (current.every(choice => typeof choice === 'boolean')) return item === 'true'
    if (current.every(choice => typeof choice === 'number')) return Number(item)
    return item
  })
  renewExperimentId(config)
  configText.value = JSON.stringify(config, null, 2)
  markConfigDirty()
}
function updateBranchParameter(field, value) {
  try {
    const parameters = JSON.parse(branchForm.parameters || '{}')
    parameters[field.name] = parseParameterValue(field.definition, value)
    branchForm.parameters = JSON.stringify(parameters, null, 2)
  } catch (error) {
    setNotice('参数格式错误', `${parameterLabel(field.name)}：${error.message}`, 'error')
  }
}

function asList(value) { return Array.isArray(value) ? value : [] }
function pretty(value) { return JSON.stringify(value, null, 2) }
function concise(value) { const text = typeof value === 'string' ? value : JSON.stringify(value); return text == null ? '∅' : text.length > 120 ? `${text.slice(0, 117)}…` : text }
function shortId(value) { if (!value) return '—'; return value.length > 20 ? `${value.slice(0, 9)}…${value.slice(-7)}` : value }
function displayTime(value) { if (!value) return '—'; return new Date(value).toLocaleString('zh-CN', { hour12: false }) }
/** @param {string | null} value */
function displayPreciseTime(value) {
  if (!value) return '—'
  return new Date(value).toLocaleString('zh-CN', { hour12: false, year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', fractionalSecondDigits: 3 })
}
function formatBytes(value) { const bytes = Number(value); if (!Number.isFinite(bytes)) return '—'; if (bytes < 1024) return `${bytes} B`; if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`; return `${(bytes / 1024 ** 2).toFixed(1)} MB` }
function interfaceLabel(value) { return ({ online: '在线策略', batch: '批求解', iterative: '迭代求解', trainable: '可训练', search_optimizer: '调优器' })[value] || value }
function optimizerLabel(value) { return ({ 'platform.grid_search': '网格搜索', 'platform.random_search': '随机搜索', 'platform.genetic_search': '遗传搜索' })[value] || value }
function purposeLabel(value) { return ({ train: '训练', validate: '验证', evaluate: '测试', compare: '比较', tune: '超参数探索', replay: '回放' })[value] || value || '—' }
function statusLabel(value) { return ({ draft: '草稿', compiled: '已编译', pending: '等待中', queued: '排队中', preparing: '准备中', running: '运行中', succeeded: '已完成', completed: '已完成', failed: '失败', infeasible: '不可行', cancelled: '已取消', cancel_requested: '取消中', timed_out: '超时', partial: '部分完成' })[value] || value || '未知' }
function statusClass(value) { if (['succeeded', 'completed'].includes(value)) return 'success'; if (['running', 'preparing'].includes(value)) return 'running'; if (['failed', 'timed_out'].includes(value)) return 'failure'; if (['cancelled', 'cancel_requested'].includes(value)) return 'muted'; return 'queued' }
function eventTypeLabel(value) { return ({ run_started: '运行开始', run_finished: '运行结束', training_started: '训练开始', training_progress: '训练进度', training_finished: '训练结束', evaluation_started: '测试开始', evaluation_finished: '测试结束', artifact_loaded: '已加载模型', tuning_started: '探索开始', tuning_finished: '探索结束', candidate_proposed: '生成候选参数', candidate_evaluated: '候选参数完成', benchmark_started: '基准测试开始', benchmark_finished: '基准测试结束' })[value] || value || '事件' }
function formatVector(value) { const values = asList(value); return values.length ? `(${values.map(item => Number.isFinite(Number(item)) ? Number(item).toLocaleString('zh-CN', { maximumFractionDigits: 4 }) : item).join(', ')})` : '—' }
function formatMetricValue(value) { return Number.isFinite(value) ? value.toLocaleString('zh-CN', { maximumFractionDigits: 4 }) : '—' }
function parameterCount(algorithm) { return Object.keys(algorithm.parameter_schema?.properties || {}).length }
function minimumInputArtifacts(algorithm, interfaceName) {
  const minimum = algorithm?.minimum_input_artifacts || {}
  return Number(minimum[interfaceName] ?? minimum[interfaceName.toUpperCase()] ?? 0)
}
function metricSummary(metrics) { if (!metrics || typeof metrics !== 'object') return '—'; const entries = Object.entries(metrics).slice(0, 5); return entries.length ? entries.map(([key, value]) => `${key}=${typeof value === 'number' ? value.toFixed(3) : concise(value)}`).join(' · ') : '—' }
function eventMessage(event) { return concise(event.message || event.payload?.message || event.payload || event.details || '') }

function setNotice(title, text, level = 'info') { notice.title = title; notice.text = text; notice.level = level }
function clearNotice() { notice.title = ''; notice.text = '' }
function errorText(error) { return typeof error?.detail === 'string' ? error.detail : error?.detail ? pretty(error.detail) : error?.message || '请求失败' }
function artifactRefValue(artifact) {
  return {
    digest: artifact.digest,
    kind: artifact.kind,
    media_type: artifact.media_type,
    size_bytes: artifact.size_bytes,
    uri: artifact.uri,
    metadata: artifact.metadata || {},
  }
}
function artifactDownloadUrl(artifact) { return getApiUrl(API_ROUTES.ALGORITHM_PLATFORM_ARTIFACT, { digest: artifact.digest }) }
function branchCompatibleAlgorithm(artifact) {
  return onlineAlgorithms.value.find(algorithm => (
    minimumInputArtifacts(algorithm, 'online') > 0
    && (!asList(algorithm.input_artifact_kinds).length || asList(algorithm.input_artifact_kinds).includes(artifact.kind))
  )) || null
}
function testCompatibleAlgorithm(artifact) {
  return onlineAlgorithms.value.find(algorithm => (
    minimumInputArtifacts(algorithm, 'online') > 0
    && (!asList(algorithm.input_artifact_kinds).length || asList(algorithm.input_artifact_kinds).includes(artifact.kind))
  )) || null
}
function rememberRuns(runs, fallbackExecutionId) {
  const byKey = new Map(knownReplayRuns.value.map(run => [`${run.execution_id}::${run.run_id}`, run]))
  for (const run of runs) {
    if (!run?.run_id) continue
    const executionId = run.route_execution_id || run.owner_execution_id || fallbackExecutionId || run.execution_id
    if (!executionId) continue
    byKey.set(`${executionId}::${run.run_id}`, { ...run, execution_id: executionId })
  }
  knownReplayRuns.value = [...byKey.values()]
}

function activateTab(tab) {
  activeTab.value = tab
  if (tab === 'executions') void loadExecutions()
  if (tab === 'replay' && !executions.value.length) void loadExecutions()
}

async function loadTemplate(kind) {
  const revision = ++configRevision
  const template = kind === 'train' ? trainTemplate : kind === 'test' ? testTemplate : tuneTemplate
  const config = JSON.parse(JSON.stringify(template))
  try {
    await resolveDatasetReferences(config)
  } catch (error) {
    if (revision !== configRevision) return
    setNotice('模板数据集无法载入', errorText(error), 'error')
    return
  }
  if (revision !== configRevision) return
  renewExperimentId(config)
  configInputEpoch.value += 1
  configText.value = JSON.stringify(config, null, 2)
  validation.value = null
  compiled.value = null
  storedExperiment.value = null
  configDirty.value = false
  const message = kind === 'train'
    ? '使用所选训练数据集优化算法内部参数并保存模型。'
    : kind === 'test'
      ? '使用固定算法和参数在所选测试数据集上运行并保存结果。'
      : '搜索算法外部参数，并使用独立数据集选择和测试候选。'
  setNotice('模板已载入', message)
}

/** @param {object} config */
async function resolveDatasetReferences(config) {
  const catalog = await apiGet(API_ROUTES.ALGORITHM_PLATFORM_DATASETS)
  const references = [...asList(config.scenarios), ...asList(config.tuning?.training_scenarios),
    ...asList(config.tuning?.benchmark_scenarios), ...asList(config.domain?.parameters?.validation_scenarios)]
  const uris = [...new Set(references.map(reference => reference.uri.split('#')[0]))]
  const details = await Promise.all(uris.map(async uri => {
    const dataset = catalog.items.find(item => item.uri === uri)
    if (!dataset) throw new Error(`找不到数据集 ${uri}，请重新选择数据集`)
    return [uri, await loadDatasetDetail(dataset.dataset_id)]
  }))
  const byUri = new Map(details)
  for (const reference of references) {
    const [uri, instanceId] = reference.uri.split('#')
    const dataset = byUri.get(uri)
    if (instanceId) {
      const instance = dataset.instances.find(item => item.instance_id === instanceId)
      if (!instance) throw new Error(`数据集中已不存在实例 ${instanceId}，请重新选择数据集`)
      reference.digest = instance.digest
    } else {
      for (const selectedId of asList(reference.metadata?.instance_ids)) {
        if (!dataset.instances.some(item => item.instance_id === selectedId)) {
          throw new Error(`数据集中已不存在验证实例 ${selectedId}，请重新选择验证数据集`)
        }
      }
      reference.digest = dataset.digest
    }
  }
}

async function refreshDatasetReferences() {
  const revision = ++configRevision
  try {
    const config = parsedConfig()
    await resolveDatasetReferences(config)
    if (revision !== configRevision) return
    renewExperimentId(config)
    configText.value = JSON.stringify(config, null, 2)
    markConfigDirty()
    setNotice('数据集引用已更新', '训练、验证和测试引用已读取当前数据版本，原参数与实例选择保留。', 'success')
  } catch (error) { if (revision !== configRevision) return; setNotice('数据集引用更新失败', errorText(error), 'error') }
}

function selectedConfigDataset(role) {
  let config
  try { config = parsedConfig() } catch { return '' }
  let scenario
  if (role === 'training') scenario = config.purpose === 'train' ? config.scenarios?.[0] : config.tuning?.training_scenarios?.[0]
  else if (role === 'validation') scenario = config.domain?.parameters?.validation_scenarios?.[0]
  else if (role === 'benchmark') scenario = config.tuning?.benchmark_scenarios?.[0]
  else scenario = config.scenarios?.[0]
  const uri = scenario?.uri?.split('#')[0]
  return datasets.value.find(dataset => dataset.uri === uri)?.dataset_id || ''
}

function renewExperimentId(config) {
  const mode = ({ train: 'train', tune: 'tune', evaluate: 'test' })[config.purpose] || 'experiment'
  const algorithmId = config.algorithms?.[0]?.id || 'algorithm'
  if (['train', 'tune'].includes(config.purpose) && config.algorithms.some(item => item.id === 'ctde_ppo' && item.interface === 'trainable')) {
    config.objective = JSON.parse(JSON.stringify(ppoSelectionObjective))
  }
  config.experiment_id = `dfjspt_${mode}_${algorithmId}_${Date.now().toString(36)}`
}

async function loadDatasetDetail(datasetId) {
  return apiGet(API_ROUTES.ALGORITHM_PLATFORM_DATASET, { params: { dataset_id: datasetId } })
}

async function applyDatasetToConfig(role, datasetId) {
  if (!datasetId) return
  const revision = ++configRevision
  try {
    const dataset = await loadDatasetDetail(datasetId)
    if (revision !== configRevision) return
    const config = parsedConfig()
    const corpus = {
      id: dataset.dataset_id,
      uri: dataset.uri,
      digest: dataset.digest,
      metadata: { corpus: true, split: dataset.split },
    }
    const instances = asList(dataset.instances).map(instance => ({
      id: instance.instance_id,
      uri: `${dataset.uri}#${instance.instance_id}`,
      digest: instance.digest,
      metadata: { split: dataset.split, instance_id: instance.instance_id },
    }))
    if (role === 'training') {
      if (config.purpose === 'train') config.scenarios = [corpus]
      else config.tuning.training_scenarios = [corpus]
    } else if (role === 'validation') {
      const previous = config.domain.parameters.validation_scenarios?.[0]
      const selectedIds = asList(previous?.metadata?.instance_ids)
      const ids = new Set(dataset.instances.map(instance => instance.instance_id))
      const keepSubset = previous?.uri === dataset.uri && selectedIds.length && selectedIds.every(id => ids.has(id))
      config.domain.parameters.validation_scenarios = [{ ...corpus,
        metadata: { ...corpus.metadata, instance_ids: keepSubset ? selectedIds : dataset.instances.slice(0, 2).map(instance => instance.instance_id) },
      }]
    } else if (role === 'benchmark') {
      config.tuning.benchmark_scenarios = instances
    } else {
      config.scenarios = instances
    }
    config.metadata ||= {}
    config.metadata.datasets ||= {}
    config.metadata.datasets[role] = dataset.dataset_id
    renewExperimentId(config)
    configText.value = JSON.stringify(config, null, 2)
    markConfigDirty()
    setNotice('数据集已更新', `${dataset.name} 已用于${role === 'training' ? '训练' : role === 'validation' ? '模型验证' : role === 'tuning' ? '参数选择' : '测试'}。`)
  } catch (error) { if (revision !== configRevision) return; setNotice('数据集应用失败', errorText(error), 'error') }
}

function formatConfig() {
  try { configText.value = JSON.stringify(JSON.parse(configText.value), null, 2); clearNotice() }
  catch (error) { setNotice('完整配置无法整理', error.message, 'error') }
}

function markConfigDirty() { configRevision += 1; configDirty.value = true; validation.value = null; compiled.value = null; storedExperiment.value = null }

function parsedConfig() {
  return JSON.parse(configText.value)
}

function requestBody() { return { config_format: 'json', config: parsedConfig() } }

async function loadCatalog() {
  loading.catalog = true
  catalogError.value = ''
  try {
    const data = await apiGet(API_ROUTES.ALGORITHM_PLATFORM_CATALOG)
    catalog.protocol_version = data.protocol_version || ''
    catalog.algorithms = asList(data.algorithms)
    catalog.optimizers = asList(data.optimizers)
    catalog.domains = asList(data.domains)
    catalog.runtimes = asList(data.runtimes)
  } catch (error) { catalogError.value = errorText(error) }
  finally { loading.catalog = false }
}

async function loadDatasets() {
  loading.datasets = true
  try {
    const data = await apiGet(API_ROUTES.ALGORITHM_PLATFORM_DATASETS)
    datasets.value = asList(data.items)
  } catch (error) { setNotice('数据集列表读取失败', errorText(error), 'error') }
  finally { loading.datasets = false }
}

async function loadExperiments() {
  loading.experiments = true
  try {
    const data = await apiGet(API_ROUTES.ALGORITHM_PLATFORM_EXPERIMENTS)
    experiments.value = asList(data.items)
  } catch (error) { setNotice('实验列表读取失败', errorText(error), 'error') }
  finally { loading.experiments = false }
}

async function openStoredExperiment(experimentId) {
  let revision = ++configRevision
  loading.experiments = true
  try {
    const stored = await apiGet(API_ROUTES.ALGORITHM_PLATFORM_EXPERIMENT, { params: { experiment_id: experimentId } })
    if (revision !== configRevision) return
    const config = JSON.parse(JSON.stringify(stored.spec))
    const objectiveChanged = ['train', 'tune'].includes(config.purpose)
      && config.algorithms.some(item => item.id === 'ctde_ppo' && item.interface === 'trainable')
      && (config.objective.mode !== 'single' || config.objective.components.length !== 1
        || config.objective.components[0].metric !== 'episode_reward' || config.objective.components[0].direction !== 'maximize')
    if (objectiveChanged) renewExperimentId(config)
    configInputEpoch.value += 1
    configText.value = JSON.stringify(config, null, 2)
    validation.value = null
    compiled.value = null
    storedExperiment.value = objectiveChanged ? null : stored
    configDirty.value = objectiveChanged
    revision = configRevision
    const result = await apiPost(API_ROUTES.ALGORITHM_PLATFORM_VALIDATE, {
      config_format: 'json', config,
    })
    if (revision !== configRevision) return
    validation.value = result
    if (!validation.value.valid) {
      const messages = asList(validation.value.errors).map(error => error.message).filter(Boolean)
      setNotice('实验配置需要更新', messages.join('；'), 'error')
      return
    }
    setNotice('实验定义已载入', objectiveChanged
      ? '已将选模目标改为最大化验证累计奖励，并创建新的实验定义。'
      : `${experimentId} 已载入编辑器。`, 'success')
  } catch (error) { if (revision !== configRevision) return; setNotice('实验定义读取失败', errorText(error), 'error') }
  finally { loading.experiments = false }
}

async function applyCatalogAlgorithm(algorithm) {
  const revision = ++configRevision
  try {
    const config = parsedConfig()
    const previousAlgorithmId = config.algorithms?.[0]?.id
    const interfaces = asList(algorithm.interfaces)
    if (config.purpose === 'train' && !interfaces.includes('trainable')) {
      setNotice('当前算法不可训练', `${algorithm.name || algorithm.algorithm_id} 没有 trainable 接口，请在超参数探索或测试模式中使用。`, 'error')
      return
    }
    const executionInterfaces = ['online', 'batch', 'iterative']
    const firstInterface = ['train', 'tune'].includes(config.purpose) && interfaces.includes('trainable')
      ? 'trainable'
      : executionInterfaces.find(interfaceName => interfaces.includes(interfaceName))
    const properties = algorithm.parameter_schema?.properties || {}
    const parameters = Object.fromEntries(Object.entries(properties).filter(([, definition]) => definition.default !== undefined).map(([name, definition]) => [name, definition.default]))
    const reference = { id: algorithm.algorithm_id, version: algorithm.version, interface: firstInterface, parameters }
    config.algorithms = [reference]
    if (firstInterface !== 'trainable') config.objective = JSON.parse(JSON.stringify(testTemplate.objective))
    if (config.purpose === 'tune' && config.tuning) {
      if (previousAlgorithmId !== algorithm.algorithm_id) config.tuning.search_space = defaultSearchSpace(properties)
      if (firstInterface === 'trainable' && !asList(config.tuning.training_scenarios).length) {
        config.tuning.training_scenarios = JSON.parse(JSON.stringify(tuneTemplate.tuning.training_scenarios))
        await resolveDatasetReferences(config)
        if (revision !== configRevision) return
      }
      if (firstInterface !== 'trainable') {
        delete config.tuning.training_scenarios
        if (config.metadata?.datasets) delete config.metadata.datasets.training
      }
    }
    renewExperimentId(config)
    configText.value = JSON.stringify(config, null, 2)
    markConfigDirty()
    configInputEpoch.value += 1
    setNotice('已选择算法', `${algorithm.name || algorithm.algorithm_id} 已写入实验配置。`)
  } catch (error) { if (revision !== configRevision) return; setNotice('无法应用算法', `请先修复完整配置：${error.message}`, 'error') }
}

function applyConfigOptimizer(key) {
  const [algorithmId, version] = key.split('@')
  const optimizer = catalogOptimizers.value.find(item => item.algorithm_id === algorithmId && item.version === version)
  const config = parsedConfig()
  const properties = optimizer.parameter_schema?.properties || {}
  const parameters = Object.fromEntries(Object.entries(properties).filter(([, definition]) => definition.default !== undefined).map(([name, definition]) => [name, definition.default]))
  const budget = config.tuning.optimizer.budget
  config.tuning.optimizer = {
    id: optimizer.algorithm_id,
    version: optimizer.version,
    interface: 'search_optimizer',
    parameters,
    ...(budget ? { budget } : {}),
  }
  renewExperimentId(config)
  configText.value = JSON.stringify(config, null, 2)
  markConfigDirty()
  configInputEpoch.value += 1
  setNotice('参数优化方式已更新', `当前使用${optimizerLabel(optimizer.algorithm_id)}。`)
}

function applyCatalogDomain(domain) {
  try {
    const config = parsedConfig()
    config.domain = { id: domain.domain_id, version: domain.version, parameters: {} }
    renewExperimentId(config)
    configText.value = JSON.stringify(config, null, 2)
    markConfigDirty()
    setNotice('已应用问题领域', `${domain.name || domain.domain_id} 已写入实验配置。`)
  } catch (error) { setNotice('无法应用领域', `请先修复完整配置：${error.message}`, 'error') }
}

async function validateConfig() {
  const revision = ++configRevision
  loading.validate = true
  validation.value = null
  try {
    const result = await apiPost(API_ROUTES.ALGORITHM_PLATFORM_VALIDATE, requestBody())
    if (revision !== configRevision) return
    validation.value = result
    if (validation.value.valid) {
      configDirty.value = false
      setNotice('配置校验通过', '领域能力、算法协议、目标、预算和数据分区均已由后端检查。', 'success')
    } else {
      const messages = asList(validation.value.errors).map(error => error.message).filter(Boolean)
      setNotice('配置校验失败', messages.join('；') || '实验定义未通过平台校验。', 'error')
    }
  } catch (error) {
    if (revision !== configRevision) return
    validation.value = { valid: false, detail: error.detail || error.message }
    setNotice('配置校验失败', errorText(error), 'error')
  } finally { loading.validate = false }
}

async function compileConfig() {
  const revision = ++configRevision
  loading.compile = true
  compiled.value = null
  try {
    const result = await apiPost(API_ROUTES.ALGORITHM_PLATFORM_COMPILE, requestBody())
    if (revision !== configRevision) return
    compiled.value = result
    configDirty.value = false
    setNotice('计划已编译', compiled.value.dynamic ? '参数候选将在调优过程中自动生成。' : '参数候选与运行单元计划已固定。', 'success')
  } catch (error) { if (revision !== configRevision) return; setNotice('计划编译失败', errorText(error), 'error') }
  finally { loading.compile = false }
}

async function saveExperiment() {
  const revision = ++configRevision
  loading.save = true
  storedExperiment.value = null
  try {
    const result = await apiPost(API_ROUTES.ALGORITHM_PLATFORM_EXPERIMENTS, requestBody())
    if (revision !== configRevision) return
    storedExperiment.value = result
    configDirty.value = false
    setNotice('实验定义已保存', storedExperiment.value.experiment_id, 'success')
    await loadExperiments()
  } catch (error) { if (revision !== configRevision) return; setNotice('保存实验定义失败', errorText(error), 'error') }
  finally { loading.save = false }
}

async function submitExecution(mode) {
  loading.submit = true
  try {
    const data = await apiPost(API_ROUTES.ALGORITHM_PLATFORM_EXECUTIONS, { mode, ...requestBody() }, { timeout: 30000 })
    setNotice('执行任务已提交', `${data.execution_id} 已进入运行队列。`, 'success')
    await loadExecutions()
    await selectExecution(data.execution_id)
    activeTab.value = 'executions'
  } catch (error) { setNotice('提交失败', errorText(error), 'error') }
  finally { loading.submit = false }
}

async function loadExecutions() {
  loading.executions = true
  executionError.value = ''
  try {
    const data = await apiGet(API_ROUTES.ALGORITHM_PLATFORM_EXECUTIONS)
    executions.value = asList(data.items)
    if (selectedExecutionId.value && !executions.value.some(item => item.execution_id === selectedExecutionId.value)) selectedExecutionId.value = ''
  } catch (error) { executionError.value = errorText(error); executions.value = [] }
  finally { loading.executions = false }
}

async function selectExecution(executionId, quiet = false) {
  selectedExecutionId.value = executionId
  loading.execution = true
  executionManifest.value = null
  executionCheckpoints.value = []
  try {
    const params = { execution_id: executionId }
    const [detail, metrics, logs, runs, checkpoints] = await Promise.all([
      apiGet(API_ROUTES.ALGORITHM_PLATFORM_EXECUTION, { params }),
      apiGet(API_ROUTES.ALGORITHM_PLATFORM_EXECUTION_METRICS, { params }),
      apiGet(API_ROUTES.ALGORITHM_PLATFORM_EXECUTION_LOGS, { params }),
      apiGet(API_ROUTES.ALGORITHM_PLATFORM_EXECUTION_RUNS, { params }),
      apiGet(API_ROUTES.ALGORITHM_PLATFORM_CHECKPOINTS, { params }),
    ])
    selectedExecutionPayload.value = detail
    metricPayload.value = metrics || { items: [] }
    executionLogs.value = asList(logs?.items)
    executionRuns.value = asList(runs?.items)
    executionCheckpoints.value = asList(checkpoints?.items)
    rememberRuns(executionRuns.value, executionId)
    if (detail.execution?.result_manifest_id) {
      loading.manifest = true
      try {
        executionManifest.value = await apiGet(API_ROUTES.ALGORITHM_PLATFORM_EXECUTION_MANIFEST, { params })
      } catch (error) {
        if (!quiet && ![404, 409].includes(error.status)) setNotice('输出文件清单读取失败', errorText(error), 'error')
      } finally { loading.manifest = false }
    }
  } catch (error) {
    if (!quiet) setNotice('执行详情读取失败', errorText(error), 'error')
  } finally { loading.execution = false }
}

async function cancelExecution() {
  if (!selectedExecutionId.value || !canCancelExecution.value) return
  loading.cancel = true
  try {
    await apiPost(API_ROUTES.ALGORITHM_PLATFORM_EXECUTION_CANCEL, {}, { params: { execution_id: selectedExecutionId.value } })
    setNotice('已请求取消', `${selectedExecutionId.value} 将在安全检查点结束。`, 'success')
    await loadExecutions()
    await selectExecution(selectedExecutionId.value, true)
  } catch (error) { setNotice('取消执行失败', errorText(error), 'error') }
  finally { loading.cancel = false }
}

function checkpointDownloadUrl(checkpoint) {
  return getApiUrl(API_ROUTES.ALGORITHM_PLATFORM_CHECKPOINT, {
    execution_id: selectedExecutionId.value, run_id: checkpoint.run_id,
    name: checkpoint.name, workspace_execution_id: checkpoint.workspace_execution_id,
  })
}

async function useCheckpoint(checkpoint, purpose) {
  const revision = ++configRevision
  const source = selectedExecutionPayload.value.experiment.spec
  try {
    const artifact = await apiPost(API_ROUTES.ALGORITHM_PLATFORM_CHECKPOINT_EXPORT, {}, { params: {
      execution_id: selectedExecutionId.value, run_id: checkpoint.run_id,
      name: checkpoint.name, workspace_execution_id: checkpoint.workspace_execution_id,
    } })
    if (revision !== configRevision) return
    if (purpose === 'test') {
      fillArtifactIntoTest(artifact)
      return
    }
    const config = JSON.parse(JSON.stringify(source))
    config.purpose = 'train'
    delete config.tuning
    config.repetitions = 1
    config.scenarios = [artifact.metadata.training_scenario]
    const original = config.algorithms.find(item => item.id === artifact.metadata.algorithm_id)
    original.interface = 'trainable'
    original.parameters = { ...artifact.metadata.parameters }
    original.input_artifacts = [artifactRefValue(artifact)]
    config.algorithms = [original]
    renewExperimentId(config)
    configText.value = JSON.stringify(config, null, 2)
    markConfigDirty()
    activeTab.value = 'configure'
    configInputEpoch.value += 1
    setNotice('Checkpoint 已用于继续训练', `已完成 ${checkpoint.completed_episodes} 轮、${checkpoint.total_steps} 步。请将训练总轮数设为大于已完成轮数，再提交。`, 'success')
  } catch (error) { if (revision !== configRevision) return; setNotice('Checkpoint 加载失败', errorText(error), 'error') }
}

async function copyArtifactRef(artifact) {
  try {
    await navigator.clipboard.writeText(JSON.stringify(artifactRefValue(artifact), null, 2))
    setNotice('文件引用已复制', artifact.digest, 'success')
  } catch (error) { setNotice('复制失败', errorText(error), 'error') }
}

async function fillArtifactIntoBranch(artifact) {
  const compatible = branchCompatibleAlgorithm(artifact)
  branchForm.algorithm_key = `${compatible.algorithm_id}@${compatible.version}`
  applyBranchAlgorithmDefaults(false)
  branchForm.input_artifacts = JSON.stringify([artifactRefValue(artifact)], null, 2)
  branchArtifactsPinned.value = true
  activeTab.value = 'replay'
  replayExecutionId.value = artifact.execution_id || selectedExecutionId.value
  await loadReplayRuns(true)
  if (artifact.run_id && replayRuns.value.some(run => run.run_id === artifact.run_id)) replayRunId.value = artifact.run_id
  setNotice('输出文件已填入分支', `已选择 ${compatible.name || compatible.algorithm_id}，加载源运行单元后即可创建分支。`, 'success')
}

function fillArtifactIntoTest(artifact) {
  const algorithm = testCompatibleAlgorithm(artifact)
  const config = JSON.parse(JSON.stringify(testTemplate))
  const properties = algorithm.parameter_schema?.properties || {}
  const parameters = Object.fromEntries(Object.entries(properties).filter(([, definition]) => definition.default !== undefined).map(([name, definition]) => [name, definition.default]))
  config.algorithms = [{
    id: algorithm.algorithm_id,
    version: algorithm.version,
    interface: 'online',
    budget: { max_steps: 5000 },
    parameters,
    input_artifacts: [artifactRefValue(artifact)],
  }]
  renewExperimentId(config)
  configText.value = JSON.stringify(config, null, 2)
  markConfigDirty()
  activeTab.value = 'configure'
  configInputEpoch.value += 1
  setNotice('模型已用于测试', `已选择 ${algorithm.name || algorithm.algorithm_id} 和该模型文件，请确认测试数据集后开始测试。`, 'success')
}

async function loadComparison() {
  loading.comparison = true
  comparisonError.value = ''
  datasetComparisonPayload.value = null
  try {
    datasetComparisonPayload.value = await apiGet(API_ROUTES.ALGORITHM_PLATFORM_DATASET_RESULTS, { params: { dataset_id: comparisonDatasetId.value } })
  } catch (error) { comparisonError.value = errorText(error) }
  finally { loading.comparison = false }
}

async function loadReplayDataset() {
  replayDatasetDetail.value = null
  replayDatasetResults.value = []
  replayInstanceId.value = ''
  replayDatasetRunKey.value = ''
  replayExecutionId.value = ''
  replayRunId.value = ''
  replayRuns.value = []
  replayResponse.value = null
  replayError.value = ''
  if (!replayDatasetId.value) return
  loading.replay = true
  try {
    replayDatasetDetail.value = await loadDatasetDetail(replayDatasetId.value)
    replayInstanceId.value = replayAvailableInstances.value[0]?.instance_id || ''
    const results = await apiGet(API_ROUTES.ALGORITHM_PLATFORM_DATASET_RESULTS, { params: { dataset_id: replayDatasetId.value } })
    replayDatasetResults.value = asList(results.items)
    selectFirstReplayAlgorithm()
  } catch (error) { replayError.value = errorText(error) }
  finally { loading.replay = false }
}

function selectFirstReplayAlgorithm() {
  replayDatasetRunKey.value = replayDatasetAlgorithms.value[0]?.key || ''
  selectReplayDatasetRun()
}

function selectReplayDatasetRun() {
  const item = replayDatasetAlgorithms.value.find(run => run.key === replayDatasetRunKey.value)
  replayResponse.value = null
  if (!item) {
    replayExecutionId.value = ''
    replayRunId.value = ''
    replayRuns.value = []
    return
  }
  replayExecutionId.value = item.execution_id
  replayRunId.value = item.run_id
  replayRuns.value = [item]
  rememberRuns(replayRuns.value, item.execution_id)
}

async function loadReplayRuns(preserveBranchInput = branchArtifactsPinned.value) {
  replayDatasetId.value = ''
  replayDatasetDetail.value = null
  replayDatasetResults.value = []
  replayInstanceId.value = ''
  replayDatasetRunKey.value = ''
  replayRunId.value = ''
  replayResponse.value = null
  reproductionReport.value = null
  branchReport.value = null
  diffReport.value = null
  viewingBranchTrace.value = false
  if (!preserveBranchInput) branchArtifactsPinned.value = false
  replayError.value = ''
  replayRuns.value = []
  if (!replayExecutionId.value) return
  try {
    const data = await apiGet(API_ROUTES.ALGORITHM_PLATFORM_EXECUTION_RUNS, { params: { execution_id: replayExecutionId.value } })
    replayRuns.value = asList(data.items)
    rememberRuns(replayRuns.value, replayExecutionId.value)
    if (replayRuns.value.length) replayRunId.value = replayRuns.value[0].run_id
  } catch (error) { replayError.value = errorText(error) }
}

async function loadReplay() {
  stopReplayPlayback()
  loading.replay = true
  replayError.value = ''
  replayResponse.value = null
  reproductionReport.value = null
  branchReport.value = null
  viewingBranchTrace.value = false
  replayItemIndex.value = 0
  replayFrameIndex.value = 0
  try {
    replayResponse.value = await apiGet(API_ROUTES.ALGORITHM_PLATFORM_RUN_REPLAY, {
      params: { run_id: replayRunId.value, execution_id: replayExecutionId.value, mode: replayMode.value },
      timeout: 120000,
    })
    const config = replayResponse.value.factory_config || replayResponse.value.trace?.metadata?.factory_config
    if (config) factoryStore.loadConfigFromFile(config)
    syncFramesToFactory()
    initializeBranchForm()
  } catch (error) { replayError.value = errorText(error) }
  finally { loading.replay = false }
}

function replayIdentifier(prefix) {
  const timestamp = new Date().toISOString().replace(/[-:.TZ]/g, '').slice(0, 14)
  return `replay-${prefix}-${timestamp}-${Math.random().toString(36).slice(2, 10)}`
}

function initializeBranchForm() {
  branchForm.snapshot_sequence = replaySnapshots.value[0]?.after_sequence ?? null
  branchForm.execution_id = replayIdentifier('branch')
  branchForm.run_id = replayIdentifier('run')
  if (!branchArtifactsPinned.value) branchForm.input_artifacts = '[]'
  if ((!selectedBranchAlgorithm.value || (!branchArtifactsPinned.value && minimumInputArtifacts(selectedBranchAlgorithm.value, 'online') > 0)) && onlineAlgorithms.value.length) {
    const runStarted = asList(replayResponse.value?.trace?.events).find(event => event.event_type === 'run_started')
    const sourceAlgorithmId = runStarted?.payload?.algorithm_id
    const directAlgorithms = onlineAlgorithms.value.filter(algorithm => minimumInputArtifacts(algorithm, 'online') === 0)
    const alternative = directAlgorithms.find(algorithm => algorithm.algorithm_id !== sourceAlgorithmId) || directAlgorithms[0] || onlineAlgorithms.value.find(algorithm => algorithm.algorithm_id !== sourceAlgorithmId) || onlineAlgorithms.value[0]
    branchForm.algorithm_key = `${alternative.algorithm_id}@${alternative.version}`
  }
  applyBranchAlgorithmDefaults(!branchArtifactsPinned.value)
}

function applyBranchAlgorithmDefaults(resetArtifacts) {
  branchInputEpoch.value += 1
  const schema = selectedBranchAlgorithm.value?.parameter_schema
  const properties = schema?.properties && typeof schema.properties === 'object' ? schema.properties : {}
  branchForm.parameters = JSON.stringify(Object.fromEntries(Object.entries(properties).filter(([, definition]) => definition.default !== undefined).map(([name, definition]) => [name, definition.default])), null, 2)
  if (resetArtifacts) {
    branchForm.input_artifacts = '[]'
    branchArtifactsPinned.value = false
  }
}

async function reproduceReplay() {
  loading.reproduce = true
  reproductionReport.value = null
  try {
    reproductionReport.value = await apiPost(API_ROUTES.ALGORITHM_PLATFORM_REPLAY_REPRODUCE, {
      policy: {
        float_tolerance: diffForm.float_tolerance,
        simulation_time_tolerance: diffForm.float_tolerance,
        compare_sequence_numbers: true,
        compare_payloads: true,
        compare_state_hashes: true,
      },
    }, {
      params: { execution_id: replayExecutionId.value, run_id: replayRunId.value },
      timeout: 15 * 60 * 1000,
    })
    if (reproductionReport.value?.trace?.run_id && reproductionReport.value.execution_id) {
      rememberRuns([{ run_id: reproductionReport.value.trace.run_id }], reproductionReport.value.execution_id)
      diffForm.left_key = `${replayExecutionId.value}::${replayRunId.value}`
      diffForm.right_key = `${reproductionReport.value.execution_id}::${reproductionReport.value.trace.run_id}`
    }
    if (reproductionRunFailed.value) {
      setNotice('确定性复现运行失败', reproductionReport.value.result?.failure?.message || statusLabel(reproductionReport.value.result?.status), 'error')
    } else {
      setNotice('确定性复现完成', reproductionEquivalent.value ? '新回放记录与源运行单元一致。' : '已定位首个复现差异。', reproductionEquivalent.value ? 'success' : 'error')
    }
  } catch (error) { setNotice('确定性复现失败', errorText(error), 'error') }
  finally { loading.reproduce = false }
}

async function branchReplay() {
  loading.branch = true
  branchReport.value = null
  try {
    const algorithm = selectedBranchAlgorithm.value
    branchReport.value = await apiPost(API_ROUTES.ALGORITHM_PLATFORM_REPLAY_BRANCH, {
      execution_id: branchForm.execution_id,
      run_id: branchForm.run_id,
      snapshot_sequence: branchForm.snapshot_sequence,
      algorithm: {
        id: algorithm.algorithm_id,
        version: algorithm.version,
        interface: 'online',
        parameters: JSON.parse(branchForm.parameters || '{}'),
        input_artifacts: branchInputArtifacts.value,
      },
      metadata: {
        created_from_workbench: true,
        source_view: activeReplayView.value,
      },
    }, {
      params: { execution_id: replayExecutionId.value, run_id: replayRunId.value },
      timeout: 15 * 60 * 1000,
    })
    diffForm.left_key = `${replayExecutionId.value}::${replayRunId.value}`
    diffForm.right_key = `${branchReport.value.execution_id}::${branchReport.value.trace.run_id}`
    if (branchRunFailed.value) {
      setNotice('分支运行失败', branchReport.value.result?.failure?.message || statusLabel(branchReport.value.result?.status), 'error')
    } else {
      setNotice('分支回放完成', `${branchReport.value.trace?.run_id || branchForm.run_id} 已从快照继续运行。`, 'success')
    }
  } catch (error) { setNotice('分支回放失败', errorText(error), 'error') }
  finally { loading.branch = false }
}

async function openBranchTrace() {
  stopReplayPlayback()
  loading.replay = true
  try {
    const data = await apiGet(API_ROUTES.ALGORITHM_PLATFORM_RUN_REPLAY, {
      params: {
        run_id: branchReport.value.trace.run_id,
        execution_id: branchReport.value.execution_id,
        mode: 'full',
      },
      timeout: 120000,
    })
    replayMode.value = 'full'
    replayExecutionId.value = branchReport.value.execution_id
    replayRunId.value = branchReport.value.trace.run_id
    replayRuns.value = [{
      run_id: replayRunId.value,
      execution_id: replayExecutionId.value,
      route_execution_id: replayExecutionId.value,
      status: branchReport.value.result?.status || 'succeeded',
    }]
    rememberRuns(replayRuns.value, replayExecutionId.value)
    replayResponse.value = data
    replayItemIndex.value = 0
    replayFrameIndex.value = 0
    viewingBranchTrace.value = true
    if (data.factory_config) factoryStore.loadConfigFromFile(data.factory_config)
    syncFramesToFactory()
    initializeBranchForm()
  } catch (error) { setNotice('分支回放记录读取失败', errorText(error), 'error') }
  finally { loading.replay = false }
}

function extractFrame(item) {
  const related = asList(item?.related_events).find(event => event?.payload?.frame || event?.payload?.payload?.frame)
  return item?.frame || item?.payload?.frame || item?.payload?.payload?.frame || item?.payload?.observation?.frame || item?.event?.payload?.frame || item?.event?.payload?.payload?.frame || item?.state?.frame || item?.observation?.payload?.observation?.frame || item?.state_change?.payload?.frame || related?.payload?.frame || related?.payload?.payload?.frame || null
}

function syncFramesToFactory() {
  if (!replayFrames.value.length) return
  factoryStore.loadData(replayFrames.value.map(item => item.frame))
  factoryStore.isLiveMode = false
  factoryStore.isPlaying = false
  factoryStore.setIndex(replayFrameIndex.value)
}

function seekFrame(index) {
  stopReplayPlayback()
  replayFrameIndex.value = Math.max(0, Math.min(index, replayFrames.value.length - 1))
}

function toggleReplayPlayback() {
  if (replayPlaying.value) { stopReplayPlayback(); return }
  if (!replayFrames.value.length) return
  if (replayFrameIndex.value >= replayFrames.value.length - 1) replayFrameIndex.value = 0
  replayPlaying.value = true
  replayTimer = window.setInterval(() => {
    if (replayFrameIndex.value >= replayFrames.value.length - 1) { stopReplayPlayback(); return }
    replayFrameIndex.value += 1
  }, 500)
}

function stopReplayPlayback() {
  if (replayTimer) window.clearInterval(replayTimer)
  replayTimer = null
  replayPlaying.value = false
}

function replayItemKey(item, index) { return `${item.request_id || item.sequence || item.event?.sequence || index}-${index}` }
function replayItemType(item) { return item.request_id ? `决策 ${item.request_id}` : item.event_type || item.event?.event_type || item.type || '记录项' }
function replayItemCaption(item) { return item.state_hash || item.event?.state_hash || item.request?.state_hash || item.payload?.message || '查看结构化证据' }

async function loadReplayDiff() {
  loading.diff = true
  diffReport.value = null
  try {
    const left = allKnownRuns.value.find(run => run.key === diffForm.left_key)
    const right = allKnownRuns.value.find(run => run.key === diffForm.right_key)
    diffReport.value = await apiPost(API_ROUTES.ALGORITHM_PLATFORM_REPLAY_DIFF, {
      left_run_id: left.run_id,
      right_run_id: right.run_id,
      mode: replayMode.value,
      left_execution_id: left.execution_id,
      right_execution_id: right.execution_id,
      policy: { float_tolerance: diffForm.float_tolerance, simulation_time_tolerance: diffForm.float_tolerance },
    }, { timeout: 120000 })
  } catch (error) { setNotice('差异诊断失败', errorText(error), 'error') }
  finally { loading.diff = false }
}

watch(replayFrameIndex, index => {
  if (replayFrames.value.length) {
    factoryStore.setIndex(index)
    replayItemIndex.value = replayFrames.value[index]?.index ?? replayItemIndex.value
  }
})

watch(replayItemIndex, index => {
  const frameIndex = replayFrames.value.findIndex(entry => entry.index === index)
  if (frameIndex >= 0) replayFrameIndex.value = frameIndex
})

onMounted(async () => {
  const initialText = configText.value
  const revision = configRevision
  await Promise.all([loadCatalog(), loadDatasets(), loadExperiments(), loadExecutions()])
  try {
    const config = JSON.parse(initialText)
    await resolveDatasetReferences(config)
    if (configRevision === revision) configText.value = JSON.stringify(config, null, 2)
  } catch (error) { if (configRevision === revision) setNotice('初始数据集无法载入', errorText(error), 'error') }
  pollTimer = window.setInterval(async () => {
    await loadExecutions()
    if (activeTab.value === 'executions' && selectedExecutionId.value && ['draft', 'compiled', 'pending', 'queued', 'preparing', 'running', 'cancel_requested'].includes(selectedExecution.value?.status)) {
      await selectExecution(selectedExecutionId.value, true)
    }
  }, 4000)
})

onBeforeUnmount(() => {
  if (pollTimer) window.clearInterval(pollTimer)
  stopReplayPlayback()
  factoryStore.reset()
})
</script>

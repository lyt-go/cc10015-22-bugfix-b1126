<template>
  <section class="page" :data-module="meta.key">
    <header class="page-head">
      <div>
        <h2>{{ meta.name }}</h2>
        <p class="page-desc">{{ meta.desc }}</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">
          登记{{ meta.entity }}
        </button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">
          导出{{ meta.name }}清单
        </button>
      </div>
    </header>

    <!-- 当前对外公布、可用于新布设的唯一生效计划回执 -->
    <div v-if="meta.key === 'station_deploy'" class="receipt-banner" data-testid="active-receipt-banner">
      <template v-if="activeReceipt">
        <strong>当前生效计划回执：{{ activeReceipt.receipt_no }}</strong>
        <span>航次 {{ activeReceipt.cruise_code }} · 第 {{ activeReceipt.review_round }} 轮 · 版本 {{ activeReceipt.plan_version }}</span>
        <span>签发时间 {{ activeReceipt.issued_at }}</span>
      </template>
      <template v-else>
        <strong>当前无生效中的航次计划回执</strong>
        <span>计划变更后需重新审阅批准，新一轮批准前不能进行新布设</span>
      </template>
      <label class="receipt-input">
        <span>确认站位使用的回执编号</span>
        <input v-model="receiptInput" data-testid="receipt-no-input" placeholder="留空则使用当前生效回执" />
      </label>
    </div>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <p class="status-legend">
      <span v-for="item in statusSummary" :key="item.status" class="legend-item">
        {{ item.status }}：{{ item.count }}
      </span>
    </p>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in meta.fields" :key="column">{{ column }}</th>
          <th>当前状态</th>
          <th v-if="extraColumns.length">审阅轮次/生效回执</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="column in meta.fields" :key="column">{{ row[column] ?? '—' }}</td>
          <td>{{ row.status }}</td>
          <td v-if="extraColumns.length" class="receipt-cell">
            <template v-if="row.receipt_no">
              <div>第 {{ row.review_round ?? '—' }} 轮</div>
              <div class="receipt-no">{{ row.receipt_no }}</div>
            </template>
            <template v-else-if="meta.key === 'cruise_plan' && row.review_round">
              <div>第 {{ row.review_round }} 轮</div>
              <div class="receipt-no">{{ row.active_receipt_no ? `回执 ${row.active_receipt_no}` : '回执已停用，待重审' }}</div>
            </template>
            <span v-else>—</span>
          </td>
          <td class="row-actions">
            <button v-for="action in meta.actions" :key="action" class="link" type="button"
              :data-testid="`action-${row.id}-${action}`" @click="runAction(action, row)">{{ action }}</button>
            <button v-if="meta.key === 'station_deploy'" class="link" type="button"
              data-testid="trace-station" @click="openTrace(row)">回执追溯</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="meta.fields.length + (extraColumns.length ? 3 : 2)" class="empty-state">
            暂无{{ meta.name }}数据，可先登记{{ meta.entity }}
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记{{ meta.entity }}</h3>
        <label v-for="field in meta.fields" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <!-- 站位详情：航次生效回执关联的本轮批准请求与沿用/重审结论 -->
    <div v-if="trace" class="modal-backdrop" data-testid="trace-modal">
      <div class="modal">
        <h3>站位 {{ trace.station_code }} 回执追溯</h3>
        <dl class="trace-list">
          <div><dt>站位状态</dt><dd>{{ trace.status }}</dd></div>
          <div><dt>当前坐标</dt><dd>{{ trace.current_coordinates ?? '—' }}</dd></div>
          <div><dt>批准当时坐标</dt><dd>{{ trace.approved_coordinates ?? trace.frozen_coordinates ?? '—' }}</dd></div>
          <div><dt>生效回执</dt><dd>{{ trace.receipt_no ?? '未关联' }}</dd></div>
          <div><dt>归属航次</dt><dd>{{ trace.cruise_code ?? '—' }}</dd></div>
          <div><dt>归属审阅轮次</dt><dd>{{ trace.review_round ? `第 ${trace.review_round} 轮（${trace.plan_version ?? '—'}）` : '—' }}</dd></div>
          <div><dt>本次布设所用计划</dt><dd>{{ trace.plan_snapshot ? `${trace.plan_snapshot['调查海域'] ?? ''} ${trace.plan_snapshot['起止日期'] ?? ''}`.trim() || '已随回执存档' : '—' }}</dd></div>
          <div><dt>回执状态</dt><dd>{{ trace.receipt_status ?? '—' }}</dd></div>
          <div><dt>回执签发时间</dt><dd>{{ trace.receipt_issued_at ?? '—' }}</dd></div>
          <div>
            <dt>沿用/重审</dt>
            <dd :class="trace.need_re_review ? 're-review' : 'follow'">{{ trace.eligibility }}</dd>
          </div>
          <div><dt>下一步</dt><dd>{{ trace.next_step }}</dd></div>
          <div class="trace-reason"><dt>判定依据</dt><dd>{{ trace.reason }}</dd></div>
        </dl>
        <div class="page-actions">
          <button class="btn primary" type="button" @click="trace = null">关闭</button>
        </div>
      </div>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条{{ meta.name }}记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  createEntry,
  downloadEntries,
  fetchActiveReceipt,
  fetchStationTrace,
  listEntries,
  moduleMeta,
  runAction as applyAction,
  type PlanReceipt,
  type StationTrace,
} from '@/api/backend-service'
import type { EntryRow } from '@/data/types'

const props = defineProps<{ moduleKey: string }>()
const meta = moduleMeta(props.moduleKey)
const rows = ref<EntryRow[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = meta.fields.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})
const activeReceipt = ref<PlanReceipt | null>(null)
const trace = ref<StationTrace | null>(null)
// 船长确认站位时出示的航次生效回执编号，留空则使用当前生效回执
const receiptInput = ref('')

const extraColumns = computed(() =>
  props.moduleKey === 'station_deploy' || props.moduleKey === 'cruise_plan' ? ['receipt'] : [],
)

const stats = computed(() => [
  { label: meta.metrics[0], value: total.value },
  { label: meta.metrics[1], value: rows.value.filter((r) => r.status === meta.statuses[1]).length },
  { label: meta.metrics[2], value: rows.value.filter((r) => String(r.status) === meta.statuses[meta.statuses.length - 1]).length },
])

const statusSummary = computed(() =>
  meta.statuses.map((s) => ({ status: s, count: rows.value.filter((r) => String(r.status) === s).length })),
)

function resetFilters() { filters.value = {}; void reload() }

async function exportRows() {
  errorMessage.value = ''
  try { await downloadEntries(meta.key, filters.value) }
  catch (e) { errorMessage.value = e instanceof Error ? e.message : '导出失败' }
}

function openCreate() {
  createValues.value = Object.fromEntries(meta.fields.map((f) => [f, '']))
  errorMessage.value = ''; showCreate.value = true
}

async function submitCreate() {
  creating.value = true; errorMessage.value = ''
  try {
    const r = await createEntry(meta.key, createValues.value)
    if (!r.ok) throw new Error(r.message)
    showCreate.value = false; await reload()
  } catch (e) { errorMessage.value = e instanceof Error ? e.message : '登记失败' }
  finally { creating.value = false }
}

async function runAction(action: string, row: EntryRow) {
  errorMessage.value = ''
  try {
    // 确认站位必须基于航次生效回执核验，旧轮/跨航次回执会被后端拒绝
    const receiptNo =
      meta.key === 'station_deploy' && action === '确认站位'
        ? receiptInput.value.trim() || activeReceipt.value?.receipt_no
        : undefined
    const r = await applyAction(meta.key, Number(row.id), action, receiptNo)
    if (!r.ok) throw new Error(r.message)
    await reload()
    await loadActiveReceipt()
  } catch (e) { errorMessage.value = e instanceof Error ? e.message : '操作失败' }
}

async function openTrace(row: EntryRow) {
  errorMessage.value = ''
  try {
    trace.value = await fetchStationTrace(Number(row.id))
  } catch (e) { errorMessage.value = e instanceof Error ? e.message : '追溯查询失败' }
}

async function loadActiveReceipt() {
  if (props.moduleKey !== 'station_deploy') return
  try {
    const payload = await fetchActiveReceipt()
    activeReceipt.value = payload.exists ? payload.receipt : null
    receiptInput.value = activeReceipt.value?.receipt_no ?? ''
  } catch (e) {
    errorMessage.value = e instanceof Error ? e.message : '生效回执读取失败'
  }
}

async function reload() {
  errorMessage.value = ''
  try {
    const payload = await listEntries(meta.key, filters.value)
    rows.value = payload.items; total.value = payload.total
  } catch (e) { errorMessage.value = e instanceof Error ? e.message : '业务列表读取失败' }
}

onMounted(() => {
  void reload()
  void loadActiveReceipt()
})
</script>

<style scoped>
.modal-backdrop { position:fixed; inset:0; background:rgba(0,0,0,.35); display:flex; align-items:center; justify-content:center; z-index:100; }
.modal { background:#fff; border-radius:8px; padding:24px; width:480px; max-height:80vh; overflow-y:auto; }
.create-field { display:flex; flex-direction:column; gap:4px; margin-bottom:12px; }
.create-field span { font-size:13px; color:var(--text-secondary,#4a5568); }
.create-field input { padding:6px 8px; border:1px solid var(--border-color,#e2e8f0); border-radius:4px; font-size:13px; }
.receipt-banner { display:flex; flex-wrap:wrap; gap:12px; align-items:center; padding:10px 14px; margin-bottom:12px; border:1px solid #b3d4ff; background:#f0f7ff; border-radius:6px; font-size:13px; }
.receipt-banner strong { color:#1d4ed8; }
.receipt-input { display:flex; align-items:center; gap:6px; margin-left:auto; font-size:12px; }
.receipt-input input { padding:5px 8px; border:1px solid #b3d4ff; border-radius:4px; min-width:180px; }
.receipt-cell { font-size:12px; color:var(--text-secondary,#4a5568); white-space:nowrap; }
.receipt-cell .receipt-no { color:#1d4ed8; }
.trace-list { margin:0 0 16px; display:grid; grid-template-columns:1fr; gap:8px; }
.trace-list div { display:flex; justify-content:space-between; gap:12px; font-size:13px; }
.trace-list dt { color:var(--text-secondary,#4a5568); }
.trace-list dd { margin:0; text-align:right; }
.trace-reason dd { max-width:300px; }
.follow { color:#15803d; font-weight:600; }
.re-review { color:#b45309; font-weight:600; }
</style>

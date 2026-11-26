<template>
  <section class="page" data-module="row_quarantine_review">
    <header class="page-head">
      <div>
        <h2>坏行复核清单</h2>
        <p class="page-desc">
          按批次和失败字段拉取隔离原行，领办范围由服务端按证据判定；同一原行的不同
          失败字段可以由不同复核员分别领办，修订值写回解析批次后按实际行数复验输出契约。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" :disabled="loading" @click="loadChecklist">刷新清单</button>
        <button class="btn" type="button" @click="loadContract">读取契约复验</button>
      </div>
    </header>

    <form class="filter-bar" @submit.prevent="loadChecklist">
      <label class="filter-item">
        <span>关联批次</span>
        <input v-model="batch" data-testid="review-batch" placeholder="按批次检索，如 FI-20260903001" />
      </label>
      <label class="filter-item">
        <span>失败字段（逗号分隔，可空）</span>
        <input v-model="fieldFilter" data-testid="review-fields" placeholder="如 postal_code,amount" />
      </label>
      <label class="filter-item">
        <span>复核人</span>
        <input v-model="reviewer" data-testid="review-reviewer" placeholder="如 reviewer-02" />
      </label>
      <button class="btn" data-testid="review-query" type="submit">查询清单</button>
    </form>

    <p v-if="errorMessage" class="error-text" data-testid="review-error">{{ errorMessage }}</p>
    <p v-if="notice" class="notice-text" data-testid="review-notice">{{ notice }}</p>

    <table class="data-table">
      <thead>
        <tr>
          <th>原始行号</th>
          <th>失败字段</th>
          <th>当前状态</th>
          <th>证据数</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <template v-for="item in checklist" :key="item['隔离编号']">
          <tr :data-testid="`checklist-${item['原始行号']}`" :data-status="item.status">
            <td>
              <button class="link" type="button" @click="toggle(item['隔离编号'])">
                {{ expanded[item['隔离编号']] ? '▾' : '▸' }} 第 {{ item['原始行号'] }} 行
              </button>
            </td>
            <td>{{ item['失败字段'].join('、') || '—' }}</td>
            <td>{{ item.status }}</td>
            <td>{{ item['证据'].length }}</td>
            <td class="row-actions">
              <button
                v-for="field in item['失败字段']"
                :key="field"
                class="link"
                type="button"
                :data-testid="`claim-${item['原始行号']}-${field}`"
                @click="claim(item['隔离编号'], [field])"
              >
                领办{{ field }}
              </button>
              <button class="link" type="button" :data-testid="`evidence-${item['原始行号']}`"
                      @click="loadEvidence(item['原始行号'])">
                回查证据
              </button>
            </td>
          </tr>
          <tr v-if="expanded[item['隔离编号']]" :data-testid="`evidence-panel-${item['原始行号']}`">
            <td colspan="5">
              <div class="evidence-panel">
                <div v-for="evidence in item['证据']" :key="evidence['证据序号']" class="evidence-item">
                  <p class="evidence-head">
                    证据 #{{ evidence['证据序号'] }} · {{ evidence['失败字段'].join('、') }}
                    · {{ evidence['证据来源'] }} · {{ evidence['登记时间'] }}
                  </p>
                  <p class="evidence-reason">{{ evidence['失败原因'] }}</p>
                  <pre class="evidence-text">{{ evidence['原始行文本'] }}</pre>
                </div>
                <form class="revise-form" @submit.prevent="revise(item)">
                  <label v-for="field in item['失败字段']" :key="field" class="filter-item">
                    <span>{{ field }} 修订值</span>
                    <input v-model="revised[item['隔离编号']][field]"
                           :data-testid="`revise-${item['原始行号']}-${field}`" />
                  </label>
                  <button class="btn primary" type="submit" :data-testid="`revise-submit-${item['原始行号']}`">
                    写回解析批次
                  </button>
                </form>
              </div>
            </td>
          </tr>
        </template>
        <tr v-if="!checklist.length">
          <td colspan="5" class="empty-state">该批次没有匹配的坏行，或尚未查询</td>
        </tr>
      </tbody>
    </table>

    <section v-if="receipt" class="receipt-card" data-testid="change-receipt">
      <h3>变更回执 · {{ receipt['隔离编号'] }}</h3>
      <p>当前状态：{{ receipt.status }}</p>
      <p>已填修订值：{{ JSON.stringify(receipt['已填修订值']) }}</p>
      <p>本申请还可提交：{{ receipt['可提交字段'].join('、') || '无' }}</p>
      <ul>
        <li v-for="line in receipt['需重新领取']" :key="line">{{ line }}</li>
      </ul>
    </section>

    <section v-if="trail" class="receipt-card" data-testid="evidence-trail">
      <h3>原行回查 · 第 {{ trail['原始行号'] }} 行</h3>
      <p>失败字段：{{ trail['失败字段'].join('、') }}</p>
      <div v-for="evidence in trail['证据']" :key="evidence['证据序号']" class="evidence-item">
        <p class="evidence-head">
          证据 #{{ evidence['证据序号'] }} · {{ evidence['失败字段'].join('、') }}
          · {{ evidence['证据来源'] }} · {{ evidence['登记时间'] }}
        </p>
        <p class="evidence-reason">{{ evidence['失败原因'] }}</p>
        <pre class="evidence-text">{{ evidence['原始行文本'] }}</pre>
      </div>
    </section>

    <section v-if="contract" class="receipt-card" data-testid="contract-result">
      <h3>输出契约复验 · {{ contract['契约编号'] }}</h3>
      <p>契约状态：{{ contract.status }}</p>
      <p>复验实际行数：{{ contract['实际行数'] ?? '—' }}</p>
      <p>校验结论：{{ contract['契约校验结论'] }}</p>
    </section>

    <footer class="page-foot">
      <span>共 {{ checklist.length }} 行待复核</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'

import { fetchJson, request } from '@/api/client'

type Evidence = {
  证据序号: number
  失败字段: string[]
  失败原因: string
  原始行文本: string
  证据来源: string
  登记时间: string
}

type ChecklistItem = {
  隔离编号: string
  关联批次: string
  原始行号: string
  失败字段: string[]
  失败原因: string
  原始行文本: string
  status: string
  证据: Evidence[]
}

type Receipt = {
  隔离编号: string
  status: string
  已填修订值: Record<string, string>
  可提交字段: string[]
  需重新领取: string[]
}

type EvidenceTrail = {
  原始行号: string
  失败字段: string[]
  证据: Evidence[]
}

type ContractRow = {
  契约编号: string
  status: string
  实际行数?: string
  契约校验结论: string
}

const batch = ref('FI-20260903001')
const fieldFilter = ref('')
const reviewer = ref('reviewer-02')
const checklist = ref<ChecklistItem[]>([])
const expanded = reactive<Record<string, boolean>>({})
const revised = reactive<Record<string, Record<string, string>>>({})
const receipt = ref<Receipt | null>(null)
const trail = ref<EvidenceTrail | null>(null)
const contract = ref<ContractRow | null>(null)
const loading = ref(false)
const errorMessage = ref('')
const notice = ref('')

function parseFields(): string[] {
  return fieldFilter.value.split(',').map((f) => f.trim()).filter(Boolean)
}

async function loadChecklist() {
  loading.value = true
  errorMessage.value = ''
  try {
    const query = new URLSearchParams({ batch: batch.value })
    if (parseFields().length) query.set('fields', parseFields().join(','))
    const payload = await fetchJson<{ items: ChecklistItem[] }>(
      `/api/row_quarantine/checklist?${query.toString()}`,
    )
    checklist.value = payload.items
    for (const item of payload.items) {
      // 保留已填的修订值，只为本轮新出现的失败字段补输入位。
      const bucket = revised[item['隔离编号']] ?? (revised[item['隔离编号']] = {})
      for (const field of item['失败字段']) {
        if (bucket[field] === undefined) bucket[field] = ''
      }
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '复核清单读取失败'
  } finally {
    loading.value = false
  }
}

function toggle(quarantineNo: string) {
  expanded[quarantineNo] = !expanded[quarantineNo]
}

async function claim(quarantineNo: string, fields: string[]) {
  errorMessage.value = ''
  notice.value = ''
  const entryId = await resolveEntryId(quarantineNo)
  if (entryId === null) return
  const response = await request(`/api/row_quarantine/${entryId}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action: '领办', reviewer: reviewer.value, fields, remark: new Date().toISOString() }),
  })
  const result = await response.json()
  if (result.ok) {
    notice.value = result.message
    await refreshReceipt(quarantineNo)
    await loadChecklist()
  } else {
    errorMessage.value = result.message
  }
}

async function resolveEntryId(quarantineNo: string): Promise<number | null> {
  try {
    const query = new URLSearchParams({ keyword: quarantineNo, size: '200' })
    const payload = await fetchJson<{ items: { id: number; 隔离编号: string }[] }>(
      `/api/row_quarantine?${query.toString()}`,
    )
    const hit = payload.items.find((row) => row['隔离编号'] === quarantineNo)
    return hit ? Number(hit.id) : null
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '隔离行定位失败'
    return null
  }
}

async function revise(item: ChecklistItem) {
  errorMessage.value = ''
  notice.value = ''
  const values = Object.fromEntries(
    Object.entries(revised[item['隔离编号']] ?? {}).filter(([, value]) => value !== ''),
  )
  if (!Object.keys(values).length) {
    errorMessage.value = '请至少填写一个失败字段的修订值'
    return
  }
  const entryId = await resolveEntryId(item['隔离编号'])
  if (entryId === null) return
  const response = await request(`/api/row_quarantine/${entryId}/actions`, {
    method: 'POST',
    body: JSON.stringify({
      action: '回补批次',
      reviewer: reviewer.value,
      revised_values: values,
      remark: new Date().toISOString(),
    }),
  })
  const result = await response.json()
  if (result.ok) {
    notice.value = result.message
    await refreshReceipt(item['隔离编号'])
    await loadChecklist()
    await loadContract()
  } else {
    errorMessage.value = result.message
  }
}

async function refreshReceipt(quarantineNo: string) {
  try {
    receipt.value = await fetchJson<Receipt>(
      `/api/row_quarantine/${encodeURIComponent(quarantineNo)}/receipt`,
    )
  } catch {
    receipt.value = null
  }
}

async function loadEvidence(rowNum: string) {
  errorMessage.value = ''
  try {
    const query = new URLSearchParams({ batch: batch.value, row: rowNum })
    trail.value = await fetchJson<EvidenceTrail>(
      `/api/row_quarantine/evidence?${query.toString()}`,
    )
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '证据回查失败'
  }
}

async function loadContract() {
  errorMessage.value = ''
  try {
    const query = new URLSearchParams({ keyword: 'OC-005', size: '20' })
    const payload = await fetchJson<{ items: ContractRow[] }>(
      `/api/output_contract?${query.toString()}`,
    )
    contract.value = payload.items.find((row) => row['契约编号'] === 'OC-005') ?? null
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '契约读取失败'
  }
}
</script>

import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, EntryRow, OverviewResult, PageResult } from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
  receiptNo?: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action, receipt_no: receiptNo ?? null }),
  })
  return await response.json()
}

/** 航次生效回执：当前唯一可用于新布设的生效计划。 */
export type PlanReceipt = {
  receipt_no: string
  cruise_plan_id: number
  cruise_code: string
  review_round: number
  plan_version: string
  plan_snapshot: Record<string, string | null>
  station_coordinates: Record<string, string>
  issued_at: string
  status: string
  supersede_reason?: string
}

export async function fetchActiveReceipt(): Promise<{ receipt: PlanReceipt | null; exists: boolean }> {
  const response = await request('/api/plan-receipts/active')
  if (!response.ok) throw new Error('生效回执读取失败')
  return await response.json()
}

export async function fetchReceipt(receiptNo: string): Promise<PlanReceipt> {
  const response = await request(`/api/plan-receipts/${encodeURIComponent(receiptNo)}`)
  if (!response.ok) throw new Error('航次生效回执查询失败')
  return await response.json()
}

/** 站位回执追溯：归属轮次、批准当时坐标、沿用/重审结论。 */
export type StationTrace = {
  station_id: number
  station_code: string
  status: string
  current_coordinates: string
  frozen_coordinates: string | null
  receipt_no: string | null
  cruise_code: string | null
  cruise_plan_id: number | null
  review_round: number | null
  plan_version: string | null
  plan_snapshot: Record<string, string | null> | null
  approved_coordinates: string | null
  receipt_status: string | null
  receipt_issued_at: string | null
  eligibility: string
  need_re_review: boolean
  next_step: string
  reason: string
}

export async function fetchStationTrace(id: number): Promise<StationTrace> {
  const response = await request(`/api/station_deploy/${id}/trace`)
  if (!response.ok) throw new Error('站位回执追溯失败')
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

export async function fetchSnapshot(key: string): Promise<Record<string, any>> {
  const response = await request(`/api/snapshot/${key}`)
  if (!response.ok) throw new Error('跨模块快照读取失败')
  return await response.json()
}

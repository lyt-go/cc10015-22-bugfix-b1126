# -*- coding: utf-8 -*-
"""站位布设领域服务：布设站位需校验航次生效回执、气象海况和人员可用性。

布设前置从“存在任意已批准计划”收紧为“持有当前生效中的航次生效回执”：

- 船长可持回执编号确认站位，系统核验回执是否生效、是否属于本航次；
- 甲航次的批准回执不能替乙航次确认站位，跨航次核验直接拒绝；
- 计划变更后旧回执停用，新一轮批准前无法做新布设；
- 布设成功即把回执轮次与批准当时坐标冻结到站位，已完成作业始终引用该坐标；
- 追溯接口可查站位归属哪轮计划、回执是否仍生效、下一步是否需要重审。
支持事务回滚和重试恢复。
"""
from __future__ import annotations

import time
from typing import Any

from app.services import plan_receipt
from app.services.base import BaseModuleService
from app.store import store


def _now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


class StationDeployService(BaseModuleService):
    MODULE = "station_deploy"
    ENTITY = "站位记录"
    OPERATION_TERM = "确认站位"
    LIST_FIELDS = ["站位编号", "经纬度坐标", "水深数据", "采样类型", "计划作业时间", "实际作业时间", "布设人", "备注说明"]
    REQUIRED_FIELDS = ["站位编号", "经纬度坐标", "水深数据"]
    STATUS_ORDER = ["待布设", "已布设", "已作业", "已跳过"]
    ACTION_RULES = {"确认站位": "已布设", "开始作业": "已作业", "跳过站位": "已跳过"}
    NEGATIVE_ACTIONS = ["跳过站位"]

    def run_action(
        self,
        entry_id: int,
        action: str,
        context: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"

        target = self.ACTION_RULES.get(action)
        if target is None:
            return None, f"动作「{action}」不属于{self.MODULE}可执行范围"

        # 幂等检查：同一站位同一动作只执行一次
        idempotent_key = f"{self.MODULE}:{entry_id}:{action}"
        if store.is_idempotent(idempotent_key):
            return None, f"动作「{action}」已执行，请勿重复操作"

        # 站位作业前置条件：检查是否有恶劣海况
        if action == "开始作业":
            weather_rows = store.rows("weather_obs")
            severe_weather = any(r.get("status") == "恶劣海况" for r in weather_rows)
            if severe_weather:
                return None, "当前存在恶劣海况标记，无法开始站位作业"
            # 检查布设人是否可用（受排班和潜水影响）
            deployer = entry.get("布设人", "")
            if deployer:
                crew_rows = store.find_by("crew_schedule", "队员姓名", deployer)
                for crew in crew_rows:
                    if crew.get("unavailable") or crew.get("diving_aborted"):
                        return None, f"布设人「{deployer}」当前不可用（受排班/潜水中止影响）"

        # 站位布设前置条件：核验航次生效回执（与签发共用锁，串行化批准与布设）
        receipt: dict[str, Any] | None = None
        if action == "确认站位":
            with store.approval_receipt_lock():
                receipt, receipt_error = self._resolve_deployment_receipt(
                    entry, str((context or {}).get("receipt_no") or "").strip()
                )
            if receipt_error:
                return None, receipt_error

        # 保存操作前快照，支持回滚
        tx_id = f"{self.MODULE}:{entry_id}:{action}:tx"
        store.save_snapshot(tx_id, self.MODULE, entry_id)

        entry["status"] = target
        entry["pending"] = target in ("待布设", "已布设")
        entry["abnormal"] = action in self.NEGATIVE_ACTIONS
        entry["action_timestamp"] = _now_text()

        if action == "确认站位":
            self._bind_receipt(entry, receipt)

        store.mark_idempotent(idempotent_key)
        return entry, f"{self.ENTITY}已{action}"

    # ------------------------------------------------------------
    # 航次生效回执核验
    # ------------------------------------------------------------
    def _resolve_deployment_receipt(
        self, entry: dict[str, Any], receipt_no: str
    ) -> tuple[dict[str, Any] | None, str | None]:
        """返回(生效回执, 错误信息)；回执为 None 且无错误表示走历史兼容路径。"""
        if receipt_no:
            receipt = plan_receipt.get_receipt(receipt_no)
            if receipt is None:
                return None, f"航次生效回执 {receipt_no} 不存在，无法确认站位"
            if receipt.get("status") != plan_receipt.RECEIPT_ACTIVE:
                return None, (
                    f"回执 {receipt_no} 状态为「{receipt.get('status')}」，"
                    f"属于第{receipt.get('review_round')}轮计划，已不能用于新布设；"
                    "请按当前生效计划重新审阅批准后再确认站位"
                )
            scope_error = self._check_cruise_scope(entry, receipt)
            if scope_error:
                return None, scope_error
            return receipt, None

        active = plan_receipt.get_active_receipt()
        if active is not None:
            scope_error = self._check_cruise_scope(entry, active)
            if scope_error:
                return None, scope_error
            return active, None

        # 尚无任何回执：兼容未走回执动作流、直接置为已批准的历史数据
        cruise_rows = store.rows("cruise_plan")
        approved = [r for r in cruise_rows if r.get("status") == "已批准"]
        if not approved:
            return None, "航次计划尚未批准，无法布设站位"
        if plan_receipt.list_receipts():
            return None, (
                "航次计划已进入变更审阅，原生效回执已停用；"
                "新一轮计划重新批准前无法布设站位"
            )
        return None, None

    @staticmethod
    def _check_cruise_scope(
        entry: dict[str, Any], receipt: dict[str, Any]
    ) -> str | None:
        """核验站位与回执是否属于同一航次，禁止跨航次借用批准。"""
        station_cruise = str(entry.get("航次编号") or "").strip()
        if station_cruise and station_cruise != str(receipt.get("cruise_code") or ""):
            return (
                f"回执 {receipt.get('receipt_no')} 属于航次 {receipt.get('cruise_code')}，"
                f"与站位所属航次 {station_cruise} 不一致；甲航次的批准不能替乙航次确认站位"
            )
        plan_id = entry.get("cruise_plan_id")
        if plan_id is not None and int(plan_id) != int(receipt.get("cruise_plan_id")):
            return (
                f"回执 {receipt.get('receipt_no')} 关联的航次计划书与站位不一致，"
                "不能跨航次确认站位"
            )
        return None

    @staticmethod
    def _bind_receipt(
        entry: dict[str, Any], receipt: dict[str, Any] | None
    ) -> None:
        """布设成功后冻结回执轮次与批准当时的坐标。"""
        entry["plan_changed"] = False
        if receipt is None:
            return
        code = str(entry.get("站位编号") or "")
        entry["receipt_no"] = receipt["receipt_no"]
        entry["cruise_code"] = receipt["cruise_code"]
        entry["cruise_plan_id"] = receipt["cruise_plan_id"]
        entry["review_round"] = receipt["review_round"]
        entry["receipt_issued_at"] = receipt["issued_at"]
        # 批准当时的坐标：优先取回执快照；站位在批准后才登记时回退到当前坐标
        entry["approved_coordinates"] = (
            receipt.get("station_coordinates", {}).get(code)
            or entry.get("经纬度坐标")
        )
        # 已完成的作业始终引用批准当时的坐标，后续改线不回写该快照
        entry["frozen_coordinates"] = entry.get("经纬度坐标")

    # ------------------------------------------------------------
    # 追溯与沿用/重审判断
    # ------------------------------------------------------------
    def get_station_trace(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None
        return plan_receipt.station_trace(entry)

    def get_eligibility(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None
        result = plan_receipt.evaluate_station_eligibility(entry)
        result["station_id"] = entry_id
        result["station_code"] = entry.get("站位编号")
        return result

    def get_operable_stations(self) -> list[dict[str, Any]]:
        """返回当前可作业的站位列表（排除恶劣海况和人员不足影响）。"""
        weather_rows = store.rows("weather_obs")
        severe = any(r.get("status") == "恶劣海况" for r in weather_rows)
        rows = store.rows(self.MODULE)
        operable = []
        for r in rows:
            if severe and r.get("status") not in ("待布设", "已布设"):
                continue
            if r.get("status") == "已跳过":
                continue
            # 检查布设人可用性
            deployer = r.get("布设人", "")
            if deployer:
                crew_rows = store.find_by("crew_schedule", "队员姓名", deployer)
                for crew in crew_rows:
                    if crew.get("unavailable") or crew.get("diving_aborted"):
                        r["crew_blocked"] = True
                        r["crew_block_reason"] = f"布设人{deployer}不可用"
            operable.append(r)
        return operable

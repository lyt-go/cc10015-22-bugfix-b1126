# -*- coding: utf-8 -*-
"""航次计划领域服务：提供航次计划的全生命周期管理，作为其他模块的前置依赖。

核心修复：提交审阅/批准计划/变更计划的幂等去重按「审阅轮次」隔离。
旧实现使用不带轮次的扁平幂等键，第一轮提交审阅后该键永久存在，导致变更稿
（新一轮）无法再次提交和批准。现在每一轮审阅各自去重：

- 同一审阅轮重复提交/重复批准仍被拦截，且一轮只签发一份生效回执；
- 变更计划开启新一轮审阅，旧轮幂等键不再影响新轮的提交与批准；
- 批准时签发航次生效回执，计划变更时停用旧回执，旧轮批准不能让变更稿生效。
"""
from __future__ import annotations

from typing import Any

from app.services import plan_receipt
from app.services.base import BaseModuleService
from app.store import store


class CruisePlanService(BaseModuleService):
    MODULE = "cruise_plan"
    ENTITY = "航次计划书"
    OPERATION_TERM = "提交审阅"
    LIST_FIELDS = ["航次编号", "调查海域", "起止日期", "观测目标", "搭载设备", "首席科学家", "计划状态", "备注说明"]
    REQUIRED_FIELDS = ["航次编号", "调查海域", "起止日期"]
    STATUS_ORDER = ["编制中", "已提交", "已批准", "已变更"]
    ACTION_RULES = {"提交审阅": "已提交", "批准计划": "已批准", "变更计划": "已变更"}
    NEGATIVE_ACTIONS = []

    def _current_round(self, entry: dict[str, Any]) -> int:
        """读取计划当前审阅轮次，历史数据默认从第 1 轮开始。"""
        return int(entry.get("review_round") or 1)

    def is_approved(self) -> bool:
        """是否存在可用于新布设的已批准航次计划（以生效回执为准）。"""
        if plan_receipt.get_active_receipt() is not None:
            return True
        # 兼容未走动作流、直接置为已批准的历史/种子数据
        rows = store.rows(self.MODULE)
        return any(row.get("status") == "已批准" for row in rows)

    def get_approved_plan(self) -> dict[str, Any] | None:
        """获取当前生效的已批准航次计划（优先取生效回执对应的计划）。"""
        active = plan_receipt.get_active_receipt()
        if active is not None:
            plan = store.find(self.MODULE, int(active["cruise_plan_id"]))
            if plan is not None:
                return dict(plan)
        rows = store.rows(self.MODULE)
        for row in rows:
            if row.get("status") == "已批准":
                return dict(row)
        return None

    def run_action(self, entry_id: int, action: str, context: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str]:
        """执行动作，幂等去重按审阅轮次隔离，并在批准时签发/变更时停用回执。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"

        target = self.ACTION_RULES.get(action)
        if target is None:
            return None, f"动作「{action}」不属于{self.MODULE}可执行范围"

        entry.setdefault("review_round", 1)
        review_round = self._current_round(entry)

        # 幂等检查：键中带审阅轮次，旧轮去重不再影响新一轮审批。
        # 同一轮重复提交/批准先判重，保证重复请求得到明确的“重复”提示。
        idempotent_key = f"{self.MODULE}:{entry_id}:{action}:R{review_round}"
        if store.is_idempotent(idempotent_key):
            return None, f"动作「{action}」已执行（第{review_round}轮），请勿重复操作"

        # 状态流转前置校验
        if action == "变更计划" and entry.get("status") != "已批准":
            return None, "只能变更已批准的航次计划"
        if action == "批准计划" and entry.get("status") != "已提交":
            return None, "只能批准已提交的航次计划"
        if action == "提交审阅" and entry.get("status") not in ("编制中", "已变更"):
            return None, "只能提交编制中或已变更的航次计划"

        # 保存操作前快照
        tx_id = f"{self.MODULE}:{entry_id}:{action}:R{review_round}:tx"
        store.save_snapshot(tx_id, self.MODULE, entry_id)

        entry["status"] = target
        entry["abnormal"] = action in self.NEGATIVE_ACTIONS

        if action == "提交审阅":
            entry["pending"] = True
            entry["submitted_round"] = review_round
        elif action == "批准计划":
            entry["pending"] = False
            entry["approved_round"] = review_round
            receipt = plan_receipt.build_receipt(entry, review_round)
            stored_receipt = store.issue_approval_receipt(receipt)
            entry["active_receipt_no"] = stored_receipt["receipt_no"]
        else:  # 变更计划：开启新一轮审阅
            entry["pending"] = True
            store.deactivate_approval_receipts(
                entry_id, "航次计划发起变更，旧轮批准停止用于新布设，须重新审阅批准"
            )
            entry["active_receipt_no"] = None
            entry["review_round"] = review_round + 1
            self._notify_plan_changed(entry)

        store.mark_idempotent(idempotent_key)
        return entry, f"{self.ENTITY}已{action}"

    def _notify_plan_changed(self, entry: dict[str, Any]) -> None:
        """航次计划变更后通知站位布设等模块标记需重新评估。"""
        station_rows = store.rows("station_deploy")
        review_round = self._current_round(entry)
        for row in station_rows:
            if row.get("status") in ("已布设", "已作业"):
                row["plan_changed"] = True
                row["plan_change_msg"] = (
                    f"航次计划第{review_round}轮变更，请按新生效回执重新评估站位"
                )

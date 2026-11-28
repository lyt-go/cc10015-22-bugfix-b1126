"""采样记录领域服务：采样入库时自动联动样品登记模块创建或更新对应样品记录。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class SamplingRecordService(BaseModuleService):
    MODULE = "sampling_record"
    ENTITY = "采样单"
    OPERATION_TERM = "记录采样"
    LIST_FIELDS = ["采样站位", "采样深度", "样品类型", "样品编号", "采样器型号", "采样人", "采样时间", "备注说明"]
    REQUIRED_FIELDS = ["采样站位", "采样深度", "样品类型"]
    STATUS_ORDER = ["待采样", "已采样", "已入库", "已废弃"]
    ACTION_RULES = {"记录采样": "已采样", "样品入库": "已入库", "废弃样品": "已废弃"}
    NEGATIVE_ACTIONS = ["废弃样品"]

    def run_action(self, entry_id: int, action: str, context: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"

        target = self.ACTION_RULES.get(action)
        if target is None:
            return None, f"动作「{action}」不属于{self.MODULE}可执行范围"

        # 幂等检查
        idempotent_key = f"{self.MODULE}:{entry_id}:{action}"
        if store.is_idempotent(idempotent_key):
            return None, f"动作「{action}」已执行，请勿重复操作"

        # 采样前验证对应的站位是否已布设
        if action == "记录采样":
            station_id = entry.get("采样站位", "")
            station_rows = store.find_by("station_deploy", "站位编号", station_id)
            if not station_rows:
                return None, f"采样站位「{station_id}」未在站位布设中登记"
            if not any(r.get("status") in ("已布设", "已作业") for r in station_rows):
                return None, f"采样站位「{station_id}」尚未布设，无法采样"

        old_status = entry.get("status")
        entry["status"] = target
        entry["pending"] = target in ("待采样", "已采样")
        entry["abnormal"] = action in self.NEGATIVE_ACTIONS

        # 标记幂等
        store.mark_idempotent(idempotent_key)

        # 样品入库后自动联动样品登记模块
        if action == "样品入库":
            self._sync_to_registry(entry)

        return entry, f"{self.ENTITY}已{action}"

    def _sync_to_registry(self, entry: dict[str, Any]) -> None:
        """采样入库后自动在样品登记中创建或更新记录。"""
        sample_no = entry.get("样品编号", "")
        sample_type = entry.get("样品类型", "")
        station = entry.get("采样站位", "")

        # 查找是否已存在
        existing = store.find_one_by("sample_registry", "样品编号", sample_no)
        if existing:
            existing["status"] = "已登记"
            existing["pending"] = True
            existing["synced_from"] = "sampling_record"
        else:
            rows = store.rows("sample_registry")
            new_id = max((int(r.get("id", 0)) for r in rows), default=0) + 1
            new_entry = {
                "id": new_id,
                "样品编号": sample_no,
                "样品类型": sample_type,
                "采集站位": station,
                "保存方式": "待确认",
                "存放位置": "待分配",
                "登记人": entry.get("采样人", ""),
                "登记日期": entry.get("采样时间", ""),
                "备注说明": "由采样记录自动创建",
                "status": "待登记",
                "pending": True,
                "abnormal": False,
                "synced_from": "sampling_record",
            }
            rows.append(new_entry)
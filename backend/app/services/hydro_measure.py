"""水文测量领域服务：上传水文剖面后自动触发数据质控流程。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class HydroMeasureService(BaseModuleService):
    MODULE = "hydro_measure"
    ENTITY = "水文剖面"
    OPERATION_TERM = "开始测量"
    LIST_FIELDS = ["测量站位", "温度剖面", "盐度剖面", "溶解氧", "叶绿素浓度", "测量时间", "仪器型号", "备注说明"]
    REQUIRED_FIELDS = ["测量站位", "温度剖面", "盐度剖面"]
    STATUS_ORDER = ["待测量", "已测量", "已上传", "数据异常"]
    ACTION_RULES = {"开始测量": "已测量", "上传剖面": "已上传", "标记异常": "数据异常"}
    NEGATIVE_ACTIONS = ["标记异常"]

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

        # 测量前置：确认站位已布设
        if action == "开始测量":
            station_id = entry.get("测量站位", "")
            station_rows = store.find_by("station_deploy", "站位编号", station_id)
            if not station_rows:
                return None, f"测量站位「{station_id}」未在站位布设中登记"

        old_status = entry.get("status")
        entry["status"] = target
        entry["pending"] = target in ("待测量", "已测量")
        entry["abnormal"] = action in self.NEGATIVE_ACTIONS

        store.mark_idempotent(idempotent_key)

        # 上传剖面后触发数据质控流程
        if action == "上传剖面":
            self._trigger_qc(entry)

        return entry, f"{self.ENTITY}已{action}"

    def _trigger_qc(self, entry: dict[str, Any]) -> None:
        """上传水文剖面后自动创建质控记录。"""
        qc_rows = store.rows("data_qc")
        new_id = max((int(r.get("id", 0)) for r in qc_rows), default=0) + 1
        qc_entry = {
            "id": new_id,
            "数据来源": f"水文测量-{entry.get('测量站位', '')}",
            "质控标准": "CTD剖面标准质控",
            "异常值个数": "0",
            "质控结论": "待评估",
            "修正措施": "",
            "质控人": "",
            "质控日期": entry.get("测量时间", ""),
            "备注说明": f"由水文剖面{entry.get('id')}上传自动触发",
            "status": "待质控",
            "pending": True,
            "abnormal": False,
            "triggered_by": "hydro_measure",
            "source_entry_id": entry.get("id"),
        }
        qc_rows.append(qc_entry)
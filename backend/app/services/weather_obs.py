"""气象观测领域服务：恶劣海况标记影响站位布设的可作业站位列表。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class WeatherObsService(BaseModuleService):
    MODULE = "weather_obs"
    ENTITY = "气象记录"
    OPERATION_TERM = "记录数据"
    LIST_FIELDS = ["观测时间", "风速风向", "浪高数据", "能见度", "气压读数", "观测人", "观测位置", "备注说明"]
    REQUIRED_FIELDS = ["观测时间", "风速风向", "浪高数据"]
    STATUS_ORDER = ["待观测", "已记录", "已同步", "恶劣海况"]
    ACTION_RULES = {"记录数据": "已记录", "同步岸端": "已同步", "标记海况": "恶劣海况"}
    NEGATIVE_ACTIONS = ["标记海况"]

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

        old_status = entry.get("status")
        entry["status"] = target
        entry["pending"] = target in ("待观测", "已记录")
        entry["abnormal"] = action in self.NEGATIVE_ACTIONS

        store.mark_idempotent(idempotent_key)

        # 恶劣海况标记：影响站位布设的可作业站位
        if action == "标记海况":
            self._notify_station_deploy(entry)

        return entry, f"{self.ENTITY}已{action}"

    def _notify_station_deploy(self, entry: dict[str, Any]) -> None:
        """恶劣海况标记：通知站位布设模块暂停作业。"""
        station_rows = store.rows("station_deploy")
        for row in station_rows:
            if row.get("status") in ("已布设",):
                row["weather_alert"] = True
                row["weather_alert_msg"] = f"恶劣海况预警：浪高{entry.get('浪高数据', '未知')}米，风速{entry.get('风速风向', '未知')}"

    def has_severe_weather(self) -> bool:
        """检查当前是否存在恶劣海况。"""
        return any(
            r.get("status") == "恶劣海况"
            for r in store.rows(self.MODULE)
        )

    def get_latest_observation(self) -> dict[str, Any] | None:
        """获取最新气象观测记录。"""
        rows = store.rows(self.MODULE)
        if not rows:
            return None
        return max(rows, key=lambda r: str(r.get("观测时间", "")))
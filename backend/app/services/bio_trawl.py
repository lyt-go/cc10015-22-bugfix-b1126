"""生物拖网领域服务：完成分拣后自动将渔获物登记到样品登记模块。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class BioTrawlService(BaseModuleService):
    MODULE = "bio_trawl"
    ENTITY = "拖网记录"
    OPERATION_TERM = "开始作业"
    LIST_FIELDS = ["拖网站位", "网具型号", "拖网深度", "拖网时长", "渔获物描述", "操作人", "记录时间", "备注说明"]
    REQUIRED_FIELDS = ["拖网站位", "网具型号", "拖网深度"]
    STATUS_ORDER = ["待作业", "已作业", "已记录", "已分拣"]
    ACTION_RULES = {"开始作业": "已作业", "提交记录": "已记录", "完成分拣": "已分拣"}
    NEGATIVE_ACTIONS = []

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
        entry["pending"] = target in ("待作业", "已作业")
        entry["abnormal"] = action in self.NEGATIVE_ACTIONS

        store.mark_idempotent(idempotent_key)

        # 完成分拣后联动样品登记
        if action == "完成分拣":
            self._register_samples(entry)

        return entry, f"{self.ENTITY}已{action}"

    def _register_samples(self, entry: dict[str, Any]) -> None:
        """完成分拣后自动将渔获物样品登记到样品管理模块。"""
        station = entry.get("拖网站位", "")
        fisher = entry.get("操作人", "")
        record_time = entry.get("记录时间", "")

        # 创建生物样品登记记录
        registry_rows = store.rows("sample_registry")
        new_id = max((int(r.get("id", 0)) for r in registry_rows), default=0) + 1
        sample_no = f"BIO-{entry.get('id'):04d}"
        sample_entry = {
            "id": new_id,
            "样品编号": sample_no,
            "样品类型": "生物样品",
            "采集站位": station,
            "保存方式": "冷藏保存",
            "存放位置": "实验室冷库",
            "登记人": fisher,
            "登记日期": record_time,
            "备注说明": f"由生物拖网{entry.get('id')}分拣后自动登记，渔获物：{entry.get('渔获物描述', '')}",
            "status": "待登记",
            "pending": True,
            "abnormal": False,
            "synced_from": "bio_trawl",
        }
        registry_rows.append(sample_entry)
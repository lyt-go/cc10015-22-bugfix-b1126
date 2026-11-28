# -*- coding: utf-8 -*-
"""航次生效回执领域服务。

回执是“某航次计划书某一审阅轮次获得批准”的凭证，记录批准当时的计划与
站位坐标快照：

- 每一轮批准签发一份回执，同一审阅轮重复提交/批准只保留一份生效回执；
- 全局同一时点只有一份“生效中”的回执可用于新布设；
- 计划发起变更后原回执停用，旧一轮批准不能让变更稿直接生效；
- 站位布设按回执识别沿用/重审资格，已完成作业仍引用批准当时的坐标。
"""
from __future__ import annotations

import time
from typing import Any

from app.store import store

# 回执状态
RECEIPT_ACTIVE = "生效中"
RECEIPT_REPLACED = "已替代"
RECEIPT_DEACTIVATED = "已停用"

# 站位沿用/重审结论
STATION_FOLLOW = "可沿用"
STATION_RE_REVIEW = "需重审"
STATION_NOT_BOUND = "未关联回执"


def _now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def snapshot_station_coordinates() -> dict[str, str]:
    """采集当前全部站位的编号→坐标，作为批准当时的计划坐标快照。"""
    snapshot: dict[str, str] = {}
    for row in store.rows("station_deploy"):
        code = str(row.get("站位编号") or "").strip()
        if code:
            snapshot[code] = str(row.get("经纬度坐标") or "")
    return snapshot


def build_receipt(plan: dict[str, Any], review_round: int) -> dict[str, Any]:
    """根据批准当时的计划构建一份航次生效回执（尚未入库）。"""
    return {
        "receipt_no": store.next_receipt_no(),
        "cruise_plan_id": int(plan.get("id", 0)),
        "cruise_code": str(plan.get("航次编号") or ""),
        "review_round": review_round,
        "plan_version": f"R{review_round}",
        "plan_snapshot": {
            "调查海域": plan.get("调查海域"),
            "起止日期": plan.get("起止日期"),
            "观测目标": plan.get("观测目标"),
            "首席科学家": plan.get("首席科学家"),
        },
        "station_coordinates": snapshot_station_coordinates(),
        "issued_at": _now_text(),
        "status": RECEIPT_ACTIVE,
        "supersede_reason": "",
    }


def get_receipt(receipt_no: str) -> dict[str, Any] | None:
    return store.get_approval_receipt(receipt_no)


def get_active_receipt() -> dict[str, Any] | None:
    return store.get_active_approval_receipt()


def list_receipts(
    cruise_plan_id: int | None = None, cruise_code: str | None = None
) -> list[dict[str, Any]]:
    return store.list_approval_receipts(
        cruise_plan_id=cruise_plan_id, cruise_code=cruise_code
    )


def evaluate_station_eligibility(station: dict[str, Any]) -> dict[str, Any]:
    """按航次生效回执判断站位是沿用还是需要重审。

    判定规则：
    - 未绑定回执的站位：未关联回执，新布设须先通过回执核验；
    - 绑定的回执仍“生效中”且坐标与回执快照一致：可沿用；
    - 回执已停用/替代，或当前坐标与批准当时快照不一致：需重审。
    已作业站位的结论同样给出，但其已冻结的批准坐标不会被改写。
    """
    receipt_no = str(station.get("receipt_no") or "")
    if not receipt_no:
        return {
            "eligibility": STATION_NOT_BOUND,
            "need_re_review": True,
            "reason": "站位尚未关联航次生效回执，新布设前须完成回执核验",
        }

    receipt = store.get_approval_receipt(receipt_no)
    if receipt is None:
        return {
            "eligibility": STATION_RE_REVIEW,
            "need_re_review": True,
            "reason": f"回执 {receipt_no} 不存在，无法证明站位归属轮次",
        }

    code = str(station.get("站位编号") or "")
    # 优先用回执快照；站位若在批准后才登记，回退到布设时冻结的批准坐标
    approved_coord = receipt.get("station_coordinates", {}).get(code)
    if approved_coord is None:
        approved_coord = station.get("approved_coordinates") or station.get(
            "frozen_coordinates"
        )
    current_coord = str(station.get("经纬度坐标") or "")
    same_coord = approved_coord is not None and approved_coord == current_coord

    if receipt.get("status") == RECEIPT_ACTIVE and same_coord:
        return {
            "eligibility": STATION_FOLLOW,
            "need_re_review": False,
            "reason": "站位坐标与生效回执快照一致，可沿用上一轮布设",
        }

    reasons: list[str] = []
    if receipt.get("status") != RECEIPT_ACTIVE:
        reasons.append(f"回执状态为「{receipt.get('status')}」，所在轮次已不是当前生效计划")
    if not same_coord:
        reasons.append("站位坐标与批准当时的回执快照不一致")
    return {
        "eligibility": STATION_RE_REVIEW,
        "need_re_review": True,
        "reason": "；".join(reasons) or "需要重新审阅批准",
    }


def station_trace(station: dict[str, Any]) -> dict[str, Any]:
    """组装站位回执追溯信息：归属哪轮计划、批准坐标、下一步是否需重审。"""
    receipt_no = str(station.get("receipt_no") or "")
    receipt = store.get_approval_receipt(receipt_no) if receipt_no else None
    code = str(station.get("站位编号") or "")
    eligibility = evaluate_station_eligibility(station)
    approved_coord = None
    if receipt is not None:
        approved_coord = receipt.get("station_coordinates", {}).get(code)
    if approved_coord is None:
        approved_coord = station.get("approved_coordinates") or station.get(
            "frozen_coordinates"
        )
    return {
        "station_id": station.get("id"),
        "station_code": code,
        "status": station.get("status"),
        "current_coordinates": station.get("经纬度坐标"),
        "frozen_coordinates": station.get("frozen_coordinates"),
        "receipt_no": receipt_no or None,
        "cruise_code": station.get("cruise_code"),
        "cruise_plan_id": station.get("cruise_plan_id"),
        "review_round": station.get("review_round"),
        "plan_version": receipt.get("plan_version") if receipt else None,
        "plan_snapshot": receipt.get("plan_snapshot") if receipt else None,
        "approved_coordinates": approved_coord,
        "receipt_status": receipt.get("status") if receipt else None,
        "receipt_issued_at": receipt.get("issued_at") if receipt else None,
        "eligibility": eligibility["eligibility"],
        "need_re_review": eligibility["need_re_review"],
        "next_step": "按当前生效回执重新审阅后布设"
        if eligibility["need_re_review"]
        else "可按生效回执继续沿用",
        "reason": eligibility["reason"],
    }

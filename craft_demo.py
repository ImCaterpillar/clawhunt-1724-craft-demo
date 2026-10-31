# -*- coding: utf-8 -*-
"""工艺 C2M 演示核心 —— 授权校验/可制造性评估/订单链路（三验收点可运行实现）"""
import json
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))


class DemoError(Exception):
    pass


def _load(rel):
    return json.load(open(os.path.join(HERE, rel), encoding="utf-8"))


# ── 1. 纹样资源库：授权字段强制校验（缺字段拒收）────────────────
REQUIRED_AUTH_FIELDS = ["pattern_id", "origin", "license_scope",
                        "rights_holder", "expires_at"]


class PatternLibrary:
    def __init__(self):
        self.patterns = {}

    def ingest(self, pattern):
        missing = [f for f in REQUIRED_AUTH_FIELDS if f not in pattern
                   or pattern[f] in ("", None)]
        if missing:
            return {"accepted": False,
                    "reason": f"缺少授权必填字段: {missing}"}
        pid = pattern["pattern_id"]
        if pid in self.patterns:
            return {"accepted": False, "reason": f"重复 pattern_id: {pid}"}
        self.patterns[pid] = pattern
        return {"accepted": True, "pattern_id": pid}

    def count(self):
        return len(self.patterns)


# ── 2. 可制造性评估助手：10 概念（含 3 反例），规则可复算 ────────
# 工艺规则（可由非程序员维护的显式规则表）：
RULES = [
    {"rule_id": "R1", "name": "最小镶嵌间隙",
     "check": lambda c: c.get("stone_gap_mm", 99) >= 0.3,
     "fail_reason": "宝石间隙 < 0.3mm，无法镶铸"},
    {"rule_id": "R2", "name": "最小壁厚",
     "check": lambda c: c.get("wall_mm", 99) >= 0.8,
     "fail_reason": "壁厚 < 0.8mm，铸造易破"},
    {"rule_id": "R3", "name": "镂空比上限",
     "check": lambda c: c.get("openwork_pct", 0) <= 55,
     "fail_reason": "镂空率 > 55%，结构强度不足"},
    {"rule_id": "R4", "name": "榫卯数量上限",
     "check": lambda c: c.get("mortise_count", 0) <= 24,
     "fail_reason": "榫卯数 > 24，手工周期超限"},
]


class ManufacturabilityChecker:
    def __init__(self, rules=None):
        self.rules = rules or RULES

    def evaluate(self, concept):
        fails = [r for r in self.rules if not r["check"](concept)]
        return {
            "concept_id": concept.get("concept_id"),
            "manufacturable": not fails,
            "failed_rules": [{"rule_id": r["rule_id"], "name": r["name"],
                              "reason": r["fail_reason"]} for r in fails],
            "suggest": ("可制造" if not fails else
                        "；".join(f["fail_reason"] for f in fails) +
                        "→ 建议调整参数后重评"),
        }

    def evaluate_batch(self, concepts):
        return [self.evaluate(c) for c in concepts]


# ── 3. C2M 订单链路：状态机 + 退款/争议路径 ────────────────────
ORDER_FLOW = ["created", "craft_order", "dispatched", "qc_passed",
              "shipped", "settled"]
REFUND_FROM = {"created", "craft_order", "dispatched"}  # 这三态可退款


class C2MOrder:
    def __init__(self, order_id, amount_cents):
        self.order_id = order_id
        self.amount_cents = amount_cents
        self.state = "created"
        self.ledger = [{"event": "created", "state": self.state,
                        "amount_cents": amount_cents}]

    def advance(self):
        idx = ORDER_FLOW.index(self.state)
        if idx >= len(ORDER_FLOW) - 1:
            raise DemoError(f"订单已终态: {self.state}")
        self.state = ORDER_FLOW[idx + 1]
        self.ledger.append({"event": "advance", "state": self.state})
        return self.state

    def refund(self, reason):
        if self.state not in REFUND_FROM:
            raise DemoError(f"状态 {self.state} 不可退款")
        self.ledger.append({"event": "refund", "reason": reason,
                            "refund_cents": self.amount_cents})
        self.state = "refunded"
        return self.ledger[-1]

    def dispute(self, reason):
        self.ledger.append({"event": "dispute_opened", "reason": reason,
                            "state": self.state})
        return self.ledger[-1]

    def trace(self):
        """台账：全事件可追溯（状态+金额）"""
        return list(self.ledger)


# ── 自检入口 ────────────────────────────────────────────────────
if __name__ == "__main__":
    lib = PatternLibrary()
    print("1. 授权字段校验（缺字段拒收）:")
    print("  ", lib.ingest({"pattern_id": "p1"}))
    print("  ", lib.ingest({"pattern_id": "p2", "origin": "self",
                             "license_scope": "exclusive",
                             "rights_holder": "demo", "expires_at": "2027"}))
    print("  库内数量:", lib.count())

    print("\n2. 可制造性评估（10 概念含 3 反例）:")
    concepts = _load("data/concepts.json")
    results = ManufacturabilityChecker().evaluate_batch(concepts)
    ok = sum(1 for r in results if r["manufacturable"])
    print(f"  可制造 {ok}/{len(results)}，反例全部被拒: "
          f"{all(not r['manufacturable'] for c, r in zip(concepts, results) if c.get('expect') == 'fail')}")
    for r in results:
        mark = "✅可" if r["manufacturable"] else "❌不可"
        print(f"   {mark} {r['concept_id']}: {r['suggest'][:50]}")

    print("\n3. C2M 订单链路:")
    o = C2MOrder("ord-001", 19900)
    for _ in range(len(ORDER_FLOW) - 1):
        o.advance()
    print("  正常链路:", [e["state"] for e in o.trace() if "state" in e])
    o2 = C2MOrder("ord-002", 9900)
    o2.advance()
    print("  退款:", o2.refund("客户取消"))
    print("  台账事件数:", len(o2.trace()))
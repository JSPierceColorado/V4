FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Hard overwrite the optional operator module with a tiny deterministic shim.
# Railway ZIP snapshots have repeatedly corrupted agent_operator.py bytes; this
# keeps the app bootable and lets autonomy.py run the deterministic trading path.
RUN cat > /app/agent_operator.py <<'PY'
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Dict, List

def _is_open(clock: Dict[str, Any]) -> bool:
    value = clock.get("is_open") if isinstance(clock, dict) else False
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "open"}
    return bool(value)

def _autonomy_action(reason: str | None = None) -> Dict[str, Any]:
    return {"tool": "autonomy_cycle", "args": {}, "reason": reason or "Market is open; run deterministic entry/exit evaluation before waiting."}

def build_operator_context(settings: Any, *, state: Dict[str, Any], autonomy_status: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "operator_policy": {"paper_account_only": True, "allowed_tools": ["state", "clock", "research", "market_brief", "review_positions", "autonomy_cycle", "wait"]},
        "settings": {"autonomy_screen_symbols_per_cycle": getattr(settings, "autonomy_screen_symbols_per_cycle", None), "autonomy_min_score": getattr(settings, "autonomy_min_score", None)},
        "state": state,
        "autonomy_status": autonomy_status,
        "clock": state.get("clock") or {},
        "account": state.get("account") or {},
        "positions": state.get("positions") or [],
        "open_orders": state.get("open_orders") or [],
        "open_trade_theses": state.get("open_trade_theses") or [],
        "recent_events": [],
    }

def fallback_plan(context: Dict[str, Any]) -> Dict[str, Any]:
    clock = context.get("clock") or (context.get("state") or {}).get("clock") or {}
    if _is_open(clock):
        return {"source": "fallback_no_openai", "rationale": "Market is open, so run deterministic autonomy_cycle before waiting.", "actions": [_autonomy_action("Market is open; screen/manage live positions before waiting.")]}
    return {"source": "fallback_no_openai", "rationale": "Market is closed; review positions then wait.", "actions": [{"tool": "review_positions", "args": {}, "reason": "Review open theses while market is closed."}, {"tool": "wait", "args": {}, "reason": "Market is closed."}]}

def model_plan(settings: Any, context: Dict[str, Any]) -> Dict[str, Any]:
    return fallback_plan(context)

def enforce_autonomy_guardrails(context: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
    clock = context.get("clock") or (context.get("state") or {}).get("clock") or {}
    if not _is_open(clock):
        return plan
    actions = list(plan.get("actions") or [])
    if any(a.get("tool") == "autonomy_cycle" for a in actions):
        return plan
    new_actions = []
    replaced = False
    for action in actions:
        if action.get("tool") == "wait":
            new_actions.append(_autonomy_action("Autonomy guardrail: market is open; run autonomy_cycle before waiting.")); replaced = True
        else:
            new_actions.append(action)
    if not replaced:
        new_actions.append(_autonomy_action("Autonomy guardrail: market is open; append autonomy_cycle before waiting."))
    patched = dict(plan); patched["actions"] = new_actions
    patched["guardrails"] = list(patched.get("guardrails") or []) + ["market_open_requires_autonomy_cycle_before_wait"]
    patched["rationale"] = ((patched.get("rationale") or "") + " Autonomy guardrail applied: market is open, so autonomy_cycle is required before wait.").strip()
    return patched

def summarize_tool_result(result: Dict[str, Any]) -> str:
    tool = result.get("tool") or "tool"
    if result.get("skipped") and result.get("reason") == "not_due":
        last = result.get("last_periodic_research_at") or result.get("last_research_at")
        return f"{tool}: skipped - not due yet." + (f" Last periodic research: {last}" if last else "")
    if tool == "clock":
        clock = result.get("clock") or {}
        return f"clock: market {'open' if _is_open(clock) else 'closed'}. Next {'close' if _is_open(clock) else 'open'}: {clock.get('next_close') if _is_open(clock) else clock.get('next_open')}"
    if result.get("reply"):
        return f"{tool}: {result.get('reply')}"
    if result.get("ok") is False:
        return f"{tool}: failed - {result.get('error', 'unknown error')}"
    return f"{tool}: completed."

def journal_operator_cycle(data_dir: str, *, plan: Dict[str, Any], results: List[Dict[str, Any]]) -> Dict[str, Any]:
    lines = [f"Operator plan source: {plan.get('source') or 'fallback_no_openai' }."]
    if plan.get("rationale"):
        lines.append(f"Rationale: {plan['rationale']}")
    for idx, action in enumerate(plan.get("actions") or [], start=1):
        lines.append(f"{idx}. {action.get('tool')}: {action.get('reason', '')}")
    for result in results:
        lines.append(summarize_tool_result(result))
    journal = {"ok": True, "summary": "\n".join(lines), "plan": plan, "results": results, "recorded_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat()}
    try:
        from storage import append_event
        append_event(data_dir, "agent_operator_journal", journal)
    except Exception:
        pass
    return journal
PY

RUN python - <<'PY'
from pathlib import Path
import shutil
for cache in Path('.').rglob('__pycache__'):
    shutil.rmtree(cache, ignore_errors=True)
for path in Path('.').rglob('*.py'):
    data = path.read_bytes()
    if b'\x00' in data:
        path.write_bytes(data.replace(b'\x00', b''))
        print(f'Removed NUL bytes from {path}')
print('Python source NUL cleanup complete')
PY

RUN python - <<'PY'
from pathlib import Path
import py_compile
for path in Path('.').rglob('*.py'):
    py_compile.compile(str(path), doraise=True)
print('Python source compile check passed')
PY

CMD ["python", "start.py"]

from __future__ import annotations

import re
from typing import Any


def is_discovery_status_question(message: str) -> bool:
    text = re.sub(r'\s+', '', message).lower()
    topic = re.search(r'探索|搜尋|搜索|任務|飽和|饱和|bd.*(?:結果|狀態|进度|進度|0筆|零筆)', text)
    question = re.search(r'為什麼|為甚麼|为什么|為何|原因|結果|进度|進度|狀態|状态|飽和|饱和|0筆|零筆|沒新增|沒有新增|怎麼樣|查詢|查看', text)
    return bool(topic and question)


def discovery_run_id(message: str) -> int | None:
    match = re.search(r'(?:[#＃]|任務\s*(?:編號)?\s*[:：]?\s*|第\s*)(\d{1,9})(?:\s*(?:筆|個))?', message)
    return int(match.group(1)) if match and int(match.group(1)) > 0 else None


def format_discovery_status(result: dict[str, Any]) -> str:
    runs = result.get('runs') if isinstance(result.get('runs'), list) else []
    if not runs:
        return str(result.get('message') or '找不到符合條件的 BD 探索任務，不推測結果。')
    run = runs[0]
    lines = [
        f'BD 任務 #{run.get("runId")}｜{run.get("targetSegmentName") or "未分類"}',
        str(run.get('statusLabel') or run.get('status') or ''),
        f'已執行 {run.get("completedRounds", 0)}/{run.get("maxRounds", 0)} 輪，檢查 {run.get("sourceCount", 0)} 個來源。',
        str(run.get('outcome') or ''),
    ]
    if run.get('stopExplanation'):
        lines.append(str(run['stopExplanation']))
    if run.get('error'):
        lines.append(f'執行異常：{run["error"]}')
    if run.get('nextRetryAt'):
        lines.append(f'下次重試：{run["nextRetryAt"]}')
    for item in (run.get('adjustments') or [])[-3:]:
        if isinstance(item, dict):
            lines.append(f'第 {item.get("round")} 輪已調整：{item.get("action", "")}。')
    recommendations = run.get('recommendations') or []
    if recommendations:
        lines.extend(['', '下一步建議：'])
        for item in recommendations[:4]:
            if isinstance(item, dict):
                approval = '（需你確認）' if item.get('requiresConfirmation') else ''
                lines.append(f'• {item.get("title", "")}{approval}：{item.get("detail", "")}')
    lines.extend(['', '新增 0 筆不代表市場已找完。這次查詢不會另開搜尋、扣搜尋預算或寄信。'])
    return '\n'.join(lines)[:4000]

import ast
import asyncio
import copy
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import time

from open_webui.utils.response import normalize_usage, merge_usage


def load_upsert():
    path = Path(__file__).parents[1] / 'open_webui/models/chat_messages.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    helper = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'get_usage')
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ChatMessageTable')
    method = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'upsert_message')
    module = ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),helper,method],type_ignores=[])
    @asynccontextmanager
    async def context(db):
        yield db
    namespace = {'normalize_usage':normalize_usage, 'time':time, 'get_async_db_context':context,
                 'ChatMessage':object, 'ChatMessageModel':SimpleNamespace(model_validate=lambda row:copy.deepcopy(row))}
    exec(compile(ast.fix_missing_locations(module),str(path),'exec'),namespace)
    return namespace['upsert_message']


def test_message_snapshots_do_not_double_count_streams_or_dual_writes():
    upsert = load_upsert()
    existing = SimpleNamespace(usage=None, content='', updated_at=0)
    db = SimpleNamespace(get=AsyncMock(return_value=existing),commit=AsyncMock())
    first = {'prompt_tokens':100, 'completion_tokens':10, 'total_tokens':110}
    cumulative = merge_usage(first, {'prompt_tokens':200,'completion_tokens':20,'total_tokens':220})
    async def run():
        for usage in [first, first, cumulative, cumulative]:
            await upsert(None,'m','c','u',{'usage':usage},db=db)
        await upsert(None,'m','c','u',{'content':'final answer'},db=db)
    asyncio.run(run())
    assert existing.usage['input_tokens'] == 300
    assert existing.usage['output_tokens'] == 30
    assert existing.usage['total_tokens'] == 330
    assert existing.content == 'final answer'


def test_provider_calls_remain_additive_but_zero_snapshot_is_not_lost():
    assert merge_usage({'total_tokens':100}, {'total_tokens':200})['total_tokens'] == 300
    existing = SimpleNamespace(usage={'total_tokens':500}, updated_at=0)
    db = SimpleNamespace(get=AsyncMock(return_value=existing),commit=AsyncMock())
    asyncio.run(load_upsert()(None,'m','c','u',{'usage':{'input_tokens':0,'output_tokens':0,'total_tokens':0}},db=db))
    assert existing.usage['total_tokens'] == 0

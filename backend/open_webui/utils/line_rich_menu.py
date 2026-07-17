from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

RICH_MENU_WIDTH = 2500
RICH_MENU_HEIGHT = 1686
RICH_MENU_HEADER_HEIGHT = 214
RICH_MENU_UTILITY_HEIGHT = 286
GUEST_PRIMARY_HEIGHT = 1020
MAX_RICH_MENU_WORKFLOWS = 4


@dataclass(frozen=True)
class LineRichMenuArtifact:
    menu: dict[str, Any]
    image: bytes
    content_hash: str
    workflow_ids: list[str]


@dataclass(frozen=True)
class LineRoleMenuArtifact:
    audience: str
    tab: str
    alias_id: str
    menu: dict[str, Any]
    image: bytes
    content_hash: str


def workflow_launch_postback(workflow_id: str) -> str:
    return json.dumps(
        {'a': 'workflow.launch.v2', 'w': workflow_id},
        separators=(',', ':'),
    )


def system_postback(action: str) -> str:
    return json.dumps({'a': action}, separators=(',', ':'))


def parse_line_postback_data(raw_data: Any) -> dict[str, Any] | None:
    raw = str(raw_data or '')
    if not raw or len(raw) > 300:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get('a'), str):
        return None
    return data


def _font_path() -> Path:
    return Path(__file__).resolve().parents[1] / 'static' / 'fonts' / 'NotoSansSC-Variable.ttf'


def _font(size: int, weight: str = 'Regular') -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(str(_font_path()), size=size)
    font.set_variation_by_name(weight)
    return font


def _clean_label(value: Any, fallback: str, *, length: int = 80) -> str:
    cleaned = ' '.join(str(value or '').split())
    return (cleaned or ' '.join(fallback.split()))[:length]


def _fit_lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
    max_lines: int = 2,
) -> list[str]:
    lines: list[str] = []
    remaining = text.strip()
    while remaining and len(lines) < max_lines:
        candidate = ''
        consumed = 0
        for index, character in enumerate(remaining):
            proposed = candidate + character
            if draw.textbbox((0, 0), proposed, font=font)[2] > max_width and candidate:
                break
            candidate = proposed
            consumed = index + 1
        if not candidate:
            candidate, consumed = remaining[:1], 1
        remaining = remaining[consumed:].lstrip()
        if remaining and len(lines) == max_lines - 1:
            while candidate and draw.textbbox((0, 0), candidate + '...', font=font)[2] > max_width:
                candidate = candidate[:-1]
            candidate = candidate.rstrip() + '...'
            remaining = ''
        lines.append(candidate)
    return lines or ['-']


def _select_workflows(workflows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    valid: list[dict[str, Any]] = []
    seen: set[str] = set()
    for workflow in workflows:
        workflow_id = str(workflow.get('id') or '')
        if workflow_id and workflow_id not in seen:
            valid.append(workflow)
            seen.add(workflow_id)
    if len(valid) <= MAX_RICH_MENU_WORKFLOWS:
        return valid

    instant = [item for item in valid if str(item.get('launchMode') or 'instant') == 'instant']
    guided = [item for item in valid if str(item.get('launchMode') or 'instant') != 'instant']
    if not instant or not guided:
        return valid[:MAX_RICH_MENU_WORKFLOWS]

    # Keep both modes visible while retaining the server's priority order within each mode.
    selected_ids = {str(item.get('id')) for item in instant[:2] + guided[:2]}
    for item in valid:
        if len(selected_ids) >= MAX_RICH_MENU_WORKFLOWS:
            break
        selected_ids.add(str(item.get('id')))
    return [item for item in valid if str(item.get('id')) in selected_ids][:MAX_RICH_MENU_WORKFLOWS]


def _launch_presentation(launch_mode: str) -> tuple[str, str, str, str]:
    if launch_mode == 'instant':
        return '立即執行', '執行', '#1D4ED8', '#EFF6FF'
    if launch_mode == 'file_input':
        return '需要檔案', '上傳', '#047857', '#ECFDF5'
    if launch_mode == 'form_input':
        return '逐步填寫', '填寫', '#047857', '#ECFDF5'
    return '需要輸入', '輸入', '#047857', '#ECFDF5'


def _workflow_slots(workflows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    slots = []
    for workflow in _select_workflows(workflows):
        workflow_id = str(workflow.get('id') or '')
        launch_mode = str(workflow.get('launchMode') or 'instant')
        is_instant = launch_mode == 'instant'
        mode_label, footer, accent, background = _launch_presentation(launch_mode)
        name = _clean_label(workflow.get('name'), '快捷工作流')
        slots.append(
            {
                'kind': 'workflow',
                'label': _clean_label(workflow.get('buttonLabel') or name, name),
                'modeLabel': mode_label,
                'footer': footer,
                'accent': accent,
                'background': background,
                'launchMode': launch_mode,
                'action': {
                    'type': 'postback',
                    'label': '立即執行' if is_instant else '開始填寫',
                    'data': workflow_launch_postback(workflow_id),
                    'displayText': (f'執行：{name}' if is_instant else f'開始填寫：{name}')[:300],
                },
                'workflowId': workflow_id,
            }
        )
    return slots


def _utility_slots(liff_uri: str | None = None) -> list[dict[str, Any]]:
    slots: list[dict[str, Any]] = [
        {
            'kind': 'system',
            'label': '全部工作流',
            'icon': 'grid',
            'accent': '#16A34A',
            'action': {
                'type': 'postback',
                'label': '全部工作流',
                'data': system_postback('menu.workflows.v1'),
                'displayText': '開啟工作流選單',
            },
        },
        {
            'kind': 'system',
            'label': '自由提問',
            'icon': 'chat',
            'accent': '#7C3AED',
            'action': {
                'type': 'postback',
                'label': '自由提問',
                'data': system_postback('menu.ask.v1'),
                'inputOption': 'openKeyboard',
            },
        },
        {
            'kind': 'system',
            'label': '公司資料',
            'icon': 'database',
            'accent': '#0369A1',
            'action': {
                'type': 'postback',
                'label': '查詢公司資料',
                'data': system_postback('menu.data.v1'),
                'inputOption': 'openKeyboard',
                'fillInText': '請查詢公司資料：',
            },
        },
        {
            'kind': 'system',
            'label': '使用說明',
            'icon': 'help',
            'accent': '#C2410C',
            'action': {
                'type': 'postback',
                'label': '使用說明',
                'data': system_postback('menu.help.v1'),
                'displayText': '查看 LINE AI 助手使用說明',
            },
        },
    ]
    if liff_uri:
        slots[-1] = {
            'kind': 'liff',
            'label': 'AI 工作台',
            'icon': 'external',
            'accent': '#C2410C',
            'action': {
                'type': 'uri',
                'label': '開啟 AI 工作台',
                'uri': liff_uri,
            },
        }
    return slots


def _workflow_bounds(count: int) -> list[tuple[int, int, int, int]]:
    top = RICH_MENU_HEADER_HEIGHT
    bottom = RICH_MENU_HEIGHT - RICH_MENU_UTILITY_HEIGHT
    middle_x = RICH_MENU_WIDTH // 2
    middle_y = top + (bottom - top) // 2
    if count <= 0:
        return []
    if count == 1:
        return [(0, top, RICH_MENU_WIDTH, bottom)]
    if count == 2:
        return [(0, top, middle_x, bottom), (middle_x, top, RICH_MENU_WIDTH, bottom)]
    if count == 3:
        return [
            (0, top, RICH_MENU_WIDTH, middle_y),
            (0, middle_y, middle_x, bottom),
            (middle_x, middle_y, RICH_MENU_WIDTH, bottom),
        ]
    return [
        (0, top, middle_x, middle_y),
        (middle_x, top, RICH_MENU_WIDTH, middle_y),
        (0, middle_y, middle_x, bottom),
        (middle_x, middle_y, RICH_MENU_WIDTH, bottom),
    ]


def _draw_workflow_card(
    draw: ImageDraw.ImageDraw,
    slot: dict[str, Any],
    bounds: tuple[int, int, int, int],
) -> None:
    x0, y0, x1, y1 = bounds
    inset = 20
    card = (x0 + inset, y0 + inset, x1 - inset, y1 - inset)
    cx0, cy0, cx1, cy1 = card
    accent = slot['accent']
    draw.rounded_rectangle(card, radius=40, fill=slot['background'], outline='#D8E0EA', width=3)
    draw.rounded_rectangle((cx0, cy0, cx0 + 22, cy1), radius=11, fill=accent)

    pill_font = _font(66, 'Bold')
    pill_text = slot['modeLabel']
    pill_width = draw.textbbox((0, 0), pill_text, font=pill_font)[2] + 88
    pill = (cx0 + 58, cy0 + 32, cx0 + 58 + pill_width, cy0 + 142)
    draw.rounded_rectangle(pill, radius=55, fill=accent)
    draw.text((pill[0] + 44, pill[1] + 8), pill_text, font=pill_font, fill='#FFFFFF')

    title_font = _font(100, 'Bold')
    title_y = cy0 + 166
    for line in _fit_lines(draw, slot['label'], title_font, cx1 - cx0 - 132, 2):
        draw.text((cx0 + 58, title_y), line, font=title_font, fill='#111827')
        title_y += 118

    footer_font = _font(64, 'Bold')
    footer_y = cy1 - 112
    draw.text((cx0 + 58, footer_y), slot['footer'], font=footer_font, fill=accent)
    arrow_font = _font(92, 'Bold')
    draw.text((cx1 - 116, cy1 - 137), '›', font=arrow_font, fill=accent)


def _draw_utility_icon(
    draw: ImageDraw.ImageDraw,
    icon: str,
    center_x: int,
    center_y: int,
    color: str,
) -> None:
    draw.ellipse((center_x - 51, center_y - 51, center_x + 51, center_y + 51), fill=color)
    white = '#FFFFFF'
    if icon == 'grid':
        for dx in (-24, 8):
            for dy in (-24, 8):
                draw.rounded_rectangle(
                    (center_x + dx, center_y + dy, center_x + dx + 20, center_y + dy + 20),
                    radius=4,
                    outline=white,
                    width=5,
                )
    elif icon == 'chat':
        draw.rounded_rectangle(
            (center_x - 27, center_y - 22, center_x + 27, center_y + 19),
            radius=10,
            outline=white,
            width=6,
        )
        draw.polygon(
            [(center_x - 15, center_y + 17), (center_x - 24, center_y + 31), (center_x - 3, center_y + 19)],
            fill=white,
        )
    elif icon == 'database':
        draw.ellipse((center_x - 29, center_y - 27, center_x + 29, center_y - 5), outline=white, width=6)
        draw.line((center_x - 29, center_y - 16, center_x - 29, center_y + 22), fill=white, width=6)
        draw.line((center_x + 29, center_y - 16, center_x + 29, center_y + 22), fill=white, width=6)
        draw.arc((center_x - 29, center_y + 6, center_x + 29, center_y + 30), 0, 180, fill=white, width=6)
    elif icon == 'external':
        draw.rounded_rectangle(
            (center_x - 28, center_y - 20, center_x + 20, center_y + 28),
            radius=7,
            outline=white,
            width=6,
        )
        draw.line((center_x - 2, center_y + 2, center_x + 28, center_y - 28), fill=white, width=6)
        draw.line((center_x + 8, center_y - 28, center_x + 28, center_y - 28), fill=white, width=6)
        draw.line((center_x + 28, center_y - 28, center_x + 28, center_y - 8), fill=white, width=6)
    else:
        help_font = _font(58)
        box = draw.textbbox((0, 0), '?', font=help_font)
        draw.text(
            (center_x - (box[2] - box[0]) // 2, center_y - 39),
            '?',
            font=help_font,
            fill=white,
        )


def _draw_utility_slot(
    draw: ImageDraw.ImageDraw,
    slot: dict[str, Any],
    bounds: tuple[int, int, int, int],
) -> None:
    x0, y0, x1, _ = bounds
    center_x = (x0 + x1) // 2
    _draw_utility_icon(draw, slot['icon'], center_x, y0 + 76, slot['accent'])
    label_font = _font(72, 'Bold')
    label = slot['label']
    label_width = draw.textbbox((0, 0), label, font=label_font)[2]
    draw.text((center_x - label_width // 2, y0 + 137), label, font=label_font, fill='#111827')


def _draw_empty_state(draw: ImageDraw.ImageDraw) -> None:
    top = RICH_MENU_HEADER_HEIGHT
    bottom = RICH_MENU_HEIGHT - RICH_MENU_UTILITY_HEIGHT
    center_x = RICH_MENU_WIDTH // 2
    center_y = top + (bottom - top) // 2
    draw.rounded_rectangle(
        (center_x - 94, center_y - 170, center_x + 94, center_y + 18),
        radius=48,
        fill='#E8F8EE',
    )
    _draw_utility_icon(draw, 'grid', center_x, center_y - 76, '#16A34A')
    title = '尚無可用工作流'
    title_font = _font(100, 'Bold')
    title_width = draw.textbbox((0, 0), title, font=title_font)[2]
    draw.text((center_x - title_width // 2, center_y + 64), title, font=title_font, fill='#111827')


def build_line_rich_menu(
    workflows: list[dict[str, Any]],
    channel_name: str,
    *,
    liff_uri: str | None = None,
) -> LineRichMenuArtifact:
    workflow_slots = _workflow_slots(workflows)
    utility_slots = _utility_slots(liff_uri)

    image = Image.new('RGB', (RICH_MENU_WIDTH, RICH_MENU_HEIGHT), '#F4F7FB')
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, RICH_MENU_WIDTH, RICH_MENU_HEADER_HEIGHT), fill='#FFFFFF')
    title = _clean_label(channel_name, 'Interact AI')
    draw.rounded_rectangle((64, 55, 88, 159), radius=12, fill='#06C755')
    draw.text((128, 31), '常用工作流', font=_font(104, 'Bold'), fill='#111827')
    draw.line(
        (0, RICH_MENU_HEADER_HEIGHT - 1, RICH_MENU_WIDTH, RICH_MENU_HEADER_HEIGHT - 1),
        fill='#D8E0EA',
        width=2,
    )

    areas: list[dict[str, Any]] = []
    workflow_bounds = _workflow_bounds(len(workflow_slots))
    if not workflow_slots:
        _draw_empty_state(draw)
    for slot, bounds in zip(workflow_slots, workflow_bounds, strict=True):
        _draw_workflow_card(draw, slot, bounds)
        x0, y0, x1, y1 = bounds
        areas.append(
            {
                'bounds': {'x': x0, 'y': y0, 'width': x1 - x0, 'height': y1 - y0},
                'action': slot['action'],
            }
        )

    utility_top = RICH_MENU_HEIGHT - RICH_MENU_UTILITY_HEIGHT
    draw.rectangle((0, utility_top, RICH_MENU_WIDTH, RICH_MENU_HEIGHT), fill='#FFFFFF')
    draw.line((0, utility_top, RICH_MENU_WIDTH, utility_top), fill='#CBD5E1', width=3)
    cell_width = RICH_MENU_WIDTH // len(utility_slots)
    for index, slot in enumerate(utility_slots):
        x0 = index * cell_width
        x1 = RICH_MENU_WIDTH if index == len(utility_slots) - 1 else x0 + cell_width
        if index:
            draw.line((x0, utility_top + 46, x0, RICH_MENU_HEIGHT - 46), fill='#E2E8F0', width=3)
        bounds = (x0, utility_top, x1, RICH_MENU_HEIGHT)
        _draw_utility_slot(draw, slot, bounds)
        areas.append(
            {
                'bounds': {
                    'x': x0,
                    'y': utility_top,
                    'width': x1 - x0,
                    'height': RICH_MENU_UTILITY_HEIGHT,
                },
                'action': slot['action'],
            }
        )

    output = io.BytesIO()
    image.save(output, format='PNG', optimize=True)
    image_bytes = output.getvalue()
    menu = {
        'size': {'width': RICH_MENU_WIDTH, 'height': RICH_MENU_HEIGHT},
        'selected': True,
        'name': f'Interact AI dynamic menu - {title}'[:300],
        'chatBarText': '開啟 AI 工作台',
        'areas': areas,
    }
    digest = hashlib.sha256()
    digest.update(json.dumps(menu, ensure_ascii=False, sort_keys=True).encode())
    digest.update(image_bytes)
    return LineRichMenuArtifact(
        menu=menu,
        image=image_bytes,
        content_hash=digest.hexdigest(),
        workflow_ids=[slot['workflowId'] for slot in workflow_slots],
    )


_ROLE_TABS = {
    'guest': [('home', '帳號綁定')],
    'member': [('home', 'AI 工作台'), ('workflows', '工作流')],
    'admin': [('home', 'AI 工作台'), ('workflows', '工作流'), ('manage', '管理中心')],
}


def _uri_action(label: str, uri: str) -> dict[str, Any]:
    return {'type': 'uri', 'label': label[:20], 'uri': uri}


def _postback_action(label: str, action: str, *, keyboard: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {
        'type': 'postback',
        'label': label[:20],
        'data': system_postback(action),
    }
    if keyboard:
        result['inputOption'] = 'openKeyboard'
    else:
        result['displayText'] = label[:300]
    return result


def _role_menu_items(
    audience: str,
    tab: str,
    *,
    portal_base_url: str | None,
) -> list[dict[str, Any]]:
    if audience == 'guest':
        return [
            {
                'label': '綁定企業帳號',
                'icon': 'link',
                'accent': '#06C755',
                'action': _postback_action('綁定企業帳號', 'menu.account_link.v1'),
            },
            {
                'label': '試用 AI 問答',
                'icon': 'chat',
                'accent': '#2563EB',
                'action': _postback_action('試用 AI 問答', 'menu.ask.v1', keyboard=True),
            },
            {
                'label': '綁定狀態',
                'icon': 'user',
                'accent': '#7C3AED',
                'action': _postback_action('查看綁定狀態', 'menu.account_status.v1'),
            },
            {
                'label': '使用說明',
                'icon': 'help',
                'accent': '#EA580C',
                'action': _postback_action('使用說明', 'menu.help.v1'),
            },
        ]

    if tab == 'workflows':
        items = [
            {
                'label': '立即執行',
                'icon': 'play',
                'accent': '#2563EB',
                'action': _postback_action('查看立即執行工作流', 'menu.workflows.instant.v1'),
            },
            {
                'label': '需要輸入',
                'icon': 'form',
                'accent': '#059669',
                'action': _postback_action('查看需要輸入工作流', 'menu.workflows.guided.v1'),
            },
            {
                'label': '需要檔案',
                'icon': 'file',
                'accent': '#0891B2',
                'action': _postback_action('查看檔案工作流', 'menu.workflows.file.v1'),
            },
            {
                'label': '全部流程',
                'icon': 'grid',
                'accent': '#16A34A',
                'action': _postback_action('查看全部工作流', 'menu.workflows.v1'),
            },
            {
                'label': '執行紀錄',
                'icon': 'history',
                'accent': '#7C3AED',
                'action': _postback_action('查看執行紀錄', 'menu.history.v1'),
            },
            {
                'label': '我的帳號',
                'icon': 'user',
                'accent': '#475569',
                'action': _postback_action('查看帳號狀態', 'menu.account_status.v1'),
            },
            {
                'label': '使用說明',
                'icon': 'help',
                'accent': '#EA580C',
                'action': _postback_action('使用說明', 'menu.help.v1'),
            },
        ]
        items.append(
            {
                'label': '帳號管理',
                'icon': 'link',
                'accent': '#06C755',
                'action': (
                    _uri_action('帳號管理', f'{portal_base_url}/company-portal/ai-channels')
                    if portal_base_url
                    else _postback_action('帳號管理', 'menu.portal_required.v1')
                ),
            }
        )
        return items

    if audience == 'admin' and tab == 'manage':
        paths = [
            ('渠道管理', 'channel', '#06C755', '/company-portal/ai-channels'),
            ('工作流管理', 'flow', '#2563EB', '/company-portal/workflows'),
            ('資料連線', 'database', '#0891B2', '/company-portal/data-connectors'),
            ('成員管理', 'users', '#7C3AED', '/company-portal/profile'),
            ('用量紀錄', 'history', '#475569', '/company-portal/history'),
        ]
        items = []
        for label, icon, accent, path in paths:
            action = (
                _uri_action(label, f'{portal_base_url}{path}')
                if portal_base_url
                else _postback_action(label, 'menu.portal_required.v1')
            )
            items.append({'label': label, 'icon': icon, 'accent': accent, 'action': action})
        items.extend(
            [
                {
                    'label': '重新同步',
                    'icon': 'refresh',
                    'accent': '#16A34A',
                    'action': _postback_action('重新同步選單', 'menu.resync.v1'),
                },
                {
                    'label': '我的帳號',
                    'icon': 'user',
                    'accent': '#475569',
                    'action': _postback_action('查看帳號狀態', 'menu.account_status.v1'),
                },
                {
                    'label': '使用說明',
                    'icon': 'help',
                    'accent': '#EA580C',
                    'action': _postback_action('使用說明', 'menu.help.v1'),
                },
            ]
        )
        return items

    return [
        {
            'label': '自由提問',
            'icon': 'chat',
            'accent': '#2563EB',
            'action': _postback_action('自由提問', 'menu.ask.v1', keyboard=True),
        },
        {
            'label': '公司資料',
            'icon': 'database',
            'accent': '#0891B2',
            'action': _postback_action('查詢公司資料', 'menu.data.v1', keyboard=True),
        },
        {
            'label': '上傳分析',
            'icon': 'file',
            'accent': '#059669',
            'action': _postback_action('上傳檔案分析', 'menu.file.v1', keyboard=True),
        },
        {
            'label': '執行紀錄',
            'icon': 'history',
            'accent': '#7C3AED',
            'action': _postback_action('查看執行紀錄', 'menu.history.v1'),
        },
        {
            'label': '立即工作流',
            'icon': 'play',
            'accent': '#2563EB',
            'action': _postback_action('查看立即執行工作流', 'menu.workflows.instant.v1'),
        },
        {
            'label': '引導工作流',
            'icon': 'form',
            'accent': '#059669',
            'action': _postback_action('查看需要輸入工作流', 'menu.workflows.guided.v1'),
        },
        {
            'label': '全部工作流',
            'icon': 'grid',
            'accent': '#16A34A',
            'action': _postback_action('查看全部工作流', 'menu.workflows.v1'),
        },
        {
            'label': '我的帳號',
            'icon': 'user',
            'accent': '#475569',
            'action': _postback_action('查看帳號狀態', 'menu.account_status.v1'),
        },
    ]


def _draw_role_icon(  # noqa: C901 - each branch draws one small, dependency-free icon
    draw: ImageDraw.ImageDraw,
    icon: str,
    center_x: int,
    center_y: int,
    color: str,
) -> None:
    draw.rounded_rectangle(
        (center_x - 74, center_y - 74, center_x + 74, center_y + 74),
        radius=36,
        fill='#F8FAFC',
        outline=color,
        width=7,
    )
    x, y = center_x, center_y
    if icon in {'chat', 'database', 'user', 'help'}:
        symbol = {'chat': 'AI', 'database': 'DB', 'user': 'ID', 'help': '?'}[icon]
        font = _font(64 if len(symbol) > 1 else 78, 'Bold')
        box = draw.textbbox((0, 0), symbol, font=font)
        draw.text(
            (x - (box[2] - box[0]) // 2, y - (box[3] - box[1]) // 2 - 8),
            symbol,
            font=font,
            fill=color,
        )
    elif icon == 'play':
        draw.polygon([(x - 27, y - 40), (x + 42, y), (x - 27, y + 40)], fill=color)
    elif icon == 'grid':
        for dx in (-42, 10):
            for dy in (-42, 10):
                draw.rounded_rectangle((x + dx, y + dy, x + dx + 32, y + dy + 32), radius=6, fill=color)
    elif icon in {'form', 'file'}:
        draw.rounded_rectangle((x - 39, y - 48, x + 39, y + 48), radius=8, outline=color, width=7)
        if icon == 'form':
            for dy in (-24, 0, 24):
                draw.rectangle((x - 25, y + dy - 4, x - 17, y + dy + 4), fill=color)
                draw.line((x - 8, y + dy, x + 24, y + dy), fill=color, width=6)
        else:
            for dy in (-18, 4, 26):
                draw.line((x - 22, y + dy, x + 22, y + dy), fill=color, width=6)
    elif icon == 'link':
        draw.rounded_rectangle((x - 48, y - 25, x + 5, y + 25), radius=22, outline=color, width=7)
        draw.rounded_rectangle((x - 5, y - 25, x + 48, y + 25), radius=22, outline=color, width=7)
        draw.line((x - 17, y, x + 17, y), fill=color, width=7)
    elif icon in {'history', 'refresh'}:
        draw.arc((x - 47, y - 47, x + 47, y + 47), 35, 300, fill=color, width=8)
        draw.polygon([(x - 47, y - 10), (x - 50, y - 43), (x - 19, y - 34)], fill=color)
        if icon == 'history':
            draw.line((x, y, x, y - 27), fill=color, width=7)
            draw.line((x, y, x + 25, y + 14), fill=color, width=7)
    elif icon == 'channel':
        draw.ellipse((x - 13, y - 13, x + 13, y + 13), fill=color)
        draw.arc((x - 42, y - 42, x + 42, y + 42), 205, 335, fill=color, width=7)
        draw.arc((x - 59, y - 59, x + 59, y + 59), 205, 335, fill=color, width=7)
    elif icon == 'users':
        draw.ellipse((x - 38, y - 40, x - 4, y - 6), outline=color, width=7)
        draw.ellipse((x + 6, y - 35, x + 36, y - 5), outline=color, width=7)
        draw.arc((x - 51, y - 4, x + 7, y + 48), 180, 360, fill=color, width=7)
        draw.arc((x - 4, y, x + 50, y + 45), 180, 360, fill=color, width=7)
    elif icon == 'flow':
        draw.ellipse((x - 47, y - 39, x - 17, y - 9), fill=color)
        draw.ellipse((x + 17, y - 7, x + 47, y + 23), fill=color)
        draw.ellipse((x - 47, y + 21, x - 17, y + 51), fill=color)
        draw.line((x - 16, y - 24, x + 16, y + 7), fill=color, width=7)
        draw.line((x - 16, y + 36, x + 16, y + 8), fill=color, width=7)


def _role_menu_image(
    audience: str,
    active_tab: str,
    tabs: list[tuple[str, str]],
    items: list[dict[str, Any]],
) -> bytes:
    if audience == 'guest':
        if len(items) != 4:
            raise ValueError('The guest LINE Rich Menu requires four actions.')
        image = Image.new('RGB', (RICH_MENU_WIDTH, RICH_MENU_HEIGHT), '#FFFFFF')
        draw = ImageDraw.Draw(image)
        primary = items[0]
        draw.rectangle((0, 0, RICH_MENU_WIDTH, GUEST_PRIMARY_HEIGHT), fill='#06C755')
        _draw_role_icon(draw, primary['icon'], 350, 470, '#06C755')
        draw.text((640, 268), primary['label'], font=_font(154, 'Bold'), fill='#FFFFFF')
        draw.text(
            (646, 502),
            '取得公司工作流與資料權限',
            font=_font(76, 'Regular'),
            fill='#E9FFF1',
        )
        draw.text((646, 685), '點一下開始安全綁定  >', font=_font(78, 'Bold'), fill='#FFFFFF')

        secondary_top = GUEST_PRIMARY_HEIGHT
        secondary_width = RICH_MENU_WIDTH // 3
        secondary_font = _font(78, 'Bold')
        draw.line((0, secondary_top, RICH_MENU_WIDTH, secondary_top), fill='#CFE9D8', width=4)
        for index, item in enumerate(items[1:]):
            x0 = index * secondary_width
            x1 = RICH_MENU_WIDTH if index == 2 else x0 + secondary_width
            if index:
                draw.line((x0, secondary_top + 54, x0, RICH_MENU_HEIGHT - 54), fill='#E2E8F0', width=4)
            center_x = (x0 + x1) // 2
            _draw_role_icon(draw, item['icon'], center_x, secondary_top + 220, item['accent'])
            label_width = draw.textbbox((0, 0), item['label'], font=secondary_font)[2]
            draw.text(
                (center_x - label_width // 2, secondary_top + 406),
                item['label'],
                font=secondary_font,
                fill='#111827',
            )
        output = io.BytesIO()
        image.save(output, format='PNG', optimize=True)
        return output.getvalue()

    image = Image.new('RGB', (RICH_MENU_WIDTH, RICH_MENU_HEIGHT), '#FFFFFF')
    draw = ImageDraw.Draw(image)
    tab_height = 260
    tab_width = RICH_MENU_WIDTH // len(tabs)
    tab_font = _font(82, 'Bold')
    for index, (tab, label) in enumerate(tabs):
        x0 = index * tab_width
        x1 = RICH_MENU_WIDTH if index == len(tabs) - 1 else x0 + tab_width
        active = tab == active_tab
        draw.rectangle((x0, 0, x1, tab_height), fill='#06C755' if active else '#FFFFFF')
        draw.line((x0, tab_height - 2, x1, tab_height - 2), fill='#D9E2EC', width=3)
        width = draw.textbbox((0, 0), label, font=tab_font)[2]
        draw.text(
            ((x0 + x1 - width) // 2, 72),
            label,
            font=tab_font,
            fill='#FFFFFF' if active else '#166534',
        )

    rows = 1 if len(items) <= 4 else 2
    grid_top = tab_height
    row_height = (RICH_MENU_HEIGHT - grid_top) // rows
    cell_width = RICH_MENU_WIDTH // 4
    label_font = _font(78, 'Bold')
    for index, item in enumerate(items):
        row, column = divmod(index, 4)
        x0, y0 = column * cell_width, grid_top + row * row_height
        x1 = RICH_MENU_WIDTH if column == 3 else x0 + cell_width
        y1 = RICH_MENU_HEIGHT if row == rows - 1 else y0 + row_height
        draw.rectangle((x0, y0, x1, y1), fill='#FFFFFF', outline='#E2E8F0', width=3)
        center_x = (x0 + x1) // 2
        icon_y = y0 + (178 if rows == 2 else 360)
        _draw_role_icon(draw, item['icon'], center_x, icon_y, item['accent'])
        label = item['label']
        label_width = draw.textbbox((0, 0), label, font=label_font)[2]
        draw.text(
            (center_x - label_width // 2, icon_y + 108),
            label,
            font=label_font,
            fill='#111827',
        )
    output = io.BytesIO()
    image.save(output, format='PNG', optimize=True)
    return output.getvalue()


def build_line_role_menus(
    *,
    audience: str,
    alias_ids: dict[str, str],
    channel_name: str,
    portal_base_url: str | None = None,
) -> list[LineRoleMenuArtifact]:
    if audience not in _ROLE_TABS:
        raise ValueError('Unsupported LINE Rich Menu audience.')
    portal_base_url = str(portal_base_url or '').strip().rstrip('/') or None
    tabs = _ROLE_TABS[audience]
    artifacts: list[LineRoleMenuArtifact] = []
    for tab, _tab_label in tabs:
        alias_id = alias_ids.get(tab)
        if not alias_id:
            raise ValueError(f'Missing Rich Menu alias for {audience}:{tab}.')
        items = _role_menu_items(audience, tab, portal_base_url=portal_base_url)
        image_bytes = _role_menu_image(audience, tab, tabs, items)
        areas: list[dict[str, Any]] = []
        if audience == 'guest':
            areas.append(
                {
                    'bounds': {
                        'x': 0,
                        'y': 0,
                        'width': RICH_MENU_WIDTH,
                        'height': GUEST_PRIMARY_HEIGHT,
                    },
                    'action': items[0]['action'],
                }
            )
            cell_width = RICH_MENU_WIDTH // 3
            for index, item in enumerate(items[1:]):
                x0 = index * cell_width
                x1 = RICH_MENU_WIDTH if index == 2 else x0 + cell_width
                areas.append(
                    {
                        'bounds': {
                            'x': x0,
                            'y': GUEST_PRIMARY_HEIGHT,
                            'width': x1 - x0,
                            'height': RICH_MENU_HEIGHT - GUEST_PRIMARY_HEIGHT,
                        },
                        'action': item['action'],
                    }
                )
        else:
            tab_width = RICH_MENU_WIDTH // len(tabs)
            for index, (target_tab, label) in enumerate(tabs):
                x0 = index * tab_width
                x1 = RICH_MENU_WIDTH if index == len(tabs) - 1 else x0 + tab_width
                action: dict[str, Any]
                if target_tab == tab:
                    action = _postback_action(label, 'menu.tab.active.v1')
                else:
                    action = {
                        'type': 'richmenuswitch',
                        'label': label[:20],
                        'richMenuAliasId': alias_ids[target_tab],
                        'data': system_postback(f'menu.tab.{target_tab}.v1'),
                    }
                areas.append(
                    {
                        'bounds': {'x': x0, 'y': 0, 'width': x1 - x0, 'height': 260},
                        'action': action,
                    }
                )
            rows = 1 if len(items) <= 4 else 2
            row_height = (RICH_MENU_HEIGHT - 260) // rows
            cell_width = RICH_MENU_WIDTH // 4
            for index, item in enumerate(items):
                row, column = divmod(index, 4)
                x0, y0 = column * cell_width, 260 + row * row_height
                x1 = RICH_MENU_WIDTH if column == 3 else x0 + cell_width
                y1 = RICH_MENU_HEIGHT if row == rows - 1 else y0 + row_height
                areas.append(
                    {
                        'bounds': {'x': x0, 'y': y0, 'width': x1 - x0, 'height': y1 - y0},
                        'action': item['action'],
                    }
                )
        menu = {
            'size': {'width': RICH_MENU_WIDTH, 'height': RICH_MENU_HEIGHT},
            'selected': True,
            'name': f'Interact AI {audience} {tab} - {_clean_label(channel_name, "LINE")}'[:300],
            'chatBarText': '綁定企業帳號' if audience == 'guest' else '開啟 AI 工作台',
            'areas': areas,
        }
        digest = hashlib.sha256()
        digest.update(json.dumps(menu, ensure_ascii=False, sort_keys=True).encode())
        digest.update(image_bytes)
        artifacts.append(
            LineRoleMenuArtifact(
                audience=audience,
                tab=tab,
                alias_id=alias_id,
                menu=menu,
                image=image_bytes,
                content_hash=digest.hexdigest(),
            )
        )
    return artifacts

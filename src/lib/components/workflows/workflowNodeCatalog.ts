export type WorkflowNodeCategory =
	| 'start'
	| 'ai'
	| 'knowledge'
	| 'transform'
	| 'control'
	| 'output';

export type WorkflowConfigField = {
	key: string;
	label: string;
	type: 'text' | 'textarea' | 'number' | 'select' | 'checkbox' | 'tags' | 'json';
	help?: string;
	placeholder?: string;
	required?: boolean;
	min?: number;
	max?: number;
	step?: number;
	options?: { value: string; label: string }[];
};

export type WorkflowNodeDefinition = {
	type: string;
	label: string;
	category: WorkflowNodeCategory;
	description: string;
	keywords?: string[];
	inputType: 'none' | 'message' | 'data' | 'any';
	outputType: 'none' | 'message' | 'data' | 'media' | 'any';
	recommended?: boolean;
	defaultConfig?: Record<string, unknown>;
	configFields?: WorkflowConfigField[];
};

export type WorkflowTemplate = {
	id: string;
	name: string;
	description: string;
	nodeTypes: string[];
};

export const WORKFLOW_NODE_GROUPS: {
	id: WorkflowNodeCategory;
	label: string;
	description: string;
}[] = [
	{ id: 'start', label: '開始', description: '決定工作流從哪裡取得訊息與檔案。' },
	{ id: 'ai', label: 'AI 與代理', description: '提示、模型推理與代理步驟。' },
	{ id: 'knowledge', label: '知識與資料', description: '查詢知識庫、語意資料集與授權資料表。' },
	{ id: 'transform', label: '資料處理', description: '整理文字、JSON 與確定性計算。' },
	{ id: 'control', label: '流程控制', description: '合併多個上游步驟。' },
	{ id: 'output', label: '回覆與交付', description: '將文字、檔案或多媒體送回使用者。' }
];

const MODEL_FIELDS: WorkflowConfigField[] = [
	{
		key: 'model_id',
		label: '使用模型',
		type: 'select',
		help: '留空時使用測試或聊天當下指定的模型。'
	},
	{
		key: 'system_prompt',
		label: '系統指示',
		type: 'textarea',
		placeholder: '說明角色、任務、限制與輸出格式。'
	}
];

export const WORKFLOW_NODE_DEFINITIONS: WorkflowNodeDefinition[] = [
	{
		type: 'chat_input',
		label: '站內聊天輸入',
		category: 'start',
		description: '從主站聊天取得文字、檔案與對話上下文。',
		keywords: ['chat', '訊息', 'webui'],
		inputType: 'none',
		outputType: 'message',
		recommended: true
	},
	{
		type: 'channel_input',
		label: '通訊頻道輸入',
		category: 'start',
		description: '從 LINE、Telegram、WeChat 等已綁定頻道取得訊息。',
		keywords: ['line', 'telegram', 'wechat', 'bot'],
		inputType: 'none',
		outputType: 'message',
		recommended: true
	},
	{
		type: 'user_input',
		label: '使用者輸入引導',
		category: 'start',
		description: '定義工作流在聊天與通訊頻道中的啟動方式、提示、欄位與檔案限制。',
		keywords: ['guide', 'launch', 'form', 'line', '引導', '欄位', '表單'],
		inputType: 'none',
		outputType: 'any',
		recommended: true,
		defaultConfig: { launch: null }
	},
	{
		type: 'file_upload',
		label: '檔案與多媒體輸入',
		category: 'start',
		description: '接收文件、圖片、音訊或影片，保留完整 parts 資料。',
		keywords: ['file', 'image', 'audio', 'video', '圖片', '音訊'],
		inputType: 'none',
		outputType: 'message'
	},
	{
		type: 'form_input',
		label: '結構化表單輸入',
		category: 'start',
		description: '接收由主站或 API 傳入的結構化 data 欄位。',
		keywords: ['form', '欄位', 'data'],
		inputType: 'none',
		outputType: 'data'
	},
	{
		type: 'system_prompt',
		label: '系統指示',
		category: 'ai',
		description: '集中定義代理角色、限制與回覆原則。',
		keywords: ['prompt', 'instruction', '角色'],
		inputType: 'any',
		outputType: 'any',
		recommended: true,
		defaultConfig: { text: '你是企業 AI 助手。請只根據可用資料回答；不確定時清楚說明。' },
		configFields: [
			{
				key: 'text',
				label: '系統指示',
				type: 'textarea',
				required: true,
				placeholder: '你是企業 AI 助手...'
			}
		]
	},
	{
		type: 'prompt_template',
		label: '提示範本',
		category: 'ai',
		description: '用 {{message}}、{{input}} 與輸入資料組合提示。',
		keywords: ['template', '變數', 'prompt'],
		inputType: 'any',
		outputType: 'message',
		recommended: true,
		defaultConfig: { template: '{{input}}' },
		configFields: [
			{
				key: 'template',
				label: '提示內容',
				type: 'textarea',
				required: true,
				help: '可使用 {{message}}、{{input}}，以及輸入 data/context 內的欄位。'
			}
		]
	},
	{
		type: 'agent',
		label: 'AI 代理',
		category: 'ai',
		description: '根據上游資料與系統指示產生最終推理回覆。',
		keywords: ['agent', 'llm', '模型'],
		inputType: 'any',
		outputType: 'message',
		recommended: true,
		configFields: MODEL_FIELDS
	},
	{
		type: 'chat_model',
		label: 'LLM 文字生成',
		category: 'ai',
		description: '以指定模型執行單次文字生成。',
		keywords: ['model', 'llm', '生成'],
		inputType: 'any',
		outputType: 'message',
		configFields: MODEL_FIELDS
	},
	{
		type: 'vision_model',
		label: '圖片理解',
		category: 'ai',
		description: '將上傳圖片連同文字提示交給支援視覺的模型。',
		keywords: ['vision', 'image', '圖片', 'ocr'],
		inputType: 'message',
		outputType: 'message',
		configFields: MODEL_FIELDS
	},
	{
		type: 'planner',
		label: '任務規劃',
		category: 'ai',
		description: '先把複雜需求整理成步驟，再交給下游處理。',
		keywords: ['plan', '規劃', '步驟'],
		inputType: 'any',
		outputType: 'message',
		configFields: MODEL_FIELDS
	},
	{
		type: 'evaluator',
		label: '結果評估',
		category: 'ai',
		description: '使用模型檢查上游答案是否完整、正確且符合政策。',
		keywords: ['evaluate', 'review', '檢查'],
		inputType: 'any',
		outputType: 'message',
		configFields: MODEL_FIELDS
	},
	{
		type: 'knowledge_query',
		label: '知識庫搜尋',
		category: 'knowledge',
		description: '在使用者有讀取權的知識庫中進行語意搜尋。',
		keywords: ['rag', 'knowledge', '向量', '文件'],
		inputType: 'any',
		outputType: 'data',
		recommended: true,
		defaultConfig: { knowledge_ids: [], count: 5 },
		configFields: [
			{
				key: 'knowledge_ids',
				label: '知識庫 ID',
				type: 'tags',
				help: '用逗號分隔；留空時搜尋目前使用者可存取的知識庫。'
			},
			{
				key: 'query',
				label: '搜尋問題',
				type: 'text',
				placeholder: '{{message}}',
				help: '留空時使用上游文字或原始使用者訊息。'
			},
			{ key: 'count', label: '最多結果數', type: 'number', min: 1, max: 20, step: 1 }
		]
	},
	{
		type: 'semantic_query',
		label: '企業語意資料查詢',
		category: 'knowledge',
		description: '依已發布資料集安全執行跨表聚合、排名與期間分析。',
		keywords: ['dataset', 'semantic', '排名', '統計', 'sql'],
		inputType: 'any',
		outputType: 'data',
		recommended: true,
		defaultConfig: {
			dataset_id: '',
			use_incoming_plan: false,
			plan: {
				version: '1',
				datasetId: '',
				dimensions: [],
				measures: [],
				metrics: [],
				orderBy: [],
				limit: 20
			}
		},
		configFields: [
			{
				key: 'dataset_id',
				label: '已發布資料集',
				type: 'select',
				required: true,
				help: '只會列出目前帳號可存取的語意資料集。'
			},
			{
				key: 'use_incoming_plan',
				label: '接受上游 Query Plan',
				type: 'checkbox',
				help: '僅在上游會產生受控 Query Plan 時啟用。'
			},
			{
				key: 'plan',
				label: '預設 Query Plan',
				type: 'json',
				help: '資料集 ID 會以本欄位上方的選擇為準。'
			}
		]
	},
	{
		type: 'database_query',
		label: '授權資料表查詢',
		category: 'knowledge',
		description: '讀取單一已授權資料表；跨表分析請用企業語意資料查詢。',
		keywords: ['database', 'table', '資料庫'],
		inputType: 'any',
		outputType: 'data',
		defaultConfig: { connector_id: 'webui_local', operation: 'select', columns: [], limit: 20 },
		configFields: [
			{ key: 'connector_id', label: 'Connector ID', type: 'text', required: true },
			{
				key: 'table',
				label: '授權資料表',
				type: 'text',
				required: true,
				placeholder: 'schema.table'
			},
			{
				key: 'operation',
				label: '操作',
				type: 'select',
				options: [
					{ value: 'select', label: '讀取資料列' },
					{ value: 'count', label: '計算筆數' }
				]
			},
			{ key: 'columns', label: '回傳欄位', type: 'tags', help: '用逗號分隔；必須在欄位白名單內。' },
			{
				key: 'filters',
				label: '篩選條件',
				type: 'json',
				help: '使用安全 filter 物件，不接受原始 SQL。'
			},
			{ key: 'limit', label: '最多資料列', type: 'number', min: 1, max: 1000, step: 1 }
		]
	},
	{
		type: 'calculator',
		label: '計算器',
		category: 'transform',
		description: '執行受限制的四則運算，避免讓 LLM 猜計算結果。',
		keywords: ['math', 'calculate', '計算'],
		inputType: 'any',
		outputType: 'message',
		defaultConfig: { expression: '{{input}}' },
		configFields: [
			{
				key: 'expression',
				label: '運算式',
				type: 'text',
				required: true,
				help: '支援 +、-、*、/、//、%、** 與括號，可使用 {{input}}。'
			}
		]
	},
	{
		type: 'transform_json',
		label: '建立 JSON',
		category: 'transform',
		description: '以固定 JSON 物件取代上游值，適合建立 API 或輸出資料。',
		keywords: ['json', 'mapping', '轉換'],
		inputType: 'any',
		outputType: 'data',
		defaultConfig: { value: {} },
		configFields: [{ key: 'value', label: 'JSON 值', type: 'json', required: true }]
	},
	{
		type: 'extract_fields',
		label: '欄位骨架',
		category: 'transform',
		description: '建立指定欄位並保留原始文字，供下游轉換或檢查。',
		keywords: ['fields', 'parse', '欄位'],
		inputType: 'any',
		outputType: 'data',
		defaultConfig: { fields: [] },
		configFields: [{ key: 'fields', label: '欄位名稱', type: 'tags', required: true }]
	},
	{
		type: 'merge',
		label: '合併上游結果',
		category: 'control',
		description: '等待所有相連的上游節點完成，並將結果合併成陣列。',
		keywords: ['merge', 'join', '合併'],
		inputType: 'any',
		outputType: 'any'
	},
	{
		type: 'chat_output',
		label: '回覆站內聊天',
		category: 'output',
		description: '將上游內容回覆到主站聊天。',
		keywords: ['chat', 'reply', '回覆'],
		inputType: 'any',
		outputType: 'none',
		recommended: true,
		defaultConfig: { output_type: 'text' },
		configFields: [
			{
				key: 'output_type',
				label: '輸出格式',
				type: 'select',
				options: [
					{ value: 'text', label: '文字' },
					{ value: 'json', label: 'JSON' },
					{ value: 'card', label: '卡片' }
				]
			}
		]
	},
	{
		type: 'channel_reply',
		label: '回覆通訊頻道',
		category: 'output',
		description: '由頻道轉接器將回覆送回 LINE、Telegram 或 WeChat。',
		keywords: ['line', 'telegram', 'wechat', 'reply'],
		inputType: 'any',
		outputType: 'none',
		recommended: true,
		defaultConfig: { output_type: 'text' },
		configFields: [
			{
				key: 'output_type',
				label: '輸出格式',
				type: 'select',
				options: [
					{ value: 'text', label: '文字' },
					{ value: 'json', label: 'JSON' },
					{ value: 'card', label: '卡片' }
				]
			}
		]
	},
	{
		type: 'media_output',
		label: '圖片／音訊／影片輸出',
		category: 'output',
		description: '將可公開存取或受簽章保護的媒體 URL 交給頻道轉接器。',
		keywords: ['image', 'audio', 'video', 'media'],
		inputType: 'any',
		outputType: 'none',
		defaultConfig: { output_type: 'image', url: '', alt: '' },
		configFields: [
			{
				key: 'output_type',
				label: '媒體類型',
				type: 'select',
				options: [
					{ value: 'image', label: '圖片' },
					{ value: 'audio', label: '音訊' },
					{ value: 'video', label: '影片' }
				]
			},
			{ key: 'url', label: '媒體 URL', type: 'text', required: true },
			{ key: 'alt', label: '替代文字', type: 'text' }
		]
	},
	{
		type: 'file_output',
		label: '檔案輸出',
		category: 'output',
		description: '回傳檔案 URL、檔名與 MIME type。',
		keywords: ['file', 'download', '檔案'],
		inputType: 'any',
		outputType: 'none',
		defaultConfig: { output_type: 'file', url: '', filename: '' },
		configFields: [
			{ key: 'url', label: '檔案 URL', type: 'text', required: true },
			{ key: 'filename', label: '檔名', type: 'text', required: true },
			{ key: 'mimeType', label: 'MIME type', type: 'text', placeholder: 'application/pdf' }
		]
	},
	{
		type: 'handoff',
		label: '轉交人工',
		category: 'output',
		description: '回傳標準 handoff 結果，交由主站或頻道進入人工處理。',
		keywords: ['human', '客服', '人工'],
		inputType: 'any',
		outputType: 'none',
		defaultConfig: { reason: '' },
		configFields: [
			{
				key: 'reason',
				label: '轉交原因',
				type: 'textarea',
				help: '留空時使用上游內容。'
			}
		]
	},
	{
		type: 'webhook_response',
		label: 'Webhook JSON 回應',
		category: 'output',
		description: '將上游資料包裝成標準 JSON 工作流輸出。',
		keywords: ['webhook', 'api', 'json'],
		inputType: 'any',
		outputType: 'none'
	}
];

export const WORKFLOW_NODE_BY_TYPE = new Map(
	WORKFLOW_NODE_DEFINITIONS.map((definition) => [definition.type, definition])
);

export const WORKFLOW_TEMPLATES: WorkflowTemplate[] = [
	{
		id: 'chat-assistant',
		name: '站內聊天助理',
		description: '聊天輸入、系統指示、AI 代理與站內回覆。',
		nodeTypes: ['chat_input', 'system_prompt', 'agent', 'chat_output']
	},
	{
		id: 'channel-assistant',
		name: 'LINE／Telegram 助理',
		description: '接收頻道訊息，交由 AI 代理處理並回覆原頻道。',
		nodeTypes: ['channel_input', 'system_prompt', 'agent', 'channel_reply']
	},
	{
		id: 'semantic-data-answer',
		name: '企業資料問答',
		description: '查詢已發布語意資料集，再由模型整理成易讀答案。',
		nodeTypes: ['channel_input', 'semantic_query', 'prompt_template', 'agent', 'channel_reply']
	},
	{
		id: 'knowledge-answer',
		name: '知識庫問答',
		description: '搜尋授權知識庫，以檢索結果回答使用者問題。',
		nodeTypes: ['chat_input', 'knowledge_query', 'prompt_template', 'agent', 'chat_output']
	},
	{
		id: 'image-understanding',
		name: '圖片理解',
		description: '接收圖片與文字，交由視覺模型分析後回覆。',
		nodeTypes: ['file_upload', 'vision_model', 'channel_reply']
	}
];

const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value));

export const buildWorkflowNode = (type: string, id: string, position: { x: number; y: number }) => {
	const definition = WORKFLOW_NODE_BY_TYPE.get(type);
	if (!definition) throw new Error(`Unknown workflow node type: ${type}`);

	return {
		id,
		type: 'workflow',
		position,
		data: {
			label: definition.label,
			type: definition.type,
			category: definition.category,
			description: definition.description,
			inputType: definition.inputType,
			outputType: definition.outputType,
			config: clone(definition.defaultConfig ?? {})
		}
	};
};

export const buildWorkflowTemplateGraph = (templateId: string) => {
	const template = WORKFLOW_TEMPLATES.find((item) => item.id === templateId);
	if (!template) throw new Error(`Unknown workflow template: ${templateId}`);

	const nodes = template.nodeTypes.map((type, index) =>
		buildWorkflowNode(type, `${type}-${index + 1}`, {
			x: 100 + index * 320,
			y: index % 2 === 0 ? 180 : 300
		})
	);

	if (templateId === 'semantic-data-answer' || templateId === 'knowledge-answer') {
		const prompt = nodes.find((node) => node.data.type === 'prompt_template');
		if (prompt) {
			prompt.data.config = {
				template:
					'請根據以下授權資料回答使用者問題。若資料不足，請明確說明，不要編造。\n\n使用者問題：{{message}}\n\n授權資料：\n{{input}}'
			};
		}
	}

	return {
		nodes,
		edges: nodes.slice(0, -1).map((node, index) => ({
			id: `${node.id}-${nodes[index + 1].id}`,
			source: node.id,
			target: nodes[index + 1].id,
			type: 'smoothstep'
		}))
	};
};

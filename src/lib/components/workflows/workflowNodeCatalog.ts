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
		defaultConfig: { knowledge_ids: [], count: 5, preserve_input: false },
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
			{ key: 'count', label: '最多結果數', type: 'number', min: 1, max: 20, step: 1 },
			{
				key: 'preserve_input',
				label: '保留上游資料',
				type: 'checkbox',
				help: '開啟後會把檢索結果附加到上游資料，適合客戶查詢後再撰寫郵件。'
			}
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
		type: 'structured_extract',
		label: '結構化需求擷取',
		category: 'ai',
		description: '依 JSON Schema 從自然語言擷取客戶名稱、寄信要求與 CC，不允許模型新增欄位。',
		keywords: ['extract', 'schema', '客戶', '寄信', 'cc'],
		inputType: 'any',
		outputType: 'data',
		recommended: true,
		defaultConfig: {
			instruction: '擷取客戶名稱、寄信目的與使用者明確提供的 CC。不要猜測電子郵件地址。',
			schema: {
				type: 'object',
				properties: {
					customer_name: { type: 'string', description: '使用者指定的客戶或公司名稱' },
					request: { type: 'string', description: '要寄送的內容或目的' },
					cc: { type: 'array', items: { type: 'string' }, description: '使用者明確指定的 CC' }
				},
				required: ['customer_name', 'request']
			}
		},
		configFields: [
			{
				key: 'model_id',
				label: '擷取模型',
				type: 'select',
				help: '留空時使用聊天或測試指定模型。'
			},
			{ key: 'instruction', label: '擷取規則', type: 'textarea', required: true },
			{ key: 'schema', label: '輸出 JSON Schema', type: 'json', required: true }
		]
	},
	{
		type: 'customer_contact_lookup',
		label: '客戶聯絡人查詢',
		category: 'knowledge',
		description: '從已發布語意資料集尋找客戶與主要 Email；不綁定特定 CRM 資料表。',
		keywords: ['customer', 'contact', 'email', '客戶', '聯絡人'],
		inputType: 'data',
		outputType: 'data',
		recommended: true,
		defaultConfig: {
			dataset_id: '',
			query_path: 'customer_name',
			customer_id_field: 'customer.id',
			customer_name_field: 'customer.name',
			contact_name_field: 'contact.name',
			customer_email_field: 'contact.email',
			primary_field: 'contact.is_primary',
			opt_out_field: 'contact.email_opt_out',
			max_candidates: 10
		},
		configFields: [
			{ key: 'dataset_id', label: '客戶聯絡資料集', type: 'select', required: true },
			{
				key: 'query_path',
				label: '上游客戶名稱欄位',
				type: 'text',
				required: true,
				help: '例如 customer_name。'
			},
			{ key: 'customer_id_field', label: '客戶 ID 語意欄位', type: 'text' },
			{ key: 'customer_name_field', label: '客戶名稱語意欄位', type: 'text', required: true },
			{ key: 'contact_name_field', label: '聯絡人名稱語意欄位', type: 'text' },
			{ key: 'customer_email_field', label: 'Email 語意欄位', type: 'text', required: true },
			{ key: 'primary_field', label: '主要聯絡人語意欄位', type: 'text' },
			{ key: 'opt_out_field', label: '拒收語意欄位', type: 'text' },
			{ key: 'max_candidates', label: '候選結果上限', type: 'number', min: 2, max: 20, step: 1 }
		]
	},
	{
		type: 'condition',
		label: '條件分支',
		category: 'control',
		description: '依欄位值只執行「符合」或「不符合」其中一條路徑。',
		keywords: ['if', 'condition', 'branch', '條件', '分支'],
		inputType: 'any',
		outputType: 'any',
		recommended: true,
		defaultConfig: { field: 'status', operator: 'eq', value: 'found' },
		configFields: [
			{ key: 'field', label: '判斷欄位路徑', type: 'text', placeholder: 'status' },
			{
				key: 'operator',
				label: '判斷方式',
				type: 'select',
				options: [
					{ value: 'eq', label: '等於' },
					{ value: 'neq', label: '不等於' },
					{ value: 'contains', label: '包含' },
					{ value: 'starts_with', label: '開頭符合' },
					{ value: 'exists', label: '有值' },
					{ value: 'truthy', label: '為真' }
				]
			},
			{ key: 'value', label: '比較值', type: 'text' }
		]
	},
	{
		type: 'user_choice',
		label: '請使用者選擇',
		category: 'control',
		description: '暫停工作流並在聊天或通訊頻道顯示選項，選擇後從原節點繼續。',
		keywords: ['choice', 'pause', 'resume', '選擇', '確認'],
		inputType: 'any',
		outputType: 'data',
		defaultConfig: {
			title: '請選擇正確的客戶',
			message: '找到多筆相似資料，請選擇後繼續。',
			choices_from_path: 'candidates',
			choice_label_path: 'name',
			choice_value_path: 'id',
			choices: []
		},
		configFields: [
			{ key: 'title', label: '標題', type: 'text', required: true },
			{ key: 'message', label: '說明', type: 'textarea' },
			{
				key: 'choices_from_path',
				label: '動態選項陣列路徑',
				type: 'text',
				help: '例如 candidates；留空時使用下方固定選項。'
			},
			{ key: 'choice_label_path', label: '選項標籤欄位', type: 'text', placeholder: 'name' },
			{ key: 'choice_value_path', label: '選項值欄位', type: 'text', placeholder: 'id' },
			{
				key: 'choices',
				label: '固定選項',
				type: 'json',
				help: '格式：[{"label":"名稱","value":"id"}]'
			}
		]
	},
	{
		type: 'email_compose',
		label: '產生郵件草稿',
		category: 'transform',
		description: '以固定範本或指定模型產生主旨與正文；收件人仍由查詢結果決定。',
		keywords: ['email', 'draft', 'subject', '郵件', '草稿'],
		inputType: 'data',
		outputType: 'data',
		recommended: true,
		defaultConfig: {
			use_model: false,
			require_knowledge: false,
			instruction: '依使用者要求撰寫簡潔、專業且不添加未知承諾的商務郵件。',
			subject_template: '關於 {{customer_name}} 的通知',
			text_template: '{{message}}',
			cc_input_key: 'cc',
			default_cc: []
		},
		configFields: [
			{
				key: 'use_model',
				label: '使用模型潤稿',
				type: 'checkbox',
				help: '關閉時完全依範本產生，結果更可預測。'
			},
			{
				key: 'require_knowledge',
				label: '必須有知識庫依據',
				type: 'checkbox',
				help: '開啟後，若上游沒有檢索到授權內容，工作流會停止且不寄送。'
			},
			{ key: 'model_id', label: '潤稿模型', type: 'select' },
			{ key: 'instruction', label: '寫作規則', type: 'textarea' },
			{ key: 'subject_template', label: '主旨範本', type: 'text', required: true },
			{ key: 'text_template', label: '純文字正文範本', type: 'textarea', required: true },
			{ key: 'cc_input_key', label: 'CC 輸入欄位', type: 'text' },
			{ key: 'default_cc', label: '工作流預設 CC', type: 'tags' }
		]
	},
	{
		type: 'approval_gate',
		label: '寄送前核准',
		category: 'control',
		description: '顯示收件人、CC、主旨與正文並暫停；核准雜湊只對本次內容有效。',
		keywords: ['approval', 'confirm', '核准', '預覽'],
		inputType: 'data',
		outputType: 'data',
		recommended: true,
		defaultConfig: {
			title: '確認寄送郵件',
			message: '請檢查收件人、CC、主旨與內容。核准後才會寄送。',
			confirm_label: '確認寄送',
			cancel_label: '取消',
			preview_fields: [
				{ label: '收件人', path: 'to' },
				{ label: 'CC', path: 'cc' },
				{ label: '主旨', path: 'subject' },
				{ label: '內容', path: 'text' }
			]
		},
		configFields: [
			{ key: 'title', label: '核准標題', type: 'text', required: true },
			{ key: 'message', label: '核准說明', type: 'textarea' },
			{ key: 'confirm_label', label: '確認按鈕文字', type: 'text' },
			{ key: 'cancel_label', label: '取消按鈕文字', type: 'text' },
			{ key: 'preview_fields', label: '預覽欄位', type: 'json', required: true }
		]
	},
	{
		type: 'email_send',
		label: '正式寄送郵件',
		category: 'output',
		description: '使用本企業自己的寄信 Connector；必須直接接在寄送前核准之後。',
		keywords: ['resend', 'send', 'email', '寄信'],
		inputType: 'data',
		outputType: 'data',
		recommended: true,
		defaultConfig: { connector_id: '', idempotency_scope: 'send' },
		configFields: [
			{ key: 'connector_id', label: '企業寄信 Connector', type: 'select', required: true },
			{
				key: 'idempotency_scope',
				label: '冪等範圍名稱',
				type: 'text',
				required: true,
				help: '同一次 Run 不會因重試而重複寄送。'
			}
		]
	},
	{
		type: 'email_delivery_status',
		label: '郵件投遞狀態',
		category: 'output',
		description: '保留 provider message ID 與 sent、delivered、bounced 等事件狀態。',
		keywords: ['delivery', 'bounce', 'status', '投遞'],
		inputType: 'data',
		outputType: 'data'
	},
	{
		type: 'email_result',
		label: '回覆寄送結果',
		category: 'output',
		description: '把標準化寄送結果回覆到站內聊天或原通訊頻道。',
		keywords: ['result', 'reply', '寄送結果'],
		inputType: 'data',
		outputType: 'message',
		defaultConfig: { success_text: '郵件已送出。', pending_text: '郵件已交由寄送服務處理。' },
		configFields: [
			{ key: 'success_text', label: '成功訊息', type: 'text' },
			{ key: 'pending_text', label: '處理中訊息', type: 'text' }
		]
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
	},
	{
		id: 'customer-email',
		name: '查客戶並寄信',
		description:
			'擷取客戶、處理唯一／多筆／查無資料結果、產生草稿，人工核准後以企業 Connector 寄送。',
		nodeTypes: [
			'channel_input',
			'structured_extract',
			'customer_contact_lookup',
			'condition',
			'condition',
			'user_choice',
			'email_compose',
			'approval_gate',
			'email_send',
			'email_result',
			'channel_reply',
			'channel_reply'
		]
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

	if (templateId === 'customer-email') {
		const [
			input,
			extract,
			lookup,
			foundCondition,
			ambiguousCondition,
			choice,
			compose,
			approval,
			send,
			result,
			reply,
			notFoundReply
		] = nodes;
		const positions = [
			[80, 260],
			[390, 260],
			[700, 260],
			[1010, 260],
			[1320, 470],
			[1630, 470],
			[1940, 220],
			[2250, 220],
			[2560, 220],
			[2870, 220],
			[3180, 220],
			[1630, 700]
		];
		nodes.forEach((node, index) => {
			node.position = { x: positions[index][0], y: positions[index][1] };
		});
		foundCondition.data.label = '是否唯一命中';
		foundCondition.data.config = { field: 'status', operator: 'eq', value: 'found' };
		ambiguousCondition.data.label = '是否有多筆候選';
		ambiguousCondition.data.config = { field: 'value.status', operator: 'eq', value: 'ambiguous' };
		choice.data.config = {
			...choice.data.config,
			choices_from_path: 'value.value.candidates',
			choice_label_path: 'name',
			choice_value_path: 'id'
		};
		notFoundReply.data.label = '查無可寄信資料';
		notFoundReply.data.config = {
			output_type: 'text',
			text: '找不到可安全寄送的客戶 Email，或該聯絡人已拒收；本次沒有寄信。'
		};
		return {
			nodes,
			edges: [
				{ id: 'email-input-extract', source: input.id, target: extract.id, type: 'smoothstep' },
				{ id: 'email-extract-lookup', source: extract.id, target: lookup.id, type: 'smoothstep' },
				{
					id: 'email-lookup-found',
					source: lookup.id,
					target: foundCondition.id,
					type: 'smoothstep'
				},
				{
					id: 'email-found-compose',
					source: foundCondition.id,
					sourceHandle: 'true',
					target: compose.id,
					type: 'smoothstep'
				},
				{
					id: 'email-found-ambiguous',
					source: foundCondition.id,
					sourceHandle: 'false',
					target: ambiguousCondition.id,
					type: 'smoothstep'
				},
				{
					id: 'email-ambiguous-choice',
					source: ambiguousCondition.id,
					sourceHandle: 'true',
					target: choice.id,
					type: 'smoothstep'
				},
				{
					id: 'email-no-contact',
					source: ambiguousCondition.id,
					sourceHandle: 'false',
					target: notFoundReply.id,
					type: 'smoothstep'
				},
				{ id: 'email-choice-compose', source: choice.id, target: compose.id, type: 'smoothstep' },
				{
					id: 'email-compose-approval',
					source: compose.id,
					target: approval.id,
					type: 'smoothstep'
				},
				{ id: 'email-approval-send', source: approval.id, target: send.id, type: 'smoothstep' },
				{ id: 'email-send-result', source: send.id, target: result.id, type: 'smoothstep' },
				{ id: 'email-result-reply', source: result.id, target: reply.id, type: 'smoothstep' }
			]
		};
	}

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

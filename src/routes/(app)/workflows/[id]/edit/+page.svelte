<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import { beforeNavigate, goto } from '$app/navigation';
	import { get } from 'svelte/store';
	import { writable } from 'svelte/store';
	import { toast } from 'svelte-sonner';
	import { Background, BackgroundVariant, Controls, MiniMap, SvelteFlow } from '@xyflow/svelte';
	import '@xyflow/svelte/dist/style.css';
	import { showSidebar, user } from '$lib/stores';
	import {
		getWorkflowById,
		getWorkflowRuns,
		publishWorkflowById,
		runWorkflowById,
		selectAgentWorkflows,
		updateWorkflowById,
		validateWorkflowById,
		type WorkflowResponse,
		type WorkflowRunResponse,
		type WorkflowSelectorResponse,
		type WorkflowValidateResponse
	} from '$lib/apis/workflows';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	type WorkflowNodeCategory =
		| 'trigger'
		| 'agent'
		| 'model'
		| 'prompt'
		| 'rag'
		| 'tool'
		| 'logic'
		| 'output'
		| 'ops';

	type WorkflowNodeDefinition = {
		type: string;
		label: string;
		category: WorkflowNodeCategory;
		description: string;
	};

	const NODE_GROUPS: { id: WorkflowNodeCategory; label: string }[] = [
		{ id: 'trigger', label: '輸入與觸發' },
		{ id: 'agent', label: '代理' },
		{ id: 'model', label: '模型' },
		{ id: 'prompt', label: '提示與記憶' },
		{ id: 'rag', label: 'RAG 與資料' },
		{ id: 'tool', label: '工具' },
		{ id: 'logic', label: '邏輯' },
		{ id: 'output', label: '輸出' },
		{ id: 'ops', label: '維運' }
	];

	const RUNTIME_SUPPORTED_NODE_TYPES = new Set([
		'input',
		'chat_input',
		'channel_input',
		'webhook_trigger',
		'schedule_trigger',
		'file_upload',
		'form_input',
		'agent',
		'supervisor_agent',
		'worker_agent',
		'planner',
		'evaluator',
		'chat_model',
		'vision_model',
		'output',
		'chat_output',
		'channel_reply',
		'media_output',
		'file_output',
		'handoff',
		'webhook_response',
		'notification',
		'conversation_memory',
		'summary_memory',
		'entity_memory',
		'context_builder',
		'merge',
		'trace',
		'usage_meter',
		'error_handler',
		'fallback',
		'system_prompt',
		'prompt_template',
		'calculator',
		'transform_json',
		'extract_fields',
		'database_query'
	]);

	const NODE_DEFINITIONS: WorkflowNodeDefinition[] = [
		{
			type: 'chat_input',
			label: '聊天輸入',
			category: 'trigger',
			description: '從站內聊天訊息啟動。'
		},
		{
			type: 'channel_input',
			label: '頻道輸入',
			category: 'trigger',
			description: '從 LINE、WeChat、Telegram 或其他已連接頻道啟動。'
		},
		{
			type: 'webhook_trigger',
			label: 'Webhook 觸發',
			category: 'trigger',
			description: '從 HTTP Webhook 事件啟動。'
		},
		{
			type: 'schedule_trigger',
			label: '排程觸發',
			category: 'trigger',
			description: '依週期或一次性排程執行。'
		},
		{
			type: 'file_upload',
			label: '檔案上傳',
			category: 'trigger',
			description: '從上傳的文件、圖片、音訊或壓縮檔啟動。'
		},
		{
			type: 'form_input',
			label: '表單輸入',
			category: 'trigger',
			description: '在工作流開始前收集結構化欄位。'
		},
		{
			type: 'agent',
			label: '代理',
			category: 'agent',
			description: '可推理、呼叫工具並產生最終回覆的主要代理。'
		},
		{
			type: 'supervisor_agent',
			label: '主管代理',
			category: 'agent',
			description: '協調多個工作代理並決定下一步。'
		},
		{
			type: 'worker_agent',
			label: '工作代理',
			category: 'agent',
			description: '負責研究、客服、程式等子任務的專用代理。'
		},
		{
			type: 'planner',
			label: '規劃器',
			category: 'agent',
			description: '執行前將使用者需求拆成有順序的步驟。'
		},
		{
			type: 'agent_router',
			label: '代理路由器',
			category: 'agent',
			description: '依意圖、頻道或上下文將任務分派給最適合的代理。'
		},
		{
			type: 'evaluator',
			label: '評估器',
			category: 'agent',
			description: '評分回覆、檢查政策，或決定是否重試。'
		},
		{
			type: 'human_approval',
			label: '人工核准',
			category: 'agent',
			description: '在敏感操作前暫停，等待人工核准。'
		},
		{
			type: 'chat_model',
			label: '聊天模型',
			category: 'model',
			description: '代理、提示或直接回覆步驟使用的 LLM。'
		},
		{
			type: 'embedding_model',
			label: '嵌入模型',
			category: 'model',
			description: '產生用於檢索與語意搜尋的 embeddings。'
		},
		{
			type: 'vision_model',
			label: '視覺模型',
			category: 'model',
			description: '讀取圖片、截圖、PDF 或多模態輸入。'
		},
		{
			type: 'image_model',
			label: '圖片模型',
			category: 'model',
			description: '在工作流中生成或編輯圖片。'
		},
		{
			type: 'speech_to_text',
			label: '語音轉文字',
			category: 'model',
			description: '轉錄語音訊息或音訊檔。'
		},
		{
			type: 'text_to_speech',
			label: '文字轉語音',
			category: 'model',
			description: '為支援的頻道產生語音輸出。'
		},
		{
			type: 'system_prompt',
			label: '系統提示',
			category: 'prompt',
			description: '定義代理角色、限制與回覆風格。'
		},
		{
			type: 'prompt_template',
			label: '提示範本',
			category: 'prompt',
			description: '用變數與上下文組合可重用提示。'
		},
		{
			type: 'conversation_memory',
			label: '對話記憶',
			category: 'prompt',
			description: '讀取近期聊天紀錄，維持對話連續性。'
		},
		{
			type: 'summary_memory',
			label: '摘要記憶',
			category: 'prompt',
			description: '維護長對話的壓縮摘要。'
		},
		{
			type: 'entity_memory',
			label: '實體記憶',
			category: 'prompt',
			description: '追蹤人物、產品、訂單等命名實體。'
		},
		{
			type: 'document_loader',
			label: '文件載入器',
			category: 'rag',
			description: '載入檔案、URL 或知識庫文件。'
		},
		{
			type: 'web_loader',
			label: '網頁載入器',
			category: 'rag',
			description: '在檢索或摘要前擷取並清理網頁內容。'
		},
		{
			type: 'database_query',
			label: '資料庫查詢',
			category: 'rag',
			description: '查詢 SQL 或商務資料連接器。'
		},
		{
			type: 'split_text',
			label: '文字切分',
			category: 'rag',
			description: '將長文字切塊，用於 embeddings、檢索或 map-reduce。'
		},
		{
			type: 'vector_store',
			label: '向量資料庫',
			category: 'rag',
			description: '將 embeddings 寫入 collection 或索引。'
		},
		{
			type: 'retriever',
			label: '檢索器',
			category: 'rag',
			description: '從向量資料庫或知識庫搜尋相關上下文。'
		},
		{
			type: 'reranker',
			label: '重排序器',
			category: 'rag',
			description: '送入模型前重新排序檢索到的文件。'
		},
		{
			type: 'context_builder',
			label: '上下文組裝器',
			category: 'rag',
			description: '將檢索資料、記憶與變數組裝成上下文。'
		},
		{
			type: 'tool_call',
			label: '工具呼叫',
			category: 'tool',
			description: '呼叫已設定的 Open WebUI 工具或函式。'
		},
		{
			type: 'mcp_tools',
			label: 'MCP 工具',
			category: 'tool',
			description: '將 MCP server 工具提供給代理使用。'
		},
		{
			type: 'http_request',
			label: 'HTTP 請求',
			category: 'tool',
			description: '帶 headers 與 payload 呼叫外部 REST API。'
		},
		{
			type: 'code_interpreter',
			label: '程式執行器',
			category: 'tool',
			description: '執行程式，用於計算、解析、圖表或檔案處理。'
		},
		{
			type: 'calculator',
			label: '計算器',
			category: 'tool',
			description: '進行價格、日期與總額等確定性計算。'
		},
		{
			type: 'search_tool',
			label: '搜尋工具',
			category: 'tool',
			description: '搜尋網路或內部索引。'
		},
		{
			type: 'crm_tool',
			label: 'CRM 工具',
			category: 'tool',
			description: '讀取或更新客戶紀錄。'
		},
		{
			type: 'ticket_tool',
			label: '工單工具',
			category: 'tool',
			description: '建立、更新或分派客服工單。'
		},
		{
			type: 'condition',
			label: '條件判斷',
			category: 'logic',
			description: '依表達式、狀態、角色或頻道分支。'
		},
		{
			type: 'switch_router',
			label: '多路路由',
			category: 'logic',
			description: '依意圖或欄位值路由到多個分支之一。'
		},
		{
			type: 'loop',
			label: '迴圈',
			category: 'logic',
			description: '重複子流程直到成功、逾時或達到最大嘗試次數。'
		},
		{
			type: 'merge',
			label: '合併',
			category: 'logic',
			description: '將多個分支重新合併成一條路徑。'
		},
		{
			type: 'transform_json',
			label: '轉換 JSON',
			category: 'logic',
			description: '對結構化資料進行映射、重新命名、篩選或重塑。'
		},
		{
			type: 'extract_fields',
			label: '欄位擷取',
			category: 'logic',
			description: '從文字或模型輸出中擷取具型別的欄位。'
		},
		{
			type: 'rate_limit',
			label: '速率限制',
			category: 'logic',
			description: '限制高成本模型或工具呼叫頻率。'
		},
		{
			type: 'chat_output',
			label: '聊天輸出',
			category: 'output',
			description: '回覆到站內聊天。'
		},
		{
			type: 'channel_reply',
			label: '頻道回覆',
			category: 'output',
			description: '回覆到 LINE、WeChat、Telegram 或其他頻道。'
		},
		{
			type: 'media_output',
			label: '多媒體輸出',
			category: 'output',
			description: '傳送圖片、音訊、文件或生成式多媒體。'
		},
		{
			type: 'file_output',
			label: '檔案輸出',
			category: 'output',
			description: '回傳生成檔案，供下載或頻道傳送。'
		},
		{
			type: 'handoff',
			label: '轉人工',
			category: 'output',
			description: '將對話轉交給人工客服或操作員。'
		},
		{
			type: 'webhook_response',
			label: 'Webhook 回應',
			category: 'output',
			description: '向呼叫端回傳結構化 HTTP 回應。'
		},
		{
			type: 'notification',
			label: '通知',
			category: 'output',
			description: '透過 email、聊天或 webhook 通知人員或系統。'
		},
		{
			type: 'trace',
			label: '追蹤紀錄',
			category: 'ops',
			description: '記錄重要步驟的輸入、輸出與耗時。'
		},
		{
			type: 'usage_meter',
			label: '用量計量',
			category: 'ops',
			description: '追蹤 token、模型與工具用量，用於計費或限制。'
		},
		{
			type: 'error_handler',
			label: '錯誤處理',
			category: 'ops',
			description: '捕捉錯誤並轉成使用者可理解的安全回覆。'
		},
		{
			type: 'fallback',
			label: '備援',
			category: 'ops',
			description: '當模型、API 或工具失敗時執行替代路徑。'
		}
	];

	const VISIBILITY_OPTIONS = [
		{
			value: 'private',
			label: '私人',
			description: '只有你和管理員可以查看、執行、編輯或發布這個工作流。'
		},
		{
			value: 'shared',
			label: '指定範圍共享',
			description: '依下方存取政策設定，開放給公司、成員、群組、頻道或模型範圍使用。'
		},
		{
			value: 'public_template',
			label: '公開範本',
			description: '登入工作區的使用者可以探索、查看並執行這個可重用範本。不要用於公司內部自動化。'
		}
	];

	const ACL_SCOPE_OPTIONS = [
		{
			value: 'private',
			label: '僅擁有者',
			description: '只有擁有者與管理員可以使用這個工作流。'
		},
		{
			value: 'company',
			label: '同公司',
			description: '解析到同一個公司帳號的成員可以使用這個工作流。'
		},
		{
			value: 'selected_members',
			label: '指定成員',
			description: '只有列出的公司成員 ID 可以使用這個工作流。'
		},
		{
			value: 'selected_groups',
			label: '指定群組',
			description: '只有列出的 WebUI 群組 ID 可以使用這個工作流。'
		}
	];

	let loaded = false;
	let saving = false;
	let validating = false;
	let publishing = false;
	let running = false;
	let workflow: WorkflowResponse | null = null;
	let name = '';
	let description = '';
	let visibility = 'private';
	let meta: Record<string, any> = {};
	let aclScope = 'private';
	let allowAgentSelection = false;
	let intentKeywords = '';
	let intentExamples = '';
	let requiredKeywords = '';
	let negativeKeywords = '';
	let agentSelectionThreshold = 0.64;
	let agentSelectionPriority = 0;
	let agentAmbiguityMargin = 0.12;
	let selectorTestMessage = '';
	let selectorTesting = false;
	let selectorResult: WorkflowSelectorResponse | null = null;
	let allowedCompanyUserIds = '';
	let allowedMemberIds = '';
	let allowedGroupIds = '';
	let allowedChannelIds = '';
	let allowedModelIds = '';
	let testInput = '{\n  "message": "來自聊天的測試訊息"\n}';
	let testModelId = '';
	let validation: WorkflowValidateResponse | null = null;
	let runs: WorkflowRunResponse[] = [];
	let jsonMode = false;
	let graphJson = '';
	let lastSavedSignature = '';
	let isDirty = false;
	let showUnsavedConfirm = false;
	let pendingNavigation = '/workflows';
	let selectedNodeGroup: WorkflowNodeCategory = 'agent';
	let nodeSearch = '';
	let selectedNodeConfigId = '';
	let nodeConfigJson = '{}';

	const nodes = writable<any[]>([]);
	const edges = writable<any[]>([]);

	const nodeStyle = {
		minWidth: '260px',
		minHeight: '64px',
		padding: '0 20px',
		fontSize: '15px',
		fontWeight: 700
	};

	$: workflowId = $page.params.id;
	$: canEdit = Boolean(workflow && ($user?.role === 'admin' || workflow.user_id === $user?.id));
	$: filteredNodeDefinitions = NODE_DEFINITIONS.filter((node) => {
		const term = nodeSearch.trim().toLowerCase();
		if (!term && node.category !== selectedNodeGroup) return false;
		if (!term) return true;

		return [node.label, node.type, node.description].join(' ').toLowerCase().includes(term);
	});
	$: selectedGroupCount = NODE_DEFINITIONS.filter(
		(node) => node.category === selectedNodeGroup
	).length;
	$: selectedNode = $nodes.find((node) => node.selected) ?? null;
	$: if (selectedNode && selectedNode.id !== selectedNodeConfigId) {
		selectedNodeConfigId = selectedNode.id;
		nodeConfigJson = JSON.stringify(selectedNode.data?.config ?? {}, null, 2);
	}
	$: visibilityDescription =
		VISIBILITY_OPTIONS.find((option) => option.value === visibility)?.description ?? '';
	$: aclScopeDescription =
		ACL_SCOPE_OPTIONS.find((option) => option.value === aclScope)?.description ?? '';

	const getNodeDefinition = (type: string) =>
		NODE_DEFINITIONS.find((definition) => definition.type === type);

	const nodeClassForKind = (kind: string) => {
		const definition = getNodeDefinition(kind);
		return definition ? `workflow-node workflow-node-${definition.category}` : 'workflow-node';
	};

	const rendererTypeForDefinition = (definition: WorkflowNodeDefinition) => {
		if (definition.category === 'trigger') return 'input';
		if (definition.category === 'output') return 'output';
		return 'default';
	};

	const graph = () => ({
		nodes: get(nodes),
		edges: get(edges)
	});

	const listToText = (value: unknown) => {
		if (Array.isArray(value)) return value.filter(Boolean).join(', ');
		if (typeof value === 'string') return value;
		return '';
	};

	const textToList = (value: string) =>
		value
			.split(',')
			.map((item) => item.trim())
			.filter(Boolean);
	const listToLines = (value: unknown) =>
		Array.isArray(value)
			? value.filter(Boolean).join('\n')
			: typeof value === 'string'
				? value
				: '';
	const textToLines = (value: string) =>
		value
			.split(/\r?\n/)
			.map((item) => item.trim())
			.filter(Boolean);

	const syncAclState = (workflowMeta: Record<string, any> | null | undefined) => {
		const acl = workflowMeta?.acl ?? {};
		aclScope = acl.scope ?? (visibility === 'shared' ? 'company' : 'private');
		allowAgentSelection = Boolean(acl.allow_agent_selection);
		intentKeywords = listToText(acl.intent_keywords);
		intentExamples = listToLines(acl.intent_examples);
		requiredKeywords = listToText(acl.required_keywords);
		negativeKeywords = listToText(acl.negative_keywords);
		agentSelectionThreshold = Number(acl.agent_selection_threshold ?? 0.64);
		agentSelectionPriority = Number(acl.agent_selection_priority ?? 0);
		agentAmbiguityMargin = Number(acl.agent_ambiguity_margin ?? 0.12);
		allowedCompanyUserIds = listToText(acl.allowed_company_user_ids);
		allowedMemberIds = listToText(acl.allowed_member_ids);
		allowedGroupIds = listToText(acl.allowed_group_ids);
		allowedChannelIds = listToText(acl.allowed_channel_ids);
		allowedModelIds = listToText(acl.allowed_model_ids);
	};

	const buildWorkflowMeta = () => {
		const acl = {
			...(meta?.acl ?? {}),
			scope: aclScope,
			allow_agent_selection: allowAgentSelection,
			intent_keywords: textToList(intentKeywords),
			intent_examples: textToLines(intentExamples),
			required_keywords: textToList(requiredKeywords),
			negative_keywords: textToList(negativeKeywords),
			agent_selection_threshold: agentSelectionThreshold,
			agent_selection_priority: agentSelectionPriority,
			agent_ambiguity_margin: agentAmbiguityMargin,
			allowed_company_user_ids: textToList(allowedCompanyUserIds),
			allowed_member_ids: textToList(allowedMemberIds),
			allowed_group_ids: textToList(allowedGroupIds),
			allowed_channel_ids: textToList(allowedChannelIds),
			allowed_model_ids: textToList(allowedModelIds)
		};

		return {
			...(meta ?? {}),
			acl
		};
	};

	const graphSignature = () =>
		JSON.stringify({
			name,
			description,
			visibility,
			meta: buildWorkflowMeta(),
			graph: graph()
		});

	const markDirty = () => {
		if (loaded) {
			isDirty = graphSignature() !== lastSavedSignature;
		}
	};

	const handleVisibilityChange = () => {
		if (visibility === 'shared' && ['private', 'owner'].includes(aclScope)) {
			aclScope = 'company';
		}
		if (visibility !== 'shared') {
			aclScope = 'private';
		}
		if (visibility === 'public_template') {
			allowAgentSelection = false;
		}
		queueMicrotask(markDirty);
	};

	const syncJson = () => {
		graphJson = JSON.stringify(graph(), null, 2);
	};

	const parseGraphJson = () => {
		const parsed = JSON.parse(graphJson);
		if (
			!parsed ||
			typeof parsed !== 'object' ||
			!Array.isArray(parsed.nodes) ||
			!Array.isArray(parsed.edges)
		) {
			throw new Error('Graph JSON 必須包含 nodes 與 edges 陣列。');
		}
		return parsed;
	};

	const normalizeGraph = () => ({
		nodes: get(nodes).map((node) => ({
			...node,
			type: node.type === 'input' || node.type === 'output' ? node.type : 'default',
			class: node.class ?? nodeClassForKind(node.data?.type ?? node.type),
			className: node.className ?? node.class ?? nodeClassForKind(node.data?.type ?? node.type),
			style: {
				...nodeStyle,
				...(node.style ?? {})
			},
			data: {
				...(node.data ?? {}),
				type: node.data?.type ?? node.type,
				category:
					node.data?.category ??
					getNodeDefinition(node.data?.type ?? node.type)?.category ??
					'logic',
				description:
					node.data?.description ??
					getNodeDefinition(node.data?.type ?? node.type)?.description ??
					''
			}
		})),
		edges: get(edges)
	});

	nodes.subscribe(() => {
		if (loaded && !jsonMode) {
			syncJson();
			markDirty();
		}
	});

	edges.subscribe(() => {
		if (loaded && !jsonMode) {
			syncJson();
			markDirty();
		}
	});

	const loadWorkflow = async () => {
		try {
			const res = await getWorkflowById(localStorage.token, workflowId);
			workflow = res;
			name = res.name;
			description = res.description ?? '';
			visibility = res.visibility;
			meta = res.meta ?? {};
			syncAclState(meta);
			nodes.set(
				(res.graph?.nodes ?? []).map((node) => ({
					...node,
					type: node.type === 'input' || node.type === 'output' ? node.type : 'default',
					class: node.class ?? nodeClassForKind(node.data?.type ?? node.type),
					className: node.className ?? node.class ?? nodeClassForKind(node.data?.type ?? node.type),
					style: {
						...nodeStyle,
						...(node.style ?? {})
					}
				}))
			);
			edges.set(res.graph?.edges ?? []);
			syncJson();
			runs = await getWorkflowRuns(localStorage.token, workflowId, 10).catch(() => []);
			lastSavedSignature = graphSignature();
			isDirty = false;
			loaded = true;
		} catch (err) {
			toast.error(`${err}`);
			goto('/workflows');
		}
	};

	const saveWorkflow = async () => {
		saving = true;
		try {
			if (jsonMode) {
				const parsed = parseGraphJson();
				nodes.set(parsed.nodes);
				edges.set(parsed.edges);
			}

			const res = await updateWorkflowById(localStorage.token, workflowId, {
				name,
				description,
				visibility,
				meta: buildWorkflowMeta(),
				graph: normalizeGraph()
			});
			workflow = res;
			meta = res.meta ?? {};
			syncAclState(meta);
			nodes.set(
				(res.graph?.nodes ?? []).map((node) => ({
					...node,
					class: node.class ?? nodeClassForKind(node.data?.type ?? node.type),
					className: node.className ?? node.class ?? nodeClassForKind(node.data?.type ?? node.type),
					style: {
						...nodeStyle,
						...(node.style ?? {})
					}
				}))
			);
			edges.set(res.graph?.edges ?? []);
			syncJson();
			lastSavedSignature = graphSignature();
			isDirty = false;
			toast.success('工作流已儲存');
			return true;
		} catch (err) {
			toast.error(`${err}`);
			return false;
		} finally {
			saving = false;
		}
	};

	const validateWorkflow = async () => {
		validating = true;
		try {
			const result = await validateWorkflowById(localStorage.token, workflowId, {
				graph: graph(),
				meta: buildWorkflowMeta(),
				visibility
			});
			validation = result;
			if (result.ok) {
				toast.success(result.warnings.length ? '驗證通過，但有警告' : '工作流驗證通過');
			} else {
				toast.error('工作流有阻斷問題');
			}
		} catch (err) {
			toast.error(`${err}`);
		} finally {
			validating = false;
		}
	};

	const testAgentSelection = async () => {
		if (!selectorTestMessage.trim()) {
			toast.error('請先輸入一段使用者訊息。');
			return;
		}
		if (isDirty || workflow?.status !== 'published') {
			toast.error('請先發布目前版本，再測試代理選型。');
			return;
		}
		selectorTesting = true;
		try {
			selectorResult = await selectAgentWorkflows(localStorage.token, selectorTestMessage.trim());
		} catch (err) {
			toast.error(`${err}`);
		} finally {
			selectorTesting = false;
		}
	};

	const publishWorkflow = async () => {
		publishing = true;
		try {
			const saved = await saveWorkflow();
			if (!saved) return;
			const version = await publishWorkflowById(localStorage.token, workflowId);
			toast.success(`已發布版本 ${version.version}`);
			await loadWorkflow();
		} catch (err) {
			toast.error(`${err}`);
		} finally {
			publishing = false;
		}
	};

	const runWorkflow = async () => {
		running = true;
		try {
			if (isDirty) {
				const saved = await saveWorkflow();
				if (!saved) return;
			}
			const input = JSON.parse(testInput);
			const run = await runWorkflowById(
				localStorage.token,
				workflowId,
				input,
				'manual_test',
				testModelId.trim() || undefined
			);
			runs = [run, ...runs].slice(0, 10);
			toast.success(run.status === 'success' ? '工作流測試執行完成' : '工作流測試已記錄');
		} catch (err) {
			toast.error(`${err}`);
		} finally {
			running = false;
		}
	};

	const addNode = (kind: string) => {
		const definition =
			getNodeDefinition(kind) ??
			({
				type: kind,
				label: kind.replaceAll('_', ' '),
				category: 'logic',
				description: ''
			} satisfies WorkflowNodeDefinition);
		const next = get(nodes).length + 1;
		const id = `${kind}-${Date.now()}`;
		nodes.update((items) => [
			...items,
			{
				id,
				type: rendererTypeForDefinition(definition),
				class: nodeClassForKind(kind),
				className: nodeClassForKind(kind),
				position: { x: 120 + next * 40, y: 100 + next * 30 },
				style: nodeStyle,
				data: {
					label: definition.label,
					type: definition.type,
					category: definition.category,
					description: definition.description
				}
			}
		]);
	};

	const edgeId = (source: string, target: string) => `${source}-${target}-${Date.now()}`;

	const createEdge = (connection: any) => {
		if (!connection.source || !connection.target) return false;
		return {
			...connection,
			id: edgeId(connection.source, connection.target),
			type: 'default',
			deletable: true
		};
	};

	const isValidConnection = (connection: any) => {
		if (!connection.source || !connection.target || connection.source === connection.target)
			return false;
		return !get(edges).some(
			(edge) => edge.source === connection.source && edge.target === connection.target
		);
	};

	const connectLastTwo = () => {
		const currentNodes = get(nodes);
		if (currentNodes.length < 2) {
			toast.error('請先新增至少兩個節點');
			return;
		}

		const source = currentNodes[currentNodes.length - 2].id;
		const target = currentNodes[currentNodes.length - 1].id;
		if (!isValidConnection({ source, target })) {
			toast.error('這些節點已連接，或無法建立連線。');
			return;
		}
		edges.update((items) => [
			...items,
			{ id: edgeId(source, target), source, target, deletable: true }
		]);
	};

	const deleteSelected = () => {
		const selectedNodeIds = new Set(
			get(nodes)
				.filter((node) => node.selected)
				.map((node) => node.id)
		);
		const selectedEdges = new Set(
			get(edges)
				.filter((edge) => edge.selected)
				.map((edge) => edge.id)
		);
		if (!selectedNodeIds.size && !selectedEdges.size) {
			toast.error('請先選取節點或連線');
			return;
		}
		nodes.update((items) => items.filter((node) => !selectedNodeIds.has(node.id)));
		edges.update((items) =>
			items.filter(
				(edge) =>
					!selectedEdges.has(edge.id) &&
					!selectedNodeIds.has(edge.source) &&
					!selectedNodeIds.has(edge.target)
			)
		);
	};

	const applyJson = () => {
		try {
			const parsed = parseGraphJson();
			nodes.set(parsed.nodes);
			edges.set(parsed.edges);
			markDirty();
			toast.success('Graph JSON 已套用');
		} catch (err) {
			toast.error(`${err}`);
		}
	};

	const applyNodeConfig = () => {
		if (!selectedNode) return;
		try {
			const config = JSON.parse(nodeConfigJson);
			if (!config || typeof config !== 'object' || Array.isArray(config)) {
				throw new Error('節點設定必須是 JSON 物件。');
			}
			nodes.update((items) =>
				items.map((node) =>
					node.id === selectedNode.id ? { ...node, data: { ...(node.data ?? {}), config } } : node
				)
			);
			markDirty();
			toast.success('節點設定已套用');
		} catch (error) {
			toast.error(`${error}`);
		}
	};

	const formatDate = (value: number | null) =>
		value ? new Date(Math.floor(value / 1000000)).toLocaleString() : '-';

	const leaveEditor = () => {
		if (isDirty) {
			pendingNavigation = '/workflows';
			showUnsavedConfirm = true;
			return;
		}
		goto('/workflows');
	};

	beforeNavigate(({ cancel, to }) => {
		if (!loaded || !isDirty || !to?.url) return;
		if (to.url.pathname === $page.url.pathname) return;

		cancel();
		pendingNavigation = `${to.url.pathname}${to.url.search}${to.url.hash}`;
		showUnsavedConfirm = true;
	});

	onMount(loadWorkflow);
</script>

<ConfirmDialog
	bind:show={showUnsavedConfirm}
	title="捨棄未儲存的變更？"
	confirmLabel="捨棄"
	on:confirm={() => {
		isDirty = false;
		goto(pendingNavigation);
	}}
>
	<div class="text-sm text-gray-500">你有尚未儲存的工作流變更。現在離開會捨棄這些變更。</div>
</ConfirmDialog>

{#if !loaded || !workflow}
	<div
		class="flex h-screen max-h-[100dvh] w-full items-center justify-center transition-width duration-200 ease-in-out {$showSidebar
			? 'md:max-w-[calc(100%-var(--sidebar-width))]'
			: ''}"
	>
		<Spinner className="size-5" />
	</div>
{:else}
	<div
		class="flex h-screen max-h-[100dvh] w-full flex-col transition-width duration-200 ease-in-out {$showSidebar
			? 'md:max-w-[calc(100%-var(--sidebar-width))]'
			: ''} max-w-full"
	>
		<div
			class="flex flex-wrap items-center justify-between gap-3 border-b border-gray-200 px-4 py-3 dark:border-gray-800"
		>
			<div class="flex min-w-0 items-center gap-3">
				<button
					class="rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-800"
					on:click={leaveEditor}
				>
					返回
				</button>
				<div class="min-w-0">
					<input
						class="w-full min-w-[16rem] bg-transparent text-lg font-semibold text-gray-900 outline-none dark:text-gray-100"
						bind:value={name}
						disabled={!canEdit}
						on:input={() => queueMicrotask(markDirty)}
						aria-label="工作流名稱"
					/>
					<div class="text-xs text-gray-500">
						{workflow.status === 'published'
							? '已發布'
							: workflow.status === 'archived'
								? '已封存'
								: '草稿'} · {workflow.default_version_id ? '已有發布版本' : '僅草稿'}
						{#if isDirty}
							<span class="ml-2 text-amber-600">尚未儲存</span>
						{/if}
					</div>
				</div>
			</div>

			{#if canEdit}
				<div class="flex flex-wrap items-center gap-2">
					<button
						class="rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-800"
						on:click={validateWorkflow}
						disabled={validating}
					>
						{validating ? '檢查中...' : '驗證'}
					</button>
					<button
						class="rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-800"
						on:click={saveWorkflow}
						disabled={saving}
					>
						{saving ? '儲存中...' : '儲存'}
					</button>
					<button
						class="rounded-lg bg-gray-900 px-3 py-2 text-sm font-medium text-white dark:bg-white dark:text-gray-900"
						on:click={publishWorkflow}
						disabled={publishing}
					>
						{publishing ? '發布中...' : '發布'}
					</button>
				</div>
			{:else}
				<div
					class="rounded-lg bg-gray-100 px-3 py-2 text-sm text-gray-600 dark:bg-gray-900 dark:text-gray-300"
				>
					唯讀檢視
				</div>
			{/if}
		</div>

		<div class="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[minmax(0,1fr)_400px]">
			<div class="workflow-canvas relative min-h-[520px] bg-gray-50 dark:bg-[#070b12]">
				{#if jsonMode}
					<div class="flex h-full flex-col gap-3 p-4">
						<textarea
							class="min-h-0 flex-1 resize-none rounded-xl border border-gray-200 bg-white p-4 font-mono text-xs outline-none dark:border-gray-800 dark:bg-gray-900"
							bind:value={graphJson}
							on:input={() => {
								if (loaded) isDirty = true;
							}}
							aria-label="工作流 Graph JSON"
						></textarea>
						<div class="flex justify-end">
							<button
								class="rounded-lg bg-gray-900 px-3 py-2 text-sm font-medium text-white dark:bg-white dark:text-gray-900"
								on:click={applyJson}
							>
								套用 JSON
							</button>
						</div>
					</div>
				{:else}
					<SvelteFlow
						{nodes}
						{edges}
						fitView
						onedgecreate={createEdge}
						{isValidConnection}
						nodesDraggable={canEdit}
						nodesConnectable={canEdit}
						elementsSelectable={canEdit}
					>
						<Controls />
						<MiniMap pannable zoomable />
						<Background variant={BackgroundVariant.Dots} gap={40} size={1.4} />
					</SvelteFlow>
				{/if}
			</div>

			<aside
				class="flex min-h-0 flex-col gap-4 overflow-y-auto border-l border-gray-200 bg-white p-4 dark:border-gray-800 dark:bg-gray-950"
			>
				<fieldset class="contents" disabled={!canEdit}>
					<section class="space-y-3">
						<div class="text-sm font-semibold text-gray-900 dark:text-gray-100">工作流設定</div>
						<textarea
							class="h-20 w-full resize-none rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm outline-none dark:border-gray-800"
							bind:value={description}
							on:input={() => queueMicrotask(markDirty)}
							placeholder="描述"
						></textarea>
						<select
							class="w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm outline-none dark:border-gray-800"
							bind:value={visibility}
							on:change={handleVisibilityChange}
						>
							{#each VISIBILITY_OPTIONS as option}
								<option value={option.value}>{option.label}</option>
							{/each}
						</select>
						<div
							class="rounded-lg bg-gray-50 px-3 py-2 text-xs leading-5 text-gray-600 dark:bg-gray-900 dark:text-gray-300"
						>
							{visibilityDescription}
						</div>
						<div class="space-y-2 rounded-lg border border-gray-200 p-3 dark:border-gray-800">
							<div class="text-xs font-semibold text-gray-500">存取政策</div>
							{#if visibility === 'shared'}
								<label class="space-y-1 text-xs font-medium text-gray-600 dark:text-gray-300">
									<span>共享範圍</span>
									<select
										class="w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm font-normal outline-none dark:border-gray-800"
										bind:value={aclScope}
										on:change={() => queueMicrotask(markDirty)}
									>
										{#each ACL_SCOPE_OPTIONS.filter((option) => option.value !== 'private') as option}
											<option value={option.value}>{option.label}</option>
										{/each}
									</select>
								</label>
								<div class="text-xs leading-5 text-gray-500 dark:text-gray-400">
									{aclScopeDescription}
								</div>
							{/if}
							<label class="flex items-start gap-2 text-sm text-gray-700 dark:text-gray-200">
								<input
									class="mt-1"
									type="checkbox"
									bind:checked={allowAgentSelection}
									on:change={() => queueMicrotask(markDirty)}
								/>
								<span>
									<span class="font-medium">允許代理自動選擇</span>
									<span class="block text-xs leading-5 text-gray-500 dark:text-gray-400">
										只有已發布、具備存取權且通過信心與歧義檢查的工作流才會執行。
									</span>
								</span>
							</label>
							{#if allowAgentSelection}
								<label class="block space-y-1 text-xs font-medium text-gray-600 dark:text-gray-300">
									<span>意圖片語</span>
									<input
										class="w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm font-normal outline-none dark:border-gray-800"
										bind:value={intentKeywords}
										on:input={() => queueMicrotask(markDirty)}
										placeholder="例如：查詢發票, 發票付款狀態"
									/>
								</label>
								<label class="block space-y-1 text-xs font-medium text-gray-600 dark:text-gray-300">
									<span>使用者說法範例（每行一則）</span>
									<textarea
										class="h-24 w-full resize-y rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm font-normal outline-none dark:border-gray-800"
										bind:value={intentExamples}
										on:input={() => queueMicrotask(markDirty)}
										placeholder="幫我查上個月的發票付款狀態"
									></textarea>
								</label>
								<label class="block space-y-1 text-xs font-medium text-gray-600 dark:text-gray-300">
									<span>必要詞（必須全部出現）</span>
									<input
										class="w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm font-normal outline-none dark:border-gray-800"
										bind:value={requiredKeywords}
										on:input={() => queueMicrotask(markDirty)}
										placeholder="例如：A 公司"
									/>
								</label>
								<label class="block space-y-1 text-xs font-medium text-gray-600 dark:text-gray-300">
									<span>排除詞（命中即不選）</span>
									<input
										class="w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm font-normal outline-none dark:border-gray-800"
										bind:value={negativeKeywords}
										on:input={() => queueMicrotask(markDirty)}
										placeholder="例如：作廢, 退款"
									/>
								</label>
								<div class="grid grid-cols-3 gap-2">
									<label class="space-y-1 text-xs text-gray-600 dark:text-gray-300"
										><span>信心門檻</span><input
											class="w-full rounded-lg border border-gray-200 bg-transparent px-2 py-2 text-sm outline-none dark:border-gray-800"
											type="number"
											min="0.5"
											max="0.95"
											step="0.01"
											bind:value={agentSelectionThreshold}
											on:input={() => queueMicrotask(markDirty)}
										/></label
									>
									<label class="space-y-1 text-xs text-gray-600 dark:text-gray-300"
										><span>優先序</span><input
											class="w-full rounded-lg border border-gray-200 bg-transparent px-2 py-2 text-sm outline-none dark:border-gray-800"
											type="number"
											min="-100"
											max="100"
											step="1"
											bind:value={agentSelectionPriority}
											on:input={() => queueMicrotask(markDirty)}
										/></label
									>
									<label class="space-y-1 text-xs text-gray-600 dark:text-gray-300"
										><span>歧義差距</span><input
											class="w-full rounded-lg border border-gray-200 bg-transparent px-2 py-2 text-sm outline-none dark:border-gray-800"
											type="number"
											min="0.05"
											max="0.3"
											step="0.01"
											bind:value={agentAmbiguityMargin}
											on:input={() => queueMicrotask(markDirty)}
										/></label
									>
								</div>
							{/if}

							{#if visibility === 'shared'}
								{#if aclScope === 'company'}
									<input
										class="w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm outline-none dark:border-gray-800"
										bind:value={allowedCompanyUserIds}
										on:input={() => queueMicrotask(markDirty)}
										placeholder="額外允許的公司 ID（選填）"
									/>
								{:else if aclScope === 'selected_members'}
									<input
										class="w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm outline-none dark:border-gray-800"
										bind:value={allowedMemberIds}
										on:input={() => queueMicrotask(markDirty)}
										placeholder="允許的公司成員 ID"
									/>
								{:else if aclScope === 'selected_groups'}
									<input
										class="w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm outline-none dark:border-gray-800"
										bind:value={allowedGroupIds}
										on:input={() => queueMicrotask(markDirty)}
										placeholder="允許的 WebUI 群組 ID"
									/>
								{/if}
							{/if}
							<input
								class="w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm outline-none dark:border-gray-800"
								bind:value={allowedChannelIds}
								on:input={() => queueMicrotask(markDirty)}
								placeholder="限制頻道 ID（留空代表不限）"
							/>
							<input
								class="w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm outline-none dark:border-gray-800"
								bind:value={allowedModelIds}
								on:input={() => queueMicrotask(markDirty)}
								placeholder="限制模型 ID（留空代表不限）"
							/>
						</div>

						{#if allowAgentSelection}
							<div class="space-y-2 rounded-lg border border-gray-200 p-3 dark:border-gray-800">
								<div class="text-xs font-semibold text-gray-600 dark:text-gray-300">
									代理選型測試
								</div>
								<textarea
									class="h-20 w-full resize-y rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm outline-none dark:border-gray-800"
									bind:value={selectorTestMessage}
									placeholder="輸入一段真實使用者訊息"
								></textarea>
								<button
									class="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm disabled:opacity-50 dark:border-gray-800"
									on:click={testAgentSelection}
									disabled={selectorTesting || isDirty || workflow.status !== 'published'}
								>
									{selectorTesting ? '判斷中...' : '測試已發布工作流選型'}
								</button>
								{#if isDirty || workflow.status !== 'published'}
									<div class="text-xs leading-5 text-amber-700 dark:text-amber-300">
										請先發布目前版本，才能得到與實際 Agent 相同的結果。
									</div>
								{/if}
								{#if selectorResult}
									<div class="rounded-lg bg-gray-50 p-3 text-xs dark:bg-gray-900">
										<div
											class="font-semibold {selectorResult.decision === 'selected'
												? 'text-green-700 dark:text-green-300'
												: selectorResult.decision === 'ambiguous'
													? 'text-amber-700 dark:text-amber-300'
													: 'text-gray-600 dark:text-gray-300'}"
										>
											{selectorResult.decision === 'selected'
												? '可安全選擇'
												: selectorResult.decision === 'ambiguous'
													? '需要使用者確認'
													: '不應呼叫工作流'}
										</div>
										{#each selectorResult.items.slice(0, 3) as item}
											<div class="mt-2 flex items-center justify-between gap-3">
												<span class="truncate">{item.name}</span>
												<span class="font-mono">{Math.round(item.confidence * 100)}%</span>
											</div>
										{/each}
									</div>
								{/if}
							</div>
						{/if}
					</section>

					<section class="space-y-3">
						<div class="flex items-center justify-between">
							<div>
								<div class="text-sm font-semibold text-gray-900 dark:text-gray-100">節點庫</div>
								<div class="text-xs text-gray-500">
									{nodeSearch.trim()
										? `找到 ${filteredNodeDefinitions.length} 個節點`
										: `此分類有 ${selectedGroupCount} 個節點`}
								</div>
							</div>
							<button
								class="text-xs text-gray-500 hover:text-gray-900 dark:hover:text-gray-100"
								on:click={() => {
									jsonMode = !jsonMode;
									syncJson();
								}}
							>
								{jsonMode ? '畫布' : 'JSON'}
							</button>
						</div>
						<input
							class="w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm outline-none focus:border-blue-500 dark:border-gray-800"
							bind:value={nodeSearch}
							placeholder="搜尋節點"
							aria-label="搜尋工作流節點"
						/>
						<div class="node-group-scroll flex gap-2 overflow-x-auto pb-1">
							{#each NODE_GROUPS as group}
								<button
									class="node-group-pill {selectedNodeGroup === group.id ? 'selected' : ''}"
									on:click={() => {
										selectedNodeGroup = group.id;
									}}
									aria-pressed={selectedNodeGroup === group.id}
								>
									{group.label}
								</button>
							{/each}
						</div>
						<div class="node-palette space-y-2">
							{#if filteredNodeDefinitions.length === 0}
								<div
									class="rounded-lg border border-dashed border-gray-200 p-4 text-sm text-gray-500 dark:border-gray-800"
								>
									沒有符合搜尋的節點。
								</div>
							{:else}
								{#each filteredNodeDefinitions as definition}
									<button
										class="node-palette-item node-palette-{definition.category}"
										on:click={() => addNode(definition.type)}
										title={definition.description}
									>
										<span class="flex min-w-0 flex-1 flex-col text-left">
											<span class="flex items-center gap-2 text-sm font-semibold">
												<span class="truncate">{definition.label}</span>
												<span
													class="shrink-0 rounded px-1.5 py-0.5 text-[10px] {RUNTIME_SUPPORTED_NODE_TYPES.has(
														definition.type
													)
														? 'bg-green-50 text-green-700 dark:bg-green-950 dark:text-green-200'
														: 'bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-200'}"
												>
													{RUNTIME_SUPPORTED_NODE_TYPES.has(definition.type)
														? '可執行'
														: '設計預覽'}
												</span>
											</span>
											<span class="mt-1 text-xs leading-5 text-gray-500 dark:text-gray-400">
												{definition.description}
											</span>
										</span>
										<span class="node-type-badge">{definition.type}</span>
									</button>
								{/each}
							{/if}
						</div>
						<div
							class="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-900 dark:border-amber-900/70 dark:bg-amber-950/40 dark:text-amber-100"
						>
							發布時會檢查每個節點是否已有正式執行器；尚未支援的節點會阻擋發布，避免執行時才失敗。
						</div>
						<button
							class="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-800"
							on:click={connectLastTwo}
						>
							連接最後兩個節點
						</button>
						<button
							class="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-800"
							on:click={deleteSelected}
						>
							刪除選取項目
						</button>
					</section>

					{#if selectedNode}
						<section class="space-y-3 rounded-lg border border-gray-200 p-3 dark:border-gray-800">
							<div>
								<div class="text-sm font-semibold text-gray-900 dark:text-gray-100">節點設定</div>
								<div class="mt-1 text-xs text-gray-500">
									{selectedNode.data?.label || selectedNode.id} · {selectedNode.data?.type ||
										selectedNode.type}
								</div>
							</div>
							<div class="text-xs leading-5 text-gray-500">
								模型節點可設定 <code>model_id</code>、<code>system_prompt</code>；提示節點使用
								<code>template</code>；輸出節點可設定 <code>output_type</code>、<code>url</code
								>、<code>filename</code> 或 <code>title</code>。
							</div>
							<textarea
								class="h-40 w-full resize-y rounded-lg border border-gray-200 bg-transparent p-3 font-mono text-xs outline-none dark:border-gray-800"
								bind:value={nodeConfigJson}
								aria-label="節點 JSON 設定"
							></textarea>
							<button
								class="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-800"
								on:click={applyNodeConfig}>套用節點設定</button
							>
						</section>
					{/if}

					{#if validation}
						<section class="rounded-xl border border-gray-200 p-3 text-sm dark:border-gray-800">
							<div class="font-medium {validation.ok ? 'text-green-600' : 'text-red-600'}">
								{validation.ok ? '工作流有效' : '工作流需要修正'}
							</div>
							{#each validation.errors as error}
								<div class="mt-2 text-red-600">{error}</div>
							{/each}
							{#each validation.warnings as warning}
								<div class="mt-2 text-amber-600">{warning}</div>
							{/each}
						</section>
					{/if}

					<section class="space-y-3">
						<div class="text-sm font-semibold text-gray-900 dark:text-gray-100">測試執行</div>
						<textarea
							class="h-28 w-full resize-none rounded-lg border border-gray-200 bg-transparent p-3 font-mono text-xs outline-none dark:border-gray-800"
							bind:value={testInput}
						></textarea>
						<input
							class="w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm outline-none dark:border-gray-800"
							bind:value={testModelId}
							placeholder="測試模型 ID（模型節點未固定時使用）"
						/>
						<button
							class="w-full rounded-lg bg-gray-900 px-3 py-2 text-sm font-medium text-white dark:bg-white dark:text-gray-900"
							on:click={runWorkflow}
							disabled={running}
						>
							{running ? '執行中...' : '執行測試'}
						</button>
					</section>

					<section class="space-y-3">
						<div class="text-sm font-semibold text-gray-900 dark:text-gray-100">最近執行</div>
						{#if runs.length === 0}
							<div
								class="rounded-lg border border-dashed border-gray-200 p-4 text-sm text-gray-500 dark:border-gray-800"
							>
								尚無執行紀錄。
							</div>
						{:else}
							<div class="space-y-2">
								{#each runs as run}
									<div class="rounded-lg border border-gray-200 p-3 text-xs dark:border-gray-800">
										<div class="flex items-center justify-between gap-3">
											<span
												class="font-medium {run.status === 'success'
													? 'text-green-600'
													: run.status === 'error'
														? 'text-red-600'
														: 'text-gray-600'}"
											>
												{run.status}
											</span>
											<span class="text-gray-400"
												>{formatDate(run.completed_at ?? run.created_at)}</span
											>
										</div>
										{#if run.error}
											<div class="mt-2 text-red-600">{run.error}</div>
										{:else if run.output}
											<pre
												class="mt-2 max-h-28 overflow-auto whitespace-pre-wrap rounded bg-gray-50 p-2 text-gray-600 dark:bg-gray-900 dark:text-gray-300">{JSON.stringify(
													run.output,
													null,
													2
												)}</pre>
										{/if}
									</div>
								{/each}
							</div>
						{/if}
					</section>
				</fieldset>
			</aside>
		</div>
	</div>
{/if}

<style>
	.node-group-scroll {
		scrollbar-width: thin;
	}

	.node-group-pill {
		white-space: nowrap;
		border: 1px solid rgb(229 231 235);
		border-radius: 999px;
		padding: 0.45rem 0.75rem;
		font-size: 0.75rem;
		font-weight: 600;
		color: #475569;
		background: transparent;
		transition:
			background 0.15s ease,
			border-color 0.15s ease,
			color 0.15s ease;
	}

	.node-group-pill:hover,
	.node-group-pill.selected {
		border-color: #2563eb;
		background: rgb(37 99 235 / 0.08);
		color: #1d4ed8;
	}

	:global(.dark) .node-group-pill {
		border-color: rgb(31 41 55);
		color: #cbd5e1;
	}

	:global(.dark) .node-group-pill:hover,
	:global(.dark) .node-group-pill.selected {
		border-color: #60a5fa;
		background: rgb(96 165 250 / 0.12);
		color: #bfdbfe;
	}

	.node-palette {
		max-height: 25rem;
		overflow-y: auto;
		padding-right: 0.15rem;
		scrollbar-width: thin;
	}

	.node-palette-item {
		display: flex;
		width: 100%;
		align-items: flex-start;
		gap: 0.75rem;
		border: 1px solid rgb(229 231 235);
		border-left-width: 4px;
		border-radius: 0.5rem;
		background: #ffffff;
		padding: 0.75rem;
		color: #0f172a;
		transition:
			background 0.15s ease,
			border-color 0.15s ease,
			box-shadow 0.15s ease,
			transform 0.15s ease;
	}

	.node-palette-item:hover {
		border-color: rgb(148 163 184);
		box-shadow: 0 8px 20px rgb(15 23 42 / 0.08);
		transform: translateY(-1px);
	}

	.node-palette-trigger {
		border-left-color: #0ea5e9;
	}

	.node-palette-agent {
		border-left-color: #8b5cf6;
	}

	.node-palette-model {
		border-left-color: #2563eb;
	}

	.node-palette-prompt {
		border-left-color: #f59e0b;
	}

	.node-palette-rag {
		border-left-color: #14b8a6;
	}

	.node-palette-tool {
		border-left-color: #6366f1;
	}

	.node-palette-logic {
		border-left-color: #64748b;
	}

	.node-palette-output {
		border-left-color: #10b981;
	}

	.node-palette-ops {
		border-left-color: #ef4444;
	}

	.node-type-badge {
		max-width: 8.5rem;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		border-radius: 999px;
		background: rgb(241 245 249);
		padding: 0.2rem 0.45rem;
		font-family:
			ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New',
			monospace;
		font-size: 0.65rem;
		line-height: 1.2;
		color: #475569;
	}

	:global(.dark) .node-palette-item {
		border-color: rgb(31 41 55);
		background: #0f172a;
		color: #f8fafc;
	}

	:global(.dark) .node-palette-item:hover {
		border-color: rgb(71 85 105);
		box-shadow: 0 10px 24px rgb(0 0 0 / 0.3);
	}

	:global(.dark) .node-type-badge {
		background: rgb(30 41 59);
		color: #cbd5e1;
	}

	.workflow-canvas :global(.svelte-flow__node-default),
	.workflow-canvas :global(.svelte-flow__node-input),
	.workflow-canvas :global(.svelte-flow__node-output) {
		border: 1px solid rgb(148 163 184 / 0.75);
		border-radius: 10px;
		background: #ffffff;
		color: #111827;
		box-shadow:
			0 14px 30px rgb(15 23 42 / 0.12),
			0 0 0 1px rgb(255 255 255 / 0.7) inset;
		line-height: 1.25;
		text-align: center;
	}

	:global(.dark) .workflow-canvas :global(.svelte-flow__node-default),
	:global(.dark) .workflow-canvas :global(.svelte-flow__node-input),
	:global(.dark) .workflow-canvas :global(.svelte-flow__node-output) {
		border-color: rgb(100 116 139 / 0.9);
		background: #111827;
		color: #f8fafc;
		box-shadow:
			0 18px 35px rgb(0 0 0 / 0.35),
			0 0 0 1px rgb(255 255 255 / 0.04) inset;
	}

	.workflow-canvas :global(.svelte-flow__node-input) {
		border-color: #0ea5e9;
		color: #075985;
	}

	.workflow-canvas :global(.svelte-flow__node-output) {
		border-color: #10b981;
		color: #065f46;
	}

	:global(.dark) .workflow-canvas :global(.svelte-flow__node-input) {
		border-color: #38bdf8;
		color: #e0f2fe;
	}

	:global(.dark) .workflow-canvas :global(.svelte-flow__node-output) {
		border-color: #34d399;
		color: #dcfce7;
	}

	.workflow-canvas :global(.workflow-node-agent) {
		border-color: #8b5cf6;
	}

	.workflow-canvas :global(.workflow-node-model) {
		border-color: #2563eb;
	}

	.workflow-canvas :global(.workflow-node-prompt) {
		border-color: #f59e0b;
	}

	.workflow-canvas :global(.workflow-node-rag) {
		border-color: #14b8a6;
	}

	.workflow-canvas :global(.workflow-node-tool) {
		border-color: #6366f1;
	}

	.workflow-canvas :global(.workflow-node-logic) {
		border-color: #64748b;
	}

	.workflow-canvas :global(.workflow-node-ops) {
		border-color: #ef4444;
	}

	:global(.dark) .workflow-canvas :global(.workflow-node-agent) {
		border-color: #c4b5fd;
	}

	:global(.dark) .workflow-canvas :global(.workflow-node-model) {
		border-color: #93c5fd;
	}

	:global(.dark) .workflow-canvas :global(.workflow-node-prompt) {
		border-color: #fbbf24;
	}

	:global(.dark) .workflow-canvas :global(.workflow-node-rag) {
		border-color: #5eead4;
	}

	:global(.dark) .workflow-canvas :global(.workflow-node-tool) {
		border-color: #a5b4fc;
	}

	:global(.dark) .workflow-canvas :global(.workflow-node-logic) {
		border-color: #94a3b8;
	}

	:global(.dark) .workflow-canvas :global(.workflow-node-ops) {
		border-color: #fca5a5;
	}

	.workflow-canvas :global(.svelte-flow__node.selected),
	.workflow-canvas :global(.svelte-flow__node:focus-visible) {
		box-shadow:
			0 0 0 2px #f59e0b,
			0 18px 36px rgb(15 23 42 / 0.24);
	}

	.workflow-canvas :global(.svelte-flow__edge-path) {
		stroke: #475569;
		stroke-width: 2.2;
	}

	:global(.dark) .workflow-canvas :global(.svelte-flow__edge-path) {
		stroke: #93c5fd;
	}

	.workflow-canvas :global(.svelte-flow__edge.selected .svelte-flow__edge-path) {
		stroke: #f59e0b;
		stroke-width: 3;
	}

	.workflow-canvas :global(.svelte-flow__handle) {
		width: 12px;
		height: 12px;
		border: 2px solid #ffffff;
		background: #2563eb;
		box-shadow: 0 0 0 2px rgb(37 99 235 / 0.25);
	}

	:global(.dark) .workflow-canvas :global(.svelte-flow__handle) {
		border-color: #020617;
		background: #60a5fa;
		box-shadow: 0 0 0 2px rgb(96 165 250 / 0.35);
	}

	.workflow-canvas :global(.svelte-flow__controls) {
		overflow: hidden;
		border: 1px solid rgb(203 213 225);
		border-radius: 10px;
		box-shadow: 0 10px 25px rgb(15 23 42 / 0.14);
	}

	:global(.dark) .workflow-canvas :global(.svelte-flow__controls) {
		border-color: rgb(51 65 85);
		background: #0f172a;
		box-shadow: 0 14px 30px rgb(0 0 0 / 0.35);
	}

	.workflow-canvas :global(.svelte-flow__controls-button) {
		border-color: rgb(226 232 240);
		background: #ffffff;
		color: #0f172a;
	}

	:global(.dark) .workflow-canvas :global(.svelte-flow__controls-button) {
		border-color: rgb(51 65 85);
		background: #111827;
		color: #f8fafc;
	}

	.workflow-canvas :global(.svelte-flow__minimap) {
		border: 1px solid rgb(203 213 225);
		border-radius: 10px;
		background: rgb(248 250 252 / 0.94);
		box-shadow: 0 14px 30px rgb(15 23 42 / 0.14);
	}

	:global(.dark) .workflow-canvas :global(.svelte-flow__minimap) {
		border-color: rgb(51 65 85);
		background: rgb(15 23 42 / 0.94);
		box-shadow: 0 14px 30px rgb(0 0 0 / 0.35);
	}
</style>

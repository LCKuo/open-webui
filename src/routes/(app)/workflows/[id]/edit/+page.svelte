<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import { beforeNavigate, goto } from '$app/navigation';
	import { get } from 'svelte/store';
	import { writable } from 'svelte/store';
	import { toast } from 'svelte-sonner';
	import {
		Background,
		BackgroundVariant,
		Controls,
		MiniMap,
		SvelteFlow,
		type Viewport
	} from '@xyflow/svelte';
	import '@xyflow/svelte/dist/style.css';
	import { models, showSidebar, user } from '$lib/stores';
	import { getSemanticDatasets, type SemanticDataset } from '$lib/apis/interact-semantic';
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
	import WorkflowCanvasNode from '$lib/components/workflows/WorkflowCanvasNode.svelte';
	import WorkflowLaunchSettings from '$lib/components/workflows/WorkflowLaunchSettings.svelte';
	import WorkflowNodeLibrary from '$lib/components/workflows/WorkflowNodeLibrary.svelte';
	import {
		normalizeWorkflowLaunch,
		type WorkflowLaunchConfig
	} from '$lib/components/workflows/workflowLaunch';
	import {
		WORKFLOW_NODE_BY_TYPE,
		buildWorkflowNode,
		buildWorkflowTemplateGraph,
		type WorkflowConfigField
	} from '$lib/components/workflows/workflowNodeCatalog';
	import CheckCircle from '$lib/components/icons/CheckCircle.svelte';
	import ChevronLeft from '$lib/components/icons/ChevronLeft.svelte';
	import CodeBracket from '$lib/components/icons/CodeBracket.svelte';
	import InfoCircle from '$lib/components/icons/InfoCircle.svelte';
	import GarbageBin from '$lib/components/icons/GarbageBin.svelte';

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
	let launchConfig: WorkflowLaunchConfig = normalizeWorkflowLaunch({
		graph: { nodes: [], edges: [] },
		meta: {}
	} as any);
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
	let selectedNodeConfigId = '';
	let nodeConfigJson = '{}';
	let selectedNodeLabel = '';
	let inspectorTab: 'node' | 'workflow' | 'test' = 'node';
	let compactPanel: 'library' | 'canvas' | 'inspector' = 'canvas';
	let pendingTemplateId = '';
	let showTemplateConfirm = false;
	let semanticDatasets: SemanticDataset[] = [];
	let canvasElement: HTMLDivElement;

	const nodes = writable<any[]>([]);
	const edges = writable<any[]>([]);
	const viewport = writable<Viewport>({ x: 0, y: 0, zoom: 1 });
	const nodeTypes = { workflow: WorkflowCanvasNode };

	$: workflowId = $page.params.id ?? '';
	$: canEdit = Boolean(workflow && ($user?.role === 'admin' || workflow.user_id === $user?.id));
	$: selectedNode = $nodes.find((node) => node.selected) ?? null;
	$: if (selectedNode && selectedNode.id !== selectedNodeConfigId) {
		selectedNodeConfigId = selectedNode.id;
		selectedNodeLabel = selectedNode.data?.label ?? '';
		nodeConfigJson = JSON.stringify(selectedNode.data?.config ?? {}, null, 2);
		inspectorTab = 'node';
	}
	$: selectedNodeDefinition = selectedNode
		? WORKFLOW_NODE_BY_TYPE.get(selectedNode.data?.type ?? '')
		: undefined;
	$: selectedNodeFields = selectedNodeDefinition?.configFields ?? [];
	$: visibilityDescription =
		VISIBILITY_OPTIONS.find((option) => option.value === visibility)?.description ?? '';
	$: aclScopeDescription =
		ACL_SCOPE_OPTIONS.find((option) => option.value === aclScope)?.description ?? '';

	const hydrateNode = (node: any) => {
		const semanticType = node.data?.type ?? node.data?.kind ?? node.type;
		const definition =
			WORKFLOW_NODE_BY_TYPE.get(semanticType) ??
			(semanticType === 'input'
				? WORKFLOW_NODE_BY_TYPE.get('chat_input')
				: semanticType === 'output'
					? WORKFLOW_NODE_BY_TYPE.get('chat_output')
					: undefined);
		return {
			...node,
			type: 'workflow',
			style: undefined,
			class: 'workflow-node',
			className: 'workflow-node',
			data: {
				...(node.data ?? {}),
				type: semanticType,
				label: node.data?.label ?? definition?.label ?? semanticType,
				category: definition?.category ?? node.data?.category ?? 'control',
				description: definition?.description ?? node.data?.description ?? '',
				inputType: definition?.inputType ?? node.data?.inputType ?? 'any',
				outputType: definition?.outputType ?? node.data?.outputType ?? 'any',
				config: node.data?.config ?? definition?.defaultConfig ?? {}
			}
		};
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
			acl,
			launch: launchConfig
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
			type: 'workflow',
			class: 'workflow-node',
			className: 'workflow-node',
			style: undefined,
			data: {
				...(node.data ?? {}),
				type: node.data?.type ?? node.type,
				category:
					node.data?.category ??
					WORKFLOW_NODE_BY_TYPE.get(node.data?.type ?? node.type)?.category ??
					'control',
				description:
					node.data?.description ??
					WORKFLOW_NODE_BY_TYPE.get(node.data?.type ?? node.type)?.description ??
					'',
				inputType:
					node.data?.inputType ??
					WORKFLOW_NODE_BY_TYPE.get(node.data?.type ?? node.type)?.inputType ??
					'any',
				outputType:
					node.data?.outputType ??
					WORKFLOW_NODE_BY_TYPE.get(node.data?.type ?? node.type)?.outputType ??
					'any'
			}
		})),
		edges: get(edges).map((edge) => ({ ...edge, type: edge.type ?? 'smoothstep' }))
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
			launchConfig = normalizeWorkflowLaunch(res);
			syncAclState(meta);
			nodes.set((res.graph?.nodes ?? []).map(hydrateNode));
			edges.set(
				(res.graph?.edges ?? []).map((edge) => ({ ...edge, type: edge.type ?? 'smoothstep' }))
			);
			syncJson();
			[runs, semanticDatasets] = await Promise.all([
				getWorkflowRuns(localStorage.token, workflowId, 10).catch(() => []),
				getSemanticDatasets(localStorage.token)
					.then((result) => result.datasets.filter((dataset) => dataset.status === 'published'))
					.catch(() => [])
			]);
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
			launchConfig = normalizeWorkflowLaunch(res);
			syncAclState(meta);
			nodes.set((res.graph?.nodes ?? []).map(hydrateNode));
			edges.set(
				(res.graph?.edges ?? []).map((edge) => ({ ...edge, type: edge.type ?? 'smoothstep' }))
			);
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

	const addNode = (kind: string, position?: { x: number; y: number }) => {
		const definition = WORKFLOW_NODE_BY_TYPE.get(kind);
		if (!definition) {
			toast.error('這個節點尚未提供正式執行器。');
			return;
		}
		const next = get(nodes).length;
		const id = `${kind}-${Date.now()}`;
		const currentViewport = get(viewport);
		const rect = canvasElement?.getBoundingClientRect();
		const fallbackPosition = rect
			? {
					x: (rect.width * 0.5 - currentViewport.x) / currentViewport.zoom - 132 + (next % 3) * 24,
					y: (rect.height * 0.45 - currentViewport.y) / currentViewport.zoom - 70 + (next % 3) * 24
				}
			: { x: 120 + next * 36, y: 120 + next * 28 };
		const node = buildWorkflowNode(kind, id, position ?? fallbackPosition);
		nodes.update((items) => [
			...items.map((item) => ({ ...item, selected: false })),
			{ ...node, selected: true }
		]);
		selectedNodeConfigId = '';
		inspectorTab = 'node';
		compactPanel = 'canvas';
	};

	const handleCanvasDragOver = (event: DragEvent) => {
		if (!canEdit) return;
		event.preventDefault();
		if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
	};

	const handleCanvasDrop = (event: DragEvent) => {
		if (!canEdit || !canvasElement || !event.dataTransfer) return;
		event.preventDefault();
		const kind = event.dataTransfer.getData('application/interact-workflow-node');
		if (!kind) return;
		const rect = canvasElement.getBoundingClientRect();
		const currentViewport = get(viewport);
		addNode(kind, {
			x: (event.clientX - rect.left - currentViewport.x) / currentViewport.zoom - 132,
			y: (event.clientY - rect.top - currentViewport.y) / currentViewport.zoom - 60
		});
	};

	const applyTemplate = (templateId: string) => {
		const graph = buildWorkflowTemplateGraph(templateId);
		nodes.set(graph.nodes.map(hydrateNode));
		edges.set(graph.edges);
		viewport.set({ x: 30, y: 80, zoom: 0.62 });
		validation = null;
		selectedNodeConfigId = '';
		markDirty();
		compactPanel = 'canvas';
		toast.success('已套用入門範本，請依標示完成必要設定。');
	};

	const requestTemplate = (templateId: string) => {
		if (get(nodes).length === 0) {
			applyTemplate(templateId);
			return;
		}
		pendingTemplateId = templateId;
		showTemplateConfirm = true;
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
		if (
			get(edges).some(
				(edge) => edge.source === connection.source && edge.target === connection.target
			)
		)
			return false;

		const currentNodes = get(nodes);
		const sourceNode = currentNodes.find((node) => node.id === connection.source);
		const targetNode = currentNodes.find((node) => node.id === connection.target);
		const sourceType = sourceNode?.data?.outputType ?? 'any';
		const targetType = targetNode?.data?.inputType ?? 'any';
		if (sourceType === 'none' || targetType === 'none') return false;
		if (sourceType !== 'any' && targetType !== 'any' && sourceType !== targetType) return false;

		const adjacency = new Map<string, string[]>();
		for (const edge of get(edges)) {
			adjacency.set(edge.source, [...(adjacency.get(edge.source) ?? []), edge.target]);
		}
		adjacency.set(connection.source, [
			...(adjacency.get(connection.source) ?? []),
			connection.target
		]);
		const stack = [connection.target];
		const visited = new Set<string>();
		while (stack.length) {
			const nodeId = stack.pop()!;
			if (nodeId === connection.source) return false;
			if (visited.has(nodeId)) continue;
			visited.add(nodeId);
			stack.push(...(adjacency.get(nodeId) ?? []));
		}
		return true;
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
					node.id === selectedNode.id
						? {
								...node,
								data: {
									...(node.data ?? {}),
									label: selectedNodeLabel.trim() || selectedNodeDefinition?.label || node.id,
									config
								}
							}
						: node
				)
			);
			markDirty();
			toast.success('節點設定已套用');
		} catch (error) {
			toast.error(`${error}`);
		}
	};

	const readNodeConfig = () => {
		try {
			const parsed = JSON.parse(nodeConfigJson);
			return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
		} catch {
			return {};
		}
	};

	const configFieldValue = (field: WorkflowConfigField) => {
		const value = readNodeConfig()[field.key];
		if (field.type === 'tags') return Array.isArray(value) ? value.join(', ') : (value ?? '');
		if (field.type === 'json') return JSON.stringify(value ?? {}, null, 2);
		return value ?? (field.type === 'checkbox' ? false : '');
	};

	const updateConfigField = (field: WorkflowConfigField, rawValue: unknown) => {
		const config = readNodeConfig();
		let value = rawValue;
		if (field.type === 'tags') {
			value = String(rawValue)
				.split(',')
				.map((item) => item.trim())
				.filter(Boolean);
		} else if (field.type === 'number') {
			value = rawValue === '' ? undefined : Number(rawValue);
		} else if (field.type === 'json') {
			try {
				value = JSON.parse(String(rawValue));
			} catch {
				toast.error(`${field.label}不是有效的 JSON。`);
				return;
			}
		}
		if (value === undefined) delete config[field.key];
		else config[field.key] = value;
		nodeConfigJson = JSON.stringify(config, null, 2);
	};

	const nodeMissingRequiredConfig = () =>
		selectedNodeFields.filter((field) => {
			if (!field.required) return false;
			const value = readNodeConfig()[field.key];
			return (
				value === undefined ||
				value === null ||
				value === '' ||
				(Array.isArray(value) && !value.length)
			);
		});

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

<ConfirmDialog
	bind:show={showTemplateConfirm}
	title="以範本取代目前畫布？"
	confirmLabel="套用範本"
	on:confirm={() => {
		if (pendingTemplateId) applyTemplate(pendingTemplateId);
		pendingTemplateId = '';
	}}
>
	<div class="text-sm leading-6 text-gray-500">
		目前節點與連線會被範本取代。你仍可在離開頁面前選擇不儲存。
	</div>
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
					class="inline-flex items-center gap-1 rounded-lg border border-gray-200 px-3 py-2 text-sm hover:bg-gray-50 dark:border-gray-800 dark:hover:bg-gray-900"
					on:click={leaveEditor}
					title="返回工作流中心"
				>
					<ChevronLeft className="size-4" /> 返回
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
								? '已停用'
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
						class="inline-flex items-center gap-2 rounded-lg border border-gray-200 px-3 py-2 text-sm hover:bg-gray-50 dark:border-gray-800 dark:hover:bg-gray-900"
						on:click={() => {
							jsonMode = !jsonMode;
							syncJson();
						}}
						title="切換畫布 JSON"
					>
						<CodeBracket className="size-4" />
						{jsonMode ? '返回畫布' : 'JSON'}
					</button>
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

		<div
			class="compact-panel-tabs border-b border-gray-200 bg-white p-2 dark:border-gray-800 dark:bg-gray-950"
		>
			<div class="grid grid-cols-3 rounded-lg bg-gray-100 p-1 dark:bg-gray-900">
				{#each [{ id: 'library', label: '節點與範本' }, { id: 'canvas', label: '畫布' }, { id: 'inspector', label: selectedNode ? '節點設定' : '工作流設定' }] as option}
					<button
						class="rounded-md px-3 py-1.5 text-xs font-medium {compactPanel === option.id
							? 'bg-white text-gray-900 shadow-sm dark:bg-gray-800 dark:text-white'
							: 'text-gray-500'}"
						on:click={() => (compactPanel = option.id as typeof compactPanel)}
						>{option.label}</button
					>
				{/each}
			</div>
		</div>

		<div class="workflow-editor-layout min-h-0 flex-1" data-compact-panel={compactPanel}>
			<WorkflowNodeLibrary {canEdit} onAdd={addNode} onApplyTemplate={requestTemplate} />

			<div
				class="workflow-canvas relative min-h-[520px] bg-slate-50 dark:bg-[#070b12]"
				bind:this={canvasElement}
				on:dragover={handleCanvasDragOver}
				on:drop={handleCanvasDrop}
				role="application"
				aria-label="工作流畫布"
			>
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
					<div
						class="canvas-guide pointer-events-none absolute left-4 top-4 z-10 max-w-[22rem] rounded-lg border border-gray-200 bg-white/95 px-3 py-2 shadow-sm backdrop-blur dark:border-gray-700 dark:bg-gray-900/95"
					>
						<div class="text-xs font-semibold text-gray-800 dark:text-gray-100">建立方式</div>
						<div class="mt-1 text-xs leading-5 text-gray-500">
							1. 從左側新增節點　2. 從右側圓點拖線　3. 選取節點完成設定
						</div>
					</div>
					{#if $nodes.length === 0}
						<div
							class="pointer-events-none absolute inset-0 z-[5] flex items-center justify-center p-6"
						>
							<div
								class="pointer-events-auto max-w-sm rounded-lg border border-dashed border-gray-300 bg-white p-5 text-center shadow-sm dark:border-gray-700 dark:bg-gray-900"
							>
								<div class="text-base font-semibold text-gray-900 dark:text-gray-100">
									從第一個節點開始
								</div>
								<div class="mt-2 text-sm leading-6 text-gray-500">
									開啟左側「入門範本」，或新增一個輸入節點。
								</div>
								<button
									class="mt-4 rounded-lg bg-gray-900 px-4 py-2 text-sm font-medium text-white dark:bg-white dark:text-gray-900"
									on:click={() => requestTemplate('chat-assistant')}
								>
									使用聊天助理範本
								</button>
							</div>
						</div>
					{/if}
					<SvelteFlow
						{nodes}
						{edges}
						{nodeTypes}
						{viewport}
						fitView
						fitViewOptions={{ padding: 0.22, maxZoom: 1 }}
						onedgecreate={createEdge}
						{isValidConnection}
						nodesDraggable={canEdit}
						nodesConnectable={canEdit}
						elementsSelectable={canEdit}
						deleteKey={canEdit ? ['Backspace', 'Delete'] : null}
						connectionRadius={28}
						on:nodeclick={() => {
							inspectorTab = 'node';
							compactPanel = 'inspector';
						}}
					>
						<Controls />
						<MiniMap pannable zoomable nodeColor="#64748b" maskColor="rgb(15 23 42 / 0.08)" />
						<Background variant={BackgroundVariant.Dots} gap={32} size={1.2} />
					</SvelteFlow>
					<div
						class="canvas-status absolute bottom-3 left-1/2 z-10 -translate-x-1/2 rounded-lg border border-gray-200 bg-white/95 px-3 py-1.5 text-xs text-gray-500 shadow-sm backdrop-blur dark:border-gray-700 dark:bg-gray-900/95"
					>
						{$nodes.length} 個節點 · {$edges.length} 條連線 · 空白鍵拖曳畫布 · Delete 刪除
					</div>
				{/if}
			</div>

			<aside
				class="workflow-inspector flex min-h-0 flex-col overflow-hidden border-l border-gray-200 bg-white dark:border-gray-800 dark:bg-gray-950"
			>
				<div class="grid grid-cols-3 border-b border-gray-200 p-2 dark:border-gray-800">
					<button
						class="inspector-tab {inspectorTab === 'node' ? 'active' : ''}"
						on:click={() => (inspectorTab = 'node')}
						disabled={!selectedNode}>節點</button
					>
					<button
						class="inspector-tab {inspectorTab === 'workflow' ? 'active' : ''}"
						on:click={() => (inspectorTab = 'workflow')}>工作流</button
					>
					<button
						class="inspector-tab {inspectorTab === 'test' ? 'active' : ''}"
						on:click={() => (inspectorTab = 'test')}>檢查與測試</button
					>
				</div>
				<div class="min-h-0 flex-1 overflow-y-auto p-4">
					<fieldset class="contents" disabled={!canEdit}>
						{#if inspectorTab === 'workflow'}
							<section class="space-y-3">
								<div class="text-sm font-semibold text-gray-900 dark:text-gray-100">工作流設定</div>
								<textarea
									class="h-20 w-full resize-none rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm outline-none dark:border-gray-800"
									bind:value={description}
									on:input={() => queueMicrotask(markDirty)}
									placeholder="描述"
								></textarea>
								<div class="border-t border-gray-200 pt-4 dark:border-gray-800">
									<WorkflowLaunchSettings
										value={launchConfig}
										disabled={!canEdit}
										onChange={(next) => {
											launchConfig = next;
											queueMicrotask(markDirty);
										}}
									/>
								</div>
								<div class="border-t border-gray-200 pt-4 dark:border-gray-800">
									<div class="mb-3 text-sm font-semibold text-gray-900 dark:text-gray-100">
										分享與存取
									</div>
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
										<label
											class="block space-y-1 text-xs font-medium text-gray-600 dark:text-gray-300"
										>
											<span>意圖片語</span>
											<input
												class="w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm font-normal outline-none dark:border-gray-800"
												bind:value={intentKeywords}
												on:input={() => queueMicrotask(markDirty)}
												placeholder="例如：查詢發票, 發票付款狀態"
											/>
										</label>
										<label
											class="block space-y-1 text-xs font-medium text-gray-600 dark:text-gray-300"
										>
											<span>使用者說法範例（每行一則）</span>
											<textarea
												class="h-24 w-full resize-y rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm font-normal outline-none dark:border-gray-800"
												bind:value={intentExamples}
												on:input={() => queueMicrotask(markDirty)}
												placeholder="幫我查上個月的發票付款狀態"
											></textarea>
										</label>
										<label
											class="block space-y-1 text-xs font-medium text-gray-600 dark:text-gray-300"
										>
											<span>必要詞（必須全部出現）</span>
											<input
												class="w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm font-normal outline-none dark:border-gray-800"
												bind:value={requiredKeywords}
												on:input={() => queueMicrotask(markDirty)}
												placeholder="例如：A 公司"
											/>
										</label>
										<label
											class="block space-y-1 text-xs font-medium text-gray-600 dark:text-gray-300"
										>
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
						{/if}

						{#if inspectorTab === 'node'}
							{#if selectedNode && selectedNodeDefinition}
								<section class="space-y-4">
									<div class="rounded-lg border border-gray-200 p-3 dark:border-gray-800">
										<div class="flex items-start justify-between gap-3">
											<div>
												<div class="text-sm font-semibold text-gray-900 dark:text-gray-100">
													{selectedNodeDefinition.label}
												</div>
												<div class="mt-1 font-mono text-[11px] text-gray-400">
													{selectedNodeDefinition.type}
												</div>
											</div>
											{#if nodeMissingRequiredConfig().length === 0}
												<span
													class="inline-flex items-center gap-1 rounded bg-green-50 px-2 py-1 text-[11px] font-medium text-green-700 dark:bg-green-950/50 dark:text-green-200"
												>
													<CheckCircle className="size-3.5" /> 設定完整
												</span>
											{:else}
												<span
													class="rounded bg-amber-50 px-2 py-1 text-[11px] font-medium text-amber-700 dark:bg-amber-950/50 dark:text-amber-200"
													>缺少必要設定</span
												>
											{/if}
										</div>
										<p class="mt-3 text-xs leading-5 text-gray-500">
											{selectedNodeDefinition.description}
										</p>
									</div>

									<label class="block space-y-1.5">
										<span class="text-xs font-semibold text-gray-700 dark:text-gray-200"
											>畫布顯示名稱</span
										>
										<input
											class="editor-input"
											bind:value={selectedNodeLabel}
											placeholder={selectedNodeDefinition.label}
										/>
									</label>

									{#each selectedNodeFields as field}
										<label class="block space-y-1.5">
											<span
												class="flex items-center gap-1 text-xs font-semibold text-gray-700 dark:text-gray-200"
											>
												{field.label}{#if field.required}<span
														class="text-red-500"
														aria-label="必填">*</span
													>{/if}
											</span>
											{#if field.type === 'textarea'}
												<textarea
													class="editor-input min-h-28 resize-y"
													value={configFieldValue(field)}
													placeholder={field.placeholder ?? ''}
													on:input={(event) => updateConfigField(field, event.currentTarget.value)}
												></textarea>
											{:else if field.type === 'select'}
												<select
													class="editor-input"
													value={configFieldValue(field)}
													on:change={(event) => updateConfigField(field, event.currentTarget.value)}
												>
													<option value="">請選擇</option>
													{#if field.key === 'model_id'}
														{#each $models as model}
															<option value={model.id}>{model.name ?? model.id}</option>
														{/each}
													{:else if field.key === 'dataset_id'}
														{#each semanticDatasets as dataset}
															<option value={dataset.id}>{dataset.name}</option>
														{/each}
													{:else}
														{#each field.options ?? [] as option}
															<option value={option.value}>{option.label}</option>
														{/each}
													{/if}
												</select>
											{:else if field.type === 'checkbox'}
												<span
													class="flex items-start gap-2 rounded-lg border border-gray-200 p-3 dark:border-gray-800"
												>
													<input
														class="mt-0.5"
														type="checkbox"
														checked={Boolean(configFieldValue(field))}
														on:change={(event) =>
															updateConfigField(field, event.currentTarget.checked)}
													/>
													<span class="text-xs leading-5 text-gray-500">{field.help}</span>
												</span>
											{:else if field.type === 'json'}
												<textarea
													class="editor-input min-h-36 resize-y font-mono text-xs"
													value={configFieldValue(field)}
													on:change={(event) => updateConfigField(field, event.currentTarget.value)}
												></textarea>
											{:else}
												<input
													class="editor-input"
													type={field.type === 'number' ? 'number' : 'text'}
													value={configFieldValue(field)}
													min={field.min}
													max={field.max}
													step={field.step}
													placeholder={field.placeholder ?? ''}
													on:input={(event) => updateConfigField(field, event.currentTarget.value)}
												/>
											{/if}
											{#if field.help && field.type !== 'checkbox'}
												<span class="block text-xs leading-5 text-gray-500">{field.help}</span>
											{/if}
										</label>
									{/each}

									<details class="rounded-lg border border-gray-200 dark:border-gray-800">
										<summary
											class="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-xs font-medium text-gray-600 dark:text-gray-300"
										>
											<CodeBracket className="size-4" /> 進階 JSON
										</summary>
										<div class="border-t border-gray-200 p-3 dark:border-gray-800">
											<textarea
												class="h-44 w-full resize-y rounded-lg border border-gray-200 bg-transparent p-3 font-mono text-xs outline-none focus:border-blue-500 dark:border-gray-800"
												bind:value={nodeConfigJson}
												aria-label="節點進階 JSON 設定"
											></textarea>
										</div>
									</details>

									<div
										class="sticky bottom-0 -mx-4 flex gap-2 border-t border-gray-200 bg-white px-4 py-3 dark:border-gray-800 dark:bg-gray-950"
									>
										<button
											class="inline-flex flex-1 items-center justify-center gap-2 rounded-lg bg-gray-900 px-3 py-2 text-sm font-medium text-white dark:bg-white dark:text-gray-900"
											on:click={applyNodeConfig}>套用設定</button
										>
										<button
											class="inline-flex items-center justify-center rounded-lg border border-red-200 px-3 py-2 text-red-600 hover:bg-red-50 dark:border-red-900 dark:hover:bg-red-950"
											on:click={deleteSelected}
											title="刪除節點"
											aria-label="刪除節點"
										>
											<GarbageBin className="size-4" />
										</button>
									</div>
								</section>
							{:else}
								<section
									class="rounded-lg border border-dashed border-gray-300 p-5 text-center dark:border-gray-700"
								>
									<div
										class="mx-auto flex size-9 items-center justify-center rounded-lg bg-gray-100 text-gray-500 dark:bg-gray-900"
									>
										<InfoCircle className="size-5" />
									</div>
									<div class="mt-3 text-sm font-semibold text-gray-900 dark:text-gray-100">
										尚未選取節點
									</div>
									<p class="mt-2 text-xs leading-5 text-gray-500">
										在畫布選取節點後，這裡會顯示對應欄位與必要設定。
									</p>
								</section>
							{/if}
						{/if}

						{#if inspectorTab === 'test'}
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
											<div
												class="rounded-lg border border-gray-200 p-3 text-xs dark:border-gray-800"
											>
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
						{/if}
					</fieldset>
				</div>
			</aside>
		</div>
	</div>
{/if}

<style>
	.workflow-editor-layout {
		display: grid;
		grid-template-columns: 292px minmax(420px, 1fr) 370px;
		min-height: 0;
	}

	.compact-panel-tabs {
		display: none;
	}

	.workflow-canvas,
	.workflow-inspector {
		min-width: 0;
		min-height: 0;
	}

	.inspector-tab {
		border-radius: 7px;
		padding: 0.5rem 0.35rem;
		font-size: 0.75rem;
		font-weight: 600;
		color: #64748b;
		transition:
			background 0.15s ease,
			color 0.15s ease;
	}

	.inspector-tab:hover:not(:disabled) {
		background: #f1f5f9;
		color: #0f172a;
	}

	.inspector-tab.active {
		background: #e0e7ff;
		color: #3730a3;
	}

	.inspector-tab:disabled {
		cursor: not-allowed;
		opacity: 0.42;
	}

	:global(.dark) .inspector-tab:hover:not(:disabled) {
		background: #1e293b;
		color: #f8fafc;
	}

	:global(.dark) .inspector-tab.active {
		background: #312e81;
		color: #e0e7ff;
	}

	.editor-input {
		display: block;
		width: 100%;
		border: 1px solid #d1d5db;
		border-radius: 8px;
		background: transparent;
		padding: 0.55rem 0.7rem;
		font-size: 0.875rem;
		line-height: 1.35;
		outline: none;
		transition:
			border-color 0.15s ease,
			box-shadow 0.15s ease;
	}

	.editor-input:focus {
		border-color: #2563eb;
		box-shadow: 0 0 0 3px rgb(37 99 235 / 0.14);
	}

	:global(.dark) .editor-input {
		border-color: #374151;
		color: #f8fafc;
	}

	:global(.dark) .editor-input option {
		background: #111827;
		color: #f8fafc;
	}

	@media (max-width: 1279px) {
		.compact-panel-tabs {
			display: block;
		}

		.workflow-editor-layout {
			display: block;
			position: relative;
		}

		.workflow-editor-layout > :global(*) {
			display: none;
			height: 100%;
		}

		.workflow-editor-layout[data-compact-panel='library'] > :global(.node-library),
		.workflow-editor-layout[data-compact-panel='canvas'] > .workflow-canvas,
		.workflow-editor-layout[data-compact-panel='inspector'] > .workflow-inspector {
			display: flex;
		}

		.workflow-editor-layout[data-compact-panel='canvas'] > .workflow-canvas {
			display: block;
		}

		.workflow-inspector {
			border-left: 0;
		}
	}

	@media (max-width: 639px) {
		.canvas-guide,
		.canvas-status,
		.workflow-canvas :global(.svelte-flow__minimap) {
			display: none;
		}
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

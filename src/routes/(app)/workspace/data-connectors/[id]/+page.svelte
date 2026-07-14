<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import { beforeNavigate, goto } from '$app/navigation';
	import { toast } from 'svelte-sonner';
	import SemanticBulkAuthorizationDialog from '$lib/components/workspace/data-connectors/SemanticBulkAuthorizationDialog.svelte';
	import SemanticPermissionReviewDialog from '$lib/components/workspace/data-connectors/SemanticPermissionReviewDialog.svelte';

	import {
		getInteractDataConnectors,
		type InteractDataConnector
	} from '$lib/apis/interact-data-connectors';
	import {
		activateSemanticDatasetVersion,
		applySemanticCatalogPermissionChange,
		bulkSemanticCatalogAuthorization,
		createSchemaSnapshot,
		deleteRowPolicy,
		deleteSemanticDataset,
		getRowPolicies,
		getSchemaSnapshots,
		getSemanticAiSchemaHandoff,
		getSemanticCatalog,
		getSemanticDatasets,
		getSemanticDatasetVersions,
		getSemanticQueryEvents,
		patchCatalogField,
		patchCatalogObjects,
		publishSemanticDataset,
		resolveSemanticDatasetImport,
		saveCatalogRelationship,
		saveRowPolicy,
		saveSemanticDataset,
		testSemanticDatasetQuery,
		validateSemanticDataset,
		type CatalogField,
		type CatalogObject,
		type BulkCatalogAuthorizationResult,
		type RowPolicy,
		type SchemaSnapshot,
		type SemanticCatalog,
		type SemanticDataset,
		type SemanticDatasetDefinition,
		type SemanticDatasetImportIssue,
		type SemanticDatasetImportPayload,
		type SemanticPermissionChange,
		type SemanticDatasetVersion,
		type SemanticQueryEvent
	} from '$lib/apis/interact-semantic';

	type Tab =
		| 'overview'
		| 'catalog'
		| 'relationships'
		| 'datasets'
		| 'policies'
		| 'lab'
		| 'activity';

	const connectorId = $page.params.id ?? '';
	const tabs: { id: Tab; label: string }[] = [
		{ id: 'overview', label: '概覽' },
		{ id: 'catalog', label: '資料目錄' },
		{ id: 'relationships', label: '關聯' },
		{ id: 'datasets', label: '語意資料集' },
		{ id: 'policies', label: '資料列權限' },
		{ id: 'lab', label: '查詢實驗室' },
		{ id: 'activity', label: '活動紀錄' }
	];

	let activeTab: Tab = 'overview';
	let loading = true;
	let busy = false;
	let catalogBusy = false;
	let connector: InteractDataConnector | null = null;
	let snapshots: SchemaSnapshot[] = [];
	let catalog: SemanticCatalog = { snapshotId: null, objects: [], relationships: [] };
	let datasets: SemanticDataset[] = [];
	let events: SemanticQueryEvent[] = [];
	let expandedObjectId = '';
	let operationStatus = '';
	let catalogStatus = '';
	let returnTo = '';
	let bulkAuthorizationPreview: BulkCatalogAuthorizationResult | null = null;
	let bulkAuthorizationAuthorized = true;
	let bulkAuthorizationObjectIds: string[] = [];
	let bulkAuthorizationScopeLabel = '';
	let bulkAuthorizationBusy = false;
	let bulkAuthorizationError = '';

	let editingDatasetId = '';
	let datasetName = '';
	let datasetSlug = '';
	let datasetDescription = '';
	let datasetDomain = '';
	let datasetWhenToUse = '';
	let datasetNotFor = '';
	let datasetExamples = '';
	let datasetSynonyms = '';
	let datasetAccessMode = 'company_admins';
	let rootObjectId = '';
	let dimensionFieldIds: string[] = [];
	let measureFieldIds: string[] = [];
	let relationshipIds: string[] = [];
	let allowedMemberIds = '';
	let allowedGroupIds = '';
	let allowedModelIds = '';
	let allowedChannelIds = '';
	let allowedWorkflowIds = '';
	let datasetVersions: SemanticDatasetVersion[] = [];
	let datasetDefinition: SemanticDatasetDefinition = {};
	let datasetFormInitialized = false;
	let savedDatasetSignature = '';
	let datasetImportOpen = false;
	let datasetImportJson = '';
	let datasetImportBusy = false;
	let datasetImportErrors: SemanticDatasetImportIssue[] = [];
	let datasetImportWarnings: SemanticDatasetImportIssue[] = [];
	let datasetImportStatus = '';
	let datasetImportCandidate: SemanticDatasetImportPayload | null = null;
	let datasetImportDocument: Record<string, unknown> | null = null;
	let datasetImportServerReady = false;
	let datasetHandoffBusy = false;
	let permissionReviewChanges: SemanticPermissionChange[] = [];
	let permissionReviewIndex = 0;
	let permissionReviewOpen = false;
	let permissionReviewBusy = false;
	let permissionReviewError = '';
	let permissionReviewDecisions: Record<string, 'accepted' | 'skipped'> = {};

	const portableDatasetTemplate = {
		format: 'interact-semantic-dataset',
		version: 1,
		permissionRecommendations: [],
		dataset: {
			name: 'Delivery performance',
			slug: 'delivery-performance',
			description: 'Delivery volume, fees, and carrier performance.',
			businessDomain: 'Logistics',
			access: {
				mode: 'company_admins',
				memberIds: [],
				groupIds: [],
				modelIds: [],
				channelIds: [],
				workflowIds: []
			},
			model: {
				rootObject: 'warehouse.shipments',
				whenToUse: 'Delivery rankings, fees, and completion rates.',
				notFor: ['inventory levels'],
				examples: ['Which carrier delivered the most shipments this month?'],
				synonyms: ['carrier', 'parcel'],
				defaultTimeDimension: 'shipment.delivered_at',
				dimensions: [
					{
						id: 'carrier.name',
						name: 'Carrier',
						field: { object: 'directory.carriers', field: 'display_name' }
					},
					{
						id: 'shipment.delivered_at',
						name: 'Delivered at',
						field: { object: 'warehouse.shipments', field: 'delivered_at' }
					}
				],
				measures: [
					{
						id: 'shipment.count',
						name: 'Shipments',
						field: { object: 'warehouse.shipments', field: 'tracking_id' },
						aggregation: 'count_distinct',
						filters: [
							{
								field: { object: 'warehouse.shipments', field: 'status' },
								operator: 'eq',
								value: 'delivered'
							}
						]
					}
				],
				metrics: [],
				relationships: [
					{
						leftObject: 'warehouse.shipments',
						rightObject: 'directory.carriers',
						joinPairs: [{ leftField: 'carrier_id', rightField: 'id' }]
					}
				]
			}
		}
	};

	let selectedPolicyDatasetId = '';
	let policies: RowPolicy[] = [];
	let policyName = '';
	let policyPrincipalType = 'all';
	let policyPrincipalIds = '';
	let policyFieldId = '';
	let policyOperator = 'eq';
	let policyValue = '$context.companyMemberId';
	let policyPublished = false;

	let labDatasetId = '';
	let labDimensions: string[] = [];
	let labMeasures: string[] = [];
	let labLimit = 20;
	let labResult: Record<string, unknown> | null = null;
	let labMode: 'validate' | 'execute' | '' = '';
	let labEditorMode: 'form' | 'json' = 'form';
	let labPlanJson = '';

	$: connectorDatasets = datasets.filter((item) => item.connector_id === connectorId);
	$: enabledObjects = catalog.objects.filter((item) => item.enabled);
	$: allObjectsEnabled =
		catalog.objects.length > 0 && enabledObjects.length === catalog.objects.length;
	$: selectedPolicyDataset =
		connectorDatasets.find((item) => item.id === selectedPolicyDatasetId) ?? null;
	$: labDataset = connectorDatasets.find((item) => item.id === labDatasetId) ?? null;
	$: labDefinition = (labDataset?.draft_definition ?? {}) as {
		dimensions?: { id: string; name?: string }[];
		measures?: { id: string; name?: string }[];
	};
	$: labFields = Array.isArray(labResult?.fields)
		? (labResult.fields as { id: string; label?: string }[])
		: [];
	$: labRows = Array.isArray(labResult?.rows) ? (labResult.rows as Record<string, unknown>[]) : [];
	$: datasetDirty = datasetFormInitialized && savedDatasetSignature !== datasetFormSignature();
	$: permissionReviewComplete = permissionReviewChanges.every((change) =>
		Boolean(permissionReviewDecisions[change.id])
	);
	$: datasetImportCanApply = Boolean(
		datasetImportCandidate && datasetImportServerReady && permissionReviewComplete
	);

	const splitIds = (value: string) =>
		value
			.split(',')
			.map((item) => item.trim())
			.filter(Boolean);
	const fieldLabel = (field: CatalogField) => field.display_name || field.physical_name;
	const objectName = (id: string) =>
		catalog.objects.find((item) => item.id === id)?.display_name ?? id;
	const time = (value?: number | null) => (value ? new Date(value * 1000).toLocaleString() : '-');
	const safeReturnUrl = (value: string | null) => {
		if (!value) return '';
		try {
			const url = new URL(value);
			const allowed = [
				'interact-vision.com.tw',
				'www.interact-vision.com.tw',
				'localhost',
				'127.0.0.1'
			];
			return ['http:', 'https:'].includes(url.protocol) && allowed.includes(url.hostname)
				? url.toString()
				: '';
		} catch {
			return '';
		}
	};
	const datasetFormSignature = () =>
		JSON.stringify({
			editingDatasetId,
			datasetName,
			datasetSlug,
			datasetDescription,
			datasetDomain,
			datasetWhenToUse,
			datasetNotFor,
			datasetExamples,
			datasetSynonyms,
			datasetAccessMode,
			rootObjectId,
			dimensionFieldIds,
			measureFieldIds,
			relationshipIds,
			allowedMemberIds,
			allowedGroupIds,
			allowedModelIds,
			allowedChannelIds,
			allowedWorkflowIds,
			datasetDefinition
		});
	const confirmDiscardDatasetChanges = () =>
		!datasetDirty || confirm('資料集草稿有尚未儲存的變更，確定要離開嗎？');
	const defaultAggregation = (field: CatalogField) =>
		field.default_aggregation ||
		(field.primary_key || field.semantic_type === 'identifier'
			? 'count_distinct'
			: field.semantic_type === 'number' || field.semantic_type === 'money'
				? 'sum'
				: 'count_distinct');
	const numericSemanticTypes = new Set([
		'number',
		'money',
		'currency',
		'integer',
		'decimal',
		'float',
		'percentage'
	]);
	const numericPhysicalTypes = new Set([
		'bigint',
		'bigserial',
		'dec',
		'decimal',
		'double',
		'double precision',
		'fixed',
		'float',
		'float4',
		'float8',
		'int',
		'int2',
		'int4',
		'int8',
		'integer',
		'mediumint',
		'money',
		'numeric',
		'number',
		'real',
		'serial',
		'smallint',
		'smallmoney',
		'smallserial',
		'tinyint'
	]);
	const isNumericField = (field: CatalogField) => {
		if (numericSemanticTypes.has((field.semantic_type || '').toLowerCase())) return true;
		const baseType = (field.physical_type || '').toLowerCase().split('(', 1)[0].trim();
		return numericPhysicalTypes.has(baseType);
	};
	const aggregationOptions = (field: CatalogField) => [
		...(isNumericField(field)
			? [
					{ value: 'sum', label: '總和' },
					{ value: 'avg', label: '平均' }
				]
			: []),
		{ value: 'count', label: '筆數' },
		{ value: 'count_distinct', label: '不重複筆數' },
		{ value: 'min', label: '最小值' },
		{ value: 'max', label: '最大值' }
	];
	const measureAggregation = (field: CatalogField) => {
		const selected =
			(datasetDefinition.measures ?? []).find((item) => item.fieldId === field.id)?.aggregation ??
			defaultAggregation(field);
		const options = aggregationOptions(field);
		return options.some((option) => option.value === selected) ? selected : options[0].value;
	};
	const setMeasureAggregation = (
		object: CatalogObject,
		field: CatalogField,
		aggregation: string
	) => {
		const measures = [...(datasetDefinition.measures ?? [])];
		const existingIndex = measures.findIndex((item) => item.fieldId === field.id);
		const existing = existingIndex >= 0 ? measures[existingIndex] : null;
		const next = {
			...(existing ?? {}),
			id: existing?.id ?? semanticId(object, field),
			name: existing?.name ?? fieldLabel(field),
			fieldId: field.id,
			aggregation: aggregation as 'sum' | 'count' | 'count_distinct' | 'avg' | 'min' | 'max'
		};
		if (existingIndex >= 0) measures[existingIndex] = next;
		else measures.push(next);
		datasetDefinition = { ...datasetDefinition, measures };
	};
	const resetDatasetImportResult = () => {
		datasetImportErrors = [];
		datasetImportWarnings = [];
		datasetImportStatus = '';
		datasetImportCandidate = null;
		datasetImportDocument = null;
		datasetImportServerReady = false;
		permissionReviewChanges = [];
		permissionReviewIndex = 0;
		permissionReviewOpen = false;
		permissionReviewBusy = false;
		permissionReviewError = '';
		permissionReviewDecisions = {};
	};
	const readDatasetImportFile = async (event: Event) => {
		const input = event.currentTarget as HTMLInputElement;
		const file = input.files?.[0];
		input.value = '';
		if (!file) return;
		if (file.size > 500_000) {
			resetDatasetImportResult();
			datasetImportErrors = [
				{ code: 'IMPORT-FILE-TOO-LARGE', path: 'file', message: 'JSON 檔案不可超過 500 KB。' }
			];
			return;
		}
		datasetImportJson = await file.text();
		resetDatasetImportResult();
		datasetImportStatus = `已載入 ${file.name}，請先檢查 JSON。`;
	};
	const downloadJson = (document: Record<string, unknown>, filename: string) => {
		const blob = new Blob([JSON.stringify(document, null, 2)], {
			type: 'application/json'
		});
		const url = URL.createObjectURL(blob);
		const link = document.createElement('a');
		link.href = url;
		link.download = filename;
		link.click();
		URL.revokeObjectURL(url);
	};
	const downloadDatasetImportTemplate = () =>
		downloadJson(portableDatasetTemplate, 'semantic-dataset-template.json');
	const downloadAiSchemaHandoff = async () => {
		datasetHandoffBusy = true;
		try {
			const response = await getSemanticAiSchemaHandoff(localStorage.token, connectorId);
			const safeName = (connector?.name || 'connector')
				.toLowerCase()
				.replace(/[^a-z0-9_-]+/g, '-')
				.replace(/^-+|-+$/g, '');
			downloadJson(response.document, `${safeName || 'connector'}-ai-schema-handoff.json`);
			toast.success('AI Schema 交接包已匯出；請自行交給選定的 AI。');
		} catch (error) {
			toast.error(`無法匯出 AI Schema 交接包：${error}`);
		} finally {
			datasetHandoffBusy = false;
		}
	};
	const resolveDatasetImportDocument = async (
		document: Record<string, unknown>,
		openPermissionReview: boolean
	) => {
		const response = await resolveSemanticDatasetImport(localStorage.token, connectorId, document);
		const result = response.import;
		datasetImportDocument = document;
		datasetImportErrors = result.errors;
		datasetImportWarnings = result.warnings;
		datasetImportCandidate = result.errors.length === 0 ? result.dataset : null;
		datasetImportServerReady = result.ok;
		permissionReviewChanges = (result.permissionChanges ?? []).filter((change) =>
			['pending', 'conflict'].includes(change.status)
		);
		const nextReviewIndex = permissionReviewChanges.findIndex(
			(change) => !permissionReviewDecisions[change.id]
		);
		permissionReviewIndex = Math.max(0, nextReviewIndex);
		permissionReviewOpen = openPermissionReview && nextReviewIndex >= 0;
		permissionReviewError = '';
		if (result.errors.length) {
			datasetImportStatus = 'JSON 有結構或關聯錯誤，請依下方訊息修正。';
		} else if (nextReviewIndex >= 0) {
			datasetImportStatus = `語意模型解析完成；請逐項審核 ${permissionReviewChanges.length} 項權限建議。`;
		} else if (!result.ok) {
			datasetImportStatus = '仍有必要權限未獲同意，暫時無法套用這份語意資料集。';
		} else if (result.summary) {
			datasetImportStatus = `解析完成：${result.summary.dimensions} 個維度、${result.summary.measures} 個指標、${result.summary.metrics} 個衍生指標、${result.summary.relationships} 條關聯。`;
		}
	};
	const inspectDatasetImport = async () => {
		resetDatasetImportResult();
		let document: unknown;
		try {
			document = JSON.parse(datasetImportJson);
		} catch (error) {
			datasetImportErrors = [
				{ code: 'IMPORT-JSON-INVALID', path: 'document', message: `JSON 格式錯誤：${error}` }
			];
			return;
		}
		if (!document || typeof document !== 'object' || Array.isArray(document)) {
			datasetImportErrors = [
				{ code: 'IMPORT-JSON-INVALID', path: 'document', message: 'JSON 最外層必須是物件。' }
			];
			return;
		}
		datasetImportBusy = true;
		try {
			await resolveDatasetImportDocument(document as Record<string, unknown>, true);
		} catch (error) {
			datasetImportErrors = [
				{ code: 'IMPORT-REQUEST-FAILED', path: 'request', message: `${error}` }
			];
		} finally {
			datasetImportBusy = false;
		}
	};
	const finishPermissionReviewItem = async (
		change: SemanticPermissionChange,
		decision: 'accepted' | 'skipped'
	) => {
		const decisions = { ...permissionReviewDecisions, [change.id]: decision };
		permissionReviewDecisions = decisions;
		const nextIndex = permissionReviewChanges.findIndex((item) => !decisions[item.id]);
		if (nextIndex >= 0) {
			permissionReviewIndex = nextIndex;
			permissionReviewError = '';
			return;
		}
		permissionReviewOpen = false;
		try {
			catalog = (await getSemanticCatalog(localStorage.token, connectorId)).catalog;
			if (datasetImportDocument) {
				datasetImportBusy = true;
				await resolveDatasetImportDocument(datasetImportDocument, true);
			}
		} catch (error) {
			datasetImportErrors = [
				{
					code: 'IMPORT-REVALIDATION-FAILED',
					path: 'permissions',
					message: `權限決定已保留，但重新驗證失敗：${error}`
				}
			];
			datasetImportStatus = '權限決定已保留，請再次檢查 JSON 以重新驗證。';
		} finally {
			datasetImportBusy = false;
		}
	};
	const acceptPermissionChange = async (change: SemanticPermissionChange) => {
		if (change.status === 'conflict') return;
		permissionReviewBusy = true;
		permissionReviewError = '';
		try {
			if (!catalog.snapshotId) throw new Error('目前沒有可用的 Schema snapshot。');
			const result = await applySemanticCatalogPermissionChange(localStorage.token, connectorId, {
				snapshot_id: catalog.snapshotId,
				target_type: change.targetType,
				object_id: change.objectId,
				field_id: change.fieldId,
				permission: change.permission,
				desired: change.desired
			});
			if (result.affectedDatasets > 0) {
				toast.warning(`${result.affectedDatasets} 個使用此權限的已發布資料集已標記為 blocked。`);
			}
			await finishPermissionReviewItem(change, 'accepted');
		} catch (error) {
			permissionReviewError = `權限變更失敗：${error}`;
		} finally {
			permissionReviewBusy = false;
		}
	};
	const skipPermissionChange = async (change: SemanticPermissionChange) => {
		permissionReviewBusy = true;
		try {
			await finishPermissionReviewItem(change, 'skipped');
		} catch (error) {
			permissionReviewError = `重新驗證失敗：${error}`;
		} finally {
			permissionReviewBusy = false;
		}
	};
	const closePermissionReview = async () => {
		permissionReviewOpen = false;
		try {
			catalog = (await getSemanticCatalog(localStorage.token, connectorId)).catalog;
		} catch (error) {
			toast.error(`無法重新載入權限狀態：${error}`);
		}
	};
	const applyDatasetImport = () => {
		const payload = datasetImportCandidate;
		if (!payload || !datasetImportCanApply || !confirmDiscardDatasetChanges()) return;
		editingDatasetId = '';
		datasetName = payload.name;
		datasetSlug = payload.slug;
		datasetDescription = payload.description;
		datasetDomain = payload.business_domain ?? '';
		datasetAccessMode = payload.access_mode;
		datasetDefinition = structuredClone(payload.definition);
		datasetWhenToUse = payload.definition.whenToUse ?? '';
		datasetNotFor = (payload.definition.notFor ?? []).join(', ');
		datasetExamples = (payload.definition.examples ?? []).join('\n');
		datasetSynonyms = (payload.definition.synonyms ?? []).join(', ');
		rootObjectId = payload.definition.rootObjectId ?? '';
		dimensionFieldIds = payload.definition.dimensions?.map((item) => item.fieldId) ?? [];
		measureFieldIds = payload.definition.measures?.map((item) => item.fieldId) ?? [];
		relationshipIds = payload.definition.relationshipIds ?? [];
		allowedMemberIds = payload.allowed_member_ids.join(', ');
		allowedGroupIds = payload.allowed_group_ids.join(', ');
		allowedModelIds = payload.allowed_model_ids.join(', ');
		allowedChannelIds = payload.allowed_channel_ids.join(', ');
		allowedWorkflowIds = payload.allowed_workflow_ids.join(', ');
		datasetVersions = [];
		datasetFormInitialized = true;
		savedDatasetSignature = '__imported_new_draft__';
		datasetImportOpen = false;
		toast.success('JSON 已套用為新的資料集草稿，尚未儲存或發布。');
	};

	const load = async () => {
		loading = true;
		try {
			const [connectorResponse, snapshotResponse, catalogResponse, datasetResponse, eventResponse] =
				await Promise.all([
					getInteractDataConnectors(localStorage.token),
					getSchemaSnapshots(localStorage.token, connectorId),
					getSemanticCatalog(localStorage.token, connectorId),
					getSemanticDatasets(localStorage.token),
					getSemanticQueryEvents(localStorage.token, 100)
				]);
			connector = connectorResponse.connectors.find((item) => item.id === connectorId) ?? null;
			if (!connector) throw '找不到這個企業資料連線';
			snapshots = snapshotResponse.snapshots;
			catalog = catalogResponse.catalog;
			datasets = datasetResponse.datasets;
			const loadedConnectorDatasets = datasetResponse.datasets.filter(
				(item) => item.connector_id === connectorId
			);
			const loadedDatasetIds = new Set(loadedConnectorDatasets.map((item) => item.id));
			events = eventResponse.events.filter((item) =>
				Boolean(item.dataset_id && loadedDatasetIds.has(item.dataset_id))
			);
			if (!rootObjectId) rootObjectId = enabledObjects[0]?.id ?? '';
			if (!selectedPolicyDatasetId) selectedPolicyDatasetId = loadedConnectorDatasets[0]?.id ?? '';
			if (!labDatasetId)
				labDatasetId =
					loadedConnectorDatasets.find((item) => ['published', 'degraded'].includes(item.status))
						?.id ?? '';
			await loadPolicies();
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			loading = false;
		}
	};

	const scan = async () => {
		busy = true;
		operationStatus = '正在讀取資料庫結構...';
		try {
			const result = await createSchemaSnapshot(localStorage.token, connectorId);
			operationStatus = result.created
				? `已建立 Schema v${result.snapshot.version}`
				: '結構未變更，沿用現有版本';
			toast.success(operationStatus);
			await load();
		} catch (error) {
			operationStatus = `掃描失敗：${error}`;
			toast.error(operationStatus);
		} finally {
			busy = false;
		}
	};

	const updateObject = async (object: CatalogObject, patch: Partial<CatalogObject>) => {
		if (catalogBusy) return;
		catalogBusy = true;
		catalogStatus = `正在更新「${object.display_name}」...`;
		try {
			if (!catalog.snapshotId || typeof patch.enabled !== 'boolean') {
				throw new Error('目前沒有可用的 Schema snapshot。');
			}
			const result = await applySemanticCatalogPermissionChange(localStorage.token, connectorId, {
				snapshot_id: catalog.snapshotId,
				target_type: 'object',
				object_id: object.id,
				permission: 'enabled',
				desired: patch.enabled
			});
			catalog = (await getSemanticCatalog(localStorage.token, connectorId)).catalog;
			catalogStatus = `「${object.display_name}」已${patch.enabled ? '啟用' : '停用'}。`;
			if (result.affectedDatasets > 0) {
				catalogStatus += ` ${result.affectedDatasets} 個已發布資料集已標記為 blocked。`;
			}
			toast.success(catalogStatus);
		} catch (error) {
			catalogStatus = `無法更新「${object.display_name}」：${error}`;
			toast.error(catalogStatus);
		} finally {
			catalogBusy = false;
		}
	};

	const updateAllObjects = async (enabled: boolean) => {
		if (catalogBusy || !catalog.snapshotId || !catalog.objects.length) return;
		if (!enabled && !confirm('取消全選會停用目前所有資料表，但不會刪除欄位設定。要繼續嗎？')) {
			return;
		}
		catalogBusy = true;
		catalogStatus = enabled ? '正在啟用全部資料表...' : '正在停用全部資料表...';
		try {
			const result = await patchCatalogObjects(
				localStorage.token,
				connectorId,
				catalog.snapshotId,
				catalog.objects.map((item) => item.id),
				enabled
			);
			catalog = (await getSemanticCatalog(localStorage.token, connectorId)).catalog;
			catalogStatus = enabled
				? `已啟用 ${result.updated_count} 張資料表。欄位權限與遮罩設定維持不變。`
				: `已停用 ${result.updated_count} 張資料表；${result.affectedDatasets ?? 0} 個已發布資料集已標記為 blocked。`;
			toast.success(catalogStatus);
		} catch (error) {
			catalogStatus = `批次更新失敗：${error}`;
			toast.error(catalogStatus);
		} finally {
			catalogBusy = false;
		}
	};

	const openBulkAuthorization = async (
		authorized: boolean,
		objectIds: string[] = [],
		scopeLabel = '全部資料表'
	) => {
		if (!catalog.snapshotId || bulkAuthorizationBusy || catalogBusy) return;
		bulkAuthorizationBusy = true;
		bulkAuthorizationError = '';
		try {
			bulkAuthorizationPreview = await bulkSemanticCatalogAuthorization(
				localStorage.token,
				connectorId,
				{
					snapshot_id: catalog.snapshotId,
					object_ids: objectIds,
					authorized,
					apply: false
				}
			);
			bulkAuthorizationAuthorized = authorized;
			bulkAuthorizationObjectIds = objectIds;
			bulkAuthorizationScopeLabel = scopeLabel;
		} catch (error) {
			toast.error(`無法預覽批次權限變更：${error}`);
		} finally {
			bulkAuthorizationBusy = false;
		}
	};

	const closeBulkAuthorization = () => {
		if (bulkAuthorizationBusy) return;
		bulkAuthorizationPreview = null;
		bulkAuthorizationError = '';
	};

	const applyBulkAuthorization = async () => {
		if (!catalog.snapshotId || !bulkAuthorizationPreview) return;
		bulkAuthorizationBusy = true;
		bulkAuthorizationError = '';
		try {
			const result = await bulkSemanticCatalogAuthorization(localStorage.token, connectorId, {
				snapshot_id: catalog.snapshotId,
				object_ids: bulkAuthorizationObjectIds,
				authorized: bulkAuthorizationAuthorized,
				apply: true,
				acknowledge_impact: !bulkAuthorizationAuthorized
			});
			const [catalogResponse, datasetResponse] = await Promise.all([
				getSemanticCatalog(localStorage.token, connectorId),
				getSemanticDatasets(localStorage.token)
			]);
			catalog = catalogResponse.catalog;
			datasets = datasetResponse.datasets;
			catalogStatus = bulkAuthorizationAuthorized
				? `已完整授權 ${result.object_count} 張資料表、${result.field_count} 個欄位。`
				: `已解除 ${result.object_count} 張資料表、${result.field_count} 個欄位的權限；${result.affected_datasets.length} 個已發布資料集已標記為 blocked。`;
			bulkAuthorizationPreview = null;
			toast.success(catalogStatus);
		} catch (error) {
			bulkAuthorizationError = `批次權限變更失敗：${error}`;
		} finally {
			bulkAuthorizationBusy = false;
		}
	};

	const updateField = async (
		object: CatalogObject,
		field: CatalogField,
		key: keyof CatalogField,
		value: unknown
	) => {
		busy = true;
		try {
			const permissionKeys = new Set(['readable', 'filterable', 'groupable', 'aggregatable']);
			if (permissionKeys.has(String(key))) {
				if (!catalog.snapshotId || typeof value !== 'boolean') {
					throw new Error('目前沒有可用的 Schema snapshot。');
				}
				const result = await applySemanticCatalogPermissionChange(localStorage.token, connectorId, {
					snapshot_id: catalog.snapshotId,
					target_type: 'field',
					object_id: object.id,
					field_id: field.id,
					permission: String(key) as 'readable' | 'filterable' | 'groupable' | 'aggregatable',
					desired: value
				});
				if (result.affectedDatasets > 0) {
					toast.warning(`${result.affectedDatasets} 個已發布資料集已標記為 blocked。`);
				}
			} else {
				await patchCatalogField(localStorage.token, field.id, { [key]: value });
			}
			catalog = (await getSemanticCatalog(localStorage.token, connectorId)).catalog;
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			busy = false;
		}
	};

	const confirmRelationship = async (relationshipId: string) => {
		const relationship = catalog.relationships.find((item) => item.id === relationshipId);
		if (!relationship) return;
		busy = true;
		try {
			await saveCatalogRelationship(localStorage.token, connectorId, {
				...relationship,
				status: 'confirmed'
			});
			catalog = (await getSemanticCatalog(localStorage.token, connectorId)).catalog;
			toast.success('關聯已確認，可供已發布資料集使用');
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			busy = false;
		}
	};

	const resetDatasetForm = () => {
		editingDatasetId = '';
		datasetName = '';
		datasetSlug = '';
		datasetDescription = '';
		datasetDomain = '';
		datasetWhenToUse = '';
		datasetNotFor = '';
		datasetExamples = '';
		datasetSynonyms = '';
		datasetAccessMode = 'company_admins';
		rootObjectId = enabledObjects[0]?.id ?? '';
		dimensionFieldIds = [];
		measureFieldIds = [];
		relationshipIds = [];
		datasetDefinition = {};
		allowedMemberIds =
			allowedGroupIds =
			allowedModelIds =
			allowedChannelIds =
			allowedWorkflowIds =
				'';
		datasetVersions = [];
		datasetFormInitialized = true;
		savedDatasetSignature = datasetFormSignature();
	};

	const loadDatasetVersions = async (datasetId: string) => {
		try {
			datasetVersions = (await getSemanticDatasetVersions(localStorage.token, datasetId)).versions;
		} catch (error) {
			datasetVersions = [];
			toast.error(`無法載入資料集版本：${error}`);
		}
	};

	const editDataset = (dataset: SemanticDataset) => {
		const definition = dataset.draft_definition as SemanticDatasetDefinition;
		editingDatasetId = dataset.id;
		datasetName = dataset.name;
		datasetSlug = dataset.slug;
		datasetDescription = dataset.description;
		datasetDomain = dataset.business_domain ?? '';
		datasetWhenToUse = definition.whenToUse ?? '';
		datasetNotFor = (definition.notFor ?? []).join(', ');
		datasetExamples = (definition.examples ?? []).join('\n');
		datasetSynonyms = (definition.synonyms ?? []).join(', ');
		datasetAccessMode = dataset.access_mode;
		datasetDefinition = structuredClone(definition);
		rootObjectId = definition.rootObjectId ?? '';
		dimensionFieldIds = definition.dimensions?.map((item) => item.fieldId) ?? [];
		measureFieldIds = definition.measures?.map((item) => item.fieldId) ?? [];
		relationshipIds = definition.relationshipIds ?? [];
		allowedMemberIds = dataset.allowed_member_ids.join(', ');
		allowedGroupIds = dataset.allowed_group_ids.join(', ');
		allowedModelIds = dataset.allowed_model_ids.join(', ');
		allowedChannelIds = dataset.allowed_channel_ids.join(', ');
		allowedWorkflowIds = dataset.allowed_workflow_ids.join(', ');
		activeTab = 'datasets';
		datasetFormInitialized = true;
		savedDatasetSignature = datasetFormSignature();
		void loadDatasetVersions(dataset.id);
	};

	const openDataset = (dataset: SemanticDataset) => {
		if (editingDatasetId !== dataset.id && !confirmDiscardDatasetChanges()) return;
		editDataset(dataset);
	};

	const selectTab = (tab: Tab) => {
		if (activeTab === 'datasets' && tab !== 'datasets' && !confirmDiscardDatasetChanges()) return;
		activeTab = tab;
	};

	const activateDatasetVersion = async (version: SemanticDatasetVersion) => {
		if (!editingDatasetId) return;
		if (!confirm(`確定將目前發布版本切換為 v${version.version}？現有執行中的查詢不受影響。`))
			return;
		busy = true;
		try {
			const result = await activateSemanticDatasetVersion(
				localStorage.token,
				editingDatasetId,
				version.id
			);
			toast.success(`已切換至 v${version.version}`);
			await load();
			editDataset(result.dataset);
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			busy = false;
		}
	};

	const semanticId = (object: CatalogObject, field: CatalogField) =>
		`${object.physical_name.split('.').at(-1)}.${field.physical_name}`
			.toLowerCase()
			.replace(/[^a-z0-9_.-]+/g, '_');

	const saveDataset = async () => {
		if (!datasetName.trim() || !datasetSlug.trim() || !rootObjectId) {
			toast.error('請填寫名稱、識別碼並選擇主要資料表');
			return;
		}
		const existingDimensions = new Map(
			(datasetDefinition.dimensions ?? []).map((item) => [item.fieldId, item])
		);
		const existingMeasures = new Map(
			(datasetDefinition.measures ?? []).map((item) => [item.fieldId, item])
		);
		const dimensions = catalog.objects.flatMap((object) =>
			object.fields
				.filter((field) => dimensionFieldIds.includes(field.id))
				.map((field) => {
					const existing = existingDimensions.get(field.id);
					return {
						...(existing ?? {}),
						id: existing?.id ?? semanticId(object, field),
						name: existing?.name ?? fieldLabel(field),
						fieldId: field.id
					};
				})
		);
		const measures = catalog.objects.flatMap((object) =>
			object.fields
				.filter((field) => measureFieldIds.includes(field.id))
				.map((field) => {
					const existing = existingMeasures.get(field.id);
					return {
						...(existing ?? {}),
						id: existing?.id ?? semanticId(object, field),
						name: existing?.name ?? fieldLabel(field),
						fieldId: field.id,
						aggregation: measureAggregation(field)
					};
				})
		);
		const preservedNotFor = datasetDefinition.notFor ?? [];
		const preservedSynonyms = datasetDefinition.synonyms ?? [];
		busy = true;
		try {
			const result = await saveSemanticDataset(
				localStorage.token,
				{
					connector_id: connectorId,
					slug: datasetSlug.trim(),
					name: datasetName.trim(),
					description: datasetDescription.trim(),
					business_domain: datasetDomain.trim() || null,
					access_mode: datasetAccessMode,
					allowed_member_ids: splitIds(allowedMemberIds),
					allowed_group_ids: splitIds(allowedGroupIds),
					allowed_model_ids: splitIds(allowedModelIds),
					allowed_channel_ids: splitIds(allowedChannelIds),
					allowed_workflow_ids: splitIds(allowedWorkflowIds),
					definition: {
						...datasetDefinition,
						snapshotId: catalog.snapshotId,
						rootObjectId,
						whenToUse: datasetWhenToUse.trim(),
						notFor:
							datasetNotFor === preservedNotFor.join(', ')
								? preservedNotFor
								: splitIds(datasetNotFor),
						examples: datasetExamples
							.split('\n')
							.map((item) => item.trim())
							.filter(Boolean),
						synonyms:
							datasetSynonyms === preservedSynonyms.join(', ')
								? preservedSynonyms
								: splitIds(datasetSynonyms),
						relationshipIds,
						dimensions,
						measures,
						metrics: datasetDefinition.metrics ?? []
					}
				},
				editingDatasetId || undefined
			);
			toast.success('草稿已儲存');
			await load();
			editDataset(result.dataset);
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			busy = false;
		}
	};

	const publishDataset = async (dataset: SemanticDataset) => {
		busy = true;
		try {
			const validation = await validateSemanticDataset(localStorage.token, dataset.id);
			if (!validation.validation.ok) {
				toast.error(`發布前檢查未通過：${JSON.stringify(validation.validation.errors)}`);
				return;
			}
			await publishSemanticDataset(localStorage.token, dataset.id);
			toast.success('資料集已發布，Agent 與工作流現在可依 ACL 使用');
			await load();
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			busy = false;
		}
	};

	const removeDataset = async (dataset: SemanticDataset) => {
		if (!confirm(`確定刪除「${dataset.name}」及其版本與資料列權限？`)) return;
		try {
			await deleteSemanticDataset(localStorage.token, dataset.id);
			resetDatasetForm();
			await load();
			toast.success('資料集已刪除');
		} catch (error) {
			toast.error(`${error}`);
		}
	};

	const loadPolicies = async () => {
		if (!selectedPolicyDatasetId) {
			policies = [];
			return;
		}
		try {
			policies = (await getRowPolicies(localStorage.token, selectedPolicyDatasetId)).policies;
		} catch (error) {
			toast.error(`${error}`);
		}
	};

	const createPolicy = async () => {
		if (!selectedPolicyDatasetId || !policyName.trim() || !policyFieldId) {
			toast.error('請選擇資料集、欄位並填寫權限名稱');
			return;
		}
		busy = true;
		try {
			await saveRowPolicy(localStorage.token, selectedPolicyDatasetId, {
				name: policyName.trim(),
				status: policyPublished ? 'active' : 'draft',
				principal_type: policyPrincipalType,
				principal_ids: splitIds(policyPrincipalIds),
				expression: {
					operator: 'and',
					conditions: [
						{
							fieldId: policyFieldId,
							operator: policyOperator,
							value: policyOperator === 'in' ? splitIds(policyValue) : policyValue
						}
					]
				},
				deny_if_unresolved: true
			});
			policyName = '';
			await loadPolicies();
			toast.success('資料列權限已儲存');
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			busy = false;
		}
	};

	const runLab = async (mode: 'validate' | 'execute') => {
		if (!labDatasetId) {
			toast.error('請選擇已發布資料集');
			return;
		}
		if (labEditorMode === 'form' && !labDimensions.length && !labMeasures.length) {
			toast.error('請選擇已發布資料集及至少一個維度或指標');
			return;
		}
		labMode = mode;
		labResult = null;
		try {
			const formPlan = {
				version: '1',
				datasetId: labDatasetId,
				dimensions: labDimensions,
				measures: labMeasures,
				metrics: [],
				orderBy: [],
				limit: labLimit
			};
			const plan = labEditorMode === 'json' ? JSON.parse(labPlanJson) : formPlan;
			if (!plan || typeof plan !== 'object' || Array.isArray(plan))
				throw 'Query Plan 必須是 JSON 物件';
			labResult = await testSemanticDatasetQuery(
				localStorage.token,
				labDatasetId,
				plan,
				mode === 'validate'
			);
			toast.success(mode === 'validate' ? '查詢計畫安全檢查通過' : '查詢完成');
			if (mode === 'execute')
				events = (await getSemanticQueryEvents(localStorage.token, 100)).events;
		} catch (error) {
			labResult = { ok: false, error: `${error}` };
			toast.error(`${error}`);
		} finally {
			labMode = '';
		}
	};

	const switchLabEditor = (mode: 'form' | 'json') => {
		labEditorMode = mode;
		labResult = null;
		if (mode === 'json') {
			labPlanJson = JSON.stringify(
				{
					version: '1',
					datasetId: labDatasetId,
					dimensions: labDimensions,
					measures: labMeasures,
					metrics: [],
					orderBy: [],
					limit: labLimit
				},
				null,
				2
			);
		}
	};

	beforeNavigate(({ cancel }) => {
		if (!confirmDiscardDatasetChanges()) cancel();
	});

	onMount(() => {
		returnTo = safeReturnUrl($page.url.searchParams.get('interact_return_to'));
		void load();
	});
</script>

<svelte:head><title>資料模型控制台 - Interact Web Ai</title></svelte:head>

<div class="mx-auto flex w-full max-w-7xl flex-col gap-5 px-4 py-6 md:px-8">
	<header class="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
		<div class="min-w-0">
			<button
				type="button"
				class="mb-2 text-sm text-gray-500 hover:text-gray-900 dark:hover:text-white"
				on:click={() => goto('/workspace/data-connectors')}>← 返回企業資料連線</button
			>
			<h1 class="truncate text-2xl font-semibold text-gray-900 dark:text-gray-100">
				{connector?.name ?? '資料模型控制台'}
			</h1>
			<p class="mt-1 text-sm text-gray-500">
				建立可由 Agent、工作流及通訊渠道安全共用的企業語意資料集。
			</p>
		</div>
		<div class="flex items-center gap-2">
			{#if returnTo}
				<a
					href={returnTo}
					class="inline-flex h-9 items-center rounded-lg border border-gray-200 px-3 text-sm hover:bg-gray-50 dark:border-gray-700 dark:hover:bg-gray-850"
					>返回企業控制台</a
				>
			{/if}
			<span
				class="rounded-full px-2.5 py-1 text-xs font-medium {connector?.enabled
					? 'bg-emerald-50 text-emerald-700'
					: 'bg-red-50 text-red-700'}">{connector?.enabled ? '連接器啟用' : '連接器停用'}</span
			>
			<button
				type="button"
				class="h-9 rounded-lg border border-gray-200 px-3 text-sm hover:bg-gray-50 dark:border-gray-700 dark:hover:bg-gray-850"
				disabled={busy}
				on:click={load}>重新整理</button
			>
		</div>
	</header>

	<nav
		class="overflow-x-auto border-b border-gray-200 dark:border-gray-800"
		aria-label="資料模型管理區"
	>
		<div class="flex min-w-max gap-1">
			{#each tabs as tab}
				<button
					type="button"
					class="border-b-2 px-3 py-2.5 text-sm font-medium {activeTab === tab.id
						? 'border-sky-600 text-sky-700 dark:text-sky-300'
						: 'border-transparent text-gray-500 hover:text-gray-900 dark:hover:text-white'}"
					aria-current={activeTab === tab.id ? 'page' : undefined}
					on:click={() => selectTab(tab.id)}>{tab.label}</button
				>
			{/each}
		</div>
	</nav>

	{#if loading}
		<div class="py-16 text-center text-sm text-gray-500" role="status">正在載入資料模型...</div>
	{:else if !connector}
		<div class="border border-red-200 bg-red-50 p-4 text-sm text-red-800">
			找不到資料連線，可能已被刪除或不屬於目前企業。
		</div>
	{:else if activeTab === 'overview'}
		<section class="space-y-5">
			<div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
				<div class="rounded-lg border border-gray-200 p-4 dark:border-gray-800">
					<p class="text-xs text-gray-500">Schema 版本</p>
					<p class="mt-2 text-2xl font-semibold">{snapshots[0]?.version ?? 0}</p>
					<p class="mt-1 text-xs text-gray-500">
						{snapshots[0] ? time(snapshots[0].completed_at) : '尚未掃描'}
					</p>
				</div>
				<div class="rounded-lg border border-gray-200 p-4 dark:border-gray-800">
					<p class="text-xs text-gray-500">已啟用資料表</p>
					<p class="mt-2 text-2xl font-semibold">{enabledObjects.length}</p>
					<p class="mt-1 text-xs text-gray-500">共 {catalog.objects.length} 個物件</p>
				</div>
				<div class="rounded-lg border border-gray-200 p-4 dark:border-gray-800">
					<p class="text-xs text-gray-500">已確認關聯</p>
					<p class="mt-2 text-2xl font-semibold">
						{catalog.relationships.filter((item) => item.status === 'confirmed').length}
					</p>
					<p class="mt-1 text-xs text-gray-500">未確認不會參與查詢</p>
				</div>
				<div class="rounded-lg border border-gray-200 p-4 dark:border-gray-800">
					<p class="text-xs text-gray-500">可執行資料集</p>
					<p class="mt-2 text-2xl font-semibold">
						{connectorDatasets.filter((item) => ['published', 'degraded'].includes(item.status))
							.length}
					</p>
					<p class="mt-1 text-xs text-gray-500">共 {connectorDatasets.length} 個草稿與版本</p>
				</div>
			</div>
			<div class="rounded-lg border border-gray-200 p-5 dark:border-gray-800">
				<div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
					<div>
						<h2 class="font-semibold">結構同步</h2>
						<p class="mt-1 text-sm text-gray-500">
							掃描只讀取 metadata；若結構未變更，不會建立重複版本。
						</p>
					</div>
					<button
						type="button"
						class="h-10 rounded-lg bg-gray-900 px-4 text-sm font-medium text-white disabled:opacity-60 dark:bg-white dark:text-gray-900"
						disabled={busy}
						on:click={scan}>{busy ? '掃描中...' : '立即掃描 Schema'}</button
					>
				</div>
				{#if operationStatus}<p class="mt-3 text-sm" aria-live="polite">{operationStatus}</p>{/if}
				{#if snapshots.length}<div class="mt-4 overflow-x-auto">
						<table class="w-full text-left text-sm">
							<thead class="text-xs text-gray-500"
								><tr><th class="py-2">版本</th><th>狀態</th><th>完成時間</th><th>指紋</th></tr
								></thead
							><tbody
								>{#each snapshots.slice(0, 8) as snapshot}<tr
										class="border-t border-gray-100 dark:border-gray-800"
										><td class="py-2">v{snapshot.version}</td><td>{snapshot.status}</td><td
											>{time(snapshot.completed_at)}</td
										><td class="font-mono text-xs">{snapshot.fingerprint.slice(0, 12)}</td></tr
									>{/each}</tbody
							>
						</table>
					</div>{/if}
			</div>
		</section>
	{:else if activeTab === 'catalog'}
		<section class="space-y-3">
			<div class="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
				<div>
					<h2 class="text-lg font-semibold">資料目錄</h2>
					<p class="text-sm text-gray-500">
						預設全部關閉。只有明確啟用且標記可讀的欄位會進入資料集。
					</p>
				</div>
				<div class="flex flex-wrap items-center gap-2">
					<span class="text-xs text-gray-500">
						已啟用 {enabledObjects.length} / {catalog.objects.length} · Snapshot {catalog.snapshotId
							? (snapshots.find((item) => item.id === catalog.snapshotId)?.version ?? '-')
							: '-'}
					</span>
					<button
						type="button"
						class="h-9 rounded-lg bg-sky-600 px-3 text-sm font-medium text-white hover:bg-sky-700 disabled:cursor-not-allowed disabled:opacity-50"
						disabled={busy ||
							catalogBusy ||
							bulkAuthorizationBusy ||
							!catalog.snapshotId ||
							!catalog.objects.length}
						on:click={() => openBulkAuthorization(true)}
					>
						完整授權全部
					</button>
					<button
						type="button"
						class="h-9 rounded-lg border border-red-300 px-3 text-sm font-medium text-red-700 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-red-900 dark:text-red-300 dark:hover:bg-red-950/30"
						disabled={busy ||
							catalogBusy ||
							bulkAuthorizationBusy ||
							!catalog.snapshotId ||
							!catalog.objects.length}
						on:click={() => openBulkAuthorization(false)}
					>
						解除全部授權
					</button>
					<button
						type="button"
						class="h-9 rounded-lg border border-gray-200 px-3 text-sm font-medium hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-700 dark:hover:bg-gray-900"
						disabled={busy || catalogBusy || !catalog.snapshotId || !catalog.objects.length}
						on:click={() => updateAllObjects(!allObjectsEnabled)}
					>
						{catalogBusy ? '更新中...' : allObjectsEnabled ? '僅停用資料表' : '僅啟用資料表'}
					</button>
				</div>
			</div>
			<p class="text-xs text-gray-500">
				「完整授權」會開啟資料表與四種欄位權限；「僅啟用資料表」會保留既有欄位與遮罩設定。
			</p>
			{#if catalogStatus}
				<p
					class="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm dark:border-gray-800 dark:bg-gray-900"
					aria-live="polite"
				>
					{catalogStatus}
				</p>
			{/if}
			{#if !catalog.snapshotId}<div
					class="border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900"
				>
					尚未建立 Schema snapshot。請先在概覽執行掃描。
				</div>{/if}
			{#each catalog.objects as object}
				<div class="rounded-lg border border-gray-200 dark:border-gray-800">
					<div class="flex items-center gap-3 p-3">
						<input
							type="checkbox"
							class="h-4 w-4 shrink-0 cursor-pointer disabled:cursor-not-allowed"
							checked={object.enabled}
							disabled={busy || catalogBusy}
							aria-label={`啟用 ${object.display_name}`}
							on:change={(event) => updateObject(object, { enabled: event.currentTarget.checked })}
						/><button
							type="button"
							class="min-w-0 flex-1 text-left"
							on:click={() => (expandedObjectId = expandedObjectId === object.id ? '' : object.id)}
							><span class="font-medium">{object.display_name}</span><span
								class="ml-2 font-mono text-xs text-gray-500">{object.physical_name}</span
							>
							<p class="mt-0.5 text-xs text-gray-500">
								{object.fields.length} 欄位 · {object.object_type}
							</p></button
						><span class="text-xs {object.enabled ? 'text-emerald-600' : 'text-gray-400'}"
							>{object.enabled ? '可建模' : '未啟用'}</span
						>
					</div>
					{#if expandedObjectId === object.id}<div
							class="overflow-x-auto border-t border-gray-100 dark:border-gray-800"
						>
							<div
								class="flex min-w-[760px] items-center justify-between gap-3 bg-gray-50 px-3 py-2 dark:bg-gray-900"
							>
								<p class="text-xs text-gray-500">批次設定此資料表的所有欄位權限</p>
								<div class="flex gap-2">
									<button
										type="button"
										class="h-8 rounded-lg border border-sky-300 px-2.5 text-xs font-medium text-sky-700 disabled:opacity-50 dark:border-sky-900 dark:text-sky-300"
										disabled={busy || catalogBusy || bulkAuthorizationBusy}
										on:click={() => openBulkAuthorization(true, [object.id], object.display_name)}
										>完整授權此表</button
									>
									<button
										type="button"
										class="h-8 rounded-lg border border-red-300 px-2.5 text-xs font-medium text-red-700 disabled:opacity-50 dark:border-red-900 dark:text-red-300"
										disabled={busy || catalogBusy || bulkAuthorizationBusy}
										on:click={() => openBulkAuthorization(false, [object.id], object.display_name)}
										>解除此表授權</button
									>
								</div>
							</div>
							<table class="w-full min-w-[760px] text-left text-xs">
								<thead class="bg-gray-50 text-gray-500 dark:bg-gray-900"
									><tr
										><th class="px-3 py-2">欄位</th><th>型別</th><th>可讀</th><th>篩選</th><th
											>分組</th
										><th>聚合</th><th>遮罩</th></tr
									></thead
								><tbody
									>{#each object.fields as field}<tr
											class="border-t border-gray-100 dark:border-gray-800"
											><td class="px-3 py-2"
												><span class="font-medium">{fieldLabel(field)}</span><span
													class="ml-2 font-mono text-gray-400">{field.physical_name}</span
												></td
											><td
												>{field.semantic_type}<span class="block text-gray-400"
													>{field.physical_type}</span
												></td
											>{#each ['readable', 'filterable', 'groupable', 'aggregatable'] as permission}<td
													><input
														type="checkbox"
														checked={Boolean(field[permission as keyof CatalogField])}
														disabled={busy || !object.enabled}
														aria-label={`${fieldLabel(field)} ${permission}`}
														on:change={(event) =>
															updateField(
																object,
																field,
																permission as keyof CatalogField,
																event.currentTarget.checked
															)}
													/></td
												>{/each}<td
												><select
													class="h-8 rounded border border-gray-200 bg-transparent px-2 dark:border-gray-700"
													value={field.masking_rule}
													disabled={busy || !object.enabled}
													on:change={(event) =>
														updateField(object, field, 'masking_rule', event.currentTarget.value)}
													><option value="none">不遮罩</option><option value="redact"
														>完全隱藏</option
													><option value="last4">只顯示末四碼</option><option value="email"
														>Email 遮罩</option
													><option value="hash">不可逆雜湊</option></select
												></td
											></tr
										>{/each}</tbody
								>
							</table>
						</div>{/if}
				</div>
			{/each}
		</section>
	{:else if activeTab === 'relationships'}
		<section class="space-y-4">
			<div>
				<h2 class="text-lg font-semibold">資料關聯</h2>
				<p class="text-sm text-gray-500">
					外鍵只會成為建議；管理員確認 cardinality 後才能發布跨表資料集。
				</p>
			</div>
			{#if !catalog.relationships.length}<div
					class="border border-gray-200 p-6 text-center text-sm text-gray-500 dark:border-gray-800"
				>
					掃描結果沒有外鍵建議。請在資料庫建立外鍵，或透過 API 新增管理員定義關聯。
				</div>{:else}<div class="grid gap-3 lg:grid-cols-2">
					{#each catalog.relationships as relationship}<div
							class="rounded-lg border border-gray-200 p-4 dark:border-gray-800"
						>
							<div class="flex items-start justify-between gap-3">
								<div>
									<p class="font-medium">
										{objectName(relationship.left_object_id)} → {objectName(
											relationship.right_object_id
										)}
									</p>
									<p class="mt-1 text-xs text-gray-500">
										{relationship.relationship_type} · {relationship.join_type} join · {relationship.source}
									</p>
								</div>
								<span
									class="rounded px-2 py-1 text-xs {relationship.status === 'confirmed'
										? 'bg-emerald-50 text-emerald-700'
										: 'bg-amber-50 text-amber-800'}"
									>{relationship.status === 'confirmed' ? '已確認' : '待確認'}</span
								>
							</div>
							<p class="mt-3 text-xs text-gray-500">
								Fanout 風險：{relationship.fanout_risk}。查詢若沿關聯放大 measure
								grain，系統仍會拒絕。
							</p>
							{#if relationship.status !== 'confirmed'}<button
									type="button"
									class="mt-3 h-9 rounded-lg bg-gray-900 px-3 text-sm text-white disabled:opacity-60 dark:bg-white dark:text-gray-900"
									disabled={busy}
									on:click={() => confirmRelationship(relationship.id)}>確認此關聯</button
								>{/if}
						</div>{/each}
				</div>{/if}
		</section>
	{:else if activeTab === 'datasets'}
		<section class="grid min-w-0 gap-5 lg:grid-cols-[320px_1fr]">
			<div class="space-y-2">
				<div class="flex items-center justify-between">
					<h2 class="font-semibold">語意資料集</h2>
					<button
						type="button"
						class="text-sm text-sky-700"
						on:click={() => {
							if (confirmDiscardDatasetChanges()) resetDatasetForm();
						}}>新增</button
					>
				</div>
				{#each connectorDatasets as dataset}<button
						type="button"
						class="w-full rounded-lg border p-3 text-left {editingDatasetId === dataset.id
							? 'border-sky-500 bg-sky-50/50 dark:bg-sky-950/20'
							: 'border-gray-200 dark:border-gray-800'}"
						on:click={() => openDataset(dataset)}
						><div class="flex items-start justify-between gap-2">
							<span class="font-medium">{dataset.name}</span><span
								class="text-xs {dataset.status === 'published'
									? 'text-emerald-600'
									: dataset.status === 'blocked'
										? 'text-red-600'
										: 'text-amber-600'}"
								>{dataset.status === 'published'
									? '已發布'
									: dataset.status === 'degraded'
										? '結構有新增'
										: dataset.status === 'blocked'
											? '結構變更已阻擋'
											: '草稿'}</span
							>
						</div>
						<p class="mt-1 line-clamp-2 text-xs text-gray-500">
							{dataset.description || '尚無說明'}
						</p></button
					>{/each}{#if !connectorDatasets.length}<p
						class="border border-dashed border-gray-300 p-4 text-sm text-gray-500"
					>
						尚未建立資料集。
					</p>{/if}
			</div>
			<form
				class="min-w-0 space-y-5 rounded-lg border border-gray-200 p-5 dark:border-gray-800"
				on:submit|preventDefault={saveDataset}
			>
				<div class="flex items-center justify-between">
					<div>
						<h2 class="text-lg font-semibold">
							{editingDatasetId ? '編輯資料集草稿' : '新增資料集'}
						</h2>
						<p class="text-xs text-gray-500">
							儲存草稿不會影響目前發布版本。{#if datasetDirty}<span
									class="ml-2 font-medium text-amber-700 dark:text-amber-300">尚未儲存</span
								>{/if}
						</p>
					</div>
					<div class="flex items-center gap-3">
						<button
							type="button"
							class="text-sm font-medium text-sky-700 dark:text-sky-300"
							on:click={() => {
								datasetImportOpen = !datasetImportOpen;
								if (!datasetImportOpen) resetDatasetImportResult();
							}}>匯入 JSON</button
						>
						{#if editingDatasetId}<button
								type="button"
								class="text-sm text-red-600"
								on:click={() =>
									removeDataset(connectorDatasets.find((item) => item.id === editingDatasetId)!)}
								>刪除</button
							>{/if}
					</div>
				</div>
				{#if datasetImportOpen}
					<section
						class="space-y-3 rounded-lg border border-sky-200 bg-sky-50/50 p-4 dark:border-sky-900 dark:bg-sky-950/20"
						aria-labelledby="dataset-import-heading"
					>
						<div class="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
							<div>
								<h3 id="dataset-import-heading" class="text-sm font-semibold">
									讓 AI 協助建立語意資料集
								</h3>
								<p class="mt-1 text-xs text-gray-600 dark:text-gray-400">
									Schema 由您自行交給選定的 AI；WebUI 不會將資料傳送給任何模型。
								</p>
							</div>
							<div class="flex shrink-0 flex-wrap gap-2">
								<button
									type="button"
									class="h-8 rounded-lg bg-sky-600 px-3 text-xs font-medium text-white disabled:opacity-50"
									disabled={datasetHandoffBusy || !catalog.snapshotId}
									on:click={downloadAiSchemaHandoff}
									>{datasetHandoffBusy ? '匯出中...' : '匯出 AI Schema 交接包'}</button
								>
								<button
									type="button"
									class="h-8 rounded-lg border border-gray-200 bg-white px-3 text-xs font-medium dark:border-gray-700 dark:bg-gray-900"
									on:click={downloadDatasetImportTemplate}>下載通用範本</button
								>
								<label
									class="flex h-8 cursor-pointer items-center rounded-lg border border-gray-200 bg-white px-3 text-xs font-medium dark:border-gray-700 dark:bg-gray-900"
								>
									選擇 JSON 檔
									<input
										type="file"
										class="sr-only"
										accept="application/json,.json"
										on:change={readDatasetImportFile}
									/>
								</label>
							</div>
						</div>
						<ol class="grid gap-2 text-xs text-gray-600 dark:text-gray-300 sm:grid-cols-3">
							<li class="border-l-2 border-sky-500 pl-2">
								<strong class="block text-gray-900 dark:text-gray-100">1. 匯出結構</strong>
								包含已／未授權物件、欄位、關聯與 AI 編寫規格，不包含帳密或資料列。
							</li>
							<li class="border-l-2 border-gray-300 pl-2 dark:border-gray-700">
								<strong class="block text-gray-900 dark:text-gray-100">2. 自行交給 AI</strong>
								描述想回答的商業問題，請 AI 只回傳規格指定的單一 JSON。
							</li>
							<li class="border-l-2 border-gray-300 pl-2 dark:border-gray-700">
								<strong class="block text-gray-900 dark:text-gray-100">3. 匯入並審核</strong>
								系統重新驗證並逐項詢問權限；未經同意不會開啟或關閉。
							</li>
						</ol>
						<label class="block space-y-1">
							<span class="text-xs font-medium">AI 建議的語意資料集 JSON</span>
							<textarea
								class="min-h-56 w-full rounded-lg border border-gray-200 bg-white p-3 font-mono text-xs dark:border-gray-700 dark:bg-gray-950"
								bind:value={datasetImportJson}
								spellcheck="false"
								placeholder="貼上 interact-semantic-dataset v1 JSON，或選擇檔案"
								on:input={resetDatasetImportResult}
							></textarea>
						</label>
						{#if datasetImportStatus}
							<p class="text-sm" aria-live="polite">{datasetImportStatus}</p>
						{/if}
						{#if datasetImportErrors.length}
							<div
								class="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-900 dark:border-red-900 dark:bg-red-950/30 dark:text-red-200"
								role="alert"
							>
								<p class="font-medium">需要修正 {datasetImportErrors.length} 個問題</p>
								<ul class="mt-2 space-y-1 text-xs">
									{#each datasetImportErrors.slice(0, 20) as issue}
										<li><span class="font-mono">{issue.path}</span>：{issue.message}</li>
									{/each}
								</ul>
							</div>
						{/if}
						{#if datasetImportWarnings.length}
							<div
								class="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200"
							>
								{#each datasetImportWarnings as issue}<p>{issue.path}：{issue.message}</p>{/each}
							</div>
						{/if}
						{#if permissionReviewChanges.length}
							<div
								class="flex flex-col gap-3 rounded-lg border border-gray-200 bg-white p-3 text-sm dark:border-gray-700 dark:bg-gray-900 sm:flex-row sm:items-center sm:justify-between"
							>
								<div>
									<p class="font-medium">
										權限審核：已處理 {permissionReviewChanges.filter(
											(change) => permissionReviewDecisions[change.id]
										).length} / {permissionReviewChanges.length}
									</p>
									<p class="mt-1 text-xs text-gray-500">
										必要授權若略過，資料集不會匯入；AI 額外建議可安全略過。
									</p>
								</div>
								{#if !permissionReviewComplete}
									<button
										type="button"
										class="h-9 shrink-0 rounded-lg bg-gray-900 px-3 text-sm font-medium text-white dark:bg-white dark:text-gray-900"
										on:click={() => {
											permissionReviewIndex = Math.max(
												0,
												permissionReviewChanges.findIndex(
													(change) => !permissionReviewDecisions[change.id]
												)
											);
											permissionReviewOpen = true;
										}}>繼續逐項審核</button
									>
								{/if}
							</div>
						{/if}
						<div class="flex flex-wrap justify-end gap-2">
							<button
								type="button"
								class="h-9 rounded-lg border border-gray-200 bg-white px-3 text-sm dark:border-gray-700 dark:bg-gray-900"
								on:click={() => {
									datasetImportOpen = false;
									resetDatasetImportResult();
								}}>取消</button
							>
							<button
								type="button"
								class="h-9 rounded-lg border border-sky-600 px-3 text-sm font-medium text-sky-700 disabled:opacity-50 dark:text-sky-300"
								disabled={datasetImportBusy || !datasetImportJson.trim()}
								on:click={inspectDatasetImport}
								>{datasetImportBusy ? '檢查中...' : '檢查 JSON'}</button
							>
							<button
								type="button"
								class="h-9 rounded-lg bg-sky-600 px-3 text-sm font-medium text-white disabled:opacity-50"
								disabled={!datasetImportCanApply || datasetImportBusy}
								on:click={applyDatasetImport}>套用為新草稿</button
							>
						</div>
					</section>
				{/if}
				<div class="grid gap-4 sm:grid-cols-2">
					<label class="space-y-1"
						><span class="text-sm font-medium">名稱</span><input
							class="h-10 w-full rounded-lg border border-gray-200 bg-transparent px-3 dark:border-gray-700"
							bind:value={datasetName}
							required
						/></label
					><label class="space-y-1"
						><span class="text-sm font-medium">識別碼</span><input
							class="h-10 w-full rounded-lg border border-gray-200 bg-transparent px-3 font-mono dark:border-gray-700"
							bind:value={datasetSlug}
							pattern="[a-z0-9][a-z0-9_-]*"
							required
						/></label
					><label class="space-y-1 sm:col-span-2"
						><span class="text-sm font-medium">用途說明</span><textarea
							class="min-h-20 w-full rounded-lg border border-gray-200 bg-transparent p-3 dark:border-gray-700"
							bind:value={datasetDescription}
						></textarea></label
					><label class="space-y-1"
						><span class="text-sm font-medium">商業領域</span><input
							class="h-10 w-full rounded-lg border border-gray-200 bg-transparent px-3 dark:border-gray-700"
							bind:value={datasetDomain}
						/></label
					><label class="space-y-1"
						><span class="text-sm font-medium">使用者範圍</span><select
							class="h-10 w-full rounded-lg border border-gray-200 bg-transparent px-3 dark:border-gray-700"
							bind:value={datasetAccessMode}
							><option value="company_admins">僅企業管理員</option><option
								value="all_company_members">所有企業成員</option
							><option value="selected_members">指定成員或群組</option><option
								value="selected_channels">指定通訊渠道</option
							></select
						></label
					>
				</div>
				<details class="rounded-lg border border-gray-200 p-3 dark:border-gray-700">
					<summary class="cursor-pointer text-sm font-medium">Agent 選擇資料集提示</summary>
					<div class="mt-3 grid gap-3 sm:grid-cols-2">
						<label class="space-y-1 sm:col-span-2"
							><span class="text-xs font-medium">適合回答</span><textarea
								class="min-h-16 w-full rounded border border-gray-200 bg-transparent p-2 text-sm dark:border-gray-700"
								bind:value={datasetWhenToUse}
								placeholder="例如：查詢每月業績、客戶銷售排名與成交金額"
							></textarea></label
						>
						><label class="space-y-1"
							><span class="text-xs font-medium">同義詞（逗號分隔）</span><input
								class="h-9 w-full rounded border border-gray-200 bg-transparent px-2 text-sm dark:border-gray-700"
								bind:value={datasetSynonyms}
								placeholder="業績, 營收, 銷售額"
							/></label
						><label class="space-y-1"
							><span class="text-xs font-medium">不適合（逗號分隔）</span><input
								class="h-9 w-full rounded border border-gray-200 bg-transparent px-2 text-sm dark:border-gray-700"
								bind:value={datasetNotFor}
								placeholder="庫存即時量, 員工薪資"
							/></label
						><label class="space-y-1 sm:col-span-2"
							><span class="text-xs font-medium">問題範例（每行一題）</span><textarea
								class="min-h-20 w-full rounded border border-gray-200 bg-transparent p-2 text-sm dark:border-gray-700"
								bind:value={datasetExamples}
								placeholder={'本月業績前五名是誰？\n各區域上季銷售額是多少？'}
							></textarea></label
						>
					</div>
				</details>
				<div>
					<label class="text-sm font-medium" for="root-object">Measure grain 主要資料表</label
					><select
						id="root-object"
						class="mt-1 h-10 w-full rounded-lg border border-gray-200 bg-transparent px-3 dark:border-gray-700"
						bind:value={rootObjectId}
						><option value="">請選擇</option>{#each enabledObjects as object}<option
								value={object.id}>{object.display_name} ({object.physical_name})</option
							>{/each}</select
					>
				</div>
				<div class="grid gap-4 md:grid-cols-2">
					<fieldset class="rounded-lg border border-gray-200 p-3 dark:border-gray-700">
						<legend class="px-1 text-sm font-medium">維度</legend>
						<div class="max-h-56 space-y-2 overflow-y-auto">
							{#each enabledObjects as object}{#each object.fields.filter((field) => field.readable && field.groupable) as field}<label
										class="flex items-center gap-2 text-sm"
										><input type="checkbox" value={field.id} bind:group={dimensionFieldIds} /><span
											>{object.display_name} / {fieldLabel(field)}</span
										></label
									>{/each}{/each}
						</div>
					</fieldset>
					<fieldset class="rounded-lg border border-gray-200 p-3 dark:border-gray-700">
						<legend class="px-1 text-sm font-medium">指標</legend>
						<div class="max-h-56 space-y-2 overflow-y-auto">
							{#each enabledObjects as object}{#each object.fields.filter((field) => field.readable && field.aggregatable) as field}<div
										class="flex flex-wrap items-center justify-between gap-2"
									>
										<label class="flex min-w-0 items-center gap-2 text-sm"
											><input type="checkbox" value={field.id} bind:group={measureFieldIds} /><span
												class="truncate">{object.display_name} / {fieldLabel(field)}</span
											></label
										>
										{#if measureFieldIds.includes(field.id)}
											<select
												class="h-8 rounded border border-gray-200 bg-transparent px-2 text-xs dark:border-gray-700"
												value={measureAggregation(field)}
												aria-label={`${fieldLabel(field)} 聚合方式`}
												on:change={(event) =>
													setMeasureAggregation(object, field, event.currentTarget.value)}
											>
												{#each aggregationOptions(field) as option}
													<option value={option.value}>{option.label}</option>
												{/each}
											</select>
										{/if}
									</div>{/each}{/each}
						</div>
					</fieldset>
				</div>
				{#if (datasetDefinition.metrics?.length ?? 0) > 0 || (datasetDefinition.measures ?? []).some((item) => (item.filters?.length ?? 0) > 0) || datasetDefinition.defaultTimeDimensionId}
					<div
						class="rounded-lg border border-sky-200 bg-sky-50/50 p-3 text-xs dark:border-sky-900 dark:bg-sky-950/20"
					>
						<p class="font-medium">JSON 進階設定</p>
						{#if datasetDefinition.defaultTimeDimensionId}<p
								class="mt-1 text-gray-600 dark:text-gray-400"
							>
								預設時間維度：<span class="font-mono"
									>{datasetDefinition.defaultTimeDimensionId}</span
								>
							</p>{/if}
						{#if (datasetDefinition.measures ?? []).some((item) => (item.filters?.length ?? 0) > 0)}<p
								class="mt-1 text-gray-600 dark:text-gray-400"
							>
								固定指標條件：{(datasetDefinition.measures ?? []).reduce(
									(total, item) => total + (item.filters?.length ?? 0),
									0
								)} 個
							</p>{/if}
						{#if datasetDefinition.metrics?.length}<p class="mt-1 text-gray-600 dark:text-gray-400">
								衍生指標：{datasetDefinition.metrics
									.map((item) => `${item.name} (${item.id})`)
									.join(', ')}
							</p>{/if}
					</div>
				{/if}
				<fieldset class="rounded-lg border border-gray-200 p-3 dark:border-gray-700">
					<legend class="px-1 text-sm font-medium">允許的已確認關聯</legend>
					<div class="grid gap-2 sm:grid-cols-2">
						{#each catalog.relationships.filter((item) => item.status === 'confirmed') as relationship}<label
								class="flex items-center gap-2 text-sm"
								><input type="checkbox" value={relationship.id} bind:group={relationshipIds} /><span
									>{objectName(relationship.left_object_id)} → {objectName(
										relationship.right_object_id
									)}</span
								></label
							>{/each}
					</div>
				</fieldset>
				<details class="rounded-lg border border-gray-200 p-3 dark:border-gray-700">
					<summary class="cursor-pointer text-sm font-medium">模型、渠道、工作流與成員 ACL</summary>
					<p class="mt-2 text-xs text-gray-500">
						以逗號分隔 ID。留空代表不限制該類 context；connector ACL 仍會再次檢查。
					</p>
					<div class="mt-3 grid gap-3 sm:grid-cols-2">
						{#each [['模型 ID', allowedModelIds, 'model'], ['渠道 ID', allowedChannelIds, 'channel'], ['工作流 ID', allowedWorkflowIds, 'workflow'], ['成員 ID', allowedMemberIds, 'member'], ['群組 ID', allowedGroupIds, 'group']] as item}<label
								class="space-y-1"
								><span class="text-xs font-medium">{item[0]}</span><input
									class="h-9 w-full rounded border border-gray-200 bg-transparent px-2 text-sm dark:border-gray-700"
									value={item[1]}
									on:input={(event) => {
										if (item[2] === 'model') allowedModelIds = event.currentTarget.value;
										if (item[2] === 'channel') allowedChannelIds = event.currentTarget.value;
										if (item[2] === 'workflow') allowedWorkflowIds = event.currentTarget.value;
										if (item[2] === 'member') allowedMemberIds = event.currentTarget.value;
										if (item[2] === 'group') allowedGroupIds = event.currentTarget.value;
									}}
								/></label
							>{/each}
					</div>
				</details>
				<div class="flex flex-wrap justify-end gap-2">
					<button
						type="button"
						class="h-10 rounded-lg border border-gray-200 px-4 text-sm dark:border-gray-700"
						on:click={() => {
							if (confirmDiscardDatasetChanges()) resetDatasetForm();
						}}>重設</button
					><button
						type="submit"
						class="h-10 rounded-lg bg-gray-900 px-4 text-sm font-medium text-white disabled:opacity-60 dark:bg-white dark:text-gray-900"
						disabled={busy}>儲存草稿</button
					>{#if editingDatasetId}<button
							type="button"
							class="h-10 rounded-lg bg-sky-600 px-4 text-sm font-medium text-white disabled:opacity-60"
							disabled={busy}
							on:click={() =>
								publishDataset(connectorDatasets.find((item) => item.id === editingDatasetId)!)}
							>檢查並發布</button
						>{/if}
				</div>
			</form>
			{#if editingDatasetId && datasetVersions.length}<details
					class="rounded-lg border border-gray-200 p-4 dark:border-gray-800 lg:col-start-2"
				>
					<summary class="cursor-pointer text-sm font-medium">已發布版本紀錄</summary>
					<div class="mt-3 overflow-x-auto">
						<table class="w-full min-w-[520px] text-left text-sm">
							<thead class="text-xs text-gray-500"
								><tr
									><th class="py-2">版本</th><th>發布時間</th><th>Schema</th><th class="text-right"
										>操作</th
									></tr
								></thead
							><tbody>
								{#each datasetVersions as version}<tr
										class="border-t border-gray-100 dark:border-gray-800"
										><td class="py-2 font-medium">v{version.version}</td><td
											>{time(version.published_at)}</td
										><td class="font-mono text-xs">{version.snapshot_id.slice(0, 8)}</td><td
											class="text-right"
											>{#if connectorDatasets.find((item) => item.id === editingDatasetId)?.current_version_id === version.id}<span
													class="text-xs text-emerald-600">目前版本</span
												>{:else}<button
													type="button"
													class="text-sm text-sky-700 disabled:opacity-60"
													disabled={busy}
													on:click={() => activateDatasetVersion(version)}>切換至此版本</button
												>{/if}</td
										></tr
									>{/each}
							</tbody>
						</table>
					</div>
				</details>{/if}
		</section>
	{:else if activeTab === 'policies'}
		<section class="space-y-5">
			<div>
				<h2 class="text-lg font-semibold">資料列權限</h2>
				<p class="text-sm text-gray-500">
					權限會在 SQL 編譯前強制加入；必要 context 缺失時預設拒絕，不會放寬查詢。
				</p>
			</div>
			<label class="block max-w-md space-y-1"
				><span class="text-sm font-medium">資料集</span><select
					class="h-10 w-full rounded-lg border border-gray-200 bg-transparent px-3 dark:border-gray-700"
					bind:value={selectedPolicyDatasetId}
					on:change={loadPolicies}
					><option value="">請選擇</option>{#each connectorDatasets as dataset}<option
							value={dataset.id}>{dataset.name}</option
						>{/each}</select
				></label
			>{#if selectedPolicyDataset}<div class="grid gap-5 lg:grid-cols-[1fr_1fr]">
					<form
						class="space-y-4 rounded-lg border border-gray-200 p-4 dark:border-gray-800"
						on:submit|preventDefault={createPolicy}
					>
						<h3 class="font-semibold">新增權限</h3>
						<label class="block space-y-1"
							><span class="text-sm">名稱</span><input
								class="h-10 w-full rounded-lg border border-gray-200 bg-transparent px-3 dark:border-gray-700"
								bind:value={policyName}
							/></label
						>
						<div class="grid gap-3 sm:grid-cols-2">
							<label class="space-y-1"
								><span class="text-sm">適用對象</span><select
									class="h-10 w-full rounded-lg border border-gray-200 bg-transparent px-3 dark:border-gray-700"
									bind:value={policyPrincipalType}
									><option value="all">所有已授權使用者</option><option value="member"
										>指定成員</option
									><option value="group">指定群組</option><option value="channel">指定渠道</option
									><option value="model">指定模型</option></select
								></label
							><label class="space-y-1"
								><span class="text-sm">對象 ID</span><input
									class="h-10 w-full rounded-lg border border-gray-200 bg-transparent px-3 dark:border-gray-700"
									bind:value={policyPrincipalIds}
									disabled={policyPrincipalType === 'all'}
									placeholder="逗號分隔"
								/></label
							>
						</div>
						<label class="block space-y-1"
							><span class="text-sm">篩選欄位</span><select
								class="h-10 w-full rounded-lg border border-gray-200 bg-transparent px-3 dark:border-gray-700"
								bind:value={policyFieldId}
								><option value="">請選擇</option
								>{#each enabledObjects as object}{#each object.fields.filter((field) => field.readable && field.filterable) as field}<option
											value={field.id}>{object.display_name} / {fieldLabel(field)}</option
										>{/each}{/each}</select
							></label
						>
						<div class="grid gap-3 sm:grid-cols-2">
							<label class="space-y-1"
								><span class="text-sm">條件</span><select
									class="h-10 w-full rounded-lg border border-gray-200 bg-transparent px-3 dark:border-gray-700"
									bind:value={policyOperator}
									><option value="eq">等於</option><option value="ne">不等於</option><option
										value="in">包含於清單</option
									></select
								></label
							><label class="space-y-1"
								><span class="text-sm">值或 context</span><input
									class="h-10 w-full rounded-lg border border-gray-200 bg-transparent px-3 font-mono text-sm dark:border-gray-700"
									bind:value={policyValue}
								/></label
							>
						</div>
						<label class="flex items-center gap-2 text-sm"
							><input type="checkbox" bind:checked={policyPublished} />立即啟用此權限</label
						><button
							type="submit"
							class="h-10 rounded-lg bg-gray-900 px-4 text-sm font-medium text-white dark:bg-white dark:text-gray-900"
							disabled={busy}>儲存權限</button
						>
					</form>
					<div class="space-y-2">
						{#each policies as policy}<div
								class="rounded-lg border border-gray-200 p-4 dark:border-gray-800"
							>
								<div class="flex items-start justify-between gap-3">
									<div>
										<p class="font-medium">{policy.name}</p>
										<p class="mt-1 text-xs text-gray-500">
											{policy.principal_type} · {policy.principal_ids.join(', ') ||
												'所有已授權使用者'}
										</p>
									</div>
									<span
										class="text-xs {['active', 'published'].includes(policy.status)
											? 'text-emerald-600'
											: 'text-amber-600'}"
										>{['active', 'published'].includes(policy.status) ? '已啟用' : '草稿'}</span
									>
								</div>
								<pre
									class="mt-3 overflow-x-auto rounded bg-gray-50 p-2 text-xs dark:bg-gray-900">{JSON.stringify(
										policy.expression,
										null,
										2
									)}</pre>
								<button
									type="button"
									class="mt-2 text-xs text-red-600"
									on:click={async () => {
										if (confirm('確定刪除此資料列權限？')) {
											await deleteRowPolicy(localStorage.token, selectedPolicyDatasetId, policy.id);
											await loadPolicies();
										}
									}}>刪除</button
								>
							</div>{/each}{#if !policies.length}<div
								class="border border-dashed border-gray-300 p-6 text-center text-sm text-gray-500"
							>
								尚未設定資料列權限。
							</div>{/if}
					</div>
				</div>{/if}
		</section>
	{:else if activeTab === 'lab'}
		<section class="grid min-w-0 gap-5 lg:grid-cols-[380px_1fr]">
			<form
				class="space-y-4 rounded-lg border border-gray-200 p-4 dark:border-gray-800"
				on:submit|preventDefault={() => runLab('execute')}
			>
				<div>
					<h2 class="text-lg font-semibold">查詢實驗室</h2>
					<p class="text-sm text-gray-500">只使用已發布 semantic IDs；畫面不接受 SQL。</p>
				</div>
				<div class="grid grid-cols-2 rounded-lg bg-gray-100 p-1 dark:bg-gray-900">
					<button
						type="button"
						class="h-8 rounded-md text-sm {labEditorMode === 'form'
							? 'bg-white font-medium shadow-sm dark:bg-gray-800'
							: 'text-gray-500'}"
						on:click={() => switchLabEditor('form')}>欄位表單</button
					><button
						type="button"
						class="h-8 rounded-md text-sm {labEditorMode === 'json'
							? 'bg-white font-medium shadow-sm dark:bg-gray-800'
							: 'text-gray-500'}"
						on:click={() => switchLabEditor('json')}>Query Plan JSON</button
					>
				</div>
				<label class="block space-y-1"
					><span class="text-sm font-medium">已發布資料集</span><select
						class="h-10 w-full rounded-lg border border-gray-200 bg-transparent px-3 dark:border-gray-700"
						bind:value={labDatasetId}
						on:change={() => {
							labDimensions = [];
							labMeasures = [];
							labResult = null;
						}}
						><option value="">請選擇</option
						>{#each connectorDatasets.filter( (item) => ['published', 'degraded'].includes(item.status) ) as dataset}<option
								value={dataset.id}>{dataset.name}</option
							>{/each}</select
					></label
				>{#if labDataset}{#if labEditorMode === 'form'}<fieldset class="space-y-2">
							<legend class="text-sm font-medium">維度</legend
							>{#each labDefinition.dimensions ?? [] as dimension}<label
									class="flex items-center gap-2 text-sm"
									><input
										type="checkbox"
										value={dimension.id}
										bind:group={labDimensions}
									/>{dimension.name || dimension.id}</label
								>{/each}
						</fieldset>
						<fieldset class="space-y-2">
							<legend class="text-sm font-medium">指標</legend
							>{#each labDefinition.measures ?? [] as measure}<label
									class="flex items-center gap-2 text-sm"
									><input
										type="checkbox"
										value={measure.id}
										bind:group={labMeasures}
									/>{measure.name || measure.id}</label
								>{/each}
						</fieldset>
						<label class="block space-y-1"
							><span class="text-sm">最多列數</span><input
								type="number"
								min="1"
								max="1000"
								class="h-10 w-full rounded-lg border border-gray-200 bg-transparent px-3 dark:border-gray-700"
								bind:value={labLimit}
							/></label
						>{:else}<label class="block space-y-1"
							><span class="text-sm font-medium">Query Plan v1</span><textarea
								class="min-h-80 w-full rounded-lg border border-gray-200 bg-transparent p-3 font-mono text-xs dark:border-gray-700"
								bind:value={labPlanJson}
								spellcheck="false"
							></textarea></label
						>{/if}
					<div class="flex gap-2">
						<button
							type="button"
							class="h-10 flex-1 rounded-lg border border-gray-200 text-sm dark:border-gray-700"
							disabled={Boolean(labMode)}
							on:click={() => runLab('validate')}
							>{labMode === 'validate' ? '檢查中...' : '僅安全檢查'}</button
						><button
							type="submit"
							class="h-10 flex-1 rounded-lg bg-sky-600 text-sm font-medium text-white"
							disabled={Boolean(labMode)}>{labMode === 'execute' ? '執行中...' : '執行查詢'}</button
						>
					</div>{/if}
			</form>
			<div class="min-w-0 rounded-lg border border-gray-200 p-4 dark:border-gray-800">
				<h3 class="font-semibold">結果</h3>
				{#if labResult?.ok && labRows.length}<div class="mt-3 space-y-3">
						<div class="flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-500">
							<span>{labResult.rowCount ?? labRows.length} 列</span><span
								>{labResult.durationMs ?? '-'} ms</span
							><span>{labResult.truncated ? '結果已截斷' : '完整結果'}</span>
						</div>
						<div
							class="max-h-[520px] overflow-auto rounded border border-gray-200 dark:border-gray-700"
						>
							<table class="w-full min-w-[600px] text-left text-sm">
								<thead class="sticky top-0 bg-gray-50 text-xs text-gray-500 dark:bg-gray-900"
									><tr
										>{#each labFields as field}<th class="px-3 py-2">{field.label || field.id}</th
											>{/each}</tr
									></thead
								><tbody
									>{#each labRows as row}<tr class="border-t border-gray-100 dark:border-gray-800"
											>{#each labFields as field}<td class="px-3 py-2">{row[field.id] ?? '-'}</td
												>{/each}</tr
										>{/each}</tbody
								>
							</table>
						</div>
						{#if labResult.totals}<details class="text-sm">
								<summary class="cursor-pointer font-medium">總計</summary>
								<pre
									class="mt-2 overflow-auto rounded bg-gray-50 p-3 text-xs dark:bg-gray-900">{JSON.stringify(
										labResult.totals,
										null,
										2
									)}</pre>
							</details>{/if}
					</div>{:else if labResult?.ok}<div class="mt-3 space-y-3">
						<div class="grid gap-3 sm:grid-cols-3">
							<div>
								<p class="text-xs text-gray-500">資料集版本</p>
								<p class="text-sm font-medium">
									{(labResult.dataset as Record<string, unknown>)?.name} v{(
										labResult.dataset as Record<string, unknown>
									)?.version}
								</p>
							</div>
							<div>
								<p class="text-xs text-gray-500">關聯數</p>
								<p class="text-sm font-medium">
									{(labResult.estimatedCost as Record<string, unknown>)?.joinCount ?? 0}
								</p>
							</div>
							<div>
								<p class="text-xs text-gray-500">列數／逾時上限</p>
								<p class="text-sm font-medium">
									{(labResult.estimatedCost as Record<string, unknown>)?.resultRowLimit} / {(
										labResult.estimatedCost as Record<string, unknown>
									)?.timeoutSeconds}s
								</p>
							</div>
						</div>
						<div
							class="rounded bg-emerald-50 p-3 text-sm text-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-200"
						>
							ACL、資料列權限、欄位與關聯檢查通過。
						</div>
						{#if labResult.sqlPreview}<details>
								<summary class="cursor-pointer text-sm font-medium">參數化 SQL 預覽</summary>
								<pre
									class="mt-2 overflow-auto rounded bg-gray-50 p-3 text-xs dark:bg-gray-900">{labResult.sqlPreview}</pre>
							</details>{/if}
					</div>{:else if labResult}<pre
						class="mt-3 max-h-[620px] overflow-auto rounded bg-red-50 p-3 text-xs text-red-900 dark:bg-red-950/30 dark:text-red-200">{JSON.stringify(
							labResult,
							null,
							2
						)}</pre>{:else}<div class="py-16 text-center text-sm text-gray-500">
						選擇欄位後先執行安全檢查，確認 ACL、關聯與成本限制。
					</div>{/if}
			</div>
		</section>
	{:else if activeTab === 'activity'}
		<section class="space-y-4">
			<div>
				<h2 class="text-lg font-semibold">語意查詢活動</h2>
				<p class="text-sm text-gray-500">
					稽核不保存資料庫密碼或完整 SQL；request ID 可用於追查渠道與工作流問題。
				</p>
			</div>
			<div class="overflow-x-auto rounded-lg border border-gray-200 dark:border-gray-800">
				<table class="w-full min-w-[760px] text-left text-sm">
					<thead class="bg-gray-50 text-xs text-gray-500 dark:bg-gray-900"
						><tr
							><th class="px-3 py-2">時間</th><th>狀態</th><th>資料集</th><th>列數</th><th>耗時</th
							><th>錯誤代碼</th><th>Request ID</th></tr
						></thead
					><tbody
						>{#each events as event}<tr class="border-t border-gray-100 dark:border-gray-800"
								><td class="px-3 py-2">{time(event.created_at)}</td><td
									class={event.status === 'success' ? 'text-emerald-600' : 'text-red-600'}
									>{event.status}</td
								><td
									>{connectorDatasets.find((item) => item.id === event.dataset_id)?.name ?? '-'}</td
								><td>{event.row_count}</td><td>{event.duration_ms ?? '-'} ms</td><td
									class="font-mono text-xs">{event.error_code ?? '-'}</td
								><td class="font-mono text-xs">{event.request_id}</td></tr
							>{/each}{#if !events.length}<tr
								><td colspan="7" class="px-3 py-12 text-center text-gray-500">尚無查詢紀錄。</td
								></tr
							>{/if}</tbody
					>
				</table>
			</div>
		</section>
	{/if}
</div>

{#if bulkAuthorizationPreview}
	<SemanticBulkAuthorizationDialog
		authorized={bulkAuthorizationAuthorized}
		scopeLabel={bulkAuthorizationScopeLabel}
		preview={bulkAuthorizationPreview}
		busy={bulkAuthorizationBusy}
		error={bulkAuthorizationError}
		on:apply={applyBulkAuthorization}
		on:close={closeBulkAuthorization}
	/>
{/if}

{#if permissionReviewOpen}
	<SemanticPermissionReviewDialog
		changes={permissionReviewChanges}
		index={permissionReviewIndex}
		busy={permissionReviewBusy}
		error={permissionReviewError}
		on:accept={(event) => acceptPermissionChange(event.detail)}
		on:skip={(event) => skipPermissionChange(event.detail)}
		on:close={closePermissionReview}
	/>
{/if}

import { WEBUI_API_BASE_URL } from '$lib/constants';

export type SchemaSnapshot = {
	id: string;
	connector_id: string;
	version: number;
	status: string;
	fingerprint: string;
	created_at: number;
	completed_at?: number | null;
};

export type CatalogField = {
	id: string;
	physical_name: string;
	display_name: string;
	description?: string | null;
	physical_type: string;
	semantic_type: string;
	primary_key: boolean;
	readable: boolean;
	filterable: boolean;
	groupable: boolean;
	aggregatable: boolean;
	default_aggregation?: string | null;
	sensitivity: string;
	masking_rule: string;
};

export type CatalogObject = {
	id: string;
	physical_name: string;
	display_name: string;
	description?: string | null;
	object_type: string;
	enabled: boolean;
	source_verified: boolean;
	fields: CatalogField[];
};

export type CatalogRelationship = {
	id: string;
	left_object_id: string;
	right_object_id: string;
	relationship_type: string;
	join_type: string;
	join_pairs: { leftFieldId: string; rightFieldId: string }[];
	source: string;
	status: string;
	fanout_risk: string;
	description?: string | null;
};

export type SemanticCatalog = {
	snapshotId: string | null;
	objects: CatalogObject[];
	relationships: CatalogRelationship[];
};

export type BulkCatalogAuthorizationResult = {
	ok: boolean;
	status: 'preview' | 'updated';
	snapshot_id: string;
	object_count: number;
	field_count: number;
	affected_datasets: { id: string; name: string; status: string }[];
};

export type SemanticDataset = {
	id: string;
	company_user_id: string;
	connector_id: string;
	slug: string;
	name: string;
	description: string;
	business_domain?: string | null;
	status: string;
	current_version_id?: string | null;
	draft_definition: Record<string, unknown>;
	access_mode: string;
	allowed_member_ids: string[];
	allowed_group_ids: string[];
	allowed_model_ids: string[];
	allowed_channel_ids: string[];
	allowed_workflow_ids: string[];
	updated_at: number;
};

export type SemanticDatasetVersion = {
	id: string;
	dataset_id: string;
	version: number;
	snapshot_id: string;
	published_by: string;
	published_at: number;
};

export type RowPolicy = {
	id: string;
	dataset_id: string;
	name: string;
	status: string;
	principal_type: string;
	principal_ids: string[];
	expression: Record<string, unknown>;
	deny_if_unresolved: boolean;
};

export type SemanticQueryEvent = {
	id: string;
	request_id: string;
	dataset_id?: string | null;
	status: string;
	row_count: number;
	duration_ms?: number | null;
	error_code?: string | null;
	created_at: number;
};

export type SemanticDatasetDefinition = {
	snapshotId?: string | null;
	rootObjectId?: string;
	whenToUse?: string;
	notFor?: string[];
	examples?: string[];
	synonyms?: string[];
	defaultTimeDimensionId?: string;
	relationshipIds?: string[];
	dimensions?: Array<Record<string, unknown> & { id: string; name: string; fieldId: string }>;
	measures?: Array<
		Record<string, unknown> & {
			id: string;
			name: string;
			fieldId: string;
			aggregation: 'sum' | 'count' | 'count_distinct' | 'avg' | 'min' | 'max';
			filters?: unknown[];
		}
	>;
	metrics?: Array<Record<string, unknown> & { id: string; name: string }>;
	[key: string]: unknown;
};

export type SemanticDatasetImportIssue = {
	code: string;
	path: string;
	message: string;
	reference?: unknown;
	candidates?: string[];
};

export type SemanticDatasetImportPayload = {
	connector_id: string;
	name: string;
	slug: string;
	description: string;
	business_domain?: string | null;
	access_mode: string;
	allowed_member_ids: string[];
	allowed_group_ids: string[];
	allowed_model_ids: string[];
	allowed_channel_ids: string[];
	allowed_workflow_ids: string[];
	definition: SemanticDatasetDefinition;
};

export type SemanticPermissionChange = {
	id: string;
	targetType: 'object' | 'field';
	object: string;
	field?: string | null;
	objectId: string;
	fieldId?: string | null;
	permission: 'enabled' | 'readable' | 'filterable' | 'groupable' | 'aggregatable';
	action: 'grant' | 'revoke';
	current: boolean;
	desired: boolean;
	reason: string;
	required: boolean;
	requiredBy: string[];
	source: 'system' | 'ai' | 'system+ai';
	status: 'pending' | 'already_satisfied' | 'conflict';
	impact: string;
};

export type SemanticDatasetImportResult = {
	ok: boolean;
	errors: SemanticDatasetImportIssue[];
	warnings: SemanticDatasetImportIssue[];
	dataset: SemanticDatasetImportPayload | null;
	permissionReviewRequired?: boolean;
	permissionChanges?: SemanticPermissionChange[];
	authorizationSummary?: {
		missingRequired: number;
		aiSuggestions: number;
		alreadySatisfied: number;
		conflicts: number;
	};
	summary?: { dimensions: number; measures: number; metrics: number; relationships: number };
};

const request = async <T>(token: string, path: string, init?: RequestInit): Promise<T> => {
	const response = await fetch(`${WEBUI_API_BASE_URL}/interact${path}`, {
		...init,
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`,
			...(init?.headers ?? {})
		}
	});
	if (!response.ok) {
		let message: unknown = response.statusText;
		try {
			const body = await response.json();
			message = body?.detail?.message ?? body?.detail ?? body?.error ?? message;
		} catch {
			// The status text is the safest fallback when a proxy returns HTML or plain text.
		}
		throw typeof message === 'string' ? message : JSON.stringify(message);
	}
	return response.json() as Promise<T>;
};

export const createSchemaSnapshot = (token: string, connectorId: string) =>
	request<{ ok: boolean; created: boolean; snapshot: SchemaSnapshot }>(
		token,
		`/data-connectors/${encodeURIComponent(connectorId)}/schema-scans`,
		{ method: 'POST', body: JSON.stringify({ max_tables: 500 }) }
	);

export const getSchemaSnapshots = (token: string, connectorId: string) =>
	request<{ ok: boolean; snapshots: SchemaSnapshot[] }>(
		token,
		`/data-connectors/${encodeURIComponent(connectorId)}/schema-snapshots`
	);

export const getSemanticCatalog = (token: string, connectorId: string, snapshotId?: string) =>
	request<{ ok: boolean; catalog: SemanticCatalog }>(
		token,
		`/data-connectors/${encodeURIComponent(connectorId)}/catalog${
			snapshotId ? `?snapshot_id=${encodeURIComponent(snapshotId)}` : ''
		}`
	);

export const getSemanticAiSchemaHandoff = (token: string, connectorId: string) =>
	request<{ ok: boolean; document: Record<string, unknown> }>(
		token,
		`/data-connectors/${encodeURIComponent(connectorId)}/semantic-datasets/ai-schema-handoff`
	);

export const patchCatalogObject = (
	token: string,
	objectId: string,
	patch: Partial<CatalogObject>
) =>
	request<{ ok: boolean; object: CatalogObject; affectedDatasets: number }>(
		token,
		`/catalog/objects/${encodeURIComponent(objectId)}`,
		{ method: 'PATCH', body: JSON.stringify(patch) }
	);

export const patchCatalogObjects = (
	token: string,
	connectorId: string,
	snapshotId: string,
	objectIds: string[],
	enabled: boolean
) =>
	request<{
		ok: boolean;
		status: string;
		snapshot_id: string;
		updated_count: number;
		affectedDatasets: number;
	}>(token, `/data-connectors/${encodeURIComponent(connectorId)}/catalog/objects`, {
		method: 'PATCH',
		body: JSON.stringify({ snapshot_id: snapshotId, object_ids: objectIds, enabled })
	});

export const bulkSemanticCatalogAuthorization = (
	token: string,
	connectorId: string,
	payload: {
		snapshot_id: string;
		object_ids?: string[];
		authorized: boolean;
		apply?: boolean;
		acknowledge_impact?: boolean;
	}
) =>
	request<BulkCatalogAuthorizationResult>(
		token,
		`/data-connectors/${encodeURIComponent(connectorId)}/catalog/authorization`,
		{ method: 'POST', body: JSON.stringify(payload) }
	);

export const patchCatalogField = (token: string, fieldId: string, patch: Partial<CatalogField>) =>
	request<{ ok: boolean; field: CatalogField; affectedDatasets: number }>(
		token,
		`/catalog/fields/${encodeURIComponent(fieldId)}`,
		{ method: 'PATCH', body: JSON.stringify(patch) }
	);

export const applySemanticCatalogPermissionChange = (
	token: string,
	connectorId: string,
	payload: {
		snapshot_id: string;
		target_type: 'object' | 'field';
		object_id: string;
		field_id?: string | null;
		permission: SemanticPermissionChange['permission'];
		desired: boolean;
	}
) =>
	request<{
		ok: boolean;
		target: 'object' | 'field';
		value: CatalogObject | CatalogField;
		affectedDatasets: number;
	}>(token, `/data-connectors/${encodeURIComponent(connectorId)}/catalog/permission-changes`, {
		method: 'POST',
		body: JSON.stringify(payload)
	});

export const saveCatalogRelationship = (
	token: string,
	connectorId: string,
	payload: Partial<CatalogRelationship>
) =>
	request<{ ok: boolean; relationship: CatalogRelationship }>(
		token,
		`/data-connectors/${encodeURIComponent(connectorId)}/relationships`,
		{ method: 'PUT', body: JSON.stringify(payload) }
	);

export const getSemanticDatasets = (token: string) =>
	request<{ ok: boolean; datasets: SemanticDataset[] }>(token, '/semantic-datasets');

export const resolveSemanticDatasetImport = (
	token: string,
	connectorId: string,
	document: Record<string, unknown>
) =>
	request<{ ok: boolean; import: SemanticDatasetImportResult }>(
		token,
		`/data-connectors/${encodeURIComponent(connectorId)}/semantic-datasets/import`,
		{ method: 'POST', body: JSON.stringify({ document }) }
	);

export const saveSemanticDataset = (
	token: string,
	payload: Record<string, unknown>,
	datasetId?: string
) =>
	request<{ ok: boolean; dataset: SemanticDataset }>(
		token,
		datasetId ? `/semantic-datasets/${encodeURIComponent(datasetId)}` : '/semantic-datasets',
		{ method: datasetId ? 'PUT' : 'POST', body: JSON.stringify(payload) }
	);

export const deleteSemanticDataset = (token: string, datasetId: string) =>
	request<{ ok: boolean; deleted: boolean }>(
		token,
		`/semantic-datasets/${encodeURIComponent(datasetId)}`,
		{ method: 'DELETE' }
	);

export const validateSemanticDataset = (token: string, datasetId: string) =>
	request<{ ok: boolean; validation: { ok: boolean; errors: unknown[]; warnings: unknown[] } }>(
		token,
		`/semantic-datasets/${encodeURIComponent(datasetId)}/validate`,
		{ method: 'POST' }
	);

export const publishSemanticDataset = (token: string, datasetId: string) =>
	request<{ ok: boolean; version: Record<string, unknown> }>(
		token,
		`/semantic-datasets/${encodeURIComponent(datasetId)}/publish`,
		{ method: 'POST' }
	);

export const getSemanticDatasetVersions = (token: string, datasetId: string) =>
	request<{ ok: boolean; versions: SemanticDatasetVersion[] }>(
		token,
		`/semantic-datasets/${encodeURIComponent(datasetId)}/versions`
	);

export const activateSemanticDatasetVersion = (
	token: string,
	datasetId: string,
	versionId: string
) =>
	request<{ ok: boolean; dataset: SemanticDataset; version: SemanticDatasetVersion }>(
		token,
		`/semantic-datasets/${encodeURIComponent(datasetId)}/versions/${encodeURIComponent(versionId)}/activate`,
		{ method: 'POST' }
	);

export const testSemanticDatasetQuery = (
	token: string,
	datasetId: string,
	plan: Record<string, unknown>,
	validateOnly = false
) =>
	request<Record<string, unknown>>(
		token,
		`/semantic-datasets/${encodeURIComponent(datasetId)}/test-query${
			validateOnly ? '?validate_only=true' : ''
		}`,
		{ method: 'POST', body: JSON.stringify(plan) }
	);

export const getRowPolicies = (token: string, datasetId: string) =>
	request<{ ok: boolean; policies: RowPolicy[] }>(
		token,
		`/semantic-datasets/${encodeURIComponent(datasetId)}/row-policies`
	);

export const saveRowPolicy = (token: string, datasetId: string, payload: Record<string, unknown>) =>
	request<{ ok: boolean; policy: RowPolicy }>(
		token,
		`/semantic-datasets/${encodeURIComponent(datasetId)}/row-policies`,
		{ method: 'PUT', body: JSON.stringify(payload) }
	);

export const deleteRowPolicy = (token: string, datasetId: string, policyId: string) =>
	request<{ ok: boolean; deleted: boolean }>(
		token,
		`/semantic-datasets/${encodeURIComponent(datasetId)}/row-policies/${encodeURIComponent(policyId)}`,
		{ method: 'DELETE' }
	);

export const validateSemanticQuery = (token: string, plan: Record<string, unknown>) =>
	request<Record<string, unknown>>(token, '/semantic-query/validate', {
		method: 'POST',
		body: JSON.stringify(plan)
	});

export const executeSemanticQuery = (token: string, plan: Record<string, unknown>) =>
	request<Record<string, unknown>>(token, '/semantic-query/execute', {
		method: 'POST',
		body: JSON.stringify(plan)
	});

export const getSemanticQueryEvents = (token: string, limit = 100) =>
	request<{ ok: boolean; events: SemanticQueryEvent[] }>(
		token,
		`/semantic-query/events?limit=${limit}`
	);

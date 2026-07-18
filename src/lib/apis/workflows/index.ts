import { WEBUI_API_BASE_URL } from '$lib/constants';

export type WorkflowGraph = {
	nodes: any[];
	edges: any[];
	[key: string]: any;
};

export type WorkflowForm = {
	name: string;
	description?: string | null;
	graph: WorkflowGraph;
	meta?: Record<string, any> | null;
	visibility?: string;
	status?: string;
};

export type WorkflowResponse = WorkflowForm & {
	id: string;
	user_id: string;
	visibility: string;
	status: string;
	default_version_id: string | null;
	created_at: number;
	updated_at: number;
};

export type WorkflowVersionResponse = {
	id: string;
	workflow_id: string;
	version: number;
	graph: WorkflowGraph;
	meta: Record<string, any> | null;
	created_by: string;
	created_at: number;
};

export type WorkflowRunResponse = {
	id: string;
	workflow_id: string;
	workflow_version_id: string | null;
	user_id: string;
	trigger_type: string;
	status: string;
	input: Record<string, any> | null;
	output: Record<string, any> | null;
	error: string | null;
	created_at: number;
	completed_at: number | null;
};

export type WorkflowValidateResponse = {
	ok: boolean;
	errors: string[];
	warnings: string[];
};

export type WorkflowLaunchCheck = {
	code: string;
	status: 'pass' | 'warning' | 'fail';
	message: string;
};

export type WorkflowLaunchPreflightResponse = {
	ok: boolean;
	workflow_id: string;
	workflow_version_id: string | null;
	launch: Record<string, any>;
	effective_model_id: string | null;
	missing_fields: string[];
	requires_confirmation: boolean;
	checks: WorkflowLaunchCheck[];
};

export type WorkflowSelectorItem = {
	id: string;
	name: string;
	description: string | null;
	visibility: string;
	default_version_id: string | null;
	score: number;
	confidence: number;
	threshold: number;
	priority: number;
	ambiguity_margin: number;
	matched_keywords: string[];
	matched_required_keywords: string[];
	matched_examples: { example: string; similarity: number }[];
	reason: string;
};

export type WorkflowSelectorResponse = {
	decision: 'selected' | 'ambiguous' | 'none';
	action: 'execute_workflow' | 'ask_user' | 'continue_chat';
	selected_workflow_id: string | null;
	selected_version_id: string | null;
	needs_confirmation: boolean;
	reason: string;
	items: WorkflowSelectorItem[];
};

const parseResponse = async (res: Response) => {
	if (!res.ok) {
		const err = await res.json().catch(() => ({ detail: res.statusText }));
		throw err.detail ?? err;
	}
	return res.json();
};

const headers = (token: string) => ({
	Accept: 'application/json',
	'Content-Type': 'application/json',
	authorization: `Bearer ${token}`
});

export const getWorkflowItems = async (
	token: string,
	query: string | null = null,
	visibility: string | null = null,
	status: string | null = null,
	page = 1
): Promise<{ items: WorkflowResponse[]; total: number }> => {
	const searchParams = new URLSearchParams();
	if (query) searchParams.append('query', query);
	if (visibility && visibility !== 'all') searchParams.append('visibility', visibility);
	if (status && status !== 'all') searchParams.append('status', status);
	if (page) searchParams.append('page', page.toString());

	return fetch(`${WEBUI_API_BASE_URL}/workflows/list?${searchParams.toString()}`, {
		method: 'GET',
		headers: headers(token)
	}).then(parseResponse);
};

export const createWorkflow = async (
	token: string,
	form: WorkflowForm
): Promise<WorkflowResponse> => {
	return fetch(`${WEBUI_API_BASE_URL}/workflows/create`, {
		method: 'POST',
		headers: headers(token),
		body: JSON.stringify(form)
	}).then(parseResponse);
};

export const getWorkflowById = async (token: string, id: string): Promise<WorkflowResponse> => {
	return fetch(`${WEBUI_API_BASE_URL}/workflows/${id}`, {
		method: 'GET',
		headers: headers(token)
	}).then(parseResponse);
};

export const updateWorkflowById = async (
	token: string,
	id: string,
	form: Partial<WorkflowForm>
): Promise<WorkflowResponse> => {
	return fetch(`${WEBUI_API_BASE_URL}/workflows/${id}/update`, {
		method: 'POST',
		headers: headers(token),
		body: JSON.stringify(form)
	}).then(parseResponse);
};

export const validateWorkflowById = async (
	token: string,
	id: string,
	form?: Pick<WorkflowForm, 'graph' | 'meta' | 'visibility'>
): Promise<WorkflowValidateResponse> => {
	return fetch(`${WEBUI_API_BASE_URL}/workflows/${id}/validate`, {
		method: 'POST',
		headers: headers(token),
		body: JSON.stringify(form ?? {})
	}).then(parseResponse);
};

export const selectAgentWorkflows = async (
	token: string,
	message: string,
	channelId?: string,
	modelId?: string,
	maxItems = 5
): Promise<WorkflowSelectorResponse> => {
	return fetch(`${WEBUI_API_BASE_URL}/workflows/agent/select`, {
		method: 'POST',
		headers: headers(token),
		body: JSON.stringify({ message, channelId, modelId, maxItems })
	}).then(parseResponse);
};

export const publishWorkflowById = async (
	token: string,
	id: string
): Promise<WorkflowVersionResponse> => {
	return fetch(`${WEBUI_API_BASE_URL}/workflows/${id}/publish`, {
		method: 'POST',
		headers: headers(token)
	}).then(parseResponse);
};

export const archiveWorkflowById = async (token: string, id: string): Promise<WorkflowResponse> => {
	return fetch(`${WEBUI_API_BASE_URL}/workflows/${id}/archive`, {
		method: 'POST',
		headers: headers(token)
	}).then(parseResponse);
};

export const activateWorkflowById = async (
	token: string,
	id: string
): Promise<WorkflowResponse> => {
	return fetch(`${WEBUI_API_BASE_URL}/workflows/${id}/activate`, {
		method: 'POST',
		headers: headers(token)
	}).then(parseResponse);
};

export const runWorkflowById = async (
	token: string,
	id: string,
	input: Record<string, any> = {},
	trigger_type = 'manual',
	model_id?: string,
	workflow_version_id?: string,
	confirmed = false
): Promise<WorkflowRunResponse> => {
	return fetch(`${WEBUI_API_BASE_URL}/workflows/${id}/run`, {
		method: 'POST',
		headers: headers(token),
		body: JSON.stringify({ input, trigger_type, model_id, workflow_version_id, confirmed })
	}).then(parseResponse);
};

export const preflightWorkflowById = async (
	token: string,
	id: string,
	input: Record<string, any> = {},
	options: {
		workflow_version_id?: string;
		model_id?: string;
		channel_id?: string;
		surface?: 'webui_chat' | 'channel' | 'api';
		confirmed?: boolean;
	} = {}
): Promise<WorkflowLaunchPreflightResponse> => {
	return fetch(`${WEBUI_API_BASE_URL}/workflows/${id}/preflight`, {
		method: 'POST',
		headers: headers(token),
		body: JSON.stringify({ input, ...options })
	}).then(parseResponse);
};

export const getWorkflowRuns = async (
	token: string,
	id: string,
	limit = 20
): Promise<WorkflowRunResponse[]> => {
	return fetch(`${WEBUI_API_BASE_URL}/workflows/${id}/runs?limit=${limit}`, {
		method: 'GET',
		headers: headers(token)
	}).then(parseResponse);
};

export const resumeWorkflowRun = async (
	token: string,
	workflowId: string,
	runId: string,
	payload: {
		decision: 'approved' | 'rejected' | 'selected' | 'cancelled';
		value?: any;
		revision: number;
		reason?: string;
	}
): Promise<WorkflowRunResponse> => {
	return fetch(
		`${WEBUI_API_BASE_URL}/workflows/${encodeURIComponent(workflowId)}/runs/${encodeURIComponent(runId)}/resume`,
		{
			method: 'POST',
			headers: headers(token),
			body: JSON.stringify(payload)
		}
	).then(parseResponse);
};

export const deleteWorkflowById = async (
	token: string,
	id: string
): Promise<{ success: boolean }> => {
	return fetch(`${WEBUI_API_BASE_URL}/workflows/${id}/delete`, {
		method: 'DELETE',
		headers: headers(token)
	}).then(parseResponse);
};

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
	page = 1
): Promise<{ items: WorkflowResponse[]; total: number }> => {
	const searchParams = new URLSearchParams();
	if (query) searchParams.append('query', query);
	if (visibility && visibility !== 'all') searchParams.append('visibility', visibility);
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
	graph?: WorkflowGraph
): Promise<WorkflowValidateResponse> => {
	return fetch(`${WEBUI_API_BASE_URL}/workflows/${id}/validate`, {
		method: 'POST',
		headers: headers(token),
		body: JSON.stringify(graph ? { graph } : {})
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

export const runWorkflowById = async (
	token: string,
	id: string,
	input: Record<string, any> = {},
	trigger_type = 'manual'
): Promise<WorkflowRunResponse> => {
	return fetch(`${WEBUI_API_BASE_URL}/workflows/${id}/run`, {
		method: 'POST',
		headers: headers(token),
		body: JSON.stringify({ input, trigger_type })
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

export const deleteWorkflowById = async (
	token: string,
	id: string
): Promise<{ success: boolean }> => {
	return fetch(`${WEBUI_API_BASE_URL}/workflows/${id}/delete`, {
		method: 'DELETE',
		headers: headers(token)
	}).then(parseResponse);
};

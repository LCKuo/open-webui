import { WEBUI_API_BASE_URL } from '$lib/constants';

export type InteractDataConnector = {
	id: string;
	companyUserId: string;
	name: string;
	connectorType: string;
	executionMode: string;
	storageMode: string;
	enabled: boolean;
	host?: string | null;
	port?: number | null;
	databaseName?: string | null;
	username?: string | null;
	hasPassword: boolean;
	hasConnectionString: boolean;
	sslMode?: string | null;
	updatedAt?: number | null;
};

export type InteractDataConnectorCredentials = {
	host?: string | null;
	port?: number | null;
	database_name?: string | null;
	username?: string | null;
	password?: string | null;
	connection_string?: string | null;
	ssl_mode?: string | null;
};

const parseError = async (res: Response) => {
	try {
		const body = await res.json();
		return body?.detail ?? body?.error ?? res.statusText;
	} catch {
		return res.statusText;
	}
};

export const getInteractDataConnectors = async (token: string) => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/interact/data-connectors`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	});

	if (!res.ok) {
		throw await parseError(res);
	}

	return res.json() as Promise<{ ok: boolean; connectors: InteractDataConnector[] }>;
};

export const updateInteractDataConnectorLocalCredentials = async (
	token: string,
	connectorId: string,
	payload: InteractDataConnectorCredentials
) => {
	const res = await fetch(
		`${WEBUI_API_BASE_URL}/interact/data-connectors/${encodeURIComponent(
			connectorId
		)}/local-credentials`,
		{
			method: 'PUT',
			headers: {
				'Content-Type': 'application/json',
				Authorization: `Bearer ${token}`
			},
			body: JSON.stringify(payload)
		}
	);

	if (!res.ok) {
		throw await parseError(res);
	}

	return res.json();
};

export const deleteInteractDataConnectorLocal = async (token: string, connectorId: string) => {
	const res = await fetch(
		`${WEBUI_API_BASE_URL}/interact/data-connectors/${encodeURIComponent(connectorId)}/local`,
		{
			method: 'DELETE',
			headers: {
				'Content-Type': 'application/json',
				Authorization: `Bearer ${token}`
			}
		}
	);

	if (!res.ok) {
		throw await parseError(res);
	}

	return res.json() as Promise<{ ok: boolean; connectorId: string; deleted: boolean }>;
};

export const scanInteractDataConnectorSchemaLocal = async (
	token: string,
	connectorId: string,
	maxTables = 200
) => {
	const res = await fetch(
		`${WEBUI_API_BASE_URL}/interact/data-connectors/${encodeURIComponent(
			connectorId
		)}/schema-scan-local`,
		{
			method: 'POST',
			headers: {
				'Content-Type': 'application/json',
				Authorization: `Bearer ${token}`
			},
			body: JSON.stringify({ max_tables: maxTables })
		}
	);

	if (!res.ok) {
		throw await parseError(res);
	}

	return res.json() as Promise<{ ok: boolean; connectorId: string; schema?: unknown }>;
};

import { WEBUI_API_BASE_URL } from '$lib/constants';

export type EmailConnector = {
	id: string;
	company_user_id: string;
	name: string;
	provider: 'resend';
	enabled: boolean;
	status: string;
	key_last4: string | null;
	has_api_key: boolean;
	has_webhook_secret: boolean;
	from_name: string | null;
	from_address: string;
	reply_to: string | null;
	verified_domain: string | null;
	access_mode: string;
	allowed_member_ids: string[];
	allowed_group_ids: string[];
	allowed_workflow_ids: string[];
	allowed_channel_ids: string[];
	cc_policy: Record<string, any>;
	recipient_policy: Record<string, any>;
	daily_send_limit: number;
	max_recipients_per_send: number;
	last_test_at: number | null;
	last_error: string | null;
	updated_at: number;
};

export type EmailConnectorForm = {
	id?: string;
	name: string;
	provider?: 'resend';
	enabled: boolean;
	api_key?: string;
	webhook_secret?: string;
	from_name?: string;
	from_address: string;
	reply_to?: string;
	verified_domain?: string;
	access_mode: string;
	allowed_member_ids: string[];
	allowed_group_ids: string[];
	allowed_workflow_ids: string[];
	allowed_channel_ids: string[];
	cc_policy: Record<string, any>;
	recipient_policy: Record<string, any>;
	daily_send_limit: number;
	max_recipients_per_send: number;
};

export type EmailDelivery = {
	id: string;
	connector_id: string;
	workflow_id: string | null;
	workflow_run_id: string | null;
	from_address: string | null;
	reply_to: string | null;
	provider_message_id: string | null;
	status: string;
	recipient_count: number;
	recipient_domains: string[];
	error_code: string | null;
	error_message: string | null;
	created_at: number;
	updated_at: number;
};

const headers = (token: string) => ({
	Accept: 'application/json',
	'Content-Type': 'application/json',
	authorization: `Bearer ${token}`
});

const parse = async (response: Response) => {
	const raw = await response.text();
	let data: Record<string, any> = {};
	try {
		data = raw ? JSON.parse(raw) : {};
	} catch {
		data = {};
	}
	if (!response.ok) {
		const detail =
			typeof data.detail === 'string'
				? data.detail
				: typeof data.error === 'string'
					? data.error
					: '';
		if (detail) throw new Error(detail);
		if (response.status >= 500) {
			throw new Error(`寄信服務暫時無法連線（HTTP ${response.status}），請稍後再試。`);
		}
		throw new Error(response.statusText || '寄信服務發生錯誤。');
	}
	return data;
};

export const getEmailConnectors = (token: string): Promise<EmailConnector[]> =>
	fetch(`${WEBUI_API_BASE_URL}/interact/email-connectors`, { headers: headers(token) }).then(parse);

export const saveEmailConnector = (
	token: string,
	form: EmailConnectorForm
): Promise<EmailConnector> =>
	fetch(`${WEBUI_API_BASE_URL}/interact/email-connectors`, {
		method: 'POST',
		headers: headers(token),
		body: JSON.stringify(form)
	}).then(parse);

export const deleteEmailConnector = (token: string, connectorId: string): Promise<{ ok: true }> =>
	fetch(`${WEBUI_API_BASE_URL}/interact/email-connectors/${encodeURIComponent(connectorId)}`, {
		method: 'DELETE',
		headers: headers(token)
	}).then(parse);

export const testEmailConnector = (
	token: string,
	connectorId: string,
	recipient: string
): Promise<{ ok: true; delivery: EmailDelivery }> =>
	fetch(`${WEBUI_API_BASE_URL}/interact/email-connectors/${encodeURIComponent(connectorId)}/test`, {
		method: 'POST',
		headers: headers(token),
		body: JSON.stringify({ recipient })
	}).then(parse);

export const getEmailDeliveries = (token: string, limit = 50): Promise<EmailDelivery[]> =>
	fetch(`${WEBUI_API_BASE_URL}/interact/email-deliveries?limit=${limit}`, {
		headers: headers(token)
	}).then(parse);

import { OPENAI_API_BASE_URL, WEBUI_API_BASE_URL, WEBUI_BASE_URL } from '$lib/constants';

export const getOpenAIConfig = async (token: string = '') => {
	let error = null;

	const res = await fetch(`${OPENAI_API_BASE_URL}/config`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			if ('detail' in err) {
				error = err.detail;
			} else {
				error = 'Server connection failed';
			}
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

type OpenAIConfig = {
	ENABLE_OPENAI_API: boolean;
	OPENAI_API_BASE_URLS: string[];
	OPENAI_API_KEYS: string[];
	OPENAI_API_CONFIGS: object;
};

export type PersonalAPIKeyConnection = {
	id: string;
	name: string;
	provider: 'openai' | 'gemini' | 'custom';
	base_url: string;
	host: string;
	auth_type: 'bearer' | 'none';
	key_last4: string | null;
	last_verified_at: number | null;
	verification_status: 'ready' | 'failed' | null;
};

export type PersonalAPIKeyConnectionsResponse = {
	mode: 'personal' | 'platform';
	connections: PersonalAPIKeyConnection[];
};

export type PersonalAPIKeyForm = {
	name: string;
	provider: 'openai' | 'gemini' | 'custom';
	base_url?: string;
	auth_type: 'bearer' | 'none';
	api_key?: string;
};

const personalAPIKeyRequest = async <T>(token: string, path: string, init: RequestInit = {}) => {
	let error: string | null = null;
	const headers = new Headers(init.headers);
	headers.set('Accept', 'application/json');
	headers.set('Content-Type', 'application/json');
	headers.set('Authorization', `Bearer ${token}`);
	const res = await fetch(`${OPENAI_API_BASE_URL}${path}`, {
		...init,
		headers
	})
		.then(async (response) => {
			if (!response.ok) throw await response.json();
			return response.json() as Promise<T>;
		})
		.catch((err) => {
			error = err?.detail ?? err?.error?.message ?? 'Server connection failed';
			return null;
		});

	if (error) throw error;
	return res as T;
};

export const getPersonalAPIKeyConnections = async (token: string) =>
	personalAPIKeyRequest<PersonalAPIKeyConnectionsResponse>(token, '/personal-api-keys');

export const createPersonalAPIKey = async (token: string, form: PersonalAPIKeyForm) =>
	personalAPIKeyRequest<PersonalAPIKeyConnection>(token, '/personal-api-keys', {
		method: 'POST',
		body: JSON.stringify(form)
	});

export const updatePersonalAPIKey = async (
	token: string,
	connectionId: string,
	form: PersonalAPIKeyForm
) =>
	personalAPIKeyRequest<PersonalAPIKeyConnection>(
		token,
		`/personal-api-keys/${encodeURIComponent(connectionId)}`,
		{
			method: 'PUT',
			body: JSON.stringify(form)
		}
	);

export const deletePersonalAPIKey = async (token: string, connectionId: string) =>
	personalAPIKeyRequest<{ ok: boolean; mode: 'personal' | 'platform' }>(
		token,
		`/personal-api-keys/${encodeURIComponent(connectionId)}`,
		{ method: 'DELETE' }
	);

export const verifyPersonalAPIKey = async (token: string, connectionId: string) =>
	personalAPIKeyRequest<{ ok: boolean; message: string }>(
		token,
		`/personal-api-keys/${encodeURIComponent(connectionId)}/verify`,
		{ method: 'POST' }
	);

export const updateOpenAIConfig = async (token: string = '', config: OpenAIConfig) => {
	let error = null;

	const res = await fetch(`${OPENAI_API_BASE_URL}/config/update`, {
		method: 'POST',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(token && { authorization: `Bearer ${token}` })
		},
		body: JSON.stringify({
			...config
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			if ('detail' in err) {
				error = err.detail;
			} else {
				error = 'Server connection failed';
			}
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getOpenAIModelsDirect = async (url: string, key: string) => {
	let error = null;

	const res = await fetch(`${url}/models`, {
		method: 'GET',
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			...(key && { authorization: `Bearer ${key}` })
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = `OpenAI: ${err?.error?.message ?? 'Network Problem'}`;
			return [];
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getOpenAIModels = async (token: string, urlIdx?: number) => {
	let error = null;

	const res = await fetch(
		`${OPENAI_API_BASE_URL}/models${typeof urlIdx === 'number' ? `/${urlIdx}` : ''}`,
		{
			method: 'GET',
			headers: {
				Accept: 'application/json',
				'Content-Type': 'application/json',
				...(token && { authorization: `Bearer ${token}` })
			}
		}
	)
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = `OpenAI: ${err?.error?.message ?? 'Network Problem'}`;
			return [];
		});

	if (error) {
		throw error;
	}

	return res;
};

export const verifyOpenAIConnection = async (
	token: string = '',
	connection: dict = {},
	direct: boolean = false
) => {
	const { url, key, config } = connection;
	if (!url) {
		throw 'OpenAI: URL is required';
	}

	let error = null;
	let res = null;

	if (direct) {
		res = await fetch(`${url}/models`, {
			method: 'GET',
			headers: {
				Accept: 'application/json',
				Authorization: `Bearer ${key}`,
				'Content-Type': 'application/json'
			}
		})
			.then(async (res) => {
				if (!res.ok) throw await res.json();
				return res.json();
			})
			.catch((err) => {
				error = `OpenAI: ${err?.error?.message ?? 'Network Problem'}`;
				return [];
			});

		if (error) {
			throw error;
		}
	} else {
		res = await fetch(`${OPENAI_API_BASE_URL}/verify`, {
			method: 'POST',
			headers: {
				Accept: 'application/json',
				Authorization: `Bearer ${token}`,
				'Content-Type': 'application/json'
			},
			body: JSON.stringify({
				url,
				key,
				config
			})
		})
			.then(async (res) => {
				if (!res.ok) throw await res.json();
				return res.json();
			})
			.catch((err) => {
				error = `OpenAI: ${err?.error?.message ?? 'Network Problem'}`;
				return [];
			});

		if (error) {
			throw error;
		}
	}

	return res;
};

export const chatCompletion = async (
	token: string = '',
	body: object,
	url: string = `${WEBUI_BASE_URL}/api`
): Promise<[Response | null, AbortController]> => {
	const controller = new AbortController();
	let error = null;

	const res = await fetch(`${url}/chat/completions`, {
		signal: controller.signal,
		method: 'POST',
		headers: {
			Authorization: `Bearer ${token}`,
			'Content-Type': 'application/json'
		},
		body: JSON.stringify(body)
	}).catch((err) => {
		console.error(err);
		error = err;
		return null;
	});

	if (error) {
		throw error;
	}

	return [res, controller];
};

export const generateOpenAIChatCompletion = async (
	token: string = '',
	body: object,
	url: string = `${WEBUI_BASE_URL}/api`
) => {
	let error = null;

	const res = await fetch(`${url}/chat/completions`, {
		method: 'POST',
		headers: {
			Authorization: `Bearer ${token}`,
			'Content-Type': 'application/json'
		},
		credentials: 'include',
		body: JSON.stringify(body)
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err?.detail ?? err;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const synthesizeOpenAISpeech = async (
	token: string = '',
	speaker: string = 'alloy',
	text: string = '',
	model: string = 'tts-1'
) => {
	let error = null;

	const res = await fetch(`${OPENAI_API_BASE_URL}/audio/speech`, {
		method: 'POST',
		headers: {
			Authorization: `Bearer ${token}`,
			'Content-Type': 'application/json'
		},
		body: JSON.stringify({
			model: model,
			input: text,
			voice: speaker
		})
	}).catch((err) => {
		console.error(err);
		error = err;
		return null;
	});

	if (error) {
		throw error;
	}

	return res;
};

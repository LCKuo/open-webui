import type { WorkflowResponse } from '$lib/apis/workflows';

export type WorkflowLaunchMode = 'instant' | 'text_input' | 'form_input' | 'file_input';
export type WorkflowFollowUpMode = 'chat_about_result' | 'rerun_each_message';
export type WorkflowConfirmationMode = 'never' | 'risk_only' | 'always';
export type WorkflowInputFieldType = 'string' | 'integer' | 'number' | 'boolean';

export type WorkflowInputField = {
	type: WorkflowInputFieldType;
	title?: string;
	description?: string;
	format?: 'text' | 'textarea' | 'date' | 'datetime-local' | 'email';
	default?: string | number | boolean;
	enum?: Array<string | number>;
	minLength?: number;
	maxLength?: number;
	minimum?: number;
	maximum?: number;
};

export type WorkflowLaunchConfig = {
	version: 1;
	mode: WorkflowLaunchMode;
	buttonLabel: string;
	instruction: string;
	followUpMode: WorkflowFollowUpMode;
	confirmation: WorkflowConfirmationMode;
	inputSchema: {
		type: 'object';
		properties: Record<string, WorkflowInputField>;
		required: string[];
		additionalProperties: false;
	};
	defaultInput: Record<string, unknown>;
	fileRules: {
		allowedMimeTypes: string[];
		maxFiles: number;
		maxSizeMB: number;
	};
};

export const WORKFLOW_LAUNCH_OPTIONS: Array<{
	value: WorkflowLaunchMode;
	label: string;
	description: string;
}> = [
	{
		value: 'instant',
		label: '立即執行',
		description: '執行所需資料已有預設值，點擊後直接開始。'
	},
	{
		value: 'text_input',
		label: '等待文字',
		description: '使用者先輸入問題或指令，再執行一次工作流。'
	},
	{
		value: 'form_input',
		label: '填寫條件',
		description: '依輸入規格顯示表單，通過檢查後執行。'
	},
	{
		value: 'file_input',
		label: '等待檔案',
		description: '使用者上傳指定格式與數量的檔案後執行。'
	}
];

const LABELS: Record<WorkflowLaunchMode, string> = {
	instant: '立即執行',
	text_input: '輸入問題',
	form_input: '填寫條件',
	file_input: '上傳檔案'
};

const INSTRUCTIONS: Record<WorkflowLaunchMode, string> = {
	instant: '這個工作流已有執行所需資料，點擊後會直接開始。',
	text_input: '輸入要交給工作流處理的內容。',
	form_input: '填寫必要條件後開始執行。',
	file_input: '上傳符合要求的檔案，可另外輸入處理要求。'
};

const inferredMode = (workflow: Pick<WorkflowResponse, 'graph'>): WorkflowLaunchMode => {
	const types = new Set(
		(workflow.graph?.nodes ?? []).map((node) => node?.data?.type ?? node?.data?.kind).filter(Boolean)
	);
	if (types.has('file_upload')) return 'file_input';
	if (types.has('form_input')) return 'form_input';
	if (types.has('schedule_trigger') || types.has('webhook_trigger')) return 'instant';
	return 'text_input';
};

const boundedInteger = (value: unknown, fallback: number, minimum: number, maximum: number) => {
	const parsed = Number(value);
	return Number.isFinite(parsed)
		? Math.min(maximum, Math.max(minimum, Math.trunc(parsed)))
		: fallback;
};

export const normalizeWorkflowLaunch = (
	workflow: Pick<WorkflowResponse, 'graph' | 'meta'>
): WorkflowLaunchConfig => {
	const raw = workflow.meta?.launch ?? {};
	const mode = WORKFLOW_LAUNCH_OPTIONS.some((item) => item.value === raw.mode)
		? (raw.mode as WorkflowLaunchMode)
		: inferredMode(workflow);
	const defaultSchema =
		mode === 'text_input'
			? {
					type: 'object' as const,
					properties: {
						message: {
							type: 'string' as const,
							title: '訊息',
							description: '輸入要交給工作流處理的內容。',
							minLength: 1
						}
					},
					required: ['message'],
					additionalProperties: false as const
				}
			: {
					type: 'object' as const,
					properties: {},
					required: [],
					additionalProperties: false as const
				};
	const schema = raw.inputSchema && typeof raw.inputSchema === 'object' ? raw.inputSchema : defaultSchema;
	return {
		version: 1,
		mode,
		buttonLabel: String(raw.buttonLabel || LABELS[mode]),
		instruction: String(raw.instruction || INSTRUCTIONS[mode]),
		followUpMode: ['chat_about_result', 'rerun_each_message'].includes(raw.followUpMode)
			? raw.followUpMode
			: 'chat_about_result',
		confirmation: ['never', 'risk_only', 'always'].includes(raw.confirmation)
			? raw.confirmation
			: 'risk_only',
		inputSchema: {
			type: 'object',
			properties:
				schema.properties && typeof schema.properties === 'object' ? schema.properties : {},
			required: Array.isArray(schema.required) ? schema.required.filter(Boolean) : [],
			additionalProperties: false
		},
		defaultInput:
			raw.defaultInput && typeof raw.defaultInput === 'object' && !Array.isArray(raw.defaultInput)
				? raw.defaultInput
				: {},
		fileRules: {
			allowedMimeTypes: Array.isArray(raw.fileRules?.allowedMimeTypes)
				? raw.fileRules.allowedMimeTypes.filter(Boolean)
				: ['image/*', 'audio/*', 'video/*', 'application/pdf', 'text/*'],
			maxFiles: boundedInteger(raw.fileRules?.maxFiles, 5, 1, 20),
			maxSizeMB: boundedInteger(raw.fileRules?.maxSizeMB, 25, 1, 500)
		}
	};
};

export const workflowLaunchLabel = (workflow: Pick<WorkflowResponse, 'graph' | 'meta'>) =>
	normalizeWorkflowLaunch(workflow).buttonLabel;

export const workflowLaunchSummary = (workflow: Pick<WorkflowResponse, 'graph' | 'meta'>) => {
	const launch = normalizeWorkflowLaunch(workflow);
	return WORKFLOW_LAUNCH_OPTIONS.find((item) => item.value === launch.mode)?.label ?? launch.buttonLabel;
};

export const buildWorkflowLaunchInput = (
	launch: WorkflowLaunchConfig,
	values: Record<string, unknown>,
	message: string,
	files: any[]
) => {
	const merged = { ...launch.defaultInput, ...values };
	const resolvedMessage = String(message || merged.message || '');
	delete merged.message;
	return { message: resolvedMessage, data: merged, files };
};

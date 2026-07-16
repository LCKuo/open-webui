import { describe, expect, it } from 'vitest';

import { buildWorkflowLaunchInput, normalizeWorkflowLaunch } from './workflowLaunch';

const workflow = (type: string, launch?: Record<string, unknown>) =>
	({
		graph: { nodes: [{ id: 'start', data: { type } }], edges: [] },
		meta: launch ? { launch } : {}
	}) as any;

describe('workflow launch contract', () => {
	it('infers file input for legacy file workflows', () => {
		expect(normalizeWorkflowLaunch(workflow('file_upload')).mode).toBe('file_input');
	});

	it('preserves an explicit instant launch contract', () => {
		const launch = normalizeWorkflowLaunch(
			workflow('channel_input', {
				mode: 'instant',
				buttonLabel: '查詢排名',
				defaultInput: { limit: 5 }
			})
		);

		expect(launch.mode).toBe('instant');
		expect(launch.buttonLabel).toBe('查詢排名');
		expect(launch.defaultInput).toEqual({ limit: 5 });
	});

	it('merges defaults while keeping the current form values', () => {
		const launch = normalizeWorkflowLaunch(
			workflow('form_input', {
				mode: 'form_input',
				defaultInput: { limit: 5, department: 'all' }
			})
		);

		expect(buildWorkflowLaunchInput(launch, { limit: 3 }, '執行', [])).toEqual({
			message: '執行',
			data: { limit: 3, department: 'all' },
			files: []
		});
	});
});

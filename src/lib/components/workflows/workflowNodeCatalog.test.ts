import { describe, expect, it } from 'vitest';

import { buildWorkflowTemplateGraph } from './workflowNodeCatalog';

describe('AI prospect discovery template', () => {
	it('returns verified candidate JSON together with the actual search sources', () => {
		const graph = buildWorkflowTemplateGraph('ai-prospect-discovery');
		const byType = new Map(graph.nodes.map((node) => [node.data.type, node]));
		const guidance = byType.get('user_input');
		const search = byType.get('web_search');
		const parser = byType.get('json_parse');
		const enrichment = byType.get('prospect_contact_enrichment');
		const merge = byType.get('merge');
		const output = byType.get('webhook_response');
		const instructions = byType.get('system_prompt');

		expect(guidance?.data.config?.launch).toEqual(
			expect.objectContaining({
				mode: 'form_input',
				confirmation: 'never'
			})
		);
		expect(search).toBeDefined();
		expect(parser).toBeDefined();
		expect(enrichment).toBeDefined();
		expect(merge).toBeDefined();
		expect(output).toBeDefined();
		expect(graph.edges).toEqual(
			expect.arrayContaining([
				expect.objectContaining({ source: search?.id, target: merge?.id }),
				expect.objectContaining({ source: parser?.id, target: enrichment?.id }),
				expect.objectContaining({ source: enrichment?.id, target: merge?.id }),
				expect.objectContaining({ source: merge?.id, target: output?.id })
			])
		);
		expect(instructions?.data.config?.text).toContain(
			'evidence.url 必須逐字複製本次搜尋結果中的 URL'
		);
		expect(instructions?.data.config?.text).toContain('"contacts"');
	});

	it('builds a one-recipient campaign workflow with approval and delivery summary', () => {
		const graph = buildWorkflowTemplateGraph('crm-prospect-email-campaign');
		const types = graph.nodes.map((node) => node.data.type);

		expect(types).toEqual([
			'form_input',
			'email_campaign_compose',
			'campaign_approval_gate',
			'email_campaign_send',
			'campaign_delivery_summary',
			'webhook_response'
		]);
		expect(graph.purpose).toBe('prospecting_email_campaign');
	});
});

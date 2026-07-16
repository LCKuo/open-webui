<script lang="ts">
	import { Handle, Position } from '@xyflow/svelte';
	import Bolt from '$lib/components/icons/Bolt.svelte';
	import ChatBubble from '$lib/components/icons/ChatBubble.svelte';
	import Database from '$lib/components/icons/Database.svelte';
	import Merge from '$lib/components/icons/Merge.svelte';
	import Sparkles from '$lib/components/icons/Sparkles.svelte';
	import Wrench from '$lib/components/icons/Wrench.svelte';

	export let data: Record<string, any> = {};
	export let selected = false;

	const typeLabel = (value: string) =>
		({
			none: '',
			message: '訊息',
			data: '資料',
			media: '媒體',
			any: '任意'
		})[value] ?? value;

	$: hasInput = data.inputType !== 'none';
	$: hasOutput = data.outputType !== 'none';
	$: config = data.config && typeof data.config === 'object' ? data.config : {};
	$: summary =
		config.model_id ||
		config.dataset_id ||
		config.table ||
		config.output_type ||
		(config.knowledge_ids?.length ? `${config.knowledge_ids.length} 個知識庫` : '') ||
		'';
</script>

{#if hasInput}
	<Handle
		type="target"
		position={Position.Left}
		class="workflow-port workflow-port-{data.inputType ?? 'any'}"
	/>
{/if}

<div
	class="node-card node-{data.category ?? 'control'} {selected ? 'is-selected' : ''}"
	data-node-type={data.type}
>
	<div class="node-header">
		<span class="node-icon" aria-hidden="true">
			{#if data.category === 'start'}
				<Bolt className="size-4" />
			{:else if data.category === 'ai'}
				<Sparkles className="size-4" />
			{:else if data.category === 'knowledge'}
				<Database className="size-4" />
			{:else if data.category === 'transform'}
				<Wrench className="size-4" />
			{:else if data.category === 'output'}
				<ChatBubble className="size-4" />
			{:else}
				<Merge className="size-4" />
			{/if}
		</span>
		<div class="min-w-0 flex-1">
			<div class="node-label">{data.label || data.type || '工作流節點'}</div>
			<div class="node-type">{data.type}</div>
		</div>
	</div>

	<div class="node-description">{data.description || '尚未提供節點說明。'}</div>

	<div class="node-footer">
		<span class="port-label">{hasInput ? typeLabel(data.inputType) : '開始'}</span>
		{#if summary}
			<span class="node-summary" title={summary}>{summary}</span>
		{:else}
			<span class="node-summary muted">選取以設定</span>
		{/if}
		<span class="port-label">{hasOutput ? typeLabel(data.outputType) : '完成'}</span>
	</div>
</div>

{#if hasOutput}
	<Handle
		type="source"
		position={Position.Right}
		class="workflow-port workflow-port-{data.outputType ?? 'any'}"
	/>
{/if}

<style>
	.node-card {
		width: 264px;
		border: 1px solid #cbd5e1;
		border-left-width: 4px;
		border-radius: 8px;
		background: #ffffff;
		color: #0f172a;
		box-shadow: 0 8px 22px rgb(15 23 42 / 0.1);
		overflow: hidden;
	}

	.node-card.is-selected {
		box-shadow:
			0 0 0 3px rgb(37 99 235 / 0.24),
			0 12px 28px rgb(15 23 42 / 0.16);
	}

	.node-start {
		border-left-color: #0284c7;
	}

	.node-ai {
		border-left-color: #7c3aed;
	}

	.node-knowledge {
		border-left-color: #0f766e;
	}

	.node-transform {
		border-left-color: #b45309;
	}

	.node-control {
		border-left-color: #475569;
	}

	.node-output {
		border-left-color: #047857;
	}

	.node-header {
		display: flex;
		align-items: center;
		gap: 10px;
		padding: 12px 13px 9px;
	}

	.node-icon {
		display: inline-flex;
		height: 30px;
		width: 30px;
		flex: 0 0 auto;
		align-items: center;
		justify-content: center;
		border-radius: 7px;
		background: #f1f5f9;
		color: #334155;
	}

	.node-label {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-size: 14px;
		font-weight: 700;
		line-height: 1.25;
	}

	.node-type {
		margin-top: 2px;
		font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
		font-size: 10px;
		color: #64748b;
	}

	.node-description {
		min-height: 42px;
		padding: 0 13px 11px;
		font-size: 12px;
		line-height: 1.45;
		color: #475569;
	}

	.node-footer {
		display: grid;
		grid-template-columns: auto minmax(0, 1fr) auto;
		align-items: center;
		gap: 8px;
		border-top: 1px solid #e2e8f0;
		background: #f8fafc;
		padding: 7px 10px;
	}

	.port-label {
		font-size: 10px;
		font-weight: 650;
		color: #64748b;
	}

	.node-summary {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		text-align: center;
		font-size: 10px;
		font-weight: 600;
		color: #334155;
	}

	.node-summary.muted {
		font-weight: 500;
		color: #94a3b8;
	}

	:global(.workflow-port) {
		width: 13px !important;
		height: 13px !important;
		border: 2px solid #ffffff !important;
		background: #64748b !important;
		box-shadow: 0 0 0 2px rgb(100 116 139 / 0.28);
	}

	:global(.workflow-port-message) {
		background: #4f46e5 !important;
	}

	:global(.workflow-port-data) {
		background: #dc2626 !important;
	}

	:global(.workflow-port-media) {
		background: #0891b2 !important;
	}

	:global(.dark) .node-card {
		border-color: #475569;
		background: #111827;
		color: #f8fafc;
		box-shadow: 0 10px 28px rgb(0 0 0 / 0.36);
	}

	:global(.dark) .node-start {
		border-left-color: #38bdf8;
	}

	:global(.dark) .node-ai {
		border-left-color: #c4b5fd;
	}

	:global(.dark) .node-knowledge {
		border-left-color: #5eead4;
	}

	:global(.dark) .node-transform {
		border-left-color: #fbbf24;
	}

	:global(.dark) .node-control {
		border-left-color: #cbd5e1;
	}

	:global(.dark) .node-output {
		border-left-color: #6ee7b7;
	}

	:global(.dark) .node-icon,
	:global(.dark) .node-footer {
		background: #1e293b;
	}

	:global(.dark) .node-icon,
	:global(.dark) .node-summary {
		color: #e2e8f0;
	}

	:global(.dark) .node-description,
	:global(.dark) .node-type,
	:global(.dark) .port-label {
		color: #cbd5e1;
	}

	:global(.dark) .node-footer {
		border-top-color: #334155;
	}

	:global(.dark) .workflow-port {
		border-color: #020617 !important;
	}
</style>

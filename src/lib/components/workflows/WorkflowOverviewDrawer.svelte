<script lang="ts">
	import { tick } from 'svelte';
	import type { WorkflowResponse } from '$lib/apis/workflows';
	import { models } from '$lib/stores';
	import { normalizeWorkflowLaunch, workflowLaunchSummary } from './workflowLaunch';
	import { WORKFLOW_NODE_BY_TYPE } from './workflowNodeCatalog';
	import XMark from '$lib/components/icons/XMark.svelte';

	export let workflow: WorkflowResponse | null = null;
	export let canManage = false;
	export let onClose: () => void = () => {};
	export let onUse: (workflow: WorkflowResponse) => void = () => {};
	export let onEdit: (workflow: WorkflowResponse) => void = () => {};
	export let onArchive: (workflow: WorkflowResponse) => void = () => {};
	export let onActivate: (workflow: WorkflowResponse) => void = () => {};
	export let lifecycleBusy = false;
	let dialogElement: HTMLDivElement | null = null;

	$: if (workflow) {
		tick().then(() => dialogElement?.focus());
	}

	const modelLabel = (modelId: string) =>
		$models?.find((model) => model.id === modelId)?.name || modelId;
	const nodeLabel = (type: string) => WORKFLOW_NODE_BY_TYPE.get(type)?.label || type;

	$: nodes = workflow?.graph?.nodes ?? [];
	$: nodeTypes = [...new Set(nodes.map((node) => node?.data?.type).filter(Boolean))];
	$: configuredModels = [
		...new Set(nodes.map((node) => node?.data?.config?.model_id).filter(Boolean))
	];
	$: allowedModels = workflow?.meta?.acl?.allowed_model_ids ?? [];
	$: channels = workflow?.meta?.acl?.allowed_channel_ids ?? workflow?.meta?.channels ?? [];
	$: inputTypes = nodeTypes.filter((type) =>
		[
			'input',
			'chat_input',
			'channel_input',
			'webhook_trigger',
			'schedule_trigger',
			'file_upload',
			'form_input'
		].includes(type)
	);
	$: outputTypes = nodeTypes.filter((type) =>
		[
			'output',
			'chat_output',
			'channel_reply',
			'media_output',
			'file_output',
			'handoff',
			'webhook_response',
			'notification'
		].includes(type)
	);
	$: hasMedia = nodeTypes.some((type) =>
		[
			'file_upload',
			'vision_model',
			'image_model',
			'speech_to_text',
			'text_to_speech',
			'media_output',
			'file_output'
		].includes(type)
	);
	$: launch = workflow ? normalizeWorkflowLaunch(workflow) : null;
</script>

<svelte:window on:keydown={(event) => event.key === 'Escape' && workflow && onClose()} />

{#if workflow}
	<div class="fixed inset-0 z-[1000] flex justify-end">
		<button
			type="button"
			class="absolute inset-0 bg-black/45"
			aria-label="關閉工作流概覽"
			on:click={onClose}
		></button>
		<div
			bind:this={dialogElement}
			class="relative z-10 h-full w-full max-w-xl overflow-y-auto bg-white shadow-2xl dark:bg-gray-950"
			role="dialog"
			aria-modal="true"
			aria-label="工作流概覽"
			tabindex="-1"
		>
			<header
				class="sticky top-0 z-10 flex items-start justify-between gap-4 border-b border-gray-200 bg-white px-5 py-4 dark:border-gray-800 dark:bg-gray-950"
			>
				<div class="min-w-0">
					<div class="text-xs font-semibold text-gray-500">企業工作流概覽</div>
					<h2 class="mt-1 truncate text-xl font-semibold text-gray-900 dark:text-gray-100">
						{workflow.name}
					</h2>
				</div>
				<button
					class="rounded-lg p-2 text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-900"
					aria-label="關閉概覽"
					title="關閉概覽"
					on:click={onClose}
				>
					<XMark className="size-4" />
				</button>
			</header>

			<div class="space-y-6 p-5">
				<section>
					<h3 class="text-sm font-semibold text-gray-900 dark:text-gray-100">用途</h3>
					<p class="mt-2 whitespace-pre-wrap text-sm leading-6 text-gray-600 dark:text-gray-300">
						{workflow.description || '建立者尚未提供功能說明。'}
					</p>
				</section>

				<div class="grid grid-cols-2 gap-3 text-sm">
					<div class="rounded-lg border border-gray-200 p-3 dark:border-gray-800">
						<div class="text-xs text-gray-500">狀態</div>
						<div class="mt-1 font-medium">
							{workflow.status === 'published'
								? '已發布'
								: workflow.status === 'archived'
									? '已停用'
									: '草稿'}
						</div>
					</div>
					<div class="rounded-lg border border-gray-200 p-3 dark:border-gray-800">
						<div class="text-xs text-gray-500">版本</div>
						<div class="mt-1 font-medium">
							{workflow.default_version_id ? '固定發布版本' : '尚未發布'}
						</div>
					</div>
					<div class="rounded-lg border border-gray-200 p-3 dark:border-gray-800">
						<div class="text-xs text-gray-500">節點</div>
						<div class="mt-1 font-medium">{nodes.length} 個</div>
					</div>
					<div class="rounded-lg border border-gray-200 p-3 dark:border-gray-800">
						<div class="text-xs text-gray-500">多媒體</div>
						<div class="mt-1 font-medium">{hasMedia ? '可帶入或回傳' : '文字／JSON'}</div>
					</div>
				</div>

				<section class="space-y-3">
					<h3 class="text-sm font-semibold text-gray-900 dark:text-gray-100">模型</h3>
					{#if configuredModels.length}
						{#each configuredModels as modelId}<div
								class="rounded-lg bg-gray-50 px-3 py-2 text-sm dark:bg-gray-900"
							>
								<span class="text-gray-500">固定模型：</span>{modelLabel(modelId)}
								<span class="ml-1 text-xs text-gray-400">({modelId})</span>
							</div>{/each}
					{:else}
						<div class="rounded-lg bg-gray-50 px-3 py-2 text-sm dark:bg-gray-900">
							沿用使用者在聊天中選擇的模型
						</div>
					{/if}
					{#if allowedModels.length}<div class="text-xs text-gray-500">
							允許模型：{allowedModels.join(', ')}
						</div>{/if}
				</section>

				<section class="space-y-2">
					<h3 class="text-sm font-semibold text-gray-900 dark:text-gray-100">啟動方式</h3>
					<div class="rounded-lg bg-blue-50 px-3 py-2 dark:bg-blue-950/40">
						<div class="text-sm font-medium text-blue-800 dark:text-blue-100">
							{workflowLaunchSummary(workflow)}
						</div>
						<div class="mt-1 text-xs leading-5 text-blue-700 dark:text-blue-200">
							{launch?.instruction}
						</div>
					</div>
				</section>

				<section class="space-y-2">
					<h3 class="text-sm font-semibold text-gray-900 dark:text-gray-100">互動能力</h3>
					<div class="flex flex-wrap gap-2">
						{#each inputTypes as type}<span
								class="rounded-full bg-blue-50 px-2.5 py-1 text-xs text-blue-700 dark:bg-blue-950 dark:text-blue-200"
								>輸入 · {nodeLabel(type)}</span
							>{/each}
						{#each outputTypes as type}<span
								class="rounded-full bg-green-50 px-2.5 py-1 text-xs text-green-700 dark:bg-green-950 dark:text-green-200"
								>輸出 · {nodeLabel(type)}</span
							>{/each}
					</div>
					{#if channels.length}<div class="text-xs text-gray-500">
							限制頻道：{channels.join(', ')}
						</div>{/if}
				</section>

				<section>
					<h3 class="text-sm font-semibold text-gray-900 dark:text-gray-100">主要步驟</h3>
					<ol class="mt-2 space-y-2">
						{#each nodes.slice(0, 12) as node, index}
							<li class="flex gap-3 text-sm">
								<span class="text-gray-400">{index + 1}</span><span
									><span class="font-medium"
										>{node?.data?.label || nodeLabel(node?.data?.type || '') || node.id}</span
									>{#if node?.data?.description}<span
											class="mt-0.5 block text-xs leading-5 text-gray-500"
											>{node.data.description}</span
										>{/if}</span
								>
							</li>
						{/each}
					</ol>
				</section>
			</div>

			<footer
				class="sticky bottom-0 flex gap-2 border-t border-gray-200 bg-white p-4 dark:border-gray-800 dark:bg-gray-950"
			>
				{#if canManage}<button
						class="rounded-lg border border-gray-200 px-4 py-2 text-sm dark:border-gray-800"
						on:click={() => onEdit(workflow)}>編輯</button
					>{/if}
				{#if workflow.status === 'published'}
					{#if canManage}
						<button
							class="rounded-lg border border-amber-300 px-4 py-2 text-sm font-medium text-amber-700 disabled:opacity-50 dark:border-amber-800 dark:text-amber-300"
							disabled={lifecycleBusy}
							on:click={() => onArchive(workflow)}>停用</button
						>
					{/if}
					<button
						class="flex-1 rounded-lg bg-gray-900 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50 dark:bg-white dark:text-gray-900"
						disabled={!workflow.default_version_id || workflow.visibility === 'public_template'}
						title={workflow.visibility === 'public_template'
							? '公開範本需先複製到企業空間才能執行'
							: undefined}
						on:click={() => onUse(workflow)}>{launch?.buttonLabel ?? '在聊天中使用'}</button
					>
				{:else if workflow.status === 'archived' && canManage}
					<button
						class="flex-1 rounded-lg bg-gray-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-white dark:text-gray-900"
						disabled={lifecycleBusy}
						on:click={() => onActivate(workflow)}
					>
						{lifecycleBusy ? '啟用中...' : '重新啟用'}
					</button>
				{/if}
			</footer>
		</div>
	</div>
{/if}

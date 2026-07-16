<script lang="ts">
	import Search from '$lib/components/icons/Search.svelte';
	import Sparkles from '$lib/components/icons/Sparkles.svelte';
	import {
		WORKFLOW_NODE_DEFINITIONS,
		WORKFLOW_NODE_GROUPS,
		WORKFLOW_TEMPLATES,
		type WorkflowNodeCategory,
		type WorkflowNodeDefinition
	} from './workflowNodeCatalog';

	export let canEdit = true;
	export let onAdd: (type: string) => void;
	export let onApplyTemplate: (templateId: string) => void;

	let search = '';
	let category: WorkflowNodeCategory = 'start';
	let view: 'nodes' | 'templates' = 'nodes';

	$: activeGroup = WORKFLOW_NODE_GROUPS.find((group) => group.id === category);
	$: filteredNodes = WORKFLOW_NODE_DEFINITIONS.filter((node) => {
		const term = search.trim().toLocaleLowerCase();
		if (!term) return node.category === category;
		return [node.label, node.type, node.description, ...(node.keywords ?? [])]
			.join(' ')
			.toLocaleLowerCase()
			.includes(term);
	});

	const categoryLabel = (definition: WorkflowNodeDefinition) =>
		WORKFLOW_NODE_GROUPS.find((group) => group.id === definition.category)?.label ?? '';

	const handleDragStart = (event: DragEvent, type: string) => {
		if (!canEdit || !event.dataTransfer) return;
		event.dataTransfer.setData('application/interact-workflow-node', type);
		event.dataTransfer.effectAllowed = 'copy';
	};
</script>

<aside class="node-library border-r border-gray-200 bg-white dark:border-gray-800 dark:bg-gray-950">
	<div class="library-header">
		<div>
			<div class="text-sm font-semibold text-gray-900 dark:text-gray-100">建立工作流</div>
			<div class="mt-1 text-xs leading-5 text-gray-500">拖曳節點到畫布，或按一下直接新增。</div>
		</div>
	</div>

	<div
		class="mx-3 grid grid-cols-2 rounded-lg bg-gray-100 p-1 dark:bg-gray-900"
		aria-label="節點庫檢視"
	>
		<button
			class="rounded-md px-3 py-1.5 text-xs font-medium transition {view === 'nodes'
				? 'bg-white text-gray-900 shadow-sm dark:bg-gray-800 dark:text-white'
				: 'text-gray-500'}"
			on:click={() => (view = 'nodes')}
			aria-pressed={view === 'nodes'}>節點</button
		>
		<button
			class="rounded-md px-3 py-1.5 text-xs font-medium transition {view === 'templates'
				? 'bg-white text-gray-900 shadow-sm dark:bg-gray-800 dark:text-white'
				: 'text-gray-500'}"
			on:click={() => (view = 'templates')}
			aria-pressed={view === 'templates'}>入門範本</button
		>
	</div>

	{#if view === 'nodes'}
		<div class="relative mx-3 mt-3">
			<Search className="pointer-events-none absolute left-3 top-2.5 size-4 text-gray-400" />
			<input
				class="h-9 w-full rounded-lg border border-gray-200 bg-transparent pl-9 pr-3 text-sm outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/15 dark:border-gray-800"
				bind:value={search}
				placeholder="搜尋節點或用途"
				aria-label="搜尋節點"
			/>
		</div>

		<div class="category-list mx-3 mt-3 flex gap-1 overflow-x-auto pb-1 xl:grid xl:grid-cols-2">
			{#each WORKFLOW_NODE_GROUPS as group}
				<button
					class="whitespace-nowrap rounded-md px-2.5 py-2 text-left text-xs font-medium transition {category ===
						group.id && !search.trim()
						? 'bg-blue-50 text-blue-700 dark:bg-blue-950/50 dark:text-blue-200'
						: 'text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-900'}"
					on:click={() => {
						category = group.id;
						search = '';
					}}
					aria-pressed={category === group.id && !search.trim()}
				>
					{group.label}
				</button>
			{/each}
		</div>

		<div class="mx-3 mt-3 rounded-lg bg-gray-50 px-3 py-2 dark:bg-gray-900">
			<div class="text-xs font-semibold text-gray-700 dark:text-gray-200">
				{search.trim() ? `搜尋結果 ${filteredNodes.length}` : activeGroup?.label}
			</div>
			<div class="mt-1 text-xs leading-5 text-gray-500">
				{search.trim() ? '只顯示目前可正式執行的內建節點。' : activeGroup?.description}
			</div>
		</div>

		<div class="library-scroll mt-2 min-h-0 flex-1 overflow-y-auto px-3 pb-4">
			{#if filteredNodes.length === 0}
				<div
					class="mt-2 rounded-lg border border-dashed border-gray-300 p-4 text-sm text-gray-500 dark:border-gray-700"
				>
					找不到符合條件的可執行節點。
				</div>
			{:else}
				<div class="space-y-2 pt-1">
					{#each filteredNodes as definition}
						<button
							class="node-item node-{definition.category} group w-full text-left disabled:cursor-not-allowed disabled:opacity-55"
							draggable={canEdit}
							disabled={!canEdit}
							on:dragstart={(event) => handleDragStart(event, definition.type)}
							on:click={() => onAdd(definition.type)}
							title={`新增${definition.label}`}
						>
							<span class="flex min-w-0 flex-1 flex-col">
								<span class="flex items-center gap-2">
									<span class="truncate text-sm font-semibold text-gray-900 dark:text-gray-100">
										{definition.label}
									</span>
									{#if search.trim()}
										<span
											class="rounded bg-gray-100 px-1.5 py-0.5 text-[10px] text-gray-500 dark:bg-gray-800"
										>
											{categoryLabel(definition)}
										</span>
									{/if}
								</span>
								<span class="mt-1 line-clamp-2 text-xs leading-5 text-gray-500">
									{definition.description}
								</span>
							</span>
							<span
								class="ml-2 text-lg leading-none text-gray-400 group-hover:text-blue-600"
								aria-hidden="true">+</span
							>
						</button>
					{/each}
				</div>
			{/if}
		</div>
	{:else}
		<div class="library-scroll mt-3 min-h-0 flex-1 overflow-y-auto px-3 pb-4">
			<div
				class="mb-3 rounded-lg bg-blue-50 px-3 py-2 text-xs leading-5 text-blue-800 dark:bg-blue-950/40 dark:text-blue-200"
			>
				套用範本會取代目前畫布；有內容時會先要求確認。
			</div>
			<div class="space-y-2">
				{#each WORKFLOW_TEMPLATES as template}
					<button
						class="w-full rounded-lg border border-gray-200 p-3 text-left transition hover:border-blue-400 hover:bg-blue-50/50 disabled:opacity-50 dark:border-gray-800 dark:hover:border-blue-700 dark:hover:bg-blue-950/20"
						disabled={!canEdit}
						on:click={() => onApplyTemplate(template.id)}
					>
						<span class="flex items-start gap-2">
							<span
								class="mt-0.5 rounded-md bg-blue-50 p-1.5 text-blue-700 dark:bg-blue-950 dark:text-blue-200"
							>
								<Sparkles className="size-4" />
							</span>
							<span class="min-w-0">
								<span class="block text-sm font-semibold text-gray-900 dark:text-gray-100"
									>{template.name}</span
								>
								<span class="mt-1 block text-xs leading-5 text-gray-500"
									>{template.description}</span
								>
								<span class="mt-2 block text-[11px] text-gray-400"
									>{template.nodeTypes.length} 個節點</span
								>
							</span>
						</span>
					</button>
				{/each}
			</div>
		</div>
	{/if}
</aside>

<style>
	.node-library {
		display: flex;
		min-height: 0;
		flex-direction: column;
		overflow: hidden;
	}

	.library-header {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		padding: 14px 12px 10px;
	}

	.category-list,
	.library-scroll {
		scrollbar-width: thin;
	}

	.node-item {
		display: flex;
		align-items: flex-start;
		gap: 8px;
		border: 1px solid #e2e8f0;
		border-left-width: 4px;
		border-radius: 8px;
		background: #ffffff;
		padding: 10px 11px;
		transition:
			border-color 0.15s ease,
			background 0.15s ease,
			box-shadow 0.15s ease;
	}

	.node-item:hover {
		border-color: #93c5fd;
		background: #eff6ff;
		box-shadow: 0 5px 16px rgb(15 23 42 / 0.08);
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

	:global(.dark) .node-item {
		border-color: #334155;
		background: #111827;
	}

	:global(.dark) .node-item:hover {
		border-color: #3b82f6;
		background: #172554;
	}
</style>

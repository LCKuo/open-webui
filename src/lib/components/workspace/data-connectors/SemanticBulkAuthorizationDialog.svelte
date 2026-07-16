<script lang="ts">
	import { createEventDispatcher, onMount } from 'svelte';
	import type { BulkCatalogAuthorizationResult } from '$lib/apis/interact-semantic';

	export let authorized = true;
	export let scopeLabel = '全部資料表';
	export let preview: BulkCatalogAuthorizationResult;
	export let busy = false;
	export let error = '';

	const dispatch = createEventDispatcher<{ apply: void; close: void }>();
	let dialog: HTMLDialogElement;
	let acknowledged = false;

	const onCancel = (event: Event) => {
		event.preventDefault();
		if (!busy) dispatch('close');
	};

	onMount(() => dialog.showModal());
</script>

<dialog
	bind:this={dialog}
	class="fixed left-1/2 top-1/2 m-0 max-h-[calc(100dvh-2rem)] w-[calc(100%-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 overflow-y-auto overscroll-contain rounded-lg border-0 bg-white p-5 text-gray-900 shadow-xl backdrop:bg-black/50 dark:bg-gray-900 dark:text-gray-100"
	aria-labelledby="bulk-authorization-title"
	aria-describedby="bulk-authorization-description"
	on:cancel={onCancel}
>
	<div class="flex items-start justify-between gap-4">
		<div>
			<p class="text-xs font-medium text-gray-500">批次權限設定 · {scopeLabel}</p>
			<h2 id="bulk-authorization-title" class="mt-1 text-lg font-semibold">
				{authorized ? '完整授權' : '解除全部授權'}
			</h2>
		</div>
		<button
			type="button"
			class="rounded-lg px-2 py-1 text-sm text-gray-500 hover:bg-gray-100 disabled:opacity-50 dark:hover:bg-gray-800"
			disabled={busy}
			on:click={() => dispatch('close')}>關閉</button
		>
	</div>

	<p id="bulk-authorization-description" class="mt-4 text-sm text-gray-600 dark:text-gray-300">
		{authorized
			? '這會啟用資料表，並將所有欄位設為可讀、可篩選、可分組及可聚合。'
			: '這會停用資料表，並關閉所有欄位的可讀、可篩選、可分組及可聚合權限。'}
	</p>

	<dl
		class="mt-4 grid grid-cols-2 gap-3 border-y border-gray-200 py-4 text-sm dark:border-gray-700"
	>
		<div>
			<dt class="text-xs text-gray-500">資料表</dt>
			<dd class="mt-1 text-lg font-semibold">{preview.object_count}</dd>
		</div>
		<div>
			<dt class="text-xs text-gray-500">欄位</dt>
			<dd class="mt-1 text-lg font-semibold">{preview.field_count}</dd>
		</div>
	</dl>

	<p class="mt-3 text-xs text-gray-500">敏感度、遮罩規則、顯示名稱與關聯設定都不會被修改。</p>

	{#if !authorized}
		<div
			class="mt-4 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-900 dark:border-red-900 dark:bg-red-950/30 dark:text-red-200"
		>
			<p class="font-medium">
				將影響 {preview.affected_datasets.length} 個已發布資料集
			</p>
			{#if preview.affected_datasets.length}
				<ul class="mt-2 max-h-32 space-y-1 overflow-auto text-xs">
					{#each preview.affected_datasets as dataset}
						<li>{dataset.name}</li>
					{/each}
				</ul>
				<p class="mt-2 text-xs">套用後這些資料集會標記為 blocked，直到重新授權、驗證並發布。</p>
			{:else}
				<p class="mt-1 text-xs">目前沒有已發布資料集使用這個範圍。</p>
			{/if}
		</div>

		<label class="mt-4 flex items-start gap-2 text-sm">
			<input type="checkbox" class="mt-0.5 h-4 w-4" bind:checked={acknowledged} />
			<span>我了解這會立即關閉查詢權限，並可能中斷 Agent、渠道或工作流的資料查詢。</span>
		</label>
	{:else}
		<p
			class="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200"
		>
			完整授權範圍很廣。建議只在確認此 Connector 內所有欄位都可供 AI
			使用時套用；敏感欄位仍會沿用既有遮罩規則。先前已 blocked
			的資料集不會自動發布，仍須重新驗證並發布。
		</p>
	{/if}

	{#if error}<p class="mt-3 text-sm text-red-600" role="alert">{error}</p>{/if}

	<div class="mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
		<button
			type="button"
			class="h-10 rounded-lg border border-gray-300 px-4 text-sm font-medium disabled:opacity-50 dark:border-gray-700"
			disabled={busy}
			on:click={() => dispatch('close')}>取消</button
		>
		<button
			type="button"
			class="h-10 rounded-lg px-4 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50 {authorized
				? 'bg-sky-600'
				: 'bg-red-600'}"
			disabled={busy || (!authorized && !acknowledged)}
			on:click={() => dispatch('apply')}
		>
			{busy ? '套用中...' : authorized ? '確認完整授權' : '確認解除全部授權'}
		</button>
	</div>
</dialog>

<script lang="ts">
	import { createEventDispatcher, onMount } from 'svelte';
	import type { SemanticPermissionChange } from '$lib/apis/interact-semantic';

	export let changes: SemanticPermissionChange[] = [];
	export let index = 0;
	export let busy = false;
	export let error = '';

	const dispatch = createEventDispatcher<{
		accept: SemanticPermissionChange;
		skip: SemanticPermissionChange;
		close: void;
	}>();
	let dialog: HTMLDialogElement;

	$: change = changes[index];
	$: target = change?.field ? `${change.object}.${change.field}` : change?.object;

	const permissionLabels: Record<SemanticPermissionChange['permission'], string> = {
		enabled: '允許資料表用於語意查詢',
		readable: '允許讀取欄位',
		filterable: '允許作為篩選條件',
		groupable: '允許分組與排名',
		aggregatable: '允許聚合計算'
	};

	const onCancel = (event: Event) => {
		event.preventDefault();
		if (!busy) dispatch('close');
	};

	onMount(() => dialog.showModal());
</script>

{#if change}
	<dialog
		bind:this={dialog}
		class="fixed left-1/2 top-1/2 m-0 max-h-[calc(100dvh-2rem)] w-[calc(100%-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 overflow-y-auto overscroll-contain rounded-lg border-0 bg-white p-5 text-gray-900 shadow-xl backdrop:bg-black/50 dark:bg-gray-900 dark:text-gray-100"
		aria-labelledby="permission-review-title"
		aria-describedby="permission-review-description"
		on:cancel={onCancel}
	>
		<div class="flex items-start justify-between gap-4">
			<div>
				<p class="text-xs font-medium text-gray-500">權限審核 {index + 1} / {changes.length}</p>
				<h2 id="permission-review-title" class="mt-1 text-lg font-semibold">
					{change.action === 'grant' ? '建議開啟權限' : '建議關閉權限'}
				</h2>
			</div>
			<button
				type="button"
				class="rounded-lg px-2 py-1 text-sm text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800"
				aria-label="暫停權限審核"
				disabled={busy}
				on:click={() => dispatch('close')}>關閉</button
			>
		</div>

		<div class="mt-4 space-y-4">
			<div class="space-y-1 border-y border-gray-200 py-3 text-sm dark:border-gray-700">
				<p><span class="text-gray-500">對象：</span><span class="font-mono">{target}</span></p>
				<p><span class="text-gray-500">權限：</span>{permissionLabels[change.permission]}</p>
				<p>
					<span class="text-gray-500">變更：</span>
					<span>{change.current ? '已開啟' : '已關閉'} → {change.desired ? '開啟' : '關閉'}</span>
				</p>
				<p>
					<span class="text-gray-500">來源：</span>
					{change.source === 'system'
						? '系統最低需求'
						: change.source === 'ai'
							? 'AI 建議'
							: '系統需求與 AI 建議'}
				</p>
			</div>

			<div>
				<h3 class="text-sm font-medium">為什麼建議這項變更</h3>
				<p id="permission-review-description" class="mt-1 text-sm text-gray-600 dark:text-gray-300">
					{change.reason}
				</p>
				{#if change.requiredBy.length}
					<p class="mt-2 text-xs text-gray-500">用途：{change.requiredBy.join('、')}</p>
				{/if}
			</div>

			<p
				class="rounded-lg border p-3 text-sm {change.status === 'conflict' ||
				change.action === 'revoke'
					? 'border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200'
					: 'border-sky-200 bg-sky-50 text-sky-900 dark:border-sky-900 dark:bg-sky-950/30 dark:text-sky-200'}"
			>
				{change.status === 'conflict'
					? '這項關閉建議與本資料集的必要權限衝突，系統不允許套用。'
					: change.impact}
			</p>

			{#if error}<p class="text-sm text-red-600" role="alert">{error}</p>{/if}
		</div>

		<div class="mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
			<button
				type="button"
				class="h-10 rounded-lg border border-gray-300 px-4 text-sm font-medium disabled:opacity-50 dark:border-gray-700"
				disabled={busy}
				on:click={() => dispatch('skip', change)}
			>
				{change.status === 'conflict'
					? '略過衝突建議'
					: change.required
						? '不授權，暫不匯入'
						: '略過這項建議'}
			</button>
			{#if change.status !== 'conflict'}
				<button
					type="button"
					class="h-10 rounded-lg px-4 text-sm font-medium text-white disabled:opacity-50 {change.action ===
					'revoke'
						? 'bg-red-600'
						: 'bg-sky-600'}"
					disabled={busy}
					on:click={() => dispatch('accept', change)}
				>
					{busy ? '套用中...' : change.action === 'grant' ? '同意並開啟' : '同意並關閉'}
				</button>
			{/if}
		</div>
	</dialog>
{/if}

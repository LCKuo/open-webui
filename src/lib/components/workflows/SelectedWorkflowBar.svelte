<script lang="ts">
	export let name = '';
	export let description = '';
	export let modelLabel = '沿用目前聊天模型';
	export let onClear: () => void = () => {};
</script>

<div
	class="mx-auto mb-2 flex w-full max-w-3xl items-center justify-between gap-3 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-left dark:border-blue-900 dark:bg-blue-950/40"
>
	<div class="min-w-0">
		<div class="flex items-center gap-2">
			<span class="text-xs font-semibold text-blue-700 dark:text-blue-200">工作流</span><span
				class="truncate text-sm font-medium text-gray-900 dark:text-gray-100">{name}</span
			>
		</div>
		<div class="mt-0.5 truncate text-xs text-gray-500 dark:text-gray-400">
			{description || '沒有功能說明'} · {modelLabel}
		</div>
	</div>
	<button
		class="shrink-0 rounded-md p-1.5 text-gray-500 hover:bg-blue-100 dark:hover:bg-blue-900"
		aria-label="取消使用工作流"
		on:click={onClear}>✕</button
	>
</div>

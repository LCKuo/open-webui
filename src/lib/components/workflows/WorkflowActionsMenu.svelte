<script lang="ts">
	import Dropdown from '$lib/components/common/Dropdown.svelte';
	import EllipsisHorizontal from '$lib/components/icons/EllipsisHorizontal.svelte';
	import GarbageBin from '$lib/components/icons/GarbageBin.svelte';

	export let disabled = false;
	export let onDelete: () => void;

	let show = false;

	const requestDelete = () => {
		show = false;
		onDelete();
	};
</script>

<Dropdown bind:show align="end">
	<button
		type="button"
		class="flex h-9 w-9 items-center justify-center rounded-lg border border-gray-200 text-gray-600 transition hover:bg-gray-50 hover:text-gray-900 disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-800 dark:text-gray-300 dark:hover:bg-gray-900 dark:hover:text-white"
		{disabled}
		aria-label="更多工作流操作"
		title="更多操作"
	>
		<EllipsisHorizontal className="h-5 w-5" />
	</button>

	<div slot="content">
		<div
			class="min-w-[190px] rounded-lg border border-gray-200 bg-white p-1 shadow-lg dark:border-gray-800 dark:bg-gray-950"
		>
			<button
				type="button"
				role="menuitem"
				class="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm text-red-600 transition hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/50"
				on:click={requestDelete}
			>
				<GarbageBin />
				<span>永久刪除</span>
			</button>
		</div>
	</div>
</Dropdown>

<script lang="ts">
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';

	onMount(() => {
		goto('/workspace/data-connectors', { replaceState: true });
	});
</script>

<svelte:head>
	<title>企業資料連線 - Interact Web Ai</title>
</svelte:head>

<div class="mx-auto flex w-full max-w-3xl flex-col gap-3 px-4 py-10 text-sm text-gray-600 dark:text-gray-300">
	<p>資料連線已移至 Workspace。</p>
	<a class="font-medium text-sky-600 hover:text-sky-500" href="/workspace/data-connectors">
		前往企業資料連線
	</a>
</div>

<script lang="ts">
	import { onMount, getContext } from 'svelte';
	import { goto } from '$app/navigation';
	import { toast } from 'svelte-sonner';
	import { WEBUI_NAME, showSidebar } from '$lib/stores';
	import {
		createWorkflow,
		deleteWorkflowById,
		getWorkflowItems,
		type WorkflowResponse
	} from '$lib/apis/workflows';
	import DeleteConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	const i18n = getContext('i18n');

	let loaded = false;
	let loading = false;
	let creating = false;
	let workflows: WorkflowResponse[] = [];
	let total = 0;
	let showDeleteConfirm = false;
	let deleteTarget: WorkflowResponse | null = null;
	let query = '';
	let visibility = 'all';
	let page = 1;
	let searchTimer: ReturnType<typeof setTimeout>;

	const defaultGraph = () => ({
		nodes: [
			{
				id: 'input',
				type: 'input',
				position: { x: 80, y: 120 },
				data: { label: 'Chat / Channel Input', type: 'channel_input' }
			},
			{
				id: 'reply',
				type: 'output',
				position: { x: 420, y: 120 },
				data: { label: 'Reply to User', type: 'channel_reply' }
			}
		],
		edges: [{ id: 'input-reply', source: 'input', target: 'reply' }]
	});

	const loadWorkflows = async () => {
		loading = true;
		try {
			const res = await getWorkflowItems(localStorage.token, query, visibility, page);
			workflows = res.items;
			total = res.total;
		} catch (err) {
			toast.error(`${err}`);
		} finally {
			loading = false;
		}
	};

	const handleSearch = () => {
		clearTimeout(searchTimer);
		searchTimer = setTimeout(() => {
			page = 1;
			loadWorkflows();
		}, 250);
	};

	const createBlankWorkflow = async () => {
		creating = true;
		try {
			const workflow = await createWorkflow(localStorage.token, {
				name: $i18n.t('Untitled workflow'),
				description: $i18n.t('Use this workflow from chat or connected channels.'),
				graph: defaultGraph(),
				meta: { channels: ['chat', 'line', 'wechat', 'telegram'] },
				visibility: 'private',
				status: 'draft'
			});
			goto(`/workflows/${workflow.id}/edit`);
		} catch (err) {
			toast.error(`${err}`);
		} finally {
			creating = false;
		}
	};

	const deleteWorkflow = async (workflow: WorkflowResponse) => {
		try {
			await deleteWorkflowById(localStorage.token, workflow.id);
			toast.success($i18n.t('Workflow deleted'));
			deleteTarget = null;
			loadWorkflows();
		} catch (err) {
			toast.error(`${err}`);
		}
	};

	const formatDate = (value: number) => new Date(Math.floor(value / 1000000)).toLocaleString();

	onMount(() => {
		loaded = true;
		loadWorkflows();

		return () => clearTimeout(searchTimer);
	});
</script>

<svelte:head>
	<title>{$i18n.t('Workflows')} | {$WEBUI_NAME}</title>
</svelte:head>

<DeleteConfirmDialog
	bind:show={showDeleteConfirm}
	title={$i18n.t('Delete workflow?')}
	confirmLabel={$i18n.t('Delete')}
	on:confirm={() => {
		if (deleteTarget) deleteWorkflow(deleteTarget);
	}}
>
	<div class="truncate text-sm text-gray-500">
		{$i18n.t('This will delete')} <span class="font-medium">{deleteTarget?.name}</span>.
	</div>
</DeleteConfirmDialog>

<div
	class="flex flex-col w-full h-screen max-h-[100dvh] transition-width duration-200 ease-in-out {$showSidebar
		? 'md:max-w-[calc(100%-var(--sidebar-width))]'
		: ''} max-w-full"
>
	<div class="flex-1 overflow-y-auto">
		<div class="mx-auto flex w-full max-w-6xl flex-col gap-6 px-4 py-6 md:px-8">
			<div class="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
				<div class="space-y-2">
					<div class="text-xs font-semibold uppercase tracking-wide text-gray-500">
						{$i18n.t('Chat-driven automation')}
					</div>
					<h1 class="text-2xl font-semibold text-gray-900 dark:text-gray-100">
						{$i18n.t('Workflows')}
					</h1>
					<p class="max-w-2xl text-sm text-gray-500">
						{$i18n.t(
							'Build once, run from chat, LINE, WeChat, Telegram, webhooks, or scheduled triggers.'
						)}
					</p>
				</div>

				<button
					class="rounded-xl bg-gray-900 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-white dark:text-gray-900"
					disabled={creating}
					on:click={createBlankWorkflow}
				>
					{creating ? $i18n.t('Creating...') : $i18n.t('New workflow')}
				</button>
			</div>

			<div
				class="flex flex-col gap-3 rounded-xl border border-gray-200 bg-white p-3 dark:border-gray-800 dark:bg-gray-950 md:flex-row"
			>
				<input
					class="min-w-0 flex-1 rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm outline-none focus:border-gray-400 dark:border-gray-800 dark:focus:border-gray-600"
					placeholder={$i18n.t('Search workflows')}
					bind:value={query}
					on:input={handleSearch}
				/>
				<select
					class="rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm outline-none dark:border-gray-800"
					bind:value={visibility}
					on:change={() => {
						page = 1;
						loadWorkflows();
					}}
				>
					<option value="all">{$i18n.t('All visibility')}</option>
					<option value="private">{$i18n.t('Private')}</option>
					<option value="shared">{$i18n.t('Shared')}</option>
					<option value="public_template">{$i18n.t('Public templates')}</option>
				</select>
			</div>

			{#if !loaded || loading}
				<div class="flex justify-center py-16">
					<Spinner />
				</div>
			{:else if workflows.length === 0}
				<div
					class="rounded-xl border border-dashed border-gray-300 bg-white p-10 text-center dark:border-gray-800 dark:bg-gray-950"
				>
					<div class="text-base font-medium text-gray-900 dark:text-gray-100">
						{$i18n.t('No workflows yet')}
					</div>
					<p class="mt-2 text-sm text-gray-500">
						{$i18n.t(
							'Create a workflow and test it from the same editor before connecting it to channels.'
						)}
					</p>
					<button
						class="mt-5 rounded-xl bg-gray-900 px-4 py-2.5 text-sm font-medium text-white dark:bg-white dark:text-gray-900"
						on:click={createBlankWorkflow}
					>
						{$i18n.t('Create first workflow')}
					</button>
				</div>
			{:else}
				<div class="grid gap-3">
					{#each workflows as workflow}
						<div
							class="flex flex-col gap-4 rounded-xl border border-gray-200 bg-white p-4 transition hover:border-gray-300 dark:border-gray-800 dark:bg-gray-950 md:flex-row md:items-center md:justify-between"
						>
							<button
								class="min-w-0 flex-1 text-left"
								on:click={() => goto(`/workflows/${workflow.id}/edit`)}
							>
								<div class="flex flex-wrap items-center gap-2">
									<div class="truncate text-base font-medium text-gray-900 dark:text-gray-100">
										{workflow.name}
									</div>
									<span
										class="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-600 dark:bg-gray-800 dark:text-gray-300"
									>
										{workflow.status}
									</span>
									<span
										class="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-600 dark:bg-gray-800 dark:text-gray-300"
									>
										{workflow.visibility}
									</span>
								</div>
								<div class="mt-1 line-clamp-2 text-sm text-gray-500">
									{workflow.description || $i18n.t('No description')}
								</div>
								<div class="mt-2 text-xs text-gray-400">
									{$i18n.t('Updated')} {formatDate(workflow.updated_at)}
								</div>
							</button>

							<div class="flex shrink-0 items-center gap-2">
								<button
									class="rounded-lg border border-gray-200 px-3 py-2 text-sm hover:bg-gray-50 dark:border-gray-800 dark:hover:bg-gray-900"
									on:click={() => goto(`/workflows/${workflow.id}/edit`)}
								>
									{$i18n.t('Edit')}
								</button>
								<button
									class="rounded-lg border border-red-200 px-3 py-2 text-sm text-red-600 hover:bg-red-50 dark:border-red-900 dark:hover:bg-red-950"
									on:click={() => {
										deleteTarget = workflow;
										showDeleteConfirm = true;
									}}
								>
									{$i18n.t('Delete')}
								</button>
							</div>
						</div>
					{/each}
				</div>

				<div class="text-sm text-gray-500">
					{$i18n.t('Showing')} {workflows.length} {$i18n.t('of')} {total}
					{$i18n.t('workflows')}
				</div>
			{/if}
		</div>
	</div>
</div>

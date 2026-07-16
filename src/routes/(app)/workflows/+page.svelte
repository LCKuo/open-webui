<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { toast } from 'svelte-sonner';
	import { WEBUI_NAME, showSidebar, user } from '$lib/stores';
	import {
		createWorkflow,
		deleteWorkflowById,
		getWorkflowItems,
		type WorkflowResponse
	} from '$lib/apis/workflows';
	import DeleteConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Pagination from '$lib/components/common/Pagination.svelte';
	import WorkflowOverviewDrawer from '$lib/components/workflows/WorkflowOverviewDrawer.svelte';
	import { buildWorkflowTemplateGraph } from '$lib/components/workflows/workflowNodeCatalog';
	import {
		workflowLaunchLabel,
		workflowLaunchSummary
	} from '$lib/components/workflows/workflowLaunch';

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
	let selectedWorkflow: WorkflowResponse | null = null;
	let centerView: 'available' | 'mine' | 'company' = 'available';

	const VISIBILITY_OPTIONS = [
		{
			value: 'all',
			label: '全部工作流',
			description: '你的工作流與公開範本。'
		},
		{
			value: 'private',
			label: '私人',
			description: '只有擁有者與管理員可以使用。'
		},
		{
			value: 'shared',
			label: '指定範圍共享',
			description: '只會依工作流存取政策開放，例如公司、成員、群組、頻道或模型範圍。'
		},
		{
			value: 'public_template',
			label: '公開範本',
			description: '登入工作區的使用者可見的可重用範本，不適合公司內部自動化。'
		}
	];

	const visibilityLabel = (value: string) =>
		VISIBILITY_OPTIONS.find((option) => option.value === value)?.label ?? value;
	const statusLabel = (value: string) =>
		({
			draft: '草稿',
			published: '已發布',
			archived: '已封存'
		})[value] ?? value;
	const canManageWorkflow = (workflow: WorkflowResponse) =>
		$user?.role === 'admin' || workflow.user_id === $user?.id;
	$: visibleWorkflows = workflows.filter((workflow) => {
		if (centerView === 'mine') return canManageWorkflow(workflow);
		if (centerView === 'company')
			return workflow.visibility === 'shared' && !canManageWorkflow(workflow);
		return true;
	});

	const useWorkflowInChat = (workflow: WorkflowResponse) => {
		if (!workflow.default_version_id) return;
		goto(
			`/?workflow=${encodeURIComponent(workflow.id)}&version=${encodeURIComponent(workflow.default_version_id)}&launch=1`
		);
	};

	const defaultGraph = () => buildWorkflowTemplateGraph('channel-assistant');

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
			if (page !== 1) page = 1;
			else loadWorkflows();
		}, 250);
	};

	$: if (loaded && page) {
		loadWorkflows();
	}

	const createBlankWorkflow = async () => {
		creating = true;
		try {
			const workflow = await createWorkflow(localStorage.token, {
				name: '未命名工作流',
				description: '尚未設定用途的工作流草稿。',
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
			toast.success('工作流已刪除');
			deleteTarget = null;
			loadWorkflows();
		} catch (err) {
			toast.error(`${err}`);
		}
	};

	const formatDate = (value: number) => new Date(Math.floor(value / 1000000)).toLocaleString();

	onMount(() => {
		loaded = true;

		return () => clearTimeout(searchTimer);
	});
</script>

<svelte:head>
	<title>工作流 | {$WEBUI_NAME}</title>
</svelte:head>

<DeleteConfirmDialog
	bind:show={showDeleteConfirm}
	title="刪除工作流？"
	confirmLabel="刪除"
	on:confirm={() => {
		if (deleteTarget) deleteWorkflow(deleteTarget);
	}}
>
	<div class="truncate text-sm text-gray-500">
		這會刪除 <span class="font-medium">{deleteTarget?.name}</span>。
	</div>
</DeleteConfirmDialog>

<WorkflowOverviewDrawer
	workflow={selectedWorkflow}
	canManage={selectedWorkflow ? canManageWorkflow(selectedWorkflow) : false}
	onClose={() => (selectedWorkflow = null)}
	onUse={useWorkflowInChat}
	onEdit={(workflow) => goto(`/workflows/${workflow.id}/edit`)}
/>

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
						企業工作流中心
					</div>
					<h1 class="text-2xl font-semibold text-gray-900 dark:text-gray-100">工作流</h1>
					<p class="max-w-2xl text-sm text-gray-500">
						建立工作流圖、發布版本並設定公司、成員、群組、頻道與模型存取政策。
					</p>
				</div>

				<button
					class="rounded-lg bg-gray-900 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-white dark:text-gray-900"
					disabled={creating}
					on:click={createBlankWorkflow}
				>
					{creating ? '建立中...' : '新增工作流'}
				</button>
			</div>

			<div
				class="flex flex-col gap-3 rounded-lg border border-gray-200 bg-white p-3 dark:border-gray-800 dark:bg-gray-950 md:flex-row"
			>
				<input
					class="min-w-0 flex-1 rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm outline-none focus:border-gray-400 dark:border-gray-800 dark:focus:border-gray-600"
					placeholder="搜尋工作流"
					bind:value={query}
					on:input={handleSearch}
				/>
				<select
					class="rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm outline-none dark:border-gray-800"
					bind:value={visibility}
					on:change={() => {
						if (page !== 1) page = 1;
						else loadWorkflows();
					}}
					title={VISIBILITY_OPTIONS.find((option) => option.value === visibility)?.description}
				>
					{#each VISIBILITY_OPTIONS as option}
						<option value={option.value}>{option.label}</option>
					{/each}
				</select>
			</div>

			<div class="flex gap-2 overflow-x-auto border-b border-gray-200 dark:border-gray-800">
				{#each [{ value: 'available', label: '我能使用的' }, { value: 'mine', label: '我建立的' }, { value: 'company', label: '企業共享' }] as option}
					<button
						class="border-b-2 px-3 py-2 text-sm {centerView === option.value
							? 'border-gray-900 font-medium text-gray-900 dark:border-white dark:text-white'
							: 'border-transparent text-gray-500'}"
						on:click={() => (centerView = option.value as typeof centerView)}>{option.label}</button
					>
				{/each}
			</div>

			{#if !loaded || loading}
				<div class="flex justify-center py-16">
					<Spinner />
				</div>
			{:else if visibleWorkflows.length === 0}
				<div
					class="rounded-lg border border-dashed border-gray-300 bg-white p-10 text-center dark:border-gray-800 dark:bg-gray-950"
				>
					<div class="text-base font-medium text-gray-900 dark:text-gray-100">目前沒有工作流</div>
					<p class="mt-2 text-sm text-gray-500">
						先建立工作流，設定節點與存取政策，再進行結構驗證。
					</p>
					<button
						class="mt-5 rounded-lg bg-gray-900 px-4 py-2.5 text-sm font-medium text-white dark:bg-white dark:text-gray-900"
						on:click={createBlankWorkflow}
					>
						建立第一個工作流
					</button>
				</div>
			{:else}
				<div class="grid gap-3">
					{#each visibleWorkflows as workflow}
						<div
							class="flex flex-col gap-4 rounded-lg border border-gray-200 bg-white p-4 transition hover:border-gray-300 dark:border-gray-800 dark:bg-gray-950 md:flex-row md:items-center md:justify-between"
						>
							<button
								class="min-w-0 flex-1 text-left"
								on:click={() => (selectedWorkflow = workflow)}
							>
								<div class="flex flex-wrap items-center gap-2">
									<div class="truncate text-base font-medium text-gray-900 dark:text-gray-100">
										{workflow.name}
									</div>
									<span
										class="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-600 dark:bg-gray-800 dark:text-gray-300"
									>
										{statusLabel(workflow.status)}
									</span>
									<span
										class="rounded-full bg-gray-100 px-2 py-0.5 text-xs text-gray-600 dark:bg-gray-800 dark:text-gray-300"
									>
										{visibilityLabel(workflow.visibility)}
									</span>
									<span
										class="rounded-full bg-blue-50 px-2 py-0.5 text-xs text-blue-700 dark:bg-blue-950 dark:text-blue-200"
									>
										{workflowLaunchSummary(workflow)}
									</span>
								</div>
								<div class="mt-1 line-clamp-2 text-sm text-gray-500">
									{workflow.description || '沒有描述'}
								</div>
								<div class="mt-2 text-xs text-gray-400">
									更新時間 {formatDate(workflow.updated_at)}
								</div>
							</button>

							<div class="flex w-full flex-wrap items-center gap-2 md:w-auto md:flex-nowrap">
								<button
									class="rounded-lg border border-gray-200 px-3 py-2 text-sm hover:bg-gray-50 dark:border-gray-800 dark:hover:bg-gray-900"
									on:click={() => (selectedWorkflow = workflow)}>概覽</button
								>
								<button
									class="rounded-lg bg-gray-900 px-3 py-2 text-sm font-medium text-white disabled:opacity-50 dark:bg-white dark:text-gray-900"
									disabled={!workflow.default_version_id || workflow.status !== 'published'}
									on:click={() => useWorkflowInChat(workflow)}>{workflowLaunchLabel(workflow)}</button
								>
								<button
									class="rounded-lg border border-gray-200 px-3 py-2 text-sm hover:bg-gray-50 dark:border-gray-800 dark:hover:bg-gray-900"
									on:click={() => goto(`/workflows/${workflow.id}/edit`)}
								>
									{canManageWorkflow(workflow) ? '編輯' : '檢視'}
								</button>
								{#if canManageWorkflow(workflow)}
									<button
										class="rounded-lg border border-red-200 px-3 py-2 text-sm text-red-600 hover:bg-red-50 dark:border-red-900 dark:hover:bg-red-950"
										on:click={() => {
											deleteTarget = workflow;
											showDeleteConfirm = true;
										}}
									>
										刪除
									</button>
								{/if}
							</div>
						</div>
					{/each}
				</div>

				<div class="text-sm text-gray-500">
					顯示 {workflows.length} / {total} 個工作流
				</div>
				{#if total > 30}
					<div class="flex justify-center">
						<Pagination bind:page count={total} perPage={30} />
					</div>
				{/if}
			{/if}
		</div>
	</div>
</div>

<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { page } from '$app/stores';
	import { beforeNavigate, goto } from '$app/navigation';
	import { get } from 'svelte/store';
	import { writable } from 'svelte/store';
	import { toast } from 'svelte-sonner';
	import { Background, BackgroundVariant, Controls, MiniMap, SvelteFlow } from '@xyflow/svelte';
	import '@xyflow/svelte/dist/style.css';
	import { showSidebar } from '$lib/stores';
	import {
		getWorkflowById,
		getWorkflowRuns,
		publishWorkflowById,
		runWorkflowById,
		updateWorkflowById,
		validateWorkflowById,
		type WorkflowResponse,
		type WorkflowRunResponse,
		type WorkflowValidateResponse
	} from '$lib/apis/workflows';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	const i18n = getContext('i18n');

	let loaded = false;
	let saving = false;
	let validating = false;
	let publishing = false;
	let running = false;
	let workflow: WorkflowResponse | null = null;
	let name = '';
	let description = '';
	let visibility = 'private';
	let testInput = '{\n  "message": "Hello from chat"\n}';
	let validation: WorkflowValidateResponse | null = null;
	let runs: WorkflowRunResponse[] = [];
	let jsonMode = false;
	let graphJson = '';
	let lastSavedSignature = '';
	let isDirty = false;
	let showUnsavedConfirm = false;
	let pendingNavigation = '/workflows';

	const nodes = writable<any[]>([]);
	const edges = writable<any[]>([]);

	$: workflowId = $page.params.id;

	const graph = () => ({
		nodes: get(nodes),
		edges: get(edges)
	});

	const graphSignature = () =>
		JSON.stringify({
			name,
			description,
			visibility,
			graph: graph()
		});

	const markDirty = () => {
		if (loaded) {
			isDirty = graphSignature() !== lastSavedSignature;
		}
	};

	const syncJson = () => {
		graphJson = JSON.stringify(graph(), null, 2);
	};

	const parseGraphJson = () => {
		const parsed = JSON.parse(graphJson);
		if (
			!parsed ||
			typeof parsed !== 'object' ||
			!Array.isArray(parsed.nodes) ||
			!Array.isArray(parsed.edges)
		) {
			throw new Error($i18n.t('Graph JSON must include nodes and edges arrays.'));
		}
		return parsed;
	};

	const normalizeGraph = () => ({
		nodes: get(nodes).map((node) => ({
			...node,
			type: node.type === 'input' || node.type === 'output' ? node.type : 'default',
			data: {
				...(node.data ?? {}),
				type: node.data?.type ?? node.type
			}
		})),
		edges: get(edges)
	});

	nodes.subscribe(() => {
		if (loaded && !jsonMode) {
			syncJson();
			markDirty();
		}
	});

	edges.subscribe(() => {
		if (loaded && !jsonMode) {
			syncJson();
			markDirty();
		}
	});

	const loadWorkflow = async () => {
		try {
			const res = await getWorkflowById(localStorage.token, workflowId);
			workflow = res;
			name = res.name;
			description = res.description ?? '';
			visibility = res.visibility;
			nodes.set(res.graph?.nodes ?? []);
			edges.set(res.graph?.edges ?? []);
			syncJson();
			runs = await getWorkflowRuns(localStorage.token, workflowId, 10).catch(() => []);
			lastSavedSignature = graphSignature();
			isDirty = false;
			loaded = true;
		} catch (err) {
			toast.error(`${err}`);
			goto('/workflows');
		}
	};

	const saveWorkflow = async () => {
		saving = true;
		try {
			if (jsonMode) {
				const parsed = parseGraphJson();
				nodes.set(parsed.nodes);
				edges.set(parsed.edges);
			}

			const res = await updateWorkflowById(localStorage.token, workflowId, {
				name,
				description,
				visibility,
				graph: normalizeGraph()
			});
			workflow = res;
			nodes.set(res.graph?.nodes ?? []);
			edges.set(res.graph?.edges ?? []);
			syncJson();
			lastSavedSignature = graphSignature();
			isDirty = false;
			toast.success($i18n.t('Workflow saved'));
			return true;
		} catch (err) {
			toast.error(`${err}`);
			return false;
		} finally {
			saving = false;
		}
	};

	const validateWorkflow = async () => {
		validating = true;
		try {
			const result = await validateWorkflowById(localStorage.token, workflowId, graph());
			validation = result;
			if (result.ok) {
				toast.success(
					result.warnings.length ? $i18n.t('Valid with warnings') : $i18n.t('Workflow is valid')
				);
			} else {
				toast.error($i18n.t('Workflow has blocking issues'));
			}
		} catch (err) {
			toast.error(`${err}`);
		} finally {
			validating = false;
		}
	};

	const publishWorkflow = async () => {
		publishing = true;
		try {
			const saved = await saveWorkflow();
			if (!saved) return;
			const version = await publishWorkflowById(localStorage.token, workflowId);
			toast.success($i18n.t('Published version {{version}}', { version: version.version }));
			await loadWorkflow();
		} catch (err) {
			toast.error(`${err}`);
		} finally {
			publishing = false;
		}
	};

	const runWorkflow = async () => {
		running = true;
		try {
			if (isDirty) {
				const saved = await saveWorkflow();
				if (!saved) return;
			}
			const input = JSON.parse(testInput);
			const run = await runWorkflowById(localStorage.token, workflowId, input, 'manual_test');
			runs = [run, ...runs].slice(0, 10);
			toast.success(
				run.status === 'success' ? $i18n.t('Test run completed') : $i18n.t('Test run recorded')
			);
		} catch (err) {
			toast.error(`${err}`);
		} finally {
			running = false;
		}
	};

	const addNode = (kind: string) => {
		const next = get(nodes).length + 1;
		const id = `${kind}-${Date.now()}`;
		nodes.update((items) => [
			...items,
			{
				id,
				type: kind === 'chat_output' || kind === 'channel_reply' ? 'output' : 'default',
				position: { x: 120 + next * 40, y: 100 + next * 30 },
				data: { label: kind.replaceAll('_', ' '), type: kind }
			}
		]);
	};

	const edgeId = (source: string, target: string) => `${source}-${target}-${Date.now()}`;

	const createEdge = (connection: any) => {
		if (!connection.source || !connection.target) return false;
		return {
			...connection,
			id: edgeId(connection.source, connection.target),
			type: 'default',
			deletable: true
		};
	};

	const isValidConnection = (connection: any) => {
		if (!connection.source || !connection.target || connection.source === connection.target) return false;
		return !get(edges).some(
			(edge) => edge.source === connection.source && edge.target === connection.target
		);
	};

	const connectLastTwo = () => {
		const currentNodes = get(nodes);
		if (currentNodes.length < 2) {
			toast.error($i18n.t('Add at least two nodes first'));
			return;
		}

		const source = currentNodes[currentNodes.length - 2].id;
		const target = currentNodes[currentNodes.length - 1].id;
		if (!isValidConnection({ source, target })) {
			toast.error($i18n.t('These nodes are already connected or cannot be connected.'));
			return;
		}
		edges.update((items) => [...items, { id: edgeId(source, target), source, target, deletable: true }]);
	};

	const deleteSelected = () => {
		const selectedNodeIds = new Set(get(nodes).filter((node) => node.selected).map((node) => node.id));
		const selectedEdges = new Set(get(edges).filter((edge) => edge.selected).map((edge) => edge.id));
		if (!selectedNodeIds.size && !selectedEdges.size) {
			toast.error($i18n.t('Select a node or edge first'));
			return;
		}
		nodes.update((items) => items.filter((node) => !selectedNodeIds.has(node.id)));
		edges.update((items) =>
			items.filter(
				(edge) =>
					!selectedEdges.has(edge.id) &&
					!selectedNodeIds.has(edge.source) &&
					!selectedNodeIds.has(edge.target)
			)
		);
	};

	const applyJson = () => {
		try {
			const parsed = parseGraphJson();
			nodes.set(parsed.nodes);
			edges.set(parsed.edges);
			markDirty();
			toast.success($i18n.t('Graph JSON applied'));
		} catch (err) {
			toast.error(`${err}`);
		}
	};

	const formatDate = (value: number | null) =>
		value ? new Date(Math.floor(value / 1000000)).toLocaleString() : '-';

	const leaveEditor = () => {
		if (isDirty) {
			pendingNavigation = '/workflows';
			showUnsavedConfirm = true;
			return;
		}
		goto('/workflows');
	};

	beforeNavigate(({ cancel, to }) => {
		if (!loaded || !isDirty || !to?.url) return;
		if (to.url.pathname === $page.url.pathname) return;

		cancel();
		pendingNavigation = `${to.url.pathname}${to.url.search}${to.url.hash}`;
		showUnsavedConfirm = true;
	});

	onMount(loadWorkflow);
</script>

<ConfirmDialog
	bind:show={showUnsavedConfirm}
	title={$i18n.t('Discard unsaved changes?')}
	confirmLabel={$i18n.t('Discard')}
	on:confirm={() => {
		isDirty = false;
		goto(pendingNavigation);
	}}
>
	<div class="text-sm text-gray-500">
		{$i18n.t('You have unsaved workflow changes. Leaving now will discard them.')}
	</div>
</ConfirmDialog>

{#if !loaded || !workflow}
	<div
		class="flex h-screen max-h-[100dvh] w-full items-center justify-center transition-width duration-200 ease-in-out {$showSidebar
			? 'md:max-w-[calc(100%-var(--sidebar-width))]'
			: ''}"
	>
		<Spinner className="size-5" />
	</div>
{:else}
	<div
		class="flex h-screen max-h-[100dvh] w-full flex-col transition-width duration-200 ease-in-out {$showSidebar
			? 'md:max-w-[calc(100%-var(--sidebar-width))]'
			: ''} max-w-full"
	>
		<div
			class="flex flex-wrap items-center justify-between gap-3 border-b border-gray-200 px-4 py-3 dark:border-gray-800"
		>
			<div class="flex min-w-0 items-center gap-3">
				<button
					class="rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-800"
					on:click={leaveEditor}
				>
					{$i18n.t('Back')}
				</button>
				<div class="min-w-0">
					<input
						class="w-full min-w-[16rem] bg-transparent text-lg font-semibold text-gray-900 outline-none dark:text-gray-100"
						bind:value={name}
						on:input={() => queueMicrotask(markDirty)}
						aria-label="Workflow name"
					/>
					<div class="text-xs text-gray-500">
						{workflow.status} · {workflow.default_version_id
							? $i18n.t('published version available')
							: $i18n.t('draft only')}
						{#if isDirty}
							<span class="ml-2 text-amber-600">{$i18n.t('Unsaved changes')}</span>
						{/if}
					</div>
				</div>
			</div>

			<div class="flex flex-wrap items-center gap-2">
				<button
					class="rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-800"
					on:click={validateWorkflow}
					disabled={validating}
				>
					{validating ? $i18n.t('Checking...') : $i18n.t('Validate')}
				</button>
				<button
					class="rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-800"
					on:click={saveWorkflow}
					disabled={saving}
				>
					{saving ? $i18n.t('Saving...') : $i18n.t('Save')}
				</button>
				<button
					class="rounded-lg bg-gray-900 px-3 py-2 text-sm font-medium text-white dark:bg-white dark:text-gray-900"
					on:click={publishWorkflow}
					disabled={publishing}
				>
					{publishing ? $i18n.t('Publishing...') : $i18n.t('Publish')}
				</button>
			</div>
		</div>

		<div class="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[1fr_360px]">
			<div class="relative min-h-[420px] bg-gray-50 dark:bg-gray-950">
				{#if jsonMode}
					<div class="flex h-full flex-col gap-3 p-4">
						<textarea
							class="min-h-0 flex-1 resize-none rounded-xl border border-gray-200 bg-white p-4 font-mono text-xs outline-none dark:border-gray-800 dark:bg-gray-900"
							bind:value={graphJson}
							on:input={() => {
								if (loaded) isDirty = true;
							}}
							aria-label="Workflow graph JSON"
						></textarea>
						<div class="flex justify-end">
							<button
								class="rounded-lg bg-gray-900 px-3 py-2 text-sm font-medium text-white dark:bg-white dark:text-gray-900"
								on:click={applyJson}
							>
								{$i18n.t('Apply JSON')}
							</button>
						</div>
					</div>
				{:else}
					<SvelteFlow {nodes} {edges} fitView onedgecreate={createEdge} {isValidConnection}>
						<Controls />
						<MiniMap />
						<Background variant={BackgroundVariant.Dots} />
					</SvelteFlow>
				{/if}
			</div>

			<aside
				class="flex min-h-0 flex-col gap-4 overflow-y-auto border-l border-gray-200 bg-white p-4 dark:border-gray-800 dark:bg-gray-950"
			>
				<section class="space-y-3">
					<div class="text-sm font-semibold text-gray-900 dark:text-gray-100">
						{$i18n.t('Workflow settings')}
					</div>
					<textarea
						class="h-20 w-full resize-none rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm outline-none dark:border-gray-800"
						bind:value={description}
						on:input={() => queueMicrotask(markDirty)}
						placeholder={$i18n.t('Description')}
					></textarea>
					<select
						class="w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm outline-none dark:border-gray-800"
						bind:value={visibility}
						on:change={() => queueMicrotask(markDirty)}
					>
						<option value="private">{$i18n.t('Private')}</option>
						<option value="shared">{$i18n.t('Shared')}</option>
						<option value="public_template">{$i18n.t('Public template')}</option>
					</select>
				</section>

				<section class="space-y-3">
					<div class="flex items-center justify-between">
						<div class="text-sm font-semibold text-gray-900 dark:text-gray-100">
							{$i18n.t('Canvas tools')}
						</div>
						<button
							class="text-xs text-gray-500 hover:text-gray-900 dark:hover:text-gray-100"
							on:click={() => {
								jsonMode = !jsonMode;
								syncJson();
							}}
						>
							{jsonMode ? $i18n.t('Canvas') : 'JSON'}
						</button>
					</div>
					<div class="grid grid-cols-2 gap-2">
						<button
							class="rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-800"
							on:click={() => addNode('chat_input')}>{$i18n.t('Chat input')}</button
						>
						<button
							class="rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-800"
							on:click={() => addNode('llm')}>{$i18n.t('LLM')}</button
						>
						<button
							class="rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-800"
							on:click={() => addNode('media_output')}>{$i18n.t('Media')}</button
						>
						<button
							class="rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-800"
							on:click={() => addNode('channel_reply')}>{$i18n.t('Reply')}</button
						>
					</div>
					<button
						class="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-800"
						on:click={connectLastTwo}
					>
						{$i18n.t('Connect last two nodes')}
					</button>
					<button
						class="w-full rounded-lg border border-gray-200 px-3 py-2 text-sm dark:border-gray-800"
						on:click={deleteSelected}
					>
						{$i18n.t('Delete selected')}
					</button>
				</section>

				{#if validation}
					<section class="rounded-xl border border-gray-200 p-3 text-sm dark:border-gray-800">
						<div class="font-medium {validation.ok ? 'text-green-600' : 'text-red-600'}">
							{validation.ok ? $i18n.t('Valid workflow') : $i18n.t('Workflow needs fixes')}
						</div>
						{#each validation.errors as error}
							<div class="mt-2 text-red-600">{error}</div>
						{/each}
						{#each validation.warnings as warning}
							<div class="mt-2 text-amber-600">{warning}</div>
						{/each}
					</section>
				{/if}

				<section class="space-y-3">
					<div class="text-sm font-semibold text-gray-900 dark:text-gray-100">
						{$i18n.t('Test run')}
					</div>
					<textarea
						class="h-28 w-full resize-none rounded-lg border border-gray-200 bg-transparent p-3 font-mono text-xs outline-none dark:border-gray-800"
						bind:value={testInput}
					></textarea>
					<button
						class="w-full rounded-lg bg-gray-900 px-3 py-2 text-sm font-medium text-white dark:bg-white dark:text-gray-900"
						on:click={runWorkflow}
						disabled={running}
					>
						{running ? $i18n.t('Running...') : $i18n.t('Run test')}
					</button>
				</section>

				<section class="space-y-3">
					<div class="text-sm font-semibold text-gray-900 dark:text-gray-100">
						{$i18n.t('Recent runs')}
					</div>
					{#if runs.length === 0}
						<div
							class="rounded-lg border border-dashed border-gray-200 p-4 text-sm text-gray-500 dark:border-gray-800"
						>
							{$i18n.t('No runs yet.')}
						</div>
					{:else}
						<div class="space-y-2">
							{#each runs as run}
								<div class="rounded-lg border border-gray-200 p-3 text-xs dark:border-gray-800">
									<div class="flex items-center justify-between gap-3">
										<span
											class="font-medium {run.status === 'success'
												? 'text-green-600'
												: run.status === 'error'
													? 'text-red-600'
													: 'text-gray-600'}"
										>
											{run.status}
										</span>
										<span class="text-gray-400"
											>{formatDate(run.completed_at ?? run.created_at)}</span
										>
									</div>
									{#if run.error}
										<div class="mt-2 text-red-600">{run.error}</div>
									{:else if run.output}
										<pre
											class="mt-2 max-h-28 overflow-auto whitespace-pre-wrap rounded bg-gray-50 p-2 text-gray-600 dark:bg-gray-900 dark:text-gray-300">{JSON.stringify(
												run.output,
												null,
												2
											)}</pre>
									{/if}
								</div>
							{/each}
						</div>
					{/if}
				</section>
			</aside>
		</div>
	</div>
{/if}

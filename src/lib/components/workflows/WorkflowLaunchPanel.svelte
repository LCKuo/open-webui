<script lang="ts">
	import type {
		WorkflowLaunchPreflightResponse,
		WorkflowResponse
	} from '$lib/apis/workflows';
	import type { WorkflowLaunchConfig } from './workflowLaunch';
	import XMark from '$lib/components/icons/XMark.svelte';

	export let workflow: WorkflowResponse;
	export let launch: WorkflowLaunchConfig;
	export let values: Record<string, unknown> = {};
	export let fileCount = 0;
	export let running = false;
	export let confirmed = false;
	export let preflight: WorkflowLaunchPreflightResponse | null = null;
	export let onValuesChange: (values: Record<string, unknown>) => void = () => {};
	export let onConfirmedChange: (confirmed: boolean) => void = () => {};
	export let onExecute: () => void = () => {};
	export let onClear: () => void = () => {};

	$: fields = Object.entries(launch.inputSchema.properties).filter(([key]) => key !== 'message');
	$: failedChecks = preflight?.checks?.filter((check) => check.status === 'fail') ?? [];
	$: requiredFileMissing = launch.mode === 'file_input' && fileCount === 0;
	$: formMissing = launch.inputSchema.required.some(
		(key) =>
			key !== 'message' &&
			(values[key] === undefined || values[key] === '' || values[key] === null)
	);
	$: workflowHasRiskyNodes = (workflow.graph?.nodes ?? []).some((node) =>
		[
			'notification',
			'handoff',
			'http_request',
			'mcp_tools',
			'ticket_tool',
			'tool_call',
			'crm_tool',
			'code_interpreter'
		].includes(node?.data?.type ?? node?.data?.kind ?? '')
	);
	$: needsConfirmation =
		Boolean(preflight?.requires_confirmation) ||
		launch.confirmation === 'always' ||
		(launch.confirmation === 'risk_only' && workflowHasRiskyNodes);

	const setValue = (key: string, value: unknown) => onValuesChange({ ...values, [key]: value });
	const fieldId = (key: string) => `workflow-launch-${workflow.id}-${key}`;
</script>

<div
	class="mx-auto mb-3 w-full max-w-3xl rounded-lg border border-blue-200 bg-white shadow-sm dark:border-blue-900 dark:bg-gray-950"
	aria-live="polite"
>
	<div class="flex items-start justify-between gap-3 border-b border-gray-100 px-4 py-3 dark:border-gray-800">
		<div class="min-w-0">
			<div class="flex flex-wrap items-center gap-2">
				<span class="text-xs font-semibold text-blue-700 dark:text-blue-200">準備執行工作流</span>
				<span class="truncate text-sm font-medium text-gray-900 dark:text-gray-100">{workflow.name}</span>
			</div>
			<p class="mt-1 text-xs leading-5 text-gray-500">{launch.instruction}</p>
		</div>
		<button
			type="button"
			class="shrink-0 rounded-md p-1.5 text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-900"
			aria-label="取消使用工作流"
			title="取消使用工作流"
			on:click={onClear}
		>
			<XMark className="size-4" />
		</button>
	</div>

	<div class="space-y-3 px-4 py-3">
		{#if launch.mode === 'instant'}
			<div class="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-200">
				<span class="size-2 rounded-full {running ? 'animate-pulse bg-blue-500' : 'bg-green-500'}"></span>
				{running ? '正在檢查權限並開始執行...' : '所需條件已有預設值，可直接執行。'}
			</div>
		{:else if launch.mode === 'text_input'}
			<div class="text-sm text-gray-700 dark:text-gray-200">請在下方輸入訊息並送出。</div>
		{:else if launch.mode === 'file_input'}
			<div class="space-y-1 text-sm text-gray-700 dark:text-gray-200">
				<div>請使用下方附件按鈕上傳檔案，再送出訊息。</div>
				<div class="text-xs text-gray-500">
					已選 {fileCount} / {launch.fileRules.maxFiles} 個 · 單檔上限 {launch.fileRules.maxSizeMB} MB ·
					{launch.fileRules.allowedMimeTypes.join(', ')}
				</div>
			</div>
		{:else if launch.mode === 'form_input'}
			<div class="grid gap-3 sm:grid-cols-2">
				{#each fields as [key, field]}
					<label
						class="space-y-1.5 text-xs font-medium text-gray-600 dark:text-gray-300 {field.format ===
						'textarea'
							? 'sm:col-span-2'
							: ''}"
						for={fieldId(key)}
					>
						<span>
							{field.title || key}{#if launch.inputSchema.required.includes(key)}<span
								class="ml-1 text-red-500">*</span
							>{/if}
						</span>
						{#if field.enum?.length}
							<select
								id={fieldId(key)}
								class="w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm font-normal outline-none focus:border-blue-500 dark:border-gray-800"
								value={String(values[key] ?? '')}
								on:change={(event) => setValue(key, event.currentTarget.value)}
							>
								<option value="">請選擇</option>
								{#each field.enum as option}<option value={option}>{option}</option>{/each}
							</select>
						{:else if field.type === 'boolean'}
							<span class="flex h-9 items-center gap-2 rounded-lg border border-gray-200 px-3 dark:border-gray-800">
								<input
									id={fieldId(key)}
									type="checkbox"
									checked={Boolean(values[key])}
									on:change={(event) => setValue(key, event.currentTarget.checked)}
								/>
								<span class="text-sm font-normal">啟用</span>
							</span>
						{:else if field.format === 'textarea'}
							<textarea
								id={fieldId(key)}
								class="h-20 w-full resize-y rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm font-normal outline-none focus:border-blue-500 dark:border-gray-800"
								value={String(values[key] ?? '')}
								on:input={(event) => setValue(key, event.currentTarget.value)}
							></textarea>
						{:else}
							<input
								id={fieldId(key)}
								class="w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm font-normal outline-none focus:border-blue-500 dark:border-gray-800"
								type={field.type === 'integer' || field.type === 'number'
									? 'number'
									: field.format === 'date'
										? 'date'
										: field.format === 'datetime-local'
											? 'datetime-local'
											: field.format === 'email'
												? 'email'
												: 'text'}
								value={String(values[key] ?? '')}
								min={field.minimum}
								max={field.maximum}
								on:input={(event) =>
									setValue(
										key,
										field.type === 'integer'
											? Math.trunc(event.currentTarget.valueAsNumber)
											: field.type === 'number'
												? event.currentTarget.valueAsNumber
												: event.currentTarget.value
									)}
							/>
						{/if}
						{#if field.description}<span class="block font-normal leading-4 text-gray-500"
							>{field.description}</span
						>{/if}
					</label>
				{/each}
			</div>
		{/if}

		{#if failedChecks.length}
			<div class="rounded-lg border border-red-200 bg-red-50 px-3 py-2 dark:border-red-900 dark:bg-red-950/30">
				<div class="text-xs font-semibold text-red-700 dark:text-red-200">目前無法執行</div>
				{#each failedChecks as check}<div class="mt-1 text-xs leading-5 text-red-700 dark:text-red-300"
					>{check.message}</div
				>{/each}
			</div>
		{/if}

		{#if needsConfirmation}
			<label class="flex items-start gap-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:bg-amber-950/40 dark:text-amber-100">
				<input
					class="mt-0.5"
					type="checkbox"
					checked={confirmed}
					on:change={(event) => onConfirmedChange(event.currentTarget.checked)}
				/>
				<span>我確認執行此工作流可能會發送內容或改變外部系統資料。</span>
			</label>
		{/if}

		{#if launch.mode === 'instant' || launch.mode === 'form_input'}
			<div class="flex justify-end">
				<button
					type="button"
					class="rounded-lg bg-gray-900 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50 dark:bg-white dark:text-gray-900"
					disabled={running || formMissing || requiredFileMissing || (needsConfirmation && !confirmed)}
					on:click={onExecute}
				>
					{running ? '執行中...' : launch.buttonLabel}
				</button>
			</div>
		{/if}

		{#if preflight}
			<details class="text-xs text-gray-500">
				<summary class="cursor-pointer select-none">執行前檢查</summary>
				<div class="mt-2 space-y-1.5">
					{#each preflight.checks as check}
						<div class="flex gap-2">
							<span
								class={check.status === 'pass'
									? 'text-green-600'
									: check.status === 'warning'
										? 'text-amber-600'
										: 'text-red-600'}>{check.status === 'pass' ? '通過' : check.status === 'warning' ? '確認' : '阻擋'}</span
							><span>{check.message}</span>
						</div>
					{/each}
				</div>
			</details>
		{/if}
	</div>
</div>

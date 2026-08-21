<script lang="ts">
	import Collapsible from '$lib/components/common/Collapsible.svelte';
	import ToolCallDisplay from '$lib/components/common/ToolCallDisplay.svelte';
	import { settings } from '$lib/stores';
	import { WEBUI_API_BASE_URL } from '$lib/constants';

	import Markdown from './Markdown.svelte';
	import ConsecutiveDetailsGroup from './Markdown/ConsecutiveDetailsGroup.svelte';
	import {
		buildOutputDisplayItems,
		type OutputDetailToken,
		type OutputDisplayItem,
		type OutputItem
	} from './structuredOutput';

	export let id = '';
	export let output: OutputItem[] = [];
	export let done = true;
	export let model = null;
	export let save = false;
	export let preview = false;
	export let compactPreview = false;
	export let renderMarkdown = true;
	export let editCodeBlock = true;
	export let topPadding = false;
	export let sourceIds: string[] = [];
	export let formatMessageContent: (content: string) => string = (content) => content;
	export let onSave: any = () => {};
	export let onSourceClick: any = () => {};
	export let onTaskClick: any = () => {};
	export let onUpdate: any = () => {};
	export let onPreview: any = () => {};

	const getDetailTitle = (detailToken: OutputDetailToken): any => detailToken.summary;
	const getDetailAttributes = (detailToken: OutputDetailToken): any => detailToken.attributes;

	$: detailButtonClassName = `w-fit py-0.5 ${
		compactPreview ? 'text-xs' : 'text-[0.9375rem]'
	} text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition`;

	$: displayItems = buildOutputDisplayItems(output) as OutputDisplayItem[];
	const safeUrl = (value: unknown) => {
		const url = typeof value === 'string' ? value.trim() : '';
		return url.startsWith('https://') || url.startsWith('http://') || url.startsWith('/')
			? url
			: '';
	};
	const outputUrl = (item: any) =>
		safeUrl(item?.url) ||
		(item?.fileId
			? `${WEBUI_API_BASE_URL}/files/${encodeURIComponent(String(item.fileId))}/content`
			: '');
</script>

{#each displayItems as displayItem (displayItem.id)}
	{#if displayItem.type === 'message'}
		{#if renderMarkdown}
			<div class="markdown-prose">
				<Markdown
					id={`${id}-${displayItem.id}`}
					content={formatMessageContent(displayItem.text)}
					{model}
					{save}
					{preview}
					{compactPreview}
					{done}
					{editCodeBlock}
					{topPadding}
					{sourceIds}
					{onSourceClick}
					{onTaskClick}
					{onSave}
					{onUpdate}
					{onPreview}
				/>
			</div>
		{:else}
			<div class="whitespace-pre-wrap text-[0.9375rem]">{displayItem.text}</div>
		{/if}
	{:else if displayItem.type === 'workflow_output'}
		{@const workflowOutput = displayItem.output}
		{@const mediaUrl = outputUrl(workflowOutput)}
		{#if workflowOutput.type === 'image' && mediaUrl}
			<figure class="my-2 max-w-2xl">
				<img
					class="max-h-[32rem] max-w-full rounded-lg object-contain"
					src={mediaUrl}
					alt={workflowOutput.alt || workflowOutput.title || '工作流圖片輸出'}
				/>
				{#if workflowOutput.title}<figcaption class="mt-1 text-xs text-gray-500">
						{workflowOutput.title}
					</figcaption>{/if}
			</figure>
		{:else if workflowOutput.type === 'audio' && mediaUrl}
			<div class="my-2 max-w-xl rounded-lg border border-gray-200 p-3 dark:border-gray-800">
				{#if workflowOutput.title}<div class="mb-2 text-sm font-medium">
						{workflowOutput.title}
					</div>{/if}
				<audio class="w-full" controls src={mediaUrl}><track kind="captions" /></audio>
			</div>
		{:else if workflowOutput.type === 'video' && mediaUrl}
			<video
				class="my-2 max-h-[36rem] max-w-full rounded-lg"
				controls
				src={mediaUrl}
				poster={safeUrl(workflowOutput.thumbnailUrl) || undefined}><track kind="captions" /></video
			>
		{:else if workflowOutput.type === 'file' && mediaUrl}
			<a
				class="my-2 flex w-fit max-w-full items-center gap-2 rounded-lg border border-gray-200 px-3 py-2 text-sm hover:bg-gray-50 dark:border-gray-800 dark:hover:bg-gray-900"
				href={mediaUrl}
				target="_blank"
				rel="noreferrer"
			>
				<span class="truncate">{workflowOutput.filename || workflowOutput.title || '開啟檔案'}</span
				>
			</a>
		{:else if workflowOutput.type === 'card'}
			<div class="my-2 max-w-xl rounded-lg border border-gray-200 p-4 dark:border-gray-800">
				<div class="font-semibold">{workflowOutput.title}</div>
				{#if workflowOutput.body}<div
						class="mt-1 whitespace-pre-wrap text-sm text-gray-600 dark:text-gray-300"
					>
						{workflowOutput.body}
					</div>{/if}
				{#if workflowOutput.actions?.length}
					<div class="mt-3 flex flex-wrap gap-2">
						{#each workflowOutput.actions as action}
							{@const actionUrl = safeUrl(action.url)}
							{#if actionUrl}<a
									class="rounded-lg border border-gray-200 px-3 py-1.5 text-sm dark:border-gray-700"
									href={actionUrl}
									target="_blank"
									rel="noreferrer">{String(action.label || action.title || '開啟')}</a
								>{/if}
						{/each}
					</div>
				{/if}
			</div>
		{:else if workflowOutput.type === 'handoff'}
			<div
				class="my-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-100"
			>
				{workflowOutput.reason || '已轉交人工處理'}
			</div>
		{:else if workflowOutput.type === 'json'}
			<pre
				class="my-2 max-h-96 overflow-auto rounded-lg bg-gray-50 p-3 text-xs dark:bg-gray-900">{JSON.stringify(
					workflowOutput.value,
					null,
					2
				)}</pre>
		{:else}
			<div class="my-2 text-sm text-gray-500">此輸出缺少可顯示的媒體網址或檔案。</div>
		{/if}
	{:else if displayItem.type === 'detail_group'}
		<ConsecutiveDetailsGroup
			id={`${id}-${displayItem.id}`}
			tokens={displayItem.tokens}
			messageDone={done}
			{compactPreview}
		>
			<div slot="content">
				{#each displayItem.tokens as detailToken, detailIndex}
					{#if detailToken.attributes?.type === 'tool_calls'}
						<ToolCallDisplay
							id={`${id}-${displayItem.id}-${detailIndex}-tool-call`}
							attributes={detailToken.attributes}
							resultContent={detailToken.text}
							grouped={true}
							open={$settings?.expandDetails ?? false}
							className="w-full"
							buttonClassName={detailButtonClassName}
						/>
					{:else if detailToken.text?.length > 0}
						<Collapsible
							title={getDetailTitle(detailToken)}
							open={$settings?.expandDetails ?? false}
							attributes={getDetailAttributes(detailToken)}
							messageDone={done}
							className="w-full"
							buttonClassName={detailButtonClassName}
						>
							<div class="mb-1.5" slot="content">
								<div class="markdown-prose">
									<Markdown
										id={`${id}-${displayItem.id}-${detailIndex}-detail`}
										content={detailToken.text}
										{done}
										{preview}
										{compactPreview}
										{editCodeBlock}
									/>
								</div>
							</div>
						</Collapsible>
					{:else}
						<Collapsible
							title={getDetailTitle(detailToken)}
							open={false}
							disabled={true}
							attributes={getDetailAttributes(detailToken)}
							messageDone={done}
							className="w-full"
							buttonClassName={detailButtonClassName}
						/>
					{/if}
				{/each}
			</div>
		</ConsecutiveDetailsGroup>
	{:else}
		{@const detailToken = displayItem.token}
		{#if detailToken.attributes?.type === 'tool_calls'}
			<ToolCallDisplay
				id={`${id}-${displayItem.id}-tool-call`}
				attributes={detailToken.attributes}
				resultContent={detailToken.text}
				open={$settings?.expandDetails ?? false}
				className="w-full space-y-2"
				buttonClassName={detailButtonClassName}
			/>
		{:else if detailToken.text?.length > 0}
			<Collapsible
				title={getDetailTitle(detailToken)}
				open={$settings?.expandDetails ?? false}
				attributes={getDetailAttributes(detailToken)}
				messageDone={done}
				className="w-full space-y-2"
				buttonClassName={detailButtonClassName}
			>
				<div class="mb-1.5" slot="content">
					<div class="markdown-prose">
						<Markdown
							id={`${id}-${displayItem.id}-detail`}
							content={detailToken.text}
							{done}
							{preview}
							{compactPreview}
							{editCodeBlock}
						/>
					</div>
				</div>
			</Collapsible>
		{:else}
			<Collapsible
				title={getDetailTitle(detailToken)}
				open={false}
				disabled={true}
				attributes={getDetailAttributes(detailToken)}
				messageDone={done}
				className="w-full space-y-2"
				buttonClassName={detailButtonClassName}
			/>
		{/if}
	{/if}
{/each}

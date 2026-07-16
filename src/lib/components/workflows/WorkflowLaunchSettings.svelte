<script lang="ts">
	import {
		WORKFLOW_LAUNCH_OPTIONS,
		type WorkflowInputField,
		type WorkflowInputFieldType,
		type WorkflowLaunchConfig,
		type WorkflowLaunchMode
	} from './workflowLaunch';

	export let value: WorkflowLaunchConfig;
	export let disabled = false;
	export let onChange: (value: WorkflowLaunchConfig) => void = () => {};

	let newFieldKey = '';

	const update = (patch: Partial<WorkflowLaunchConfig>) => onChange({ ...value, ...patch });

	const changeMode = (mode: WorkflowLaunchMode) => {
		let inputSchema = value.inputSchema;
		if (mode === 'text_input' && !inputSchema.properties.message) {
			inputSchema = {
				...inputSchema,
				properties: {
					message: {
						type: 'string',
						title: '訊息',
						description: '輸入要交給工作流處理的內容。',
						minLength: 1
					},
					...inputSchema.properties
				},
				required: [...new Set(['message', ...inputSchema.required])]
			};
		}
		const option = WORKFLOW_LAUNCH_OPTIONS.find((item) => item.value === mode);
		update({ mode, buttonLabel: option?.label ?? value.buttonLabel, inputSchema });
	};

	const setSchema = (patch: Partial<WorkflowLaunchConfig['inputSchema']>) =>
		update({ inputSchema: { ...value.inputSchema, ...patch } });

	const addField = () => {
		const key = newFieldKey.trim().replace(/[^A-Za-z0-9_]/g, '_');
		if (!key || value.inputSchema.properties[key]) return;
		setSchema({
			properties: {
				...value.inputSchema.properties,
				[key]: { type: 'string', title: key }
			}
		});
		newFieldKey = '';
	};

	const updateField = (key: string, patch: Partial<WorkflowInputField>) => {
		const field = { ...value.inputSchema.properties[key], ...patch };
		if (patch.type && patch.type !== 'string') {
			delete field.format;
			delete field.minLength;
			delete field.maxLength;
		}
		setSchema({ properties: { ...value.inputSchema.properties, [key]: field } });
	};

	const renameField = (oldKey: string, rawKey: string) => {
		const key = rawKey.trim().replace(/[^A-Za-z0-9_]/g, '_');
		if (!key || key === oldKey || value.inputSchema.properties[key]) return;
		const properties = { ...value.inputSchema.properties };
		properties[key] = properties[oldKey];
		delete properties[oldKey];
		const required = value.inputSchema.required.map((item) => (item === oldKey ? key : item));
		const defaultInput = { ...value.defaultInput };
		if (oldKey in defaultInput) {
			defaultInput[key] = defaultInput[oldKey];
			delete defaultInput[oldKey];
		}
		onChange({ ...value, inputSchema: { ...value.inputSchema, properties, required }, defaultInput });
	};

	const removeField = (key: string) => {
		const properties = { ...value.inputSchema.properties };
		delete properties[key];
		const defaultInput = { ...value.defaultInput };
		delete defaultInput[key];
		onChange({
			...value,
			inputSchema: {
				...value.inputSchema,
				properties,
				required: value.inputSchema.required.filter((item) => item !== key)
			},
			defaultInput
		});
	};

	const toggleRequired = (key: string, required: boolean) =>
		setSchema({
			required: required
				? [...new Set([...value.inputSchema.required, key])]
				: value.inputSchema.required.filter((item) => item !== key)
		});

	const parseDefault = (field: WorkflowInputField, raw: string | boolean) => {
		if (field.type === 'boolean') return Boolean(raw);
		if (field.type === 'integer') return raw === '' ? undefined : Math.trunc(Number(raw));
		if (field.type === 'number') return raw === '' ? undefined : Number(raw);
		return raw === '' ? undefined : raw;
	};

	const updateDefault = (key: string, raw: string | boolean) => {
		const next = { ...value.defaultInput };
		const parsed = parseDefault(value.inputSchema.properties[key], raw);
		if (parsed === undefined || Number.isNaN(parsed)) delete next[key];
		else next[key] = parsed;
		update({ defaultInput: next });
	};
</script>

<fieldset class="space-y-5" {disabled}>
	<div>
		<div class="text-sm font-semibold text-gray-900 dark:text-gray-100">使用者如何啟動</div>
		<p class="mt-1 text-xs leading-5 text-gray-500">
			這會決定工作流中心的動作，以及進入聊天後是否立即執行、等待文字、顯示表單或等待檔案。
		</p>
	</div>

	<div class="grid grid-cols-2 gap-2">
		{#each WORKFLOW_LAUNCH_OPTIONS as option}
			<button
				type="button"
				class="rounded-lg border p-3 text-left transition {value.mode === option.value
					? 'border-blue-500 bg-blue-50 dark:border-blue-500 dark:bg-blue-950/40'
					: 'border-gray-200 hover:border-gray-300 dark:border-gray-800 dark:hover:border-gray-700'}"
				on:click={() => changeMode(option.value)}
			>
				<span class="block text-sm font-medium text-gray-900 dark:text-gray-100">{option.label}</span>
				<span class="mt-1 block text-xs leading-5 text-gray-500">{option.description}</span>
			</button>
		{/each}
	</div>

	<label class="block space-y-1.5 text-xs font-medium text-gray-600 dark:text-gray-300">
		<span>動作按鈕名稱</span>
		<input
			class="w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm font-normal outline-none focus:border-blue-500 dark:border-gray-800"
			value={value.buttonLabel}
			maxlength="40"
			on:input={(event) => update({ buttonLabel: event.currentTarget.value })}
		/>
	</label>

	<label class="block space-y-1.5 text-xs font-medium text-gray-600 dark:text-gray-300">
		<span>執行前說明</span>
		<textarea
			class="h-20 w-full resize-none rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm font-normal leading-5 outline-none focus:border-blue-500 dark:border-gray-800"
			value={value.instruction}
			maxlength="500"
			on:input={(event) => update({ instruction: event.currentTarget.value })}
		></textarea>
	</label>

	<div class="grid gap-3 sm:grid-cols-2">
		<label class="block space-y-1.5 text-xs font-medium text-gray-600 dark:text-gray-300">
			<span>執行後</span>
			<select
				class="w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm font-normal outline-none dark:border-gray-800"
				value={value.followUpMode}
				on:change={(event) => update({ followUpMode: event.currentTarget.value as any })}
			>
				<option value="chat_about_result">針對結果繼續聊天</option>
				<option value="rerun_each_message">每則訊息重新執行</option>
			</select>
		</label>
		<label class="block space-y-1.5 text-xs font-medium text-gray-600 dark:text-gray-300">
			<span>執行確認</span>
			<select
				class="w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm font-normal outline-none dark:border-gray-800"
				value={value.confirmation}
				on:change={(event) => update({ confirmation: event.currentTarget.value as any })}
			>
				<option value="risk_only">有外部動作時確認</option>
				<option value="always">每次都確認</option>
				<option value="never">不確認（僅限唯讀）</option>
			</select>
		</label>
	</div>

	{#if value.mode === 'form_input' || value.mode === 'instant'}
		<div class="space-y-3 border-t border-gray-200 pt-4 dark:border-gray-800">
			<div>
				<div class="text-sm font-semibold text-gray-900 dark:text-gray-100">輸入欄位</div>
				<p class="mt-1 text-xs leading-5 text-gray-500">
					立即執行的必要欄位都必須有預設值；填寫條件會依這些欄位產生表單。
				</p>
			</div>

			{#each Object.entries(value.inputSchema.properties) as [key, field] (key)}
				<div class="space-y-3 rounded-lg border border-gray-200 p-3 dark:border-gray-800">
					<div class="flex items-center gap-2">
						<input
							class="min-w-0 flex-1 rounded-md border border-gray-200 bg-transparent px-2.5 py-1.5 font-mono text-xs outline-none dark:border-gray-800"
							value={key}
							on:change={(event) => renameField(key, event.currentTarget.value)}
							aria-label="欄位代碼"
						/>
						<select
							class="rounded-md border border-gray-200 bg-transparent px-2 py-1.5 text-xs dark:border-gray-800"
							value={field.type}
							on:change={(event) =>
								updateField(key, { type: event.currentTarget.value as WorkflowInputFieldType })}
						>
							<option value="string">文字</option>
							<option value="integer">整數</option>
							<option value="number">數字</option>
							<option value="boolean">是／否</option>
						</select>
						<button
							type="button"
							class="rounded-md px-2 py-1.5 text-xs text-red-600 hover:bg-red-50 dark:hover:bg-red-950"
							on:click={() => removeField(key)}>移除</button
						>
					</div>
					<input
						class="w-full rounded-md border border-gray-200 bg-transparent px-2.5 py-1.5 text-sm outline-none dark:border-gray-800"
						value={field.title ?? ''}
						placeholder="顯示名稱"
						on:input={(event) => updateField(key, { title: event.currentTarget.value })}
					/>
					<input
						class="w-full rounded-md border border-gray-200 bg-transparent px-2.5 py-1.5 text-sm outline-none dark:border-gray-800"
						value={field.description ?? ''}
						placeholder="說明這個欄位的用途"
						on:input={(event) => updateField(key, { description: event.currentTarget.value })}
					/>
					<div class="grid gap-2 sm:grid-cols-2">
						<label class="flex items-center gap-2 text-xs text-gray-600 dark:text-gray-300">
							<input
								type="checkbox"
								checked={value.inputSchema.required.includes(key)}
								on:change={(event) => toggleRequired(key, event.currentTarget.checked)}
							/>
							必要欄位
						</label>
						{#if field.type === 'boolean'}
							<label class="flex items-center gap-2 text-xs text-gray-600 dark:text-gray-300">
								<input
									type="checkbox"
									checked={Boolean(value.defaultInput[key])}
									on:change={(event) => updateDefault(key, event.currentTarget.checked)}
								/>
								預設啟用
							</label>
						{:else}
							<input
								class="w-full rounded-md border border-gray-200 bg-transparent px-2.5 py-1.5 text-sm outline-none dark:border-gray-800"
								type={field.type === 'string' ? 'text' : 'number'}
								value={value.defaultInput[key] ?? ''}
								placeholder="預設值（可留空）"
								on:input={(event) => updateDefault(key, event.currentTarget.value)}
							/>
						{/if}
					</div>
					{#if field.type === 'string'}
						<div class="grid gap-2 sm:grid-cols-2">
							<select
								class="rounded-md border border-gray-200 bg-transparent px-2.5 py-1.5 text-xs dark:border-gray-800"
								value={field.format ?? 'text'}
								on:change={(event) => updateField(key, { format: event.currentTarget.value as any })}
							>
								<option value="text">單行文字</option>
								<option value="textarea">多行文字</option>
								<option value="date">日期</option>
								<option value="datetime-local">日期時間</option>
								<option value="email">Email</option>
							</select>
							<input
								class="rounded-md border border-gray-200 bg-transparent px-2.5 py-1.5 text-xs outline-none dark:border-gray-800"
								value={field.enum?.join(', ') ?? ''}
								placeholder="選項，以逗號分隔"
								on:input={(event) =>
									updateField(key, {
										enum: event.currentTarget.value
											.split(',')
											.map((item) => item.trim())
											.filter(Boolean)
									})}
							/>
						</div>
					{/if}
				</div>
			{/each}

			<div class="flex gap-2">
				<input
					class="min-w-0 flex-1 rounded-lg border border-gray-200 bg-transparent px-3 py-2 font-mono text-xs outline-none dark:border-gray-800"
					bind:value={newFieldKey}
					placeholder="欄位代碼，例如 dateFrom"
					on:keydown={(event) => event.key === 'Enter' && (event.preventDefault(), addField())}
				/>
				<button
					type="button"
					class="rounded-lg border border-gray-200 px-3 py-2 text-xs font-medium dark:border-gray-800"
					on:click={addField}>新增欄位</button
				>
			</div>
		</div>
	{/if}

	{#if value.mode === 'file_input'}
		<div class="space-y-3 border-t border-gray-200 pt-4 dark:border-gray-800">
			<div class="text-sm font-semibold text-gray-900 dark:text-gray-100">檔案限制</div>
			<label class="block space-y-1.5 text-xs text-gray-600 dark:text-gray-300">
				<span>允許格式（MIME，以逗號分隔）</span>
				<input
					class="w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm outline-none dark:border-gray-800"
					value={value.fileRules.allowedMimeTypes.join(', ')}
					on:input={(event) =>
						update({
							fileRules: {
								...value.fileRules,
								allowedMimeTypes: event.currentTarget.value
									.split(',')
									.map((item) => item.trim())
									.filter(Boolean)
							}
						})}
				/>
			</label>
			<div class="grid grid-cols-2 gap-3">
				<label class="space-y-1.5 text-xs text-gray-600 dark:text-gray-300">
					<span>最多檔案數</span>
					<input
						class="w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm outline-none dark:border-gray-800"
						type="number"
						min="1"
						max="20"
						value={value.fileRules.maxFiles}
						on:input={(event) =>
							update({ fileRules: { ...value.fileRules, maxFiles: Number(event.currentTarget.value) } })}
					/>
				</label>
				<label class="space-y-1.5 text-xs text-gray-600 dark:text-gray-300">
					<span>單檔上限（MB）</span>
					<input
						class="w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm outline-none dark:border-gray-800"
						type="number"
						min="1"
						max="500"
						value={value.fileRules.maxSizeMB}
						on:input={(event) =>
							update({ fileRules: { ...value.fileRules, maxSizeMB: Number(event.currentTarget.value) } })}
					/>
				</label>
			</div>
		</div>
	{/if}
</fieldset>

<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';

	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';
	import { getModels } from '$lib/apis';
	import {
		createPersonalAPIKey,
		deletePersonalAPIKey,
		getPersonalAPIKeyConnections,
		updatePersonalAPIKey,
		verifyPersonalAPIKey,
		type PersonalAPIKeyConnection,
		type PersonalAPIKeyForm
	} from '$lib/apis/openai';
	import { models } from '$lib/stores';

	let mode: 'personal' | 'platform' = 'platform';
	let connections: PersonalAPIKeyConnection[] = [];
	let loading = true;
	let busyId = '';
	let editingId = '';
	let form: PersonalAPIKeyForm = {
		name: '我的 OpenAI',
		provider: 'openai',
		auth_type: 'bearer',
		api_key: ''
	};

	const providerLabels = {
		openai: 'OpenAI',
		gemini: 'Gemini',
		custom: '其他相容服務'
	};
	const providerDefaultNames = {
		openai: '我的 OpenAI',
		gemini: '我的 Gemini',
		custom: '我的 AI 服務'
	};

	const verificationLabel = (connection: PersonalAPIKeyConnection) => {
		if (connection.verification_status === 'ready') return '連線正常';
		if (connection.verification_status === 'failed') return '上次測試失敗';
		return '尚未測試';
	};
	const errorText = (error: unknown) =>
		typeof error === 'string' ? error : ((error as { message?: string })?.message ?? '未知錯誤');

	const refreshModelList = async () => {
		models.set(await getModels(localStorage.token, null, false, true));
	};

	const loadConnections = async () => {
		loading = true;
		try {
			const response = await getPersonalAPIKeyConnections(localStorage.token);
			mode = response.mode;
			connections = response.connections;
		} catch (error) {
			toast.error(`無法讀取自有 AI 連線：${errorText(error)}`);
		} finally {
			loading = false;
		}
	};

	const changeProvider = (provider: PersonalAPIKeyForm['provider']) => {
		editingId = '';
		form = {
			name: providerDefaultNames[provider],
			provider,
			auth_type: 'bearer',
			api_key: '',
			base_url: provider === 'custom' ? '' : undefined
		};
	};

	const editConnection = (connection: PersonalAPIKeyConnection) => {
		editingId = connection.id;
		form = {
			name: connection.name,
			provider: connection.provider,
			base_url: connection.provider === 'custom' ? connection.base_url : undefined,
			auth_type: connection.auth_type,
			api_key: ''
		};
		document.getElementById('personal-ai-connection-form')?.scrollIntoView({ behavior: 'smooth' });
	};

	const cancelEdit = () => {
		editingId = '';
		changeProvider(form.provider);
	};

	const saveConnection = async () => {
		if (!form.name.trim()) {
			toast.error('請填寫連線名稱。');
			return;
		}
		if (form.provider === 'custom' && !form.base_url?.trim()) {
			toast.error('請填寫相容服務的 API 網址。');
			return;
		}
		if (form.auth_type === 'bearer' && !(form.api_key ?? '').trim()) {
			toast.error('請貼上 API 金鑰。');
			return;
		}

		busyId = 'create';
		try {
			const payload = {
				...form,
				name: form.name.trim(),
				base_url: form.base_url?.trim(),
				api_key: form.api_key?.trim()
			};
			if (editingId) {
				await updatePersonalAPIKey(localStorage.token, editingId, payload);
			} else {
				await createPersonalAPIKey(localStorage.token, payload);
			}
			await loadConnections();
			await refreshModelList();
			form = {
				name: providerDefaultNames[form.provider],
				provider: form.provider,
				auth_type: 'bearer',
				api_key: '',
				base_url: form.provider === 'custom' ? '' : undefined
			};
			editingId = '';
			toast.success('自有 AI 連線已加密儲存，模型清單已切換。');
		} catch (error) {
			toast.error(`儲存失敗：${errorText(error)}`);
		} finally {
			busyId = '';
		}
	};

	const verifyConnection = async (connection: PersonalAPIKeyConnection) => {
		busyId = connection.id;
		try {
			const result = await verifyPersonalAPIKey(localStorage.token, connection.id);
			await loadConnections();
			await refreshModelList();
			toast.success(result.message);
		} catch (error) {
			await loadConnections();
			toast.error(`測試失敗：${errorText(error)}`);
		} finally {
			busyId = '';
		}
	};

	const removeConnection = async (connection: PersonalAPIKeyConnection) => {
		const returnsToPlatform = connections.length === 1;
		if (
			!window.confirm(
				returnsToPlatform
					? `確定移除「${connection.name}」？移除最後一條自有連線後，模型清單會切回平台授權模型。`
					: `確定移除「${connection.name}」？`
			)
		)
			return;

		busyId = connection.id;
		try {
			await deletePersonalAPIKey(localStorage.token, connection.id);
			await loadConnections();
			await refreshModelList();
			toast.success(returnsToPlatform ? '已切回平台授權模型。' : '自有 AI 連線已移除。');
		} catch (error) {
			toast.error(`移除失敗：${errorText(error)}`);
		} finally {
			busyId = '';
		}
	};

	onMount(loadConnections);
</script>

<div id="tab-personal-api-keys" class="h-full overflow-y-auto pr-1.5 scrollbar-hover">
	<div class="max-w-3xl pb-8">
		<h2 class="text-base font-semibold text-gray-900 dark:text-white">自有 AI 連線</h2>
		<p class="mt-1 text-xs leading-5 text-gray-500 dark:text-gray-400">
			連接自己的 OpenAI、Gemini 或 OpenAI 相容 API。金鑰會加密保存，儲存後只顯示末四碼。
		</p>
		<p class="mt-1 text-xs leading-5 text-gray-500 dark:text-gray-400">
			自有 API 只會切換模型來源；聊天、AI 工具、背景處理與工作流仍依平台模型相同費率與規則計費並累計 Token。
		</p>

		<div
			class="mt-3 rounded-lg border px-3 py-2.5 text-xs leading-5 {mode === 'personal'
				? 'border-blue-200 bg-blue-50 text-blue-900 dark:border-blue-900/60 dark:bg-blue-950/30 dark:text-blue-100'
				: 'border-emerald-200 bg-emerald-50 text-emerald-900 dark:border-emerald-900/60 dark:bg-emerald-950/30 dark:text-emerald-100'}"
		>
			<div class="font-semibold">目前使用：{mode === 'personal' ? '自有模型' : '平台授權模型'}</div>
			<div class="mt-0.5">
				{mode === 'personal'
					? '模型清單只會顯示下方自有連線提供的模型，不會混入平台模型。刪除全部連線後會自動切回平台模式。'
					: '尚未設定自有連線，模型清單維持原樣。新增第一條連線後，會立即切換為只使用自有模型。'}
			</div>
		</div>

		<form
			id="personal-ai-connection-form"
			class="mt-4 rounded-lg border border-gray-200 p-4 dark:border-gray-700"
			on:submit|preventDefault={saveConnection}
		>
			<h3 class="text-sm font-semibold text-gray-900 dark:text-white">
				{editingId ? '更新連線' : '新增連線'}
			</h3>
			<div class="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
				<label class="text-xs font-medium text-gray-700 dark:text-gray-200">
					服務類型
					<select
						class="mt-1 h-9 w-full rounded-lg border border-gray-200 bg-transparent px-3 text-sm outline-none focus:border-gray-400 dark:border-gray-700"
						value={form.provider}
						on:change={(event) =>
							changeProvider(
								(event.currentTarget as HTMLSelectElement).value as PersonalAPIKeyForm['provider']
							)}
					>
						<option value="openai">OpenAI</option>
						<option value="gemini">Gemini</option>
						<option value="custom">其他相容服務／自架模型</option>
					</select>
				</label>
				<label class="text-xs font-medium text-gray-700 dark:text-gray-200">
					連線名稱
					<input
						class="mt-1 h-9 w-full rounded-lg border border-gray-200 bg-transparent px-3 text-sm outline-none focus:border-gray-400 dark:border-gray-700"
						maxlength="80"
						placeholder="例如：公司 Gemini"
						bind:value={form.name}
					/>
				</label>
			</div>

			{#if form.provider === 'custom'}
				<div class="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-[1fr_10rem]">
					<label class="text-xs font-medium text-gray-700 dark:text-gray-200">
						API 網址
						<input
							class="mt-1 h-9 w-full rounded-lg border border-gray-200 bg-transparent px-3 text-sm outline-none focus:border-gray-400 dark:border-gray-700"
							placeholder="https://example.com/v1"
							bind:value={form.base_url}
						/>
					</label>
					<label class="text-xs font-medium text-gray-700 dark:text-gray-200">
						驗證方式
						<select
							class="mt-1 h-9 w-full rounded-lg border border-gray-200 bg-transparent px-3 text-sm outline-none focus:border-gray-400 dark:border-gray-700"
							bind:value={form.auth_type}
						>
							<option value="bearer">API Key</option>
							<option value="none">免驗證</option>
						</select>
					</label>
				</div>
				<p class="mt-1 text-[11px] leading-4 text-gray-500">
					服務需提供 OpenAI 相容的 <code>/models</code> 與 <code>/chat/completions</code> API。
				</p>
			{/if}

			{#if form.auth_type === 'bearer'}
				<label class="mt-3 block text-xs font-medium text-gray-700 dark:text-gray-200">
					API 金鑰
					<SensitiveInput
						variant="settings"
						type="password"
						required={false}
						autocomplete="new-password"
						outerClassName="mt-1 flex w-full !h-9"
						placeholder="貼上你的 API 金鑰"
						bind:value={form.api_key}
					/>
				</label>
				{#if editingId}
					<p class="mt-1 text-[11px] text-gray-500">
						基於安全考量不會回填舊金鑰；更新時請重新輸入。
					</p>
				{/if}
			{/if}

			<div class="mt-3 flex justify-end gap-2">
				{#if editingId}
					<button
						type="button"
						class="h-9 rounded-lg border border-gray-200 px-4 text-xs font-medium hover:bg-gray-50 dark:border-gray-700 dark:hover:bg-gray-800"
						on:click={cancelEdit}>取消更新</button
					>
				{/if}
				<button
					type="submit"
					class="h-9 rounded-lg bg-black px-4 text-xs font-medium text-white hover:bg-gray-800 disabled:opacity-50 dark:bg-white dark:text-black dark:hover:bg-gray-100"
					disabled={busyId === 'create'}
				>
					{busyId === 'create' ? '正在連接…' : editingId ? '儲存更新' : '儲存並切換模型清單'}
				</button>
			</div>
		</form>

		{#if loading}
			<div
				class="mt-4 rounded-lg border border-dashed border-gray-200 px-4 py-8 text-center text-xs text-gray-500 dark:border-gray-700"
			>
				正在讀取自有 AI 連線…
			</div>
		{:else if connections.length > 0}
			<div class="mt-4 space-y-3">
				<h3 class="text-sm font-semibold text-gray-900 dark:text-white">已連接的服務</h3>
				{#each connections as connection (connection.id)}
					<section class="rounded-lg border border-gray-200 p-3 dark:border-gray-700">
						<div class="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
							<div class="min-w-0">
								<div class="flex flex-wrap items-center gap-2">
									<div class="truncate text-sm font-semibold text-gray-900 dark:text-white">
										{connection.name}
									</div>
									<span
										class="rounded bg-gray-100 px-1.5 py-0.5 text-[11px] text-gray-600 dark:bg-gray-800 dark:text-gray-300"
										>{providerLabels[connection.provider]}</span
									>
								</div>
								<div class="mt-0.5 break-all text-[11px] text-gray-500">{connection.base_url}</div>
							</div>
							<div class="shrink-0 text-left text-[11px] sm:text-right">
								<div class="text-gray-500">
									{connection.auth_type === 'none'
										? '免驗證'
										: `金鑰：•••• ${connection.key_last4 ?? ''}`}
								</div>
								<div
									class={connection.verification_status === 'ready'
										? 'text-emerald-600 dark:text-emerald-400'
										: connection.verification_status === 'failed'
											? 'text-red-600 dark:text-red-400'
											: 'text-gray-500'}
								>
									{verificationLabel(connection)}
								</div>
							</div>
						</div>

						<div class="mt-3 flex flex-wrap gap-x-4 gap-y-2">
							<button
								type="button"
								class="text-xs font-medium text-blue-600 hover:text-blue-800 disabled:opacity-50 dark:text-blue-400"
								disabled={busyId === connection.id}
								on:click={() => verifyConnection(connection)}>測試並更新模型</button
							>
							<button
								type="button"
								class="text-xs text-gray-500 hover:text-gray-800 dark:text-gray-400 dark:hover:text-gray-200"
								on:click={() => editConnection(connection)}>更新設定</button
							>
							<button
								type="button"
								class="text-xs text-gray-500 hover:text-red-700 disabled:opacity-50 dark:text-gray-400 dark:hover:text-red-300"
								disabled={busyId === connection.id}
								on:click={() => removeConnection(connection)}>移除連線</button
							>
						</div>
					</section>
				{/each}
			</div>
		{/if}
	</div>
</div>

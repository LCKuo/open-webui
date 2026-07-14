<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { toast } from 'svelte-sonner';

	import {
		deleteInteractDataConnectorLocal,
		getInteractDataConnectors,
		scanInteractDataConnectorSchemaLocal,
		updateInteractDataConnectorLocalCredentials,
		type InteractDataConnector
	} from '$lib/apis/interact-data-connectors';

	let loading = true;
	let saving = false;
	let scanning = false;
	let deleting = false;
	let connectors: InteractDataConnector[] = [];
	let selectedId = '';
	let lastScanSummary = '';

	let host = '';
	let port = '';
	let databaseName = '';
	let username = '';
	let password = '';
	let connectionString = '';
	let sslMode = '';

	$: selected = connectors.find((connector) => connector.id === selectedId) ?? null;

	const selectConnector = (connector: InteractDataConnector) => {
		selectedId = connector.id;
		host = connector.host ?? '';
		port = connector.port ? `${connector.port}` : '';
		databaseName = connector.databaseName ?? '';
		username = connector.username ?? '';
		password = '';
		connectionString = '';
		sslMode = connector.sslMode ?? '';
		lastScanSummary = '';
	};

	const load = async () => {
		loading = true;
		try {
			const response = await getInteractDataConnectors(localStorage.token);
			connectors = response.connectors ?? [];
			if (connectors.length === 0) {
				selectedId = '';
				return;
			}

			const current = connectors.find((connector) => connector.id === selectedId);
			selectConnector(current ?? connectors[0]);
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			loading = false;
		}
	};

	const formatTime = (value?: number | null) => {
		if (!value) {
			return '-';
		}
		return new Date(value * 1000).toLocaleString();
	};

	const save = async () => {
		if (!selected) {
			return;
		}

		saving = true;
		try {
			const cleanPort = port.trim();
			if (cleanPort && !/^\d+$/.test(cleanPort)) {
				throw 'Port 必須是數字';
			}
			const payload: Record<string, string | number | null> = {
				host: host.trim() || null,
				port: cleanPort ? Number(cleanPort) : null,
				database_name: databaseName.trim() || null,
				username: username.trim() || null,
				ssl_mode: sslMode.trim() || null
			};

			if (password) {
				payload.password = password;
			}
			if (connectionString.trim()) {
				payload.connection_string = connectionString.trim();
			}

			await updateInteractDataConnectorLocalCredentials(localStorage.token, selected.id, payload);
			toast.success('本地資料庫憑證已儲存');
			await load();
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			saving = false;
		}
	};

	const scan = async () => {
		if (!selected) {
			return;
		}

		scanning = true;
		lastScanSummary = '';
		try {
			const response = await scanInteractDataConnectorSchemaLocal(
				localStorage.token,
				selected.id,
				200
			);
			const tables = Array.isArray((response.schema as { tables?: unknown[] } | undefined)?.tables)
				? ((response.schema as { tables?: unknown[] }).tables ?? [])
				: [];
			lastScanSummary = `Schema 掃描成功：${tables.length} 個資料表`;
			toast.success(lastScanSummary);
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			scanning = false;
		}
	};

	const deleteSelected = async () => {
		if (!selected || deleting) {
			return;
		}

		const confirmed = window.confirm(
			`確定要從 Interact Web Ai 刪除「${selected.name}」？這只會清除 WebUI 本地 connector 與本地保存的連線資料，不會刪除客戶資料庫。`
		);
		if (!confirmed) {
			return;
		}

		deleting = true;
		try {
			await deleteInteractDataConnectorLocal(localStorage.token, selected.id);
			toast.success('資料連線已從 Interact Web Ai 刪除');
			selectedId = '';
			await load();
		} catch (error) {
			toast.error(`${error}`);
		} finally {
			deleting = false;
		}
	};

	onMount(async () => {
		await load();
	});
</script>

<svelte:head>
	<title>企業資料連線 - Interact Web Ai</title>
</svelte:head>

<div class="mx-auto flex w-full max-w-6xl flex-col gap-5 px-4 py-6 md:px-8">
	<div class="flex flex-col gap-2 md:flex-row md:items-end md:justify-between">
		<div>
			<h1 class="text-2xl font-semibold text-gray-900 dark:text-gray-100">企業資料連線</h1>
			<p class="mt-1 text-sm text-gray-500 dark:text-gray-400">
				管理貴公司資料庫在此 Interact Web Ai 節點上的本地憑證與掃描測試。
			</p>
		</div>

		<button
			class="h-9 rounded-full border border-gray-200 px-4 text-sm font-medium text-gray-700 transition hover:bg-gray-50 disabled:opacity-60 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-850"
			type="button"
			disabled={loading}
			on:click={load}
		>
			重新整理
		</button>
	</div>

	<div
		class="rounded-xl border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-900 dark:border-sky-900/60 dark:bg-sky-950/30 dark:text-sky-100"
	>
		主站台負責建立 connector、模型授權與查詢邊界；此頁只保存貴公司在本 Interact Web Ai
		節點上的資料庫密碼或 connection string。 密碼不會回傳主站台，留空儲存會保留既有密碼。
	</div>

	{#if loading}
		<div
			class="rounded-xl border border-gray-200 bg-white p-8 text-center text-sm text-gray-500 dark:border-gray-800 dark:bg-gray-900"
		>
			正在載入資料連線...
		</div>
	{:else if connectors.length === 0}
		<div
			class="rounded-xl border border-gray-200 bg-white p-8 text-center text-sm text-gray-500 dark:border-gray-800 dark:bg-gray-900"
		>
			尚未同步任何資料連線。請先到主站台建立 connector，並同步到此 Interact Web Ai 節點。
		</div>
	{:else}
		<div class="grid min-w-0 gap-5 lg:grid-cols-[minmax(260px,360px)_1fr]">
			<div class="min-w-0 space-y-3">
				{#each connectors as connector}
					<div
						class="w-full rounded-xl border bg-white p-4 text-left transition hover:border-sky-300 hover:shadow-sm dark:bg-gray-900 {selectedId ===
						connector.id
							? 'border-sky-400 ring-2 ring-sky-100 dark:ring-sky-900'
							: 'border-gray-200 dark:border-gray-800'}"
					>
						<button
							type="button"
							class="w-full text-left"
							on:click={() => selectConnector(connector)}
						>
							<div class="flex min-w-0 items-start justify-between gap-3">
								<div class="min-w-0">
									<p class="truncate text-sm font-semibold text-gray-900 dark:text-gray-100">
										{connector.name}
									</p>
									<p class="mt-1 truncate text-xs text-gray-500 dark:text-gray-400">
										{connector.connectorType.toUpperCase()} · {connector.storageMode}
									</p>
								</div>
								<span
									class="shrink-0 rounded-full px-2 py-0.5 text-xs font-medium {connector.enabled
										? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-200'
										: 'bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400'}"
								>
									{connector.enabled ? '啟用' : '停用'}
								</span>
							</div>
							<div class="mt-3 grid min-w-0 gap-1 text-xs text-gray-500 dark:text-gray-400">
								<div class="truncate" title={connector.id}>ID: {connector.id}</div>
								<div class="truncate">DB: {connector.databaseName || '-'}</div>
								<div class="truncate">Host: {connector.host || '-'}</div>
							</div>
						</button>
						<div class="mt-3 border-t border-gray-100 pt-3 dark:border-gray-800">
							<button
								type="button"
								class="text-sm font-medium text-sky-700 hover:text-sky-600 dark:text-sky-300"
								on:click={() =>
									goto(`/workspace/data-connectors/${encodeURIComponent(connector.id)}`)}
							>
								開啟資料模型控制台 →
							</button>
						</div>
					</div>
				{/each}
			</div>

			<form
				class="min-w-0 rounded-xl border border-gray-200 bg-white p-4 shadow-sm dark:border-gray-800 dark:bg-gray-900 md:p-6"
				on:submit|preventDefault={save}
			>
				{#if selected}
					<div
						class="flex flex-col gap-3 border-b border-gray-100 pb-4 dark:border-gray-800 md:flex-row md:items-start md:justify-between"
					>
						<div class="min-w-0">
							<h2 class="truncate text-lg font-semibold text-gray-900 dark:text-gray-100">
								{selected.name}
							</h2>
							<p class="mt-1 text-sm text-gray-500 dark:text-gray-400">
								{selected.connectorType.toUpperCase()} 本地連線憑證
							</p>
						</div>
						<div class="grid shrink-0 gap-1 text-xs text-gray-500 dark:text-gray-400">
							<div>密碼：{selected.hasPassword ? '已設定' : '尚未設定'}</div>
							<div>Connection string：{selected.hasConnectionString ? '已設定' : '尚未設定'}</div>
							<div>更新時間：{formatTime(selected.updatedAt)}</div>
						</div>
					</div>

					<div class="mt-5 grid min-w-0 gap-4 md:grid-cols-2">
						<label class="min-w-0 space-y-1.5">
							<span class="text-sm font-medium text-gray-700 dark:text-gray-200">Host</span>
							<input
								class="h-10 w-full rounded-lg border border-gray-200 bg-transparent px-3 text-sm outline-none transition focus:border-sky-400 focus:ring-2 focus:ring-sky-100 dark:border-gray-700 dark:focus:ring-sky-900"
								bind:value={host}
								placeholder="127.0.0.1"
							/>
						</label>

						<label class="min-w-0 space-y-1.5">
							<span class="text-sm font-medium text-gray-700 dark:text-gray-200">Port</span>
							<input
								class="h-10 w-full rounded-lg border border-gray-200 bg-transparent px-3 text-sm outline-none transition focus:border-sky-400 focus:ring-2 focus:ring-sky-100 dark:border-gray-700 dark:focus:ring-sky-900"
								bind:value={port}
								inputmode="numeric"
								placeholder="5432 / 3306 / 1433"
							/>
						</label>

						<label class="min-w-0 space-y-1.5">
							<span class="text-sm font-medium text-gray-700 dark:text-gray-200">Database name</span
							>
							<input
								class="h-10 w-full rounded-lg border border-gray-200 bg-transparent px-3 text-sm outline-none transition focus:border-sky-400 focus:ring-2 focus:ring-sky-100 dark:border-gray-700 dark:focus:ring-sky-900"
								bind:value={databaseName}
								placeholder="crm"
							/>
						</label>

						<label class="min-w-0 space-y-1.5">
							<span class="text-sm font-medium text-gray-700 dark:text-gray-200">Username</span>
							<input
								class="h-10 w-full rounded-lg border border-gray-200 bg-transparent px-3 text-sm outline-none transition focus:border-sky-400 focus:ring-2 focus:ring-sky-100 dark:border-gray-700 dark:focus:ring-sky-900"
								bind:value={username}
								autocomplete="off"
								placeholder="readonly_user"
							/>
						</label>

						<label class="min-w-0 space-y-1.5">
							<span class="text-sm font-medium text-gray-700 dark:text-gray-200">Password</span>
							<input
								class="h-10 w-full rounded-lg border border-gray-200 bg-transparent px-3 text-sm outline-none transition focus:border-sky-400 focus:ring-2 focus:ring-sky-100 dark:border-gray-700 dark:focus:ring-sky-900"
								bind:value={password}
								autocomplete="new-password"
								type="password"
								placeholder={selected.hasPassword ? '已設定，留空會保留原密碼' : '輸入資料庫密碼'}
							/>
						</label>

						<label class="min-w-0 space-y-1.5">
							<span class="text-sm font-medium text-gray-700 dark:text-gray-200">SSL mode</span>
							<select
								class="h-10 w-full rounded-lg border border-gray-200 bg-transparent px-3 text-sm outline-none transition focus:border-sky-400 focus:ring-2 focus:ring-sky-100 dark:border-gray-700 dark:focus:ring-sky-900"
								bind:value={sslMode}
							>
								<option value="">Default</option>
								<option value="disable">Disable</option>
								<option value="prefer">Prefer</option>
								<option value="require">Require</option>
							</select>
						</label>
					</div>

					<label class="mt-4 block min-w-0 space-y-1.5">
						<span class="text-sm font-medium text-gray-700 dark:text-gray-200"
							>Connection string</span
						>
						<textarea
							class="min-h-24 w-full rounded-lg border border-gray-200 bg-transparent px-3 py-2 text-sm outline-none transition focus:border-sky-400 focus:ring-2 focus:ring-sky-100 dark:border-gray-700 dark:focus:ring-sky-900"
							bind:value={connectionString}
							autocomplete="off"
							placeholder={selected.hasConnectionString
								? '已設定，留空會保留原 connection string'
								: 'postgresql://user:password@host:5432/database'}
						></textarea>
						<p class="text-xs text-gray-500 dark:text-gray-400">
							Connection string 可選；填寫後，資料庫工具會優先使用它，而不是 Host / Port / Database
							欄位。
						</p>
						{#if lastScanSummary}
							<p class="text-xs font-medium text-emerald-700 dark:text-emerald-300">
								{lastScanSummary}
							</p>
						{/if}
					</label>

					<div
						class="mt-6 flex flex-col-reverse gap-2 border-t border-gray-100 pt-4 dark:border-gray-800 sm:flex-row sm:justify-end"
					>
						<button
							type="button"
							class="h-10 rounded-full border border-red-200 px-5 text-sm font-medium text-red-600 transition hover:bg-red-50 disabled:opacity-60 dark:border-red-900 dark:text-red-300 dark:hover:bg-red-950/40"
							disabled={saving || scanning || deleting}
							on:click={deleteSelected}
						>
							{deleting ? '刪除中...' : '刪除本地連線'}
						</button>
						<button
							type="button"
							class="h-10 rounded-full border border-gray-200 px-5 text-sm font-medium text-gray-700 transition hover:bg-gray-50 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-850"
							on:click={() => selectConnector(selected)}
							disabled={saving || deleting}
						>
							重設
						</button>
						<button
							type="submit"
							class="h-10 rounded-full bg-sky-600 px-5 text-sm font-medium text-white transition hover:bg-sky-500 disabled:opacity-60"
							disabled={saving || deleting}
						>
							{saving ? '儲存中...' : '儲存本地憑證'}
						</button>
						<button
							type="button"
							class="h-10 rounded-full bg-gray-900 px-5 text-sm font-medium text-white transition hover:bg-gray-800 disabled:opacity-60 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white"
							disabled={saving || scanning || deleting}
							on:click={scan}
						>
							{scanning ? '掃描中...' : '測試 Schema 掃描'}
						</button>
					</div>
				{/if}
			</form>
		</div>
	{/if}
</div>

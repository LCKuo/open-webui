<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';

	import Modal from '$lib/components/common/Modal.svelte';
	import DocumentArrowDown from '$lib/components/icons/DocumentArrowDown.svelte';
	import Plus from '$lib/components/icons/Plus.svelte';
	import XMark from '$lib/components/icons/XMark.svelte';
	import {
		createUserAPIKey,
		downloadAPITour,
		listUserAPIKeys,
		revokeUserAPIKey,
		type CreatedUserAPIKey,
		type UserAPIKey
	} from '$lib/apis/auths';
	import { copyToClipboard } from '$lib/utils';

	let loading = true;
	let creating = false;
	let downloading = false;
	let keys: UserAPIKey[] = [];
	let showCreateModal = false;
	let showSecretModal = false;
	let keyName = '';
	let expiresInDays: 30 | 90 | 365 | null = 90;
	let createdKey: CreatedUserAPIKey | null = null;
	let secretCopied = false;

	const formatDate = (timestamp: number | null) => {
		if (!timestamp) return '尚未使用';
		return new Intl.DateTimeFormat('zh-TW', {
			year: 'numeric',
			month: 'short',
			day: 'numeric',
			hour: '2-digit',
			minute: '2-digit'
		}).format(new Date(timestamp * 1000));
	};

	const isExpired = (timestamp: number | null) =>
		timestamp !== null && timestamp <= Math.floor(Date.now() / 1000);

	const loadKeys = async () => {
		loading = true;
		try {
			keys = await listUserAPIKeys(localStorage.token);
		} catch (error) {
			toast.error(`無法讀取 API Key：${error}`);
		} finally {
			loading = false;
		}
	};

	const openCreate = () => {
		if (createdKey) {
			showSecretModal = true;
			toast.info('請先確認已保存剛建立的 API Key。');
			return;
		}
		keyName = '';
		expiresInDays = 90;
		showCreateModal = true;
	};

	const createKey = async () => {
		if (!keyName.trim()) {
			toast.error('請輸入用途名稱，之後才知道是哪個系統在使用。');
			return;
		}

		creating = true;
		try {
			createdKey = await createUserAPIKey(localStorage.token, {
				name: keyName.trim(),
				expires_in_days: expiresInDays
			});
			keys = [
				{
					id: createdKey.id,
					name: createdKey.name,
					prefix: createdKey.prefix,
					last_four: createdKey.last_four,
					scopes: createdKey.scopes,
					expires_at: createdKey.expires_at,
					last_used_at: createdKey.last_used_at,
					created_at: createdKey.created_at
				},
				...keys
			];
			showCreateModal = false;
			showSecretModal = true;
			secretCopied = false;
			toast.success('API Key 已建立');
		} catch (error) {
			toast.error(`建立失敗：${error}`);
		} finally {
			creating = false;
		}
	};

	const revokeKey = async (key: UserAPIKey) => {
		if (!window.confirm(`確定撤銷「${key.name}」？使用它的程式會立即停止運作，且無法復原。`)) {
			return;
		}
		try {
			await revokeUserAPIKey(localStorage.token, key.id);
			keys = keys.filter((item) => item.id !== key.id);
			toast.success('API Key 已撤銷');
		} catch (error) {
			toast.error(`撤銷失敗：${error}`);
		}
	};

	const downloadTour = async () => {
		downloading = true;
		try {
			await downloadAPITour(localStorage.token);
			toast.success('串接指南已下載');
		} catch (error) {
			toast.error(`下載失敗：${error}`);
		} finally {
			downloading = false;
		}
	};

	onMount(loadKeys);
</script>

<section
	class="w-full"
	aria-labelledby="api-access-title"
>
	<div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
		<div class="max-w-xl">
			<h3 id="api-access-title" class="text-base font-semibold text-gray-900 dark:text-white">
				API 存取
			</h3>
			<p class="mt-1 text-xs leading-5 text-gray-500 dark:text-gray-400">
				用自己的 Key
				取得此帳號可用的工作區模型與企業原始模型。工作區模型既有的知識庫、工具及提示詞會自動套用。
			</p>
		</div>
		<div class="flex shrink-0 flex-wrap gap-2">
			<button
				type="button"
				class="inline-flex h-9 items-center gap-1.5 rounded-lg border border-gray-200 px-3 text-xs font-medium hover:bg-gray-50 disabled:opacity-50 dark:border-gray-700 dark:hover:bg-gray-800"
				disabled={downloading}
				on:click={downloadTour}
			>
				<DocumentArrowDown className="size-4" />
				{downloading ? '下載中' : '下載 tour.md'}
			</button>
			<button
				type="button"
				class="inline-flex h-9 items-center gap-1.5 rounded-lg bg-black px-3 text-xs font-medium text-white hover:bg-gray-800 dark:bg-white dark:text-black dark:hover:bg-gray-100"
				on:click={openCreate}
			>
				<Plus className="size-4" strokeWidth="2" />
				建立 API Key
			</button>
		</div>
	</div>

	<div
		class="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-100"
	>
		外部 API 推論會依企業方案與實際使用量扣除主站台 Token。模型與企業 ACL
		仍會在每次呼叫時重新檢查。
	</div>

	{#if createdKey && !showSecretModal}
		<div
			class="mt-3 flex flex-col gap-2 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-900 sm:flex-row sm:items-center sm:justify-between dark:border-blue-900/60 dark:bg-blue-950/30 dark:text-blue-100"
		>
			<span>剛建立的「{createdKey.name}」尚未確認保存，完整 Key 仍可在本頁重新顯示。</span>
			<button
				type="button"
				class="h-8 shrink-0 rounded-lg border border-blue-300 px-3 font-medium hover:bg-blue-100 dark:border-blue-800 dark:hover:bg-blue-900/40"
				on:click={() => (showSecretModal = true)}
			>
				重新顯示
			</button>
		</div>
	{/if}

	{#if loading}
		<div
			class="mt-4 rounded-lg border border-dashed border-gray-200 px-4 py-6 text-center text-xs text-gray-500 dark:border-gray-700"
		>
			正在讀取 API Key…
		</div>
	{:else if keys.length === 0}
		<div
			class="mt-4 rounded-lg border border-dashed border-gray-200 px-4 py-6 text-center dark:border-gray-700"
		>
			<div class="text-sm font-medium text-gray-800 dark:text-gray-100">尚未建立 API Key</div>
			<div class="mt-1 text-xs text-gray-500">建議每個串接系統使用不同 Key，日後可獨立撤銷。</div>
		</div>
	{:else}
		<div
			class="mt-4 divide-y divide-gray-100 border-y border-gray-100 dark:divide-gray-800 dark:border-gray-800"
		>
			{#each keys as key (key.id)}
				<div class="flex flex-col gap-3 py-3 sm:flex-row sm:items-center sm:justify-between">
					<div class="min-w-0">
						<div class="flex flex-wrap items-center gap-2">
							<span class="truncate text-sm font-medium text-gray-900 dark:text-white"
								>{key.name}</span
							>
							<code
								class="rounded bg-gray-100 px-1.5 py-0.5 text-[11px] text-gray-600 dark:bg-gray-800 dark:text-gray-300"
							>
								{key.prefix}…{key.last_four}
							</code>
							{#if isExpired(key.expires_at)}
								<span
									class="rounded bg-red-50 px-1.5 py-0.5 text-[11px] font-medium text-red-700 dark:bg-red-950/40 dark:text-red-300"
								>
									已到期
								</span>
							{/if}
						</div>
						<div
							class="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-gray-500 dark:text-gray-400"
						>
							<span>建立：{formatDate(key.created_at)}</span>
							<span>最後使用：{formatDate(key.last_used_at)}</span>
							<span>到期：{key.expires_at ? formatDate(key.expires_at) : '永不到期'}</span>
						</div>
					</div>
					<button
						type="button"
						class="h-8 shrink-0 self-start rounded-lg border border-red-200 px-3 text-xs font-medium text-red-700 hover:bg-red-50 sm:self-center dark:border-red-900 dark:text-red-300 dark:hover:bg-red-950/30"
						on:click={() => revokeKey(key)}
					>
						撤銷
					</button>
				</div>
			{/each}
		</div>
	{/if}
</section>

<Modal bind:show={showCreateModal} size="sm" className="bg-white dark:bg-gray-900 rounded-lg">
	<div class="p-5">
		<div class="flex items-start justify-between gap-4">
			<div>
				<h3 class="text-lg font-semibold text-gray-900 dark:text-white">建立 API Key</h3>
				<p class="mt-1 text-xs leading-5 text-gray-500">用用途命名，之後才能快速辨識與撤銷。</p>
			</div>
			<button
				type="button"
				class="rounded-lg p-2 hover:bg-gray-100 dark:hover:bg-gray-800"
				aria-label="關閉"
				on:click={() => (showCreateModal = false)}
			>
				<XMark className="size-5" />
			</button>
		</div>

		<label
			class="mt-5 block text-xs font-medium text-gray-700 dark:text-gray-200"
			for="api-key-name">用途名稱</label
		>
		<input
			id="api-key-name"
			class="mt-1 h-10 w-full rounded-lg border border-gray-200 bg-transparent px-3 text-sm outline-none focus:border-gray-500 dark:border-gray-700"
			placeholder="例如：CRM 正式環境"
			maxlength="80"
			bind:value={keyName}
		/>

		<label
			class="mt-4 block text-xs font-medium text-gray-700 dark:text-gray-200"
			for="api-key-expiry">有效期限</label
		>
		<select
			id="api-key-expiry"
			class="mt-1 h-10 w-full rounded-lg border border-gray-200 bg-transparent px-3 text-sm outline-none focus:border-gray-500 dark:border-gray-700"
			bind:value={expiresInDays}
		>
			<option value={30}>30 天</option>
			<option value={90}>90 天（建議）</option>
			<option value={365}>1 年</option>
			<option value={null}>永不到期</option>
		</select>
		<p class="mt-2 text-[11px] leading-5 text-gray-500">
			權限固定為「讀取模型」與「聊天推論」，不能操作管理端或其他內部 API。
		</p>

		<div class="mt-5 flex justify-end gap-2">
			<button
				type="button"
				class="h-9 rounded-lg border border-gray-200 px-4 text-xs font-medium dark:border-gray-700"
				on:click={() => (showCreateModal = false)}>取消</button
			>
			<button
				type="button"
				class="h-9 rounded-lg bg-black px-4 text-xs font-medium text-white disabled:opacity-50 dark:bg-white dark:text-black"
				disabled={creating}
				on:click={createKey}
			>
				{creating ? '建立中…' : '建立金鑰'}
			</button>
		</div>
	</div>
</Modal>

<Modal bind:show={showSecretModal} size="sm" className="bg-white dark:bg-gray-900 rounded-lg">
	<div class="p-5">
		<h3 class="text-lg font-semibold text-gray-900 dark:text-white">立即保存這把 Key</h3>
		<p class="mt-1 text-xs leading-5 text-gray-500">
			離開本頁或按下「我已安全保存」後，不會再次顯示完整內容。遺失時請撤銷並建立新 Key。
		</p>

		{#if createdKey}
			<div
				class="mt-4 rounded-lg border border-gray-200 bg-gray-50 p-3 dark:border-gray-700 dark:bg-gray-800"
			>
				<code class="block break-all text-xs text-gray-800 dark:text-gray-100"
					>{createdKey.api_key}</code
				>
			</div>
			<div class="mt-3 flex flex-wrap gap-2">
				<button
					type="button"
					class="h-9 rounded-lg bg-black px-4 text-xs font-medium text-white dark:bg-white dark:text-black"
					on:click={() => {
						copyToClipboard(createdKey?.api_key ?? '');
						secretCopied = true;
						toast.success('API Key 已複製');
					}}
				>
					{secretCopied ? '已複製' : '複製 API Key'}
				</button>
				<button
					type="button"
					class="h-9 rounded-lg border border-gray-200 px-4 text-xs font-medium dark:border-gray-700"
					on:click={downloadTour}
				>
					下載 tour.md
				</button>
			</div>
		{/if}

		<div class="mt-5 flex justify-end">
			<button
				type="button"
				class="h-9 rounded-lg border border-gray-200 px-4 text-xs font-medium dark:border-gray-700"
				on:click={() => {
					showSecretModal = false;
					createdKey = null;
				}}>我已安全保存</button
			>
		</div>
	</div>
</Modal>

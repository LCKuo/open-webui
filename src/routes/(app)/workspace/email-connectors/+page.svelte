<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import { toast } from 'svelte-sonner';
	import {
		getEmailConnectors,
		saveEmailConnector,
		testEmailConnector,
		type EmailConnector,
		type EmailConnectorForm
	} from '$lib/apis/interact-email';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import { WEBUI_API_BASE_URL } from '$lib/constants';

	const emptyForm = (): EmailConnectorForm => ({
		name: 'Resend 企業寄信',
		provider: 'resend',
		enabled: false,
		api_key: '',
		webhook_secret: '',
		from_name: '',
		from_address: '',
		reply_to: '',
		verified_domain: '',
		access_mode: 'company_admins',
		allowed_member_ids: [],
		allowed_group_ids: [],
		allowed_workflow_ids: [],
		allowed_channel_ids: [],
		cc_policy: { allow_runtime_cc: true, default_cc: [], allowed_domains: [], max_cc: 10 },
		recipient_policy: { allowed_domains: [], blocked_domains: [] },
		daily_send_limit: 500,
		max_recipients_per_send: 20
	});

	let connectors: EmailConnector[] = [];
	let form = emptyForm();
	let selected: EmailConnector | null = null;
	let loading = true;
	let saving = false;
	let testing = false;
	let testRecipient = '';
	let apiKeyChanged = false;
	let webhookSecretChanged = false;
	$: hasUnsavedSecrets = apiKeyChanged || webhookSecretChanged;
	const recommendedWebhookEvents = [
		{ value: 'email.sent', label: '已接受寄送' },
		{ value: 'email.delivered', label: '已送達郵件伺服器' },
		{ value: 'email.delivery_delayed', label: '延遲投遞' },
		{ value: 'email.bounced', label: '退信' },
		{ value: 'email.failed', label: '寄送失敗' },
		{ value: 'email.complained', label: '標記為垃圾郵件' },
		{ value: 'email.opened', label: '已開啟' },
		{ value: 'email.clicked', label: '已點擊連結' }
	];
	$: webhookUrl =
		selected && typeof window !== 'undefined'
			? new URL(
					`${WEBUI_API_BASE_URL}/interact/email-webhooks/resend/${encodeURIComponent(selected.id)}`,
					window.location.origin
				).toString()
			: '';

	const selectConnector = (connector: EmailConnector) => {
		selected = connector;
		form = {
			id: connector.id,
			name: connector.name,
			provider: 'resend',
			enabled: connector.enabled,
			api_key: '',
			webhook_secret: '',
			from_name: connector.from_name ?? '',
			from_address: connector.from_address,
			reply_to: connector.reply_to ?? '',
			verified_domain: connector.verified_domain ?? '',
			access_mode: connector.access_mode,
			allowed_member_ids: connector.allowed_member_ids,
			allowed_group_ids: connector.allowed_group_ids,
			allowed_workflow_ids: connector.allowed_workflow_ids,
			allowed_channel_ids: connector.allowed_channel_ids,
			cc_policy: connector.cc_policy,
			recipient_policy: connector.recipient_policy,
			daily_send_limit: connector.daily_send_limit,
			max_recipients_per_send: connector.max_recipients_per_send
		};
		testRecipient = '';
		apiKeyChanged = false;
		webhookSecretChanged = false;
	};

	const formatTestTime = (value: number | null) =>
		value ? new Date(value / 1_000_000).toLocaleString('zh-TW') : '';

	const explainConnectorError = (value: string | null) => {
		if (!value) return '';
		const domainMatch = value.match(/\bthe\s+([a-z0-9.-]+)\s+domain\s+is\s+not\s+verified\b/i);
		if (domainMatch) {
			return `寄件網域 ${domainMatch[1].toLowerCase()} 尚未在 Resend 驗證。請新增網域並完成 DNS 驗證後再測試。`;
		}
		return value;
	};

	$: displayedConnectorError = explainConnectorError(selected?.last_error ?? null);
	$: needsDomainVerification = Boolean(
		selected?.last_error?.match(/\bdomain\s+is\s+not\s+verified\b/i)
	);

	const confirmDiscard = () =>
		!hasUnsavedSecrets || window.confirm('尚有未更新的安全憑證，確定要放棄嗎？');

	const selectWithGuard = (connector: EmailConnector) => {
		if (confirmDiscard()) selectConnector(connector);
	};

	const load = async () => {
		loading = true;
		try {
			connectors = await getEmailConnectors(localStorage.token);
			const requestedId = $page.url.searchParams.get('connectorId') ?? '';
			if (selected) {
				const refreshed = connectors.find((item) => item.id === selected?.id);
				if (refreshed) selectConnector(refreshed);
			} else if (requestedId) {
				const requested = connectors.find((item) => item.id === requestedId);
				if (requested) selectConnector(requested);
				else if (connectors.length > 0) selectConnector(connectors[0]);
			} else if (connectors.length > 0) {
				selectConnector(connectors[0]);
			}
		} catch (error) {
			toast.error(error instanceof Error ? error.message : `${error}`);
		} finally {
			loading = false;
		}
	};

	const save = async () => {
		if (!selected) {
			toast.error('請先在主站台建立 Connector 治理設定。');
			return;
		}
		if (!hasUnsavedSecrets) {
			toast.error('尚未輸入新的 API Key 或 Webhook Signing Secret。');
			return;
		}
		saving = true;
		try {
			const payload = {
				...form,
				api_key: apiKeyChanged ? form.api_key?.trim() : undefined,
				webhook_secret: webhookSecretChanged ? form.webhook_secret?.trim() : undefined,
				from_name: form.from_name?.trim() || undefined,
				reply_to: form.reply_to?.trim() || undefined,
				verified_domain: form.verified_domain?.trim().toLowerCase() || undefined
			};
			const saved = await saveEmailConnector(localStorage.token, payload);
			toast.success('Connector 憑證已加密更新');
			await load();
			selectConnector(saved);
		} catch (error) {
			toast.error(error instanceof Error ? error.message : `${error}`);
		} finally {
			saving = false;
		}
	};

	const test = async () => {
		if (!selected || !testRecipient.trim()) return;
		if (hasUnsavedSecrets) {
			toast.error('請先更新安全憑證，再執行測試寄送。');
			return;
		}
		testing = true;
		try {
			await testEmailConnector(localStorage.token, selected.id, testRecipient.trim());
			toast.success('測試信已交給 Resend，請到收件匣確認結果。');
			await load();
		} catch (error) {
			toast.error(error instanceof Error ? error.message : `${error}`);
		} finally {
			testing = false;
		}
	};

	const copyWebhookUrl = async () => {
		if (!webhookUrl) return;
		await navigator.clipboard.writeText(webhookUrl);
		toast.success('Webhook URL 已複製');
	};

	const handleApiKeyInput = (event: Event) => {
		apiKeyChanged = Boolean((event.currentTarget as HTMLInputElement).value.trim());
	};

	const handleWebhookSecretInput = (event: Event) => {
		webhookSecretChanged = Boolean((event.currentTarget as HTMLInputElement).value.trim());
	};

	onMount(() => {
		void load();
		const warnBeforeLeave = (event: BeforeUnloadEvent) => {
			if (!hasUnsavedSecrets) return;
			event.preventDefault();
		};
		window.addEventListener('beforeunload', warnBeforeLeave);
		return () => window.removeEventListener('beforeunload', warnBeforeLeave);
	});
</script>

<svelte:head><title>企業寄信 Connector</title></svelte:head>

<div class="mx-auto w-full max-w-7xl space-y-6 py-6">
	<header
		class="flex flex-col gap-4 border-b border-gray-200 pb-5 dark:border-gray-800 sm:flex-row sm:items-end sm:justify-between"
	>
		<div>
			<p class="text-sm font-semibold text-blue-700 dark:text-blue-300">寄信憑證</p>
			<h1 class="mt-1 text-2xl font-bold text-gray-950 dark:text-white">企業寄信 Connector</h1>
			<p class="mt-2 max-w-3xl text-sm leading-6 text-gray-500">
				WebUI 只加密保存企業自己的 Resend
				Key。成員、工作流與渠道範圍由主站台治理，寄送時系統會再次檢查全部權限。
			</p>
		</div>
		<button
			type="button"
			class="rounded-lg border border-gray-300 px-4 py-2 text-sm font-semibold dark:border-gray-700"
			on:click={() => confirmDiscard() && window.history.back()}>返回主站台</button
		>
	</header>

	{#if loading}
		<div class="flex min-h-52 items-center justify-center" aria-label="正在讀取 Connector">
			<Spinner />
		</div>
	{:else}
		<div class="grid gap-6 lg:grid-cols-[minmax(240px,320px)_minmax(0,1fr)]">
			<aside aria-label="寄信 Connector 清單" class="space-y-2">
				{#if connectors.length === 0}
					<div
						class="rounded-lg border border-dashed border-gray-300 p-5 text-sm leading-6 text-gray-500 dark:border-gray-700"
					>
						尚未收到主站台的 Connector 治理設定。請先回主站台建立，再到這裡輸入企業自己的 Resend
						Key。
					</div>
				{:else}
					{#each connectors as connector}
						<button
							type="button"
							aria-pressed={selected?.id === connector.id}
							class="w-full rounded-lg border p-4 text-left transition {selected?.id ===
							connector.id
								? 'border-blue-500 bg-blue-50 dark:bg-blue-950/30'
								: 'border-gray-200 hover:border-gray-400 dark:border-gray-800'}"
							on:click={() => selectWithGuard(connector)}
						>
							<span class="flex items-center justify-between gap-3">
								<span class="truncate font-semibold text-gray-900 dark:text-white"
									>{connector.name}</span
								>
								<span
									class="rounded-full px-2 py-0.5 text-xs font-semibold {connector.status ===
									'ready'
										? 'bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-200'
										: connector.status === 'error'
											? 'bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-200'
											: 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300'}"
									>{connector.status === 'ready'
										? '可寄送'
										: connector.status === 'error'
											? '需處理'
											: connector.enabled
												? '設定中'
												: '已停用'}</span
								>
							</span>
							<span class="mt-2 block truncate text-sm text-gray-500">{connector.from_address}</span
							>
							<span class="mt-1 block text-xs text-gray-400"
								>Key {connector.key_last4 ? `末四碼 ${connector.key_last4}` : '尚未設定'}</span
							>
						</button>
					{/each}
				{/if}
			</aside>

			<main class="min-w-0 space-y-7">
				<section aria-labelledby="connector-basic" class="space-y-4">
					<div>
						<h2 id="connector-basic" class="text-lg font-bold text-gray-900 dark:text-white">
							治理設定（唯讀）
						</h2>
						<p class="mt-1 text-sm text-gray-500">
							寄件身分、啟用狀態與存取範圍請回主站台修改，避免兩邊設定漂移。
						</p>
					</div>
					<div class="grid gap-4 sm:grid-cols-2">
						<label class="field"><span>名稱</span><input bind:value={form.name} disabled /></label>
						<label class="field"
							><span>寄件顯示名稱</span><input bind:value={form.from_name} disabled /></label
						>
						<label class="field"
							><span>寄件 Email</span><input
								bind:value={form.from_address}
								type="email"
								disabled
							/></label
						>
						<label class="field"
							><span>Reply-To</span><input
								bind:value={form.reply_to}
								type="email"
								disabled
							/></label
						>
						<label class="field"
							><span>已驗證網域</span><input bind:value={form.verified_domain} disabled /></label
						>
						<label class="field"
							><span>成員存取範圍</span><select bind:value={form.access_mode} disabled
								><option value="company_admins">僅企業管理員</option><option
									value="all_company_members">所有企業成員</option
								><option value="selected_members">指定成員</option><option value="selected_groups"
									>指定群組</option
								></select
							></label
						>
					</div>
					<label
						class="flex items-start gap-3 rounded-lg border border-gray-200 p-4 dark:border-gray-800"
						><input class="mt-1" type="checkbox" bind:checked={form.enabled} disabled /><span
							><strong class="block text-sm text-gray-900 dark:text-white">啟用正式寄送</strong
							><span class="mt-1 block text-sm leading-5 text-gray-500"
								>啟用不會略過核准、Connector ACL、企業邊界或收件人政策。</span
							></span
						></label
					>
				</section>

				<section
					aria-labelledby="connector-secret"
					class="space-y-4 border-t border-gray-200 pt-6 dark:border-gray-800"
				>
					<div>
						<h2 id="connector-secret" class="text-lg font-bold text-gray-900 dark:text-white">
							安全憑證
						</h2>
						<p class="mt-1 text-sm text-gray-500">
							API Key 用來寄信；Webhook 用來回報送達、退信、開啟與點擊狀態。兩者是不同憑證。
						</p>
					</div>
					<div class="grid gap-3 sm:grid-cols-3" aria-label="寄信設定完成度">
						<div class="credential-status">
							<span class="status-kicker">必要</span>
							<strong>Resend API Key</strong>
							<span
								class:status-ready={selected?.has_api_key}
								class:status-pending={!selected?.has_api_key}
							>
								{selected?.has_api_key ? '已安全儲存' : '尚未設定'}
							</span>
							<small
								>{selected?.has_api_key ? `末四碼 ${selected.key_last4}` : '目前無法寄信'}</small
							>
						</div>
						<div class="credential-status">
							<span class="status-kicker">選配但建議</span>
							<strong>Webhook 驗證</strong>
							<span
								class:status-ready={selected?.has_webhook_secret}
								class:status-pending={!selected?.has_webhook_secret}
							>
								{selected?.has_webhook_secret ? '簽章密鑰已儲存' : '尚未設定'}
							</span>
							<small
								>{selected?.has_webhook_secret
									? '可驗證 Resend 事件來源'
									: '仍可寄信，但不會追蹤投遞結果'}</small
							>
						</div>
						<div class="credential-status">
							<span class="status-kicker">驗證結果</span>
							<strong>測試寄送</strong>
							<span
								class:status-ready={selected?.last_test_at && !selected?.last_error}
								class:status-error={selected?.last_error}
								class:status-pending={!selected?.last_test_at}
							>
								{selected?.last_test_at
									? selected.last_error
										? '最近測試失敗'
										: '最近測試成功'
									: '尚未測試'}
							</span>
							<small
								>{selected?.last_test_at
									? formatTestTime(selected.last_test_at)
									: '儲存 Key 後寄送測試信'}</small
							>
						</div>
					</div>
					{#if displayedConnectorError}
						<div
							class="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-900 dark:border-red-900/70 dark:bg-red-950/30 dark:text-red-100"
							role="alert"
						>
							<strong>最近一次測試未通過</strong>
							<p class="mt-1 leading-6">{displayedConnectorError}</p>
							{#if needsDomainVerification}
								<a
									class="mt-3 inline-flex font-semibold text-red-800 underline underline-offset-4 dark:text-red-200"
									href="https://resend.com/domains"
									target="_blank"
									rel="noreferrer"
								>
									開啟 Resend Domains
								</a>
							{/if}
						</div>
					{/if}

					<label class="field" for="resend-api-key">
						<span>{selected?.has_api_key ? '更換 Resend API Key' : 'Resend API Key'}</span>
						<input
							id="resend-api-key"
							bind:value={form.api_key}
							type="password"
							autocomplete="new-password"
							on:input={handleApiKeyInput}
							aria-describedby="resend-api-key-help"
							placeholder={selected?.has_api_key ? '留空會保留目前的 Key' : 're_...'}
						/>
						<small id="resend-api-key-help" class="font-normal leading-5 text-gray-500">
							{selected?.has_api_key
								? '目前 Key 已加密保存且不會回傳瀏覽器。只有輸入新值並更新，才會覆蓋原 Key。'
								: '請貼上 Resend 的 Sending access Key；建議在 Resend 將它限制為上方已驗證網域。'}
						</small>
					</label>

					{#if selected}
						<div
							class="space-y-5 rounded-lg border border-gray-200 p-4 dark:border-gray-800 sm:p-5"
						>
							<div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
								<div>
									<h3 class="font-bold text-gray-900 dark:text-white">設定投遞狀態追蹤</h3>
									<p class="mt-1 max-w-3xl text-sm leading-6 text-gray-500">
										Webhook
										不負責寄信。設定後，系統才能把信件從「已送出」更新為已送達、退信、開啟或點擊。
									</p>
								</div>
								<a
									href="https://resend.com/webhooks"
									target="_blank"
									rel="noreferrer"
									class="inline-flex min-h-10 shrink-0 items-center justify-center rounded-lg border border-gray-300 px-4 text-sm font-semibold hover:bg-gray-50 dark:border-gray-700 dark:hover:bg-gray-900"
									>開啟 Resend Webhooks</a
								>
							</div>

							<ol class="space-y-5" aria-label="Resend Webhook 設定步驟">
								<li class="setup-step">
									<span class="step-number" aria-hidden="true">1</span>
									<div>
										<strong>在 Resend 建立 Webhook</strong>
										<p>進入 Webhooks，按 Add Webhook，將下方網址貼到 Endpoint URL。</p>
										<div class="mt-2 flex flex-col gap-2 sm:flex-row">
											<input
												id="resend-webhook-url"
												aria-label="Resend Webhook URL"
												class="min-w-0 flex-1 rounded-lg border border-gray-300 bg-gray-50 px-3 py-2 font-mono text-xs dark:border-gray-700 dark:bg-gray-900"
												value={webhookUrl}
												readonly
											/>
											<button
												type="button"
												class="rounded-lg border border-gray-300 px-4 py-2 text-sm font-semibold hover:bg-gray-50 dark:border-gray-700 dark:hover:bg-gray-900"
												on:click={copyWebhookUrl}>複製網址</button
											>
										</div>
									</div>
								</li>
								<li class="setup-step">
									<span class="step-number" aria-hidden="true">2</span>
									<div>
										<strong>訂閱郵件事件</strong>
										<p>在 Events 勾選下列八項；事件名稱必須包含 <code>email.</code> 前綴。</p>
										<div class="mt-2 flex flex-wrap gap-2">
											{#each recommendedWebhookEvents as event}
												<span class="event-chip" title={event.label}>{event.value}</span>
											{/each}
										</div>
									</div>
								</li>
								<li class="setup-step">
									<span class="step-number" aria-hidden="true">3</span>
									<div class="min-w-0 flex-1">
										<strong>儲存 Signing Secret</strong>
										<p>
											建立 Webhook 後，從該 Webhook 詳細頁複製 <code>whsec_...</code
											>。它用來驗證事件確實來自 Resend，不是 API Key。
										</p>
										<label class="field mt-2" for="resend-webhook-secret">
											<span
												>{selected.has_webhook_secret
													? '更換 Webhook Signing Secret'
													: 'Webhook Signing Secret'}</span
											>
											<input
												id="resend-webhook-secret"
												bind:value={form.webhook_secret}
												type="password"
												autocomplete="new-password"
												on:input={handleWebhookSecretInput}
												placeholder={selected.has_webhook_secret
													? '留空會保留目前的 Signing Secret'
													: 'whsec_...'}
											/>
											<small class="font-normal leading-5 text-gray-500">
												{selected.has_webhook_secret
													? '簽章密鑰已加密保存。只有輸入新值並更新，才會覆蓋原值。'
													: '只想先測試寄信時可暫時略過，之後再回來補上。'}
											</small>
										</label>
									</div>
								</li>
								<li class="setup-step">
									<span class="step-number" aria-hidden="true">4</span>
									<div>
										<strong>更新憑證並寄測試信</strong>
										<p>
											按頁面下方「更新安全憑證」，再使用「測試寄送」確認 API Key、網域與寄件地址。
										</p>
									</div>
								</li>
							</ol>
						</div>
					{/if}
				</section>

				<section
					aria-labelledby="connector-acl"
					class="space-y-4 border-t border-gray-200 pt-6 dark:border-gray-800"
				>
					<div>
						<h2 id="connector-acl" class="text-lg font-bold text-gray-900 dark:text-white">
							目前治理範圍
						</h2>
						<p class="mt-1 text-sm text-gray-500">
							詳細成員、群組、工作流與渠道清單由主站台管理；這裡顯示同步到執行端的結果。
						</p>
					</div>
					<dl class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
						<div class="summary">
							<dt>指定成員</dt>
							<dd>{form.allowed_member_ids.length}</dd>
						</div>
						<div class="summary">
							<dt>指定群組</dt>
							<dd>{form.allowed_group_ids.length}</dd>
						</div>
						<div class="summary">
							<dt>允許工作流</dt>
							<dd>{form.allowed_workflow_ids.length || '不限'}</dd>
						</div>
						<div class="summary">
							<dt>允許渠道</dt>
							<dd>{form.allowed_channel_ids.length || '不限'}</dd>
						</div>
					</dl>
				</section>

				<section
					aria-labelledby="connector-policy"
					class="space-y-4 border-t border-gray-200 pt-6 dark:border-gray-800"
				>
					<div>
						<h2 id="connector-policy" class="text-lg font-bold text-gray-900 dark:text-white">
							收件人政策與限制（唯讀）
						</h2>
						<p class="mt-1 text-sm text-gray-500">封鎖網域優先於允許網域；請由主站台修改並同步。</p>
					</div>
					<div class="grid gap-4 sm:grid-cols-2">
						<label class="field"
							><span>每日寄送上限</span><input
								bind:value={form.daily_send_limit}
								type="number"
								disabled
							/></label
						>
						<label class="field"
							><span>單封最大收件人數</span><input
								bind:value={form.max_recipients_per_send}
								type="number"
								disabled
							/></label
						>
						<label class="field"
							><span>允許收件網域</span><textarea
								value={(form.recipient_policy.allowed_domains ?? []).join('\n')}
								disabled
							></textarea></label
						>
						<label class="field"
							><span>封鎖收件網域</span><textarea
								value={(form.recipient_policy.blocked_domains ?? []).join('\n')}
								disabled
							></textarea></label
						>
						<label class="field"
							><span>預設 CC</span><textarea
								value={(form.cc_policy.default_cc ?? []).join('\n')}
								disabled
							></textarea></label
						>
						<label class="field"
							><span>允許 CC 網域</span><textarea
								value={(form.cc_policy.allowed_domains ?? []).join('\n')}
								disabled
							></textarea></label
						>
					</div>
					<label class="flex items-center gap-3 text-sm"
						><input
							type="checkbox"
							checked={form.cc_policy.allow_runtime_cc !== false}
							disabled
						/>允許使用者在每次工作流輸入 CC</label
					>
				</section>

				{#if selected}
					<section
						aria-labelledby="connector-test"
						class="space-y-3 border-t border-gray-200 pt-6 dark:border-gray-800"
					>
						<div>
							<h2 id="connector-test" class="text-lg font-bold text-gray-900 dark:text-white">
								測試寄送
							</h2>
							<p class="mt-1 text-sm text-gray-500">
								先寄一封測試信，確認網域、Key 與寄件身分均可使用。
							</p>
						</div>
						<div class="flex flex-col gap-2 sm:flex-row">
							<label class="sr-only" for="test-recipient">測試收件 Email</label><input
								id="test-recipient"
								class="min-w-0 flex-1 rounded-lg border border-gray-300 bg-transparent px-3 py-2 text-sm dark:border-gray-700"
								bind:value={testRecipient}
								type="email"
								placeholder="測試收件 Email"
							/><button
								type="button"
								class="rounded-lg border border-gray-300 px-4 py-2 text-sm font-semibold disabled:opacity-50 dark:border-gray-700"
								disabled={testing ||
									hasUnsavedSecrets ||
									!selected.has_api_key ||
									!testRecipient.trim()}
								title={!selected.has_api_key
									? '請先設定 Resend API Key'
									: hasUnsavedSecrets
										? '請先更新安全憑證'
										: undefined}
								on:click={test}>{testing ? '寄送中...' : '寄送測試信'}</button
							>
						</div>
						{#if selected.last_error}<p
								role="alert"
								class="rounded-lg bg-red-50 p-3 text-sm text-red-700 dark:bg-red-950/30 dark:text-red-200"
							>
								最近錯誤：{selected.last_error}
							</p>{/if}
					</section>
				{/if}

				{#if selected}
					<div
						class="sticky bottom-0 flex flex-wrap items-center justify-end gap-3 border-t border-gray-200 bg-white/95 py-4 backdrop-blur dark:border-gray-800 dark:bg-gray-950/95"
					>
						{#if hasUnsavedSecrets}<span
								class="text-sm font-medium text-amber-700 dark:text-amber-300"
								role="status">安全憑證尚未更新</span
							>{/if}
						<button
							type="button"
							class="rounded-lg bg-blue-600 px-5 py-2.5 text-sm font-semibold text-white disabled:opacity-50"
							disabled={saving || (!apiKeyChanged && !webhookSecretChanged)}
							on:click={save}>{saving ? '更新中...' : '更新安全憑證'}</button
						>
					</div>
				{/if}
			</main>
		</div>
	{/if}
</div>

<style>
	.field {
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
		font-size: 0.875rem;
		font-weight: 650;
		color: #374151;
	}
	.field :global(input),
	.field :global(select),
	.field :global(textarea) {
		width: 100%;
		border: 1px solid #d1d5db;
		border-radius: 0.5rem;
		background: transparent;
		padding: 0.6rem 0.75rem;
		font-size: 0.875rem;
		font-weight: 400;
		outline: none;
	}
	.field :global(textarea) {
		min-height: 5.5rem;
		resize: vertical;
	}
	.field :global(input:focus),
	.field :global(select:focus),
	.field :global(textarea:focus) {
		border-color: #2563eb;
		box-shadow: 0 0 0 3px rgb(37 99 235 / 0.14);
	}
	.summary {
		border: 1px solid #e5e7eb;
		border-radius: 0.5rem;
		padding: 0.8rem;
	}
	.summary dt {
		color: #6b7280;
		font-size: 0.75rem;
		font-weight: 650;
	}
	.summary dd {
		margin-top: 0.2rem;
		color: #111827;
		font-size: 1.125rem;
		font-weight: 700;
	}
	.credential-status {
		display: flex;
		min-height: 8.5rem;
		flex-direction: column;
		align-items: flex-start;
		gap: 0.35rem;
		border: 1px solid #e5e7eb;
		border-radius: 0.5rem;
		padding: 0.9rem;
	}
	.credential-status strong {
		color: #111827;
		font-size: 0.875rem;
	}
	.credential-status small {
		color: #6b7280;
		font-size: 0.75rem;
		line-height: 1.25rem;
	}
	.status-kicker {
		border-radius: 9999px;
		background: #f3f4f6;
		padding: 0.15rem 0.5rem;
		color: #4b5563;
		font-size: 0.6875rem;
		font-weight: 700;
	}
	.status-ready,
	.status-pending,
	.status-error {
		font-size: 0.875rem;
		font-weight: 700;
	}
	.status-ready {
		color: #047857;
	}
	.status-pending {
		color: #a16207;
	}
	.status-error {
		color: #b91c1c;
	}
	.setup-step {
		display: flex;
		align-items: flex-start;
		gap: 0.75rem;
	}
	.setup-step p {
		margin-top: 0.25rem;
		color: #6b7280;
		font-size: 0.8125rem;
		line-height: 1.35rem;
	}
	.step-number {
		display: inline-flex;
		height: 1.75rem;
		width: 1.75rem;
		flex: 0 0 auto;
		align-items: center;
		justify-content: center;
		border-radius: 9999px;
		background: #eff6ff;
		color: #1d4ed8;
		font-size: 0.75rem;
		font-weight: 800;
	}
	.event-chip {
		border: 1px solid #d1d5db;
		border-radius: 0.375rem;
		background: #f9fafb;
		padding: 0.25rem 0.45rem;
		color: #374151;
		font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
		font-size: 0.6875rem;
	}
	:global(.dark) .field {
		color: #e5e7eb;
	}
	:global(.dark) .field :global(input),
	:global(.dark) .field :global(select),
	:global(.dark) .field :global(textarea) {
		border-color: #374151;
		color: #f9fafb;
	}
	:global(.dark) .summary {
		border-color: #374151;
	}
	:global(.dark) .credential-status {
		border-color: #374151;
	}
	:global(.dark) .credential-status strong {
		color: #f9fafb;
	}
	:global(.dark) .status-kicker,
	:global(.dark) .event-chip {
		border-color: #374151;
		background: #111827;
		color: #d1d5db;
	}
	:global(.dark) .summary dd {
		color: #f9fafb;
	}
</style>

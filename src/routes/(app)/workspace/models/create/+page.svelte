<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { goto } from '$app/navigation';
	import { config, models, settings } from '$lib/stores';
	import { WEBUI_BASE_URL } from '$lib/constants';

	import { onMount, getContext } from 'svelte';
	import { createNewModel } from '$lib/apis/models';
	import { getModels } from '$lib/apis';

	import ModelEditor from '$lib/components/workspace/Models/ModelEditor.svelte';

	const i18n = getContext('i18n');
	let activationError = '';

	const errorMessage = (error: unknown) => {
		if (typeof error === 'string') return error;
		if (error && typeof error === 'object' && 'detail' in error) {
			return String((error as { detail?: unknown }).detail ?? '');
		}
		return String(error || $i18n.t('An error occurred while saving the model.'));
	};

	const agentTemplates = {
		'crm-bd': {
			label: 'BD 新客開發',
			model: {
				id: 'bd-prospecting-agent',
				name: 'BD 新客開發 Agent',
				base_model_id: null,
				meta: {
					description: '搜尋並查證公開來源，整理潛在客戶、證據與待確認的產品切入點。',
					tags: [{ name: 'BD' }, { name: '新客開發' }],
					suggestion_prompts: null,
					capabilities: {
						web_search: true,
						citations: true,
						builtin_tools: true,
						status_updates: true
					}
				},
				params: {
					system:
						'你是企業的 BD 新客開發 Agent。先確認目標產業、地區與排除條件，再搜尋公開網路。每家公司都要附可追溯來源，區分已查證事實與待確認假設；不可宣稱已寫入 CRM，也不可存取未授權的企業資料。'
				}
			}
		},
		'crm-am': {
			label: 'AM 客戶經營',
			model: {
				id: 'am-account-growth-agent',
				name: 'AM 客戶經營 Agent',
				base_model_id: null,
				meta: {
					description: '依已授權 CRM 資料分析回購週期、流失風險、交叉銷售與下一步跟進。',
					tags: [{ name: 'AM' }, { name: '客戶經營' }],
					suggestion_prompts: null,
					capabilities: {
						citations: true,
						builtin_tools: true,
						status_updates: true
					}
				},
				params: {
					system:
						'你是企業的 AM 客戶經營 Agent。只能使用目前企業與 Channel 已授權的 CRM 語意資料及知識庫，分析回購、流失、交叉銷售與跟進建議。回答要標示資料日期、判斷依據、信心與缺口；信件只能先產草稿，未經人工確認不得寄送。'
				}
			}
		}
	} as const;

	const onSubmit = async (modelInfo) => {
		activationError = '';
		if ($models.find((m) => m.id === modelInfo.id)) {
			toast.error(
				$i18n.t(
					"Error: A model with the ID '{{modelId}}' already exists. Please select a different ID to proceed.",
					{ modelId: modelInfo.id }
				)
			);
			return;
		}

		if (modelInfo.id === '') {
			toast.error($i18n.t('Error: Model ID cannot be empty. Please enter a valid ID to proceed.'));
			return;
		}

		if (modelInfo) {
			const res = await createNewModel(localStorage.token, {
				...modelInfo,
				meta: {
					...modelInfo.meta,
					profile_image_url:
						modelInfo.meta.profile_image_url ?? `${WEBUI_BASE_URL}/static/favicon.png`,
					suggestion_prompts: modelInfo.meta.suggestion_prompts
						? modelInfo.meta.suggestion_prompts.filter((prompt) => prompt.content !== '')
						: null
				},
				params: { ...modelInfo.params }
			}).catch((error) => {
				activationError = errorMessage(error);
				toast.error(activationError);
				return null;
			});

			if (res) {
				await models.set(
					await getModels(
						localStorage.token,
						$config?.features?.enable_direct_connections && ($settings?.directConnections ?? null)
					)
				);
				toast.success($i18n.t('Model created successfully!'));
				await goto('/workspace/models');
			}
		}
	};

	let model: Record<string, unknown> | null = null;
	let appliedTemplateLabel = '';

	onMount(() => {
		const handleMessageEvent = async (event: MessageEvent) => {
			if (
				!['https://openwebui.com', 'https://www.openwebui.com', 'http://localhost:9999'].includes(
					event.origin
				)
			) {
				return;
			}

			try {
				let data = JSON.parse(event.data);

				if (data?.info) {
					data = data.info;
				}

				model = data;
			} catch (e) {
				console.error('Failed to parse message data:', e);
			}
		};
		window.addEventListener('message', handleMessageEvent);

		if (window.opener ?? false) {
			window.opener.postMessage('loaded', '*');
		}

		if (sessionStorage.model) {
			model = JSON.parse(sessionStorage.model);
			sessionStorage.removeItem('model');
		} else {
			const templateKey = new URL(window.location.href).searchParams.get('template');
			const template =
				templateKey === 'crm-bd' || templateKey === 'crm-am' ? agentTemplates[templateKey] : null;
			if (template) {
				model = JSON.parse(JSON.stringify(template.model));
				appliedTemplateLabel = template.label;
			}
		}

		return () => {
			window.removeEventListener('message', handleMessageEvent);
		};
	});
</script>

{#key model}
	{#if activationError}
		<div
			class="mx-auto mb-4 w-full max-w-5xl rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-100"
			role="alert"
		>
			<p class="font-medium">無法啟用此模型</p>
			<p class="mt-1">{activationError}</p>
		</div>
	{/if}
	{#if appliedTemplateLabel}
		<div
			class="mx-auto mb-4 w-full max-w-5xl rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-950 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-100"
			role="status"
		>
			<p class="font-medium">已套用「{appliedTemplateLabel}」範本</p>
			<p class="mt-1">
				請選擇企業可用的基礎模型，再確認知識庫、工具及存取權限。範本不會自動擴大資料權限。
			</p>
		</div>
	{/if}
	<ModelEditor
		{model}
		{onSubmit}
		onBack={async () => {
			await goto('/workspace/models');
		}}
	/>
{/key}

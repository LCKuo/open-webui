<script>
	import { toast } from 'svelte-sonner';
	import { goto } from '$app/navigation';

	import { onMount, getContext } from 'svelte';
	const i18n = getContext('i18n');

	import { page } from '$app/stores';
	import { config, models, settings } from '$lib/stores';

	import { getModelById, updateModelById } from '$lib/apis/models';

	import { getModels } from '$lib/apis';
	import ModelEditor from '$lib/components/workspace/Models/ModelEditor.svelte';

	let model = null;
	let activationError = '';
	const errorMessage = (error) =>
		typeof error === 'string' ? error : String(error?.detail ?? error ?? '模型儲存失敗。');

	onMount(async () => {
		const _id = $page.url.searchParams.get('id');
		if (_id) {
			model = await getModelById(localStorage.token, _id).catch((e) => {
				return null;
			});

			if (!model) {
				goto('/workspace/models');
			}

			if (!model?.write_access) {
				toast.error($i18n.t('You do not have permission to edit this model'));
				goto('/workspace/models');
			}
		} else {
			goto('/workspace/models');
		}
	});

	const onSubmit = async (modelInfo) => {
		activationError = '';
		const res = await updateModelById(localStorage.token, modelInfo.id, modelInfo).catch(
			(error) => {
				activationError = errorMessage(error);
				toast.error(activationError);
				return null;
			}
		);

		if (res) {
			await models.set(
				await getModels(
					localStorage.token,
					$config?.features?.enable_direct_connections && ($settings?.directConnections ?? null)
				)
			);
			toast.success($i18n.t('Model updated successfully'));
			await goto('/workspace/models');
		}
	};
</script>

{#if model}
	{#if activationError}
		<div
			class="mx-auto mb-4 w-full max-w-5xl rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-100"
			role="alert"
		>
			<p class="font-medium">模型未啟用</p>
			<p class="mt-1">{activationError}</p>
		</div>
	{/if}
	<ModelEditor
		edit={true}
		{model}
		{onSubmit}
		onBack={async () => {
			await goto('/workspace/models');
		}}
	/>
{/if}

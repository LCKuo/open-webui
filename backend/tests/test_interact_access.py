from open_webui.utils.interact_access import append_channel_for_selected_model


def test_replacement_channel_is_appended_for_an_existing_model_grant():
    assert append_channel_for_selected_model(
        access_mode='selected_channels',
        allowed_model_ids='["bd-agent"]',
        allowed_channel_ids='["old-channel"]',
        model_id='bd-agent',
        channel_id='new-channel',
    ) == ['old-channel', 'new-channel']


def test_channel_sync_does_not_widen_other_access_modes_or_models():
    assert append_channel_for_selected_model(
        access_mode='company_admins',
        allowed_model_ids=['bd-agent'],
        allowed_channel_ids=[],
        model_id='bd-agent',
        channel_id='new-channel',
    ) is None
    assert append_channel_for_selected_model(
        access_mode='selected_channels',
        allowed_model_ids=['am-agent'],
        allowed_channel_ids=[],
        model_id='bd-agent',
        channel_id='new-channel',
    ) is None


def test_channel_sync_is_idempotent():
    assert append_channel_for_selected_model(
        access_mode='selected_channels',
        allowed_model_ids=['bd-agent'],
        allowed_channel_ids=['new-channel'],
        model_id='bd-agent',
        channel_id='new-channel',
    ) is None

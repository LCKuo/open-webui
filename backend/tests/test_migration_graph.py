from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_migration_graph_has_one_head(monkeypatch):
    monkeypatch.setenv('WEBUI_SECRET_KEY', 'migration-graph-test-secret-key-at-least-32-characters')
    monkeypatch.setenv('ENABLE_DB_MIGRATIONS', 'False')

    migrations_dir = Path(__file__).resolve().parents[1] / 'open_webui' / 'migrations'
    config = Config()
    config.set_main_option('script_location', str(migrations_dir))

    heads = ScriptDirectory.from_config(config).get_heads()

    assert len(heads) == 1, f'Expected one migration head, found: {heads}'

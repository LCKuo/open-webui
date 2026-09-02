import { existsSync } from 'node:fs';
import { spawnSync } from 'node:child_process';

const tests = [
	'backend/tests/test_database_error_status.py',
	'backend/tests/test_interact_access.py',
	'backend/tests/test_interact_billing.py',
	'backend/tests/test_interact_channel_context.py',
	'backend/tests/test_interact_channel_queue.py',
	'backend/tests/test_interact_email.py',
	'backend/tests/test_line_rich_menu.py',
	'backend/tests/test_workflow_runtime.py',
	'backend/tests/test_workflows.py'
];

const candidates = [
	process.env.PYTHON ? [process.env.PYTHON] : null,
	process.platform === 'win32' && existsSync('.venv/Scripts/python.exe')
		? ['.venv/Scripts/python.exe']
		: null,
	process.platform !== 'win32' && existsSync('.venv/bin/python') ? ['.venv/bin/python'] : null,
	['python3'],
	['python'],
	process.platform === 'win32' ? ['py', '-3'] : null
].filter(Boolean);

for (const [command, ...prefixArgs] of candidates) {
	const probe = spawnSync(command, [...prefixArgs, '--version'], { stdio: 'ignore' });
	if (probe.error?.code === 'ENOENT') continue;
	if (probe.status !== 0) continue;

	const result = spawnSync(command, [...prefixArgs, '-m', 'pytest', '-q', ...tests], {
		stdio: 'inherit',
		env: {
			...process.env,
			WEBUI_SECRET_KEY:
				process.env.WEBUI_SECRET_KEY ?? 'interact-custom-test-runner-secret-not-for-production'
		}
	});
	process.exit(result.status ?? 1);
}

console.error('找不到可用的 Python。請建立 .venv，或用 PYTHON 指定 Python 執行檔。');
process.exit(1);

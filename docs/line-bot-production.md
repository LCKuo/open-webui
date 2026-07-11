# LINE Bot Production Operations

## Processing contract

- The webhook verifies the LINE signature, atomically claims the platform event, stores a durable job, replies with an acknowledgement, and returns HTTP 200.
- Jobs for the same `channel + LINE user` run in arrival order. Jobs for different users may run concurrently.
- The model/workflow result is stored before LINE push delivery. Delivery retries reuse that result and do not rerun the workflow or charge it again.
- Final LINE push requests use the durable job UUID as `X-Line-Retry-Key`, preventing duplicate answers if a worker crashes after LINE accepts the request but before the database is marked complete.
- A worker renews its lease while processing. Expired leases are recovered automatically after a crash or forced restart.
- Platform event IDs and job event IDs are unique, so LINE webhook redelivery is idempotent.

## Required production database

Use PostgreSQL when `UVICORN_WORKERS` is greater than one or when running more than one WebUI replica. PostgreSQL row locks coordinate job claims across processes and hosts.

The repository includes `docker-compose.production.yaml` as a PostgreSQL production overlay. Set `POSTGRES_PASSWORD`, then launch it together with the base Compose file.
Do not point an existing SQLite deployment at an empty PostgreSQL database without migrating its data first.

SQLite with WAL is supported only for development or a single WebUI process. In that mode, keep `UVICORN_WORKERS=1`; the async channel worker pool can still process different users concurrently.

## Capacity settings

```env
INTERACT_CHANNEL_WORKERS=8
INTERACT_CHANNEL_JOB_LEASE_SECONDS=300
INTERACT_CHANNEL_JOB_MAX_ATTEMPTS=5
```

Each channel also has three independent controls:

- Channel messages per minute: aggregate admission rate for the whole bot.
- User messages per minute: burst protection for one external user.
- Maximum concurrent jobs: parallel model/workflow executions for that channel.

The effective concurrency is the lower of channel maximum concurrent jobs, total channel workers across all replicas, database capacity, and upstream model limits.

## Health and monitoring

`POST /api/v1/interact/health` returns:

- `workersReady` and `workerCount`
- queued, processing, delivery, retry, and dead job counts
- user and billing binding verification

Alert when workers are not ready, dead jobs are nonzero, retries grow continuously, or queued jobs do not drain. Completed jobs are retained for 7 days; dead jobs are retained for 30 days.

## Deployment sequence

1. Deploy database-compatible WebUI code and allow startup to create the queue table and additive channel columns.
2. Confirm `/ready` and `/api/v1/interact/health` report ready workers.
3. Save the channel once in Company Portal to synchronize the new rate and concurrency settings.
4. Configure the LINE webhook URL shown in Company Portal.
5. Run a concurrency smoke test before enabling the channel for users.

## Minimum smoke test

- Send messages concurrently from at least 20 distinct LINE users.
- Send two messages rapidly from one user and verify response order and context.
- Redeliver one webhook event and verify only one model run and one charge.
- Restart WebUI during a long workflow and verify the job resumes after lease recovery.
- Temporarily reject LINE push delivery and verify retry uses the saved result.

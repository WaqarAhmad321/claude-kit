# Deploy, verify and roll back

<!-- fill: where it runs (host, region, services), what triggers a deploy, and who may run one. -->

## Before a deploy
- CI is green on the commit you deploy.
- Every variable in `.env.example` has a value in <!-- fill: the secret store or host settings -->. Production has no defaults for required values.
- <!-- fill: anything else to check first, such as a database backup before a migration. -->

## Deploy
<!-- fill: the exact steps or command, and how migrations run. -->

## Verify
<!-- fill: how to tell the deploy worked: the health check URL, a read-only smoke test, the logs to watch. -->

## Roll back
<!-- fill: how to go back to the previous release, and what to do about a migration that already ran. -->

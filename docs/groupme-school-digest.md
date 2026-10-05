# GroupMe school digest source

This workflow fetches recent messages from selected GroupMe groups and uploads
them as GitHub Actions artifacts for the school-email digest to consume.

## GitHub configuration

In **Settings → Secrets and variables → Actions** add:

### Secret

- `GROUPME_ACCESS_TOKEN` — your personal GroupMe API access token. Treat this
  like a password. Do not commit it to the repository.

### Variables

Set at least one of these:

- `GROUPME_GROUP_NAMES` — comma-separated exact GroupMe group names, for
  example `USchool Parents Class of 2033,5th Grade Parents`.
- `GROUPME_GROUP_IDS` — comma-separated GroupMe group IDs. This is useful if
  two groups have the same name.

Optional:

- `GROUPME_LOOKBACK_HOURS` — how far back to fetch messages. Defaults to
  `36`, which gives the daily digest overlap if a run is delayed or missed.

## Getting the GroupMe token

GroupMe's developer site exposes an access token after you sign in / authorize
an application. The script sends it only in the `X-Access-Token` header.

## Finding group names or IDs

Once the secret is configured, run locally:

```bash
GROUPME_ACCESS_TOKEN=... python3 scripts/groupme_digest.py --list-groups
```

The normal workflow can resolve configured groups by exact name, so IDs are not
required unless names are ambiguous.

## Schedule

GitHub Actions cron is UTC. The workflow has both UTC times corresponding to
6:45 PM Central and checks the current `America/Chicago` hour before fetching,
so only the correct daylight/standard-time run produces an artifact.

It also supports manual runs with `workflow_dispatch`.

## Output

Each successful run uploads an artifact named `groupme-school-digest` with:

- `groupme_messages.json` — structured data for automation.
- `groupme_messages.md` — readable chronological transcript.

Only the configured groups are fetched. The workflow does not post to GroupMe.

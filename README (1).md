# Vermont town-by-town map (sandbox)

`fetch_town_maps.py` reads the SOS town-level feed (`election-result-data.xml`)
and writes, per office in `town_maps.json`:

- `data/map-<slug>.csv` -- one row per town (247), for a Datawrapper choropleth
- `data/map-<slug>-summary.csv` -- statewide totals + precincts reporting

No dependencies beyond Python 3. A GitHub Action runs it and commits changes.

## CSV columns
`Town, County, Id, Margin, Leader, LeaderParty, TotalVotes`, then
`<Candidate> votes` and `<Candidate> %` for every candidate.

`Margin` = leader's lead over 2nd place, in points of the town's total vote.
Democratic leader = positive, Republican leader = negative (see `party_map`).
Independent/other leader or no votes yet = blank (grey); exact tie = 0.
The feed splits some towns by house district (283 entries -> 247 towns); the
script sums them by town Id.

## Setup
1. New **public** repo; push all files to `main` (keep `.github/workflows/`).
2. Settings -> Actions -> General -> Workflow permissions -> **Read and write**.
3. Actions tab -> "Update town map CSVs" -> Run workflow. Check `data/`.
4. Datawrapper: Upload Data -> Link external dataset ->
   `https://raw.githubusercontent.com/<user>/<repo>/main/data/map-governor.csv`
   Key column `Town`, value `Margin`, diverging red-blue scale centered on 0.

## Run every 5 minutes (cron-job.org)
- Token: GitHub -> Settings -> Developer settings -> Fine-grained tokens, this
  repo only, **Actions: Read and write**, expires ~Nov 7.
- Job URL: `https://api.github.com/repos/<user>/<repo>/actions/workflows/update-map.yml/dispatches`
- POST, headers `Authorization: Bearer <token>`,
  `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28`
- Body `{"ref":"main"}`; success = HTTP 204.
- Schedule: every 5 min, 7pm ET Nov 3 to ~3am ET Nov 4 (America/New_York).

## Notes
- If the feed is unreachable or the office isn't found, the run fails (red) and
  no CSV is touched, so the chart keeps the last good data.
- Feed URL is in `town_maps.json`. Add offices to `maps` (single-seat only).
- Test locally: `python fetch_town_maps.py --feed-file election-result-data.xml`
- After the election: delete the `schedule:` block and the token.

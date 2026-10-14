# Game Collection

A static "Shelf" page built from a BoardGameGeek collection export, grouping
games by best player count with relative complexity and rating bars.

## Flagging a game as Easy to Learn

To mark a game as introductory-friendly with an "Easy to Learn" badge on
the Shelf, check the **Preordered** checkbox on that game's entry in your
BGG collection (Collection > click the game > Status). This flag is
otherwise unused in this collection, so it's repurposed as the signal —
it's picked up automatically the next time you re-export and re-run
`extract_data.py`.

## Flagging a game as Cooperative or 2 Teams

Unlike "Easy to Learn", these two badges have no BGG field to repurpose, so
they're hand-maintained by BGG objectid in `scripts/generate.py`
(`COOPERATIVE_GAME_OBJECTIDS` / `TWO_TEAM_GAME_OBJECTIDS`). Add a game's objectid
(the `objectid` field in `data/games.json`) to the relevant set and re-run
`generate.py`.

## Updating the collection

1. Re-export your BGG collection as CSV and save it to `data/collection.csv`
   (see the URL in [scripts/extract_data.py](scripts/extract_data.py) — this
   file is gitignored since it contains pricing and location fields).
2. Run `python3 scripts/extract_data.py` to produce `data/games.json`
   (committed — contains only name/rating/complexity/player-count fields)
   and rebuild `public/index.html` from it (it calls `generate.py` itself).
3. Commit and push to `main`.

Changing only `scripts/generate.py` or `scripts/template.html` (without a
new CSV export)? Run `python3 scripts/generate.py` directly instead.

## Kallax packing plan

`public/kallax.html` shows how many IKEA Kallax cubbies the collection needs
and draws each cubby's front to scale, with a numbered loading order. Every
cubby is labeled Solo, 2-Player, Party or Other; Other games may fill gaps
left in the other groups' cubbies (drawn grey, tagged "filler").

It's built from `data/storage.csv`, a spreadsheet you edit by hand. Running
`extract_data.py` (or `python3 scripts/kallax.py` on its own) adds a row for
each newly owned game or expansion and never changes values you've entered.

| Column | What to put there |
|---|---|
| `tall`, `wide`, `deep` | Box size in inches with the box flat on a table, lid up: `11.75` or `11 3/4`. Only needed if `stored_in` is blank. |
| `stored_in` | Blank if the box needs its own spot. Another row's `id` if it lives inside that box or container (e.g. an expansion that fits in its base game). `off-shelf` if it isn't kept in the Kallax. |
| `group` | Blank to work it out from player counts (`classify()` in `scripts/kallax.py`). `solo`, `two`, `party` or `other` to override — this is how party games are marked. |

Containers (bins, tins, a travel bag) aren't in BGG, so add a row for each
yourself: any `id` you like (e.g. `dice-bin`), `kind` set to `container`, and
its size. Games can then use that id in `stored_in`. A container can be
`off-shelf` too, and anything inside it is then off-shelf as well.

Measure one real cubby and adjust `CUBBY_WIDTH`, `CUBBY_HEIGHT` and
`CUBBY_DEPTH` at the top of `scripts/kallax.py` if yours differ. Tests:
`python3 -m unittest discover -s scripts -p 'test_*.py'`.

## Automatic S3 Deployment

The `main` branch deploys the contents of `public/` to the `games/` prefix
in S3 automatically through GitHub Actions, leaving `games/summaries/`
(deployed separately by the `game_summaries` repo) untouched. Configure
these repository variables in **GitHub > Settings > Secrets and variables >
Actions > Variables**:

- `AWS_REGION`: The AWS region containing the bucket, such as `us-east-1`.
- `AWS_S3_BUCKET`: The S3 bucket name.
- `AWS_ROLE_ARN`: The ARN of an AWS IAM role trusted by this GitHub
  repository through OIDC.
- `AWS_CLOUDFRONT_DISTRIBUTION_ID`: The CloudFront distribution serving the
  bucket, so the `games/*` cache can be invalidated after each deploy.

The IAM role needs permission to list the bucket and sync objects
(including put/delete) under the `games/` prefix, and to create CloudFront
invalidations. If the role's OIDC trust policy is scoped to specific
repositories, it must also trust `game-collection`, not just
`game_summaries`. The workflow can also be started manually from the
repository's **Actions** tab.

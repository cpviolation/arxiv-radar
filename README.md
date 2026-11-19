# arXiv radar

Daily digest of arXiv papers on a set of topics, published as a small website
with one page per day, an archive and an RSS feed.

Every weekday morning (Mon–Fri) a GitHub Action queries the arXiv API, selects papers
by keyword, saves the result in `data/digests/` and publishes the site on GitHub Pages.
Everything runs on GitHub: no server, no API keys, nothing to install for daily use.

The default configuration follows charm physics, CP violation, B physics,
hadron spectroscopy, machine learning in particle physics, new ML methods
and ML in genetics. Fork the repository to get your own radar with your own topics.

## Get your own radar

You need a GitHub account and Python ≥ 3.11 on your computer (for the setup only).

1. **Fork** this repository: click **Fork** at the top of its GitHub page.
2. **Clone your fork** and enter it:

   ```bash
   git clone https://github.com/YOUR-USERNAME/arxiv-radar.git
   ```

   ```bash
   cd arxiv-radar
   ```

3. **Choose your topics** by editing `config.toml` (see [Customising the topics](#customising-the-topics)).
   You can also do this later: every change you push re-renders the site.
4. **Run the setup script:**

   ```bash
   python3 setup_radar.py
   ```

That's it. A few minutes later your site is at `https://YOUR-USERNAME.github.io/arxiv-radar/`,
and it updates itself every weekday morning.

### What the setup script does

- writes your site address into `base_url` in `config.toml`;
- offers to clear the digests inherited from the repository you forked, so your
  archive starts empty and your first digest is not limited by someone else's history;
- if the [GitHub CLI](https://cli.github.com) (`gh`) is installed and logged in
  (`gh auth login`), it also:
  - enables GitHub Actions on your fork (forks start with Actions turned off),
  - turns on GitHub Pages with **GitHub Actions** as the source,
  - commits and pushes the changes,
  - enables the daily workflow and starts a first run covering the last 7 days.

Without `gh`, the script prepares the local files and prints the links for the
remaining clicks, which are the same as the manual setup below.

Useful options: `--days 14` for a longer first run, `--keep-data` / `--reset-data`
to skip the question about inherited data, `--no-github` to only edit local files,
`--yes` to accept every default. The script is safe to run again.

### Manual setup (without the script)

1. In `config.toml`, set `base_url` to `https://YOUR-USERNAME.github.io/arxiv-radar/`.
   To start with an empty archive, delete `data/digests/*.json` (keep `.gitkeep`) and
   write `[]` into `data/seen.json`. Commit and push.
2. Open the **Actions** tab of your fork. If GitHub says workflows are disabled on this fork,
   click **I understand my workflows, go ahead and enable them**.
3. **Settings › Pages › Build and deployment › Source:** choose **GitHub Actions**.
4. **Actions › Daily digest:** if there is an **Enable workflow** button, click it.
   Then **Run workflow**, optionally with a number of days, for example `7`.

## Customising the topics

Everything is done in `config.toml`:

- `[site]`: title and description of your site.
- `[fetch]`: the arXiv categories to download, the time window and the
  maximum number of papers per topic.
- `[category_groups]` and `[term_sets]`: reusable lists of categories and keywords.
- `[[topics]]`: one block per section of the site, in display order, each with a name,
  slug, colour, keywords (regular expressions, case-insensitive) and allowed categories.
  The comments in the file explain every field.

A paper is listed under a topic when at least one keyword matches its title or abstract;
papers matching more keywords come first. To add a topic, copy a `[[topics]]` block and
change it; to drop one, delete its block. Make sure the arXiv categories your topics need
are listed in `[fetch] categories`.

You can test a configuration locally before pushing:

```bash
python3 arxiv_radar.py run --days 3 --no-state
```

then open `site/index.html` in a browser. `--no-state` leaves `data/seen.json` untouched;
delete the generated `data/digests/` file afterwards if you don't want to commit it.

`data/seen.json` records every paper already examined, so keyword changes
take effect from the next digest and do not pull in past papers.

## Layout

```
arxiv_radar.py                      script (standard library only, Python ≥ 3.11)
setup_radar.py                      one-time setup for a new fork
config.toml                         topics, keywords, categories, site settings
site_assets/style.css               site stylesheet
data/digests/YYYY-MM-DD.json        one file per day (source of the site)
data/seen.json                      papers already examined, so they are not repeated
.github/workflows/daily-digest.yml  daily run and deployment to GitHub Pages
```

The `site/` folder is generated on every run and is not under version control.

## Local use

```bash
python3 arxiv_radar.py run            # fetch and generate site/
python3 arxiv_radar.py run --days 7   # custom window
python3 arxiv_radar.py render         # regenerate the site only
```

## Notes and troubleshooting

- The cron schedule is in UTC (`15 6 * * 1-5`, around 7:15 or 8:15 in Milan); change it in
  `.github/workflows/daily-digest.yml`. GitHub may start scheduled workflows a few minutes late.
- Forks of a public repository are public, and so is the site. GitHub Pages on a private
  repository needs a paid plan.
- On public repositories GitHub may suspend scheduled workflows after a long
  period without activity: if the site stops updating, check the Actions tab.
- If the run fails when committing data (`Permission denied` / `403`), your organisation
  may restrict workflow permissions: check **Settings › Actions › General › Workflow permissions**.
- If the deploy step fails with “Pages not enabled”, set **Settings › Pages › Source** to
  **GitHub Actions** and run the workflow again.
- To get code updates from the original repository, **don't use Sync fork**: it would also
  merge the original's `data/` and `config.toml` into yours. Copy only the code instead:

  ```bash
  git remote add upstream https://github.com/ORIGINAL-OWNER/arxiv-radar.git
  ```

  ```bash
  git fetch upstream && git checkout upstream/main -- arxiv_radar.py site_assets .github
  ```

  then commit and push.
- The script follows the arXiv API usage guidelines (3 s pause between requests).

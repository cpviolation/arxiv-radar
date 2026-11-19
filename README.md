# arXiv radar

Daily digest of arXiv papers on my topics: charm physics, CP violation,
B physics, hadron spectroscopy, machine learning in particle physics,
new ML methods, and ML in genetics.

Every weekday morning (Mon–Fri) a GitHub Action queries the arXiv API, selects papers
by keyword, saves the result in `data/digests/` and publishes the site on GitHub Pages,
with one page per day, an archive and an RSS feed.

## Layout

```
arxiv_radar.py                    script (standard library only, Python ≥ 3.11)
config.toml                       topics, keywords, categories, site settings
site_assets/style.css             site stylesheet
data/digests/YYYY-MM-DD.json      one file per day (source of the site)
data/seen.json                    papers already examined, so they are not repeated
.github/workflows/daily-digest.yml
```

The `site/` folder is generated on every run and is not under version control.

## Setup

1. Copy the files into the repository and push to `main`.
2. In `config.toml`, replace `YOUR-USERNAME` in `base_url` with your GitHub username.
3. **Settings › Pages › Build and deployment › Source:** choose **GitHub Actions**.
4. **Settings › Actions › General › Workflow permissions:** choose **Read and write permissions**
   (needed to commit the day's data to the repository).
5. **Actions › Daily digest › Run workflow** for the first run. You can enter
   a number of days, for example `7`, to start with a week of papers.

The site will be at `https://YOUR-USERNAME.github.io/arxiv-radar/`.

## Local use

```bash
python3 arxiv_radar.py run            # fetch and generate site/
python3 arxiv_radar.py run --days 7   # custom window
python3 arxiv_radar.py render         # regenerate the site only
```

Open `site/index.html` in a browser to see the result.

## Changing topics

Everything is done in `config.toml`. Each `[[topics]]` block is a section of the site, with a name,
colour, keywords (regular expressions) and allowed categories. A push that changes
`config.toml`, the script or the stylesheet regenerates and republishes the site without fetching new papers.

`data/seen.json` records every paper already examined, so new keywords
take effect from the next digest and do not pull in past papers.

## Notes

- The cron schedule is in UTC (`15 6 * * 1-5`, around 7:15 or 8:15 in Milan).
  GitHub may start scheduled workflows a few minutes late.
- On public repositories GitHub may suspend scheduled workflows after a long
  period of inactivity: if the site stops updating, check the Actions tab.
- The script follows the arXiv API usage guidelines (3 s pause between requests).

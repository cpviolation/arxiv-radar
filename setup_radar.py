#!/usr/bin/env python3
"""
setup_radar.py — one-time setup of your own arXiv radar after forking.

Run it from your clone of the fork:

    python3 setup_radar.py

What it does:
  1. finds your GitHub repository from the `origin` remote;
  2. writes your site address into `base_url` in config.toml;
  3. offers to clear the digests inherited from the repository you forked;
  4. if the GitHub CLI (`gh`) is installed and logged in:
       - enables GitHub Actions on the fork,
       - enables GitHub Pages with "GitHub Actions" as the source,
       - commits and pushes the changes,
       - enables the daily workflow and starts a first run;
     otherwise it prints the few clicks needed to do the same on github.com.

Options:
    --repo OWNER/NAME   use this repository instead of reading the `origin` remote
    --days N            days covered by the first run (default 7)
    --reset-data        clear inherited digests without asking
    --keep-data         keep inherited digests without asking
    --no-github         only edit local files; skip everything on GitHub
    --yes               answer yes to every question

Standard library only (Python >= 3.11). Safe to run again.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config.toml"
DIGESTS = ROOT / "data" / "digests"
SEEN = ROOT / "data" / "seen.json"
WORKFLOW = "daily-digest.yml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def say(msg=""):
    print(msg, flush=True)


def step(msg):
    print(f"\n▸ {msg}", flush=True)


def ask(question, default, args):
    if args.yes:
        return True
    hint = "[Y/n]" if default else "[y/N]"
    try:
        answer = input(f"  {question} {hint} ").strip().lower()
    except EOFError:
        answer = ""
    return default if not answer else answer.startswith("y")


def run(cmd, check=True, capture=True):
    return subprocess.run(cmd, cwd=ROOT, check=check, text=True,
                          capture_output=capture)


def gh_api(path, method="GET", fields=None):
    """Call the GitHub REST API through `gh`. Returns (ok, parsed JSON or error text)."""
    cmd = ["gh", "api", "-X", method, path, "-H", "Accept: application/vnd.github+json"]
    for key, value in (fields or {}).items():
        flag = "-F" if isinstance(value, bool) else "-f"
        cmd += [flag, f"{key}={str(value).lower() if isinstance(value, bool) else value}"]
    r = run(cmd, check=False)
    if r.returncode != 0:
        return False, (r.stderr or r.stdout).strip()
    try:
        return True, json.loads(r.stdout) if r.stdout.strip() else {}
    except json.JSONDecodeError:
        return True, r.stdout


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def detect_repo(args):
    if args.repo:
        repo = args.repo
    else:
        r = run(["git", "remote", "get-url", "origin"], check=False)
        if r.returncode != 0:
            sys.exit("No `origin` remote found. Clone your fork first, or pass --repo OWNER/NAME.")
        url = r.stdout.strip()
        m = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?$", url)
        if not m:
            sys.exit(f"`origin` ({url}) is not a GitHub repository. Pass --repo OWNER/NAME.")
        repo = f"{m.group(1)}/{m.group(2)}"
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        sys.exit(f"Invalid repository name: {repo}")
    return repo


def site_url(repo):
    owner, name = repo.split("/")
    host = f"{owner.lower()}.github.io"
    return f"https://{host}/" if name.lower() == host else f"https://{host}/{name}/"


def set_base_url(url):
    text = CONFIG.read_text(encoding="utf-8")
    new, n = re.subn(r'(?m)^base_url\s*=\s*".*"', f'base_url = "{url}"', text, count=1)
    if n == 0:
        sys.exit("Could not find `base_url` in config.toml.")
    tomllib.loads(new)  # make sure the file is still valid TOML
    if new != text:
        CONFIG.write_text(new, encoding="utf-8")
        say(f"  base_url set to {url}")
    else:
        say(f"  base_url already {url}")


def inherited_data():
    digests = sorted(DIGESTS.glob("*.json")) if DIGESTS.exists() else []
    seen = json.loads(SEEN.read_text()) if SEEN.exists() else []
    return digests, seen


def reset_data(digests):
    for f in digests:
        f.unlink()
    SEEN.parent.mkdir(parents=True, exist_ok=True)
    SEEN.write_text("[]")
    DIGESTS.mkdir(parents=True, exist_ok=True)
    (DIGESTS / ".gitkeep").touch()
    say(f"  removed {len(digests)} digests and cleared data/seen.json")


def gh_ready():
    if not shutil.which("gh"):
        return False, "the GitHub CLI (gh) is not installed: https://cli.github.com"
    if run(["gh", "auth", "status"], check=False).returncode != 0:
        return False, "gh is not logged in: run `gh auth login` first"
    return True, ""


def enable_actions(repo):
    ok, out = gh_api(f"repos/{repo}/actions/permissions", "PUT", {"enabled": True})
    say("  Actions enabled" if ok else f"  could not enable Actions ({out})")
    return ok


def enable_pages(repo):
    ok, info = gh_api(f"repos/{repo}/pages")
    if ok and isinstance(info, dict):
        if info.get("build_type") == "workflow":
            say("  Pages already set to deploy from GitHub Actions")
            return True
        ok, out = gh_api(f"repos/{repo}/pages", "PUT", {"build_type": "workflow"})
    else:
        ok, out = gh_api(f"repos/{repo}/pages", "POST", {"build_type": "workflow"})
    if ok:
        say("  Pages enabled, source: GitHub Actions")
    else:
        say(f"  could not enable Pages ({out})")
        say("  Note: Pages on a private repository needs a paid GitHub plan.")
    return ok


def commit_and_push(args):
    paths = ["config.toml", "data", ".github", ".gitignore"]
    paths = [p for p in paths if (ROOT / p).exists()]
    if not run(["git", "status", "--porcelain", "--", *paths]).stdout.strip():
        say("  nothing to commit")
        return True
    say(run(["git", "status", "--short", "--", *paths]).stdout.rstrip())
    if not ask("Commit and push these changes?", True, args):
        say("  skipped: push them yourself before running the workflow")
        return False
    run(["git", "add", "--", *paths])
    run(["git", "commit", "-m", "Set up my arXiv radar", "--", *paths], capture=False)
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    if branch != "main":
        say(f"  warning: you are on `{branch}`; the workflow runs on `main`")
    r = run(["git", "push", "-u", "origin", branch], check=False, capture=False)
    return r.returncode == 0


def start_workflow(repo, days):
    r = run(["gh", "workflow", "enable", WORKFLOW, "-R", repo], check=False)
    if r.returncode != 0:
        say(f"  could not enable the workflow ({r.stderr.strip()})")
    r = run(["gh", "workflow", "run", WORKFLOW, "-R", repo, "-f", f"days={days}"], check=False)
    if r.returncode == 0:
        say(f"  first run started, covering the last {days} days")
        return True
    say(f"  could not start the workflow ({r.stderr.strip()})")
    return False


def manual_steps(repo, days, pushed):
    base = f"https://github.com/{repo}"
    say("\nFinish the setup on github.com:")
    n = 1
    if not pushed:
        say(f"  {n}. Commit and push config.toml (and data/, if you cleared it)."); n += 1
    say(f"  {n}. {base}/actions\n"
        "     If GitHub says workflows are disabled on this fork, click\n"
        "     “I understand my workflows, go ahead and enable them”."); n += 1
    say(f"  {n}. {base}/settings/pages\n"
        "     Build and deployment › Source: choose “GitHub Actions”."); n += 1
    say(f"  {n}. {base}/actions/workflows/{WORKFLOW}\n"
        "     If there is an “Enable workflow” button, click it. Then\n"
        f"     Run workflow › days = {days} › Run workflow.")


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="One-time setup of your own arXiv radar.")
    ap.add_argument("--repo", help="OWNER/NAME (default: read from the origin remote)")
    ap.add_argument("--days", type=int, default=7, help="days covered by the first run (default 7)")
    data = ap.add_mutually_exclusive_group()
    data.add_argument("--reset-data", action="store_true", help="clear inherited digests")
    data.add_argument("--keep-data", action="store_true", help="keep inherited digests")
    ap.add_argument("--no-github", action="store_true", help="only edit local files")
    ap.add_argument("--yes", "-y", action="store_true", help="answer yes to every question")
    args = ap.parse_args()

    if sys.version_info < (3, 11):
        sys.exit("Python 3.11 or newer is needed.")

    repo = detect_repo(args)
    url = site_url(repo)
    say(f"Repository: {repo}\nSite:       {url}")

    use_gh = not args.no_github
    if use_gh:
        use_gh, why = gh_ready()
        if not use_gh:
            say(f"\nNote: {why}.\nLocal files will be prepared and the GitHub steps printed at the end.")

    step("Site address")
    set_base_url(url)

    step("Inherited data")
    digests, seen = inherited_data()
    if not digests and not seen:
        say("  archive is empty")
    elif args.keep_data:
        say(f"  keeping {len(digests)} digests")
    else:
        is_fork = False
        if use_gh:
            ok, info = gh_api(f"repos/{repo}")
            is_fork = ok and isinstance(info, dict) and info.get("fork", False)
        say(f"  found {len(digests)} digests and {len(seen)} seen papers"
            + (" from the repository you forked" if is_fork else ""))
        if args.reset_data or ask("Start with an empty archive?", is_fork, args):
            reset_data(digests)
        else:
            say("  kept")

    if not use_gh:
        manual_steps(repo, args.days, pushed=False)
        say(f"\nYour site will be at {url}")
        return

    step("GitHub Actions")
    enable_actions(repo)

    step("GitHub Pages")
    pages_ok = enable_pages(repo)

    step("Commit and push")
    pushed = commit_and_push(args)

    if pushed and pages_ok:
        step("First run")
        started = start_workflow(repo, args.days)
    else:
        started = False

    if not (pushed and pages_ok and started):
        manual_steps(repo, args.days, pushed)

    say(f"\nProgress: https://github.com/{repo}/actions")
    say(f"Your site (a few minutes after the run ends): {url}")
    say("Edit config.toml to change topics and keywords; each push re-renders the site.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""skills-update — update every installed agent skill with one command.

Finds skills in all known agent skill directories (Claude Code, Codex, Gemini,
Antigravity, ~/.agents, Cursor, OpenCode, ...), figures out where each one
came from and brings every copy up to date:

  * git checkouts            -> git pull --ff-only
  * ~/.agents/.skill-lock.json (npx skills) -> fetch source repo, sync folder
  * repos from config.json   -> scan for SKILL.md, sync skills with same name
  * Claude Code / Codex plugins -> their own update commands

Replaced copies are backed up to ~/.cache/skills-update/backups/<timestamp>.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

HOME = Path.home()
CACHE = Path(os.environ.get("XDG_CACHE_HOME", HOME / ".cache")) / "skills-update"
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config")) / "skills-update" / "config.json"
LOCK = HOME / ".agents" / ".skill-lock.json"
KEEP_BACKUPS = 10

AGENT_ROOTS = {
    "claude": ".claude/skills",
    "codex": ".codex/skills",
    "agents": ".agents/skills",
    "gemini": ".gemini/skills",
    "antigravity": ".gemini/antigravity-cli/skills",
    "cursor": ".cursor/skills",
    "opencode": ".config/opencode/skills",
    "copilot": ".copilot/skills",
    "factory": ".factory/skills",
    "kiro": ".kiro/skills",
    "windsurf": ".codeium/windsurf/skills",
    "qwen": ".qwen/skills",
    "goose": ".config/goose/skills",
    "amp": ".config/amp/skills",
    "roo": ".roo/skills",
    "cline": ".cline/skills",
    "openclaw": ".openclaw/skills",
    "hermes": ".hermes/skills",
    "pi": ".pi/agent/skills",
}
MANAGED_DIRS = {".system", "synced"}  # owned by the host app, never touched
IGNORE = {".git", ".DS_Store", "__pycache__"}
DEFAULT_CONFIG = {
    "roots": {},
    "repos": ["cloudflare/skills", "rudrankriyam/app-store-connect-cli-skills"],
    "skip": [],
}

TTY = sys.stdout.isatty()


def c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if TTY else text


MARK = {
    "updated": c("32", "↑"), "would": c("33", "↑"), "ok": c("2", "="),
    "error": c("31", "!"), "nosrc": c("2", "?"), "skipped": c("33", "~"),
}


# ---------------------------------------------------------------- helpers

def run(cmd: list[str], cwd: Path | None = None, timeout: int = 300) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=env, timeout=timeout)


def git(cwd: Path, *args: str, timeout: int = 300) -> str:
    p = run(["git", *args], cwd=cwd, timeout=timeout)
    if p.returncode:
        raise RuntimeError((p.stderr or p.stdout).strip().splitlines()[-1] if (p.stderr or p.stdout).strip()
                           else f"git {args[0]} failed")
    return p.stdout.strip()


def tree_hash(d: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(d.rglob("*")):
        rel = p.relative_to(d)
        if any(part in IGNORE for part in rel.parts) or not p.is_file():
            continue
        h.update(str(rel).encode() + b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def frontmatter_name(skill_md: Path) -> str | None:
    try:
        head = skill_md.read_text(errors="replace")[:2000]
    except OSError:
        return None
    m = re.search(r"^name:\s*[\"']?([^\"'\n]+?)[\"']?\s*$", head, re.M)
    return m.group(1).strip() if m else None


def repo_url(spec: str) -> str:
    if re.fullmatch(r"[\w.-]+/[\w.-]+", spec):
        return f"https://github.com/{spec}.git"
    return spec


def slug(url: str) -> str:
    s = re.sub(r"^\w+://", "", url).removesuffix(".git")
    return re.sub(r"[^\w.-]+", "__", s)


def is_git_root(d: Path) -> bool:
    return (d / ".git").exists()


# ---------------------------------------------------------------- model

@dataclass
class Copy:
    name: str
    real: Path                     # resolved directory
    agents: list[str] = field(default_factory=list)

    @property
    def git(self) -> bool:
        return is_git_root(self.real)


@dataclass
class Result:
    name: str
    status: str                    # updated | would | ok | error | nosrc | skipped
    source: str = ""
    detail: str = ""
    agents: list[str] = field(default_factory=list)


def load_config() -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if CONFIG.exists():
        user = json.loads(CONFIG.read_text())
        for k, v in user.items():
            cfg[k] = v
    return cfg


def discover(cfg: dict) -> dict[str, list[Copy]]:
    roots = {k: HOME / v for k, v in AGENT_ROOTS.items()}
    roots.update({k: Path(os.path.expanduser(v)) for k, v in cfg.get("roots", {}).items()})
    found: dict[str, dict[Path, Copy]] = {}
    for agent, root in roots.items():
        if not root.is_dir():
            continue
        for entry in sorted(root.iterdir()):
            if entry.name.startswith(".") or entry.name in MANAGED_DIRS:
                continue
            if not (entry / "SKILL.md").is_file():
                continue
            real = entry.resolve()
            copy = found.setdefault(entry.name, {}).setdefault(real, Copy(entry.name, real))
            copy.agents.append(agent)
    return {n: list(v.values()) for n, v in sorted(found.items())}


# ---------------------------------------------------------------- sources

def fetch_repo(url: str, ref: str | None = None) -> Path:
    dest = CACHE / "repos" / slug(url)
    if (dest / ".git").exists():
        git(dest, "fetch", "--depth", "1", "--quiet", "origin", ref or "HEAD")
        git(dest, "reset", "--hard", "--quiet", "FETCH_HEAD")
    else:
        if dest.exists():
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        args = ["clone", "--depth", "1", "--quiet"] + (["--branch", ref] if ref else []) + [url, str(dest)]
        git(dest.parent, *args)
    return dest


def scan_repo(repo: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for md in repo.rglob("SKILL.md"):
        if ".git" in md.relative_to(repo).parts:
            continue
        folder = md.parent
        for n in {folder.name, frontmatter_name(md)}:
            if n and (n not in out or len(folder.parts) < len(out[n].parts)):
                out[n] = folder
    return out


def git_update(copy: Copy, check: bool) -> tuple[str, str]:
    """Returns (status, detail) for a skill that is a git checkout."""
    d = copy.real
    if git(d, "status", "--porcelain", "--untracked-files=no"):
        return "skipped", "local changes, not pulled"
    try:
        git(d, "rev-parse", "--abbrev-ref", "@{u}")
    except RuntimeError:
        return "skipped", "no upstream branch"
    git(d, "fetch", "--quiet")
    behind = int(git(d, "rev-list", "--count", "HEAD..@{u}"))
    if not behind:
        return "ok", ""
    if check:
        return "would", f"{behind} new commit(s)"
    old = git(d, "rev-parse", "--short", "HEAD")
    git(d, "pull", "--ff-only", "--quiet")
    return "updated", f"{old}..{git(d, 'rev-parse', '--short', 'HEAD')}"


# ---------------------------------------------------------------- sync

class Syncer:
    def __init__(self, check: bool):
        self.check = check
        self.stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.backup_root = CACHE / "backups" / self.stamp

    def sync(self, src: Path, copy: Copy) -> bool:
        """Make copy.real identical to src. Returns True if it changed (or would)."""
        if tree_hash(src) == tree_hash(copy.real):
            return False
        if self.check:
            return True
        dst = copy.real
        tmp = dst.with_name(f".{dst.name}.skills-update-tmp")
        if tmp.exists():
            shutil.rmtree(tmp)
        shutil.copytree(src, tmp, symlinks=True, ignore=shutil.ignore_patterns(*IGNORE))
        backup = self.backup_root / f"{copy.agents[0]}__{copy.name}"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(dst), str(backup))
        tmp.rename(dst)
        return True

    def prune(self) -> None:
        root = CACHE / "backups"
        if not root.is_dir():
            return
        for old in sorted(root.iterdir())[:-KEEP_BACKUPS]:
            shutil.rmtree(old, ignore_errors=True)


# ---------------------------------------------------------------- main flow

def update_skills(args, cfg: dict) -> list[Result]:
    inventory = discover(cfg)
    skip = set(cfg.get("skip", []))
    if args.only:
        missing = set(args.only) - inventory.keys()
        if missing:
            print(c("31", f"not installed: {', '.join(sorted(missing))}"), file=sys.stderr)
        inventory = {n: v for n, v in inventory.items() if n in args.only}
    syncer = Syncer(args.check)
    results: list[Result] = []
    handled: set[str] = set()

    lock = json.loads(LOCK.read_text()) if LOCK.exists() else {"skills": {}}
    lock_skills = {n: e for n, e in lock.get("skills", {}).items() if n in inventory}

    repo_specs: list[tuple[str, str | None]] = []
    for e in lock_skills.values():
        if e.get("sourceUrl"):
            repo_specs.append((e["sourceUrl"], e.get("ref")))
    for r in cfg.get("repos", []):
        spec = r if isinstance(r, dict) else {"url": r}
        repo_specs.append((repo_url(spec["url"]), spec.get("ref")))
    repo_specs = list(dict.fromkeys(repo_specs))

    git_copies = [cp for n, cps in inventory.items() if n not in skip for cp in cps if cp.git]
    pre_hash = {cp.real: tree_hash(cp.real) for cp in git_copies}

    # plain git checkouts outside skill folders (e.g. hook-injected instructions)
    checkouts: list[Copy] = []
    for raw in cfg.get("checkouts", []):
        p = Path(os.path.expanduser(raw))
        name = p.name.lstrip(".") or p.name
        if args.only and name not in args.only:
            continue
        if is_git_root(p):
            checkouts.append(Copy(name, p.resolve(), ["checkout"]))
        else:
            results.append(Result(name, "skipped", raw, "not a git checkout"))

    # phase 1: network, in parallel
    repos: dict[str, Path | Exception] = {}
    git_res: dict[Path, tuple[str, str]] = {}
    with cf.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futs = {pool.submit(fetch_repo, u, ref): ("repo", u) for u, ref in repo_specs}
        futs.update({pool.submit(git_update, cp, args.check): ("git", cp.real)
                     for cp in git_copies + checkouts})
        for f in cf.as_completed(futs):
            kind, key = futs[f]
            try:
                val = f.result()
            except Exception as e:  # noqa: BLE001
                val = e
            if kind == "repo":
                repos[key] = val
            else:
                git_res[key] = val if not isinstance(val, Exception) else ("error", str(val))

    def report(name, status, source, detail="", agents=()):
        results.append(Result(name, status, source, detail, sorted(set(agents))))

    # phase 2a: git checkouts (+ identical plain copies of the same skill)
    for name, cps in inventory.items():
        gits = [cp for cp in cps if cp.git]
        if not gits or name in skip:
            continue
        handled.add(name)
        for g in gits:
            status, detail = git_res[g.real]
            src = git(g.real, "remote", "get-url", "origin") if status != "error" else "git"
            agents = list(g.agents)
            if status in ("updated", "would"):
                for cp in cps:
                    if not cp.git and tree_hash(cp.real) == pre_hash[g.real]:
                        if args.check or syncer.sync(g.real, cp):
                            agents += cp.agents
            report(name, status, src, detail, agents)

    for cp in checkouts:
        status, detail = git_res[cp.real]
        report(cp.name, status, "checkout " + str(cp.real).replace(str(HOME), "~", 1), detail)

    # phase 2b: skills tracked by npx skills lock file
    lock_dirty = False
    for name, entry in lock_skills.items():
        if name in handled or name in skip:
            continue
        handled.add(name)
        url = entry.get("sourceUrl", "")
        repo = repos.get(url)
        if repo is None or isinstance(repo, Exception):
            report(name, "error", entry.get("source", url), f"fetch failed: {repo}")
            continue
        rel = Path(entry.get("skillPath", "SKILL.md")).parent
        src = repo / rel
        if not (src / "SKILL.md").is_file():
            alt = scan_repo(repo).get(name)
            if not alt:
                report(name, "skipped", entry.get("source", url),
                       f"upstream has no {rel / 'SKILL.md'}, local copy kept")
                continue
            src, rel = alt, alt.relative_to(repo)
        changed = [cp for cp in inventory[name] if not cp.git and syncer.sync(src, cp)]
        status = ("would" if args.check else "updated") if changed else "ok"
        report(name, status, entry.get("source", url), agents=[a for cp in changed for a in cp.agents])
        if changed and not args.check:
            treeish = f"HEAD:{rel}" if str(rel) != "." else "HEAD^{tree}"
            entry["skillFolderHash"] = git(repo, "rev-parse", treeish)
            entry["skillPath"] = str(rel / "SKILL.md") if str(rel) != "." else "SKILL.md"
            entry["updatedAt"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + "000Z"
            lock_dirty = True
    if lock_dirty:
        tmp = LOCK.with_suffix(".tmp")
        tmp.write_text(json.dumps(lock, indent=2) + "\n")
        tmp.replace(LOCK)

    # phase 2c: repos from config, matched by skill name
    for url, _ in repo_specs:
        repo = repos.get(url)
        cfg_urls = {repo_url(r if isinstance(r, str) else r["url"]) for r in cfg.get("repos", [])}
        if url not in cfg_urls:
            continue
        if repo is None or isinstance(repo, Exception):
            report(url.removeprefix("https://github.com/").removesuffix(".git"), "error", url, f"fetch failed: {repo}")
            continue
        index = scan_repo(repo)
        source = url.removeprefix("https://github.com/").removesuffix(".git")
        for name, cps in inventory.items():
            if name in handled or name in skip or name not in index:
                continue
            handled.add(name)
            changed = [cp for cp in cps if not cp.git and syncer.sync(index[name], cp)]
            status = ("would" if args.check else "updated") if changed else "ok"
            report(name, status, source, agents=[a for cp in changed for a in cp.agents])

    # leftovers
    for name, cps in inventory.items():
        if name in skip:
            report(name, "skipped", "", "in config skip list", [a for cp in cps for a in cp.agents])
        elif name not in handled:
            report(name, "nosrc", "", "", [a for cp in cps for a in cp.agents])

    if not args.check:
        syncer.prune()
    if not args.check and any(r.status == "updated" for r in results) and syncer.backup_root.exists():
        print(c("2", f"backups: {syncer.backup_root}"))
    return results


def update_plugins(check: bool) -> list[Result]:
    out: list[Result] = []
    installed = HOME / ".claude" / "plugins" / "installed_plugins.json"
    if shutil.which("claude") and installed.exists():
        ids = list(json.loads(installed.read_text()).get("plugins", {}))
        if check:
            out += [Result(i, "skipped", "claude plugin", "check mode: not queried") for i in ids]
        else:
            run(["claude", "plugin", "marketplace", "update"], timeout=600)
            for pid in ids:
                p = run(["claude", "plugin", "update", pid], timeout=600)
                text = (p.stdout + p.stderr).strip().splitlines()
                last = text[-1] if text else ""
                if p.returncode:
                    out.append(Result(pid, "error", "claude plugin", last))
                elif re.search(r"already|up to date|latest", last, re.I):
                    out.append(Result(pid, "ok", "claude plugin"))
                else:
                    out.append(Result(pid, "updated", "claude plugin", last))
    if shutil.which("codex") and not check:
        p = run(["codex", "plugin", "marketplace", "upgrade"], timeout=600)
        text = (p.stdout + p.stderr).strip().splitlines()
        out.append(Result("codex marketplaces", "ok" if p.returncode == 0 else "error",
                          "codex plugin", "" if p.returncode == 0 else (text[-1] if text else "")))
    return out


def print_results(results: list[Result], verbose: bool) -> None:
    w = max((len(r.name) for r in results), default=10)
    for r in results:
        if r.status in ("ok", "nosrc") and not verbose:
            continue
        extra = " ".join(x for x in (r.detail, f"[{', '.join(r.agents)}]" if r.agents else "") if x)
        print(f" {MARK[r.status]} {r.name:<{w}}  {c('2', r.source)}  {extra}".rstrip())
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    labels = [("updated", "updated"), ("would", "to update"), ("ok", "up to date"),
              ("skipped", "skipped"), ("nosrc", "no source"), ("error", "errors")]
    print(" " + ", ".join(f"{counts[k]} {lbl}" for k, lbl in labels if counts.get(k)))
    if counts.get("nosrc") and not verbose:
        print(c("2", "   (-v lists skills with no known source; add their repos to " + str(CONFIG) + ")"))


def cmd_list(cfg: dict) -> None:
    inv = discover(cfg)
    lock = json.loads(LOCK.read_text()).get("skills", {}) if LOCK.exists() else {}
    w = max((len(n) for n in inv), default=10)
    for name, cps in inv.items():
        if any(cp.git for cp in cps):
            src = "git: " + next(git(cp.real, "remote", "get-url", "origin") for cp in cps if cp.git)
        elif name in lock:
            src = "lock: " + lock[name].get("source", "")
        else:
            src = ""
        agents = ", ".join(sorted({a for cp in cps for a in cp.agents}))
        print(f" {name:<{w}}  {agents:<34} {c('2', src)}")
    print(f" {len(inv)} skills")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="skills-update", description=__doc__.split("\n\n")[0])
    ap.add_argument("-n", "--check", action="store_true", help="only show what would be updated")
    ap.add_argument("-l", "--list", action="store_true", help="list installed skills, agents and sources")
    ap.add_argument("-o", "--only", nargs="+", metavar="NAME", help="update only these skills")
    ap.add_argument("--no-plugins", action="store_true", help="skip Claude Code / Codex plugin updates")
    ap.add_argument("-j", "--jobs", type=int, default=8, help="parallel fetches (default 8)")
    ap.add_argument("-v", "--verbose", action="store_true", help="also show up-to-date and unknown skills")
    args = ap.parse_args(argv)
    cfg = load_config()

    if args.list:
        cmd_list(cfg)
        return 0
    if not shutil.which("git"):
        print("git is required", file=sys.stderr)
        return 2
    results = update_skills(args, cfg)
    if not args.no_plugins and not args.only:
        results += update_plugins(args.check)
    print_results(results, args.verbose)
    return 1 if any(r.status == "error" for r in results) else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)

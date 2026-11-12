# skills-update

Update the skills of every coding agent on your machine with one command: Claude Code, Codex, Gemini CLI, Antigravity, `~/.agents` and a dozen more.

```console
$ skills-update
 ↑ ex-ussr-kinship-research   https://github.com/DenisSergeevitch/ex-ussr-kinship-research  557abe4..60be957 [claude]
 ↑ swiftui-pro                twostraws/swiftui-agent-skill  [agents, antigravity, claude]
 ↑ writing-skills             obra/superpowers  [agents, claude, openclaw]
 ~ benjamin-plus              JetBrains/benjamin-plus-skill  upstream has no SKILL.md, local copy kept
 ↑ wrangler                   cloudflare/skills  [claude, codex, gemini, hermes, openclaw]
 30 updated, 16 up to date, 1 skipped, 53 no source
```

Status: early, single maintainer, tested on macOS. MIT licence.

## Why

Each agent keeps its own copy of a skill. `npx skills update` refreshes the skills in its lock file, `git pull` refreshes the ones you cloned, and every other copy stays on whatever version you installed. skills-update finds every copy, works out where it came from and syncs it, so Claude Code and Codex end up on the same version.

It does not install new skills or edit `SKILL.md` content. It also leaves app-managed folders alone (`.system` in Codex, `synced` in Claude Code).

## Install

Requires Python 3.9+ and git. No other dependencies.

```sh
git clone https://github.com/Christian-SC26/skills-update ~/skills-update
ln -s ~/skills-update/skills_update.py ~/.local/bin/skills-update
skills-update -n    # dry run: lists what would change, writes nothing
```

## Usage

```sh
skills-update                  # update all skills and plugins
skills-update -n               # dry run
skills-update -l               # list skills, the agents that have them, and their source
skills-update -o NAME [NAME…]  # update only these skills
skills-update -v               # also print up-to-date skills and skills with no known source
```

Exit code is 1 if any fetch or update failed.

## Where updates come from

| Skill origin | What skills-update does |
|---|---|
| Git checkout, in a skill folder or listed under `checkouts` | `git pull --ff-only`; skips checkouts with uncommitted changes |
| `~/.agents/.skill-lock.json` from `npx skills` | fetches the source repo, syncs the folder into every copy, writes the new hash back to the lock file |
| Repo listed in your config | scans the repo for `SKILL.md` and matches installed skills by name |
| Claude Code plugins | `claude plugin marketplace update`, then `claude plugin update <id>` |
| Codex plugins | `codex plugin marketplace upgrade` |

A skill with none of these origins shows up as `?` under `-v`. Add its repo to the config and the next run picks it up.

Before replacing a copy, skills-update moves the old one to `~/.cache/skills-update/backups/<timestamp>/` and keeps the last 10 runs. A copy you edited by hand gets overwritten, so restore it from there if you need it.

## Config

`~/.config/skills-update/config.json`:

```json
{
  "roots": { "myagent": "~/.myagent/skills" },
  "repos": ["cloudflare/skills", { "url": "https://github.com/org/repo.git", "ref": "main" }],
  "skip":  ["skill-to-freeze"],
  "checkouts": ["~/.benjamin-plus"]
}
```

- `roots`: extra skill folders on top of the built-in list in `AGENT_ROOTS`
- `repos`: `owner/repo` for GitHub, or any git URL with an optional branch
- `skip`: skill names to leave untouched
- `checkouts`: git clones outside any skill folder that skills-update should pull, such as instructions an agent loads through a hook

## Licence

[MIT](LICENSE)

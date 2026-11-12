# skills-update

Одна команда, чтобы обновить все установленные скиллы у всех агентов: Claude Code, Codex, Gemini CLI, Antigravity, `~/.agents`, OpenClaw, Hermes, Cursor, OpenCode и других.

```sh
skills-update            # обновить всё
skills-update -n         # только показать, что обновится
skills-update -l         # список скиллов: у каких агентов стоят и откуда взяты
skills-update -o NAME…   # обновить только выбранные скиллы
skills-update -v         # показать также актуальные скиллы и скиллы без источника
```

## Откуда берутся обновления

| Источник | Как обновляется |
|---|---|
| Скилл установлен как git-клон | `git pull --ff-only`; при локальных изменениях пропускается |
| `~/.agents/.skill-lock.json` (`npx skills`) | Свежий upstream-репозиторий, папка синхронизируется во все копии; хеш в lock-файле обновляется |
| Репозитории из `~/.config/skills-update/config.json` | Поиск всех `SKILL.md`, сопоставление с установленными скиллами по имени |
| Плагины Claude Code / Codex | `claude plugin marketplace update` + `claude plugin update`, `codex plugin marketplace upgrade` |

Каталоги, которыми управляет приложение-владелец (`.system`, `synced`), не трогаются. Перед заменой копия сохраняется в `~/.cache/skills-update/backups/<время>/`; хранятся последние 10 запусков.

## Установка

```sh
git clone https://github.com/<you>/skills-update ~/skills-update
ln -s ~/skills-update/skills_update.py ~/.local/bin/skills-update
```

Нужны Python 3.9+ и git.

## Конфиг

`~/.config/skills-update/config.json`:

```json
{
  "roots":  { "myagent": "~/.myagent/skills" },
  "repos":  ["cloudflare/skills", {"url": "https://github.com/org/repo.git", "ref": "main"}],
  "skip":   ["skill-to-freeze"]
}
```

Если у скилла нет известного источника, `-v` покажет его со знаком `?`. Добавьте его репозиторий в `repos`.

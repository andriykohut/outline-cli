# outline-cli

A single-file command line client for [Outline](https://www.getoutline.com/)
wikis. Python 3.12+, standard library only, no runtime dependencies.

Built to be driven by hand *and* by scripts and coding agents, so every command
takes `--json` and the write commands are safe to retry.

```console
$ outline search "postgres" --context
91386745-2434-4342-88b5-e3f2eeb87cfe  Running Outline behind Caddy
    …the official compose mounts /var/lib/postgresql/data. That was correct…

$ outline get 91386745-2434-4342-88b5-e3f2eeb87cfe > runbook.md
$ $EDITOR runbook.md
$ outline update 91386745-2434-4342-88b5-e3f2eeb87cfe --file runbook.md
updated: Running Outline behind Caddy  https://wiki.example.com/doc/running-…
```

## Install

Requires Python 3.12 or newer. There are no runtime dependencies, so there is
nothing to install alongside it.

**Not on PyPI** — `pip install outline-cli` will not work. Install it as the
single file it is, either way below.

### Clone and symlink

Pick this if you want `git pull` to update the tool:

```sh
git clone https://github.com/andriykohut/outline-cli.git ~/code/outline-cli
ln -s ~/code/outline-cli/outline.py ~/.local/bin/outline
```

### Or just take the file

```sh
curl -fsSL -o ~/.local/bin/outline \
  https://raw.githubusercontent.com/andriykohut/outline-cli/main/outline.py
chmod +x ~/.local/bin/outline
```

Either way, the target directory has to be on your `PATH` — `~/.local/bin` is
conventional but nothing depends on it. Check with:

```sh
outline --help
```

## Configure

**Wiki URL** — environment variable, or a config file:

```sh
export OUTLINE_URL=https://wiki.example.com
```

```sh
mkdir -p ~/.config/outline-cli
echo 'url = https://wiki.example.com' > ~/.config/outline-cli/config
```

**API token** — create one at Settings → API → New API key. Scope it to
`documents.*` and `collections.*` if you only need this tool; the key otherwise
carries all of your own permissions.

```sh
export OUTLINE_API_TOKEN=ol_api_...
```

On macOS you can keep it in the Keychain instead, which is what the tool falls
back to when the environment variable is unset:

```sh
security add-generic-password -s outline-api -w 'ol_api_...' -U
```

Both settings check the environment first, so you can override either per
command without editing anything.

## Commands

| Command | What it does |
|---|---|
| `collections` | list collections with their ids |
| `collection-create --name N` | create a collection |
| `list [--collection C] [--parent D]` | list documents, or one document's children |
| `tree --collection C` | the collection's nested structure, indented |
| `search QUERY [--context]` | full-text search |
| `get ID [--meta]` | print a document as markdown |
| `create --collection C --title T` | create a document |
| `upsert --collection C --title T` | update the document with that title, else create |
| `update ID [--file F] [--title T]` | replace text and/or title |
| `move ID [--collection C] [--parent D]` | move between collections or nest it |
| `archive ID` | archive a document |
| `delete ID [--permanent]` | trash a document; `--permanent` destroys it outright |

`ID` accepts a document id or a full document URL, so you can paste straight
from the browser. `--collection` and `--parent` likewise accept a name or an id.

`--file -` reads from stdin, which makes the tool composable:

```sh
pandoc notes.rst -t markdown | outline create --collection Notes --title Notes --file -
```

## Editing an existing document

There is no in-place edit. Round-trip through a file:

```sh
outline get <id> > doc.md
$EDITOR doc.md
outline update <id> --file doc.md
```

`update` replaces the whole body, so start from `get` rather than writing a
partial file. To add to the end instead, use `--append`:

```sh
echo "- another entry" | outline update <id> --file - --append
```

## Scripting and agents

**Every command takes `--json`**, which prints a single JSON value: an object
for one document, an array for listings. Parse that rather than the human
output, whose column layout is not a stable interface.

```sh
outline search "caddy" --json | jq -r '.[0].id'
```

**`upsert` makes a workflow idempotent.** A step that runs twice should not
leave two documents behind:

```sh
outline upsert --collection Infrastructure --title "Nightly report" --file report.md
```

It matches on exact title within the collection. Outline does not enforce unique
titles, so if two already share a title, the first is updated.

**`--if-unchanged` prevents a lost update.** Between your `get` and your
`update`, somebody may have edited the document in the browser; a plain `update`
overwrites their work with no error. Pass the `updatedAt` you read and the write
aborts instead:

```sh
before=$(outline get <id> --json | jq -r .updatedAt)
outline get <id> > doc.md
# …edit…
outline update <id> --file doc.md --if-unchanged "$before"
```

This is a guard, not a lock — the check and the write are two API calls, so a
write landing between them still wins. It converts the common case from silent
data loss into a visible failure, which is the point.

Every command exits non-zero with a message on stderr when something fails,
including a refused `--if-unchanged`.

## Using it with a coding agent

A CLI is a good fit for agents: there is no server to run, the agent already
knows how to call shell commands, and the API token stays in your Keychain or
environment instead of being pasted into a conversation.

### Claude Code

Two pieces of setup. First, tell the agent the wiki exists — otherwise it has no
reason to reach for the tool. Add to `~/.claude/CLAUDE.md` (applies everywhere)
or a project `CLAUDE.md`:

```markdown
## Wiki

Notes and runbooks live in Outline at <https://wiki.example.com>. Read and
write them with `outline`; the API token is already configured.

    outline collections
    outline search "deploy" --context
    outline get <doc-id|url>
    outline upsert --collection Runbooks --title "…" --file doc.md

Prefer updating an existing document over creating a near-duplicate.
```

Second, allow the command so it does not prompt on every call. In
`~/.claude/settings.json`:

```json
{
  "permissions": {
    "allow": ["Bash(outline:*)"]
  }
}
```

Leave that out if you would rather approve each write by hand — reads and writes
share one command, so the allowlist covers both.

### Patterns that hold up

**Have the agent read before it writes.** `outline search --json` then
`outline get` is far cheaper than pasting a document into the prompt, and the
agent gets the current version rather than whatever was in the context window.

**Use `upsert`, not `create`, in anything repeatable.** Agents retry. `create`
in a retried step leaves duplicates behind; `upsert` converges.

**Pass `--if-unchanged` when the agent edits something a human also edits.**
It turns a silent overwrite into a failure the agent can report.

**Parse `--json`, never the plain output.** The human format is columns of text
and may change; the JSON keys are the interface.

### Keep the token out of the transcript

Configure it once in the Keychain or the environment, as above. An agent that
runs `outline` never sees the token. Do not paste a token into a chat to "give
the agent access" — anything in a conversation is in its history, and the fix
then is to rotate the key.

## If your wiki is behind Cloudflare

Cloudflare's managed bot rules reject Python's default `User-Agent` with a
`403 Forbidden` that never reaches Outline — so it looks like an authentication
failure when it is not. This tool sends its own `User-Agent` to avoid that. If
you write your own scripts against the same wiki, set one there too.

To tell the two apart: a Cloudflare block has `server: cloudflare` in the
response headers and a plain `Forbidden` body, while a genuine token scope
problem returns Outline's JSON with a readable `message`.

## Development

```sh
uv sync
uv run ty check
```

## Licence

MIT

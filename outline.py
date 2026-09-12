#!/usr/bin/env python3
"""Read and write an Outline wiki from the command line."""

import argparse
import json
import os
import pathlib
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Any

type Json = dict[str, Any]

CONFIG_PATH: pathlib.Path = (
    pathlib.Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser()
    / "outline-cli"
    / "config"
)
KEYCHAIN_SERVICE = "outline-api"

SETUP_HELP = f"""Set the wiki URL with either:
  export OUTLINE_URL=https://wiki.example.com
  echo 'url = https://wiki.example.com' > {CONFIG_PATH}

Set the API token (Outline: Settings -> API -> New API key) with either:
  export OUTLINE_API_TOKEN=ol_api_...
  security add-generic-password -s {KEYCHAIN_SERVICE} -w 'ol_api_...' -U"""


def config() -> dict[str, str]:
    if not CONFIG_PATH.exists():
        return {}
    out: dict[str, str] = {}
    for raw in CONFIG_PATH.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if "=" in line:
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip()
    return out


def base_url() -> str:
    url = os.environ.get("OUTLINE_URL") or config().get("url")
    if not url:
        sys.exit(f"No Outline URL configured.\n\n{SETUP_HELP}")
    return url.rstrip("/")


def token() -> str:
    if env := os.environ.get("OUTLINE_API_TOKEN"):
        return env
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        sys.exit(f"No API token found.\n\n{SETUP_HELP}")
    return out.stdout.strip()


def api(endpoint: str, payload: Json) -> Any:
    """POST to an Outline API endpoint, returning its `data` field, or None
    for endpoints that answer without one.

    Exits with the server's message on any non-ok response; Outline reports
    token scope violations as 403 with a human-readable message worth surfacing.
    """
    request = urllib.request.Request(
        f"{base_url()}/api/{endpoint}",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {token()}",
            "Content-Type": "application/json",
            # Cloudflare's managed bot rules 403 the default Python-urllib agent.
            "User-Agent": "outline-cli/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.load(response)
    except urllib.error.HTTPError as e:
        try:
            message = json.load(e).get("message", e.reason)
        except Exception:
            message = e.reason
        sys.exit(f"{endpoint}: HTTP {e.code}: {message}")
    except urllib.error.URLError as e:
        sys.exit(f"{endpoint}: {e.reason}")
    if not body.get("ok", True):
        sys.exit(f"{endpoint}: {body.get('message', body)}")
    # Deletes and other side-effect endpoints answer {"success": true} only.
    return body.get("data")


def emit(args: argparse.Namespace, data: Any, *human: str) -> None:
    if args.json:
        json.dump(data, sys.stdout, indent=2)
        print()
    else:
        for line in human:
            print(line)


def doc_id(ref: str) -> str:
    """Accept a bare id or a full document URL."""
    return ref.rsplit("/", 1)[-1] if "/" in ref else ref


def read_text(path: str) -> str:
    return sys.stdin.read() if path == "-" else pathlib.Path(path).read_text()


def resolve_collection(ref: str) -> str:
    for collection in api("collections.list", {"limit": 100}):
        if ref in (collection["id"], collection["name"]):
            return collection["id"]
    sys.exit(f"No collection named or with id '{ref}'")


def find_by_title(collection_id: str, title: str) -> Json | None:
    """Return the document with exactly this title in a collection, or None.

    Titles are not unique in Outline, so this returns the first match and is
    only as reliable as your own naming discipline.
    """
    documents = api("documents.list", {"collectionId": collection_id, "limit": 100})
    return next((d for d in documents if d["title"] == title), None)


def summary(document: Json) -> Json:
    return {
        "id": document["id"],
        "title": document["title"],
        "url": f"{base_url()}{document['url']}",
        "updatedAt": document.get("updatedAt"),
        "collectionId": document.get("collectionId"),
        "parentDocumentId": document.get("parentDocumentId"),
        "published": bool(document.get("publishedAt")),
    }


def cmd_collections(args: argparse.Namespace) -> None:
    collections = api("collections.list", {"limit": 100})
    emit(args, collections, *(f"{c['id']}  {c['name']}" for c in collections))


def cmd_collection_create(args: argparse.Namespace) -> None:
    collection = api(
        "collections.create",
        {"name": args.name, "description": args.description or ""},
    )
    emit(
        args,
        collection,
        f"created collection: {collection['id']}  {collection['name']}",
    )


def cmd_list(args: argparse.Namespace) -> None:
    payload: Json = {"limit": args.limit}
    if args.collection:
        payload["collectionId"] = resolve_collection(args.collection)
    if args.parent:
        payload["parentDocumentId"] = doc_id(args.parent)
    documents = api("documents.list", payload)
    emit(
        args,
        [summary(d) for d in documents],
        *(f"{d['id']}  {d['title']}" for d in documents),
    )


def cmd_tree(args: argparse.Namespace) -> None:
    nodes = api("collections.documents", {"id": resolve_collection(args.collection)})

    def lines(branch: list[Json], depth: int = 0) -> Iterator[str]:
        for node in branch:
            yield f"{'  ' * depth}{node['id']}  {node['title']}"
            yield from lines(node.get("children", []), depth + 1)

    emit(args, nodes, *lines(nodes))


def cmd_search(args: argparse.Namespace) -> None:
    hits = api("documents.search", {"query": args.query, "limit": args.limit})
    data = [{**summary(h["document"]), "context": h.get("context", "")} for h in hits]
    human: list[str] = []
    for hit in hits:
        human.append(f"{hit['document']['id']}  {hit['document']['title']}")
        if args.context:
            human.append(f"    {hit.get('context', '').strip()[:200]}")
    emit(args, data, *human)


def cmd_get(args: argparse.Namespace) -> None:
    document = api("documents.info", {"id": doc_id(args.id)})
    if args.json:
        emit(args, {**summary(document), "text": document["text"]})
        return
    if args.meta:
        print(f"# {document['title']}\n# {base_url()}{document['url']}\n")
    print(document["text"])


def cmd_create(args: argparse.Namespace) -> None:
    payload: Json = {
        "title": args.title,
        "text": read_text(args.file) if args.file else "",
        "collectionId": resolve_collection(args.collection),
        "publish": not args.draft,
    }
    if args.parent:
        payload["parentDocumentId"] = doc_id(args.parent)
    document = api("documents.create", payload)
    emit(
        args,
        summary(document),
        f"created: {document['title']}  {base_url()}{document['url']}",
    )


def cmd_update(args: argparse.Namespace) -> None:
    payload: Json = {"id": doc_id(args.id)}
    if args.file:
        payload["text"] = read_text(args.file)
    if args.title:
        payload["title"] = args.title
    if args.append:
        if "text" not in payload:
            sys.exit("--append needs --file")
        payload["append"] = True
    if len(payload) == 1:
        sys.exit("Nothing to update: pass --file and/or --title")
    if args.if_unchanged:
        current = api("documents.info", {"id": payload["id"]})["updatedAt"]
        if current != args.if_unchanged:
            sys.exit(
                f"Document changed since {args.if_unchanged} (now {current}); "
                "refusing to overwrite. Re-fetch and retry."
            )
    document = api("documents.update", payload)
    emit(
        args,
        summary(document),
        f"updated: {document['title']}  {base_url()}{document['url']}",
    )


def cmd_upsert(args: argparse.Namespace) -> None:
    collection_id = resolve_collection(args.collection)
    existing = find_by_title(collection_id, args.title)
    text = read_text(args.file) if args.file else ""
    if existing is not None:
        document = api("documents.update", {"id": existing["id"], "text": text})
        emit(
            args,
            summary(document),
            f"updated: {document['title']}  {base_url()}{document['url']}",
        )
        return
    payload: Json = {
        "title": args.title,
        "text": text,
        "collectionId": collection_id,
        "publish": not args.draft,
    }
    if args.parent:
        payload["parentDocumentId"] = doc_id(args.parent)
    document = api("documents.create", payload)
    emit(
        args,
        summary(document),
        f"created: {document['title']}  {base_url()}{document['url']}",
    )


def cmd_move(args: argparse.Namespace) -> None:
    if not args.collection and not args.parent:
        sys.exit("Pass --collection and/or --parent")
    payload: Json = {"id": doc_id(args.id)}
    if args.collection:
        payload["collectionId"] = resolve_collection(args.collection)
    if args.parent:
        payload["parentDocumentId"] = doc_id(args.parent)
    api("documents.move", payload)
    document = api("documents.info", {"id": payload["id"]})
    emit(
        args,
        summary(document),
        f"moved: {document['title']}  {base_url()}{document['url']}",
    )


def cmd_archive(args: argparse.Namespace) -> None:
    document = api("documents.archive", {"id": doc_id(args.id)})
    emit(args, summary(document), f"archived: {document['title']}")


def cmd_delete(args: argparse.Namespace) -> None:
    target = doc_id(args.id)
    if not args.permanent:
        api("documents.delete", {"id": target})
        emit(args, {"id": target, "deleted": True}, f"moved to trash: {target}")
        return
    # Outline refuses to permanently delete a document that is not already
    # in the trash.
    if not api("documents.info", {"id": target}).get("deletedAt"):
        api("documents.delete", {"id": target})
    api("documents.delete", {"id": target, "permanent": True})
    emit(args, {"id": target, "deleted": True}, f"permanently deleted: {target}")


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="machine-readable output")

    parser = argparse.ArgumentParser(
        description="Read and write an Outline wiki.",
        epilog=SETUP_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("collections", parents=[common], help="list collections")
    p.set_defaults(func=cmd_collections)

    p = sub.add_parser("collection-create", parents=[common], help="create a collection")
    p.add_argument("--name", required=True)
    p.add_argument("--description")
    p.set_defaults(func=cmd_collection_create)

    p = sub.add_parser("list", parents=[common], help="list documents")
    p.add_argument("--collection", help="name or id")
    p.add_argument("--parent", help="list children of this document")
    p.add_argument("--limit", type=int, default=25)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("tree", parents=[common], help="nested structure of a collection")
    p.add_argument("--collection", required=True, help="name or id")
    p.set_defaults(func=cmd_tree)

    p = sub.add_parser("search", parents=[common], help="full-text search")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--context", action="store_true", help="show matching snippets")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("get", parents=[common], help="print a document as markdown")
    p.add_argument("id", help="document id or URL")
    p.add_argument("--meta", action="store_true", help="prefix title and URL")
    p.set_defaults(func=cmd_get)

    p = sub.add_parser("create", parents=[common], help="create a document")
    p.add_argument("--collection", required=True, help="name or id")
    p.add_argument("--title", required=True)
    p.add_argument("--file", help="markdown file, or - for stdin")
    p.add_argument("--parent", help="nest under this document")
    p.add_argument("--draft", action="store_true", help="do not publish")
    p.set_defaults(func=cmd_create)

    p = sub.add_parser(
        "upsert", parents=[common], help="update a document with this title, else create"
    )
    p.add_argument("--collection", required=True, help="name or id")
    p.add_argument("--title", required=True)
    p.add_argument("--file", help="markdown file, or - for stdin")
    p.add_argument("--parent", help="nest under this document when creating")
    p.add_argument("--draft", action="store_true", help="do not publish when creating")
    p.set_defaults(func=cmd_upsert)

    p = sub.add_parser("update", parents=[common], help="replace text and/or title")
    p.add_argument("id", help="document id or URL")
    p.add_argument("--file", help="markdown file, or - for stdin")
    p.add_argument("--title")
    p.add_argument(
        "--append", action="store_true", help="append --file instead of replacing"
    )
    p.add_argument(
        "--if-unchanged",
        metavar="UPDATED_AT",
        help="abort unless the document still has this updatedAt",
    )
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("move", parents=[common], help="move to another collection/parent")
    p.add_argument("id", help="document id or URL")
    p.add_argument("--collection", help="name or id")
    p.add_argument("--parent", help="document id or URL")
    p.set_defaults(func=cmd_move)

    p = sub.add_parser("archive", parents=[common], help="archive a document")
    p.add_argument("id", help="document id or URL")
    p.set_defaults(func=cmd_archive)

    p = sub.add_parser("delete", parents=[common], help="move a document to trash")
    p.add_argument("id", help="document id or URL")
    p.add_argument("--permanent", action="store_true", help="skip the trash")
    p.set_defaults(func=cmd_delete)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

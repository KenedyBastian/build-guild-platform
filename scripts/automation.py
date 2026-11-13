"""Approval-gated GitHub automation. Issue input is data, never shell/code."""
import argparse
import base64
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from scripts import site_builder as site

ROOT = Path(__file__).resolve().parents[1]
LABEL = "guild-request"
FIELDS = {
    "Guild name": "name", "State": "state", "Description": "description",
    "Repository owner": "owner", "Repository slug": "slug", "Public support": "support",
    "Public meeting details": "meeting", "Public registration URL": "meeting_url",
    "Brand accent": "accent",
}


def dump(value):
    return json.dumps(value, indent=2, ensure_ascii=True) + "\n"


def digest(value):
    return hashlib.sha256(dump(value).encode("utf-8")).hexdigest()


class APIError(RuntimeError):
    def __init__(self, status, message):
        self.status = status
        super().__init__(f"GitHub HTTP {status}: {message}")


class API:
    def __init__(self, token):
        if not token:
            raise ValueError("Required automation token is not configured")
        self.token = token

    def call(self, method, path, data=None):
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("Invalid API path")
        request = Request("https://api.github.com" + path,
                          data=None if data is None else json.dumps(data).encode(),
                          method=method, headers={"Authorization": f"Bearer {self.token}",
                          "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                          "User-Agent": "build-guild-platform"})
        try:
            with urlopen(request, timeout=45) as response:
                body = response.read()
                return json.loads(body) if body else {}
        except HTTPError as exc:
            # Do not log request headers, tokens, or arbitrary response bodies.
            raise APIError(exc.code, "request rejected; check permissions, resource state and GitHub availability") from exc
        except URLError as exc:
            raise APIError(0, "network request failed; inspect resource state before retrying") from exc

    def optional(self, path):
        try:
            return self.call("GET", path)
        except APIError as exc:
            if exc.status == 404:
                return None
            raise


def repo_path(repo):
    if not isinstance(repo, str) or not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("Invalid repository identifier")
    return "/repos/" + repo


def read_file(api, repo, path):
    entry = api.optional(repo_path(repo) + "/contents/" + quote(path, safe="/"))
    if entry is None:
        return None
    if entry.get("type") != "file" or entry.get("encoding") != "base64":
        raise ValueError("Expected a small JSON file: " + path)
    return json.loads(base64.b64decode(entry["content"], validate=False).decode("utf-8"))


def update_json(api, repo, path, transform, message):
    """Contents SHA provides optimistic concurrency, never overwriting a concurrent edit."""
    endpoint = repo_path(repo) + "/contents/" + quote(path, safe="/")
    entry = api.call("GET", endpoint)
    current = json.loads(base64.b64decode(entry["content"]).decode("utf-8"))
    updated = transform(current)
    if current == updated:
        return False
    api.call("PUT", endpoint, {
        "message": message, "sha": entry["sha"],
        "content": base64.b64encode(dump(updated).encode()).decode(),
    })
    return True


def commit_files(api, repo, files, message):
    """Atomic commit; non-fast-forward rejection prevents lost updates."""
    base = repo_path(repo)
    info = api.call("GET", base)
    branch = quote(info["default_branch"], safe="")
    ref = base + "/git/refs/heads/" + branch
    head = api.call("GET", base + "/git/ref/heads/" + branch)["object"]["sha"]
    tree = api.call("GET", base + "/git/commits/" + head)["tree"]["sha"]
    entries = [{"path": p, "mode": "100644", "type": "blob", "content": c} for p, c in files.items()]
    new_tree = api.call("POST", base + "/git/trees", {"base_tree": tree, "tree": entries})["sha"]
    if new_tree == tree:
        return False
    commit = api.call("POST", base + "/git/commits",
                      {"message": message, "tree": new_tree, "parents": [head]})["sha"]
    api.call("PATCH", ref, {"sha": commit, "force": False})
    return True


def issue_digest(issue):
    # Author ID comes from the API, not an editable form field or display name.
    return digest({"number": issue["number"], "title": issue["title"], "body": issue.get("body") or "",
                   "requester_id": issue["user"]["id"]})


def parse_issue(issue):
    if issue.get("state") != "open" or "pull_request" in issue:
        raise ValueError("Request must be an open issue; reopen to retry provisioning")
    if LABEL not in [label["name"] for label in issue.get("labels", [])]:
        raise ValueError(f"Request must have the {LABEL} label")
    body = (issue.get("body") or "").replace("\r\n", "\n")
    if len(body) > 16000:
        raise ValueError("Request is too large")
    sections = re.split(r"^### ([^\n]+)\n", body, flags=re.M)
    if sections[0].strip() or sections[1::2] != list(FIELDS):
        raise ValueError("Use the request form with exactly its original field headings and order")
    data = {}
    for heading, value in zip(sections[1::2], sections[2::2]):
        data[FIELDS[heading]] = "" if value.strip() == "_No response_" else value.strip()
    match = re.fullmatch(r"```json\n(.*?)\n```", data["support"], re.S)
    if not match:
        raise ValueError("Public support must be a JSON code block from the form")
    data["support"] = json.loads(match.group(1))
    return site.validate_guild(data)


def authorize(api, source, actor):
    if not re.fullmatch(r"[A-Za-z0-9-]+", actor):
        raise ValueError("Invalid approval actor")
    permission = api.call("GET", repo_path(source) + "/collaborators/" + actor + "/permission")
    if permission.get("permission") not in ("admin", "maintain", "write"):
        raise ValueError("Approval requires current write, maintain or admin permission")


def live_issue(api, source, number, expected=None):
    if type(number) is not int or number < 1:
        raise ValueError("Invalid issue number")
    issue = api.call("GET", repo_path(source) + f"/issues/{number}")
    config = parse_issue(issue)
    if expected is not None and issue_digest(issue) != expected:
        raise ValueError("Issue changed since review; preview and approve its new digest")
    return issue, config


def comment(api, source, number, message):
    api.call("POST", repo_path(source) + f"/issues/{number}/comments",
             {"body": "<!-- build-guild-platform-status -->\n" + message})


def preview(api, source, number):
    issue, guild = live_issue(api, source, number)
    comment(api, source, number, f"Validated request for **{guild['name']}**. "
            f"Requested repository: `{guild['owner']}/{guild['slug']}`.\n\n"
            "A maintainer must review all public content and run **Provision guild** "
            f"with issue `{number}` and digest `{issue_digest(issue)}`. Nothing has been provisioned. "
            "The issue author is recorded as requester; this does not grant repository access.")


def validate_settings(settings):
    site.keys(settings, {"schema_version", "provisioning_enabled", "owners"}, "settings")
    if settings.get("schema_version") != 1 or type(settings.get("provisioning_enabled")) is not bool:
        raise ValueError("Invalid platform settings")
    if not isinstance(settings.get("owners"), dict) or not settings["owners"]:
        raise ValueError("Configure explicitly allowed owners")
    for name, owner in settings["owners"].items():
        if not re.fullmatch(r"[A-Za-z0-9-]+", name):
            raise ValueError("Invalid configured owner")
        site.keys(owner, {"type", "auth"}, "owner")
        if (owner.get("type"), owner.get("auth")) not in (
                ("User", "user"), ("Organization", "installation"), ("Organization", "user")):
            raise ValueError("Unsupported owner authentication configuration")
    return settings


def approved_owner(settings, guild):
    validate_settings(settings)
    if settings["provisioning_enabled"] is not True:
        raise ValueError("Provisioning is disabled; complete credential and owner setup first")
    if guild["owner"] not in settings["owners"]:
        raise ValueError("Owner is not explicitly allowlisted")
    return settings["owners"][guild["owner"]]


def generated_files(config, identity, network):
    return {
        ".build-guild-platform.json": dump(identity),
        "data/guild.json": dump(config),
        "data/local.json": dump({"schema_version": 1, "items": []}),
        "data/network.json": dump(site.validate_items(network)),
        "scripts/build_site.py": (ROOT / "scripts" / "site_builder.py").read_text(encoding="utf-8"),
        "scripts/sync_network.py": (ROOT / "scripts" / "sync_network.py").read_text(encoding="utf-8"),
        ".github/workflows/pages.yml": (ROOT / "templates" / "pages.yml").read_text(encoding="utf-8"),
        ".gitignore": "__pycache__/\n_site/\n",
        "README.md": f"# {config['name']}\n\n"
        "Edit `data/guild.json` for identity, support and public meeting details; "
        "edit `data/local.json` for local news/events. Run `python scripts/build_site.py` to preview "
        "`_site/index.html`. Content uses schema_version 1 and items with id, kind (news/event), "
        "title, body, date (YYYY-MM-DD) and optional HTTPS url. HTML is escaped, not interpreted.\n\n"
        "Network publishing changes only `data/network.json`. Every Monday at 09:23 UTC the Pages "
        "workflow pulls the platform's approved feed and redeploys. Manual dispatch refreshes it too; "
        "fetch/validation failures stop deployment and preserve the last good live site. "
        "Preserve `.build-guild-platform.json` "
        "for safe retries. The requester is not automatically granted repository access. "
        "The site deploys via Actions; check the Pages deployment run for live status.\n",
    }


def identity_for(source, number, issue, guild):
    return {"schema_version": 1, "source": source, "issue": number,
            "repository": guild["owner"] + "/" + guild["slug"],
            "requester": {"id": issue["user"]["id"], "login": issue["user"]["login"]},
            "config_digest": digest(guild)}


def provision(control, target, source, actor, number, expected, settings):
    authorize(control, source, actor)
    issue, guild = live_issue(control, source, number, expected)
    owner = approved_owner(settings, guild)
    repo = guild["owner"] + "/" + guild["slug"]
    identity = identity_for(source, number, issue, guild)
    reservation = f"Build Guild platform: {source} issue {number} config {digest(guild)}"
    if owner["auth"] == "user":
        user = target.call("GET", "/user")
        if owner["type"] == "User" and user["login"].lower() != guild["owner"].lower():
            raise ValueError("Personal creation token must belong to the allowlisted owner")
    account = target.call("GET", "/users/" + guild["owner"])
    if account["type"] != owner["type"]:
        raise ValueError("Configured owner type differs from GitHub")
    existing = target.optional(repo_path(repo))
    if existing is None:
        live_issue(control, source, number, expected)
        endpoint = "/user/repos" if owner["type"] == "User" else "/orgs/" + guild["owner"] + "/repos"
        existing = target.call("POST", endpoint, {"name": guild["slug"], "description": reservation,
                                                "private": False, "auto_init": True})
    if existing["full_name"].lower() != repo.lower() or existing.get("private"):
        raise ValueError("Unexpected or private repository; refusing overwrite")
    if existing["default_branch"] != "main":
        raise ValueError("Set the target owner's default branch for new repositories to main, then retry")
    saved = read_file(target, repo, ".build-guild-platform.json")
    if saved is not None and saved != identity:
        raise ValueError("Repository identity marker mismatch; refusing overwrite")
    if saved is None:
        if existing.get("description") != reservation:
            raise ValueError("Repository collision: not reserved for this exact approved request")
        tree = target.call("GET", repo_path(repo) + "/git/trees/main")
        if tree.get("truncated") or any(entry["path"] != "README.md" for entry in tree["tree"]):
            raise ValueError("Unmarked repository contains unexpected files; refusing overwrite")
        network = read_file(control, source, "published/network.json")
        live_issue(control, source, number, expected)
        commit_files(target, repo, generated_files(guild, identity, network), f"Initialize guild from {source}#{number}")
    live_issue(control, source, number, expected)
    pages = target.optional(repo_path(repo) + "/pages")
    if pages is None:
        pages = target.call("POST", repo_path(repo) + "/pages", {"build_type": "workflow"})
    elif pages.get("build_type") != "workflow":
        raise ValueError("Existing Pages configuration is not Actions-based; operator review required")

    def register(registry):
        validate_registry(registry)
        updated = json.loads(dump(registry))
        entry = {"repository": repo, "enabled": True, "mode": "generated",
                 "identity": identity, "site_url": pages["html_url"]}
        prior = next((g for g in updated["guilds"] if g["repository"].lower() == repo.lower()), None)
        if prior and prior != entry:
            raise ValueError("Registry entry differs; operator review required")
        if not prior:
            updated["guilds"].append(entry)
        return updated

    update_json(control, source, "platform/guilds.json", register, f"Register {repo}")
    # Installation-token pushes do not always start workflows; explicitly dispatch,
    # including on retry after a successful commit but failed deployment request.
    dispatch(target, repo)
    comment(control, source, number, f"Repository: {existing['html_url']}\n\n"
            f"Pages URL returned by GitHub: {pages['html_url']}\n\n"
            "Deployment requested. Check the target Actions run for build/deployment status; "
            "a returned URL does not mean the site is already live. "
            "This issue stays open for safe retries; close it after confirming deployment. "
            "No collaborator access was granted to the requester.")
    return existing["html_url"], pages["html_url"]


def validate_registry(registry):
    site.keys(registry, {"schema_version", "guilds"}, "registry")
    if registry.get("schema_version") != 1 or not isinstance(registry.get("guilds"), list):
        raise ValueError("Invalid registry schema")
    seen = set()
    for guild in registry["guilds"]:
        site.keys(guild, {"repository", "enabled", "mode", "identity", "site_url"}, "registry entry")
        repo_path(guild.get("repository"))
        if guild["repository"].lower() in seen or type(guild.get("enabled")) is not bool:
            raise ValueError("Duplicate registry repository or invalid enabled flag")
        seen.add(guild["repository"].lower())
        if guild.get("mode") not in ("generated", "bespoke"):
            raise ValueError("Unsupported integration mode")
        if not isinstance(guild.get("identity"), dict) or guild["identity"].get("repository") != guild["repository"]:
            raise ValueError("Subscriber requires an explicit matching identity")
        site.url(guild.get("site_url", ""), "site_url")
    return registry


def dispatch(api, repo):
    info = api.call("GET", repo_path(repo))
    api.call("POST", repo_path(repo) + "/actions/workflows/pages.yml/dispatches",
             {"ref": info["default_branch"], "inputs": {"refresh_network": "false"}})


def publish_snapshot(control, source):
    content = site.validate_items(read_file(control, source, "content/network.json"))
    registry = validate_registry(read_file(control, source, "platform/guilds.json"))
    return content, registry, digest({"content": content, "registry": registry})


def broadcast(control, target, source, actor, expected, settings):
    authorize(control, source, actor)
    validate_settings(settings)
    content, registry, actual = publish_snapshot(control, source)
    if actual != expected:
        raise ValueError(f"Content or subscriber registry changed; review digest {actual}")
    update_json(control, source, "published/network.json", lambda _: content,
                f"Approve network revision {actual[:12]}")
    failures, successes = [], []
    for entry in registry["guilds"]:
        if not entry["enabled"]:
            continue
        repo = entry["repository"]
        try:
            if repo.split("/")[0] not in settings["owners"]:
                raise ValueError("Subscriber owner is not allowlisted")
            identity = entry["identity"]
            if identity.get("source") != source or identity.get("schema_version") != 1:
                raise ValueError("Subscriber identity does not trust this platform")
            if entry["mode"] == "bespoke" and identity.get("mode") != "bespoke":
                raise ValueError("Bespoke integration requires explicit opt-in")
            if read_file(target, repo, ".build-guild-platform.json") != identity:
                raise ValueError("Subscriber identity mismatch; explicit opt-in is missing")

            def replace(_current):
                return content

            # A subscriber must already have this file. Never add/overwrite bespoke HTML.
            update_json(target, repo, "data/network.json", replace, f"Publish network {actual[:12]}")
            dispatch(target, repo)
            successes.append(repo)
        except (ValueError, APIError, KeyError) as exc:
            failures.append(f"{repo}: {exc}")
    return actual, successes, failures


def ensure_default_branch(api, source):
    info = api.call("GET", repo_path(source))
    if os.environ.get("GITHUB_REF") != "refs/heads/" + info["default_branch"]:
        raise ValueError("Automation must run from the platform default branch")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["preview", "provision", "broadcast", "check", "preview-site"])
    parser.add_argument("--output", type=Path, default=ROOT / "_site")
    args = parser.parse_args()
    settings = validate_settings(json.loads((ROOT / "platform" / "config.json").read_text()))
    if args.command == "check":
        content = site.validate_items(json.loads((ROOT / "content" / "network.json").read_text()))
        registry = validate_registry(json.loads((ROOT / "platform" / "guilds.json").read_text()))
        print("Publish approval digest:", digest({"content": content, "registry": registry}))
        return
    if args.command == "preview-site":
        guild = json.loads((ROOT / "examples" / "guild.json").read_text())
        local = json.loads((ROOT / "examples" / "local.json").read_text())
        network = json.loads((ROOT / "content" / "network.json").read_text())
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "index.html").write_text(site.render(guild, local, network), encoding="utf-8")
        return
    source = os.environ["GITHUB_REPOSITORY"]
    control = API(os.environ.get("CONTROL_TOKEN"))
    number = int(os.environ.get("ISSUE_NUMBER", "0"))
    ensure_default_branch(control, source)
    # Read owner authorization from current default-branch config, not an old run checkout.
    settings = validate_settings(read_file(control, source, "platform/config.json"))
    try:
        if args.command == "preview":
            preview(control, source, number)
        elif args.command == "provision":
            provision(control, API(os.environ.get("TARGET_TOKEN")), source, os.environ["GITHUB_ACTOR"],
                      number, os.environ["APPROVAL_DIGEST"], settings)
        else:
            version, successes, failures = broadcast(control, API(os.environ.get("TARGET_TOKEN")), source,
                                                     os.environ["GITHUB_ACTOR"], os.environ["APPROVAL_DIGEST"], settings)
            report = f"Network revision: {version}\n\nDeployment dispatch accepted: {', '.join(successes) or 'none'}\n\n"
            report += "\n".join(failures) if failures else "No distribution failures. Inspect target deployment runs."
            summary = os.environ.get("GITHUB_STEP_SUMMARY")
            if summary:
                Path(summary).write_text(report, encoding="utf-8")
            print(report)
            if failures:
                raise ValueError("Subscribers failed; fix permissions/integrations and retry this revision")
    except (ValueError, APIError, KeyError) as exc:
        if args.command in ("preview", "provision") and number > 0:
            comment(control, source, number, f"**{args.command.title()} failed:** {exc}\n\n"
                    "No automatic rollback was attempted. Resolve the error and retry after reviewing the current digest.")
        raise


if __name__ == "__main__":
    main()

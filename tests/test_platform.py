import base64
import copy
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from scripts import automation as app
from scripts import site_builder as site
from scripts import sync_network

SOURCE = "KenedyBastian/build-guild-platform"
OWNER = "KenedyBastian"
REPO = OWNER + "/iowa-build-guild"
SETTINGS = {"schema_version": 1, "provisioning_enabled": True,
            "owners": {OWNER: {"type": "User", "auth": "user"}}}
EMPTY = {"schema_version": 1, "items": []}
NEWS = {"schema_version": 1, "items": [
    {"id": "network-news", "kind": "news", "title": "New workshop",
     "body": "Bring a question & build.", "date": "2026-11-13", "url": "https://github.com"}
]}


def guild(**overrides):
    value = json.loads((app.ROOT / "examples" / "guild.json").read_text())
    value.update(name="Iowa", state="IA", slug="")
    value.update(overrides)
    return value


def issue(**overrides):
    data = guild()
    body = ""
    for heading, key in app.FIELDS.items():
        value = data[key]
        if key == "support":
            value = "```json\n" + json.dumps(value) + "\n```"
        body += f"### {heading}\n\n{value or '_No response_'}\n\n"
    value = {"number": 7, "title": "[Guild request] Iowa", "body": body, "state": "open",
             "labels": [{"name": app.LABEL}], "user": {"id": 12345, "login": "requester"}}
    value.update(overrides)
    return value


class FakeAPI:
    """Stateful GitHub boundary: stores files, commits, issues and dispatches."""
    def __init__(self):
        self.issue = issue()
        self.permission = "write"
        self.login = OWNER
        self.calls = []
        self.comments = []
        self.dispatches = []
        self.fail_dispatch = set()
        self.conflict = False
        self.created = []
        self.repos = {SOURCE: {
            "full_name": SOURCE, "html_url": f"https://github.com/{SOURCE}",
            "default_branch": "main", "private": False,
        }}
        self.files = {SOURCE: {
            "platform/config.json": copy.deepcopy(SETTINGS),
            "platform/guilds.json": {"schema_version": 1, "guilds": []},
            "content/network.json": copy.deepcopy(NEWS),
            "published/network.json": copy.deepcopy(EMPTY),
        }}
        self.pages = {}
        self.trees = {}

    def optional(self, path):
        try:
            return self.call("GET", path)
        except app.APIError as exc:
            if exc.status == 404:
                return None
            raise

    def call(self, method, path, data=None):
        self.calls.append((method, path, copy.deepcopy(data)))
        if path == "/user":
            return {"login": self.login}
        if path.startswith("/users/"):
            return {"type": "User"}
        if "/collaborators/" in path:
            return {"permission": self.permission}
        if path == "/user/repos" and method == "POST":
            repo = self.login + "/" + data["name"]
            if repo in self.repos:
                raise app.APIError(422, "already exists")
            value = dict(data, full_name=repo, html_url=f"https://github.com/{repo}", default_branch="main")
            self.repos[repo] = value
            self.files[repo] = {"README.md": "initial"}
            self.created.append(repo)
            return copy.deepcopy(value)
        if not path.startswith("/repos/"):
            raise AssertionError((method, path))
        parts = path.split("/")
        repo = "/".join(parts[2:4])
        tail = "/".join(parts[4:])
        if repo not in self.repos:
            raise app.APIError(404, "not found")
        if tail == "issues/7" and method == "GET":
            return copy.deepcopy(self.issue)
        if tail == "issues/7/comments" and method == "POST":
            self.comments.append(data["body"])
            return {}
        if not tail:
            return copy.deepcopy(self.repos[repo])
        if tail.startswith("contents/"):
            name = tail[len("contents/"):]
            if method == "GET":
                if name not in self.files[repo]:
                    raise app.APIError(404, "file missing")
                raw = app.dump(self.files[repo][name])
                return {"type": "file", "encoding": "base64", "content": base64.b64encode(raw.encode()).decode(),
                        "sha": app.digest(raw)}
            if method == "PUT":
                if self.conflict:
                    raise app.APIError(409, "concurrent edit")
                self.files[repo][name] = json.loads(base64.b64decode(data["content"]))
                return {}
        if tail == "git/trees/main":
            return {"tree": [{"path": p} for p in self.files[repo]], "truncated": False}
        if tail == "git/ref/heads/main" and method == "GET":
            return {"object": {"sha": "head"}}
        if tail == "git/commits/head":
            return {"tree": {"sha": "base-tree"}}
        if tail == "git/trees" and method == "POST":
            self.trees[repo] = copy.deepcopy(data["tree"])
            return {"sha": "new-tree"}
        if tail == "git/commits" and method == "POST":
            return {"sha": "commit"}
        if tail.startswith("git/refs/heads/") and method == "PATCH":
            self.assert_nonforce(data)
            for entry in self.trees[repo]:
                content = entry["content"]
                self.files[repo][entry["path"]] = json.loads(content) if entry["path"].endswith(".json") else content
            return {}
        if tail == "pages":
            if method == "GET":
                if repo not in self.pages:
                    raise app.APIError(404, "Pages missing")
                return copy.deepcopy(self.pages[repo])
            self.pages[repo] = {"build_type": "workflow", "html_url": "https://example.test/actual-returned-site/"}
            return copy.deepcopy(self.pages[repo])
        if tail == "actions/workflows/pages.yml/dispatches":
            if repo in self.fail_dispatch:
                raise app.APIError(403, "dispatch denied")
            self.dispatches.append(repo)
            return {}
        raise AssertionError((method, path, data))

    @staticmethod
    def assert_nonforce(data):
        if data.get("force") is not False:
            raise AssertionError("Updates must not force-push")


class ValidationTests(unittest.TestCase):
    def test_issue_form_normalizes_name_slug_and_optional_answers(self):
        result = app.parse_issue(issue())
        self.assertEqual(result["name"], "Iowa Build Guild")
        self.assertEqual(result["slug"], "iowa-build-guild")
        self.assertEqual(result["meeting_url"], "")
        self.assertEqual(site.validate_guild(guild(name="Iowa Build Guild"))["name"], "Iowa Build Guild")
        self.assertEqual(site.validate_guild(guild(name="Iowa BUILD guild"))["name"], "Iowa Build Guild")
        self.assertEqual(site.validate_guild(guild(name="A" * 100))["name"], "A" * 100 + " Build Guild")
        self.assertEqual(site.validate_guild(guild(slug="custom-guild"))["slug"], "custom-guild")

    def test_invalid_input_and_unknown_fields_rejected(self):
        for data in [guild(state="Iowa"), guild(owner="../evil"), guild(slug="$(run)"),
                     guild(accent='red;"'), guild(meeting_url="javascript:alert(1)"),
                     guild(meeting_url="https://user:pass@example.com"),
                     guild(support=[]), guild(unexpected="value"), guild(name="Build Guild")]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                site.validate_guild(data)
        for changed in [issue(state="closed"), issue(labels=[]), issue(pull_request={}),
                        issue(body=issue()["body"] + "### Extra\nhello"),
                        issue(body=issue()["body"].replace("```json", "```python"))]:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                app.parse_issue(changed)

    def test_author_is_trusted_metadata_not_form_data(self):
        request = issue()
        identity = app.identity_for(SOURCE, 7, request, app.parse_issue(request))
        self.assertEqual(identity["requester"], request["user"])
        self.assertNotEqual(app.issue_digest(request), app.issue_digest(issue(user={"id": 678, "login": "other"})))
        self.assertNotIn("collaborators", app.dump(identity))

    def test_escaping_urls_and_metadata(self):
        config = guild(name='Iowa <script>alert("x")</script>', description='"><img src=x onerror=alert(1)>',
                       support=[{"name": "<b>Team</b>", "role": "Public & support", "url": "https://github.com?a=1&b=2"}])
        page = site.render(config, NEWS, EMPTY)
        self.assertNotIn("<script>", page)
        self.assertNotIn("<img", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("https://github.com?a=1&amp;b=2", page)
        self.assertIn('property="og:title"', page)
        self.assertIn('href="#main"', page)
        normalized = site.validate_guild(guild())
        self.assertIn("<title>Iowa Build Guild</title>", site.render(normalized, EMPTY, EMPTY))
        self.assertIn("<h1>Iowa Build Guild</h1>", site.render(normalized, EMPTY, EMPTY))

    def test_content_schema_rejects_bad_dates_and_duplicates(self):
        for change in ({"date": "2026-02-31"}, {"kind": "script"}, {"url": "data:text/html,test"}):
            data = copy.deepcopy(NEWS)
            data["items"][0].update(change)
            with self.assertRaises(ValueError):
                site.validate_items(data)
        duplicate = copy.deepcopy(NEWS)
        duplicate["items"] *= 2
        with self.assertRaises(ValueError):
            site.validate_items(duplicate)


class ProvisionTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeAPI()
        self.approval = app.issue_digest(self.api.issue)

    def provision(self, **kwargs):
        return app.provision(self.api, self.api, SOURCE, "maintainer", 7, self.approval,
                             kwargs.get("settings", SETTINGS))

    def test_preview_has_digest_and_no_external_side_effects(self):
        app.preview(self.api, SOURCE, 7)
        self.assertIn(self.approval, self.api.comments[0])
        self.assertIn("Iowa Build Guild", self.api.comments[0])
        self.assertFalse(self.api.created)

    def test_approval_owner_and_token_boundaries(self):
        for permission in ("read", "triage", "none"):
            self.api.permission = permission
            with self.assertRaisesRegex(ValueError, "Approval"):
                self.provision()
        self.api.permission = "write"
        self.api.issue["body"] = self.api.issue["body"].replace("Iowa", "Utah")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.provision()
        self.api.issue = issue()
        settings = copy.deepcopy(SETTINGS)
        settings["provisioning_enabled"] = False
        with self.assertRaisesRegex(ValueError, "disabled"):
            self.provision(settings=settings)
        settings["provisioning_enabled"] = True
        settings["owners"] = {"DifferentOwner": {"type": "User", "auth": "user"}}
        with self.assertRaisesRegex(ValueError, "allowlisted"):
            self.provision(settings=settings)
        self.api.login = "wrong-user"
        with self.assertRaisesRegex(ValueError, "must belong"):
            self.provision()
        self.assertFalse(self.api.created)

    def test_create_pages_register_and_retry_preserves_local_changes(self):
        result = self.provision()
        self.assertEqual(result, (f"https://github.com/{REPO}", "https://example.test/actual-returned-site/"))
        self.assertEqual(self.api.created, [REPO])
        self.assertEqual(self.api.files[REPO]["data/network.json"], EMPTY)  # not the unapproved draft
        self.assertEqual(self.api.files[REPO]["data/guild.json"]["name"], "Iowa Build Guild")
        self.api.files[REPO]["data/local.json"] = copy.deepcopy(NEWS)
        self.provision()
        self.assertEqual(self.api.created, [REPO])
        self.assertEqual(self.api.files[REPO]["data/local.json"], NEWS)
        self.assertEqual(len(self.api.files[SOURCE]["platform/guilds.json"]["guilds"]), 1)
        self.assertEqual(self.api.issue["state"], "open")
        self.assertIn("does not mean the site is already live", self.api.comments[-1])

    def test_unrelated_repo_collision_never_written(self):
        self.api.repos[REPO] = {"full_name": REPO, "private": False, "default_branch": "main", "description": "bespoke"}
        self.api.files[REPO] = {"README.md": "do not replace"}
        with self.assertRaisesRegex(ValueError, "collision"):
            self.provision()
        self.assertEqual(self.api.files[REPO], {"README.md": "do not replace"})
        self.assertFalse(self.api.pages)

    def test_failed_dispatch_retry_does_not_duplicate_repo(self):
        self.api.fail_dispatch.add(REPO)
        with self.assertRaises(app.APIError):
            self.provision()
        self.api.fail_dispatch.clear()
        self.provision()
        self.assertEqual(self.api.created, [REPO])
        self.assertEqual(self.api.dispatches, [REPO])

    def test_recheck_issue_before_create(self):
        original = self.api.call

        def mutate(method, path, data=None):
            if path == "/users/" + OWNER:
                self.api.issue["state"] = "closed"
            return original(method, path, data)

        self.api.call = mutate
        with self.assertRaisesRegex(ValueError, "open issue"):
            self.provision()
        self.assertFalse(self.api.created)

    def test_registry_conflict_is_reported_not_overwritten(self):
        self.api.conflict = True
        with self.assertRaises(app.APIError):
            self.provision()
        self.assertEqual(self.api.files[SOURCE]["platform/guilds.json"]["guilds"], [])

    def test_reserved_repo_with_unexpected_files_refused(self):
        self.provision()
        del self.api.files[REPO][".build-guild-platform.json"]
        with self.assertRaisesRegex(ValueError, "unexpected files"):
            self.provision()

    def test_generated_artifact_builds_standalone(self):
        request = self.api.issue
        config = app.parse_issue(request)
        files = app.generated_files(config, app.identity_for(SOURCE, 7, request, config), NEWS)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, content in files.items():
                target = root.joinpath(*name.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            completed = subprocess.run([sys.executable, str(root / "scripts" / "build_site.py")],
                                       capture_output=True, text=True, cwd=root)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            page = (root / "_site" / "index.html").read_text(encoding="utf-8")
            self.assertIn("<title>Iowa Build Guild</title>", page)
            self.assertIn("New workshop", page)
            self.assertTrue((root / "_site" / ".nojekyll").is_file())
            self.assertEqual(files[".github/workflows/pages.yml"], (app.ROOT / "templates" / "pages.yml").read_text())


class BroadcastTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeAPI()
        app.provision(self.api, self.api, SOURCE, "maintainer", 7, app.issue_digest(self.api.issue), SETTINGS)
        self.approval = app.publish_snapshot(self.api, SOURCE)[2]
        self.api.calls.clear()
        self.api.dispatches.clear()

    def publish(self):
        return app.broadcast(self.api, self.api, SOURCE, "maintainer", self.approval, SETTINGS)

    def test_publish_approved_feed_and_only_network_file_then_retry(self):
        before = copy.deepcopy(self.api.files[REPO])
        version, good, bad = self.publish()
        self.assertEqual(version, self.approval)
        self.assertEqual(good, [REPO])
        self.assertFalse(bad)
        self.assertEqual(self.api.files[SOURCE]["published/network.json"], NEWS)
        for name in before:
            self.assertEqual(self.api.files[REPO][name], NEWS if name == "data/network.json" else before[name])
        self.api.calls.clear()
        self.publish()
        self.assertFalse(any(c[0] == "PUT" for c in self.api.calls))
        self.assertEqual(len(self.api.dispatches), 2)

    def test_no_publication_without_current_approval_and_write_permission(self):
        self.api.permission = "read"
        with self.assertRaisesRegex(ValueError, "Approval"):
            self.publish()
        self.api.permission = "write"
        self.api.files[SOURCE]["content/network.json"] = EMPTY
        with self.assertRaisesRegex(ValueError, "changed"):
            self.publish()
        self.assertEqual(self.api.files[SOURCE]["published/network.json"], EMPTY)

    def test_registry_changes_invalidate_approval(self):
        self.api.files[SOURCE]["platform/guilds.json"]["guilds"][0]["enabled"] = False
        with self.assertRaisesRegex(ValueError, "changed"):
            self.publish()

    def test_failure_is_explicit_and_retry_dispatches_already_updated_data(self):
        self.api.fail_dispatch.add(REPO)
        _, good, bad = self.publish()
        self.assertFalse(good)
        self.assertIn(REPO, bad[0])
        self.assertEqual(self.api.files[REPO]["data/network.json"], NEWS)
        self.api.fail_dispatch.clear()
        _, good, bad = self.publish()
        self.assertEqual(good, [REPO])
        self.assertFalse(bad)

    def test_missing_opt_in_never_updates_target(self):
        del self.api.files[REPO][".build-guild-platform.json"]
        _, good, bad = self.publish()
        self.assertFalse(good)
        self.assertIn("identity mismatch", bad[0])
        self.assertEqual(self.api.files[REPO]["data/network.json"], EMPTY)

    def test_bespoke_explicit_opt_in_does_not_touch_html(self):
        entry = self.api.files[SOURCE]["platform/guilds.json"]["guilds"][0]
        identity = {"schema_version": 1, "source": SOURCE, "repository": REPO, "mode": "bespoke"}
        entry.update(mode="bespoke", identity=identity)
        self.api.files[REPO][".build-guild-platform.json"] = identity
        self.api.files[REPO]["index.html"] = "<html>bespoke</html>"
        self.approval = app.publish_snapshot(self.api, SOURCE)[2]
        _, good, bad = self.publish()
        self.assertEqual(good, [REPO])
        self.assertFalse(bad)
        self.assertEqual(self.api.files[REPO]["index.html"], "<html>bespoke</html>")

    def test_disabled_candidates_are_not_contacted(self):
        self.api.files[SOURCE]["platform/guilds.json"]["guilds"][0]["enabled"] = False
        self.approval = app.publish_snapshot(self.api, SOURCE)[2]
        _, good, bad = self.publish()
        self.assertFalse(good or bad)
        self.assertFalse(any(REPO in call[1] for call in self.api.calls))


class SyncTests(unittest.TestCase):
    def opener(self, value):
        def open_response(request, timeout):
            self.assertEqual(request.full_url,
                             f"https://raw.githubusercontent.com/{SOURCE}/main/published/network.json")
            self.assertEqual(timeout, 45)
            return io.BytesIO(value)
        return open_response

    def test_fetch_render_and_preserve_local_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "data").mkdir()
            (root / ".build-guild-platform.json").write_text(json.dumps({"source": SOURCE}))
            (root / "data" / "network.json").write_text(json.dumps(EMPTY))
            (root / "data" / "local.json").write_text(json.dumps(EMPTY))
            (root / "data" / "guild.json").write_text(json.dumps(guild()))
            sync_network.sync(root, self.opener(json.dumps(NEWS).encode()))
            site.build(root)
            self.assertEqual(json.loads((root / "data" / "local.json").read_text()), EMPTY)
            self.assertIn("New workshop", (root / "_site" / "index.html").read_text())

    def test_failure_leaves_last_good_content_and_deployment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "data").mkdir()
            (root / "_site").mkdir()
            (root / "_site" / "index.html").write_text("last good deployed output")
            (root / ".build-guild-platform.json").write_text(json.dumps({"source": SOURCE}))
            target = root / "data" / "network.json"
            target.write_text(json.dumps(NEWS))
            for payload in (b"not json", b'{"schema_version": 1, "items": "bad"}', b"x" * (sync_network.MAX_BYTES + 1)):
                with self.assertRaises(ValueError):
                    sync_network.sync(root, self.opener(payload))
                self.assertEqual(json.loads(target.read_text()), NEWS)
            with self.assertRaises(URLError):
                sync_network.sync(root, lambda *_args, **_kwargs: (_ for _ in ()).throw(URLError("offline")))
            self.assertEqual((root / "_site" / "index.html").read_text(), "last good deployed output")
            self.assertFalse(target.with_suffix(".json.tmp").exists())

    def test_source_cannot_inject_fetch_host_or_path(self):
        with self.assertRaises(ValueError):
            sync_network.fetch("evil.test/repo/../../payload", self.opener(b"{}"))


class APITests(unittest.TestCase):
    def test_optional_only_ignores_404(self):
        api = app.API("test-not-a-real-token")
        with patch.object(api, "call", side_effect=app.APIError(403, "denied")):
            with self.assertRaises(app.APIError):
                api.optional("/repos/owner/repo")
        with patch.object(api, "call", side_effect=app.APIError(404, "not found")):
            self.assertIsNone(api.optional("/repos/owner/repo"))

    def test_non_default_ref_rejected(self):
        with patch.dict("os.environ", {"GITHUB_REF": "refs/heads/untrusted"}):
            with self.assertRaisesRegex(ValueError, "default branch"):
                app.ensure_default_branch(FakeAPI(), SOURCE)


if __name__ == "__main__":
    unittest.main()

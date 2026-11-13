# Setup and operations

## 1. Configure the platform

Keep the platform default branch **main**. Create a `guild-request` label in this
repository before opening requests (the form uses it). Enable Actions and allow
`actions/checkout`, `setup-python`, `create-github-app-token`, `upload-pages-artifact`
and `deploy-pages`. Set the target owner's default branch for new repositories
to **main**. The provisioner explicitly refuses another default branch rather
than creating a broken deployment trigger.

Create the Actions environment **platform-automation**. Restrict its deployment
branches to main and, where your GitHub plan supports it, add required maintainer
reviewers and prevent self-review. Environment approval is extra defense; the
mandatory boundary is manual workflow dispatch plus a fresh API check of the
dispatch actor's write/maintain/admin permission and the exact reviewed digest.
An issue label alone never authorizes provisioning or publishing.

Protect changes to workflows, scripts, owner configuration, subscriber registry
and approved feed with branch rules/review requirements appropriate to your
organization. Only trusted maintainers should have write access. GitHub's
`GITHUB_TOKEN` needs contents write for registry/feed commits and issues write
for status comments. If branch rules reject these commits, explicitly authorize
the automation identity under your policy, or review/merge those data changes
manually before retrying. Do not remove protections just to make a run green.

## 2. Supply supported cross-repository credentials

The ordinary platform `GITHUB_TOKEN` cannot create/write arbitrary repositories.
No token is stored in generated sites. Choose one supported path for all targets
accessible to that credential; do not mix owners inaccessible to that identity.

### Personal owner (initial KenedyBastian configuration)

Use an **owner-bound user token** in environment secret
`PLATFORM_TARGET_TOKEN`; leave `TARGET_APP_ID` unset. The implementation calls
`GET /user` and requires the authenticated login to equal the configured personal
owner before `POST /user/repos`. It cannot create under an issue-supplied stranger.

Prefer a fine-grained PAT owned by that account, resource owner that account,
repository access **All repositories** (selected-only cannot include repositories
that do not exist yet), with repository permissions:

| Permission | Access | Reason |
| --- | --- | --- |
| Administration | Read/write | Create repository, configure Pages |
| Contents | Read/write | Initialize site and publish data |
| Workflows | Read/write | Commit the Pages workflow |
| Actions | Read/write | Explicitly dispatch child deployment workflows |
| Pages | Read/write | Read/create the Pages configuration |
| Metadata | Read | Read repository and identity state (automatic) |

GitHub currently also documents **Repository creation: write** as an alternative
creation permission; Administration is still required by this implementation's
Pages setup. Use the permission names available for your token/account. If your
fine-grained token cannot perform the required endpoints in your account/policy,
the supported fallback for public sites is a classic PAT with **public_repo**
and **workflow** scopes. It is broader; use a dedicated automation account only
if it is the intended personal owner, a short expiration, rotation and the
environment boundary. Never paste tokens into issues, commands/logs or JSON.

The [current create-personal-repository API docs](https://docs.github.com/en/rest/repos/repos#create-a-repository-for-the-authenticated-user)
list fine-grained PATs, App user tokens **and installation tokens**, with
Administration or Repository creation write. Do not assume either that every
installation can create personal repos, or that installation tokens are universally
unsupported: endpoint/account/permission support must be verified. **This platform
deliberately uses a user-authenticated personal-owner path**, not an unverified
installation-token personal-provisioning claim. App user tokens must be refreshed
externally if used in the token secret; automatic OAuth refresh is not implemented.

### Organization owner with a GitHub App

Add the organization to `platform/config.json`:

```json
{"type": "Organization", "auth": "installation"}
```

Install an operator-owned App on that organization with repository
**Administration, Contents, Workflows, Actions and Pages: write** and Metadata:
read. Installation access must cover **all target repositories**, including newly
created ones. Obtain organization approval if required by policy. Set environment
variables `TARGET_APP_ID`, `TARGET_APP_OWNER` (the allowlisted organization), and
secret `TARGET_APP_PRIVATE_KEY`. The workflow mints a short-lived installation
token and revokes it on completion; no long-lived installation token secret is
needed. It calls `POST /orgs/{org}/repos`, never the personal `/user/repos` route.

An organization may instead use `"auth": "user"` and the user-token secret with
organization-approved permissions/policy. `GET /users/{owner}` verifies the
configured owner type in both paths.

References: [organization creation](https://docs.github.com/en/rest/repos/repos#create-an-organization-repository),
[Pages creation](https://docs.github.com/en/rest/pages/pages#create-a-github-pages-site),
[workflow dispatch](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event),
[App installation authentication](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/authenticating-as-a-github-app-installation).

Finally set `provisioning_enabled` to `true` in the reviewed config commit.
No real target provisioning is validated merely by committing this code.

## 3. Review and provision an issue

Use the **Request a Build Guild site** form. `Iowa`, state `IA`, with a blank slug
becomes `Iowa Build Guild` / `iowa-build-guild`. An existing suffix is normalized
without duplication. A slug override is allowed but never interpreted as a path
or command. Public support is a JSON array with `name`, `role`, optional HTTPS
`url`. All other content is plain text; HTML is escaped.

Review the full public content, public-link consent, support contacts, allowed
owner and target name. The requester is trusted issue-author metadata (ID/login),
recorded in the target marker and registry. Public support contacts are independent
of requester and owner. No collaborators are added automatically: any access grant
is a separate owner-admin decision outside this automation.

The intake workflow comments the digest. From **Actions -> Provision guild ->
Run workflow**, choose main and provide the issue number/digest. The workflow
re-reads current issue state/content/author and owner settings. Editing any answer
or title invalidates approval; re-preview and review the new digest.

The generated site includes state identity, description, meeting callout, local
updates, approved network news/events and public support cards. Semantic landmarks,
skip link, keyboard focus, responsive grid, reduced-motion support and light/dark
themes are included. Accent color is decorative and never controls text contrast.
No framework install or client-side executable feed/HTML is required.

On success the issue receives the **actual returned URLs** and a deployment
request status. Check the target **Deploy guild Pages** run and the live page.
The issue remains open for retries; close it only after confirming deployment.
Pages URLs are not proof that hosting is already live.

## 4. Author and approve network news/events

Edit `content/network.json`, not the approved feed. It is a full snapshot;
removing an item intentionally removes it at the next approved publication.
Stable unique IDs prevent duplication. Both kinds use ISO calendar dates:

```json
{
  "schema_version": 1,
  "items": [
    {
      "id": "winter-workshop",
      "kind": "event",
      "title": "Build together workshop",
      "body": "Public description. Registration opens soon.",
      "date": "2026-12-15",
      "url": "https://github.com"
    }
  ]
}
```

Run `python -m scripts.automation check` on the intended main revision (CI also
prints the digest). Review the **draft and complete subscriber registry** together.
Run **Publish network updates** on main with that digest. The live snapshot must
match exactly. The workflow first promotes the reviewed content to
`published/network.json`, then updates only `data/network.json` and dispatches
Pages in each enabled, registered, owner-allowlisted, identity-matching subscriber.
With zero subscribers it still publishes the approved public feed.

The run summary lists accepted dispatches and each failed repository. A successful
dispatch is not a successful child deployment; check target Actions runs. Failures
do not roll back already updated sites or the approved feed. Correct the integration
or permission problem and retry the same digest; already-current content is not
rewritten, but deployment is dispatched again. If the draft/registry changed,
review and approve a new digest.

Only the approved feed is consumed by weekly pulls; changes to the draft never
leak into new guilds or scheduled deployments. For immediate push runs the exact
approved snapshot is committed in each child and dispatched with
`refresh_network: false` to avoid stale raw-cache reads. Weekly/manual refresh
downloads the fixed HTTPS raw GitHub feed, bounds its size, validates the schema,
atomically replaces the local network data, builds, then deploys. A download,
JSON/schema or build failure stops before deployment, preserving the last good
live site. Weekly data updates are in the build artifact, not committed back to
the child repo. Local `data/guild.json` and `data/local.json` are untouched.

Scheduled runs can be delayed by GitHub; public-repository schedules can be disabled
after 60 days without repository activity. Monitor Actions and re-enable inactive
schedules. Use **Run workflow** to refresh on demand. The platform and approved
feed must remain public, and the platform branch main must remain available.

## 5. Safe retries and failures

The new repository's description reserves an exact source issue and normalized
config digest before initialization. Atomic initialization writes an identity
marker alongside the site. Retries reuse it only when the marker matches. An
unmarked repository must have the exact reservation and contain only its initial
README; otherwise initialization fails without overwriting it. Name collisions
never adopt an unrelated site. Choose a different slug after a collision.

Do not delete the marker or change the request after partial provisioning:
inspect the resulting repository, resolve the error and retry the same approval.
Changed config after partial creation intentionally requires operator reconciliation
rather than overwriting local edits. Changing the owner's new-repository default
branch after creation does not rename an already created branch; fix that branch
explicitly if the provisioner reports a non-main default.

Registry/feed writes use file SHA optimistic concurrency; initial multi-file writes
use non-forced Git commits. Conflicts fail rather than overwrite newer changes.
Platform provisioning and publishing share a concurrency group. No failed run
automatically deletes a repository, undoes a successful publication, or marks
all subscribers successful. Network/timeouts can leave a completed side effect
with an unconfirmed response: inspect GitHub state and retry, never blindly delete.

## 6. Opt in an existing bespoke site (owner-reviewed only)

OH, PA and CA are **not registered**. Do not run the new-site provisioner against
them. Their owners may integrate deliberately without replacing their HTML:

1. Add `data/network.json` with the empty version-1 items document. Add a
   `.build-guild-platform.json` marker with exact values:
   `{"schema_version":1,"source":"KenedyBastian/build-guild-platform","repository":"OWNER/REPO","mode":"bespoke"}`.
2. Implement an escaped/validated renderer for that data within the existing
   site's own design. Optionally adopt the standalone sync helper and validator,
   adjusted for its own builder. Preserve all existing local content and URLs.
3. Adapt its deployment workflow to `.github/workflows/pages.yml` (or a thin
   wrapper there), support `workflow_dispatch` boolean input `refresh_network`
   (default true), and add the Monday weekly pull. Honor false for exact-snapshot
   broadcast dispatches. Require fetch/build success before deployment.
4. Give the platform's credential access only after the owner agrees. Add that
   owner to the allowlist. Add a reviewed registry entry containing `repository`,
   `enabled: true`, `mode: "bespoke"`, the exact marker object as `identity`, and
   the real `site_url`. Run tests/review before publishing a new digest.

The platform only writes the existing network data file and dispatches that
workflow; it never installs a renderer, replaces HTML or migrates those sites
without their owners' explicit work. Remove/disable the registry entry to stop
pushes, and remove the child's scheduled pull separately to stop weekly refresh.

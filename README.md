# Build Guild platform

Turn a reviewed GitHub issue into an independent Build Guild repository and GitHub
Pages website. Enter **Iowa** and the site becomes **Iowa Build Guild**, with default
repository slug `iowa-build-guild`. An existing "Build Guild" suffix is not repeated.

**[Request a guild site](https://github.com/KenedyBastian/build-guild-platform/issues/new?template=guild.yml)**.
Requests collect the guild name, state, description, public support teams/roles,
optional public meeting details, target owner and branding. The requester is
captured automatically from GitHub's issue author metadata, not an editable name.
Creating a site on someone's behalf does **not** impersonate them or grant access.

## How it works

1. The issue form produces structured answers. **Validate guild request** checks
   them and comments an approval digest. It cannot create repositories.
2. A maintainer reviews every answer and runs **Provision guild** with the issue
   number and digest. Current write/maintain/admin permission, issue state/content,
   owner allowlist and target identity are checked again at execution.
3. Automation creates a public repository, commits a complete responsive,
   light/dark, accessible static site and independent Pages workflow, enables
   Actions Pages, registers the subscriber and comments the actual API-returned
   repository/Pages URLs. The Actions run, not the existence of a URL, determines
   whether deployment succeeded.
4. Maintainers edit draft network news/events, then explicitly run **Publish
   network updates** with a reviewed digest. It promotes the approved feed and
   pushes only the network data file to registered, opted-in guilds.
5. Each generated child also pulls the approved feed and redeploys **weekly on
   Monday at 09:23 UTC**, or on manual dispatch. No credentials into this main repo
   are needed for that public read. Fetch/validation failure blocks deployment,
   leaving the last good live site in place.

Local identity, meeting details and local news stay in each child repository;
central publishing never rewrites them or any bespoke HTML. Failed operations
are explicit and safely retryable, with collision and optimistic-concurrency
guards. No server, database, external framework or runtime package is needed.

## Start here

**Provisioning is disabled by default.** An operator must configure cross-repository
credentials and the owner allowlist before a request can be approved.
See [setup and operations](docs/operations.md) for exact secrets/permissions,
approvals, retries and opt-in instructions for existing sites.

Run locally with Python 3.11+ (Actions uses 3.12):

```powershell
python -m unittest discover -s tests -v
python -m scripts.automation check
python -m scripts.automation preview-site
```

Open `_site/index.html` for a representative, fictional guild preview.
The tests use mocked API boundaries and generated standalone sites; they do not
create repositories or broadcast to real guilds.

## Content and templates

| Location | Purpose |
| --- | --- |
| `content/network.json` | Draft network news and events; not consumed by children |
| `published/network.json` | Approved public feed, consumed by weekly child refresh |
| `platform/config.json` | Explicitly trusted owners and provisioning switch |
| `platform/guilds.json` | Explicit registered subscribers; initially empty |
| `scripts/site_builder.py` | Escaping/validation and complete standalone site builder |
| `templates/pages.yml` | Independent deployment + weekly refresh workflow for new sites |
| `examples/` | Fictional preview content, never a live subscriber |

This follows the **parameterized independent-instance** pattern demonstrated by
[octodemo-framework/demo_octocat_supply](https://github.com/octodemo-framework/demo_octocat_supply):
structured inputs become a configured instance. That repository is an Octodemo
template, not the issue-intake service implemented here. This platform does not
depend on Octodemo or copy its application backend.

The read-only design references were
[OH](https://github.com/msftaustin/OHBuildGuild),
[PA](https://github.com/msftaustin/PennDOTBuildGuild), and
[CA](https://github.com/jileary23/CAGitHubBuildGuild):
guild identity, meeting callouts, support and local/network updates, with light/dark
styling. They are **integration candidates, not active subscribers**. No original
repository, private meeting link, contact detail or bespoke site is modified.

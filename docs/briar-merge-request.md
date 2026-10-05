# Upstream merge request: the private-group API for briar-headless

Prepared in session S25 (D29, D51, D53) for the owner to submit to Briar's GitLab. The branch is
`1664-private-group-api` in `~/Projects/briar`: one commit, "Add a private group API to
briar-headless", on `release-1.5.21`, which is also upstream `master` (checked 2026-10-05: no commits
since the release), so it is rebased on `master` as it is.

## State of the branch (2026-10-05)

- Upstream CI's tasks for headless pass in `eclipse-temurin:17-jdk`:
  `./gradlew --configure-on-demand briar-headless:check briar-headless:linuxJars`, 174 tests
  (99 existing, 75 new), no failures. Animal Sniffer is not applied to headless (only to the
  `bramble-*` and `briar-*` libraries the patch does not touch).
- The pre-review checklist (Briar wiki "pre-review-checklist", spikes.md S3 part 2), item by item:
  - *Thread safety:* the controller is stateless (`@Immutable`, only final injected fields); every
    change is one database transaction, so concurrent posts cannot fork the author's chain (D51).
  - *Minimal visibility:* the implementation and the output functions are `internal`; only the
    controller interface and the Dagger module are public, as in the forum package.
  - *Exceptions:* `DbException` is declared on every transaction; Briar's `NoSuch…Exception`s become
    404s; `ProtocolStateException` of a reveal under way is logged and ignored, as in the app.
  - *No blocking in event handlers:* `eventOccurred` only turns the event's own header and text
    into JSON for the WebSocket; no database access.
  - *No transactions while holding locks:* the patch takes no locks.
  - *Style:* Kotlin official style like the rest of headless; only import lines exceed 100 columns.
  - *Dependencies:* none added, so Gradle Witness is unchanged.
- It changes four existing files (`HeadlessModule.kt`, `Router.kt`, `HeadlessTestModule.kt`,
  `IntegrationTest.kt`) and the README, and adds the `privategroups` package and its tests.

## Steps for the owner

1. Sign in to <https://code.briarproject.org> (an account there; GitLab may ask to verify it before
   forking) and fork `briar/briar`.
2. Push the branch to the fork:
   ```bash
   cd ~/Projects/briar
   git remote add fork https://code.briarproject.org/<user>/briar.git
   git push -u fork 1664-private-group-api
   ```
   The fork is also the patch's public home: chatko's image can then be built from
   `BRIAR_SRC=https://code.briarproject.org/<user>/briar.git#1664-private-group-api`
   (deploy/README.md), so its default in the compose files can become that URL.
3. Open a merge request from `<user>/briar:1664-private-group-api` into `briar/briar:master` with
   the title and description below, and link it in a comment on issue #1664.
4. Before submitting, check that `master` has not moved: `git fetch origin master` and
   `git log release-1.5.21..origin/master`. If it has, rebase the branch on it and run the Gradle
   command above again.

## Title

Add a private group API to briar-headless (#1664)

## Description

Addresses #1664 for private groups (forums are not part of this).

This turns private groups on in briar-headless (`shouldEnablePrivateGroupsInCore`) and adds REST
endpoints and WebSocket events for them, so that a headless peer can take part in private groups
created by people in the Android app, or create its own. It only calls Briar's existing managers
(`PrivateGroupManager`, `GroupInvitationManager` and their factories), the way the Android app's
controllers do; nothing changes outside `briar-headless` and no dependency is added.

Endpoints (all documented in the README):

- `GET /v1/groups`, `POST /v1/groups`: list the peer's private groups; create one with the peer as
  the creator.
- `DELETE /v1/groups/{groupId}`: dissolve a group the peer created, or leave one it joined.
- `GET /v1/groups/{groupId}/members`, `POST /v1/groups/{groupId}/members/reveal`: list members,
  with the contact ID of every member who is a contact; reveal the relationship with such a
  contact, so that the two sync the group directly.
- `GET /v1/groups/{groupId}/invitations`, `POST /v1/groups/{groupId}/invitations`: the creator
  lists the sharing status of its contacts and invites one.
- `GET /v1/groups/invitations`, `POST /v1/groups/invitations/{groupId}`: invitations to groups
  created by others; accept or decline one.
- `GET /v1/groups/{groupId}/messages`, `POST /v1/groups/{groupId}/messages`,
  `POST /v1/groups/{groupId}/messages/read`: list joins and posts, write a post, mark a message
  read.
- WebSocket: `GroupMessageAddedEvent` for other members' joins and posts, in the same form as
  listed, and `GroupDissolvedEvent`.

Notes for review:

- Group IDs in URL paths are URL-safe base64 (RFC 4648 §5, padding optional), because standard
  base64 can contain `/`; in JSON they are standard base64 like everywhere else in the API.
- Every group ID in a path is checked against the peer's private groups and answered with 404
  otherwise, since `removePrivateGroup` and the private-group readers would act on any group.
  Marking a message read also checks that the message belongs to the group.
- Writing a post and sending an invitation each happen in one transaction (read the previous
  message or timestamp, sign, store), so two concurrent requests cannot chain to the same
  previous message. A post's timestamp is the later of now and the group's latest message plus
  1 ms, as in the app.
- Refused state changes answer 403 with `{"error": …}`: `NOT_CREATOR`, `NOT_SHAREABLE` (with the
  contact's status), `NOT_MEMBER`, `DISSOLVED`. Invalid input answers 400 like the existing
  routes.
- Turning the flag on needs no migration: Briar sets the private-group clients up for an existing
  account at its next start. A phone running Briar 1.5.21 showed an existing headless account as
  "not supported" for invitations, and could invite it as soon as the two were connected again
  after the switch, on the same data.
- `IntegrationTest` got a port parameter: the API server of a finished test class keeps its port,
  so the new integration test class uses a port of its own.

Testing: 75 new tests (unit tests with mockk and JSONAssert, an integration test class against a
real peer, its WebSocket included); `./gradlew :briar-headless:check` passes with all 174 tests.
Tried by hand against Briar 1.5.21 on an Android phone in both directions: a group the phone
created (invitation, accept, joins and posts as events, posts from headless, members, mark read,
dissolve) and a group headless created (invite, the phone's acceptance and posts, dissolve);
declining, leaving and revealing between two headless peers.

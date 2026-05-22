# Session Branching Refactor Plan

This document records the planned refactor from fork-as-new-session behavior to
branch-aware message management inside a single TUI session.

## Current Behavior

The current TUI session model stores each session as a flat JSON file under
`.lambda/sessions/`. A session has one linear `history` list. Rewind/fork creates
a new session by truncating the parent history before the selected user message
and continuing from there.

This is simple, but it causes several problems:

- Session count grows quickly when users explore alternatives.
- The relationship between a parent session and its forks is not first-class.
- The session list mixes real workspaces with small branch experiments.
- The user mental model is closer to "continue this conversation from here" than
  "create a completely new session".

## Target Model

A session should represent the larger conversation workspace. Branches should be
represented inside that session as a message tree.

The persisted model should eventually move from a single linear `history` list to
a schema with message nodes and an active leaf, for example:

```json
{
  "id": "session_id",
  "schema_version": 2,
  "message_nodes": {
    "msg_001": {
      "id": "msg_001",
      "parent_id": null,
      "role": "user",
      "content": "...",
      "created_at": "..."
    },
    "msg_002": {
      "id": "msg_002",
      "parent_id": "msg_001",
      "role": "assistant",
      "content": "...",
      "created_at": "..."
    },
    "msg_003": {
      "id": "msg_003",
      "parent_id": "msg_001",
      "role": "assistant",
      "content": "alternative answer...",
      "created_at": "..."
    }
  },
  "active_leaf_id": "msg_003"
}
```

Agent calls should still receive a linear message list. The TUI/session layer
should project the selected branch from root to `active_leaf_id` into the existing
chat-history format before invoking the agent.

## Design Principles

- Keep one TUI session for one conversation workspace.
- Treat rewind/fork as branch creation inside the current session.
- Keep model/provider calls compatible by projecting the active branch to a
  linear history.
- Preserve compatibility with existing v1 session files.
- Avoid exposing the full tree UI before the persistence and projection model is
  stable.

## Proposed API Shape

The app layer should avoid manipulating the tree directly. Session code should
provide small operations such as:

```python
session.get_active_history()
session.append_to_active_branch(message)
session.fork_from_message(message_id)
session.switch_branch(leaf_id)
```

This keeps most TUI and agent-invocation logic close to the current linear
history flow while allowing the persistence layer to become branch-aware.

## Migration Plan

### Phase 1: Add the branch-aware session model

- Introduce a v2 session schema with message nodes and `active_leaf_id`.
- Load legacy v1 sessions by converting their flat `history` into a single trunk
  branch in memory.
- Add projection from active branch to linear history.
- Keep the TUI rendering only the active branch at first.

### Phase 2: Replace fork-as-new-session

- Change rewind/fork so it creates a branch inside the current session instead of
  creating a new session file.
- Keep the session list stable: one conversation remains one session.
- Preserve the current active plan/session metadata behavior where possible, but
  explicitly decide which fields are session-level and which are branch-level.

### Phase 3: Add branch navigation UI

- Show a small branch indicator when the active path has siblings.
- Allow switching between sibling branches.
- Consider branch names after the basic switch flow is stable.
- Defer a full tree visualization until the data model proves useful.

### Phase 4: Clean up compatibility behavior

- Decide whether to keep exporting a branch as a separate session.
- Decide whether v1 sessions should be rewritten as v2 on save or only migrated
  lazily in memory.
- Remove obsolete fork-as-session assumptions after tests cover branch behavior.

## Metadata Decisions to Resolve

Branching changes the meaning of some metadata. Before implementation, decide the
owner for each field:

| Field | Likely owner |
| --- | --- |
| Session id/name | Session-level |
| Provider/model | Session-level by default; message-level if historical accuracy is needed |
| Context usage | Active branch summary or per-message stats |
| Active plan metadata | Probably branch-level or active-leaf-associated |
| Created/updated timestamps | Session-level and message-level |
| Active branch selection | Session-level `active_leaf_id` |

The active plan decision is the riskiest one. A rewind/fork can represent a new
implementation strategy, so blindly sharing the same active plan across all
branches may be misleading.

## Test Coverage Needed

Add or update tests for:

- Loading old v1 flat-history sessions.
- Saving and loading v2 branch-aware sessions.
- Projecting the active branch into linear history.
- Forking from a selected message without creating a new session file.
- Switching between sibling branches.
- Preserving the recently fixed behavior that empty placeholder sessions are not
  saved unless the user has sent a message.
- Session list behavior when a session contains multiple branches.

## Rollout Notes

This refactor should be treated as a session/message-model change, not a small UI
patch. The safest path is to implement the model and projection first, then move
rewind/fork onto that model, and only then improve branch navigation UI.

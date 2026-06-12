# fanout-peer-replacement Specification

### Requirement: Peer replacement applies to every fan-out execution mode

When an agent fails inside a fan-out execution mode, the runner SHALL attempt a single peer-agent substitution before treating the turn as degraded. This behavior MUST be uniform across all five fan-out modes: parallel, competitive, collaborative, debate, and speculative. The runner SHALL use the existing `_maybe_replace_failed` helper, invoked between the agent dispatch and the point where the failure would otherwise set `any_failure = True`.

Peer replacement in fan-out modes SHALL be limited to a single one-hop substitution (the `REPLACE_WITH_PEER` strategy). Multi-strategy retry remains sequential-mode only; this requirement does not introduce cancel-and-retry semantics into `asyncio.gather`.

#### Scenario: Producer fails in parallel mode

- **WHEN** an agent fails during a `parallel` workflow turn and a peer is configured for it
- **THEN** the configured peer is dispatched in the failed agent's slot and its result is used in the merge

#### Scenario: Producer fails in collaborative mode

- **WHEN** an agent fails during a `collaborative` workflow turn and a peer is configured for it
- **THEN** the configured peer is dispatched in the failed agent's slot and contributes its section to the merged document

### Requirement: Competitive mode replaces a failed candidate before ranking

In competitive mode the runner SHALL attempt peer replacement for any candidate-producing agent that fails, before the evaluator ranks candidates. A recovered candidate MUST be included in the ranking set.

#### Scenario: Failed candidate is replaced and ranked

- **WHEN** a candidate agent fails in a `competitive` turn and a peer is configured for it
- **THEN** the peer is dispatched in the failed slot, and its result enters the candidate set the evaluator ranks over

#### Scenario: Failed candidate with no configured peer

- **WHEN** a candidate agent fails in a `competitive` turn and no peer is configured for it
- **THEN** no peer is dispatched, the turn proceeds with the remaining candidates, and the existing `any_failure` degradation behavior is preserved

### Requirement: Debate mode replaces a failed debater before synthesis

In debate mode the runner SHALL attempt peer replacement for a debater that fails, before the synthesizer runs, so a failure does not drop a debating voice.

#### Scenario: Failed debater is replaced

- **WHEN** a debater agent fails in a `debate` turn and a peer is configured for it
- **THEN** the peer is dispatched in the failed debater's slot and its contribution is carried into synthesis

### Requirement: Speculative mode replaces a failed leader

In speculative mode the runner SHALL attempt peer replacement for the leader slot (the cheap-or-strong response chosen as the leader) when it fails. The non-leader side already serves as a natural fallback, so peer replacement is scoped to the leader and SHALL NOT be applied to the side that already succeeded.

#### Scenario: Leader fails and is replaced

- **WHEN** the leader response in a `speculative` turn fails and a peer is configured for the leader agent
- **THEN** the peer is dispatched and its result is used as the leader text

#### Scenario: Non-leader fails, leader succeeds

- **WHEN** one side of a `speculative` turn fails but the side selected as leader succeeds
- **THEN** no peer replacement is attempted, because the successful leader already satisfies the turn

### Requirement: Unrecoverable or unconfigured failures fall through unchanged

When a fan-out agent failure is unrecoverable, or no peer is configured for the failed agent, the runner SHALL NOT dispatch a peer and SHALL preserve the existing degradation behavior (`any_failure = True`, the turn continues with remaining results).

#### Scenario: Unrecoverable failure is not replaced

- **WHEN** an agent fails with an unrecoverable error (for example `execution_refused`) in any fan-out mode
- **THEN** no peer is dispatched and the turn degrades exactly as it did before this change

### Requirement: Recovered fan-out failures are audited

When peer replacement recovers a fan-out failure in competitive, debate, or speculative mode, the runner SHALL persist the same audit trail it persists for parallel and collaborative: one `repair_runs` row with `strategy_attempted='replace_with_peer'` recording the failing agent, the peer, the failure kind, and the outcome, and one `agent_runs` row for the peer marked `retried=True`.

#### Scenario: Audit rows written on competitive recovery

- **WHEN** a peer replacement recovers a failed candidate in a `competitive` turn
- **THEN** a `repair_runs` row exists with `strategy_attempted='replace_with_peer'` and `outcome='recovered'`, and an `agent_runs` row exists for the peer with `retried=True`

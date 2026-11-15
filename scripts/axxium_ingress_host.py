"""Run one admitted Axxium ingress phase under the native lock and hold."""

import axxium_ingress_transaction as journal
from axxium_minimal_release import NativeHost


class IngressHost(NativeHost):
    def command(self, args, *, effect=False, pass_fds=(), **options):
        # A surviving Docker client must keep the lock across a controller exit.
        if effect:
            self.recheck_lock()
            pass_fds = tuple(set((*pass_fds, self.lock_fd)))
        return super().command(args, effect=effect, pass_fds=pass_fds, **options)


def execute(adapter, operation_id, plan_sha256, action, phase=None, *, paths=None):
    """Use fresh host observations for one action; reconciliation has no effects."""
    if action not in ("start", "step", "reconcile", "finish"):
        raise journal.Held("Unknown ingress host action")
    if (action == "step" and phase not in journal.PHASES) or (action != "step" and phase is not None):
        raise journal.Held("An ingress phase is required only for a step")

    host = IngressHost() if paths is None else IngressHost(lock_path=paths.lock, pending_path=paths.pending)

    def observe(states):
        host.recheck_lock()
        result = adapter.observe(host, states)
        host.recheck_lock()
        if not isinstance(result, journal.Observation) or result.state not in states:
            raise journal.Held("Host readback did not establish an admitted state")
        return result

    initial = (lambda: observe({"shared_live"})) if action == "start" else None
    with journal.open_journal(operation_id, plan_sha256, observe_initial=initial, paths=paths, host=host) as change:
        status = "started"
        if action == "step":
            if change.pending is not None or change.terminal:
                raise journal.Held("Reconcile the pending phase or finish terminal cleanup first")
            sources, _ = journal.PHASES[phase]
            source = observe(sources)
            change.begin(phase, source)
            host.recheck_lock()
            adapter.effect(host, phase)
        if action in ("step", "reconcile"):
            if change.pending is None:
                raise journal.Held("Ingress journal has no phase to reconcile")
            _, target = journal.PHASES[change.pending]
            observed = observe({change.current.state, target})
            if action == "reconcile" and change.current.agrees(observed):
                # Docker can continue a previously accepted daemon request
                # even after its client exits or times out.
                raise journal.Held("The interrupted effect has not been independently settled")
            status = change.reconcile(observed)
        if action == "finish":
            if change.pending is not None or change.current.state not in journal.TERMINAL:
                raise journal.Held("A reconciled terminal state is required")
            change.finish(observe({change.current.state}))
            status = "finished"
        return {"status": status, "state": change.current.state, "exposure": change.exposure,
                "state_sha256": change.current.state_sha256, "records": change.count}

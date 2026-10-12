# T1 Client API context repair

The rerun after the platform model repair (run `38208097945`, job
`dade425e-5e10-436a-a338-5c12f6fe393b`, commit `2dcf09d`) reached actual model
tool calls. Both `lookup_customer` and `verify_customer` received the supplied
email and customer ID, but failed with `Client API context has not been
initialized` before making a REST search. T1 failed 0/1.

Our `_sync_session()` eagerly read `client_api.context.conversation_id` on
every tool invocation. This introduced a dependency that ordinary customer
search/detail endpoints do not have. The SDK constructs `ClientAPI` without
context and only binds context when the host reset payload supplies it. A
fresh toolkit is constructed at reset. The earlier local real-model test
injected a context in its reference backend and missed this host condition.

The repair tolerates only the SDK's exact uninitialized-context exception
during session synchronization. The fresh toolkit's local identity remains
isolated, and a later supplied or changed context still clears the identity,
proposals, plans and transfer state. Other context errors propagate. No
conversation ID is invented: transfer requests continue to require the
host's trusted context. Identity search, customer-ID comparison and profile
read ordering are unchanged.

Validation: 180 offline tests passed, including three new real tau2 REST
runtime cases for a contextless identity lookup, a contextless transfer with
no outgoing request, and identity clearing on later trusted context binding.
No provider call or credential read was required for these tests.

Remote verification uses the unchanged binding, `main`, `retail_plus`,
Python, `t1`, all current public integration cases. The result is recorded
in the corresponding GitHub artifact and ignored `.git/enterprise-evaluations`
directory; local tests alone do not establish a remote pass.

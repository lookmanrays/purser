# M1 replay and retained history

The store worker identified the complete-suffix case while qualifying M1-A. An independent verifier reproduced it on integrated candidate `8956aabc812ad15e46fc843ae66f5224483c9bf1`. Removing revisions 5–8 of the public fixture left a valid prefix at 4 and a previously published head at 8. Replay accepted 4 and overwrote the surviving evidence that revision 8 had existed.

**Invariant and authority.** I2 requires the journal to reconstruct all accepted state. A surviving retained head supplies an independent comparison for the prefix it names. Batch and link verification alone authenticate the internal consistency of the available prefix. This first finding belongs to the M1 replay/publication cluster. No Reset has been issued in this cluster or this delivery session.

**Affected paths.** The Store API has four surfaces. Initialization of an existing store, current reads and admission compare the materialized head and projection with the folded log. Replay deliberately rebuilds damaged derived rows. Its former unconditional publication also discarded a contradictory retained head. The regression set includes a whole missing suffix, a different correctly chained history at the retained revision, a damaged batch, an older valid anchor, and a fresh store containing only original events. Concurrent readers must see one completed state when rebuild succeeds or fails.

**Treatment.** The store checks a surviving head against its actual complete position in the original log before publishing the rebuilt projection, using the same SQLite write transaction. It can rebuild from the genesis of a newly initialized log-only restore. A self-consistent rollback of both history and checkpoint requires a head retained elsewhere to detect. External backup qualification remains with M7. Acceptance requires a fresh independent run on the integrated correction.

## Reset 1 · Historical reads and the same retained anchor

Independent M1-G returned NO-GO on `fc6d72a92fc8340b2f90f35e77d824be6efaabd3` on the clean local development branch. The first treatment fixed complete-suffix and fork detection in replay. For the same divergent eight-event history, unqualified status and replay failed at the retained head, while status and why naming revision 8 returned alternate history. The verifier compared both legitimate histories and the surviving physical rows, with the latter unchanged through all four calls.

The disproven assumption was that reaching the end of a requested verified prefix sufficed for every historical query. A surviving anchor is evidence regardless of which consumer asks for its revision. The implementation class includes every public consumer of an event fold and its interaction with complete versus bounded reads, live projection checks and repairs. The log, SQLite transaction and original v0 bytes remain the accepted authority. The claim of integrated integrity and the earlier implementation map are invalidated. The frozen vectors, CLI success path and crash atomicity evidence retain their original candidate identities.

The replay/publication cluster has consumed its one Reset. Session Reset count is one. Implementation is paused pending the reconstructed coverage, independent two-stage challenge and reconciled M1 preflight.

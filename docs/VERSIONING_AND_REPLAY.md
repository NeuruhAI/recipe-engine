# Versioning and Replay

- Contract schema versions on every type (`autonomy.*.v1`)
- Engine version: `recipe-engine/autonomy@0.1.0`
- Config version + config_hash in DecisionPacket
- Replay: same opportunity+candidates+config → same input_hash, packet_id, receipt_hash
- Candidate order permutation invariant (sorted by strategy_id before evaluation)

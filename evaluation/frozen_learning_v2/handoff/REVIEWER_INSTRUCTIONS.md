# Reviewer instructions

Review the retained original package, conversion report, exact v2 raw package,
freeze receipt, exposure disclosure and generation record. Confirm that:

1. the conversion report preserves the canonical operational-material digest
   and sealed expected-results commitment;
2. the receipt hashes match the exact files reviewed;
3. preparation history is stated honestly, including `UNKNOWN` where exact
   original time was not retained;
4. learner-visible and evaluator-retained exposure is disclosed;
5. the reviewer is actually separate from the preparer; and
6. review time is the actual time of this review, after receipt/freeze.

Return a JSON document conforming to `../review.schema.json`. The
`identity_authentication` value must remain
`NOT_PROVEN_BY_DIGEST_ALONE`. Describe the evidence and independence basis in
the bounded text fields. Do not inspect or expose sealed answers to the
learner, run ORION, or claim authenticated identity/time without separate
evidence.

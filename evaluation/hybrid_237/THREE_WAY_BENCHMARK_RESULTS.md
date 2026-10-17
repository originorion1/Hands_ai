# Three-way benchmark

The fixed 72-row fixture has eight designed positive tenant-period-class
units. Conventional rules plus the simple price statistic produce 8 TP, 1 FP,
precision 0.889 and recall 1.000. Existing ORION's applicable missing-document
investigation produces 1 TP, 0 FP, precision 1.000 and recall 0.125. The
hybrid union produces 8 TP and 1 FP: exactly the conventional result, with
zero incremental validated discoveries and zero extra false positives.
Under a six-minute review assumption, baseline and hybrid queues each require
54 analyst minutes. ORION-only requires six minutes but misses seven of the
eight designed positives. These are synthetic design labels, not blinded
independent customer ground truth. No superiority or generalization follows.

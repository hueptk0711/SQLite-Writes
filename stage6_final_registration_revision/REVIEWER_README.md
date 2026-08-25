# Stage 6E Final Registration Revision

This package locks the final Stage 6 confirmation denominator after human review.

The original registered set had 500 CRUDSQL official test Create samples. Stage 6E
excludes exactly the 19 SOURCE_TASK_INVALID items identified by the accepted R04
resolution workflow, with no replacement samples. The final confirmation set has
481 samples: 460 original review-accepted gold items plus 21 corrected-and-
re-reviewed accepted gold items.

This stage creates the final gold corpus hash and replays all 481 final gold
programs on fresh isolated SQLite databases. It also anchors each final gold
artifact to the exact human-reviewed content hash and approval path, and checks
all upstream human-review roots against accepted constants. It does not call a model, does not
use GPU, and does not permit confirmation inference. The next stage is GPU
environment preflight after reviewer acceptance.

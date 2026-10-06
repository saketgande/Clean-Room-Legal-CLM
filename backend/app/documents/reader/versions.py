"""Which clause in a new version is which clause in the last one.

A clause keeps its `clause_id` across versions when it is recognisably the same
clause, and that is what lets its comments follow it (anchoring's rung 1). A
content hash changes on any edit — precisely when identity matters most — and
position alone breaks the moment a clause is inserted above. So both:

1. The two versions' clauses are aligned in order, and a clause whose words are
   unchanged keeps its id. The body is compared, not the number, so the clauses
   renumbered by an insertion above them are still the same clauses.
2. Inside each stretch that changed, clauses are paired by similarity, best
   pair first; a pair at or above `CARRY_FLOOR` keeps its id — a clause reworded.
3. Everything else is new, and an old clause with no partner was deleted.
"""

import difflib

from rapidfuzz import fuzz

# How alike a reworded clause must stay to still be the same clause, as a
# rapidfuzz ratio (0-100). Below it the id is not carried, and the clause's
# annotations are found by their quotes instead — or orphaned, honestly.
CARRY_FLOOR = 70.0


def carry_ids(previous: list[tuple[str, str]], bodies: list[str]) -> list[str | None]:
    """For each new clause body, the id of the clause it continues, or None.

    `previous` is the last version's clauses as (clause_id, body), in order.
    """
    old = [_norm(text) for _, text in previous]
    new = [_norm(text) for text in bodies]
    carried: list[str | None] = [None] * len(new)
    matcher = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                carried[j1 + k] = previous[i1 + k][0]
        elif tag == "replace":
            pairs = sorted(
                ((fuzz.ratio(old[i], new[j]), i, j) for i in range(i1, i2) for j in range(j1, j2)),
                reverse=True,
            )
            taken_old: set[int] = set()
            for score, i, j in pairs:
                if score < CARRY_FLOOR:
                    break
                if i in taken_old or carried[j] is not None:
                    continue
                taken_old.add(i)
                carried[j] = previous[i][0]
    return carried


def _norm(text: str) -> str:
    return " ".join(text.split()).lower()

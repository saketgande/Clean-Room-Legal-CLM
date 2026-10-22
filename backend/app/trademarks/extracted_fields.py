"""Best-effort lookups into ``DocumentExtract.extracted_fields``.

The keys of ``extracted_fields`` are whatever the uploader named them in their
own field schema (``Jurisdiction`` vs ``City`` vs ``jurisdiction``) or the fixed
set the IP India journal parser emits, so there is no fixed schema to rely on.
"""


def find_field(fields: dict, keywords: tuple[str, ...], exclude: tuple[str, ...] = ()) -> str | None:
    for key, value in fields.items():
        lowered = key.lower()
        if any(kw in lowered for kw in keywords) and not any(ex in lowered for ex in exclude) and value:
            return str(value)
    return None


def find_name(fields: dict) -> str | None:
    return find_field(fields, ("name",), exclude=("number",))


def find_jurisdiction(fields: dict) -> str | None:
    return find_field(fields, ("jurisdiction", "city", "location"))

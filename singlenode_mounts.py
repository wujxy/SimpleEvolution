"""Small, dependency-free parsing helpers for single-node mount policy."""


def parse_read_only_mounts(value: str) -> list[tuple[str, str]]:
    """Parse whitespace-separated ``SOURCE:DESTINATION`` mount mappings."""
    mounts = []
    for item in value.split():
        source, separator, destination = item.partition(":")
        if not source or not separator or not destination:
            raise ValueError("read-only mounts must be SOURCE:DESTINATION")
        mounts.append((source, destination))
    return mounts

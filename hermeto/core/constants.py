import enum


class Mode(str, enum.Enum):
    """Represents a global CLI option to relax input expectations and requirements checks."""

    STRICT = "strict"
    PERMISSIVE = "permissive"

    def __str__(self) -> str:
        return self.value

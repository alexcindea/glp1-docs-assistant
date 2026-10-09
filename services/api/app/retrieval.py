"""Finding the chunks that answer a question."""

from collections.abc import Sequence


def search_text(section_path: Sequence[str], text: str) -> str:
    """What retrieval indexes for a chunk: where it sits, then what it says.

    A label's "Nausea | 4 | 18" row or a short "Discard the pen after 30 days"
    means little without "6.1 Clinical Trials Experience" or "16 HOW SUPPLIED"
    above it. Only the search sees this; the citeable text is stored unchanged.
    """
    if not section_path:
        return text
    return f"{' > '.join(section_path)}\n{text}"

"""Helper for inserting and extracting text from a string"""

from typing import Optional


class TextWrapper:
    """
    A class for inserting and extracting text from a string
    """

    def __init__(self, prefix: str, suffix: str) -> None:
        """
        A class for inserting and extracting text from a string.

        The text is wrapped with a prefix and suffix, which are used to identify the
        content across subsequent operations.

        Parameters:
            - prefix: The prefix to wrap the content with
            - suffix: The suffix to wrap the content with
        """
        self._field_prefix = prefix
        self._field_suffix = suffix

    def contains(self, text: str) -> bool:
        """
        Check if the text contains the wrapped content

        Parameters:
            - text: The text to check for the wrapped content

        Returns:
            True if the text contains wrapped content, False otherwise

        >>> w = TextWrapper("=== START ===", "=== END ===")
        >>> w.contains("This is some\\n=== START ===\\nwrapped\\n=== END ===\\ncontent")
        True
        >>> w.contains("This is some\\nunwrapped\\ncontent")
        False
        """
        return self._field_prefix in text and self._field_suffix in text

    def get(self, text: str) -> Optional[str]:
        """
        Get the wrapped content from the provided text

        Parameters:
            - text: The text to extract the wrapped content from

        Returns:
            The wrapped content, or None if the text does not contain the
            wrapped content

        >>> w = TextWrapper("=== START ===", "=== END ===")
        >>> w.get("This is some\\n=== START ===\\nwrapped\\n=== END ===\\ncontent")
        'wrapped'
        >>> w.get("This is some\\nunwrapped\\ncontent")  # returns None
        """
        start = text.find(self._field_prefix)
        if start == -1:
            return None
        start += len(self._field_prefix)
        end = text.find(self._field_suffix, start)
        return text[start:end].strip()

    def remove(self, text: str) -> str:
        """
        Remove the wrapped content from the text

        Parameters:
            - text: The text to remove the wrapped content from

        Returns:
            The text with the wrapped content removed

        >>> w = TextWrapper("=== START ===", "=== END ===")
        >>> w.remove("This is some\\n=== START ===\\nwrapped\\n=== END ===\\ncontent")
        'This is some\\ncontent'
        >>> w.remove("This is some\\nunwrapped\\ncontent")
        'This is some\\nunwrapped\\ncontent'
        """
        start = text.find(self._field_prefix)
        if start == -1:
            return text
        end = text.find(self._field_suffix, start)
        front = text[:start]
        back = text[end + len(self._field_suffix) :]
        return front + back.lstrip()

    def upsert(self, text: str, new_content: str) -> str:
        """
        Insert or update the wrapped content within the text

        Parameters:
            - text: The text to insert or update the wrapped content in
            - new_content: The new content to insert or update

        Returns:
            The text with the wrapped content inserted or updated

        >>> w = TextWrapper("=== START ===", "=== END ===")
        >>> w.upsert("This is some\\n=== START ===\\nwrapped\\n=== END ===\\ncontent", "new")
        'This is some\\n=== START ===\\nnew\\n=== END ===\\ncontent'
        >>> w.upsert("This is some\\nunwrapped\\ncontent", "new")
        'This is some\\nunwrapped\\ncontent\\n=== START ===\\nnew\\n=== END ==='
        """
        start = text.find(self._field_prefix)
        if start == -1:
            return (
                text
                + "\n"
                + self._field_prefix
                + "\n"
                + new_content
                + "\n"
                + self._field_suffix
            )
        end = text.find(self._field_suffix, start)
        return (
            text[:start]
            + self._field_prefix
            + "\n"
            + new_content
            + "\n"
            + self._field_suffix
            + text[end + len(self._field_suffix) :]
        )

"""
Helper functions for working with the Confluence API

See the Confluence Storage Format documentation for more information on the tags
that are supported for building content:
https://confluence.atlassian.com/doc/confluence-storage-format-790796544.html
"""

import logging
import xml.etree.ElementTree as ET
from typing import Optional

_logger = logging.getLogger(__name__)

# Atlassian Confluence uses XML to store content, but the XML that is retrieved
# isn't a well-formed document. To properly parse, the below header and footer
# are necessary (e.g., _CONFLUENCE_HEADER + content + _CONFLUENCE_FOOTER).
#
# References:
#     - https://jira.atlassian.com/browse/CONFCLOUD-60739
#     - https://confluence.atlassian.com/doc/confluence-storage-format-790796544.html
_CONFLUENCE_HEADER = """\
    <?xml version='1.0'?>
      <xml xmlns:atlassian-content="http://atlassian.com/content"
           xmlns:ac="http://atlassian.com/content"
           xmlns:ri="http://atlassian.com/resource/identifier"
           xmlns:atlassian-template="http://atlassian.com/template"
           xmlns:at="http://atlassian.com/template">
    """
_CONFLUENCE_FOOTER = "</xml>"

# The namespaces used in the Confluence XML also need to be registered w/ ET
ET.register_namespace("ac", "http://atlassian.com/content")
ET.register_namespace("ri", "http://atlassian.com/resource/identifier")
ET.register_namespace("at", "http://atlassian.com/template")


class CFElement(ET.Element):
    """
    XML Element with some convenience methods

    This class extends the standard xml.etree.ElementTree.Element class with:
    - A constructor that takes optional content
    - An add() method to append content to the element
    - An unwrap() method to return the XML content as a string without the tag
    """

    def __init__(
        self,
        tag,
        attrib: Optional[dict[str, str]] = None,
        content: Optional[str | ET.Element] = None,
        **extra
    ):
        """
        Create a new XML element with optional content

        Parameters:
            - tag: The tag name
            - attrib: Dictionary of attributes
            - content: Optional text content

        Examples:
            Create an element with content:
            >>> e = CFElement("p", content="Hello, world!")
            >>> print(ET.tostring(e, encoding="unicode"))
            <p>Hello, world!</p>

            Create an element without content:
            >>> e = CFElement("br")
            >>> print(ET.tostring(e, encoding="unicode"))
            <br />
        """
        super().__init__(tag, attrib or {}, **extra)
        if content is not None:
            self.add(content)

    def add(self, content: int | str | ET.Element) -> "CFElement":
        """
        Add content to the end of the Element

        The content can be a string or another Element. If the content is a
        string, it will be added as text content to the end of the element,
        after any existing text and subelements. If the content is an Element,
        it will be added as the last subelement.

        Parameters:
            - content: The content to add

        Returns:
            The Element itself, to allow chaining

        Example:
            >>> e = CFElement("p")
            >>> _ = e.add("Hello, ")
            >>> _ = e.add(CFElement("b", content="world"))
            >>> _ = e.add("!")
            >>> print(ET.tostring(e, encoding="unicode"))
            <p>Hello, <b>world</b>!</p>

            # The same example using method chaining
            >>> e = CFElement("p").add("Hello, ").add(CFElement("b", content="world")).add("!")
            >>> print(ET.tostring(e, encoding="unicode"))
            <p>Hello, <b>world</b>!</p>

            # Simple text can be concatenated as well
            >>> e = CFElement("p").add("Hello, ").add("world").add("!")
            >>> print(ET.tostring(e, encoding="unicode"))
            <p>Hello, world!</p>

            # Multiple elements
            >>> e = CFElement("p").add(CFElement("b", content="Hello")).add(", ")
            >>> _ = e.add(CFElement("i", content="world")).add("!")
            >>> print(ET.tostring(e, encoding="unicode"))
            <p><b>Hello</b>, <i>world</i>!</p>
        """
        if isinstance(content, int):
            content = str(content)
        if isinstance(content, str):
            has_subelements = len(self) > 0
            if has_subelements:
                self[-1].tail = self[-1].tail or ""
                self[-1].tail += content
            else:
                self.text = self.text or ""
                self.text += content
        else:
            self.append(content)
        return self

    def unwrap(self, encoding="unicode") -> str:
        """
        Return the XML content of the Element as a string, omitting the tag of
        the Element itself.

        Returns:
            The XML content as a string

        Example:
            >>> root = CFElement("root")
            >>> _ = root.add(CFElement("p", content="Hello, world!"))
            >>> print(ET.tostring(root, encoding="unicode"))
            <root><p>Hello, world!</p></root>
            >>> print(root.unwrap())  # without the <root> tag
            <p>Hello, world!</p>
        """
        return "".join(ET.tostring(e, encoding=encoding) for e in self)


def anchor(title: str, url: str) -> CFElement:
    """
    Create an anchor element

    Parameters:
        - title: The title of the anchor
        - url: The URL to link to

    Returns:
        A CFElement representing an anchor

    Example:
        >>> e = anchor("Google", "https://www.google.com")
        >>> print(ET.tostring(e, encoding="unicode"))
        <a href="https://www.google.com">Google</a>
    """
    return CFElement("a", {"href": url}, content=title)


def list_to_li(items: list[str | ET.Element], ordered=False) -> CFElement:
    """
    Create a list Element

    Parameters:
        - items: A list of strings or Elements to add as list items
        - ordered: Whether the list should be ordered (True) or unordered (False)

    Returns:
        A CFElement representing a list

    Examples:
        >>> e = list_to_li(["One", "Two", "Three"])
        >>> print(ET.tostring(e, encoding="unicode"))
        <ul><li>One</li><li>Two</li><li>Three</li></ul>

        >>> e = list_to_li(["One", CFElement("b", content="Two"), "Three"], ordered=True)
        >>> print(ET.tostring(e, encoding="unicode"))
        <ol><li>One</li><li><b>Two</b></li><li>Three</li></ol>
    """
    tag = "ol" if ordered else "ul"
    e = CFElement(tag)
    for item in items:
        e.add(CFElement("li", content=item))
    return e


def jiralink(issue_key: str) -> CFElement:
    # pylint: disable=line-too-long
    """
    Link to a Jira issue.

    This is a special Confluence link that will render as a Jira issue link. It
    appears to render as an inline element.

    Parameters:
        - issue_key: The Jira issue key

    Returns:
        A CFElement representing a Jira issue link

    Example:
        >>> e = jiralink("ABC-123")
        >>> print(ET.tostring(e, encoding="unicode"))
        <ac:structured-macro ac:name="jira" ac:schema-version="1" ac:macro-id="9245001e-9ae4-4e0f-b383-dd3952c98ae0"><ac:parameter ac:name="server">Red Hat Issue Tracker</ac:parameter><ac:parameter ac:name="columnIds">issuekey,summary,issuetype,created,updated,duedate,assignee,reporter,priority,status,resolution</ac:parameter><ac:parameter ac:name="columns">key,summary,type,created,updated,due,assignee,reporter,priority,status,resolution</ac:parameter><ac:parameter ac:name="serverId">6a7247df-aeb5-31ba-bf94-111b6698af21</ac:parameter><ac:parameter ac:name="key">ABC-123</ac:parameter></ac:structured-macro>
    """
    #   <p>
    #     <ac:structured-macro ac:name="jira" ac:schema-version="1" ac:macro-id="9245001e-9ae4-4e0f-b383-dd3952c98ae0">
    #       <ac:parameter ac:name="server">Red Hat Issue Tracker</ac:parameter>
    #       <ac:parameter ac:name="columnIds">issuekey,summary,issuetype,created,updated,duedate,assignee,reporter,priority,status,resolution</ac:parameter>
    #       <ac:parameter ac:name="columns">key,summary,type,created,updated,due,assignee,reporter,priority,status,resolution</ac:parameter>
    #       <ac:parameter ac:name="serverId">6a7247df-aeb5-31ba-bf94-111b6698af21</ac:parameter>
    #       <ac:parameter ac:name="key">OCTO-2</ac:parameter>
    #     </ac:structured-macro>
    #   </p>

    macro = CFElement(
        "ac:structured-macro",
        {
            "ac:name": "jira",
            "ac:schema-version": "1",
            "ac:macro-id": "9245001e-9ae4-4e0f-b383-dd3952c98ae0",
        },
    )
    macro.add(
        CFElement(
            "ac:parameter", {"ac:name": "server"}, content="Red Hat Issue Tracker"
        )
    )
    macro.add(
        CFElement(
            "ac:parameter",
            {"ac:name": "columnIds"},
            content="issuekey,summary,issuetype,created,updated,duedate,assignee,reporter,priority,status,resolution",
        )
    )
    macro.add(
        CFElement(
            "ac:parameter",
            {"ac:name": "columns"},
            content="key,summary,type,created,updated,due,assignee,reporter,priority,status,resolution",
        )
    )
    macro.add(
        CFElement(
            "ac:parameter",
            {"ac:name": "serverId"},
            content="6a7247df-aeb5-31ba-bf94-111b6698af21",
        )
    )
    macro.add(CFElement("ac:parameter", {"ac:name": "key"}, content=issue_key))
    return macro

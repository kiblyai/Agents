"""The email reader now lives in agentkit (other agents read emails too); these names stay importable from here."""

from agentkit.inbox import (EMAIL_TYPES, MAX_PART, Attachment, Email, html_to_text, pdf_text, read_eml,  # noqa: F401
                            read_inbox, read_txt, split_thread)

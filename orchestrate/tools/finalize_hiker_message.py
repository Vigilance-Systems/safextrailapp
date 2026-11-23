"""
Tool: finalize_hiker_message   (diagram: dashed "Messages to hiker" path)

Satellite messengers carry short messages (inReach: about 160 characters). This tool enforces that
limit, strips characters that satellite SMS can mangle, and ALWAYS marks the message as needing
operator approval. It sends nothing; a human sends it from the dashboard.
"""
try:
    from ibm_watsonx_orchestrate.agent_builder.tools import tool
except ImportError:                      # lets you run/test the file locally without the ADK
    def tool(*args, **kwargs):
        if args and callable(args[0]):
            return args[0]
        return lambda fn: fn


MAX_LEN = 160


@tool()
def finalize_hiker_message(text: str) -> dict:
    """Validate and shorten a draft safety message for a satellite device.

    Args:
        text: The draft message to the hiker.

    Returns:
        A dict with message (max 160 ASCII characters), length, truncated and requires_operator_approval=true.
    """
    t = text.replace("\u00b0", " deg ").replace("\u2019", "'").replace("\u2013", "-").replace("\u2014", "-")
    t = " ".join(t.encode("ascii", "ignore").decode().split())
    truncated = len(t) > MAX_LEN
    if truncated:
        t = t[:MAX_LEN].rsplit(" ", 1)[0].rstrip(",;:-")
    return {"message": t, "length": len(t), "truncated": truncated, "requires_operator_approval": True}

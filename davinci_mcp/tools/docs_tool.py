"""scripting_api_docs — search Blackmagic's shipped scripting reference (never touches Resolve)."""

from . import READ_ONLY, register
from .. import docs

DESCRIPTION = (
    "Search Blackmagic's shipped DaVinci Resolve scripting API reference (method signatures with "
    "docstrings from the .pyi stub, plus README sections). Use it before writing run_lua code: the "
    "Lua API has the same names as the Python one, called with a colon (timeline:GetName()). "
    "Returns {matches:[{kind,class,name,signature,doc,source}], total, truncated}."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "minLength": 1, "maxLength": 120,
                  "description": "Words to find, e.g. 'timeline marker add' or 'GetItemListInTrack'"},
        "class": {"type": "string", "maxLength": 60,
                  "description": "Restrict to one API class, e.g. Timeline, MediaPool, Project"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 30,
                  "description": "Max matches (default 10)"},
    },
    "required": ["query"],
    "additionalProperties": False,
}


@register("scripting_api_docs", DESCRIPTION, SCHEMA, READ_ONLY, timeout=5)
def scripting_api_docs(client, args):
    return docs.search(args["query"], limit=args.get("limit", 10), class_filter=args.get("class"))

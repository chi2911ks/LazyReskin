"""Attribute-only XML edits that preserve comments, encoding and formatting."""
from __future__ import annotations

import html
import re
from xml.parsers import expat

from .common import ReskinError
from .source import NAMESPACES, parse_xml


def patch_attributes(raw: bytes, changes: list[dict]) -> bytes:
    parse_xml(raw)
    parser = expat.ParserCreate(namespace_separator="|")
    parser.namespace_prefixes = True
    positions, stack, pending = [], [], {}

    def namespace(prefix, uri):
        pending[prefix or ""] = uri

    def start(tag, attributes):
        ns = dict(stack[-1] if stack else {})
        ns.update(pending)
        pending.clear()
        stack.append(ns)
        offset = parser.CurrentByteIndex
        match = re.match(rb'<(?:[^>"\']|"[^"]*"|\x27[^\x27]*\x27)*>', raw[offset:])
        if not match:
            raise ReskinError("Cannot locate XML start tag")
        names = {}
        for key in attributes:
            bits = key.split("|")
            canonical = NAMESPACES.get(bits[0], bits[0]) + ":" + bits[1] if len(bits) > 1 else key
            names[canonical] = (bits[2] + ":" + bits[1]) if len(bits) > 2 else bits[-1]
        positions.append((offset, offset + match.end(), names, ns))

    parser.StartNamespaceDeclHandler = namespace
    parser.StartElementHandler = start
    parser.EndElementHandler = lambda tag: stack.pop()
    parser.Parse(raw, True)
    grouped = {}
    for change in changes:
        grouped.setdefault(change["index"], {})[change["attribute"]] = change["value"]
    edits = []
    for index, attributes in grouped.items():
        if index >= len(positions):
            raise ReskinError("Layout changed since indexing")
        offset, end, names, namespaces = positions[index]
        tag = raw[offset:end]
        for canonical, value in attributes.items():
            name = names.get(canonical)
            if name is None:
                prefix, local = canonical.split(":", 1)
                uri = next((uri for uri, key in NAMESPACES.items() if key == prefix), None)
                actual = next((key for key, item in namespaces.items() if item == uri and key), None)
                if actual is None:
                    raise ReskinError(f"No namespace declared for {canonical}")
                name = actual + ":" + local
            encoded = html.escape(str(value), quote=True).encode("utf-8")
            pattern = re.compile(rb'(\s' + re.escape(name.encode()) + rb'\s*=\s*)(["\'])(.*?)\2', re.S)
            match = pattern.search(tag)
            if match:
                tag = tag[:match.start(3)] + encoded + tag[match.end(3):]
            else:
                closing = -2 if tag.endswith(b"/>") else -1
                tag = tag[:closing] + b' ' + name.encode() + b'="' + encoded + b'"' + tag[closing:]
        edits.append((offset, end, tag))
    for offset, end, tag in sorted(edits, reverse=True):
        raw = raw[:offset] + tag + raw[end:]
    parse_xml(raw)
    return raw

#!/usr/bin/env python3
"""
apply_plan_to_code.py (v1, state→code)
- Reads plan.json from `terraform show -json tfplan`
- For each resource with "update" actions, rewrites .tf so attributes match the *live state* (the plan's "before").
- Supports: scalars (string/number/bool), flat maps, flat lists
- Skips: nested blocks, expressions (var./local./module./functions), computed attrs, unknowns
"""

import json, re, sys, glob

SKIP_ATTRS = {
    "id", "arn", "owner_id", "primary_network_interface_id", "tags_all",
    # add more computed attrs if you see them in your plans
}

EXPR_SNIPPETS = [
    "${", "var.", "local.", "module.", "data.", "path.",
    "lookup(", "merge(", "tolist(", "tomap(", "cidrsubnet(", "format(", "join(", "concat("
]

def to_hcl(v):
    if isinstance(v, bool): return "true" if v else "false"
    if isinstance(v, (int, float)): return str(v)
    if isinstance(v, str):
        s = v.replace("\\", "\\\\").replace('"','\\"').replace("\n","\\n")
        return f"\"{s}\""
    if isinstance(v, list): return "[" + ", ".join(to_hcl(x) for x in v) + "]"
    if isinstance(v, dict):
        items = [f'{k} = {to_hcl(v[k])}' for k in sorted(v.keys())]
        return "{ " + ", ".join(items) + " }"
    return f"\"{str(v)}\""

def looks_like_expression(s: str) -> bool:
    return any(tok in s for tok in EXPR_SNIPPETS)

def index_tf_resources():
    """
    (rtype,rname) -> [{path,start,end}]
    """
    idx = {}
    for tf in glob.glob("**/*.tf", recursive=True):
        try:
            text = open(tf, "r", encoding="utf-8").read()
        except Exception:
            continue
        for m in re.finditer(r'resource\s+"([^"]+)"\s+"([^"]+)"\s*{', text):
            rtype, rname = m.group(1), m.group(2)
            # brace match
            i = m.end() - 1
            depth = 0
            end = None
            while i < len(text):
                c = text[i]
                if c == "{": depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
                i += 1
            if end:
                idx.setdefault((rtype, rname), []).append({"path": tf, "start": m.start(), "end": end})
    return idx

def find_attr_line(block_text, attr):
    """
    Find single-line `attr = <rhs>`; returns (start,end,indent,rhs) in block coords.
    """
    pat = re.compile(rf'(?m)^([ \t]*)({re.escape(attr)})\s*=\s*(.+?)\s*$')
    m = pat.search(block_text)
    if not m: return None
    return (m.start(), m.end(), m.group(1), m.group(3).strip())

def find_attr_braced_block(file_text, block_start, block_end, attr, opener, closer):
    """
    Find entire braced block like:
      attr = {
        ...
      }
    or
      attr = [
        ...
      ]
    Returns (span_start, span_end, indent) in FILE coords.
    """
    pat = re.compile(rf'(?m)^([ \t]*){re.escape(attr)}\s*=\s*\\{opener}')
    m = pat.search(file_text[block_start:block_end])
    if not m: return None
    indent = m.group(1)
    span_start = block_start + m.start()
    i = block_start + m.end() - 1   # at opener
    depth = 0
    while i < block_end:
        c = file_text[i]
        if c == opener: depth += 1
        elif c == closer:
            depth -= 1
            if depth == 0:
                return (span_start, i + 1, indent)
        i += 1
    return None

def build_map_attr(indent, attr, d):
    lines = [f"{indent}{attr} = {{\n"]
    for k in sorted(d.keys()):
        lines.append(f'{indent}  {k} = {to_hcl(d[k])}\n')
    lines.append(f"{indent}}}\n")
    return "".join(lines)

def build_list_attr(indent, attr, arr):
    lines = [f"{indent}{attr} = [\n"]
    for v in arr:
        lines.append(f"{indent}  {to_hcl(v)}\n")
    lines.append(f"{indent}]\n")
    return "".join(lines)

def insert_or_replace_scalar(file_text, block_start, block_end, attr, new_value_hcl):
    block = file_text[block_start:block_end]
    found = find_attr_line(block, attr)
    if found:
        astart, aend, indent, rhs = found
        if looks_like_expression(rhs):
            return file_text, False, f"SKIP {attr}: expression ({rhs[:40]}...)"
        new_line = f"{indent}{attr} = {new_value_hcl}"
        new_block = block[:astart] + new_line + block[aend:]
        return file_text[:block_start] + new_block + file_text[block_end:], True, f"UPDATED {attr}"
    else:
        # insert before closing brace
        mhead = re.match(r'([ \t]*)resource[^\n]*\n', file_text[block_start:block_end])
        base_indent = mhead.group(1) if mhead else ""
        insert_indent = base_indent + "  "
        insertion = f"\n{insert_indent}{attr} = {new_value_hcl}\n"
        new_text = file_text[:block_end-1] + insertion + file_text[block_end-1:]
        return new_text, True, f"ADDED {attr}"

def keys_to_update(before, after, after_unknown=None):
    """
    Return attribute keys we will sync (state→code).
    - Compare before vs after; if equal, no change.
    - Skip SKIP_ATTRS.
    - Skip attributes with unknown after values.
    - Allow scalars, flat maps, flat lists (of scalars).
    """
    keys = set()
    if not isinstance(before, dict) or not isinstance(after, dict):
        return keys
    a_unknown = after_unknown or {}
    for k in before.keys() | after.keys():
        if k in SKIP_ATTRS:
            continue
        if a_unknown.get(k) is True:
            # unknown value after apply; skip
            continue
        b, a = before.get(k), after.get(k)
        if b == a:
            continue
        # choose only types we can safely write
        if isinstance(b, (str, int, float, bool)):
            keys.add(k)
        elif isinstance(b, list) and all(isinstance(x, (str, int, float, bool)) for x in b):
            keys.add(k)
        elif isinstance(b, dict) and all(isinstance(v, (str, int, float, bool)) for v in b.values()):
            keys.add(k)
        # Note: skipping nested blocks/lists-of-objects for v1
    return keys

def main():
    if len(sys.argv) != 2:
        print("Usage: apply_plan_to_code.py plan.json", file=sys.stderr); sys.exit(2)

    plan = json.load(open(sys.argv[1], "r", encoding="utf-8"))
    idx = index_tf_resources()

    changes_applied = 0
    touched = set()

    for rc in (plan.get("resource_changes") or []):
        actions = rc.get("change", {}).get("actions", [])
        if "update" not in actions:
            continue

        rtype = rc.get("type")
        rname = rc.get("name")
        address = rc.get("address")

        change = rc.get("change", {})
        before = change.get("before") or {}
        after = change.get("after") or {}
        after_unknown = change.get("after_unknown") or {}

        # We sync from STATE -> CODE, so we write "before" values.
        keys = keys_to_update(before, after, after_unknown)
        if not keys:
            continue

        matches = idx.get((rtype, rname)) or []
        if len(matches) == 0:
            print(f"SKIP {address}: resource not found in code", file=sys.stderr)
            continue
        if len(matches) > 1:
            paths = [m["path"] for m in matches]
            print(f"SKIP {address}: multiple code matches {paths}", file=sys.stderr)
            continue

        entry = matches[0]
        path, start, end = entry["path"], entry["start"], entry["end"]
        try:
            file_text = open(path, "r", encoding="utf-8").read()
        except Exception as e:
            print(f"SKIP {address}: cannot read {path}: {e}", file=sys.stderr)
            continue

        any_changed = False

        for k in sorted(keys):
            val = before.get(k)  # <— state value to write into code

            # flat map -> replace whole map block
            if isinstance(val, dict) and all(isinstance(v, (str, int, float, bool)) for v in val.values()):
                blk = find_attr_braced_block(file_text, start, end, k, "{", "}")
                new_chunk = build_map_attr("  ", k, val).lstrip()
                if blk:
                    s, e, indent = blk
                    # keep existing indent style
                    new_chunk = build_map_attr(indent, k, val)
                    new_text = file_text[:s] + new_chunk + file_text[e:]
                else:
                    # insert near end of block
                    new_text = file_text[:end-1] + "\n  " + new_chunk + file_text[end-1:]
                delta = len(new_text) - len(file_text)
                file_text = new_text; end += delta; any_changed = True
                print(f"{address}: SYNC {k} (map)")

            # flat list -> replace whole list block
            elif isinstance(val, list) and all(isinstance(x, (str, int, float, bool)) for x in val):
                blk = find_attr_braced_block(file_text, start, end, k, "[", "]")
                new_chunk = build_list_attr("  ", k, val).lstrip()
                if blk:
                    s, e, indent = blk
                    new_chunk = build_list_attr(indent, k, val)
                    new_text = file_text[:s] + new_chunk + file_text[e:]
                else:
                    new_text = file_text[:end-1] + "\n  " + new_chunk + file_text[end-1:]
                delta = len(new_text) - len(file_text)
                file_text = new_text; end += delta; any_changed = True
                print(f"{address}: SYNC {k} (list)")

            # scalar -> replace single line or insert
            elif isinstance(val, (str, int, float, bool)):
                new_hcl = to_hcl(val)
                new_text, changed, msg = insert_or_replace_scalar(file_text, start, end, k, new_hcl)
                if changed:
                    delta = len(new_text) - len(file_text)
                    file_text = new_text; end += delta; any_changed = True
                    print(f"{address}: SYNC {k} (scalar) — {msg}")
                else:
                    print(f"{address}: {msg}")

        if any_changed:
            open(path, "w", encoding="utf-8").write(file_text)
            changes_applied += 1
            touched.add(path)

    print(f"APPLIED_CHANGES={changes_applied}")
    if touched:
        print("FILES_CHANGED=" + ",".join(sorted(touched)))

if __name__ == "__main__":
    main()

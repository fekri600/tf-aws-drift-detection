#!/usr/bin/env python3
"""
apply_plan_to_code.py (state→code v1.2)

- Syncs Terraform *.tf code to match the *live state* shown in plan.before
  (from: `terraform show -json tfplan`).
- Handles: scalars (str/int/float/bool), flat lists, flat maps (e.g., tags).
- Fully replaces existing attribute blocks (no duplicates).
- Removes attributes absent/empty in state (does NOT write empty {} or []).
- Skips computed attrs (id/arn/tags_all/...), and unknown-after values.
- Resource matching: by (resource type, resource name); if multiple blocks with
  same pair exist, the resource is skipped to avoid ambiguity.

Usage:
  python3 scripts/apply_plan_to_code.py plan.json
"""

import json, re, sys, glob

# Attributes we should never write back into code
SKIP_ATTRS = {
    "id", "arn", "owner_id", "primary_network_interface_id", "tags_all",
}

# ---------- HCL serialization helpers ----------

def to_hcl(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        s = v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f"\"{s}\""
    if isinstance(v, list):
        return "[" + ", ".join(to_hcl(x) for x in v) + "]"
    if isinstance(v, dict):
        items = [f'{k} = {to_hcl(v[k])}' for k in sorted(v.keys())]
        return "{ " + ", ".join(items) + " }"
    return f"\"{str(v)}\""

# ---------- TF file indexing ----------

def index_tf_resources():
    """
    Build index: (rtype, rname) -> [ {path,start,end}, ... ]
    Finds: resource "TYPE" "NAME" { ... } using brace counting.
    """
    idx = {}
    for tf in glob.glob("**/*.tf", recursive=True):
        try:
            text = open(tf, "r", encoding="utf-8").read()
        except Exception:
            continue
        for m in re.finditer(r'resource\s+"([^"]+)"\s+"([^"]+)"\s*{', text):
            rtype, rname = m.group(1), m.group(2)
            i = m.end() - 1
            depth, end = 0, None
            while i < len(text):
                c = text[i]
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
                i += 1
            if end:
                idx.setdefault((rtype, rname), []).append(
                    {"path": tf, "start": m.start(), "end": end}
                )
    return idx

def resource_base_indent(file_text, block_start, block_end):
    mhead = re.match(r'([ \t]*)resource[^\n]*\n', file_text[block_start:block_end])
    return (mhead.group(1) if mhead else "") + "  "  # indent two spaces inside block

# ---------- Find / delete existing attributes in a resource block ----------

def find_attr_braced_block(file_text, block_start, block_end, attr, opener, closer):
    """
    Find entire braced block like:
      attr = { ... }  or  attr = [ ... ]
    Returns (span_start, span_end, indent) in FILE coordinates.
    """
    pat = re.compile(rf'(?m)^([ \t]*){re.escape(attr)}\s*=\s*{re.escape(opener)}')
    m = pat.search(file_text[block_start:block_end])
    if not m:
        return None
    indent = m.group(1)
    span_start = block_start + m.start()
    i = block_start + m.end() - 1  # at opener char
    depth = 0
    while i < block_end:
        c = file_text[i]
        if c == opener:
            depth += 1
        elif c == closer:
            depth -= 1
            if depth == 0:
                return (span_start, i + 1, indent)
        i += 1
    return None

def delete_attr_occurrences(file_text, block_start, block_end, attr):
    """
    Remove *all* occurrences of `attr = ...` within the resource block:
    - braced map blocks: attr = { ... }
    - braced list blocks: attr = [ ... ]
    - single-line scalars: attr = <rhs>
    Returns (new_text, new_block_end, changed_flag).
    """
    changed = False
    # Remove braced maps
    while True:
        blk = find_attr_braced_block(file_text, block_start, block_end, attr, "{", "}")
        if not blk:
            break
        s, e, _ = blk
        file_text = file_text[:s] + file_text[e:]
        block_end -= (e - s)
        changed = True
    # Remove braced lists
    while True:
        blk = find_attr_braced_block(file_text, block_start, block_end, attr, "[", "]")
        if not blk:
            break
        s, e, _ = blk
        file_text = file_text[:s] + file_text[e:]
        block_end -= (e - s)
        changed = True
    # Remove scalar lines
    pat = re.compile(rf'(?m)^[ \t]*{re.escape(attr)}\s*=\s*.+?\n?')
    while True:
        block = file_text[block_start:block_end]
        m = pat.search(block)
        if not m:
            break
        s = block_start + m.start()
        e = block_start + m.end()
        file_text = file_text[:s] + file_text[e:]
        block_end -= (e - s)
        changed = True
    return file_text, block_end, changed

# ---------- Builders & inserters ----------

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

def insert_scalar(file_text, block_start, block_end, attr, val):
    insert_indent = resource_base_indent(file_text, block_start, block_end)
    insertion = f"\n{insert_indent}{attr} = {to_hcl(val)}\n"
    new_text = file_text[:block_end-1] + insertion + file_text[block_end-1:]
    return new_text, block_end + len(insertion), True

def insert_map(file_text, block_start, block_end, attr, d):
    if not d:  # empty map -> remove attribute entirely
        return file_text, block_end, False
    insert_indent = resource_base_indent(file_text, block_start, block_end)
    chunk = "\n" + build_map_attr(insert_indent, attr, d)
    new_text = file_text[:block_end-1] + chunk + file_text[block_end-1:]
    return new_text, block_end + len(chunk), True

def insert_list(file_text, block_start, block_end, attr, arr):
    if not arr:  # empty list -> remove attribute entirely
        return file_text, block_end, False
    insert_indent = resource_base_indent(file_text, block_start, block_end)
    chunk = "\n" + build_list_attr(insert_indent, attr, arr)
    new_text = file_text[:block_end-1] + chunk + file_text[block_end-1:]
    return new_text, block_end + len(chunk), True

# ---------- Diff selection (which keys to sync) ----------

def keys_to_sync(before, after, after_unknown=None):
    """
    Decide which attributes to sync from STATE (before) → CODE.
    Include:
      - keys where before != after, and
      - either 'before' has a simple type we can serialize, OR
      - 'before' is None but 'after' exists (meaning: delete from code).
    Skip:
      - SKIP_ATTRS, and attributes with unknown-after values.
    """
    keys = set()
    if not isinstance(before, dict) or not isinstance(after, dict):
        return keys
    a_unknown = after_unknown or {}
    for k in set(before.keys()) | set(after.keys()):
        if k in SKIP_ATTRS:
            continue
        if a_unknown.get(k) is True:
            continue
        b = before.get(k, None)  # live state
        a = after.get(k, None)   # desired (code)
        if b == a:
            continue
        if b is None:
            # present in desired, absent in state -> delete from code
            if a is not None:
                keys.add(k)
            continue
        # Syncable simple types
        if isinstance(b, (str, int, float, bool)):
            keys.add(k)
        elif isinstance(b, list) and all(isinstance(x, (str, int, float, bool)) for x in b):
            keys.add(k)
        elif isinstance(b, dict) and all(isinstance(v, (str, int, float, bool)) for v in b.values()):
            keys.add(k)
    return keys

# ---------- Main ----------

def main():
    if len(sys.argv) != 2:
        print("Usage: apply_plan_to_code.py plan.json", file=sys.stderr)
        sys.exit(2)

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

        keys = keys_to_sync(before, after, after_unknown)
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
            # 1) remove *all* existing occurrences of the attribute
            file_text, end, _ = delete_attr_occurrences(file_text, start, end, k)

            # 2) write the state (before) value — or remove entirely if absent/empty
            val = before.get(k, None)

            if val is None:
                # absent in state -> keep removed (we've deleted occurrences already)
                print(f"{address}: REMOVE {k} (absent in state)")
                any_changed = True
                continue

            # flat map
            if isinstance(val, dict) and all(isinstance(v, (str, int, float, bool)) for v in val.values()):
                if len(val) == 0:
                    print(f"{address}: REMOVE {k} (empty map in state)")
                    any_changed = True
                    continue
                file_text, end, changed = insert_map(file_text, start, end, k, val)
                any_changed = any_changed or changed
                print(f"{address}: SYNC {k} (map)")
                continue

            # flat list
            if isinstance(val, list) and all(isinstance(x, (str, int, float, bool)) for x in val):
                if len(val) == 0:
                    print(f"{address}: REMOVE {k} (empty list in state)")
                    any_changed = True
                    continue
                file_text, end, changed = insert_list(file_text, start, end, k, val)
                any_changed = any_changed or changed
                print(f"{address}: SYNC {k} (list)")
                continue

            # scalar
            if isinstance(val, (str, int, float, bool)):
                file_text, end, changed = insert_scalar(file_text, start, end, k, val)
                any_changed = any_changed or changed
                print(f"{address}: SYNC {k} (scalar)")
                continue

            # not a supported type -> attribute remains removed
            print(f"{address}: REMOVE {k} (unsupported type)")

        if any_changed:
            open(path, "w", encoding="utf-8").write(file_text)
            changes_applied += 1
            touched.add(path)

    print(f"APPLIED_CHANGES={changes_applied}")
    if touched:
        print("FILES_CHANGED=" + ",".join(sorted(touched)))

if __name__ == "__main__":
    main()

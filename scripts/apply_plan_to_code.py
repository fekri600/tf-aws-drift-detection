#!/usr/bin/env python3
"""
apply_plan_to_code.py (state→code v1.3)

Syncs Terraform *.tf code to match the *live state* in plan.before
(`terraform show -json tfplan`).

Supports:
  - Scalars (str/int/float/bool)
  - Flat maps (e.g., tags)  ->   attr = { ... }
  - Flat lists               ->   attr = [ ... ]
  - One-level blocks         ->   block_name { k = v, ... }
  - Lists of blocks          ->   repeated `block_name { ... }`

Behavior:
  - Replaces entire existing attr/block(s) (no duplicates).
  - Removes attributes/blocks absent or empty in state.
  - Skips computed attrs and unknown-after values.
  - Skips expression-driven lines (var./local./module./functions).
  - Matches resources by (type,name); skips if multiple matches exist.

Usage:
  python3 scripts/apply_plan_to_code.py plan.json
"""

import json, re, sys, glob

SKIP_ATTRS = {
    "id", "arn", "owner_id", "primary_network_interface_id", "tags_all",
}

EXPR_SNIPPETS = [
    "${", "var.", "local.", "module.", "data.", "path.",
    "lookup(", "merge(", "tolist(", "tomap(", "cidrsubnet(",
    "format(", "join(", "concat(",
]

# ---------- small helpers ----------

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

def is_scalar(x):
    return isinstance(x, (str, int, float, bool))

def is_object_dict(d):
    return isinstance(d, dict) and all(is_scalar(v) for v in d.values())

def is_list_of_scalars(lst):
    return isinstance(lst, list) and all(is_scalar(x) for x in lst)

def is_list_of_object_dicts(lst):
    return isinstance(lst, list) and all(is_object_dict(x) for x in lst)

def looks_like_expression(s: str) -> bool:
    return any(tok in s for tok in EXPR_SNIPPETS)

# ---------- index .tf resources ----------

def index_tf_resources():
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
                if c == "{": depth += 1
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
    return (mhead.group(1) if mhead else "") + "  "

# ---------- find / delete existing occurrences ----------

def find_attr_braced_block(file_text, block_start, block_end, attr, opener, closer):
    # attr = { ... }   or   attr = [ ... ]
    pat = re.compile(rf'(?m)^([ \t]*){re.escape(attr)}\s*=\s*{re.escape(opener)}')
    m = pat.search(file_text[block_start:block_end])
    if not m: return None
    indent = m.group(1)
    span_start = block_start + m.start()
    i = block_start + m.end() - 1
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

def find_unassigned_block(file_text, block_start, block_end, name):
    # block_name { ... }
    pat = re.compile(rf'(?m)^([ \t]*){re.escape(name)}\s*{{')
    m = pat.search(file_text[block_start:block_end])
    if not m: return None
    indent = m.group(1)
    span_start = block_start + m.start()
    i = block_start + m.end() - 1
    depth = 0
    while i < block_end:
        c = file_text[i]
        if c == "{": depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return (span_start, i + 1, indent)
        i += 1
    return None

def delete_attr_occurrences(file_text, block_start, block_end, name):
    changed = False
    # attr = { ... }
    while True:
        blk = find_attr_braced_block(file_text, block_start, block_end, name, "{", "}")
        if not blk: break
        s, e, _ = blk
        file_text = file_text[:s] + file_text[e:]
        block_end -= (e - s); changed = True
    # attr = [ ... ]
    while True:
        blk = find_attr_braced_block(file_text, block_start, block_end, name, "[", "]")
        if not blk: break
        s, e, _ = blk
        file_text = file_text[:s] + file_text[e:]
        block_end -= (e - s); changed = True
    # block_name { ... }
    while True:
        blk = find_unassigned_block(file_text, block_start, block_end, name)
        if not blk: break
        s, e, _ = blk
        file_text = file_text[:s] + file_text[e:]
        block_end -= (e - s); changed = True
    # scalar lines: name = ...
    pat = re.compile(rf'(?m)^[ \t]*{re.escape(name)}\s*=\s*.+?\n?')
    while True:
        block = file_text[block_start:block_end]
        m = pat.search(block)
        if not m: break
        s = block_start + m.start()
        e = block_start + m.end()
        file_text = file_text[:s] + file_text[e:]
        block_end -= (e - s); changed = True
    return file_text, block_end, changed

# ---------- builders & inserters ----------

def build_map_attr(indent, attr, d):
    lines = [f"{indent}{attr} = {{\n"]
    for k in sorted(d.keys()):
        lines.append(f"{indent}  {k} = {to_hcl(d[k])}\n")
    lines.append(f"{indent}}}\n")
    return "".join(lines)

def build_list_attr(indent, attr, arr):
    lines = [f"{indent}{attr} = [\n"]
    for v in arr:
        lines.append(f"{indent}  {to_hcl(v)}\n")
    lines.append(f"{indent}]\n")
    return "".join(lines)

def build_unassigned_block(indent, name, obj_dict):
    lines = [f"{indent}{name} {{\n"]
    for k in sorted(obj_dict.keys()):
        lines.append(f"{indent}  {k} = {to_hcl(obj_dict[k])}\n")
    lines.append(f"{indent}}}\n")
    return "".join(lines)

def insert_scalar(file_text, block_start, block_end, attr, val):
    insert_indent = resource_base_indent(file_text, block_start, block_end)
    chunk = f"\n{insert_indent}{attr} = {to_hcl(val)}\n"
    new_text = file_text[:block_end-1] + chunk + file_text[block_end-1:]
    return new_text, block_end + len(chunk), True

def insert_map(file_text, block_start, block_end, attr, d):
    if not d: return file_text, block_end, False
    insert_indent = resource_base_indent(file_text, block_start, block_end)
    chunk = "\n" + build_map_attr(insert_indent, attr, d)
    new_text = file_text[:block_end-1] + chunk + file_text[block_end-1:]
    return new_text, block_end + len(chunk), True

def insert_list(file_text, block_start, block_end, attr, arr):
    if not arr: return file_text, block_end, False
    insert_indent = resource_base_indent(file_text, block_start, block_end)
    chunk = "\n" + build_list_attr(insert_indent, attr, arr)
    new_text = file_text[:block_end-1] + chunk + file_text[block_end-1:]
    return new_text, block_end + len(chunk), True

def insert_block_object(file_text, block_start, block_end, name, obj):
    if not obj: return file_text, block_end, False
    insert_indent = resource_base_indent(file_text, block_start, block_end)
    chunk = "\n" + build_unassigned_block(insert_indent, name, obj)
    new_text = file_text[:block_end-1] + chunk + file_text[block_end-1:]
    return new_text, block_end + len(chunk), True

def insert_block_list(file_text, block_start, block_end, name, list_of_objs):
    if not list_of_objs: return file_text, block_end, False
    insert_indent = resource_base_indent(file_text, block_start, block_end)
    chunk = ""
    for obj in list_of_objs:
        chunk += "\n" + build_unassigned_block(insert_indent, name, obj)
    new_text = file_text[:block_end-1] + chunk + file_text[block_end-1:]
    return new_text, block_end + len(chunk), True

# ---------- choose which keys to sync ----------

def keys_to_sync(before, after, after_unknown=None):
    keys = set()
    if not isinstance(before, dict) or not isinstance(after, dict):
        return keys
    a_unknown = after_unknown or {}
    for k in set(before.keys()) | set(after.keys()):
        if k in SKIP_ATTRS:          # computed
            continue
        if a_unknown.get(k) is True: # unknown-after
            continue
        b, a = before.get(k, None), after.get(k, None)
        if b == a:                   # no diff
            continue
        # We sync state (b) → code. Accept types we can print safely:
        if is_scalar(b) or is_object_dict(b) or is_list_of_scalars(b) or is_list_of_object_dicts(b):
            keys.add(k)
        elif b is None and a is not None:
            # present in code/desired but absent in state -> delete
            keys.add(k)
    return keys

# ---------- main ----------

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

        rtype, rname = rc.get("type"), rc.get("name")
        address = rc.get("address")
        change = rc.get("change", {})
        before = change.get("before") or {}
        after  = change.get("after")  or {}
        after_unknown = change.get("after_unknown") or {}

        keys = keys_to_sync(before, after, after_unknown)
        if not keys:
            continue

        matches = idx.get((rtype, rname)) or []
        if len(matches) == 0:
            print(f"SKIP {address}: resource not found in code", file=sys.stderr); continue
        if len(matches) > 1:
            paths = [m["path"] for m in matches]
            print(f"SKIP {address}: multiple code matches {paths}", file=sys.stderr); continue

        entry = matches[0]
        path, start, end = entry["path"], entry["start"], entry["end"]

        try:
            file_text = open(path, "r", encoding="utf-8").read()
        except Exception as e:
            print(f"SKIP {address}: cannot read {path}: {e}", file=sys.stderr); continue

        any_changed = False

        for k in sorted(keys):
            # remove ALL existing occurrences (attr map/list, scalar line, or block)
            file_text, end, _ = delete_attr_occurrences(file_text, start, end, k)

            val = before.get(k, None)  # state value to write (or None => delete)

            if val is None:
                print(f"{address}: REMOVE {k} (absent in state)")
                any_changed = True
                continue

            # Decide representation:
            # 1) If code previously had a block (we already deleted it), prefer block representation.
            had_block = bool(find_unassigned_block(file_text, start, end, k))
            had_map   = bool(find_attr_braced_block(file_text, start, end, k, "{", "}"))
            # But since we deleted occurrences above, both finders will return None now.
            # Heuristic: block for names ending with '_configuration' or if value is object/list-of-objects;
            # map for common map-like names.
            prefer_map_names = {"tags", "labels", "metadata", "tags_map"}
            prefer_block = k.endswith("_configuration") or is_object_dict(val) or is_list_of_object_dicts(val)
            prefer_map   = (k in prefer_map_names) or (isinstance(val, dict) and not prefer_block)

            if is_object_dict(val):
                # block (e.g., versioning_configuration { status = "Enabled" ... })
                file_text, end, changed = insert_block_object(file_text, start, end, k, val)
                any_changed = any_changed or changed
                print(f"{address}: SYNC {k} (block)")
            elif is_list_of_object_dicts(val):
                file_text, end, changed = insert_block_list(file_text, start, end, k, val)
                any_changed = any_changed or changed
                print(f"{address}: SYNC {k} (blocks list)")
            elif is_list_of_scalars(val):
                file_text, end, changed = insert_list(file_text, start, end, k, val)
                any_changed = any_changed or changed
                print(f"{address}: SYNC {k} (list)")
            elif isinstance(val, dict):
                # treat as map unless heuristics prefer block
                if prefer_block and not prefer_map:
                    file_text, end, changed = insert_block_object(file_text, start, end, k, val)
                    any_changed = any_changed or changed
                    print(f"{address}: SYNC {k} (block-from-dict)")
                else:
                    file_text, end, changed = insert_map(file_text, start, end, k, val)
                    any_changed = any_changed or changed
                    print(f"{address}: SYNC {k} (map)")
            elif is_scalar(val):
                file_text, end, changed = insert_scalar(file_text, start, end, k, val)
                any_changed = any_changed or changed
                print(f"{address}: SYNC {k} (scalar)")
            else:
                print(f"{address}: REMOVE {k} (unsupported type)")
                any_changed = True

        if any_changed:
            open(path, "w", encoding="utf-8").write(file_text)
            changes_applied += 1
            touched.add(path)

    print(f"APPLIED_CHANGES={changes_applied}")
    if touched:
        print("FILES_CHANGED=" + ",".join(sorted(touched)))

if __name__ == "__main__":
    main()

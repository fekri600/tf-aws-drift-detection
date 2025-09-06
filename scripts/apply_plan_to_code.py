#!/usr/bin/env python3
"""
apply_plan_to_code.py  (v1)
- Reads `plan.json` (from `terraform show -json tfplan`)
- For each resource with "update" actions, compares before/after
- Patches .tf files so simple attributes match the "after" values
- Scope v1: scalars (string/number/bool), flat lists/maps.
- Skips: nested blocks, complex expressions, and attributes set via var/local/module/* functions.
"""

import json, re, sys, glob

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

def looks_like_expression(s):
    return any(tok in s for tok in [
        "${","var.","local.","module.","data.","path.","lookup(","merge(",
        "tolist(","tomap(","cidrsubnet(","format(","join(","concat("
    ])

def index_tf_resources():
    idx = {}
    for tf in glob.glob("**/*.tf", recursive=True):
        try:
            text = open(tf,"r",encoding="utf-8").read()
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
                idx.setdefault((rtype,rname),[]).append({"path":tf,"start":m.start(),"end":end})
    return idx

def find_attribute_span(block_text, attr):
    pat = re.compile(rf'(?m)^([ \t]*)({re.escape(attr)})\s*=\s*(.+?)\s*$')
    m = pat.search(block_text)
    if not m: return None
    return (m.start(), m.end(), m.group(1), m.group(3).strip())

def insert_or_replace_attribute(file_text, block_start, block_end, attr, new_value_hcl):
    block = file_text[block_start:block_end]
    found = find_attribute_span(block, attr)
    if found:
        astart, aend, indent, rhs = found
        if looks_like_expression(rhs):
            return file_text, False, f"SKIP {attr}: expression ({rhs[:40]}...)"
        new_line = f"{indent}{attr} = {new_value_hcl}"
        new_block = block[:astart] + new_line + block[aend:]
        return file_text[:block_start] + new_block + file_text[block_end:], True, f"UPDATED {attr}"
    else:
        mhead = re.match(r'([ \t]*)resource[^\n]*\n', file_text[block_start:block_end])
        base_indent = mhead.group(1) if mhead else ""
        insert_indent = base_indent + "  "
        insertion = f"\n{insert_indent}{attr} = {new_value_hcl}\n"
        new_text = file_text[:block_end-1] + insertion + file_text[block_end-1:]
        return new_text, True, f"ADDED {attr}"

def simple_attr_keys(before, after):
    keys = set()
    if not isinstance(before, dict) or not isinstance(after, dict):
        return keys
    for k in after.keys() | before.keys():
        b, a = before.get(k), after.get(k)
        if a == b: continue
        if k in ["id","arn","owner_id","primary_network_interface_id"]: continue
        if isinstance(a,(str,int,float,bool)):
            keys.add(k)
        elif isinstance(a, list):
            if all(isinstance(x,(str,int,float,bool)) for x in a): keys.add(k)
        elif isinstance(a, dict):
            if all(isinstance(v,(str,int,float,bool)) for v in a.values()): keys.add(k)
    return keys

def main():
    if len(sys.argv)!=2:
        print("Usage: apply_plan_to_code.py plan.json", file=sys.stderr); sys.exit(2)
    plan = json.load(open(sys.argv[1],"r",encoding="utf-8"))
    idx = index_tf_resources()
    changes = 0; touched=set()
    for rc in plan.get("resource_changes",[]) or []:
        actions = rc.get("change",{}).get("actions",[])
        if "update" not in actions: continue
        rtype, rname = rc.get("type"), rc.get("name")
        addr = rc.get("address")
        before = rc.get("change",{}).get("before") or {}
        after  = rc.get("change",{}).get("after") or {}
        keys = simple_attr_keys(before, after)
        if not keys: continue
        matches = idx.get((rtype,rname)) or []
        if len(matches)==0:
            print(f"SKIP {addr}: not found in code", file=sys.stderr); continue
        if len(matches)>1:
            paths=[m['path'] for m in matches]
            print(f"SKIP {addr}: multiple matches {paths}", file=sys.stderr); continue
        entry = matches[0]
        path, start, end = entry["path"], entry["start"], entry["end"]
        try:
            file_text = open(path,"r",encoding="utf-8").read()
        except Exception as e:
            print(f"SKIP {addr}: cannot read {path}: {e}", file=sys.stderr); continue
        any_changed=False
        for k in sorted(keys):
            new_hcl = to_hcl(after.get(k))
            new_text, changed, msg = insert_or_replace_attribute(file_text, start, end, k, new_hcl)
            if changed:
                delta = len(new_text)-len(file_text)
                file_text = new_text; end += delta; any_changed=True
                print(f"{addr}: {msg}")
            else:
                print(f"{addr}: {msg}")
        if any_changed:
            open(path,"w",encoding="utf-8").write(file_text)
            changes += 1; touched.add(path)
    print(f"APPLIED_CHANGES={changes}")
    if touched: print("FILES_CHANGED="+",".join(sorted(touched)))

if __name__ == "__main__": main()

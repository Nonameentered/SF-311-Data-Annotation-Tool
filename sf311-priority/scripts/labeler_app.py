#!/usr/bin/env python3
import json, random, time
from pathlib import Path
from typing import List, Dict, Any, Optional

import streamlit as st

DATA = Path("data")
RAW = DATA / "transformed.jsonl"
OUT = DATA / "golden.jsonl"

st.set_page_config(page_title="SF311 Priority Labeler", layout="wide")
st.title("SF311 Priority Labeler — Human-in-the-Loop")

@st.cache_data
def load_rows(path: Path) -> List[Dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            rows.append(json.loads(s))
    return rows

def subset(rows: List[Dict[str, Any]], *, has_photo: Optional[bool], kw_filters: List[str], tag_filters: List[str]) -> List[Dict[str, Any]]:
    def pass_kw(r):
        return all(r.get(f"kw_{k}", False) for k in kw_filters)
    def pass_tag(r):
        for t in tag_filters:
            if t == "lying_face_down" and r.get("tag_lying_face_down") is not True: return False
            if t == "tents_present" and r.get("tag_tents_present") is not True: return False
        return True
    out = []
    for r in rows:
        if has_photo is not None and bool(r.get("has_photo")) != has_photo:
            continue
        if not pass_kw(r): 
            continue
        if not pass_tag(r):
            continue
        out.append(r)
    return out

def show_images(urls: List[str] | None):
    if not urls: 
        st.info("No images for this report.")
        return
    cols = st.columns(min(3, len(urls)))
    for i, u in enumerate(urls[:9]):
        with cols[i % len(cols)]:
            st.image(u, use_column_width=True)

def save_label(rec_id: str, payload: Dict[str, Any]):
    OUT.parent.mkdir(exist_ok=True, parents=True)
    with OUT.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"request_id": rec_id, **payload}, ensure_ascii=False) + "\n")

# Sidebar filters
with st.sidebar:
    st.header("Queue filters")
    has_photo = st.selectbox("Has photo?", ["any","with photos","no photos"])
    has_photo = None if has_photo == "any" else (True if has_photo == "with photos" else False)
    kw_opts = ["passed_out","blocking","onramp","propane","fire","children","wheelchair"]
    kw_filters = st.multiselect("Must include keywords", kw_opts, default=[])
    tag_opts = ["lying_face_down","tents_present"]
    tag_filters = st.multiselect("Must include tags", tag_opts, default=[])
    seed = st.number_input("Random seed", value=42, step=1)
    st.button("Reset queue", key="reset")

rows_all = load_rows(RAW)
rows = subset(rows_all, has_photo=has_photo, kw_filters=kw_filters, tag_filters=tag_filters)
random.Random(seed).shuffle(rows)

if "idx" not in st.session_state or st.session_state.get("reset"):
    st.session_state.idx = 0
    st.session_state["reset"] = False

if not rows:
    st.warning("No items matching the filters. Adjust the sidebar.")
    st.stop()

idx = st.session_state.idx
idx = max(0, min(idx, len(rows)-1))
r = rows[idx]

# Record header
left, right = st.columns([2,1])
with left:
    st.subheader(f"Request {r.get('request_id') or '—'}")
    st.write(f"**Created:** {r.get('created_at') or '—'}")
    st.write(f"**District:** {r.get('police_district') or '—'}")
    st.write(f"**Location:** {r.get('lat')}, {r.get('lon')}")
    st.write(f"**Text:** {r.get('text') or '—'}")
    st.write(f"**Auto-tags:** lying={r.get('tag_lying_face_down')} | tents={r.get('tag_tents_present')} | people={r.get('tag_num_people')} | size_feet={r.get('tag_size_feet')}")
with right:
    st.metric("Queue size", len(rows))
    st.metric("Index", f"{idx+1}/{len(rows)}")

# Images
with st.expander("Images", expanded=True):
    show_images(r.get("image_urls"))

st.markdown("---")
st.markdown("### Human labels")
prio = st.radio("Priority", ["P1","P2","P3","P4"], horizontal=True, index=2)
c1, c2, c3 = st.columns(3)
with c1:
    lying = st.checkbox("lying_face_down")
    safety = st.checkbox("safety_issue")
    drugs = st.checkbox("drugs")
with c2:
    tents = st.checkbox("tents_present")
    blocking = st.checkbox("blocking")
    onramp = st.checkbox("on_ramp")
with c3:
    propane = st.checkbox("propane_or_flame")
    kids = st.checkbox("children_present")
    chair = st.checkbox("wheelchair")
num_people_bin = st.selectbox("num_people_bin", ["0","1","2-3","4-5","6+"], index=1)
size_feet_bin = st.selectbox("size_feet_bin", ["0","1-20","21-80","81-150","150+"], index=2)
abstain = st.checkbox("Abstain (not sure)")
notes = st.text_area("Notes (optional)", height=80)

colA, colB, colC = st.columns([1,1,6])
if colA.button("Save & Next", type="primary"):
    payload = {
        "labels": {
            "priority": prio,
            "features": {
                "lying_face_down": lying, "safety_issue": safety, "drugs": drugs,
                "tents_present": tents, "blocking": blocking, "on_ramp": onramp,
                "propane_or_flame": propane, "children_present": kids, "wheelchair": chair,
                "num_people_bin": num_people_bin, "size_feet_bin": size_feet_bin
            },
            "abstain": bool(abstain),
            "notes": (notes.strip() or None)
        },
        "annotator": None,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S")
    }
    save_label(r.get("request_id"), payload)
    st.session_state.idx = min(idx + 1, len(rows)-1)
    st.experimental_rerun()

if colB.button("Skip"):
    st.session_state.idx = min(idx + 1, len(rows)-1)
    st.experimental_rerun()

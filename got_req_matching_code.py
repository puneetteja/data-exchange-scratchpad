import os, re, json, unicodedata
from collections import defaultdict
from difflib import SequenceMatcher
import pandas as pd

BASE = "/Workspace/Users/sl9ta@s-mxs.net/imb-invoice-checker-expts"
REQUESTED_JSON_FILE = f"{BASE}/requested_json/Rechnungskontrolle-op.json"
GOT_MATCHED_FILE = f"{BASE}/got_matched/got_invoice_document_matches.json"

NAME_MATCH_THRESHOLD = 0.85
AMOUNT_TOLERANCE = 0.01

# ---------- helpers ----------
def as_list(v):
    return [] if v is None else (v if isinstance(v, list) else [v])

def first(v):
    l = as_list(v)
    return l[0] if l else None

def norm_no(s):
    """Letters/digits only, uppercase, no leading zeros: '26-0934' -> '260934'."""
    return re.sub(r"[^0-9A-Za-z]", "", str(s or "")).upper().lstrip("0")

def number_variants(s):
    """Shorter forms of a got invoice number that the requested file may use:
    '26/00309' -> {'309'} (part after the last separator), 'ATS2632889' -> {'2632889'} (trailing digits)."""
    s = str(s or "")
    out = set()
    parts = re.split(r"[/\-\s.]", s)
    if len(parts) > 1:
        out.add(norm_no(parts[-1]))
    m = re.search(r"(\d+)$", s)
    if m:
        out.add(norm_no(m.group(1)))
    out.discard(norm_no(s))
    return {v for v in out if len(v) >= 3}

LEGAL_FORMS = r"\b(gmbh|mbh|m b h|ges|mitges|ag|kg|og|co|llc|inc|ltd|se|sa|sl|s a|sro|s r o|nv|bv|eu|e u)\b"

def norm_name(s):
    if not s:
        return ""
    s = str(s).lower()
    for a, b in {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}.items():
        s = s.replace(a, b)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(LEGAL_FORMS, " ", re.sub(r"[^a-z0-9]+", " ", s))
    return " ".join(s.split())

def name_score(a, b):
    """Best similarity between any name in a and any name in b (strings or lists)."""
    best = 0.0
    for x in as_list(a):
        for y in as_list(b):
            na, nb = norm_name(x), norm_name(y)
            if not na or not nb:
                continue
            if na == nb:
                return 1.0
            if min(len(na), len(nb)) >= 4 and (na in nb or nb in na):
                best = max(best, 0.95)
                continue
            sa, sb = " ".join(sorted(na.split())), " ".join(sorted(nb.split()))
            best = max(best, SequenceMatcher(None, na, nb).ratio(), SequenceMatcher(None, sa, sb).ratio())
    return best

def parse_amount(s):
    """'58202.32', '30.761,24', '1,036,367.91 USD', '44 239,83', '12.966,00 €' -> float."""
    s = first(s)
    if s is None:
        return None
    s = re.sub(r"[^\d,.\-]", "", str(s))
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".") if len(s.split(",")[-1]) != 3 else s.replace(",", "")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None

def amount_equal(a, b):
    a, b = parse_amount(a), parse_amount(b)
    return None if a is None or b is None else abs(a - b) <= AMOUNT_TOLERANCE

# ---------- load ----------
with open(REQUESTED_JSON_FILE, encoding="utf-8") as f:
    req_raw = json.load(f)
requested = [d for item in as_list(req_raw) for d in (item.get("json", item).get("documents") or [])]

with open(GOT_MATCHED_FILE, encoding="utf-8") as f:
    got_matched = json.load(f)
got_invoices = got_matched["invoices"]

print(f"Requested records: {len(requested)} | got invoices: {len(got_invoices)} "
      f"(with delivery notes: {sum(bool(i.get('delivery_notes')) for i in got_invoices)})")
	  
################
# Index got invoices by full number and by shorter variants
by_exact, by_variant = defaultdict(list), defaultdict(list)
for inv in got_invoices:
    by_exact[norm_no(inv.get("invoice number"))].append(inv)
    for v in number_variants(inv.get("invoice number")):
        by_variant[v].append(inv)

req_count_by_no = defaultdict(int)
for r in requested:
    req_count_by_no[norm_no(r.get("documentNumber"))] += 1

def find_invoice(r):
    """Return (invoice, match_method) or (None, None).
    EXACT_NUMBER  : same normalized number; if the number is not unique in the request, the client must match the invoice sender.
    NUMBER_VARIANT: request uses a short form ('309' for '26/00309'); client must match AND debtor or amount must match."""
    no = norm_no(r.get("documentNumber"))
    if not no:
        return None, None
    score = lambda inv: name_score(r.get("clientName"), inv.get("sender name"))

    cands = by_exact.get(no, [])
    if cands:
        best = max(cands, key=score)
        if req_count_by_no[no] == 1 or score(best) >= NAME_MATCH_THRESHOLD:
            return best, "EXACT_NUMBER"

    cands = [inv for inv in by_variant.get(no, [])
             if score(inv) >= NAME_MATCH_THRESHOLD
             and (name_score(r.get("debtorName"), inv.get("customer name")) >= NAME_MATCH_THRESHOLD
                  or amount_equal(r.get("amount"), inv.get("total amount")))]
    if cands:
        return max(cands, key=score), "NUMBER_VARIANT"
    return None, None

CATEGORIES = {
    "1": "1. Matched invoice (only invoice required)",
    "2": "2. Matched invoice + delivery note (both required)",
    "3": "3. Partially matched",
    "4": "4. Unmatched",
}

rows = []
for r in requested:
    required = [x.lower() for x in as_list(r.get("requiredDocuments"))] or ["invoice"]
    inv, method = find_invoice(r)
    dns = (inv.get("delivery_notes") or []) if inv else []

    found = (["invoice"] if inv else []) + (["deliverynote"] if dns else [])
    missing = [x for x in required if x not in found]
    if not found:
        cat = "4"
    elif missing:
        cat = "3"
    else:
        cat = "2" if "deliverynote" in required else "1"

    rows.append({
        "category": CATEGORIES[cat],
        "docRefUnique": r.get("docRefUnique"),
        "documentNumber": r.get("documentNumber"),
        "clientName": r.get("clientName"),
        "debtorName": r.get("debtorName"),
        "amount": r.get("amount"),
        "currency": r.get("currency"),
        "requiredDocuments": ", ".join(required),
        "missingDocuments": ", ".join(missing),
        "got_invoice_number": inv.get("invoice number") if inv else None,
        "match_method": method,
        "got_sender": first(inv.get("sender name")) if inv else None,
        "got_customer": first(inv.get("customer name")) if inv else None,
        "got_total_amount": inv.get("total amount") if inv else None,
        "amount_ok": amount_equal(r.get("amount"), inv.get("total amount")) if inv else None,
        "client_name_score": round(name_score(r.get("clientName"), inv.get("sender name")), 2) if inv else None,
        "debtor_name_score": round(name_score(r.get("debtorName"), inv.get("customer name")), 2) if inv else None,
        "got_delivery_notes": ", ".join(str(d.get("delivery note number")) for d in dns),
    })

result_df = pd.DataFrame(rows)

# ---------- summary ----------
summary_df = (result_df.assign(amount_num=result_df["amount"].map(parse_amount))
              .groupby("category")
              .agg(records=("docRefUnique", "count"), total_amount=("amount_num", "sum"))
              .reindex(list(CATEGORIES.values()), fill_value=0)
              .reset_index())
summary_df["total_amount"] = summary_df["total_amount"].round(2)
assert summary_df["records"].sum() == len(requested), "Category counts do not add up to the requested records"
display(summary_df)
############
# All records with at least one got document (categories 1-3)
display(result_df[result_df["category"] != CATEGORIES["4"]].sort_values(["category", "clientName", "documentNumber"]))

# Got invoices that were not matched to any requested record
used = set(result_df["got_invoice_number"].dropna())
unused = [i for i in got_invoices if i.get("invoice number") not in used]
print(f"Got invoices not matched to any requested record: {len(unused)}")
for i in unused:
    print(f"  {i.get('invoice number')} | {first(i.get('sender name'))} | {first(i.get('customer name'))} | {i.get('total amount')}")

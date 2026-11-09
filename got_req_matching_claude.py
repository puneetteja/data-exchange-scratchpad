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
    s = re.sub(r"[^\d,.\-]", "", str(s)).strip(".,")
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

def norm_cur(c):
    c = str(first(c) or "").strip().upper()
    return {"\u20ac": "EUR", "EURO": "EUR", "$": "USD", "\u00a3": "GBP"}.get(c, c)

def debtor_score(r, inv):
    """Debtor may be listed as customer, or (collective invoices) among the sender names."""
    return max(name_score(r.get("debtorName"), inv.get("customer name")),
               name_score(r.get("debtorName"), inv.get("sender name")))

def evidence(r, inv):
    """Which independent signals agree between request and got invoice."""
    return {"client": name_score(r.get("clientName"), inv.get("sender name")) >= NAME_MATCH_THRESHOLD,
            "debtor": debtor_score(r, inv) >= NAME_MATCH_THRESHOLD,
            "amount": bool(amount_equal(r.get("amount"), inv.get("total amount")))}

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
    EXACT_NUMBER  : same normalized number AND at least 2 of 3 signals (client, debtor, amount) agree.
    NUMBER_VARIANT: request uses a short form ('309' for '26/00309'); client must match AND debtor or amount must match."""
    no = norm_no(r.get("documentNumber"))
    if not no:
        return None, None
    score = lambda inv: name_score(r.get("clientName"), inv.get("sender name"))
    strength = lambda inv: (sum(evidence(r, inv).values()), score(inv))

    cands = [inv for inv in by_exact.get(no, []) if sum(evidence(r, inv).values()) >= 2]
    if cands:
        return max(cands, key=strength), "EXACT_NUMBER"

    cands = [inv for inv in by_variant.get(no, [])
             if evidence(r, inv)["client"] and (evidence(r, inv)["debtor"] or evidence(r, inv)["amount"])]
    if cands:
        return max(cands, key=strength), "NUMBER_VARIANT"
    return None, None

CATEGORIES = {
    "1": "1. Matched invoice (only invoice required)",
    "2": "2. Matched invoice + delivery note (both required)",
    "3": "3. Partially matched",
    "4": "4. Unmatched",
    "5": "5. Needs review (number matched, amount/currency differs)",
}

rows = []
for r in requested:
    required = [x.lower() for x in as_list(r.get("requiredDocuments"))] or ["invoice"]
    inv, method = find_invoice(r)
    dns = (inv.get("delivery_notes") or []) if inv else []

    found = (["invoice"] if inv else []) + (["deliverynote"] if dns else [])
    missing = [x for x in required if x not in found]
    ev = evidence(r, inv) if inv else {}
    amount_ok = amount_equal(r.get("amount"), inv.get("total amount")) if inv else None
    currency_ok = (norm_cur(r.get("currency")) == norm_cur(inv.get("currency"))) if inv else None
    if not found:
        cat = "4"
    elif amount_ok is False or currency_ok is False:
        cat = "5"
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
        "amount_ok": amount_ok,
        "currency_ok": currency_ok,
        "evidence": ", ".join(k for k, v in ev.items() if v),
        "client_name_score": round(name_score(r.get("clientName"), inv.get("sender name")), 2) if inv else None,
        "debtor_name_score": round(debtor_score(r, inv), 2) if inv else None,
        "_inv_id": id(inv) if inv else None,
        "got_delivery_notes": ", ".join(str(d.get("delivery note number")) for d in dns),
    })

result_df = pd.DataFrame(rows)

# ---------- summary ----------
summary_df = (result_df.assign(amount_num=result_df["amount"].map(parse_amount))
              .groupby(["category", "currency"])
              .agg(records=("docRefUnique", "count"), total_amount=("amount_num", "sum"))
              .reset_index())
summary_df["total_amount"] = summary_df["total_amount"].round(2)
summary_df["category"] = pd.Categorical(summary_df["category"], list(CATEGORIES.values()), ordered=True)
summary_df = summary_df.sort_values(["category", "currency"])
assert summary_df["records"].sum() == len(requested), "Category counts do not add up to the requested records"
display(summary_df)
############
# Got invoices claimed by more than one requested record (should be empty)
dups = result_df["_inv_id"].notna() & result_df["_inv_id"].duplicated(keep=False)
print(f"Got invoices claimed by >1 requested record: {result_df.loc[dups, 'got_invoice_number'].nunique()}")
if dups.any():
    display(result_df[dups].drop(columns="_inv_id"))

# All records with at least one got document (categories 1-3 and 5)
display(result_df[result_df["category"] != CATEGORIES["4"]].drop(columns="_inv_id")
        .sort_values(["category", "clientName", "documentNumber"]))

# Got invoices that were not matched to any requested record
used = set(result_df["_inv_id"].dropna())
unused = [i for i in got_invoices if id(i) not in used]
print(f"Got invoices not matched to any requested record: {len(unused)}")
for i in unused:
    print(f"  {i.get('invoice number')} | {first(i.get('sender name'))} | {first(i.get('customer name'))} | {i.get('total amount')}")

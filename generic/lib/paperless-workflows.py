#!/usr/bin/env python3
"""Create Paperless-ngx category tags, document types and workflows (idempotent).

Every new document gets the 'inbox' tag; each category workflow matches a case-insensitive regex against the
OCR'd text and title, assigns the category's document type + tag, and removes 'inbox'. Workflows run in `order`,
so the most specific category (highest order) wins the document type while every matching category leaves a tag.
Tags/types use Paperless's "Auto" (classifier) matching too, so corrections you make in the UI teach it over time.

  PAPERLESS_URL=https://paperless.example.com PAPERLESS_ADMIN_USER=admin PAPERLESS_ADMIN_PASSWORD=... \
      python3 paperless-workflows.py [--dry-run]
"""
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

B = os.environ["PAPERLESS_URL"].rstrip("/") + "/api"
H = {"Authorization": "Basic " + base64.b64encode(f"{os.environ['PAPERLESS_ADMIN_USER']}:{os.environ['PAPERLESS_ADMIN_PASSWORD']}".encode()).decode(),
     "Accept": "application/json; version=9", "Content-Type": "application/json"}
DRY = "--dry-run" in sys.argv
T_ADDED, M_NONE, M_REGEX, A_ASSIGN, A_REMOVE, AUTO = 2, 0, 4, 1, 2, 6

# name: (tag colour, workflow order, regex). Edit freely; re-run to apply.
CATS = {
    "Correspondence": ("#9e9e9e", 10, r"\b(dear (mr|mrs|ms|sir|madam)|sincerely|kind regards|best regards|yours truly)\b"),
    "Receipt": ("#8bc34a", 20, r"\b(receipt|order (number|no\.?|#|confirmation)|thank you for your (purchase|order)|subtotal|cashier|change due|items? purchased)\b"),
    "Invoice": ("#4caf50", 25, r"\b(invoice|inv(oice)? ?(no\.?|number|#)|amount due|balance due|bill to|remit(tance)? to|net ?30|due upon receipt)\b"),
    "Utility Bill": ("#ffc107", 30, r"\b(kwh|kilowatt|therms?|water (usage|service)|sewer|electric(ity)? (service|usage)|natural gas|utility (bill|statement)|usage history|internet service|trash service)\b"),
    "Bank Statement": ("#03a9f4", 35, r"\b(statement period|beginning balance|ending balance|account statement|deposits and (other )?(credits|additions)|withdrawals and (other )?(debits|subtractions)|checking account|savings account|routing (number|no))\b"),
    "Credit Card": ("#00bcd4", 40, r"\b(minimum payment( due)?|credit limit|available credit|annual percentage rate|new balance|payment due date|cardholder|purchases and adjustments|cash advances)\b"),
    "Home & Mortgage": ("#795548", 45, r"\b(mortgage|escrow|principal balance|homeowner'?s?|hoa dues|warranty deed|closing disclosure|property (address|description)|lease agreement|rent (due|payment)|landlord|appraisal|home inspection)\b"),
    "Vehicle": ("#607d8b", 50, r"\b(vin|vehicle identification|odometer|registration renewal|certificate of title|license plate|oil change|tire rotation|mileage|auto repair|service advisor)\b"),
    "Warranty & Manual": ("#9c27b0", 55, r"\b(limited warranty|warranty (card|information|period)|user manual|owner'?s manual|instruction manual|serial (number|no)|model (number|no)|quick start guide|troubleshooting|safety instructions)\b"),
    "School": ("#ff9800", 60, r"\b(school|student|report card|teacher|classroom|permission slip|tuition|enrollment|daycare|child ?care|field trip|pta)\b"),
    "Government": ("#3f51b5", 65, r"\b(social security (administration|number|card)|passport|birth certificate|voter registration|department of (motor vehicles|state|homeland)|uscis|jury (duty|summons)|notice of (assessment|valuation))\b"),
    "Legal & Contract": ("#673ab7", 70, r"\b(agreement|contract|terms and conditions|hereby|whereas|notary public|affidavit|district court|summons|power of attorney|last will|testament|trust agreement|settlement)\b"),
    "Insurance": ("#2196f3", 75, r"\b(policy (number|no\.?|#)|premium|deductible|coverage|insured|claim (number|no\.?|#)|declarations? page|explanation of benefits)\b"),
    "Medical": ("#e91e63", 80, r"\b(patient (name|id)|diagnosis|clinic|physician|provider|copay|co-pay|prescription|rx (number|#)|pharmacy|dental|orthodont|immunization|vaccin|lab results?|appointment reminder|medical record)\b"),
    "Pay Stub": ("#009688", 85, r"\b(pay ?stub|earnings statement|gross pay|net pay|ytd|year to date|pay period|federal (withholding|income tax)|fica|medicare tax|direct deposit (advice|notice))\b"),
    "Tax": ("#f44336", 90, r"\b(form (1040|w-?2|w-?4|1099|1098|1095)|internal revenue service|irs|tax (return|year|statement|form|refund)|property tax statement|taxable (income|wages)|withholding statement)\b"),
}
# local names worth adding per household, e.g. utilities/clinics/schools: append them to the regexes above.


def api(m, path, d=None):
    if DRY and m not in ("GET", "OPTIONS"):
        print(f"  dry-run {m} {path}"); return {"id": 0}
    r = urllib.request.Request(B + path, method=m, data=json.dumps(d).encode() if d is not None else None, headers=H)
    try:
        with urllib.request.urlopen(r, timeout=60) as x:
            b = x.read(); return json.loads(b) if b else {}
    except urllib.error.HTTPError as e:
        sys.exit(f"{m} {path} -> {e.code}: {e.read().decode()[:600]}")


def ensure(kind, name, **extra):
    r = api("GET", f"/{kind}/?name__iexact={urllib.parse.quote(name)}")["results"]
    return r[0]["id"] if r else api("POST", f"/{kind}/", {"name": name, **extra})["id"]


def workflow(name, order, triggers, actions, existing):
    body = {"name": name, "order": order, "enabled": True, "triggers": triggers, "actions": actions}
    if name in existing:
        api("PUT", f"/workflows/{existing[name]['id']}/", body); return "updated"
    api("POST", "/workflows/", body); return "created"


def main():
    for c in CATS.values():
        re.compile(c[2], re.I)
    inbox = ensure("tags", "inbox", color="#ff5722", is_inbox_tag=True, matching_algorithm=M_NONE)
    tags = {n: ensure("tags", n, color=c[0], matching_algorithm=AUTO) for n, c in CATS.items()}
    types = {n: ensure("document_types", n, matching_algorithm=AUTO) for n in CATS}
    existing = {w["name"]: w for w in api("GET", "/workflows/?page_size=100")["results"]}
    print("inbox:", workflow("01 · Tag new documents 'inbox'", 1,
                             [{"type": T_ADDED, "matching_algorithm": M_NONE, "match": ""}],
                             [{"type": A_ASSIGN, "assign_tags": [inbox]}], existing))
    for n, (_, order, rx) in CATS.items():
        print(f"{n}:", workflow(f"{order:02d} · Category: {n}", order,
                                [{"type": T_ADDED, "matching_algorithm": M_REGEX, "match": rx, "is_insensitive": True}],
                                [{"type": A_ASSIGN, "assign_tags": [tags[n]], "assign_document_type": types[n]},
                                 {"type": A_REMOVE, "remove_tags": [inbox]}], existing))
    print("workflows:", api("GET", "/workflows/?page_size=100")["count"])


if __name__ == "__main__":
    main()

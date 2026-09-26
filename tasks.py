"""What each document type asks the model for. The same prompt goes to every model."""

RULES = (
    "You are extracting data from a business document for an accounting team.\n"
    "Return ONLY one JSON object with exactly the keys listed below, no prose, no code fences.\n"
    "Copy every value exactly as the document prints it, keeping its number format (for example '1,234.56').\n"
    "Use null for any field the document does not show. Never guess, and never compute a value that is not printed."
)

TASKS = {
    "w2": {
        "input": "image",
        "keys": (
            "employee_ssn (box a), employer_ein (box b), employer_name_address (box c, all lines joined with newlines), "
            "employee_first_name, employee_last_name, employee_address (box f, lines joined with newlines), "
            "box1_wages, box2_federal_tax, box3_ss_wages, box4_ss_tax, box5_medicare_wages, box6_medicare_tax, "
            "box12a_code, box12a_amount, box15_state, box16_state_wages, box17_state_tax"
        ),
        "note": "This is a US Form W-2.",
    },
    "receipts": {
        "input": "image",
        "keys": ('total, subtotal, tax, items (a list of every purchased line item as {"name": ..., "price": ...}, '
                 "with the line price as printed)"),
        "note": "This is a shop or restaurant receipt.",
    },
    "sroie": {
        "input": "image",
        "keys": "company (the seller's name), date (as printed), address (the seller's address, one line), total (the final amount paid)",
        "note": "This is a scanned shop receipt.",
    },
    "invoices": {
        "input": "image",
        "keys": ("vendor (the company that issued the invoice), invoice_number (as printed by the vendor), "
                 "invoice_date (the issue date printed by the vendor), total (the final amount billed)"),
        "note": ("This is a scanned invoice. Handwritten notes and stamps were added later by the recipient; "
                 "ignore them and use only what the vendor printed. Use null if the vendor printed no invoice number "
                 "or no issue date."),
    },
    "statements": {
        "input": "image",
        "keys": ('header: {"bank_name", "account_holder", "account_number", "ifsc_code", "opening_balance", '
                 '"start_date", "end_date"}, transactions: a list of EVERY transaction row visible on this page, '
                 'top to bottom, each {"date": "YYYY-MM-DD", "debit": number or null, "credit": number or null, '
                 '"balance": number}'),
        "note": ("This is page 1 of an Indian bank statement. For numbers in the transactions list and opening_balance, "
                 "return plain numbers without commas. Dates in header also as YYYY-MM-DD."),
    },
    "contracts": {
        "input": "text",
        "keys": ("agreement_date, effective_date, expiration_date (the date the initial term ends: if the contract gives a "
                 "start date and a term length, work out the end date; 'perpetual' if it never expires), "
                 "governing_law (the jurisdiction whose law governs), "
                 "renewal_term (length of each automatic renewal, e.g. '1 year', or 'perpetual'), "
                 "notice_period_to_terminate_renewal (e.g. '90 days')"),
        "note": ("This is a commercial contract. For this document only, you may work out expiration_date as described; "
                 "everything else as stated in the contract. Use null when the contract does not state the item. "
                 "effective_date is null unless the contract states an effective date."),
    },
}


def prompt(doc_type):
    t = TASKS[doc_type]
    return f"{RULES}\n\n{t['note']}\nKeys: {t['keys']}"


SELF_CHECK = (
    "Below is a JSON extraction produced earlier from the same document. Check every field against the document itself. "
    "Correct any value that is wrong or incomplete, and set to null any value the document does not actually print. "
    "Return ONLY the corrected JSON object with the same keys.\n\nEarlier extraction:\n"
)

# PII Redactor — MVP

Detect and **permanently redact** personally identifiable information (PII) in
PDF files, with a Dropbox integration for pulling a source file and writing the
redacted copy back. Built as a minimum viable product.

This MVP delivers the core slice of the larger vision: *retrieve a user-selected
PDF → detect configurable PII → produce a verified redacted copy.* The
admin-policy / auto-redact-on-share scenario is intentionally out of scope here
but the architecture is designed to grow into it (see Roadmap).

---

## What it does

1. **Configurable detection** — pick from predefined PII types or add your own
   regex rules at runtime.
2. **Trustworthy matching** — high-severity types (credit card, SSN, AU TFN /
   Medicare / ABN) are confirmed with real **checksum / structural validators**,
   not just pattern matching, which sharply cuts false positives.
3. **True redaction** — **surgical** mode (default) removes PII text runs with
   PyMuPDF so the rest of the document stays selectable. **Flatten** mode
   rasterizes each page and paints out PII pixels (no text layer). Pages with
   no extractable words are flattened even in surgical mode.
4. **Honest verification** — the output is re-scanned. Surgical mode fails if
   any detected value is still extractable. Flatten mode reports that the text
   layer is gone; it does **not** claim every pixel was covered. A detection
   without a redaction box is always a fail.
5. **Dropbox in/out** — list PDFs, download a selected file, and (optionally)
   upload the redacted copy back as `<name> (redacted).pdf`.
6. **Shared-secret auth** — `/api/*` and `/jobs/*` require `REDACTOR_API_KEY`
   (Bearer header, session unlock in the UI). Job files expire after a TTL.

---

## The redaction approach (and why it matters)

The most common redaction bug is drawing a black box *on top of* the text. The
box is just a graphic — the words underneath remain selectable and trivially
extractable with any PDF tool. That is a data breach waiting to happen.

This MVP uses **locate, then surgical or flatten**:

- Extract every word and its bounding box (`pdfplumber`).
- Detect PII over the reconstructed page text and map each match back to the
  word boxes it covers.
- **Surgical (default):** PyMuPDF `apply_redactions()` removes only the PII
  runs. Non-PII text stays searchable.
- **Flatten:** render the page to a raster (`pypdfium2`), paint solid boxes,
  rebuild the PDF (`reportlab`). Use this when you need no leftover text layer.
- Pages with no extractable words are flattened even if surgical mode is selected
  (scanned PDFs still need OCR — out of scope here).
- **Verify:** every match must have a box. Surgical outputs must not contain
  the original PII strings. Flatten outputs must have an empty text layer; the
  UI says the text layer was removed, not that visual coverage is proven.

The detection layer (`pii_engine/`) is shared; only the render step differs.

---

## Project layout

```
pii-redactor/
├── app.py                  Flask web app (UI + JSON API)
├── dropbox_client.py       Dropbox SDK wrapper (list / download / upload)
├── pii_engine/
│   ├── detectors.py        Predefined detectors + checksum validators
│   ├── engine.py           Detection orchestration, custom rules, overlap merge
│   └── redactor.py         Surgical + flatten redaction + verification + previews
├── templates/index.html    Single-page UI
├── samples/
│   ├── make_sample.py       Generates a synthetic PII document
│   ├── sample_pii.pdf       Synthetic source document
│   ├── sample_pii_REDACTED.pdf
│   └── sample_audit_report.json
├── requirements.txt
└── README.md
```

---

## Run it

```bash
cd pii-redactor
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export REDACTOR_API_KEY="change-me"
python app.py
# open http://127.0.0.1:8080
```

If `REDACTOR_API_KEY` is unset, the process prints an ephemeral key at startup.
Enter that key in the UI unlock screen (or send `Authorization: Bearer …`).

Click **Sample → Redact PDF** to see the full flow with no file setup. Or upload
your own PDF. (Tip: regenerate the sample any time with
`python samples/make_sample.py samples/sample_pii.pdf`.)

### Environment

| Variable | Default | Purpose |
|----------|---------|---------|
| `REDACTOR_API_KEY` | ephemeral (printed) | Shared secret for API + UI unlock |
| `FLASK_SECRET_KEY` | derived from API key | Flask session signing |
| `FLASK_DEBUG` | off | Set `1` only on a trusted machine |
| `REDACTOR_BIND` | `127.0.0.1` | Bind address |
| `REDACTOR_PORT` | `8080` | HTTP port |
| `REDACTOR_JOB_TTL` | `3600` | Seconds before `_work/<job>` is deleted |
| `DROPBOX_TOKEN` | unset | Optional Dropbox access token |

### Enable Dropbox (optional)

Set an access token before launching:

```bash
export DROPBOX_TOKEN="sl.xxxxx"   # token for an app with files.content.read + .write
python app.py
```

The Dropbox tab then lets you pick a PDF by path (or from a dropdown) and tick
"write redacted copy back to Dropbox." A single access token is enough for
local use; production should use the full OAuth 2 + refresh-token flow.

---

## Predefined PII types

| Type | Category | Validated? |
|------|----------|-----------|
| Credit / debit card | Financial | Luhn checksum |
| IBAN | Financial | format |
| US SSN | Government ID | structural rules |
| US passport | Government ID | format (off by default) |
| AU Tax File Number | Government ID | ATO checksum |
| AU Medicare number | Government ID | checksum |
| AU Business Number (ABN) | Government ID | mod-89 (off by default) |
| Email | Contact | format |
| Phone | Contact | digit-count sanity |
| IPv4 | Contact | range check (off by default) |
| Date of birth / date | Contact | format (off by default) |

Adding a predefined type is a one-line entry in `pii_engine/detectors.py`.
Custom rules (label + regex) can be added live in the UI or via the API.

---

## API

```
GET  /api/session            {authenticated: bool}  (public)
POST /api/unlock             JSON {api_key} → session cookie
GET  /api/detectors          predefined types grouped by category
GET  /api/dropbox/status     {enabled: bool}
GET  /api/dropbox/files      list PDFs in a folder
POST /api/redact             multipart: file | use_sample | dropbox_path,
                             config_json {enabled_keys, custom_rules},
                             dpi (72–300), mode (surgical|flatten)
                             → audit report + before/after preview URLs + downloads
GET  /jobs/<12-hex>/<file>   redacted.pdf | report.json | before_N.png | after_N.png

Authenticated routes require Authorization: Bearer $REDACTOR_API_KEY
or a session from /api/unlock.
```

---

## Roadmap to the full vision

- **Admin policies & auto-redact-on-share** — a team admin defines a policy
  (which PII types, which folders). A Dropbox **webhook** fires on share/upload,
  the engine redacts server-side, and the shared link resolves to the redacted
  copy. The detection/redaction core here is reused as-is.
- **Named entities** — add a NER model (e.g. Presidio + spaCy) for names and
  street addresses, which regex handles poorly.
- **Scanned PDFs** — OCR (Tesseract) before detection for image-only documents.
- **More formats** — extend beyond PDF to DOCX / images.
- **Tamper-evident audit log** — persist reports to durable storage for
  compliance.

---

## Limitations

- Works on text-based PDFs; scanned/image PDFs need the OCR step above.
- Regex-based detection has inherent recall/precision limits; the phone heuristic
  favors recall (it may cover extra numeric strings) while high-severity types
  use validators for precision.
- Flattened pages are not text-searchable (by design). Surgical mode keeps
  non-PII text.
- Coordinate mapping assumes standard, unrotated page geometry.
- Flatten verification only proves the text layer is gone, not pixel-perfect
  coverage. Job artifacts live on disk until `REDACTOR_JOB_TTL` expires.

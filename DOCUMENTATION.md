# Documentation

Technical reference for the Legal Document Analyzer. For a quick start, see [README.md](README.md).

## Architecture

```
uploaded file
   │
   ▼
read_document()      PDF / DOCX / TXT  →  plain text
   │
   ▼
split_clauses()      plain text  →  [(clause_id, clause_text), ...]
   │
   ├──► ner_hints()  legal NER model  →  {clause_id: {label: [values]}}
   │        (optional: failure is logged and skipped)
   ▼
analyze_with_gemini()   clauses + hints  →  JSON analysis
   │
   ▼
render_report()      JSON  →  markdown shown in the Gradio UI
```

Everything lives in `app.py`. `analyze(file_path)` is the entry point the UI calls. It wraps the whole pipeline, and any exception is returned to the user as "Something went wrong: ...".

## Configuration

| Setting | Where | Default | Description |
|---|---|---|---|
| `GEMINI_API_KEY` | environment variable | none (required) | API key from https://aistudio.google.com/apikey |
| `GEMINI_MODEL` | environment variable | `gemini-3.5-flash-lite` | Gemini model ID used for analysis |
| `NER_MODEL` | constant in `app.py` | `ssanskar9/legal_ner_model` | Hugging Face token-classification model |
| `MAX_CHARS` | constant in `app.py` | `200000` | Maximum contract characters sent to Gemini; the rest is dropped |
| `HINT_LABELS` | constant in `app.py` | DATE, ORG, GPE, OTHER_PERSON, STATUTE, PROVISION | NER labels passed to Gemini as hints |

## Modules and functions

### File reading

`read_document(path)` picks a reader by file extension:

- `.pdf`: `pypdf`, pages joined by newlines. Raises an error if fewer than 50 characters are extracted (likely a scan that needs OCR).
- `.docx`: `python-docx`, paragraphs only. Text inside tables is not read.
- `.txt`, `.md`: read as UTF-8, undecodable bytes ignored.
- Anything else raises "Unsupported file type".

### Clause splitting

`split_clauses(text)` scans line by line and starts a new clause when a line matches the `HEADING` regex:

- `Article 5`, `Section 3`, `Clause IV`, `Section 2.1`
- `1.2`, `3.4.1` (with optional trailing `.` or `)`)
- `1.` or `1)`

Text before the first heading becomes the clause `Preamble`. Each clause is returned as `(id, text)`, where the id is the matched number with trailing `.` or `)` stripped. Contracts without numbered headings end up as a single `Preamble` clause.

### NER hints

- `load_ner()` loads the model once and caches it. It uses aggregation strategy `simple`, so word pieces are merged into entities.
- `_chunks(text, limit=1200)` splits a clause on sentence boundaries (`. ; :`) into pieces of at most 1200 characters, so they stay under the model's 512-token limit. Sentences longer than the limit are hard-split.
- `ner_hints(clauses)` runs the model on every chunk. It keeps entities whose label is in `HINT_LABELS` and whose score is at least 0.6, removes duplicates, and returns `{clause_id: {label: [values]}}`.
- `format_hints(hints)` turns that into lines like `[Clause 4.2] DATE: 15 March 2027 | ORG: Acme Ltd`, or `(none)`.

The hints are context for Gemini only. They do not appear in the final report.

### Gemini analysis

`analyze_with_gemini(clauses, hints)`:

1. Reads `GEMINI_API_KEY` (raises if missing).
2. Builds the contract text as `[Clause id]` blocks and truncates it to `MAX_CHARS`.
3. Fills the `PROMPT` template with the hints and contract.
4. Calls `generate_content` with `response_mime_type="application/json"` and `temperature=0.2`.
5. Strips any code fences, parses the JSON, and adds `_truncated` to record whether the text was cut.

The prompt tells Gemini to use only the contract text, to write "not specified" for missing facts, and to treat the contract as governed by Indian law. It lists the red flags to look for: auto-renewal, unlimited or one-sided liability, broad indemnity, one-sided or short-notice termination, unilateral changes, non-compete or exclusivity, IP assignment, penalties, vague terms, distant arbitration seat or jurisdiction, and missing termination rights.

### Expected JSON

```json
{
  "summary": "under 150 words, plain language",
  "obligations": [{"party": "", "action": "", "deadline": "", "clause": ""}],
  "red_flags":   [{"clause": "", "issue": "", "severity": "high|medium|low", "why": ""}],
  "deadlines":   [{"date": "", "event": "", "clause": ""}]
}
```

### Rendering

`render_report(data)` produces markdown with four sections: Summary, Key obligations (table), Red-flag clauses (sorted high → medium → low, with 🔴 🟠 🟡 icons) and Deadlines (table). It adds a truncation note when `_truncated` is true, and always ends with the disclaimer.

### UI

`main()` builds a Gradio `Interface`: a file upload (`.pdf`, `.docx`, `.txt`) as input and a Markdown component as output. Flagging is disabled. `python app.py` launches it locally.

## Dependencies

| Package | Used for |
|---|---|
| `gradio` | Web UI |
| `transformers`, `torch` | Running the NER model |
| `pypdf` | PDF text extraction |
| `python-docx` | DOCX text extraction |
| `google-genai` | Gemini API client |

The NER model is downloaded from Hugging Face on first use, so the first run is slower.

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| `GEMINI_API_KEY is not set.` | Export the key in the same terminal before running `python app.py`. |
| Model not found or 404 error from Gemini | The default model ID may not be available to you. Set `GEMINI_MODEL` to a valid ID from Google AI Studio. |
| "No text found in this PDF" | The PDF is a scan. Run OCR first. |
| `NER step skipped: ...` in the terminal | The NER model failed to load. The analysis still runs, without hints. Check your internet connection and that `torch` and `transformers` are installed. |
| JSON parse error | Gemini returned malformed output. Retry, or lower the document size. |
| Text after some point is missing from the analysis | The document exceeded `MAX_CHARS` and was truncated. |
| Table contents missing from a DOCX | Only paragraphs are read. Copy the table text into paragraphs or a TXT file. |

## Known limitations

- Contract text is sent to Google's Gemini API. Use non-confidential documents only.
- Clause detection relies on numbered headings. Unnumbered contracts are analyzed as one block, and clause references in the report will be less precise.
- Output quality depends on the LLM and can contain mistakes. The report is not legal advice.
- The prompt is tuned for Indian law.

## Possible extensions

- Show the NER entities as their own report section.
- Read DOCX tables.
- Split very long contracts into batches instead of truncating.
- Clause-level Q&A over the uploaded contract.
- Export the report to PDF.

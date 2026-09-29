# Legal Document Analyzer (LegalNER Extended)

Upload a contract and get a plain-language summary, key obligations, red-flag clauses and deadlines.

## How it works

1. **Read**: extracts text from a PDF, DOCX or TXT file.
2. **Split**: breaks the text into numbered clauses (Article / Section / Clause / `1.2` style headings).
3. **Tag**: a custom legal NER model ([`ssanskar9/legal_ner_model`](https://huggingface.co/ssanskar9/legal_ner_model)) finds dates, organisations, places, persons, statutes and provisions in each clause.
4. **Analyze**: Gemini receives the clauses plus the NER hints and returns a JSON analysis, which is rendered as a report with a summary, obligations table, severity-ranked red flags and a deadlines table.

## Setup

```bash
pip install -r requirements.txt
export GEMINI_API_KEY="your-key"   # from https://aistudio.google.com/apikey
python app.py
```

Then open the local URL Gradio prints.

Optional: set `GEMINI_MODEL` to use a different Gemini model (default is a Flash-Lite model, chosen for its free-tier limits).

## Files

| File | Purpose |
|---|---|
| `app.py` | The whole app: file reading, clause splitting, NER, Gemini call, Gradio UI |
| `requirements.txt` | Python dependencies |
| `sample_contract.txt` | Example contract to try |

## Limitations

- Contract text is sent to Gemini's API. Use sample or non-confidential documents only.
- Scanned PDFs need OCR first.
- Very long documents are truncated (200,000 characters).
- The prompt targets contracts governed by Indian law.
- This is an automated analysis, not legal advice. Have a qualified lawyer review important contracts.

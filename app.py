"""
Legal Document Analyzer
Upload a contract -> plain-language summary, key obligations, red-flag clauses, deadlines.

- Your model (ssanskar9/legal_ner_model) finds dates, organisations, statutes and provisions.
- Gemini writes the summary and does the obligation / red-flag / deadline analysis.

Setup:
    pip install -r requirements.txt
    export GEMINI_API_KEY="your-key"        # from https://aistudio.google.com/apikey
    python app.py
"""

import json
import os
import re

NER_MODEL = "ssanskar9/legal_ner_model"
# Use a Flash-Lite model for the highest free-tier daily limit.
# Check the exact model id available to you at https://aistudio.google.com
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
MAX_CHARS = 200_000  # safety cap on document size sent to Gemini

# NER labels worth showing Gemini as hints (the rest are court-case labels)
HINT_LABELS = {"DATE", "ORG", "GPE", "OTHER_PERSON", "STATUTE", "PROVISION"}

DISCLAIMER = (
    "*This is an automated analysis, not legal advice. "
    "Have a qualified lawyer review important contracts.*"
)


# ---------------------------------------------------------------- reading files
def read_document(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(path)
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
        if len(text.strip()) < 50:
            raise ValueError(
                "No text found in this PDF. It may be a scan; run OCR on it first."
            )
        return text
    if ext == ".docx":
        import docx

        d = docx.Document(path)
        return "\n".join(p.text for p in d.paragraphs)
    if ext in (".txt", ".md"):
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    raise ValueError("Unsupported file type. Please upload a PDF, DOCX or TXT file.")


# -------------------------------------------------------------- clause splitting
HEADING = re.compile(
    r"^\s*(?P<num>(?:Article|Section|Clause)\s+[IVXLC\d]+(?:\.\d+)*|\d+(?:\.\d+)+[.)]?|\d+[.)])\s+\S",
    re.IGNORECASE,
)


def split_clauses(text: str):
    """Return a list of (clause_id, clause_text)."""
    clauses, current_id, current = [], "Preamble", []
    for line in text.splitlines():
        m = HEADING.match(line)
        if m:
            if "".join(current).strip():
                clauses.append((current_id, "\n".join(current).strip()))
            current_id = m.group("num").strip().rstrip(".)")
            current = [line]
        else:
            current.append(line)
    if "".join(current).strip():
        clauses.append((current_id, "\n".join(current).strip()))
    return clauses


# ------------------------------------------------------------------- NER hints
_ner = None


def load_ner():
    global _ner
    if _ner is None:
        from transformers import (
            AutoModelForTokenClassification,
            AutoTokenizer,
            pipeline,
        )

        tok = AutoTokenizer.from_pretrained(NER_MODEL, use_fast=True, add_prefix_space=True)
        model = AutoModelForTokenClassification.from_pretrained(NER_MODEL)
        _ner = pipeline(
            "token-classification",
            model=model,
            tokenizer=tok,
            aggregation_strategy="simple",
        )
    return _ner


def _chunks(text: str, limit: int = 1200):
    """Split into pieces that stay under the model's 512-token limit."""
    sentences = re.split(r"(?<=[.;:])\s+", text)
    buf = ""
    for s in sentences:
        while len(s) > limit:  # very long sentence: hard split
            yield s[:limit]
            s = s[limit:]
        if len(buf) + len(s) + 1 > limit and buf:
            yield buf
            buf = ""
        buf = f"{buf} {s}".strip()
    if buf:
        yield buf


def ner_hints(clauses):
    """Run the NER model per clause. Returns {clause_id: {label: [values]}}."""
    ner = load_ner()
    hints = {}
    for cid, text in clauses:
        found = {}
        for chunk in _chunks(text):
            for ent in ner(chunk):
                label = ent["entity_group"]
                if label in HINT_LABELS and ent["score"] >= 0.6:
                    value = ent["word"].strip()
                    found.setdefault(label, [])
                    if value and value not in found[label]:
                        found[label].append(value)
        if found:
            hints[cid] = found
    return hints


def format_hints(hints):
    lines = []
    for cid, found in hints.items():
        parts = [f"{label}: {', '.join(vals)}" for label, vals in found.items()]
        lines.append(f"[Clause {cid}] " + " | ".join(parts))
    return "\n".join(lines) if lines else "(none)"


# ---------------------------------------------------------------------- Gemini
PROMPT = """You are helping a non-lawyer understand a contract governed by Indian law.
Use ONLY the contract text below. Do not invent facts. If something is not stated, write "not specified".

Return JSON with exactly these keys:
{{
  "summary": "plain-language summary in under 150 words, no legal jargon",
  "obligations": [{{"party": "...", "action": "what they must do", "deadline": "when, or 'not specified'", "clause": "clause number"}}],
  "red_flags": [{{"clause": "clause number", "issue": "short name", "severity": "high|medium|low", "why": "one plain-language sentence on the risk"}}],
  "deadlines": [{{"date": "date or period, e.g. 15 March 2027 or 'within 30 days of signing'", "event": "what happens", "clause": "clause number"}}]
}}

Red flags to look for: auto-renewal, unlimited or one-sided liability, broad indemnity, one-sided or short-notice termination, unilateral changes, non-compete or exclusivity, IP assignment, penalties or liquidated damages, unclear or vague terms, arbitration seat or jurisdiction far from the user, missing termination rights.

An automated entity tagger found these names, dates and legal references (use as hints, verify against the text):
{hints}

CONTRACT (clauses are labelled):
{contract}
"""


def analyze_with_gemini(clauses, hints):
    from google import genai
    from google.genai import types

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set.")
    client = genai.Client(api_key=api_key)

    contract = "\n\n".join(f"[Clause {cid}]\n{text}" for cid, text in clauses)
    truncated = len(contract) > MAX_CHARS
    contract = contract[:MAX_CHARS]

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=PROMPT.format(hints=format_hints(hints), contract=contract),
        config=types.GenerateContentConfig(
            response_mime_type="application/json", temperature=0.2
        ),
    )
    raw = (response.text or "").strip()
    raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
    data = json.loads(raw)
    data["_truncated"] = truncated
    return data


# ------------------------------------------------------------------- rendering
SEVERITY_ICON = {"high": "🔴", "medium": "🟠", "low": "🟡"}


def render_report(data):
    out = ["## Summary", data.get("summary", "not available"), ""]

    out.append("## Key obligations")
    obligations = data.get("obligations") or []
    if obligations:
        out += ["| Who | Must do | By when | Clause |", "|---|---|---|---|"]
        for o in obligations:
            out.append(
                f"| {o.get('party', '')} | {o.get('action', '')} | "
                f"{o.get('deadline', 'not specified')} | {o.get('clause', '')} |"
            )
    else:
        out.append("None found.")
    out.append("")

    out.append("## Red-flag clauses")
    flags = data.get("red_flags") or []
    order = {"high": 0, "medium": 1, "low": 2}
    flags = sorted(flags, key=lambda f: order.get(str(f.get("severity", "low")).lower(), 3))
    if flags:
        for f in flags:
            sev = str(f.get("severity", "low")).lower()
            out.append(
                f"- {SEVERITY_ICON.get(sev, '🟡')} **Clause {f.get('clause', '?')} - "
                f"{f.get('issue', '')}** ({sev}): {f.get('why', '')}"
            )
    else:
        out.append("No red flags detected.")
    out.append("")

    out.append("## Deadlines")
    deadlines = data.get("deadlines") or []
    if deadlines:
        out += ["| When | What | Clause |", "|---|---|---|"]
        for d in deadlines:
            out.append(f"| {d.get('date', '')} | {d.get('event', '')} | {d.get('clause', '')} |")
    else:
        out.append("None found.")
    out.append("")

    if data.get("_truncated"):
        out.append("> Note: the document was very long, so only the first part was analyzed.\n")
    out.append(DISCLAIMER)
    return "\n".join(out)


# ------------------------------------------------------------------------ main
def analyze(file_path):
    if not file_path:
        return "Please upload a contract (PDF, DOCX or TXT)."
    try:
        text = read_document(file_path)
        clauses = split_clauses(text)
        try:
            hints = ner_hints(clauses)
        except Exception as e:  # NER is a helper; don't fail the whole analysis
            print("NER step skipped:", e)
            hints = {}
        return render_report(analyze_with_gemini(clauses, hints))
    except Exception as e:
        return f"**Something went wrong:** {e}"


def main():
    import gradio as gr

    demo = gr.Interface(
        fn=analyze,
        inputs=gr.File(label="Upload contract", file_types=[".pdf", ".docx", ".txt"], type="filepath"),
        outputs=gr.Markdown(label="Analysis"),
        title="⚖️ Legal Document Analyzer",
        description=(
            "Upload a contract to get a plain-language summary, key obligations, "
            "red-flag clauses and deadlines. Use sample or non-confidential documents only."
        ),
        flagging_mode="never",
    )
    demo.launch()


if __name__ == "__main__":
    main()

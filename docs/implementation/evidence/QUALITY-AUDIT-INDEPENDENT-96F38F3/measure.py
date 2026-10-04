"""Bounded authored detector matrix, scaling, profile and throughput evidence."""
from __future__ import annotations
import cProfile
import hashlib
import io
import json
from pathlib import Path
import platform
import pstats
import sys
import time

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "tests"))
from quality_fixtures import document, build_corpus
from xlm.data.quality import detectors
from xlm.data.quality.policy import METRICS, FLAGS, policy_body
from xlm.data.quality.runner import Limits, run_audit
from xlm.data.evidence_v2 import canonical

CASES = {
    "html": "<!doctype html><html><head><style>p{color:red}</style></head><body><script>alert(1)</script><p>Hello</p></body></html>",
    "nested": "<div><span>Hello</span><p>World</p></div>",
    "xml": '<?xml version="1.0"?><note><to>Ada</to></note>',
    "xml_code_doctype": '```xml\n<!DOCTYPE note>\n<note><to>Ada</to></note>\n```',
    "entities": "A &amp; B &lt; C &#123;",
    "cpp": '#include <vector>\nstd::vector<int> xs;\nreturn xs.size();',
    "rust": 'fn main() {\nlet xs: Vec<Option<u32>> = vec![];\n}',
    "inequality": "For a real number, 0 < x < 1 and y > x.",
    "special_tokens": "The literals <unk> and <eos> are tokens.",
    "markdown": "# Notes\n\n**Bold** and _italic_ prose.\n- First thing\n- Second thing",
    "redirect": "echo hello > output.txt\ncat < input.txt\ncat <<EOF\nhello\nEOF",
    "angle_prose": "Please fill <your-name> here.",
    "separator": "A legitimate paragraph about rivers and the surrounding landscape.\n" + "-"*80,
    "braces": "int main() {\n if (x) {\n  return 1;\n }\n}",
    "indentation": "def f():\n" + " "*32 + "return 1\n",
    "ascii": "+-----+-----+\n| one | two |\n+-----+-----+",
    "refrain": ("The sun returns to the valley every morning.\nSing again, sing again.\n")*8,
    "legal": ("ARTICLE I: GENERAL PROVISIONS\nDifferent requirements apply to this subsection.\n")*8,
    "generated_code": "\n".join(f"const int v{i} = {i};" for i in range(200)),
    "char_spam": "z"*4096,
    "punct_spam": "!"*4096,
    "spaces": " "*4096,
    "paragraph_spam": ("This is one authored paragraph with enough characters to qualify for comparison.\n\n")*200,
    "phrase_loop": "buy the amazing special product today "*400,
    "french": "Élève, déjà, forêt, où, Noël, cœur et français.",
    "german": "Straße, größer, süß, München, ÄÖÜäöüß.",
    "japanese": "日本語の普通の文章です。こんにちは世界。",
    "cjk": "這是一個普通的中文句子。汉字自然语言。",
    "arabic": "هذه جملة عربية عادية وطبيعية.",
    "hebrew": "זה משפט רגיל בשפה העברית.",
    "emoji": "Friends 👨‍👩‍👧‍👦 😊 🚀 celebrate.",
    "combining": "Cafe\u0301 nai\u0308ve coo\u0308perate.",
    "unicode_math": "∀x∈ℝ, x²≥0. ∫ f(x) dx ≤ ∞; α + β ≠ γ.",
    "nul": "abc\x00def",
    "c0_c1": "abc\x01\x7f\x85\x9fdef",
    "replacement": "damaged \ufffd\ufffd text",
    "zero_width": "zero\u200bwidth\u2060text\u180e",
    "bom": "embedded\ufeffmark",
    "bidi": "abc\u202edef\u202c",
    "private": "private \ue000 glyph",
    "mojibake": "cafÃ© â€™ ï»¿",
    "scientific": "We estimate the integral using a well-defined method. The measured uncertainty agrees with the model.\nE = mc²\nSmith, A. (2020). A study. Journal 12, 24–31.\n42",
    "hyphenated": "A state-of-the-art method uses well-defined, non-linear equations.",
    "headers": ("Journal of Authored Studies\nThis prose is an authored page with a complete sentence.\n17\n")*10,
    "fragments": "a\nb\nc\nd\ne\nf\ng\nh\ni\nj",
    "broken_hyphens": "inter-\nnation-\nally\nfrag-\nmented\n",
    "python": "import math\ndef f(x):\n    return math.sqrt(x)\n",
    "json": '{\n  "name": "example",\n  "enabled": true,\n  "value": 42\n}',
    "shell": "#!/bin/sh\nfor f in *.txt; do\n  cat \"$f\"\ndone",
    "table": "| Name | Value |\n|---|---|\n| Alpha | Beta |\n| Gamma | Delta |",
    "csv": "name,city,occupation\nAlice,Paris,chemist\nBob,Berlin,teacher\nCarol,Tokyo,writer",
    "bibliography": "Smith, A. (2020). A useful study. Journal 12, 1–9.\nDoe, B. (2021). Another study. Proceedings 4, 20–24.",
    "cookie": "This website uses cookies. Accept all cookies. Cookie settings.",
    "privacy": "Privacy Policy\nTerms of Service",
    "navigation": "Home\nAbout us\nContact\nMenu\nSearch\nLogin\nRegister",
    "subscribe": "Subscribe to our newsletter\nSign up for our updates",
    "copyright": "© 2026 Example. All rights reserved.",
    "url_farm": "\n".join(f"https://example.org/item/{i}" for i in range(100)),
    "seo": "best cheap shoes online buy shoes discount shoes "*200,
    "cookie_prose": "My grandmother baked cookies for the whole family.",
    "privacy_prose": "The paper discusses privacy and its social implications.",
    "subscription_prose": "A subscription supports the journal's scientific work.",
    "newsletter_prose": "The historian described how the newsletter changed public opinion.",
    "url_prose": "The paper uses URLs to identify resources, such as https://example.org/reference.",
}


def main():
    evidence = Path(__file__).parent
    matrix = {}
    for name, text in CASES.items():
        result = detectors.analyze(text, len(text.encode()))
        matrix[name] = {"class":result.doc_class, "flags":[FLAGS[i] for i in result.flags],
            "metrics":{m.name:v for m,v in zip(METRICS,result.values)}}
    scaling = []
    for unique in (1000, 2000, 4000, 8000):
        text = "".join(chr(0x4000+i)*8 for i in range(unique))
        counts = detectors.char_counts(text)
        times = []
        for _ in range(3):
            values = [None]*len(METRICS)
            start = time.perf_counter()
            detectors._runs(text, counts, values)
            times.append(time.perf_counter()-start)
        scaling.append({"unique":unique, "characters":len(text), "utf8_bytes":len(text.encode()), "run_seconds":times, "best_seconds":min(times)})
    profiler = cProfile.Profile()
    profiler.enable()
    for _ in range(30):
        for text in CASES.values():
            detectors.analyze(text, len(text.encode()))
    profiler.disable()
    stream = io.StringIO()
    pstats.Stats(profiler, stream=stream).strip_dirs().sort_stats("cumulative").print_stats(30)
    (evidence/"profile.txt").write_text(stream.getvalue(), encoding="utf-8")
    (evidence/"detector-matrix.json").write_text(json.dumps(matrix, indent=1, ensure_ascii=False)+"\n", encoding="utf-8")
    (evidence/"scaling.json").write_text(json.dumps(scaling, indent=1)+"\n", encoding="utf-8")
    (evidence/"policy-definitions.json").write_text(json.dumps(policy_body(), indent=1, ensure_ascii=False)+"\n", encoding="utf-8")
    print(json.dumps({"detector_cases":len(matrix), "scaling":scaling}))
    root = Path("F:/qa96-benchmark")
    assert not root.exists(), "fresh authored benchmark directory required"
    layout = {}
    texts = list(CASES.values())
    # 32 MiB physical corpus target; computed hashes, no live inputs.
    for file in range(8):
        rows = []
        size = 0
        while size < 4*1024**2:
            n = len(rows)
            base = texts[(n+file)%len(texts)]
            text = (base+" ")*max(1, 4096//(len(base)+1))
            row = document(f"authored-{file}-{n}", text, n+1)
            size += len(canonical.canonical_bytes(row))+1
            rows.append(row)
        layout[f"c{file%4}/v/f{file}"] = rows
    manifest = build_corpus(root, layout)
    del layout
    results = []
    for workers in (1,8):
        bound = Limits(workers, 4*1024**3, 1024**3, 256*1024**2, 1024**2, 120)
        results.append(run_audit(manifest, root/f"w{workers}", limits=bound, progress_interval=None))
    data = {"environment":{"python":sys.version,"platform":platform.platform()}, "runs":results,
        "note":"Authored adversarial mixture; 32 MiB is startup-sensitive, not production qualification."}
    (evidence/"benchmark.json").write_text(json.dumps(data,indent=1)+"\n",encoding="utf-8")
    print(json.dumps(data))


if __name__ == "__main__":
    main()

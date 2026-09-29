"""Check every statistic the manuscript cites against the figure notebooks' printed output.

Usage (from the repo root):
    python figures/scripts/check_manuscript_stats.py [path/to/manuscript.docx]
    python figures/scripts/check_manuscript_stats.py --dump-json expected_stats.json [docx]
    python figures/scripts/check_manuscript_stats.py --from-json expected_stats.json

Pass 1: every F / t / H / chi2 / U / W / Z / r / R2 / eta2 / BF / d / beta statistic in the docx, with its
degrees of freedom and the p-value that follows it, must appear on one printed line of
figures/notebooks/figure2..5.ipynb or supp_figure1.ipynb (value at the docx's precision, dfs on the same line,
p on the same line or in the same cell). Pass 2: descriptive values in the Results (mean ± SE,
medians, percentages, seconds, SD) and standalone post-hoc p-values must appear somewhere in the
printed output. Exit status 1 if any pass-1 statistic is NOT FOUND, so the check can gate a submission.

--dump-json writes the extracted statistics (numbers only, no manuscript prose) so a copy of the
notebooks can be checked without the docx; --from-json runs both passes from such a file. The
coverage scan (which sentences of the docx yielded no statistic) needs the prose and runs only
against the docx. Nothing else is written except this report on stdout.
"""
import argparse, collections, json, os, re, sys, zipfile
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(os.path.dirname(os.path.dirname(HERE)))   # figures/scripts/ -> repo root
DEFAULT_DOCX = 'docs/manuscript/Grgurich_et_al_final.docx'
NOTEBOOKS = ['figure2', 'figure3', 'figure4', 'figure5', 'supp_figure1']
NS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main', 'mc': 'http://schemas.openxmlformats.org/markup-compatibility/2006'}
W = '{%s}' % NS['w']; MC = '{%s}' % NS['mc']
SUB = str.maketrans('₀₁₂₃₄₅₆₇₈₉−–', '0123456789--')
def norm(s): return s.translate(SUB).replace(' ', ' ').replace('\xa0', ' ').replace(' ', ' ')

# ── statistics regexes ──────────────────────────────────────────────────────
NUM = r'-?\d*\.\d+|-?\d+'
PATS = [
    ('F',    re.compile(r'\bF\s*\(\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*\)\s*=\s*(' + NUM + ')')),
    ('t',    re.compile(r'\bt\s*\(\s*(\d+(?:\.\d+)?)\s*\)\s*=\s*(' + NUM + ')')),
    ('H',    re.compile(r'\bH\s*\(\s*(\d+)\s*\)\s*=\s*(' + NUM + ')')),
    ('chi2', re.compile(r'χ\s*[²2]?\s*(?:\(\s*(\d+)\s*\))?\s*=\s*(' + NUM + ')')),
    ('U',    re.compile(r'\bU\s*=\s*(' + NUM + ')')),
    ('W',    re.compile(r'\bW\s*=\s*(' + NUM + ')')),
    ('Z',    re.compile(r'\bZ\s*=\s*(' + NUM + ')')),
    ('r',    re.compile(r'(?<![A-Za-z])(?:r|ρ|rho|r_s|rs)\s*=\s*(' + NUM + ')')),
    ('R2',   re.compile(r'R\s*[²2]\s*=\s*(' + NUM + ')')),
    ('eta2', re.compile(r'(?:η|ε)\s*[²2]?\s*(?:p|G)?\s*=\s*(' + NUM + ')')),
    ('BF',   re.compile(r'\bBF\s*(?:10|01)?\s*=\s*(' + NUM + ')')),
    ('d',    re.compile(r"(?<![A-Za-z])d\s*=\s*(" + NUM + ')')),
    ('beta', re.compile(r'β\s*=\s*(' + NUM + ')')),
]
P_PAT = re.compile(r'\bp\s*(<|=|>)\s*(\.\d+|0\.\d+|1\.0+)')
DESC = [
    ('mean±', re.compile(r'(\d+(?:\.\d+)?)\s*±\s*(\d+(?:\.\d+)?)')),
    ('median', re.compile(r'[Mm]edians?\s*(?:of\s*)?(?:=\s*)?(\d+(?:\.\d+)?)(?:\s*(?:and|,)\s*(\d+(?:\.\d+)?))?')),
    ('SD', re.compile(r'SD\s*=\s*(\d+(?:\.\d+)?)')),
    ('pct', re.compile(r'(\d+(?:\.\d+)?)\s*%')),
    ('secs', re.compile(r'(\d+(?:\.\d+)?)\s*s\b(?!\w)')),
    ('p-alone', re.compile(r'\bp\s*(?:=|≤|<)\s*(\.\d+|1\.0+|0\.\d+)(?:\s*and\s*(\.\d+))?')),
    ('all p', re.compile(r'all\s+p\s*(?:≤|<|=)\s*(\.\d+)')),
]
FLOAT = re.compile(r'-?\d*\.\d+|-?\d+')


# ── docx side: paragraphs, sections, claims ─────────────────────────────────
def load_docx_paragraphs(docx):
    from lxml import etree
    doc = etree.fromstring(zipfile.ZipFile(docx).read('word/document.xml'))
    def in_fallback(el):
        p = el.getparent()
        while p is not None:
            if p.tag == MC + 'Fallback': return True
            p = p.getparent()
        return False
    paras = []
    for p in doc.find('w:body', NS).iter(W + 'p'):
        txt = ''.join(t.text or '' for t in p.iter(W + 't') if not in_fallback(t))
        style = p.find('w:pPr/w:pStyle', NS); st = style.get(W + 'val') if style is not None else ''
        paras.append((txt, st))
    return paras


def sections_of(paras):
    section = 'front'; sec_of = []
    for txt, st in paras:
        t = txt.strip()
        if t and len(t) < 60 and (st.startswith('Heading') or re.fullmatch(r'\d*\.?\s*(Abstract|Introduction|Methods|Materials and Methods|Results|Discussion|References|Figure Legends|Figure captions|Figures|Supporting Information|Supplementary|Acknowledg\w+|Author Contributions|Data Availability(?: Statement)?|Conflict of Interest|Funding)\W*', t, re.I)):
            section = t
        sec_of.append(section)
    return sec_of


def extract_stat_claims(paras, sec_of):
    claims = []
    for i, (txt, st) in enumerate(paras):
        s = norm(txt)
        for kind, rx in PATS:
            for m in rx.finditer(s):
                groups = [g for g in m.groups() if g is not None]
                value = groups[-1]; dfs = groups[:-1]
                tail = s[m.end(): m.end() + 60]
                pm = P_PAT.search(tail)
                pval = (pm.group(1), pm.group(2)) if pm else None
                claims.append(dict(para=i, section=sec_of[i], kind=kind, dfs=dfs, value=value, p=pval,
                                   text=s[max(0, m.start() - 40): m.end() + (pm.end() if pm else 0)].strip()))
    return claims


def results_bounds(paras, sec_of, claims):
    """Paragraph range scanned by pass 2: first statistic .. the Discussion heading."""
    first_stat = min(r['para'] for r in claims)
    disc = next((i for i, s in enumerate(sec_of) if s.upper().startswith('DISCUSSION')), len(paras))
    return first_stat, disc


def extract_desc_claims(paras, first_stat, disc):
    desc = []
    for i in range(first_stat, disc):
        s = norm(paras[i][0])
        if not s.strip(): continue
        stat_spans = [(m.start(), m.end()) for _, rx in PATS for m in rx.finditer(s)]
        for kind, rx in DESC:
            for m in rx.finditer(s):
                if any(a <= m.start() < b + 45 for a, b in stat_spans) and kind == 'p-alone': continue   # p already tied to a statistic
                vals = [g for g in m.groups() if g]
                for v in vals:
                    fv = float(v)
                    if kind == 'pct' and fv in (0, 100): continue
                    pct_ctx = kind == 'pct' or '%' in s[m.start(): m.end() + 12]
                    desc.append(dict(para=i, kind=kind, value=v, pct_ctx=pct_ctx,
                                     ctx=s[max(0, m.start() - 60): m.end() + 20].strip()))
    return desc


# ── notebook side ───────────────────────────────────────────────────────────
def load_notebook_lines(nb_dir='figures/notebooks', notebooks=NOTEBOOKS):
    nb_lines = []
    for nb in notebooks:
        d = json.load(open(os.path.join(nb_dir, f'{nb}.ipynb')))
        for ci, c in enumerate(d['cells']):
            if c['cell_type'] != 'code': continue
            for o in c.get('outputs', []):
                text = ''.join(o.get('text', [])) if o.get('output_type') == 'stream' else ''.join(o.get('data', {}).get('text/plain', [])) if o.get('output_type') in ('execute_result', 'display_data') else ''
                for l in text.splitlines():
                    if l.strip(): nb_lines.append((nb, ci, norm(l)))
    return nb_lines


def decimals(v): return len(v.split('.')[1]) if '.' in v else 0
def matches(v, line_floats):
    fv = float(v); d = decimals(v); tol = 0.5 * 10 ** (-d) + 1e-9
    return any(abs(x - fv) <= tol for x in line_floats)
def p_matches(p, line):
    op, pv = p; lf = [float(x) for x in FLOAT.findall(line)]
    if op == '<' and pv in ('.001', '0.001'):
        return bool(re.search(r'p\s*<\s*\.?0*\.001|p\s*=\s*0?\.000\d*|p\s*<\s*0\.001', line)) or any(0 <= x < 0.001 for x in lf[3:] if re.search(r'\d\.\d{4}\s+\d', line)) or any(0 < x < 0.001 for x in lf)
    return matches(pv, lf)


def match_stats(claims, nb_lines):
    line_floats = [(nb, ci, l, [float(x) for x in FLOAT.findall(l)]) for nb, ci, l in nb_lines]
    rows = []
    for c in claims:
        hits = [(nb, ci, l) for nb, ci, l, lf in line_floats if matches(c['value'], lf) and all(matches(df, lf) for df in c['dfs'])]
        if not hits and c['dfs']:
            hits_loose = [(nb, ci, l) for nb, ci, l, lf in line_floats if matches(c['value'], lf)]
        else: hits_loose = []
        p_ok = None
        if c['p']:
            p_ok = any(p_matches(c['p'], l) for _, _, l in hits) if hits else False
            if not p_ok and hits:   # p on a neighbouring line of the same cell
                cells = {(nb, ci) for nb, ci, _ in hits}
                p_ok = any(p_matches(c['p'], l) for nb, ci, l in nb_lines if (nb, ci) in cells)
        verdict = ('MATCH' if hits else ('VALUE ONLY (dfs differ)' if hits_loose else 'NOT FOUND'))
        if hits and c['p'] and not p_ok: verdict = 'MATCH, p differs'
        where = ', '.join(sorted({f"{nb}:c{ci}" for nb, ci, _ in (hits or hits_loose)})) if (hits or hits_loose) else ''
        rows.append(dict(**c, verdict=verdict, where=where, example=(hits or hits_loose)[0][2][:120] if (hits or hits_loose) else ''))
    return rows, line_floats


def match_desc(desc_claims, line_floats):
    desc_rows = []
    for c in desc_claims:
        v = c['value']; fv = float(v)
        cands = [fv] + ([fv / 100] if c['pct_ctx'] else [])
        hit = next(((nb, ci, l) for nb, ci, l, lf in line_floats if any(matches(str(x) if x == fv else f"{x:.{decimals(v)+2}f}", lf) for x in cands)), None)
        desc_rows.append(dict(**c, ok=hit is not None, where=f"{hit[0]}:c{hit[1]}" if hit else '', line=hit[2][:110] if hit else ''))
    return desc_rows


# ── reports ─────────────────────────────────────────────────────────────────
def report_stats(rows, nb_lines, n_paras):
    print(f"docx paragraphs: {n_paras}; statistics found: {len(rows)}; notebook output lines: {len(nb_lines)}")
    print("by section:", dict(collections.Counter(r['section'] for r in rows)))
    print("verdicts:", dict(collections.Counter(r['verdict'] for r in rows)))
    print("\n=== NOT FOUND / partial ===")
    for r in rows:
        if r['verdict'] != 'MATCH':
            print(f"[para {r['para']} | {r['section'][:18]}] {r['verdict']:24s} {r['kind']}{tuple(r['dfs']) if r['dfs'] else ''} = {r['value']}  p{r['p'][0] + r['p'][1] if r['p'] else ''}")
            if r.get('text'): print(f"      docx: …{r['text'][:150]}")
            if r['example']: print(f"      nb  : {r['where']} | {r['example']}")


def report_coverage(paras, sec_of, rows):
    print("\n=== coverage scan: statistic-family tokens in the docx and whether each sentence yielded a claim ===")
    TOK = re.compile(r'Kruskal|Mann.Whitney|Wilcoxon|log.rank|Spearman|Pearson|Bayes|BF|χ|chi.square|η|ε²|R²|Cohen|Bonferroni|Holm|Scheff|Levene|Mauchly|Greenhouse|Welch|ANOVA|median|Mdn|M = |SD = |SEM|CI\b|odds|hazard', re.I)
    n_sent = n_covered = 0
    for i, (txt, st) in enumerate(paras):
        for sent in re.split(r'(?<=[.!?])\s+', norm(txt)):
            if not TOK.search(sent): continue
            has_num = bool(re.search(r'\d', sent)); n_sent += 1
            covered = any(r['text'][:30] in sent or sent[:40] in r['text'] for r in rows if r['para'] == i) or any(re.search(rx.pattern, sent) for _, rx in PATS)
            if covered: n_covered += 1
            elif has_num: print(f"   [para {i} | {sec_of[i][:14]}] no statistic extracted: {sent[:170]}")
    print(f"   sentences with statistic-family tokens: {n_sent}; with an extracted statistic: {n_covered}")
    print("\nheadings detected:", sorted({x for x in sec_of}))


def report_desc(desc_rows, first_stat, disc, paras=None):
    print("\n=== pass 2: descriptive values and standalone p-values (Results + legends) ===")
    if paras is not None:
        print(f"   scanning paragraphs {first_stat}-{disc - 1}; headings seen around the Results start:", [paras[i][0][:40] for i in range(max(0, first_stat - 6), first_stat) if paras[i][0].strip()])
    else:
        print(f"   scanning paragraphs {first_stat}-{disc - 1}")
    ok = sum(r['ok'] for r in desc_rows)
    print(f"   descriptive values / standalone p: {len(desc_rows)}; found in notebook output: {ok}; NOT found: {len(desc_rows) - ok}")
    for r in desc_rows:
        if not r['ok']: print(f"   [para {r['para']}] {r['kind']:7s} {r['value']:>7s}  …{r.get('ctx', '')[:130]}")


# ── JSON round trip (numbers only; no manuscript prose) ─────────────────────
def to_json(docx, paras, claims, desc, first_stat, disc):
    return {
        'source': os.path.basename(docx), 'extracted': date.today().isoformat(), 'n_paragraphs': len(paras),
        'results_paragraphs': [first_stat, disc],
        'stats': [dict(para=c['para'], section=c['section'], kind=c['kind'], dfs=c['dfs'], value=c['value'], p=c['p']) for c in claims],
        'descriptives': [dict(para=c['para'], kind=c['kind'], value=c['value'], pct_ctx=c['pct_ctx']) for c in desc],
    }


def from_json(path):
    d = json.load(open(path))
    claims = [dict(c, dfs=list(c['dfs']), p=tuple(c['p']) if c['p'] else None, text='') for c in d['stats']]
    desc = [dict(c, ctx='') for c in d['descriptives']]
    return d, claims, desc


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('docx', nargs='?', default=None, help=f'manuscript (default {DEFAULT_DOCX})')
    ap.add_argument('--dump-json', metavar='PATH', help='write the extracted statistics to PATH and also run the check')
    ap.add_argument('--from-json', metavar='PATH', help='check against statistics extracted earlier (no docx needed)')
    ap.add_argument('--notebooks', default='figures/notebooks', help='directory holding the executed figure notebooks')
    args = ap.parse_args(argv)
    if not args.from_json and args.docx is None and not os.path.exists(DEFAULT_DOCX):
        j = os.path.join(HERE, 'expected_stats.json')   # the companion repository ships this instead of the docx
        if os.path.exists(j): args.from_json = j

    nb_lines = load_notebook_lines(args.notebooks)
    if args.from_json:
        meta, claims, desc = from_json(args.from_json)
        paras = None; n_paras = meta['n_paragraphs']; first_stat, disc = meta['results_paragraphs']
        print(f"expected statistics from {args.from_json} (extracted {meta['extracted']} from {meta['source']})")
    else:
        docx = args.docx or DEFAULT_DOCX
        paras = load_docx_paragraphs(docx); sec_of = sections_of(paras); n_paras = len(paras)
        claims = extract_stat_claims(paras, sec_of)
        first_stat, disc = results_bounds(paras, sec_of, claims)
        desc = extract_desc_claims(paras, first_stat, disc)
        if args.dump_json:
            with open(args.dump_json, 'w') as f:
                json.dump(to_json(docx, paras, claims, desc, first_stat, disc), f, indent=1, ensure_ascii=False)
            print(f"wrote {args.dump_json}: {len(claims)} statistics, {len(desc)} descriptive values")

    rows, line_floats = match_stats(claims, nb_lines)
    report_stats(rows, nb_lines, n_paras)
    if paras is not None: report_coverage(paras, sec_of, rows)
    desc_rows = match_desc(desc, line_floats)
    report_desc(desc_rows, first_stat, disc, paras)
    return 1 if any(r['verdict'] == 'NOT FOUND' for r in rows) else 0


if __name__ == '__main__':
    sys.exit(main())

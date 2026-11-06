"""
coach_review.py
Coach-validated accuracy. A coach who has seen a player's stroke marks
each metric TennisAC scored as right, partly right or wrong, and says
whether the overall score was fair. These helpers turn those reviews into
agreement numbers: overall, per metric (what to fix next), and per scoring
version (did a change to the rules make TennisAC more accurate?).
"""

# What a coach can say about one metric, and how much it counts as agreement.
VERDICTS = {'right': 1.0, 'partly': 0.5, 'wrong': 0.0}
VERDICT_LABELS = {'right': 'Right', 'partly': 'Partly right', 'wrong': 'Wrong', 'unsure': "Couldn't tell"}

# Whether the overall score was fair, as the coach saw the stroke.
OVERALL_VERDICTS = {'too_high': 'Too high', 'about_right': 'About right', 'too_low': 'Too low'}


def clean_review(form, metric_names):
    """The review a coach submitted, keeping only known metrics and
    verdicts. 'unsure' is kept but never counts toward agreement."""
    metrics = {}
    for name in metric_names:
        v = form.get('m:' + name)
        if v in VERDICT_LABELS:
            metrics[name] = v
    overall = form.get('overall') if form.get('overall') in OVERALL_VERDICTS else None
    try:
        coach_score = float(form.get('coach_score', ''))
        coach_score = coach_score if 0 <= coach_score <= 100 else None
    except ValueError:
        coach_score = None
    missed = (form.get('missed') or '').strip()[:2000] or None
    return {'metrics': metrics, 'overall': overall, 'coach_score': coach_score, 'missed': missed}


def agreement(verdicts):
    """Share of judged metrics the coach agreed with, 0-100 (partly right
    counts half), or None if nothing was judged."""
    judged = [VERDICTS[v] for v in verdicts if v in VERDICTS]
    return round(100 * sum(judged) / len(judged)) if judged else None


def summarize(reviews):
    """Numbers for the admin Accuracy page from a list of reviews, each a
    dict with metrics, overall, coach_score, app_score, version and month."""
    all_verdicts, by_metric, by_version, by_month = [], {}, {}, {}
    overall = {k: 0 for k in OVERALL_VERDICTS}
    gaps = []
    for r in reviews:
        verdicts = list(r['metrics'].values())
        all_verdicts += verdicts
        for name, v in r['metrics'].items():
            by_metric.setdefault(name, []).append(v)
        by_version.setdefault(r.get('version') or 'Before versions', []).extend(verdicts)
        by_month.setdefault(r['month'], []).extend(verdicts)
        if r.get('overall') in overall:
            overall[r['overall']] += 1
        if r.get('coach_score') is not None and r.get('app_score') is not None:
            gaps.append(r['app_score'] - r['coach_score'])

    def rows(groups):
        out = []
        for key, vs in groups.items():
            out.append({
                'name': key,
                'agreement': agreement(vs),
                'judged': sum(1 for v in vs if v in VERDICTS),
                'wrong': vs.count('wrong'),
                'partly': vs.count('partly'),
            })
        return out

    metrics = sorted((m for m in rows(by_metric) if m['judged']), key=lambda m: (m['agreement'], -m['judged']))
    return {
        'reviews': len(reviews),
        'judged': sum(1 for v in all_verdicts if v in VERDICTS),
        'agreement': agreement(all_verdicts),
        'metrics': metrics,
        'versions': sorted(rows(by_version), key=lambda m: (m['name'] != 'Before versions', m['name'])),
        'months': sorted(rows(by_month), key=lambda m: m['name']),
        'overall': overall,
        # Positive: TennisAC scores higher than coaches would.
        'avg_gap': round(sum(gaps) / len(gaps), 1) if gaps else None,
        'gap_count': len(gaps),
    }
